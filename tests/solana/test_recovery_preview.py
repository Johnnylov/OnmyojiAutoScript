import base64
from pathlib import Path
from queue import Queue
import tempfile
import unittest
import uuid

from module.observability import EventStore
from module.scheduling.fence import DeviceProcessLock
from module.scheduling.preview import PreviewPublisher
from module.server.solana_runtime import RuntimeService, ServiceError


class Adapter:
    def names(self):
        return ['p']

    def device(self, name):
        return {'device_id': 'test-device-' + name}


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = EventStore(self.temp.name)
        self.service = RuntimeService(self.store, Adapter())
        self.profile = self.service.profile_id('p')
        self.run_id = str(uuid.uuid4())
        self.device = 'test-' + str(uuid.uuid4())
        for kind in ('run.created', 'run.started'):
            self.service.emit({'type': kind, 'profile_id': self.profile, 'task_id': 'Orochi',
                               'run_id': self.run_id, 'device_id': self.device})
        self.store.checkpoints.save('runs', 'task-pointer', {'run_id': self.run_id, 'profile_id': self.profile,
            'task': 'Orochi', 'device_id': self.device, 'state': 'running', 'in_flight': True})
        self.service.runs[self.run_id]['state'] = 'needs_reconciliation'
        self.data = {'profile_id': self.profile, 'run_id': self.run_id, 'request_id': str(uuid.uuid4()),
                     'resolution': 'close_interrupted', 'game_state_reviewed': True}

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def test_uncertain_run_is_closed_once_without_replaying_or_success(self):
        result = self.service.resolve_run(self.data)
        self.assertTrue(result['executed'])
        self.assertTrue(self.service.resolve_run(self.data)['duplicate'])
        point = self.store.checkpoints.get('runs', 'task-pointer')
        self.assertEqual(point['state'], 'interrupted')
        self.assertFalse(point['in_flight'])
        stats = self.store.statistics()
        self.assertEqual(stats['totals']['interrupted'], 1)
        self.assertEqual(stats['totals']['succeeded'], 0)
        self.assertTrue(stats['incomplete'])

    def test_live_executor_or_device_lock_prevents_resolution(self):
        identity = self.service.register_process('p', 'owner')
        with self.assertRaises(ServiceError):
            self.service.resolve_run(self.data)
        self.service.process_exited('owner', -1)
        lock = DeviceProcessLock(self.device)
        self.assertTrue(lock.acquire())
        try:
            with self.assertRaises(ServiceError):
                self.service.resolve_run(self.data)
        finally:
            lock.release()
        self.assertTrue(self.service.resolve_run(self.data)['executed'])

    def test_missing_review_never_resolves(self):
        with self.assertRaises(ServiceError):
            self.service.resolve_run({**self.data, 'game_state_reviewed': False})
        self.assertEqual(self.service.runs[self.run_id]['state'], 'needs_reconciliation')

    def test_storage_recovers_only_after_run_and_pointer_are_resolved(self):
        self.service.dispatch_blocked = True
        with self.assertRaises(ServiceError):
            self.service.reconcile_storage()
        self.service.resolve_run(self.data)
        result = self.service.reconcile_storage()
        self.assertTrue(result['verified'])
        self.assertFalse(result['automatic_restart'])
        self.assertFalse(self.service.dispatch_blocked)

    def test_terminal_wal_before_pointer_failure_can_be_reconciled_on_retry(self):
        original = self.store.checkpoints.save
        def failing(kind, *args, **kwargs):
            if kind == 'runs':
                raise OSError('injected checkpoint failure')
            return original(kind, *args, **kwargs)
        self.store.checkpoints.save = failing
        with self.assertRaises(OSError):
            self.service.resolve_run(self.data)
        self.store.checkpoints.save = original
        result = self.service.resolve_run(self.data)
        self.assertTrue(result['executed'])
        self.assertEqual(self.store.statistics()['totals']['interrupted'], 1)


class PreviewTests(unittest.TestCase):
    def test_bounded_optional_queue_never_blocks_and_respects_interval(self):
        queue = Queue(maxsize=1)
        now = [0.0]
        calls = []
        publisher = PreviewPublisher(queue, clock=lambda: now[0], encoder=lambda frame: calls.append(frame) or b'jpeg')
        publisher('frame1')
        publisher('too_soon')
        now[0] = 3
        publisher('dropped_when_full')
        self.assertEqual(len(calls), 2)
        self.assertEqual(base64.b64decode(queue.get()['image_base64']), b'jpeg')

    def test_oversized_thumbnail_is_not_enqueued(self):
        queue = Queue(maxsize=1)
        PreviewPublisher(queue, encoder=lambda frame: b'x' * 100000)(None)
        self.assertTrue(queue.empty())


if __name__ == '__main__':
    unittest.main()
