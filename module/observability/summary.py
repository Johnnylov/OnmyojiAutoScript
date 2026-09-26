"""Replayable daily aggregates; each day's values and cursor commit together.

The global cursor moves only after EVERY affected day is durable. A crash
between day commits therefore replays safely and cannot authorize premature
deletion of the source event shard.
"""
from __future__ import annotations

import copy
import math
import threading
from datetime import datetime, timedelta, time
from pathlib import Path

from .common import (SCHEMA_VERSION, TERMINAL, atomic_json, read_json,
                     timestamp, report_zone, encode, DirectoryLock)

RULE_VERSION = "logical-runs-v1"
COUNTERS = ("started", "succeeded", "failed", "cancelled", "interrupted", "crashed",
            "device_seconds", "queue_wait_seconds", "successful_execution_seconds",
            "success_samples", "anomalous_device_seconds")


def empty_totals():
    result = {key: 0 for key in COUNTERS}
    result["failure_categories"] = {}
    return result


def merge_totals(target, contribution):
    for key in COUNTERS:
        target[key] = target.get(key, 0) + contribution.get(key, 0)
    for key, count in contribution.get("failure_categories", {}).items():
        target["failure_categories"][key] = target["failure_categories"].get(key, 0) + count


def with_ratios(value):
    value = copy.deepcopy(value)
    denominator = value["succeeded"] + value["failed"]
    value["success_denominator"] = denominator
    value["success_rate"] = value["succeeded"] / denominator if denominator else None
    samples = value["success_samples"]
    value["average_execution_seconds"] = value["successful_execution_seconds"] / samples if samples else None
    return value


def seconds(value):
    value = float(value or 0)
    if not math.isfinite(value) or value < 0:
        raise ValueError("Duration must be finite and non-negative")
    return value


