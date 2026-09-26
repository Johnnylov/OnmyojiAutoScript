"""Realistic parent lifecycle/recovery failures; no game or emulator is started."""
from datetime import datetime, timedelta, timezone
import multiprocessing
from tempfile import TemporaryDirectory
import unittest
import uuid

from module.observability import EventStore
from module.server.solana_runtime import RuntimeService
from module.server.solana_bridge import ProcessBridge, RuntimeBridgeError


class Adapter:
    def names(self):
        return ['profile']

    def device(self, name):
        return {'device_id': 'failure-regression-device'}


def send_without_reader(connection, output):
    bridge = ProcessBridge(connection, 'p', 'o', 'd', timeout=.1)
    output.put('started')
    try:
        bridge.call('event.append', {'padding': 'x' * (128 * 1024)})
        output.put('unexpected_success')
    except RuntimeBridgeError as exc:
        output.put(exc.code)


class ParentFailureRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.store = EventStore(self.temp.name)
        self.service = RuntimeService(self.store, Adapter())

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def test_storage_fence_does_not_hide_pending_safe_stop_from_due_worker(self):
        identity = self.service.register_process('profile', 'owner')
        self.service.dispatch_blocked = True
        self.service.request_control(identity['profile_id'], 'safe_stop')
        result = self.service.dispatch('owner', 'scheduling.acquire', {
            'candidates': [{'task': 'Orochi'}], 'legacy_order': ['Orochi']})
        self.assertNotEqual(result['status'], 'acquired')
        self.assertIn(result.get('control'), ('safe_stop', 'stop'))

    def test_dead_owner_revoke_is_retryable_after_checkpoint_disk_failure(self):
        identity = self.service.register_process('profile', 'owner')
        result = self.service.dispatch('owner', 'scheduling.acquire', {
            'candidates': [{'task': 'Orochi'}], 'legacy_order': ['Orochi']})
        self.assertEqual(result['status'], 'acquired')
        original = self.store.checkpoints.save
        def fail_device_checkpoint(kind, *args, **kwargs):
            if kind == 'devices':
                raise OSError('simulated disk failure during confirmed-dead cleanup')
            return original(kind, *args, **kwargs)
        self.store.checkpoints.save = fail_device_checkpoint
        try:
            self.service.process_exited('owner', -1)
        finally:
            self.store.checkpoints.save = original
        self.assertFalse(self.service.owners['owner']['alive'])
        self.assertTrue(self.service.dispatch_blocked)
        result = self.service.reconcile_storage()
        self.assertTrue(result['verified'])
        self.assertIsNone(self.service.coordinator.snapshot()[identity['device_id']]['lease'])

    def test_bounded_reducer_scan_never_advances_over_unread_older_event(self):
        now = [datetime(2026, 9, 24, tzinfo=timezone.utc)]
        self.store._clock = lambda: now[0]
        run_id = str(uuid.uuid4())
        # Simulate events committed just before a parent/reducer interruption.
        oldest = self.store.append({'event_id': str(uuid.uuid4()), 'type': 'run.created',
            'run_id': run_id, 'profile_id': 'p', 'task_id': 'Orochi',
            'payload': {'padding': 'x' * 1600}})
        now[0] += timedelta(days=1)
        self.store.append({'event_id': str(uuid.uuid4()), 'type': 'config.changed', 'payload': {'value': 1}})
        original_budget = self.store.policy.query_max_bytes
        self.store.policy.query_max_bytes = 1024
        try:
            try:
                self.service._recover_reducer()
            except Exception:
                # Explicitly refusing partial recovery is also safe.
                pass
            cursor = self.store.checkpoints.get('settings', 'reducer_cursor')['cursor_seq']
            self.assertLess(cursor, oldest['seq'], 'unread prefix must remain replayable')
        finally:
            self.store.policy.query_max_bytes = original_budget
        self.service._recover_reducer()
        self.assertIn(run_id, self.service.runs)

    def test_bridge_deadline_includes_blocking_pipe_write(self):
        context = multiprocessing.get_context('spawn')
        parent, child = context.Pipe(duplex=True)
        output = context.Queue()
        worker = context.Process(target=send_without_reader, args=(child, output))
        worker.start()
        child.close()
        try:
            self.assertEqual(output.get(timeout=10), 'started')
            worker.join(1.0)
            self.assertFalse(worker.is_alive(), 'send_bytes blocked before the configured ACK deadline began')
            self.assertIn(output.get(timeout=3), ('ack_timeout', 'send_timeout', 'bridge_unavailable'))
        finally:
            if worker.is_alive():
                worker.terminate()
                worker.join(5)
            parent.close()
            output.close()


if __name__ == '__main__':
    unittest.main()
