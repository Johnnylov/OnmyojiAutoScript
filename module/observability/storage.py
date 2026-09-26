"""Durable single-writer JSONL event history with bounded retention.

The WAL is authoritative. Append -> fsync -> dedup commit -> aggregation -> ACK.
An append failure has an UNKNOWN commit outcome: retry the SAME event_id. On
restart, scanning repairs an incomplete final line and reconstructs dedup before
accepting requests. A damaged complete/interior line is reported as a gap.
"""
from __future__ import annotations

import base64
import copy
import gzip
import hashlib
import json
import math
import os
import shutil
import threading
import time
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path

from .common import (
    SCHEMA_VERSION, TERMINAL, DirectoryLock, StorageError, StorageUnavailable,
    StorageRestricted, HistoryExpired, ReplayExpired, TerminalConflict,
    SchemaError, atomic_json, read_json, encode, fsync_directory,
    iso, timestamp, utcnow, redact, report_zone,
)
from .checkpoint import CheckpointStore
from .summary import SummaryStore, seconds


@dataclass
class StoragePolicy:
    mode: str = "standard"
    event_retention_days: int = 14
    summary_retention_days: int = 180
    normal_budget_bytes: int = 64 * 1024 * 1024
    temporary_reserve_bytes: int = 16 * 1024 * 1024
    shard_max_bytes: int = 2 * 1024 * 1024
    event_max_bytes: int = 8 * 1024
    retry_window_seconds: int = 14 * 86400
    sequence_reservation: int = 64
    query_max_bytes: int = 8 * 1024 * 1024
    query_timeout_seconds: float = 1.0
    timezone: str = "Asia/Shanghai"

    def validate(self):
        if self.mode not in {"standard", "summary_only", "off"}:
            raise ValueError("Invalid history mode")
        for key in ("event_retention_days", "summary_retention_days", "normal_budget_bytes",
                    "temporary_reserve_bytes", "shard_max_bytes", "event_max_bytes",
                    "retry_window_seconds", "sequence_reservation", "query_max_bytes"):
            value = getattr(self, key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{key} must be a positive integer")
        if self.event_max_bytes < 1024 or self.shard_max_bytes < self.event_max_bytes:
            raise ValueError("Events need >= 1024 bytes and must fit inside a shard")
        if not math.isfinite(self.query_timeout_seconds) or self.query_timeout_seconds <= 0:
            raise ValueError("query_timeout_seconds must be positive")
        report_zone(self.timezone)
        return self


MINIMAL_TYPES = {"run.created", "run.started", "run.finished", "segment.started",
                 "segment.finished", "run.yielded", "run.paused", "run.resumed",
                 "recovery.requested", "recovery.resolved", "control.requested",
                 "control.completed", "config.changed", "strategy.changed",
                 "storage.degraded", "storage.recovered"}
CRITICAL_TYPES = MINIMAL_TYPES
STAT_FIELDS = {"outcome", "error_category", "duration_seconds", "device_seconds",
               "execution_seconds", "queue_wait_seconds", "started_at", "finished_at",
               "terminal", "verified", "reason", "action", "status", "revision",
               "config_revision", "checkpoint_id", "task_id", "result"}


class EventStore:
    """Thread-safe synchronous owner. ACK means fsync completed, never queued.

    All readers should use this instance as well. For async servers call through
    a bounded worker/IPC queue; do not block the event loop on disk operations.
    """
    def __init__(self, root, policy=None, clock=None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._writer_lock = DirectoryLock(self.root / "meta" / "writer.lock")
        self._clock = clock or utcnow
        self._closed = False
        self._status = "healthy"
        self._last_error = None
        self._needs_reconciliation = False
        self._peak_bytes = 0
        self._cleanup_running = False
        self._write_uncertain = False
        self._maintenance_at = time.monotonic()
        try:
            for directory in ("events", "summaries", "checkpoints", "meta", "temp"):
                (self.root / directory).mkdir(exist_ok=True)
            self._orphan_temp_bytes = 0
            for directory in ("summaries", "checkpoints", "meta"):
                for temporary in (self.root / directory).rglob(".*.tmp"):
                    self._orphan_temp_bytes += temporary.stat().st_size
                    temporary.unlink()
            for temporary in (self.root / "temp").glob("events-*.jsonl.gz.tmp"):
                self._orphan_temp_bytes += temporary.stat().st_size
                temporary.unlink()
            self.manifest_path = self.root / "meta" / "manifest.json"
            self.dedup_path = self.root / "meta" / "dedup.json"
            self.manifest = read_json(self.manifest_path, {
                "schema_version": SCHEMA_VERSION, "reserved_through": 0,
                "committed_seq": 0, "next_shard": 1, "generation": 0,
                "shards": [], "gaps": [], "dedup_floor": None,
                "reconciled_through": 0, "policy": asdict(StoragePolicy()),
            })
            stored_policy = self.manifest["policy"]
            self._needs_reconciliation = bool(self.manifest.get("reconciliation_required", False))
            if self._needs_reconciliation:
                self._status = "restricted"
            merged = {**stored_policy, **(asdict(policy) if isinstance(policy, StoragePolicy) else (policy or {}))}
            self.policy = StoragePolicy(**merged).validate()
            self.manifest["policy"] = asdict(self.policy)
            self.dedup = read_json(self.dedup_path, {
                "schema_version": SCHEMA_VERSION, "events": {}, "business": {},
                "active_runs": {}, "pending_requests": {},
            })
            self._next_seq = int(self.manifest["reserved_through"]) + 1
            self.summaries = SummaryStore(self.root, timezone=self.policy.timezone,
                                          observer=self._observe, lock=self._lock, assert_open=self._assert_open)
            self.checkpoints = CheckpointStore(self.root,
                committed_seq=lambda: self.manifest["committed_seq"],
                observer=self._observe, lock=self._lock, assert_open=self._assert_open,
                is_persisted=lambda seq: any(entry["seq"] == seq for entry in self.dedup["events"].values()))
            self._recover()
        except BaseException:
            self._writer_lock.close()
            raise

    def _now(self):
        return timestamp(self._clock())

    def _assert_open(self):
        if self._closed:
            raise StorageUnavailable("EventStore is closed")

    def _usage(self):
        buckets = {key: 0 for key in ("events", "summaries", "checkpoints", "meta", "temp", "other")}
        for path in self.root.rglob("*"):
            if path.is_file():
                key = path.relative_to(self.root).parts[0]
                buckets[key if key in buckets else "other"] += path.stat().st_size
        return buckets

    def _observe(self, phase, new_bytes):
        if phase == "failed":
            self._degrade("A persistence operation failed; reconcile before dispatch")
            return
        usage = sum(self._usage().values())
        self._peak_bytes = max(self._peak_bytes, usage)
        if phase == "before":
            hard = self.policy.normal_budget_bytes + self.policy.temporary_reserve_bytes
            if usage + new_bytes > hard or shutil.disk_usage(self.root).free < new_bytes + 4096:
                self._degrade("Insufficient storage for an atomic commit")
                raise StorageUnavailable(self._last_error)

    def _degrade(self, message):
        self._status = "unavailable"
        self._needs_reconciliation = True
        self._last_error = str(message)
        self._write_uncertain = True

    def _save_manifest(self):
        self.manifest["reconciliation_required"] = self._needs_reconciliation
        atomic_json(self.manifest_path, self.manifest, self._observe)

    def _save_dedup(self):
        atomic_json(self.dedup_path, self.dedup, self._observe)

    def _gap(self, shard, reason, offset=None, seq=None):
        gap = {"shard": shard, "reason": reason}
        if offset is not None:
            gap["offset"] = offset
        if seq is not None:
            gap["seq"] = seq
        if gap not in self.manifest["gaps"]:
            self.manifest["gaps"].append(gap)
        self.summaries.mark_gap(gap)
        self._needs_reconciliation = True
        self._status = "restricted"

    def _read_shard(self, path, *, repair=False, last=False):
        """Return valid records. Corrupt content is retained in place as evidence."""
        result = []
        compressed = path.name.endswith(".gz")
        try:
            with (gzip.open(path, "rb") if compressed else path.open("rb")) as stream:
                offset = 0
                while True:
                    line = stream.readline(self.policy.event_max_bytes + 2)
                    if not line:
                        break
                    start = offset
                    offset += len(line)
                    if not line.endswith(b"\n"):
                        # A full-size line without a newline may be malicious or
                        # corrupted interior data, never treat that as torn tail.
                        remainder = stream.readline() if len(line) > self.policy.event_max_bytes else b""
                        offset += len(remainder)
                        more = stream.peek(1) if hasattr(stream, "peek") else b"?"
                        if not compressed and repair and last and not more and not remainder and len(line) <= self.policy.event_max_bytes:
                            with path.open("r+b") as writable:
                                writable.truncate(start)
                                writable.flush()
                                os.fsync(writable.fileno())
                            self._gap(path.name, "torn_tail_repaired", start)
                            break
                        self._gap(path.name, "corrupt_record", start)
                        continue
                    try:
                        event = json.loads(line)
                        if event.get("schema_version") != SCHEMA_VERSION:
                            raise SchemaError(f"Unsupported event schema in {path.name}")
                        if not isinstance(event["seq"], int) or not isinstance(event["event_id"], str):
                            raise ValueError("invalid envelope")
                        timestamp(event["occurred_at"])
                        result.append(event)
                    except SchemaError:
                        raise
                    except (ValueError, KeyError, TypeError):
                        self._gap(path.name, "corrupt_record", start)
        except (EOFError, gzip.BadGzipFile, OSError) as exc:
            self._gap(path.name, "unreadable_shard")
        return result

    def _business_key(self, event):
        kind = event["type"]
        if kind in {"run.started", "run.finished"} and event.get("run_id"):
            return kind + ":" + event["run_id"]
        if kind in {"segment.started", "segment.finished"} and event.get("segment_id"):
            return kind + ":" + event["segment_id"]
        if kind in {"control.requested", "control.completed"} and event.get("request_id"):
            return kind + ":" + event["request_id"]
        return None

    def _index(self, event):
        entry = {"seq": event["seq"], "event_id": event["event_id"],
                 "recorded_at": event["recorded_at"], "type": event["type"],
                 "run_id": event.get("run_id"), "request_id": event.get("request_id"),
                 "outcome": event["payload"].get("outcome")}
        self.dedup["events"][event["event_id"]] = entry
        business = self._business_key(event)
        if business:
            self.dedup["business"].setdefault(business, entry)
        run_id, request_id = event.get("run_id"), event.get("request_id")
        if run_id and event["type"] in {"run.created", "run.started"}:
            # Retained late starts cannot resurrect a known finished run.
            if "run.finished:" + run_id not in self.dedup["business"]:
                self.dedup["active_runs"][run_id] = event["seq"]
        if run_id and event["type"] == "run.finished":
            self.dedup["active_runs"].pop(run_id, None)
        if request_id and event["type"] == "control.requested":
            if "control.completed:" + request_id not in self.dedup["business"]:
                self.dedup["pending_requests"][request_id] = event["seq"]
        if request_id and event["type"] == "control.completed":
            self.dedup["pending_requests"].pop(request_id, None)

    def _recover(self):
        for name in self.manifest.get("deleted_shards", []):
            # Persisted deletion intents have already passed all watermarks.
            if Path(name).name != name:
                raise SchemaError("Invalid deletion tombstone")
            (self.root / "events" / name).unlink(missing_ok=True)
        self.manifest["deleted_shards"] = []
        expected = {item["name"]: item for item in self.manifest["shards"]}
        for name in expected:
            path = self.root / "events" / name
            if not path.exists() and not path.with_suffix(path.suffix + ".gz").exists():
                self._gap(name, "missing_shard")
        paths = sorted((self.root / "events").glob("events-*.jsonl*"))
        # Compression can crash after new .gz commit but before old unlink.
        # Prefer uncompressed original and safely discard redundant compressed
        # copy only after verifying identical uncompressed bytes.
        for path in list(paths):
            if path.name.endswith(".gz") and path.with_suffix("").exists():
                try:
                    with gzip.open(path, "rb") as stream:
                        equal = stream.read() == path.with_suffix("").read_bytes()
                    if equal:
                        path.unlink()
                        paths.remove(path)
                    else:
                        self._gap(path.name, "compression_copy_mismatch")
                        paths.remove(path)
                except (OSError, EOFError):
                    self._gap(path.name, "compression_copy_corrupt")
                    paths.remove(path)
        shards, all_events = [], []
        seen, previous = set(), 0
        for index, path in enumerate(paths):
            events = self._read_shard(path, repair=True, last=index == len(paths) - 1)
            number = int(path.name.split("-")[1].split(".")[0])
            self.manifest["next_shard"] = max(self.manifest["next_shard"], number + 1)
            if not events:
                continue
            for event in events:
                if event["seq"] <= previous:
                    self._gap(path.name, "sequence_not_monotonic", seq=event["seq"])
                    continue
                previous = event["seq"]
                if event["event_id"] in seen:
                    self._gap(path.name, "duplicate_event_record", seq=event["seq"])
                    continue
                seen.add(event["event_id"])
                self._index(event)
                all_events.append(event)
            shards.append(self._shard_metadata(path, events, closed=True))
        self.manifest["shards"] = shards
        committed = max([self.manifest["committed_seq"]] + [e["seq"] for e in all_events])
        if committed > self.manifest["reserved_through"]:
            # Never silently repair high-water metadata: it is our anti-reuse
            # boundary and can be changed only with external reconciliation.
            raise SchemaError("Event sequence exceeds durable reserved high water")
        self.manifest["committed_seq"] = committed
        for event in all_events:
            self.summaries.apply(event)
        self._save_dedup()
        self._save_manifest()
        self._peak_bytes = sum(self._usage().values())

    def _shard_metadata(self, path, events, closed):
        return {"name": path.name, "first_seq": events[0]["seq"], "last_seq": events[-1]["seq"],
                "count": len(events), "first_at": min(e["recorded_at"] for e in events),
                "last_at": max(e["recorded_at"] for e in events), "closed": closed,
                "bytes": path.stat().st_size}

    def _normalize(self, event):
        if not isinstance(event, dict) or not event.get("event_id") or not event.get("type"):
            raise ValueError("event_id and type are required; retries must reuse event_id")
        if event.get("schema_version", 1) != SCHEMA_VERSION:
            raise SchemaError("Unsupported submitted event schema")
        for key in ("event_id", "type", "profile_id", "device_id", "run_id", "segment_id", "request_id", "task_id"):
            if event.get(key) is not None and (not isinstance(event[key], str) or len(event[key]) > 256):
                raise ValueError(f"Invalid {key}")
        result = {"schema_version": SCHEMA_VERSION, "event_id": event["event_id"],
                  "type": event["type"], "occurred_at": iso(event.get("occurred_at") or self._now()),
                  "recorded_at": iso(self._now()), "source": redact(event.get("source", {"type": "local"})),
                  "payload": redact(event.get("payload", {})),
                  "summary_required": self.policy.mode != "off", "recording_mode": self.policy.mode}
        if not isinstance(result["payload"], dict):
            raise ValueError("payload must be an object")
        for key in ("profile_id", "device_id", "run_id", "segment_id", "request_id", "task_id", "config_revision"):
            if event.get(key) is not None:
                result[key] = redact(event[key])
        payload = result["payload"]
        if result["type"] == "run.finished" and payload.get("outcome") not in TERMINAL:
            raise ValueError("run.finished requires outcome")
        if result["type"].startswith(("run.", "segment.")) and not result.get("run_id"):
            raise ValueError("Run lifecycle events require run_id")
        if result["type"] in {"segment.started", "segment.finished"} and not result.get("segment_id"):
            raise ValueError("Segment events require segment_id")
        for key in ("duration_seconds", "device_seconds", "execution_seconds", "queue_wait_seconds"):
            if key in payload:
                seconds(payload[key])
        if self.policy.mode == "off":
            # Recovery/control minimum only; disabling history must not keep
            # ordinary full configuration diffs or candidate snapshots.
            result["payload"] = {key: item for key, item in payload.items() if key in STAT_FIELDS}
            result["payload"]["history_reduced"] = True
        return result

    def _bound_event(self, event):
        if len(encode(event)) + 1 <= self.policy.event_max_bytes:
            return event
        payload = event["payload"]
        # Preserve fields required for statistical correctness; discard oversized
        # candidate snapshots/diffs rather than truncate the JSON byte stream.
        retained = {k: v for k, v in payload.items() if k in STAT_FIELDS and len(encode(v)) < 512}
        event["payload"] = {**retained, "truncated": True,
                            "omitted_fields": list(payload)[:32]}
        event["source"] = {"type": str(event["source"].get("type", "local"))[:64]} if isinstance(event["source"], dict) else {"type": "local"}
        if len(encode(event)) + 1 > self.policy.event_max_bytes:
            event["payload"].pop("omitted_fields")
        if len(encode(event)) + 1 > self.policy.event_max_bytes:
            raise ValueError("Event envelope exceeds maximum size")
        return event

    def _ack(self, entry, *, duplicate=False, elapsed=0):
        return {"status": "saved", "persisted": True, "event_id": entry["event_id"],
                "seq": entry["seq"], "duplicate": duplicate,
                "duration_ms": round(elapsed * 1000, 3), "delayed": elapsed > .250,
                "summary_saved": entry["seq"] <= self.summaries.cursor_seq}

    def append(self, event):
        started = time.monotonic()
        with self._lock:
            self._assert_open()
            if self._write_uncertain:
                # A failed fsync/metadata commit has unknown outcome. Reload
                # durable watermarks, then discover any appended record before
                # retrying, even within the same process.
                self.manifest = read_json(self.manifest_path, self.manifest)
                self.dedup = read_json(self.dedup_path, self.dedup)
                self._next_seq = int(self.manifest["reserved_through"]) + 1
                self.summaries = SummaryStore(self.root, timezone=self.policy.timezone,
                                              observer=self._observe, lock=self._lock, assert_open=self._assert_open)
                self._recover()
                self._write_uncertain = False
            if time.monotonic() - self._maintenance_at >= 60:
                self.cleanup(compress=False)
            value = self._normalize(event)
            existing = self.dedup["events"].get(value["event_id"])
            business = self._business_key(value)
            if existing is None and business:
                existing = self.dedup["business"].get(business)
                if existing and value["type"] == "run.finished" and existing["outcome"] != value["payload"]["outcome"]:
                    raise TerminalConflict("Logical run already has a different terminal outcome")
            if existing:
                if existing["seq"] > self.summaries.cursor_seq:
                    self._recover()
                return self._ack(existing, duplicate=True, elapsed=time.monotonic() - started)
            floor = self.manifest.get("dedup_floor")
            if floor and value["occurred_at"] < floor:
                raise ReplayExpired("Event predates retained dedup window; reconcile before replay")
            if self.policy.mode == "off" and value["type"] not in MINIMAL_TYPES:
                return {"status": "not_recorded", "persisted": False,
                        "event_id": value["event_id"], "reason": "history_off"}
            # Reserve pessimistically for atomic metadata/summary temp copies.
            estimated = self.policy.event_max_bytes + len(encode(self.dedup)) + len(encode(self.manifest)) + 16384
            growth = self.policy.event_max_bytes + 4096
            usage = sum(self._usage().values())
            if usage + growth > self.policy.normal_budget_bytes:
                self.cleanup(compress=False, _target_bytes=max(0, self.policy.normal_budget_bytes - growth))
                usage = sum(self._usage().values())
            if usage + growth > self.policy.normal_budget_bytes and value["type"] not in CRITICAL_TYPES:
                self._status = "restricted"
                self._last_error = "Optional detail recording paused by capacity budget"
                gaps = self.manifest.setdefault("collection_gaps", [])
                if not gaps or gaps[-1].get("reason") != "capacity_restriction":
                    gaps.append({"reason": "capacity_restriction", "occurred_at": iso(self._now())})
                    self.manifest["collection_gaps"] = gaps[-100:]
                    self._save_manifest()
                raise StorageRestricted(self._last_error)
            if usage + estimated > self.policy.normal_budget_bytes + self.policy.temporary_reserve_bytes:
                self._degrade("Critical history cannot be saved within total protection threshold")
                raise StorageUnavailable(self._last_error)
            try:
                if self._next_seq > self.manifest["reserved_through"]:
                    self.manifest["reserved_through"] = self._next_seq + self.policy.sequence_reservation - 1
                    self._save_manifest()
                value["seq"] = self._next_seq
                self._next_seq += 1
                value = self._bound_event(value)
                line = encode(value) + b"\n"
                shard = self.manifest["shards"][-1] if self.manifest["shards"] else None
                today = value["recorded_at"][:10]
                if shard and (shard["closed"] or shard["bytes"] + len(line) > self.policy.shard_max_bytes or shard["last_at"][:10] != today):
                    shard["closed"] = True
                    shard = None
                if shard is None:
                    number = self.manifest["next_shard"]
                    self.manifest["next_shard"] += 1
                    shard = {"name": f"events-{number:06d}.jsonl", "first_seq": value["seq"],
                             "last_seq": value["seq"], "count": 0, "first_at": value["recorded_at"],
                             "last_at": value["recorded_at"], "closed": False, "bytes": 0}
                    self.manifest["shards"].append(shard)
                path = self.root / "events" / shard["name"]
                self._observe("before", len(line))
                with path.open("ab") as stream:
                    stream.write(line)
                    stream.flush()
                    os.fsync(stream.fileno())
                fsync_directory(path.parent)
                shard.update(last_seq=value["seq"], last_at=value["recorded_at"],
                             count=shard["count"] + 1, bytes=path.stat().st_size)
                self.manifest["committed_seq"] = value["seq"]
                self._index(value)
                self._save_dedup()
                self._save_manifest()
                self.summaries.apply(value)
                self._peak_bytes = max(self._peak_bytes, sum(self._usage().values()))
                return self._ack(value, elapsed=time.monotonic() - started)
            except (OSError, StorageUnavailable) as exc:
                self._degrade(exc)
                raise StorageUnavailable("Commit outcome unknown; retry the same event_id after recovery") from exc

    def _cursor(self, document):
        return base64.urlsafe_b64encode(encode(document)).decode("ascii")

    def query(self, filters=None, cursor=None, limit=50):
        filters = dict(filters or {})
        if not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        fingerprint = hashlib.sha256(encode(filters)).hexdigest()[:24]
        started = time.monotonic()
        with self._lock:
            self._assert_open()
            snapshot = self.manifest["committed_seq"]
            anchor = snapshot + 1
            generation = self.manifest["generation"]
            if cursor:
                try:
                    saved = json.loads(base64.urlsafe_b64decode(cursor.encode()))
                except Exception as exc:
                    raise ValueError("Invalid history cursor") from exc
                if saved.get("generation") != generation:
                    raise HistoryExpired("History changed during pagination; start a new query")
                if saved.get("filters") != fingerprint:
                    raise ValueError("Cursor filters do not match")
                snapshot, anchor = int(saved["snapshot"]), int(saved["anchor"])
            items, scanned, last_scanned, bounded = [], 0, anchor, None
            more = False
            for shard in reversed(self.manifest["shards"]):
                if shard["first_seq"] >= anchor or shard["first_seq"] > snapshot:
                    continue
                if scanned + shard["bytes"] > self.policy.query_max_bytes:
                    bounded, more = "scan_budget", True
                    break
                events = self._read_shard(self.root / "events" / shard["name"])
                scanned += sum(len(encode(item)) + 1 for item in events)
                for event in reversed(events):
                    seq = event["seq"]
                    if seq >= anchor or seq > snapshot:
                        continue
                    if len(items) >= limit:
                        more = True
                        break
                    if time.monotonic() - started >= self.policy.query_timeout_seconds:
                        bounded, more = "timeout", True
                        break
                    last_scanned = seq
                    if self._matches(event, filters):
                        items.append(event)
                if more:
                    break
            next_cursor = self._cursor({"generation": generation, "snapshot": snapshot,
                                       "anchor": last_scanned, "filters": fingerprint}) if more and last_scanned != anchor else None
            return {"items": items, "next_cursor": next_cursor, "snapshot_seq": snapshot,
                    "incomplete": bool(self.manifest["gaps"] or self.manifest.get("collection_gaps")),
                    "gaps": copy.deepcopy(self.manifest["gaps"] + self.manifest.get("collection_gaps", [])),
                    "bounded_reason": bounded, "scanned_bytes": scanned,
                    "history_pruned": bool(self.manifest.get("removed_ranges")),
                    "removed_ranges": copy.deepcopy(self.manifest.get("removed_ranges", [])),
                    "earliest_available_at": self.manifest["shards"][0]["first_at"] if self.manifest["shards"] else None}

    def _matches(self, event, filters):
        for field in ("profile_id", "device_id", "task_id", "run_id", "request_id", "type"):
            if field in filters and event.get(field) != filters[field]:
                return False
        if filters.get("types") and event["type"] not in filters["types"]:
            return False
        if filters.get("type_prefix") and not event["type"].startswith(filters["type_prefix"]):
            return False
        if filters.get("outcome") and event["payload"].get("outcome") != filters["outcome"]:
            return False
        if filters.get("after_seq") is not None and event["seq"] <= int(filters["after_seq"]):
            return False
        if filters.get("start_at") and event["occurred_at"] < iso(filters["start_at"]):
            return False
        if filters.get("end_at") and event["occurred_at"] > iso(filters["end_at"]):
            return False
        local_day = timestamp(event["occurred_at"]).astimezone(report_zone(self.policy.timezone)).date().isoformat()
        if filters.get("start_date") and local_day < filters["start_date"]:
            return False
        if filters.get("end_date") and local_day > filters["end_date"]:
            return False
        return True

    def statistics(self, start_date=None, end_date=None, profile_id=None, task_id=None):
        with self._lock:
            result = self.summaries.query(start_date, end_date, profile_id, task_id)
            result["details_available_from"] = self.manifest["shards"][0]["first_at"] if self.manifest["shards"] else None
            result["recording_mode"] = self.policy.mode
            return result

    def storage_status(self):
        with self._lock:
            usage = self._usage()
            days = sorted(p.stem for p in (self.root / "summaries").glob("????-??-??.json"))
            return {"state": self._status, "last_error": self._last_error,
                    "needs_reconciliation": self._needs_reconciliation,
                    "dispatch_allowed": self._status != "unavailable" and not self._needs_reconciliation,
                    "bytes": usage, "total_bytes": sum(usage.values()), "peak_bytes": self._peak_bytes,
                    "policy": asdict(self.policy), "committed_seq": self.manifest["committed_seq"],
                    "reserved_through": self.manifest["reserved_through"],
                    "summary_cursor": self.summaries.cursor_seq,
                    "detail_from": self.manifest["shards"][0]["first_at"] if self.manifest["shards"] else None,
                    "summary_from": days[0] if days else None, "summary_to": days[-1] if days else None,
                    "gaps": copy.deepcopy(self.manifest["gaps"] + self.manifest.get("collection_gaps", [])),
                    "removed_ranges": copy.deepcopy(self.manifest.get("removed_ranges", [])),
                    "orphan_temp_bytes_reclaimed": self._orphan_temp_bytes,
                    "scope": "runtime_data_only", "dedup_floor": self.manifest.get("dedup_floor"),
                    "pending_runs": len(self.dedup["active_runs"]),
                    "pending_requests": len(self.dedup["pending_requests"])}

    def set_policy(self, policy):
        with self._lock:
            self._assert_open()
            candidate = StoragePolicy(**{**asdict(self.policy), **dict(policy)}).validate()
            if candidate.timezone != self.policy.timezone and (self.summaries.cursor_seq or list((self.root / "summaries").glob("*.json"))):
                raise ValueError("Changing reporting timezone requires an explicit rebuild from complete retained events")
            self.policy = candidate
            self.manifest["policy"] = asdict(candidate)
            try:
                self._save_manifest()
            except (OSError, StorageUnavailable) as exc:
                self._degrade(exc)
                raise StorageUnavailable("Storage policy was not durably saved") from exc
            return self.storage_status()

    def acknowledge_recovery(self):
        """Call ONLY after application reconciles device/run/checkpoint state."""
        with self._lock:
            self.manifest["reconciled_through"] = self.manifest["committed_seq"]
            self._needs_reconciliation = False
            try:
                self._save_manifest()
                self._status = "healthy"
                self._last_error = None
            except (OSError, StorageUnavailable) as exc:
                self._degrade(exc)
                raise StorageUnavailable("Recovery acknowledgement was not saved") from exc
            return self.storage_status()

    def _safe_to_delete(self, events, checkpoints, cutoff):
        consumer_cursors = [int(cp["data"].get("cursor_seq", 0)) for cp in checkpoints
                            if cp["data"].get("retention_consumer")]
        for event in events:
            if consumer_cursors and event["seq"] > min(consumer_cursors):
                return False
            run_id, request_id = event.get("run_id"), event.get("request_id")
            if run_id in self.dedup["active_runs"] or request_id in self.dedup["pending_requests"]:
                return False
            if any(cp["event_seq"] == event["seq"] and not cp["data"].get("terminal")
                   and not cp["data"].get("retention_consumer") for cp in checkpoints):
                return False
            if event.get("summary_required", True):
                if event["seq"] > self.summaries.cursor_seq:
                    return False
            else:
                if event["recorded_at"] >= cutoff:
                    return False
                associated = [cp for cp in checkpoints if cp["key"] in {run_id, request_id}]
                covered = any(cp["event_seq"] >= event["seq"] and cp["data"].get("verified") for cp in associated)
                if not covered:
                    return False
        return True

    def _prune_dedup(self, cutoff):
        retained = {}
        for key, entry in self.dedup["events"].items():
            if entry["recorded_at"] >= cutoff or entry.get("run_id") in self.dedup["active_runs"] or entry.get("request_id") in self.dedup["pending_requests"]:
                retained[key] = entry
        self.dedup["events"] = retained
        self.dedup["business"] = {key: entry for key, entry in self.dedup["business"].items() if entry["event_id"] in retained}
        self.manifest["dedup_floor"] = max(cutoff, self.manifest.get("dedup_floor") or cutoff)

    def cleanup(self, force=False, *, compress=True, _target_bytes=None):
        with self._lock:
            self._assert_open()
            if self._cleanup_running:
                return {"removed_shards": 0, "removed_summaries": 0, "compressed_shards": 0}
            self._cleanup_running = True
            try:
                now = self._now()
                detail_cutoff = iso(now - timedelta(days=self.policy.event_retention_days))
                dedup_cutoff = iso(now - timedelta(seconds=self.policy.retry_window_seconds))
                checkpoints = self.checkpoints.list()
                target_bytes = self.policy.normal_budget_bytes if _target_bytes is None else _target_bytes
                removed, compressed = [], 0
                # Rotate current shard before explicit cleanup; an empty shard
                # is not created until the next append.
                if force and self.manifest["shards"]:
                    self.manifest["shards"][-1]["closed"] = True
                for shard in list(self.manifest["shards"]):
                    if not shard["closed"]:
                        continue
                    over = sum(self._usage().values()) > target_bytes
                    expired = shard["last_at"] < detail_cutoff
                    summary_only = self.policy.mode == "summary_only"
                    if not (force or expired or over or summary_only):
                        continue
                    path = self.root / "events" / shard["name"]
                    events = self._read_shard(path)
                    if any(g["shard"] == shard["name"] for g in self.manifest["gaps"]):
                        continue
                    if not self._safe_to_delete(events, checkpoints, dedup_cutoff):
                        continue
                    # Persist removal intent and generation BEFORE unlink. A
                    # concurrent/continued query must not reuse its old cursor.
                    self.manifest["shards"].remove(shard)
                    self.manifest["generation"] += 1
                    self.manifest.setdefault("deleted_shards", []).append(shard["name"])
                    self.manifest.setdefault("removed_ranges", []).append({key: shard[key] for key in ("first_seq", "last_seq", "first_at", "last_at")})
                    self.manifest["removed_ranges"] = self.manifest["removed_ranges"][-100:]
                    self._save_manifest()
                    path.unlink(missing_ok=True)
                    removed.append(shard["name"])
                self._prune_dedup(dedup_cutoff)
                self._save_dedup()
                # Maintain deletion tombstones only for unlink crash recovery.
                self.manifest["deleted_shards"] = []
                summary_cutoff = (now.astimezone(report_zone(self.policy.timezone)).date() - timedelta(days=self.policy.summary_retention_days)).isoformat()
                removed_summaries = self.summaries.cleanup(summary_cutoff)
                # Capacity wins over retention for old summaries, but preserve
                # today's data and active run state held separately in metadata.
                while sum(self._usage().values()) > target_bytes:
                    paths = sorted((self.root / "summaries").glob("????-??-??.json"))
                    if len(paths) <= 1:
                        break
                    removed_summaries += self.summaries.cleanup(paths[1].stem)
                removed_checkpoints = self.checkpoints.reclaim(dedup_cutoff)
                if compress:
                    for shard in self.manifest["shards"]:
                        if shard["closed"] and not shard["name"].endswith(".gz"):
                            compressed += bool(self._compress(shard))
                self._save_manifest()
                return {"removed_shards": len(removed), "removed_summaries": removed_summaries,
                        "removed_checkpoints": removed_checkpoints, "compressed_shards": compressed, "total_bytes": sum(self._usage().values()),
                        "peak_bytes": self._peak_bytes}
            except (OSError, StorageUnavailable) as exc:
                self._degrade(exc)
                raise StorageUnavailable("History cleanup did not complete") from exc
            finally:
                self._cleanup_running = False
                self._maintenance_at = time.monotonic()

    def _compress(self, shard):
        source = self.root / "events" / shard["name"]
        size = source.stat().st_size
        hard = self.policy.normal_budget_bytes + self.policy.temporary_reserve_bytes
        if sum(self._usage().values()) + size + 4096 > hard or shutil.disk_usage(self.root).free < size + 4096:
            return False
        temporary = self.root / "temp" / (source.name + ".gz.tmp")
        target = source.with_suffix(source.suffix + ".gz")
        try:
            with source.open("rb") as original, temporary.open("wb") as output:
                with gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as compressor:
                    shutil.copyfileobj(original, compressor)
                output.flush()
                os.fsync(output.fileno())
            self._peak_bytes = max(self._peak_bytes, sum(self._usage().values()))
            with gzip.open(temporary, "rb") as candidate:
                if candidate.read() != source.read_bytes():
                    raise OSError("Compressed shard verification failed")
            os.replace(temporary, target)
            fsync_directory(target.parent)
            shard["name"] = target.name
            shard["bytes"] = target.stat().st_size
            self._save_manifest()
            source.unlink()
            return True
        finally:
            temporary.unlink(missing_ok=True)

    def close(self):
        with self._lock:
            if not self._closed:
                self._closed = True
                self._writer_lock.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
