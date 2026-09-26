"""Real process-death recovery at durability boundaries (not mocked restarts)."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from module.observability import EventStore


PREAMBLE = '''
import sys, os
from pathlib import Path
from module.observability import EventStore
from module.observability import storage, summary, common
root = Path(sys.argv[1])
store = EventStore(root)
'''


class CrashBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def crash(self, body):
        result = subprocess.run([sys.executable, "-c", PREAMBLE + body, str(self.root)],
                                capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 77, result.stderr.decode(errors="replace"))

    def test_death_after_event_fsync_before_index(self):
        self.crash('''
storage.fsync_directory = lambda path: os._exit(77)
store.append({'event_id': 'one', 'type': 'run.started', 'run_id': 'r', 'payload': {}})
''')
        with EventStore(self.root) as store:
            ack = store.append({"event_id": "one", "type": "run.started", "run_id": "r", "payload": {}})
            self.assertTrue(ack["duplicate"])
            self.assertEqual(store.statistics()["totals"]["started"], 1)
            self.assertEqual(len(store.query()["items"]), 1)

    def test_death_after_reservation_before_append_does_not_reuse(self):
        self.crash('''
original = store._save_manifest
def save_and_die():
    original()
    if store.manifest['reserved_through'] > 0:
        os._exit(77)
store._save_manifest = save_and_die
store.append({'event_id': 'not-written', 'type': 'scheduler.selected', 'payload': {}})
''')
        with EventStore(self.root) as store:
            ack = store.append({"event_id": "next", "type": "scheduler.selected", "payload": {}})
            self.assertEqual(ack["seq"], 65)

    def test_death_between_two_day_commits(self):
        self.crash('''
original = summary.atomic_json
def commit_then_die(path, value, observer=None):
    original(path, value, observer)
    if Path(path).name == '2026-09-23.json':
        os._exit(77)
summary.atomic_json = commit_then_die
store.append({'event_id': 'cross', 'type': 'segment.finished', 'run_id': 'r', 'segment_id': 's',
              'occurred_at': '2026-09-23T16:00:20Z', 'payload': {
                  'started_at': '2026-09-23T15:59:50Z', 'finished_at': '2026-09-23T16:00:20Z', 'duration_seconds': 30}})
''')
        with EventStore(self.root) as store:
            days = store.statistics()["days"]
            self.assertEqual([day["totals"]["device_seconds"] for day in days], [10, 20])
            self.assertEqual(store.summaries.cursor_seq, 1)
        with EventStore(self.root) as store:
            self.assertEqual(store.statistics()["totals"]["device_seconds"], 30)

    def test_death_before_checkpoint_replace_keeps_previous_version(self):
        self.crash('''
store.checkpoints.save('runs', 'r', {'generation': 1})
original = common.os.replace
def die_on_checkpoint(source, target):
    if 'checkpoints' in Path(target).parts:
        os._exit(77)
    original(source, target)
common.os.replace = die_on_checkpoint
store.checkpoints.save('runs', 'r', {'generation': 2})
''')
        with EventStore(self.root) as store:
            self.assertEqual(store.checkpoints.get("runs", "r"), {"generation": 1})

    def test_death_after_cleanup_intent_does_not_resurrect_history(self):
        self.crash('''
store.append({'event_id': 'done', 'type': 'run.finished', 'run_id': 'r', 'payload': {'outcome': 'succeeded'}})
original = store._save_manifest
def die_after_tombstone():
    original()
    if store.manifest.get('deleted_shards'):
        os._exit(77)
store._save_manifest = die_after_tombstone
store.cleanup(force=True, compress=False)
''')
        with EventStore(self.root) as store:
            self.assertEqual(store.query()["items"], [])
            self.assertEqual(store.statistics()["totals"]["succeeded"], 1)


if __name__ == "__main__":
    unittest.main()
