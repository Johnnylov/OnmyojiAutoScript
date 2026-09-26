"""Bounded, reproducible synthetic near-budget query benchmark.

This seeds a valid corpus directly to avoid conflating fixture generation with
durable-write performance. It is NOT a 24-hour soak or a filesystem fault test.
Only measured append samples go through the normal fsync/ACK path. All data is
created in an automatically removed temporary directory.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import platform
import random
import statistics
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from module.observability import EventStore, StoragePolicy
from module.observability.common import atomic_json, encode, iso
from module.observability.summary import empty_totals, RULE_VERSION
from dataclasses import asdict


def seed(root, count=9000, padding=6800):
    for directory in ("events", "summaries", "meta"):
        (root / directory).mkdir(parents=True)
    now = datetime.now(timezone.utc)
    origin = now - timedelta(days=13)
    shards = []
    shard_size = 0
    shard = None
    rng = random.Random(9124)
    source = "".join(rng.choice("0123456789abcdef") for _ in range(padding))
    for index in range(1, count + 1):
        point = origin + (now - origin) * (index / count)
        value = {"schema_version": 1, "seq": index, "event_id": f"fixture-{index}",
                 "type": "scheduler.selected", "occurred_at": iso(point), "recorded_at": iso(point),
                 "source": {"type": "fixture"}, "profile_id": f"p{index % 4}", "task_id": f"task{index % 30}",
                 "payload": {"reason": "bounded benchmark fixture", "diagnostic": source},
                 "summary_required": True, "recording_mode": "standard"}
        line = encode(value) + b"\n"
        if shard is None or shard_size + len(line) > 2 * 1024 * 1024:
            if shard:
                stream.close()
            number = len(shards) + 1
            name = f"events-{number:06d}.jsonl"
            stream = (root / "events" / name).open("wb")
            shard = {"name": name, "first_seq": index, "last_seq": index, "first_at": iso(point),
                     "last_at": iso(point), "closed": True, "count": 0, "bytes": 0}
            shards.append(shard)
            shard_size = 0
        stream.write(line)
        shard_size += len(line)
        shard.update(last_seq=index, last_at=iso(point), count=shard["count"] + 1, bytes=shard_size)
    stream.close()
    for index in range(180):
        day = (now - timedelta(days=index)).date().isoformat()
        groups = {}
        total = empty_totals()
        for number in range(120):
            values = empty_totals()
            values.update(started=2, succeeded=1, failed=1, success_samples=1,
                          device_seconds=120, successful_execution_seconds=60, queue_wait_seconds=4)
            groups[json.dumps([f"p{number // 30}", f"task{number % 30}"])] = {
                "profile_id": f"p{number // 30}", "task_id": f"task{number % 30}", "totals": values}
            for key, value in values.items():
                if key != "failure_categories":
                    total[key] += value
        # Older summaries represent retained aggregate history whose details
        # have expired; fixture values are only for query cost measurement.
        atomic_json(root / "summaries" / f"{day}.json", {
            "schema_version": 1, "date": day, "timezone": "Asia/Shanghai", "rule_version": RULE_VERSION,
            "cursor_seq": count, "totals": total, "groups": groups, "incomplete": False, "anomalies": []})
    atomic_json(root / "meta" / "manifest.json", {
        "schema_version": 1, "reserved_through": ((count + 63) // 64) * 64,
        "committed_seq": count, "next_shard": len(shards) + 1, "generation": 0,
        "shards": shards, "gaps": [], "dedup_floor": None, "reconciled_through": count,
        "policy": asdict(StoragePolicy()),
    })
    atomic_json(root / "meta" / "summary_cursor.json", {
        "schema_version": 1, "cursor_seq": count, "timezone": "Asia/Shanghai", "rule_version": RULE_VERSION,
        "runs": {}, "incomplete": False, "gaps": [], "pruned_before": None,
    })


def measure(function, samples):
    values = []
    last = None
    for _ in range(samples):
        before = time.perf_counter()
        last = function()
        values.append((time.perf_counter() - before) * 1000)
    ordered = sorted(values)
    return {"samples": samples, "first_ms": round(values[0], 3),
            "median_ms": round(statistics.median(values), 3),
            "p95_ms": round(ordered[max(0, math.ceil(.95 * len(ordered)) - 1)], 3),
            "max_ms": round(max(values), 3),
            "last_bounded_reason": last.get("bounded_reason") if isinstance(last, dict) else None}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--records", type=int, default=8200)
    parser.add_argument("--output")
    args = parser.parse_args()
    report = {"python": platform.python_version(), "platform": platform.platform(),
              "logical_cpus": os.cpu_count(), "fixture_records": args.records,
              "cache_condition": "first query after startup scan is warm OS cache; repeated queries warm; cold cache not claimed",
              "fixture_note": "direct deterministic corpus seed, not durable write soak"}
    with tempfile.TemporaryDirectory(prefix="oas-storage-benchmark-") as temporary:
        root = Path(temporary)
        seed(root, count=args.records)
        before = time.perf_counter()
        with EventStore(root) as store:
            report["startup_ms"] = round((time.perf_counter() - before) * 1000, 3)
            status = store.storage_status()
            report["initial_bytes"] = status["bytes"]
            report["initial_total_bytes"] = status["total_bytes"]
            report["budget_ratio"] = round(status["total_bytes"] / (64 * 1024 * 1024), 4)
            report["audit_50"] = measure(lambda: store.query(limit=50), args.samples)
            report["filtered_audit_50"] = measure(lambda: store.query({"profile_id": "p1"}, limit=50), args.samples)
            report["no_match_bounded"] = measure(lambda: store.query({"task_id": "absent"}, limit=50), args.samples)
            report["summaries_180_days"] = measure(lambda: store.statistics(), args.samples)
            report["summaries_filtered"] = measure(lambda: store.statistics(profile_id="p1", task_id="task2"), args.samples)
            identity = [0]
            def append():
                identity[0] += 1
                return store.append({"event_id": f"benchmark-append-{identity[0]}",
                                     "type": "scheduler.selected", "payload": {"reason": "ACK measurement"}})
            report["durable_ack"] = measure(append, min(5, args.samples))
            report["final_total_bytes"] = store.storage_status()["total_bytes"]
            report["observed_peak_bytes"] = store.storage_status()["peak_bytes"]
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(output + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
