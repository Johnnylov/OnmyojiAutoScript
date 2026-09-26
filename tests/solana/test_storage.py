"""Storage correctness tests; every test owns a temporary runtime directory."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from module.observability import (
    EventStore, CheckpointStore, HistoryExpired, ReplayExpired, TerminalConflict,
    StorageUnavailable, StorageRestricted, WriterLocked, SchemaError,
)
from module.observability import storage, summary, common


BASE = datetime(2026, 9, 24, 4, tzinfo=timezone.utc)


def event(identity, kind="scheduler.selected", at=BASE, **kwargs):
    return {"event_id": identity, "type": kind, "occurred_at": at.isoformat(),
            "profile_id": "profile-1", "task_id": "Orochi", "payload": {}, **kwargs}


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.now = BASE
        self.store = EventStore(self.root, clock=lambda: self.now)

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def reopen(self, **kwargs):
        self.store.close()
        self.store = EventStore(self.root, clock=lambda: self.now, **kwargs)

    def test_fsync_ack_dedup_and_reserved_sequence_restart(self):
        first = self.store.append(event("first", "run.started", run_id="run-1"))
        self.assertTrue(first["persisted"])
        self.assertEqual(self.store.append(event("first", "run.started", run_id="run-1"))["seq"], first["seq"])
        self.reopen()
        again = self.store.append(event("first", "run.started", run_id="run-1"))
        self.assertTrue(again["duplicate"])
        following = self.store.append(event("second"))
        self.assertGreater(following["seq"], 64)
        self.assertEqual(self.store.statistics()["totals"]["started"], 1)

    def test_second_writer_fails_in_same_and_different_process(self):
        with self.assertRaises(WriterLocked):
            EventStore(self.root)
        code = "from module.observability import EventStore, WriterLocked\nimport sys\ntry: EventStore(sys.argv[1])\nexcept WriterLocked: sys.exit(23)"
        result = subprocess.run([sys.executable, "-c", code, str(self.root)], capture_output=True)
        self.assertEqual(result.returncode, 23, result.stderr.decode())

    def test_logical_terminal_and_started_dedup(self):
        self.store.append(event("start1", "run.started", run_id="r"))
        self.assertTrue(self.store.append(event("start2", "run.started", run_id="r"))["duplicate"])
        self.store.append(event("finish1", "run.finished", run_id="r", payload={"outcome": "succeeded"}))
        self.assertTrue(self.store.append(event("finish2", "run.finished", run_id="r", payload={"outcome": "succeeded"}))["duplicate"])
        with self.assertRaises(TerminalConflict):
            self.store.append(event("finish3", "run.finished", run_id="r", payload={"outcome": "failed"}))
        totals = self.store.statistics()["totals"]
        self.assertEqual((totals["started"], totals["succeeded"], totals["success_denominator"]), (1, 1, 1))

    def test_mixed_outcomes_segments_resume_statistics(self):
        for index, outcome in enumerate(("succeeded", "failed", "cancelled", "interrupted", "crashed")):
            run = str(index)
            self.store.append(event(f"s{index}", "run.started", run_id=run))
            self.store.append(event(f"p{index}", "run.paused", run_id=run))
            self.store.append(event(f"r{index}", "run.resumed", run_id=run))
            self.store.append(event(f"g{index}", "segment.finished", run_id=run, segment_id=f"g{index}", payload={
                "duration_seconds": 10, "started_at": BASE.isoformat(),
                "finished_at": (BASE + timedelta(seconds=10)).isoformat()}))
            self.store.append(event(f"f{index}", "run.finished", run_id=run,
                                    payload={"outcome": outcome, "error_category": "network", "queue_wait_seconds": 2}))
        totals = self.store.statistics()["totals"]
        self.assertEqual(totals["started"], 5)
        self.assertEqual(totals["success_rate"], .5)
        self.assertEqual(totals["success_denominator"], 2)
        self.assertEqual(totals["device_seconds"], 50)
        self.assertEqual(totals["average_execution_seconds"], 10)
        self.assertEqual(totals["queue_wait_seconds"], 10)
        self.assertEqual(totals["failure_categories"], {"network": 1})

    def test_cross_midnight_partial_summary_commit_replays_once(self):
        start = datetime(2026, 9, 23, 15, 59, 50, tzinfo=timezone.utc)
        finish = start + timedelta(seconds=30)
        item = event("cross-day", "segment.finished", finish, run_id="r", segment_id="seg",
                     payload={"started_at": start.isoformat(), "finished_at": finish.isoformat(), "duration_seconds": 30})
        original = summary.atomic_json
        fired = []

        def fail_after_first_day(path, value, observer=None):
            original(path, value, observer)
            if Path(path).name == "2026-09-23.json" and not fired:
                fired.append(True)
                raise OSError("simulated crash between day commits")

        with patch.object(summary, "atomic_json", fail_after_first_day):
            with self.assertRaises(StorageUnavailable):
                self.store.append(item)
        self.reopen()
        self.assertTrue(self.store.append(item)["duplicate"])
        days = self.store.statistics()["days"]
        self.assertEqual([d["totals"]["device_seconds"] for d in days], [10, 20])
        self.assertEqual(self.store.statistics()["totals"]["device_seconds"], 30)

    def test_unknown_commit_retry_same_process_discovers_wal(self):
        item = event("after-fsync", "run.started", run_id="r")
        with patch.object(storage, "fsync_directory", side_effect=OSError("post append failure")):
            with self.assertRaises(StorageUnavailable):
                self.store.append(item)
        self.assertFalse(self.store.storage_status()["dispatch_allowed"])
        receipt = self.store.append(item)
        self.assertTrue(receipt["duplicate"])
        self.assertEqual(self.store.statistics()["totals"]["started"], 1)
        self.assertFalse(self.store.storage_status()["dispatch_allowed"])
        self.store.acknowledge_recovery()
        self.assertTrue(self.store.storage_status()["dispatch_allowed"])

    def test_reconciliation_requirement_survives_restart_until_acknowledged(self):
        self.store.append(event("one"))
        self.store.close()
        path = next((self.root / "events").glob("*.jsonl"))
        with path.open("ab") as stream:
            stream.write(b"partial")
        self.reopen()
        self.assertFalse(self.store.storage_status()["dispatch_allowed"])
        self.reopen()
        self.assertFalse(self.store.storage_status()["dispatch_allowed"])
        self.store.acknowledge_recovery()
        self.reopen()
        self.assertTrue(self.store.storage_status()["dispatch_allowed"])

    def test_torn_tail_repaired_but_middle_corruption_reported(self):
        for index in range(3):
            self.store.append(event(str(index)))
        self.store.close()
        path = next((self.root / "events").glob("*.jsonl"))
        original = path.read_bytes()
        with path.open("ab") as stream:
            stream.write(b'{"event_id":"incomplete')
        self.reopen()
        self.assertEqual(path.read_bytes(), original)
        self.assertTrue(any(g["reason"] == "torn_tail_repaired" for g in self.store.storage_status()["gaps"]))
        self.store.close()
        lines = path.read_bytes().splitlines(keepends=True)
        lines[1] = b"broken record\n"
        path.write_bytes(b"".join(lines))
        self.reopen()
        self.assertTrue(self.store.query()["incomplete"])
        self.assertTrue(self.store.statistics()["incomplete"])
        self.assertEqual(len(self.store.query()["items"]), 2)

    def test_checkpoint_atomic_previous_survives_replace_failure(self):
        ack = self.store.append(event("ack"))
        self.store.checkpoints.save("runtime_runs", "r", {"generation": 4, "token_lost": False}, event_seq=ack["seq"])
        self.store.checkpoints.save("runtime_runs", "r", {"generation": 5})
        self.assertEqual(self.store.checkpoints.get_document("runtime_runs", "r")["event_seq"], ack["seq"])
        with patch.object(common.os, "replace", side_effect=OSError("replace failure")):
            with self.assertRaises(OSError):
                self.store.checkpoints.save("runtime_runs", "r", {"generation": 6})
        self.assertEqual(self.store.checkpoints.get("runtime_runs", "r"), {"generation": 5})
        self.assertFalse(self.store.storage_status()["dispatch_allowed"])
        with self.assertRaises(ValueError):
            self.store.checkpoints.save("runs", "invalid", {}, event_seq=ack["seq"] + 1)
        with self.assertRaises(ValueError):
            self.store.checkpoints.get("../escape", "x")

    def test_checkpoint_rejects_reserved_but_never_written_sequence(self):
        self.store.append(event("one"))
        self.reopen()
        self.store.append(event("two"))
        with self.assertRaises(ValueError):
            self.store.checkpoints.save("runs", "gap", {}, event_seq=32)

    def test_query_scan_limit_returns_bounded_reason_not_endless_cursor(self):
        self.store.append(event("one", payload={"detail": "a" * 2000}))
        self.store.set_policy({"query_max_bytes": 512})
        result = self.store.query()
        self.assertEqual(result["bounded_reason"], "scan_budget")
        self.assertIsNone(result["next_cursor"])

    def test_low_disk_space_degrades_and_checkpoint_cannot_bypass_budget(self):
        from collections import namedtuple
        Usage = namedtuple("Usage", "total used free")
        with patch.object(storage.shutil, "disk_usage", return_value=Usage(100, 100, 0)):
            with self.assertRaises(StorageUnavailable):
                self.store.checkpoints.save("runs", "x", {"important": True})
        self.assertFalse(self.store.storage_status()["dispatch_allowed"])

    def test_checkpoint_reclaim_needs_terminal_verification_and_skips_identity(self):
        from datetime import datetime
        self.store.checkpoints.save("runtime_runs", "done", {"terminal": True, "verified": True})
        self.store.checkpoints.save("runtime_runs", "unknown", {"terminal": True, "verified": False})
        self.store.checkpoints.save("settings", "identity", {"terminal": True, "verified": True})
        self.now = datetime(2035, 1, 1, tzinfo=timezone.utc)
        result = self.store.cleanup(compress=False)
        self.assertEqual(result["removed_checkpoints"], 1)
        self.assertIsNone(self.store.checkpoints.get("runtime_runs", "done"))
        self.assertIsNotNone(self.store.checkpoints.get("runtime_runs", "unknown"))
        self.assertIsNotNone(self.store.checkpoints.get("settings", "identity"))

    def test_secret_redaction_before_all_persistence_and_oversized_event(self):
        payload = {"changes": [{"path": "global.password", "old": "secret-old", "new": "secret-new"}],
                   "access_token": "token-value", "nested": {"notify_config": "secret-url"}}
        self.store.append(event("secret", "config.changed", payload=payload))
        self.store.checkpoints.save("settings", "x", payload)
        content = b"".join(p.read_bytes() for p in self.root.rglob("*") if p.is_file() and p.name != "writer.lock")
        for secret in (b"secret-old", b"secret-new", b"token-value", b"secret-url"):
            self.assertNotIn(secret, content)
        self.store.append(event("huge", payload={"candidates": "x" * 20000}))
        huge = self.store.query({"type": "scheduler.selected"})["items"][0]
        self.assertTrue(huge["payload"]["truncated"])
        self.assertLessEqual(len(common.encode(huge)) + 1, 8192)

    def test_cursor_snapshot_excludes_new_events_and_cleanup_expires(self):
        for index in range(5):
            self.store.append(event(str(index)))
        first = self.store.query(limit=2)
        self.store.append(event("new"))
        second = self.store.query(cursor=first["next_cursor"], limit=2)
        self.assertEqual([item["event_id"] for item in second["items"]], ["2", "1"])
        self.store.cleanup(force=True, compress=False)
        with self.assertRaises(HistoryExpired):
            self.store.query(cursor=second["next_cursor"], limit=2)

    def test_cleanup_protects_unfinished_run_and_checkpoint(self):
        self.store.append(event("start", "run.started", run_id="active"))
        self.assertEqual(self.store.cleanup(force=True)["removed_shards"], 0)
        self.store.append(event("finish", "run.finished", run_id="active", payload={"outcome": "cancelled"}))
        ack = self.store.append(event("cp"))
        self.store.checkpoints.save("runs", "needs-restore", {"terminal": False}, event_seq=ack["seq"])
        result = self.store.cleanup(force=True, compress=False)
        self.assertGreaterEqual(result["removed_shards"], 1)
        self.assertTrue(self.store.query()["items"])
        self.assertEqual(self.store.statistics()["totals"]["cancelled"], 1)

    def test_off_minimal_records_verified_checkpoint_retention_and_highwater(self):
        self.store.set_policy({"mode": "off", "retry_window_seconds": 1})
        self.assertFalse(self.store.append(event("optional"))["persisted"])
        self.store.append(event("intent", "control.requested", request_id="req"))
        receipt = self.store.append(event("done", "control.completed", request_id="req"))
        self.assertEqual(self.store.cleanup(force=True, compress=False)["removed_shards"], 0)
        self.store.checkpoints.save("requests", "req", {"terminal": True, "verified": True}, event_seq=receipt["seq"])
        self.now += timedelta(seconds=2)
        self.assertGreater(self.store.cleanup(force=True, compress=False)["removed_shards"], 0)
        previous = receipt["seq"]
        self.reopen()
        receipt = self.store.append(event("new-intent", "control.requested", self.now, request_id="new-req"))
        self.assertGreater(receipt["seq"], previous)
        with self.assertRaises(ReplayExpired):
            self.store.append(event("intent", "control.requested", request_id="req"))

    def test_mode_switch_cannot_exempt_old_summary_requirement(self):
        self.store.append(event("terminal", "run.finished", run_id="r", payload={"outcome": "succeeded"}))
        self.store.set_policy({"mode": "off"})
        self.store.summaries.state["cursor_seq"] = 0
        self.assertEqual(self.store.cleanup(force=True, compress=False)["removed_shards"], 0)

    def test_unconsumed_runtime_events_survive_cleanup(self):
        self.store.checkpoints.save("settings", "reducer_cursor", {"cursor_seq": 0, "retention_consumer": True})
        ack = self.store.append(event("finished", "run.finished", run_id="r", payload={"outcome": "succeeded"}))
        self.assertEqual(self.store.cleanup(force=True, compress=False)["removed_shards"], 0)
        self.store.checkpoints.save("settings", "reducer_cursor", {"cursor_seq": ack["seq"], "retention_consumer": True, "terminal": True}, event_seq=ack["seq"])
        self.assertEqual(self.store.cleanup(force=True, compress=False)["removed_shards"], 1)

    def test_summary_only_evicts_aggregated_detail_and_keeps_totals(self):
        self.store.set_policy({"mode": "summary_only"})
        self.store.append(event("done", "run.finished", run_id="r", payload={"outcome": "succeeded"}))
        self.store.cleanup(force=True, compress=False)
        self.assertEqual(self.store.query()["items"], [])
        self.assertEqual(self.store.statistics()["totals"]["succeeded"], 1)
        self.reopen()
        self.assertEqual(self.store.statistics()["totals"]["succeeded"], 1)

    def test_compression_verified_and_query_works(self):
        self.store.set_policy({"shard_max_bytes": 8192})
        for index in range(30):
            self.store.append(event(str(index), payload={"reason": "same data" * 10}))
        result = self.store.cleanup()
        self.assertGreater(result["compressed_shards"], 0)
        self.assertEqual(len(self.store.query(limit=100)["items"]), 30)
        self.reopen()
        self.assertEqual(len(self.store.query(limit=100)["items"]), 30)

    def test_clock_jump_preserves_duration_as_anomaly(self):
        self.store.append(event("jump", "segment.finished", run_id="r", segment_id="s", payload={
            "started_at": BASE.isoformat(), "finished_at": (BASE - timedelta(hours=1)).isoformat(), "duration_seconds": 45}))
        result = self.store.statistics()
        self.assertTrue(result["incomplete"])
        self.assertEqual(result["totals"]["device_seconds"], 45)
        self.assertEqual(result["totals"]["anomalous_device_seconds"], 45)

    def test_missing_execution_duration_is_unknown_not_zero(self):
        self.store.append(event("finished", "run.finished", run_id="r", payload={"outcome": "succeeded"}))
        result = self.store.statistics()
        self.assertEqual(result["totals"]["succeeded"], 1)
        self.assertEqual(result["totals"]["success_samples"], 0)
        self.assertIsNone(result["totals"]["average_execution_seconds"])
        self.assertTrue(result["incomplete"])

    def test_budget_blocks_critical_preserves_pending_and_reports(self):
        self.store.append(event("pending", "run.started", run_id="r"))
        usage = self.store.storage_status()["total_bytes"]
        self.store.set_policy({"normal_budget_bytes": usage + 12000, "temporary_reserve_bytes": 2000})
        with self.assertRaises(StorageRestricted):
            self.store.append(event("optional"))
        with self.assertRaises(StorageUnavailable):
            self.store.append(event("critical", "run.finished", run_id="r", payload={"outcome": "succeeded"}))
        self.assertFalse(self.store.storage_status()["dispatch_allowed"])
        self.assertEqual(self.store.statistics()["totals"]["succeeded"], 0)
        self.assertTrue(self.store.query()["items"])

    def test_query_date_filters_limits_and_schema_rejection(self):
        self.store.append(event("a"))
        self.assertEqual(len(self.store.query({"start_date": "2026-09-24", "end_date": "2026-09-24"})["items"]), 1)
        self.assertEqual(len(self.store.query({"start_date": "2026-09-25"})["items"]), 0)
        with self.assertRaises(ValueError):
            self.store.query(limit=201)
        with self.assertRaises(SchemaError):
            self.store.append({**event("invalid"), "schema_version": 999})


if __name__ == "__main__":
    unittest.main()
