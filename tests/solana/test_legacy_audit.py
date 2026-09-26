"""Actual legacy FastAPI handlers and durable audit, using temporary profiles.

Only process/device actions are fake. Models, manager, route execution, file
transactions, event store, identity checkpoints and recovery are real.
"""
import importlib.util
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import ModuleType
import unittest
import uuid
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
try:
    from fastapi.testclient import TestClient
except ImportError:
    TestClient = None

from module.config.config import Config
from module.config.config_model import ConfigModel
from module.server.config_manager import ConfigManager
from module.server.solana_adapter import ManagerAdapter
from module.server.solana_runtime import RuntimeService
from module.server.solana_router import create_router
from module.server.solana_legacy_audit import (reconcile_legacy_operations,
    list_pending_operations, resolve_pending_operation)
from module.observability import EventStore


REPO = Path(__file__).resolve().parents[2]


class FakeProcess:
    def __init__(self):
        self.state = 0
        self.starts = self.stops = 0
        self.connections = []

    async def start(self):
        self.starts += 1
        self.state = 1
        await self.broadcast_state({'state': 1})

    async def stop(self):
        self.stops += 1
        self.state = 0
        await self.broadcast_state({'state': 0})

    async def connect(self, socket):
        await socket.accept()
        self.connections.append(socket)

    async def send_json(self, socket, data):
        await socket.send_json(data)

    async def broadcast_state(self, data):
        for socket in list(self.connections):
            await socket.send_json(data)

    async def disconnect(self, socket):
        if socket in self.connections:
            self.connections.remove(socket)


class Manager(ConfigManager):
    def __init__(self):
        self.script_process = {name: FakeProcess() for name in ('trial', 'source')}

    @staticmethod
    def config_cache(name):
        return Config(name)

    def add_script_file(self, name):
        self.script_process[name] = FakeProcess()


class Adapter(ManagerAdapter):
    def __init__(self, manager):
        self._manager = manager

    @property
    def manager(self):
        return self._manager