class SummaryStore:
    def __init__(self, root, *, timezone="Asia/Shanghai", observer=None, lock=None, assert_open=None):
        self.root = Path(root)
        self.directory = self.root / "summaries"
        self.state_path = self.root / "meta" / "summary_cursor.json"
        self.timezone = timezone
        self.zone = report_zone(timezone)
        self._observer = observer
        self._lock = lock or threading.RLock()
        self._writer = DirectoryLock(self.root / "meta" / "writer.lock") if lock is None else None
        self._assert_open = assert_open or (lambda: None)
        self.state = read_json(self.state_path, {
            "schema_version": SCHEMA_VERSION, "cursor_seq": 0,
            "timezone": timezone, "rule_version": RULE_VERSION, "runs": {},
            "incomplete": False, "gaps": [], "pruned_before": None,
        })
        if self.state["timezone"] != timezone or self.state["rule_version"] != RULE_VERSION:
            raise ValueError("Existing summaries use another reporting rule/timezone; rebuilding needs complete details")

    @property
    def cursor_seq(self):
        return self.state["cursor_seq"]

    def _day(self, value):
        return timestamp(value).astimezone(self.zone).date().isoformat()

    def _read_day(self, day):
        return read_json(self.directory / f"{day}.json", {
            "schema_version": SCHEMA_VERSION, "date": day, "timezone": self.timezone,
            "rule_version": RULE_VERSION, "cursor_seq": 0, "totals": empty_totals(),
            "groups": {}, "incomplete": False, "anomalies": [],
        })

    def _segment_days(self, event):
        payload = event["payload"]
        duration = seconds(payload.get("duration_seconds", payload.get("device_seconds", 0)))
        try:
            start = timestamp(payload["started_at"])
            end = timestamp(payload.get("finished_at", event["occurred_at"]))
            wall = (end - start).total_seconds()
            if wall <= 0 or wall > 366 * 86400 or abs(wall - duration) > max(2.0, duration * .05):
                raise ValueError("wall_clock_discontinuity")
        except (ValueError, KeyError, TypeError):
            return {self._day(event["occurred_at"]): duration}, True
        splits = {}
        position = start
        while position < end:
            local = position.astimezone(self.zone)
            boundary = datetime.combine(local.date() + timedelta(days=1), time(), self.zone)
            boundary = min(boundary.astimezone(start.tzinfo), end)
            day = local.date().isoformat()
            splits[day] = splits.get(day, 0) + duration * (boundary - position).total_seconds() / wall
            position = boundary
        return splits, False

    def apply(self, event):
        with self._lock:
            self._assert_open()
            seq = int(event["seq"])
            if seq <= self.cursor_seq:
                return False
            following = copy.deepcopy(self.state)
            contributions = {}
            anomalies = set()
            incomplete_days = set()
            profile = event.get("profile_id", "unknown")
            task = event.get("task_id", event["payload"].get("task_id", "unknown"))
            run_id = event.get("run_id")
            payload = event["payload"]
            event_type = event["type"]
            day = self._day(event["occurred_at"])

            def contribution(for_day=day):
                return contributions.setdefault(for_day, empty_totals())

            # Mode at WRITE time controls aggregation, not current policy.
            if event.get("summary_required", True):
                if event_type == "run.started":
                    contribution()["started"] += 1
                    if run_id:
                        following["runs"].setdefault(run_id, {"execution_seconds": 0, "execution_known": False})
                elif event_type == "segment.finished":
                    splits, anomaly = self._segment_days(event)
                    duration = sum(splits.values())
                    if run_id:
                        run = following["runs"].setdefault(run_id, {"execution_seconds": 0, "history_incomplete": True})
                        run["execution_seconds"] += duration
                        run["execution_known"] = True
                    for split_day, amount in splits.items():
                        contribution(split_day)["device_seconds"] += amount
                        if anomaly:
                            contribution(split_day)["anomalous_device_seconds"] += amount
                            anomalies.add(split_day)
                elif event_type == "run.finished":
                    outcome = payload.get("outcome")
                    if outcome not in TERMINAL:
                        raise ValueError("run.finished requires a valid terminal outcome")
                    contribution()[outcome] += 1
                    contribution()["queue_wait_seconds"] += seconds(payload.get("queue_wait_seconds", 0))
                    run = following["runs"].pop(run_id, {})
                    accumulated = run.get("execution_seconds", 0)
                    if run.get("history_incomplete"):
                        incomplete_days.add(day)
                    if payload.get("duration_incomplete"):
                        incomplete_days.add(day)
                    if outcome == "succeeded":
                        if "execution_seconds" in payload or run.get("execution_known"):
                            contribution()["success_samples"] += 1
                            contribution()["successful_execution_seconds"] += seconds(payload.get("execution_seconds", accumulated))
                        else:
                            incomplete_days.add(day)
                    if outcome == "failed":
                        category = str(payload.get("error_category") or "unknown")[:128]
                        contribution()["failure_categories"][category] = 1
            elif run_id:
                if event_type == "run.finished":
                    following["runs"].pop(run_id, None)
                elif event_type in {"run.started", "segment.finished"}:
                    following["runs"].setdefault(run_id, {"execution_seconds": 0})["history_incomplete"] = True

            key = encode([profile, task]).decode("utf-8")
            for affected_day in sorted(contributions):
                # A committed global cursor must never recreate previously
                # pruned days from an old event on restart.
                if following.get("pruned_before") and affected_day < following["pruned_before"]:
                    following["incomplete"] = True
                    continue
                document = self._read_day(affected_day)
                if seq <= document["cursor_seq"]:
                    continue
                merge_totals(document["totals"], contributions[affected_day])
                group = document["groups"].setdefault(key, {
                    "profile_id": profile, "task_id": task, "totals": empty_totals()})
                merge_totals(group["totals"], contributions[affected_day])
                if affected_day in anomalies:
                    document["incomplete"] = True
                    document["anomalies"].append({"seq": seq, "reason": "wall_clock_discontinuity"})
                    document["anomalies"] = document["anomalies"][-100:]
                if affected_day in incomplete_days:
                    document["incomplete"] = True
                    document["anomalies"].append({"seq": seq, "reason": "incomplete_execution_history"})
                    document["anomalies"] = document["anomalies"][-100:]
                document["cursor_seq"] = seq
                atomic_json(self.directory / f"{affected_day}.json", document, self._observer)
            following["cursor_seq"] = seq
            atomic_json(self.state_path, following, self._observer)
            self.state = following
            return True

    def mark_gap(self, gap):
        with self._lock:
            self._assert_open()
            following = copy.deepcopy(self.state)
            following["incomplete"] = True
            if gap not in following["gaps"]:
                following["gaps"].append(gap)
                following["gaps"] = following["gaps"][-100:]
            atomic_json(self.state_path, following, self._observer)
            self.state = following

    def query(self, start_date=None, end_date=None, profile_id=None, task_id=None):
        with self._lock:
            days, total = [], empty_totals()
            for path in sorted(self.directory.glob("????-??-??.json")):
                day = path.stem
                if (start_date and day < start_date) or (end_date and day > end_date):
                    continue
                document = read_json(path)
                values = empty_totals()
                for group in document["groups"].values():
                    if profile_id and group["profile_id"] != profile_id:
                        continue
                    if task_id and group["task_id"] != task_id:
                        continue
                    merge_totals(values, group["totals"])
                merge_totals(total, values)
                days.append({"date": day, "totals": with_ratios(values),
                             "incomplete": document["incomplete"],
                             "anomalies": document["anomalies"],
                             "cursor_seq": document["cursor_seq"]})
            return {"days": days, "totals": with_ratios(total), "timezone": self.timezone,
                    "rule_version": RULE_VERSION, "cursor_seq": self.cursor_seq,
                    "incomplete": self.state["incomplete"] or any(d["incomplete"] for d in days),
                    "gaps": copy.deepcopy(self.state["gaps"]),
                    "pruned_before": self.state.get("pruned_before")}

    def cleanup(self, before_date):
        with self._lock:
            self._assert_open()
            paths = [p for p in self.directory.glob("????-??-??.json") if p.stem < before_date]
            if not paths:
                return 0
            following = copy.deepcopy(self.state)
            following["pruned_before"] = max(before_date, following.get("pruned_before") or before_date)
            # First persist the boundary so a crash cannot recreate deleted days.
            atomic_json(self.state_path, following, self._observer)
            self.state = following
            for path in paths:
                path.unlink()
            return len(paths)

    def close(self):
        if self._writer:
            self._writer.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
