"""HTTP and WS contracts against the actual file store, without game devices."""
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
import uuid
import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    HTTP_AVAILABLE = True
except ImportError:
    HTTP_AVAILABLE = False

from module.observability import EventStore
from module.server.solana_runtime import RuntimeService


class Adapter:
    calls = 0

    def names(self):
        return ['p']

    def device(self, name):
        return {'serial': 'auto'}

    def queues(self, service):
        return {'ready': [], 'waiting': []}

    async def control(self, service, receipt):
        self.calls += 1
        return 'waiting_safe_boundary'


@unittest.skipUnless(HTTP_AVAILABLE, 'HTTP test dependencies not installed')
class RouteTests(unittest.TestCase):
    def setUp(self):
        from module.server.solana_router import create_router
        self.temp = tempfile.TemporaryDirectory()
        self.store = EventStore(self.temp.name)
        self.adapter = Adapter()
        self.service = RuntimeService(self.store, self.adapter)
        app = FastAPI()
        app.include_router(create_router(lambda: self.service))
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.service.close()
        self.temp.cleanup()

    def test_unknown_pending_operation_blocks_reconcile_and_has_review_errors(self):
        key = str(uuid.uuid4())
        self.store.checkpoints.save('config_requests', key, {'digest': 'old-format', 'terminal': False})
        inventory = self.client.get('/api/v2/recovery/operations')
        self.assertEqual(inventory.status_code, 200)
        self.assertEqual(inventory.json()['pending_count'], 1)
        self.assertFalse(inventory.json()['items'][0]['resolvable'])
        blocked = self.client.post('/api/v2/storage/reconcile')
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()['error']['code'], 'unresolved_operations')
        body = {'kind': 'config_requests', 'key': key, 'expected_revisions': {},
                'reviewed': False, 'request_id': str(uuid.uuid4())}
        response = self.client.post('/api/v2/recovery/operations/resolve', json=body)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error']['code'], 'review_required')
        response = self.client.post('/api/v2/recovery/operations/resolve', json={**body, 'reviewed': True})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['error']['code'], 'operation_identity_unknown')
        self.assertTrue(self.service.dispatch_blocked)

    def test_reviewed_unknown_control_remains_unknown_when_wal_replays(self):
        request_id = str(uuid.uuid4())
        receipt = self.service.begin_control({'profile_id': 'p', 'action': 'start', 'request_id': request_id})
        event = {'type': 'control.completed', 'event_id': str(uuid.uuid4()), 'request_id': request_id,
                 'profile_id': receipt['profile_id'], 'payload': {'executed': None,
                     'status': 'unknown_state_accepted', 'historical_outcome': 'unknown'}}
        self.service.emit(event)
        self.service.emit(event)
        saved = self.store.checkpoints.get('requests', request_id)
        self.assertIsNone(saved['result']['executed'])
        self.assertEqual(saved['result']['historical_outcome'], 'unknown')
        self.assertFalse(self.service._reducer_dirty)

    def test_recovery_inventory_does_not_fence_a_control_still_executing(self):
        entered, release = threading.Event(), threading.Event()
        async def slow_control(service, receipt):
            entered.set()
            await asyncio.to_thread(release.wait, 5)
            return 'waiting_safe_boundary'
        self.adapter.control = slow_control
        body = {'profile_id': 'p', 'action': 'start', 'request_id': str(uuid.uuid4())}
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.client.post, '/api/v2/control', json=body)
            try:
                self.assertTrue(entered.wait(5))
                result = self.client.get('/api/v2/recovery/operations')
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.json()['pending_count'], 0)
                self.assertFalse(self.service.dispatch_blocked)
            finally:
                release.set()
            self.assertEqual(future.result(timeout=5).status_code, 200)
        self.assertFalse(self.service.active_operations)

    def test_control_schema_idempotency_and_conflict(self):
        self.service.register_process('p', 'control-test-owner')
        overview = self.client.get('/api/v2/overview').json()
        profile = overview['profiles'][0]
        body = {'profile_id': profile['id'], 'action': 'pause', 'request_id': str(uuid.uuid4()),
                'expected_state_version': profile['state_version']}
        result = self.client.post('/api/v2/control', json=body)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['status'], 'waiting_safe_boundary')
        self.assertTrue(result.json()['persisted'])
        self.assertTrue(self.client.post('/api/v2/control', json=body).json()['duplicate'])
        self.assertEqual(self.adapter.calls, 1)
        self.assertEqual(self.client.post('/api/v2/control', json={**body, 'request_id': str(uuid.uuid4())}).status_code, 409)
        self.assertEqual(self.client.post('/api/v2/control', json={**body, 'action': 'kill_everything'}).status_code, 422)

    def test_ws_snapshot_gap_and_reconnect(self):
        snapshot = self.client.get('/api/v2/overview').json()
        self.service.publish('progress', {'count': 7})
        path = f"/api/v2/events?stream_id={snapshot['stream_id']}&after={snapshot['stream_seq']}"
        with self.client.websocket_connect(path) as socket:
            event = socket.receive_json()
            self.assertEqual(event['type'], 'progress')
            self.assertEqual(event['payload']['count'], 7)
        with self.client.websocket_connect('/api/v2/events?stream_id=old&after=0') as socket:
            self.assertEqual(socket.receive_json()['type'], 'resync_required')

    def test_logical_runs_not_segments_and_dates(self):
        run_id = str(uuid.uuid4())
        base = {'run_id': run_id, 'profile_id': 'p', 'task_id': 'Orochi'}
        for kind, payload in [('run.created', {}), ('run.started', {}), ('run.yielded', {}),
                               ('run.resumed', {}), ('run.finished', {'outcome': 'succeeded', 'execution_seconds': 5})]:
            self.service.emit({**base, 'type': kind, 'payload': payload})
        page = self.client.get('/api/v2/runs', params={'task_id': 'Orochi'}).json()
        self.assertEqual(len(page['items']), 1)
        self.assertEqual(page['items'][0]['run_id'], run_id)
        self.assertEqual(page['items'][0]['state'], 'succeeded')
        empty = self.client.get('/api/v2/runs', params={'start_date': '2099-01-01'}).json()
        self.assertEqual(empty['items'], [])
        stats = self.client.get('/api/v2/statistics').json()['totals']
        self.assertEqual(stats['started'], 1)
        self.assertEqual(stats['success_denominator'], 1)

    def test_policy_and_sensitive_audit(self):
        response = self.client.put('/api/v2/scheduler/policy', json={'mode': 'eevdf_shadow'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['policy']['mode'], 'eevdf_shadow')
        self.assertEqual(self.client.put('/api/v2/scheduler/policy', json={'batch_seconds': -2}).status_code, 422)
        self.service.emit({'type': 'config.changed', 'payload': {'password': 'DO_NOT_LEAK'}})
        audit = self.client.get('/api/v2/audit').text
        self.assertNotIn('DO_NOT_LEAK', audit)
        replay = str(self.service.stream_after(self.service.stream_id, 0))
        self.assertNotIn('DO_NOT_LEAK', replay)
        storage = self.client.get('/api/v2/storage').json()
        self.assertEqual(storage['scope'], 'runtime_data_only')


if __name__ == '__main__':
    unittest.main()