@unittest.skipUnless(TestClient, 'Install tests/solana/requirements-http.txt into an isolated test target')
class LegacyAuditTests(unittest.TestCase):
    def setUp(self):
        self.old_cwd = Path.cwd()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        os.chdir(self.root)
        (self.root / 'config').mkdir()
        base = ConfigModel().model_dump()
        for name in ('trial', 'source'):
            data = {**base, 'config_name': name}
            (self.root / 'config' / f'{name}.json').write_text(json.dumps(data, default=str), encoding='utf-8')
        self.manager = Manager()
        self.adapter = Adapter(self.manager)
        self.store = EventStore(self.root / 'runtime_data')
        self.service = RuntimeService(self.store, self.adapter)
        self.identity = self.service.profile_id('trial')
        fake_manager = ModuleType('module.server.main_manager')
        fake_manager.mm = self.manager
        spec = importlib.util.spec_from_file_location('isolated_legacy_router', REPO / 'module/server/script_router.py')
        self.router = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'module.server.main_manager': fake_manager}):
            spec.loader.exec_module(self.router)
        self.runtime_patch = patch('module.server.solana_runtime.get_runtime', side_effect=lambda: self.service)
        self.runtime_patch.start()
        self.logs = []
        self.log_patch = patch('module.server.api_logger.log_http_access', side_effect=self.logs.append)
        self.log_patch.start()
        self.ws_log_patch = patch.object(self.router, 'log_ws_event')
        self.ws_log_patch.start()
        app = FastAPI()
        app.include_router(self.router.script_app)
        app.include_router(create_router(lambda: self.service))
        self.client = TestClient(app)
        self.disabled = logging.getLogger('oas').disabled
        logging.getLogger('oas').disabled = True

    def tearDown(self):
        self.client.close()
        self.runtime_patch.stop()
        self.log_patch.stop()
        self.ws_log_patch.stop()
        self.service.close()
        logging.getLogger('oas').disabled = self.disabled
        os.chdir(self.old_cwd)
        self.temp.cleanup()

    def headers(self):
        return {'X-Request-ID': str(uuid.uuid4()), 'X-Client-ID': 'legacy-test'}

    def events(self, kind=None):
        return self.store.query({'type': kind} if kind else {}, limit=200)['items']

    def restart(self):
        self.service.close()
        self.store = EventStore(self.root / 'runtime_data')
        self.service = RuntimeService(self.store, self.adapter)

    def test_legacy_value_boolean_response_safe_audit_source_and_retry(self):
        headers = self.headers()
        url = '/trial/GlobalGame/team_flow/password/value'
        params = {'types': 'string', 'value': 'NEVER_LOG_THIS_SECRET'}
        one = self.client.put(url, params=params, headers=headers)
        two = self.client.put(url, params=params, headers=headers)
        self.assertEqual((one.status_code, one.json(), two.json()), (200, True, True))
        audit = self.events('config.changed')
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]['source']['client_id'], 'legacy-test')
        self.assertEqual(audit[0]['request_id'], headers['X-Request-ID'])
        self.assertNotIn('NEVER_LOG_THIS_SECRET', str(audit) + str(self.logs))
        self.assertEqual(audit[0]['payload']['profiles'][0]['changes'][0]['new'], '[changed]')
        self.assertEqual(self.store.storage_status()['pending_requests'], 0)

    def test_copy_group_import_profile_and_sync_are_audited_with_old_shapes(self):
        copied = self.client.post('/config_copy', params={'file': 'copy', 'template': 'trial'})
        self.assertIsInstance(copied.json(), list)
        self.manager.add_script_file('copy')
        group = self.client.put('/config/task/group/copy', params={
            'task_name': 'Orochi', 'group_name': 'orochi_config', 'source_config_name': 'trial', 'dest_config_name': 'copy'})
        self.assertTrue(group.json())
        task = self.client.put('/config/task/copy', params={
            'task_name': 'Orochi', 'source_config_name': 'trial', 'dest_config_name': 'copy'})
        self.assertTrue(task.json())
        data = json.loads((self.root / 'config/trial.json').read_text())
        imported = self.client.post('/config/import', data={'name': 'uploaded'}, files={
            'file': ('SENSITIVE_UPLOAD_FILENAME.json', json.dumps(data).encode(), 'application/json')})
        self.assertEqual(imported.status_code, 200, imported.text)
        self.assertEqual(imported.json()['name'], 'uploaded')
        single = self.client.post('/config/task/import', data={
            'config_name': 'copy', 'task_name': 'Orochi', 'json_text': json.dumps({'orochi': data['orochi']})})
        self.assertTrue(single.json()['updated'])
        synchronized = self.client.put('/trial/Orochi/sync_next_run', params={'target_dt': '2030-01-01 00:00:00'})
        self.assertTrue(synchronized.json())
        actions = {item['payload']['action'] for item in self.events('config.changed')}
        self.assertTrue({'config.copy', 'config.group.copy', 'config.task.copy', 'config.import',
                         'config.task.import', 'config.sync_next_run'} <= actions)
        self.assertNotIn('SENSITIVE_UPLOAD_FILENAME', str(self.logs) + str(self.events()))

    def test_http_start_stop_share_core_and_retry_does_not_start_twice(self):
        headers = self.headers()
        self.assertIsNone(self.client.get('/trial/start', headers=headers).json())
        self.assertIsNone(self.client.get('/trial/start', headers=headers).json())
        self.assertEqual(self.manager.script_process['trial'].starts, 1)
        self.assertIsNone(self.client.get('/trial/stop').json())
        self.assertEqual(self.manager.script_process['trial'].stops, 1)
        self.assertEqual(len(self.events('control.requested')), 2)
        self.assertEqual(len(self.events('control.completed')), 2)

    def test_active_async_legacy_start_is_not_misreported_as_pending_recovery(self):
        headers = self.headers()
        entered, release = threading.Event(), threading.Event()
        process = self.manager.script_process['trial']
        original = process.start
        async def blocked_start():
            entered.set()
            while not release.is_set():
                await asyncio.sleep(0.01)
            await original()
        with patch.object(process, 'start', side_effect=blocked_start), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.client.get, '/trial/start', headers=headers)
            try:
                self.assertTrue(entered.wait(timeout=5))
                snapshot = self.client.get('/api/v2/recovery/operations')
                self.assertEqual(snapshot.status_code, 200, snapshot.text)
                self.assertEqual(snapshot.json()['pending_count'], 0)
                self.assertFalse(snapshot.json()['dispatch_blocked'])
                denied = self.client.post('/api/v2/recovery/operations/resolve', json={
                    'kind': 'requests', 'key': headers['X-Request-ID'], 'expected_revisions': {},
                    'reviewed': True, 'request_id': str(uuid.uuid4())})
                self.assertEqual(denied.status_code, 409, denied.text)
                self.assertEqual(denied.json()['error']['code'], 'operation_in_progress')
            finally:
                release.set()
            self.assertEqual(future.result(timeout=5).status_code, 200)
        self.assertEqual(process.starts, 1)
        self.assertEqual(self.service.active_operations, set())
        self.assertEqual(list_pending_operations(self.service), [])

    def test_storage_failure_blocks_config_but_does_not_block_stop(self):
        before = (self.root / 'config/trial.json').read_bytes()
        with patch.object(self.service, 'emit', side_effect=OSError('disk unavailable')):
            denied = self.client.put('/trial/Orochi/orochi_config/limit_count/value', params={'types': 'integer', 'value': 71})
            stopped = self.client.get('/trial/stop')
        self.assertEqual(denied.status_code, 503)
        self.assertEqual((self.root / 'config/trial.json').read_bytes(), before)
        self.assertEqual(stopped.status_code, 200)
        self.assertEqual(stopped.headers['X-OAS-Audit-State'], 'executed_not_saved')
        self.assertEqual(self.manager.script_process['trial'].stops, 1)
        self.assertTrue(self.service.dispatch_blocked)

    def test_result_audit_failure_preserves_boolean_and_reports_degraded_header(self):
        original = self.service.emit
        def fail_result(item):
            if item['type'] == 'config.changed':
                raise OSError('audit failure')
            return original(item)
        headers = self.headers()
        with patch.object(self.service, 'emit', side_effect=fail_result):
            result = self.client.put('/trial/Orochi/orochi_config/limit_count/value',
                params={'types': 'integer', 'value': 91}, headers=headers)
        self.assertTrue(result.json())
        self.assertEqual(result.headers['X-OAS-Audit-State'], 'executed_not_saved')
        retry = self.client.put('/trial/Orochi/orochi_config/limit_count/value',
                params={'types': 'integer', 'value': 91}, headers=headers)
        self.assertEqual(retry.status_code, 409)

    def test_rename_preserves_identity_and_delete_returns_boolean(self):
        headers = self.headers()
        for _ in range(2):
            result = self.client.put('/config', params={'old_name': 'trial', 'new_name': 'renamed'}, headers=headers)
            self.assertTrue(result.json())
        self.assertEqual(self.service.profile_id('renamed'), self.identity)
        self.assertNotIn('trial', self.service.registry['names'])
        deleted = self.client.delete('/config', params={'name': 'renamed'})
        self.assertTrue(deleted.json())
        self.assertFalse((self.root / 'config/renamed.json').exists())

    def test_rename_identity_is_repaired_after_file_move_then_process_death(self):
        self.service.close()
        code = '''
import os, sys, uuid
from pathlib import Path
from module.observability import EventStore
from module.server.solana_runtime import RuntimeService
from module.server.solana_legacy_audit import run_config_mutation, legacy_request_context
from module.server import solana_legacy_audit as audit
from module.server.config_manager import ConfigManager
os.chdir(sys.argv[1])
service = RuntimeService(EventStore(Path('runtime_data')))
audit._repair_identity = lambda *args, **kwargs: os._exit(77)
with legacy_request_context(str(uuid.uuid4()), 'crash-fixture'):
    run_config_mutation('config.rename', ['trial', 'renamed'], lambda: ConfigManager.rename('trial', 'renamed'),
        rename={'old_name': 'trial', 'new_name': 'renamed'}, service=service)
'''
        child = subprocess.run([sys.executable, '-c', code, str(self.root)], cwd=REPO,
                               capture_output=True, timeout=30)
        self.assertEqual(child.returncode, 77, child.stderr.decode(errors='replace'))
        self.restart()
        result = reconcile_legacy_operations(self.service)
        self.assertEqual(len(result['reconciled']), 1)
        self.assertEqual(result['unresolved'], [])
        self.assertEqual(self.service.profile_id('renamed'), self.identity)
        self.assertNotIn('trial', self.service.registry['names'])

    def test_rename_recovery_refuses_ambiguous_file_state(self):
        from module.server import solana_legacy_audit as audit
        with patch.object(audit, '_repair_identity', side_effect=OSError('identity save failed')):
            result = self.client.put('/config', params={'old_name': 'trial', 'new_name': 'renamed'})
        self.assertEqual(result.status_code, 503)
        (self.root / 'config/trial.json').write_text('{}', encoding='utf-8')
        self.restart()
        recovered = reconcile_legacy_operations(self.service)
        self.assertEqual(len(recovered['unresolved']), 1)
        self.assertEqual(recovered['reconciled'], [])
        self.assertTrue(self.service.dispatch_blocked)
        pending = list_pending_operations(self.service)[0]
        self.assertFalse(pending['resolvable'])
        with self.assertRaises(HTTPException) as rejected:
            resolve_pending_operation(self.service, pending['kind'], pending['key'],
                pending['expected_revisions'], reviewed=True, request_id=str(uuid.uuid4()))
        self.assertEqual(rejected.exception.detail['code'], 'rename_requires_reconciliation')
        (self.root / 'config/trial.json').unlink()
        self.assertEqual(reconcile_legacy_operations(self.service)['pending_count'], 0)
        self.assertEqual(self.service.profile_id('renamed'), self.identity)

    def pending_value(self, value=91):
        original = self.service.emit
        def fail_result(event):
            if event['type'] == 'config.changed':
                raise OSError('fixture audit failure')
            return original(event)
        headers = self.headers()
        with patch.object(self.service, 'emit', side_effect=fail_result):
            result = self.client.put('/trial/Orochi/orochi_config/limit_count/value',
                params={'types': 'integer', 'value': value}, headers=headers)
        self.assertTrue(result.json())
        return headers

    def test_pending_legacy_review_checks_revision_and_never_reexecutes(self):
        headers = self.pending_value()
        pending = list_pending_operations(self.service)[0]
        self.assertTrue(pending['resolvable'])
        self.assertEqual(pending['profile_id'], self.identity)
        review_id = str(uuid.uuid4())
        args = (self.service, pending['kind'], pending['key'], pending['expected_revisions'])
        with self.assertRaises(HTTPException) as rejected:
            resolve_pending_operation(*args, request_id=review_id)
        self.assertEqual(rejected.exception.detail['code'], 'review_required')
        # Another writer wins after the UI loaded its review snapshot.
        self.manager.config_cache('trial').model.script_set_arg('Orochi', 'orochi_config', 'limit_count', 93)
        with self.assertRaises(HTTPException) as stale:
            resolve_pending_operation(*args, reviewed=True, request_id=review_id)
        self.assertEqual(stale.exception.detail['code'], 'revision_conflict')
        self.assertEqual(len(self.events('recovery.requested')), 0)
        pending = list_pending_operations(self.service)[0]
        before = (self.root / 'config/trial.json').read_bytes()
        result = resolve_pending_operation(self.service, pending['kind'], pending['key'],
            pending['expected_revisions'], reviewed=True, request_id=review_id)
        self.assertEqual(result['historical_outcome'], 'unknown')
        self.assertFalse(result['reexecuted'])
        self.assertEqual((self.root / 'config/trial.json').read_bytes(), before)
        duplicate = resolve_pending_operation(self.service, pending['kind'], pending['key'],
            pending['expected_revisions'], reviewed=True, request_id=review_id)
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(len(self.events('recovery.resolved')), 1)
        self.assertEqual(list_pending_operations(self.service), [])
        completion = self.events('control.completed')[0]
        self.assertIsNone(completion['payload']['executed'])
        retry = self.client.put('/trial/Orochi/orochi_config/limit_count/value',
            params={'types': 'integer', 'value': 91}, headers=headers)
        self.assertEqual(retry.status_code, 409)
        self.assertEqual(retry.json()['detail']['code'], 'operation_outcome_unknown')

    def test_pending_review_remains_visible_after_mid_review_failure_and_restart(self):
        self.pending_value()
        pending = list_pending_operations(self.service)[0]
        review_id = str(uuid.uuid4())
        original = self.service.emit
        def fail_final(event):
            if event['type'] == 'recovery.resolved':
                raise OSError('fixture result failure')
            return original(event)
        with patch.object(self.service, 'emit', side_effect=fail_final):
            with self.assertRaises(HTTPException) as failed:
                resolve_pending_operation(self.service, pending['kind'], pending['key'],
                    pending['expected_revisions'], reviewed=True, request_id=review_id)
        self.assertEqual(failed.exception.status_code, 503)
        self.restart()
        pending = list_pending_operations(self.service)[0]
        self.assertEqual(pending['resolution_request_id'], review_id)
        result = resolve_pending_operation(self.service, pending['kind'], pending['key'],
            pending['expected_revisions'], reviewed=True, request_id=review_id)
        self.assertTrue(result['resolved'])
        self.assertEqual(list_pending_operations(self.service), [])
        self.assertEqual(len(self.events('control.completed')), 1)

    def test_pending_v2_config_has_identity_and_unknown_result_blocks_retry(self):
        from module.server.solana_runtime import ServiceError
        data = {'profile_id': self.identity, 'request_id': str(uuid.uuid4()),
            'expected_revision': self.adapter.revision('trial'), 'task': 'Orochi',
            'group': 'orochi_config', 'argument': 'limit_count', 'types': 'integer', 'value': 71}
        original = self.service.emit
        def fail_result(event):
            if event['type'] == 'config.changed':
                raise OSError('fixture')
            return original(event)
        with patch.object(self.service, 'emit', side_effect=fail_result):
            self.assertFalse(self.adapter.save_value(self.service, data)['persisted'])
        pending = list_pending_operations(self.service)[0]
        self.assertEqual((pending['kind'], pending['name'], pending['profile_id']),
                         ('config_requests', 'trial', self.identity))
        before = (self.root / 'config/trial.json').read_bytes()
        resolve_pending_operation(self.service, pending['kind'], pending['key'],
            pending['expected_revisions'], reviewed=True, request_id=str(uuid.uuid4()))
        self.assertEqual((self.root / 'config/trial.json').read_bytes(), before)
        with self.assertRaises(ServiceError) as rejected:
            self.adapter.save_value(self.service, data)
        self.assertEqual(rejected.exception.code, 'operation_outcome_unknown')

    def test_control_receipt_pending_is_visible_and_review_does_not_launch(self):
        request = {'profile_id': self.identity, 'request_id': str(uuid.uuid4()), 'action': 'start'}
        self.service.begin_control(request)
        self.service.owners['fixture'] = {'alive': True, 'profile_id': self.identity}
        pending = list_pending_operations(self.service)[0]
        self.assertEqual(pending['kind'], 'requests')
        self.assertFalse(pending['resolvable'])
        self.service.owners['fixture']['alive'] = False
        pending = list_pending_operations(self.service)[0]
        self.assertTrue(pending['resolvable'])
        resolve_pending_operation(self.service, pending['kind'], pending['key'],
            pending['expected_revisions'], reviewed=True, request_id=str(uuid.uuid4()))
        self.assertEqual(self.manager.script_process['trial'].starts, 0)
        receipt = self.service.begin_control(request)
        self.assertTrue(receipt['duplicate'])
        self.assertFalse(receipt['accepted'])
        self.assertIsNone(receipt['executed'])
        self.assertEqual(receipt['status'], 'unknown_state_accepted')
        self.assertEqual(list_pending_operations(self.service), [])

    def test_old_pending_without_identity_is_not_guessed(self):
        key = str(uuid.uuid4())
        self.store.checkpoints.save('config_requests', key, {'terminal': False, 'path': 'secret.password'})
        pending = list_pending_operations(self.service)[0]
        self.assertFalse(pending['resolvable'])
        self.assertIsNone(pending['profile_id'])
        with self.assertRaises(HTTPException) as rejected:
            resolve_pending_operation(self.service, 'config_requests', key, {},
                reviewed=True, request_id=str(uuid.uuid4()))
        self.assertEqual(rejected.exception.detail['code'], 'operation_identity_unknown')

    def test_process_death_between_intent_and_checkpoint_is_reconstructed_without_write(self):
        before = (self.root / 'config/trial.json').read_bytes()
        self.service.close()
        code = '''
import os, sys, uuid
from pathlib import Path
from module.observability import EventStore
from module.server.solana_runtime import RuntimeService
from module.server.solana_legacy_audit import run_config_mutation, legacy_request_context
from module.server.config_manager import ConfigManager
os.chdir(sys.argv[1])
service = RuntimeService(EventStore(Path('runtime_data')))
save = service.store.checkpoints.save
def cut(kind, *args, **kwargs):
    if kind == 'legacy_requests':
        os._exit(78)
    return save(kind, *args, **kwargs)
service.store.checkpoints.save = cut
with legacy_request_context(str(uuid.uuid4()), 'intent-crash-fixture'):
    run_config_mutation('config.copy', ['trial', 'copy'], lambda: ConfigManager.copy('copy', 'trial'), service=service)
'''
        child = subprocess.run([sys.executable, '-c', code, str(self.root)], cwd=REPO,
                               capture_output=True, timeout=30)
        self.assertEqual(child.returncode, 78, child.stderr.decode(errors='replace'))
        self.restart()
        self.assertEqual(self.store.checkpoints.list('legacy_requests'), [])
        result = reconcile_legacy_operations(self.service)
        self.assertEqual(result['pending_count'], 1)
        pending = list_pending_operations(self.service)[0]
        self.assertEqual(pending['kind'], 'legacy_requests')
        self.assertTrue(pending['resolvable'])
        self.assertEqual(pending['profile_id'], self.identity)
        self.assertEqual((self.root / 'config/trial.json').read_bytes(), before)
        self.assertFalse((self.root / 'config/copy.json').exists())
        resolve_pending_operation(self.service, pending['kind'], pending['key'],
            pending['expected_revisions'], reviewed=True, request_id=str(uuid.uuid4()))
        self.assertFalse((self.root / 'config/copy.json').exists())
        self.assertEqual(self.store.storage_status()['pending_requests'], 0)

    def test_v2_checkpoint_failure_reconstructs_explicit_metadata_only(self):
        request_id = str(uuid.uuid4())
        data = {'profile_id': self.identity, 'request_id': request_id,
            'expected_revision': self.adapter.revision('trial'), 'task': 'Orochi',
            'group': 'orochi_config', 'argument': 'limit_count', 'types': 'integer', 'value': 73}
        before = (self.root / 'config/trial.json').read_bytes()
        save = self.store.checkpoints.save
        def cut(kind, *args, **kwargs):
            if kind == 'config_requests':
                raise OSError('fixture checkpoint failure')
            return save(kind, *args, **kwargs)
        with patch.object(self.store.checkpoints, 'save', side_effect=cut):
            with self.assertRaises(OSError):
                self.adapter.save_value(self.service, data)
        self.assertEqual((self.root / 'config/trial.json').read_bytes(), before)
        pending = list_pending_operations(self.service)[0]
        self.assertEqual((pending['kind'], pending['name'], pending['key']),
                         ('config_requests', 'trial', request_id))
        self.assertTrue(pending['resolvable'])
        # The reconstructed original digest rejects changed replay input.
        from module.server.solana_runtime import ServiceError
        with self.assertRaises(ServiceError) as rejected:
            self.adapter.save_value(self.service, {**data, 'value': 74})
        self.assertEqual(rejected.exception.code, 'request_conflict')

    def test_old_orphan_intent_without_name_stays_unresolvable(self):
        key = str(uuid.uuid4())
        self.service.emit({'type': 'control.requested', 'request_id': key,
            'profile_id': self.identity, 'payload': {'action': 'start'}})
        recovered = reconcile_legacy_operations(self.service)
        self.assertEqual(recovered['pending_count'], 1)
        pending = list_pending_operations(self.service)[0]
        self.assertEqual((pending['kind'], pending['key']), ('requests', key))
        self.assertFalse(pending['resolvable'])
        self.assertIsNone(pending['name'])

    def test_websocket_plain_commands_and_typed_retry_are_audited(self):
        request_id = str(uuid.uuid4())
        with self.client.websocket_connect('/ws/trial', headers={'X-Client-ID': 'ws-fixture'}) as socket:
            socket.receive_json()
            socket.receive_json()
            socket.send_json({'type': 'start', 'request_id': request_id})
            self.assertEqual(socket.receive_json()['state'], 1)
            socket.send_text('stop')
            self.assertEqual(socket.receive_json()['state'], 0)
        events = self.events('control.requested')
        self.assertEqual(len(events), 2)
        self.assertTrue(all(item['source']['client_id'] == 'ws-fixture' for item in events))


if __name__ == '__main__':
    unittest.main()
