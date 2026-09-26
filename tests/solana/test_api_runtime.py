import asyncio
import multiprocessing
from pathlib import Path
import tempfile
import threading
import unittest
import uuid

from module.observability import EventStore
from module.server.solana_runtime import RuntimeService, ServiceError
from module.server.solana_bridge import ProcessBridge, serve_bridge


class Adapter:
    def names(self):
        return ['profile']

    def device(self, name):
        return {'serial': '127.0.0.1:5555'}

    def queues(self, service):
        return {'ready': [], 'waiting': []}

    async def control(self, service, receipt):
        return 'started'


def bridge_child(bridge, output):
    result = bridge.call('event.append', {'event_id': str(uuid.uuid4()), 'type': 'control.completed',
        'request_id': str(uuid.uuid4()), 'payload': {'action': 'test', 'executed': True}})
    output.put(result['persisted'])


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = EventStore(Path(self.temp.name))
        self.service = RuntimeService(self.store, Adapter(), replay_limit=4)

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def test_snapshot_cursor_replay_and_expiration(self):
        snapshot = self.service.overview()
        self.service.publish('progress', {'count': 2})
        page = self.service.stream_after(snapshot['stream_id'], snapshot['stream_seq'])
        self.assertEqual(page['items'][0]['payload']['count'], 2)
        for i in range(5):
            self.service.publish('progress', {'count': i})
        self.assertTrue(self.service.stream_after(snapshot['stream_id'], snapshot['stream_seq'])['resync_required'])
        self.assertTrue(self.service.stream_after('old_process', 0)['resync_required'])

    def test_profile_identity_survives_rename_and_restart(self):
        identity = self.service.profile_id('profile')
        self.service.rename_profile('profile', 'renamed')
        self.assertEqual(identity, self.service.profile_id('renamed'))
        self.service.close()
        self.store = EventStore(Path(self.temp.name))
        self.service = RuntimeService(self.store, Adapter())
        self.assertEqual(identity, self.service.profile_id('renamed'))

    def test_control_idempotency_and_stale_state(self):
        data = {'profile_id': 'profile', 'action': 'start', 'request_id': str(uuid.uuid4()), 'expected_state_version': 0}
        receipt = self.service.begin_control(data)
        self.service.complete_control(receipt, True, 'started')
        self.assertTrue(self.service.begin_control(data)['duplicate'])
        with self.assertRaises(ServiceError):
            self.service.begin_control({**data, 'action': 'pause'})
        with self.assertRaises(ServiceError):
            self.service.begin_control({**data, 'request_id': str(uuid.uuid4())})

    def test_stop_executes_when_storage_cannot_record(self):
        self.service.profile_id('profile')
        original = self.store.append
        self.store.append = lambda event: (_ for _ in ()).throw(OSError('disk full'))
        for action in ('safe_stop', 'immediate_stop'):
            receipt = self.service.begin_control({'profile_id': 'profile', 'action': action,
                'request_id': str(uuid.uuid4())})
            result = self.service.complete_control(receipt, True, 'stopped')
            self.assertTrue(result['executed'])
            self.assertFalse(result['persisted'])
            self.assertEqual(result['status'], 'executed_not_saved')
        with self.assertRaises(ServiceError):
            self.service.begin_control({'profile_id': 'profile', 'action': 'start', 'request_id': str(uuid.uuid4())})
        self.store.append = original

    def test_duplicate_event_after_checkpoint_failure_reduces_once(self):
        original = self.store.checkpoints.save
        self.store.checkpoints.save = lambda *a, **k: (_ for _ in ()).throw(OSError('crash boundary'))
        event = {'event_id': str(uuid.uuid4()), 'type': 'segment.finished', 'run_id': str(uuid.uuid4()),
                 'segment_id': str(uuid.uuid4()),
                 'profile_id': 'p', 'task_id': 'Orochi',
                 'payload': {'duration_seconds': 2, 'started_at': '2026-09-24T00:00:00Z',
                             'finished_at': '2026-09-24T00:00:02Z'}}
        with self.assertRaises(OSError):
            self.service.emit(event)
        self.store.checkpoints.save = original
        self.assertTrue(self.service.emit(event)['duplicate'])
        self.service.emit(event)
        self.assertEqual(self.service.runs[event['run_id']]['execution_seconds'], 2)

    def test_spawn_bridge_durable_ack_and_fencing(self):
        context = multiprocessing.get_context('spawn')
        parent, child = context.Pipe()
        output = context.Queue()
        owner_id = str(uuid.uuid4())
        identity = self.service.register_process('profile', owner_id)
        worker = context.Process(target=bridge_child, args=(ProcessBridge(child, **identity), output))
        worker.start()
        child.close()
        server = threading.Thread(target=serve_bridge, args=(parent, self.service, owner_id, worker))
        server.start()
        self.assertTrue(output.get(timeout=10))
        worker.join(10)
        server.join(10)
        self.assertEqual(worker.exitcode, 0)
        self.assertFalse(self.service.owners[owner_id]['alive'])
        with self.assertRaises(ServiceError):
            self.service.dispatch(owner_id, 'scheduling.acquire', {})
        output.close()


if __name__ == '__main__':
    unittest.main()
