"""Real Pydantic config writers against isolated config folders; no game IO."""
import copy
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from module.config.config_model import ConfigModel
from module.config.config import Config
from module.config.edit_lock import ConfigConflict, merge_config_fields
from module.server.config_manager import ConfigManager
from module.server.solana_adapter import ManagerAdapter
from module.server.solana_runtime import RuntimeService, ServiceError
from module.observability import EventStore


REPOSITORY = Path(__file__).resolve().parents[2]


class RealAdapter(ManagerAdapter):
    def __init__(self):
        self.cache = lambda name: Config(name)

    @property
    def manager(self):
        return SimpleNamespace(all_script_files=lambda: ['trial'], config_cache=self.cache)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.previous_cwd = Path.cwd()
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / 'config').mkdir()
        os.chdir(self.root)
        data = ConfigModel().model_dump()
        data['config_name'] = 'trial'
        self.path = self.root / 'config' / 'trial.json'
        self.path.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding='utf-8')
        self.store = EventStore(self.root / 'runtime_data')
        self.adapter = RealAdapter()
        self.service = RuntimeService(self.store, self.adapter)
        self.disabled = logging.getLogger('oas').disabled
        logging.getLogger('oas').disabled = True

    def tearDown(self):
        self.service.close()
        os.chdir(self.previous_cwd)
        logging.getLogger('oas').disabled = self.disabled
        self.temporary.cleanup()

    def raw(self):
        return json.loads(self.path.read_text(encoding='utf-8'))

    def request(self, value, *, task='Orochi', group='orochi_config', argument='limit_count', types='integer', **kwargs):
        import uuid
        return {'profile_id': self.service.profile_id('trial'), 'task': task, 'group': group,
                'argument': argument, 'value': value, 'types': types,
                'expected_revision': self.adapter.revision('trial'),
                'request_id': str(uuid.uuid4()), **kwargs}

    def test_running_marker_and_task_delay_preserve_ui_edits_and_fixed_view(self):
        worker = Config('trial')
        self.adapter.save_value(self.service, self.request(123))
        worker.model.running_task = 'Orochi'
        worker.task_delay(task='Orochi', target=datetime.now() + timedelta(days=5), server=False)
        self.assertEqual(self.raw()['orochi']['orochi_config']['limit_count'], 123)
        self.assertEqual(self.raw()['running_task'], 'Orochi')
        self.assertEqual(worker.orochi.orochi_config.limit_count, 30)
        self.assertEqual(ConfigModel('trial').orochi.orochi_config.limit_count, 123)

    def test_same_field_conflicts_even_after_unrelated_successful_save(self):
        worker = ConfigModel('trial')
        ui = ConfigModel('trial')
        self.assertTrue(ui.script_set_arg('Orochi', 'orochi_config', 'limit_count', 90))
        worker.running_task = 'Orochi'
        before = self.path.read_bytes()
        worker.orochi.orochi_config.limit_count = 60
        with self.assertRaises(ConfigConflict) as caught:
            worker.save()
        self.assertEqual(caught.exception.paths, ['orochi.orochi_config.limit_count'])
        self.assertEqual(self.path.read_bytes(), before)

    def test_old_client_conflict_returns_false_and_rolls_back_local_argument(self):
        stale = ConfigModel('trial')
        ConfigModel('trial').script_set_arg('Orochi', 'orochi_config', 'limit_count', 90)
        before = self.path.read_bytes()
        self.assertFalse(stale.script_set_arg('Orochi', 'orochi_config', 'limit_count', 60))
        self.assertEqual(stale.orochi.orochi_config.limit_count, 30)
        self.assertEqual(self.path.read_bytes(), before)

    def test_same_value_concurrent_save_is_idempotent(self):
        one, two = ConfigModel('trial'), ConfigModel('trial')
        self.assertTrue(one.script_set_arg('Orochi', 'orochi_config', 'limit_count', 77))
        self.assertTrue(two.script_set_arg('Orochi', 'orochi_config', 'limit_count', 77))

    def test_conflict_aborts_all_fields_not_a_partial_patch(self):
        stale = ConfigModel('trial')
        ConfigModel('trial').script_set_arg('Orochi', 'orochi_config', 'limit_count', 77)
        stale.orochi.orochi_config.limit_count = 45
        stale.orochi.scheduler.priority = 8
        before = self.path.read_bytes()
        with self.assertRaises(ConfigConflict):
            stale.save()
        self.assertEqual(self.path.read_bytes(), before)

    def test_deleted_empty_profile_cannot_be_recreated_by_stale_writer(self):
        self.path.write_text('{}', encoding='utf-8')
        stale = ConfigModel('trial')
        ConfigManager.delete('trial')
        with self.assertRaises(ConfigConflict):
            stale.running_task = 'Orochi'
        self.assertFalse(self.path.exists())

    def test_unknown_custom_fields_survive_and_defaults_do_not_override_remote(self):
        raw = self.raw()
        raw['private_custom_task'] = {'keep': 7}
        raw['orochi']['custom_field'] = {'retain': True}
        del raw['orochi']['scheduler']['fair_weight']
        self.path.write_text(json.dumps(raw), encoding='utf-8')
        worker, ui = ConfigModel('trial'), ConfigModel('trial')
        self.assertTrue(ui.script_set_arg('Orochi', 'scheduler', 'fair_weight', 3))
        worker.running_task = 'Orochi'
        after = self.raw()
        self.assertEqual(after['private_custom_task'], {'keep': 7})
        self.assertEqual(after['orochi']['custom_field'], {'retain': True})
        self.assertEqual(after['orochi']['scheduler']['fair_weight'], 3)

    def test_lists_reject_concurrent_reordering(self):
        base = {'list': [{'id': 'a', 'count': 1}, {'id': 'b', 'count': 2}]}
        wanted = copy.deepcopy(base)
        wanted['list'][0]['count'] = 3
        current = {'list': list(reversed(base['list']))}
        with self.assertRaises(ConfigConflict):
            merge_config_fields(base, base, wanted, current)

    def test_reset_flag_and_schedule_side_effect_commit_once(self):
        model = ConfigModel('trial')
        target = datetime(2030, 2, 3, 4, 5, 6)
        self.assertTrue(model.script_set_arg('Restart', 'tasks_config_reset', 'reset_task_datetime', target))
        from module.config import config_model as module
        with patch.object(module, 'write_file', wraps=module.write_file) as write:
            self.assertTrue(model.script_set_arg('Restart', 'tasks_config_reset', 'reset_task_datetime_enable', True))
            self.assertEqual(write.call_count, 1)
        result = self.raw()
        self.assertTrue(result['restart']['tasks_config_reset']['reset_task_datetime_enable'])
        self.assertEqual(result['orochi']['scheduler']['next_run'], '2030-02-03 04:05:06')
        self.assertEqual(result['mystery_shop']['scheduler']['next_run'], '2030-02-03 04:05:06')

    def test_invalid_field_bounds_enum_interval_and_fraction_do_not_write(self):
        before = self.path.read_bytes()
        for request in [self.request(0, group='scheduler', argument='fair_weight', types='number'),
                        self.request('bad', argument='user_status', types='enum'),
                        self.request('oops', group='scheduler', argument='success_interval', types='time_delta'),
                        self.request(12.5)]:
            with self.subTest(request=request['argument']):
                with self.assertRaises(ServiceError) as caught:
                    self.adapter.save_value(self.service, request)
                self.assertEqual(caught.exception.code, 'invalid_field')
                self.assertEqual(self.path.read_bytes(), before)

    def test_revision_conflict_and_stale_cached_model_conflict(self):
        request = self.request(42)
        stale = Config('trial')
        ConfigModel('trial').script_set_arg('Orochi', 'orochi_config', 'limit_count', 60)
        with self.assertRaises(ServiceError) as caught:
            self.adapter.save_value(self.service, request)
        self.assertEqual(caught.exception.code, 'revision_conflict')
        self.adapter.cache = lambda name: stale
        with self.assertRaises(ServiceError) as caught:
            self.adapter.save_value(self.service, self.request(43))
        self.assertEqual(caught.exception.code, 'revision_conflict')
        self.assertEqual(self.raw()['orochi']['orochi_config']['limit_count'], 60)

    def test_normal_and_secret_audits_and_request_retry(self):
        first = self.request(78)
        self.assertTrue(self.adapter.save_value(self.service, first)['persisted'])
        self.assertTrue(self.adapter.save_value(self.service, first)['duplicate'])
        self.adapter.save_value(self.service, self.request('sensitive-example-value', task='GlobalGame', group='team_flow', argument='password', types='string'))
        audit = self.store.query({'type': 'config.changed'})['items']
        self.assertEqual(len(audit), 2)
        changes = [change for item in audit for change in item['payload']['changes']]
        self.assertIn({'path': 'orochi.orochi_config.limit_count', 'old': 30, 'new': 78}, changes)
        self.assertIn({'path': 'global_game.team_flow.password', 'old': '[changed]', 'new': '[changed]'}, changes)
        all_persisted = b''.join(p.read_bytes() for p in (self.root / 'runtime_data').rglob('*') if p.is_file() and p.name != 'writer.lock')
        self.assertNotIn(b'sensitive-example-value', all_persisted)
        self.assertNotIn('sensitive-example-value', str(self.service.stream_after(self.service.stream_id, 0)))
        self.assertEqual(self.store.storage_status()['pending_requests'], 0)

    def test_audit_intent_failure_does_not_edit_and_result_failure_is_explicit(self):
        before = self.path.read_bytes()
        original = self.service.emit
        with patch.object(self.service, 'emit', side_effect=OSError('intent unavailable')):
            with self.assertRaises(OSError):
                self.adapter.save_value(self.service, self.request(88))
        self.assertEqual(before, self.path.read_bytes())
        request = self.request(89)
        def failing_result(item):
            if item['type'] == 'config.changed':
                raise OSError('result unavailable')
            return original(item)
        with patch.object(self.service, 'emit', side_effect=failing_result):
            result = self.adapter.save_value(self.service, request)
        self.assertTrue(result['saved'])
        self.assertFalse(result['persisted'])
        self.assertEqual(result['status'], 'executed_not_saved')
        self.assertEqual(self.raw()['orochi']['orochi_config']['limit_count'], 89)
        with self.assertRaises(ServiceError) as caught:
            self.adapter.save_value(self.service, request)
        self.assertEqual(caught.exception.code, 'reconciliation_required')

    def test_mystery_shop_date_edit_is_not_manual_run_and_retry_is_once(self):
        from tasks.MysteryShop.schedule import MysteryShopSchedule
        shop = MysteryShopSchedule('trial', root=self.root / 'shop-state')
        past = datetime.now() - timedelta(seconds=30)
        with patch('tasks.MysteryShop.schedule.MysteryShopSchedule', return_value=shop):
            self.adapter.save_value(self.service, self.request(past.isoformat(), task='MysteryShop', group='scheduler', argument='next_run', types='date_time'))
            self.assertFalse(shop.manual_path.exists())
            request = self.request(past.isoformat(), task='MysteryShop', group='scheduler', argument='next_run', types='next_run')
            with patch.object(shop, 'request_manual_run', wraps=shop.request_manual_run) as manual:
                self.adapter.save_value(self.service, request)
                self.adapter.save_value(self.service, request)
                self.assertEqual(manual.call_count, 1)
            self.assertIsNotNone(shop.read_manual_run(datetime.now()))

    def test_config_manager_import_copy_rename_delete_use_compatible_real_models(self):
        ConfigManager.copy('copy', 'trial')
        stale = ConfigModel('copy')
        patch_task = self.raw()['orochi']
        patch_task['orochi_config']['limit_count'] = 88
        ConfigManager.import_task_config('copy', 'Orochi', {'orochi': patch_task})
        stale.running_task = 'Orochi'
        self.assertEqual(ConfigModel('copy').orochi.orochi_config.limit_count, 88)
        ConfigManager.import_config('imported', self.raw())
        self.assertTrue(ConfigManager.rename('copy', 'renamed'))
        with self.assertRaises(ConfigConflict):
            stale.running_task = ''
        self.assertFalse((self.root / 'config' / 'copy.json').exists())
        self.assertTrue(ConfigManager.delete('renamed'))

    def test_import_keeps_name_error_contract_and_generic_alias_validation(self):
        from module.server.config_manager import ConfigNameError
        from pydantic import BaseModel
        self.assertFalse(ConfigManager._is_model_type(list[str]))
        self.assertTrue(ConfigManager._is_model_type(BaseModel))
        with self.assertRaises(ConfigNameError):
            ConfigManager.import_config('../escape', self.raw())

    def test_detached_template_generator_is_preserved(self):
        ConfigModel().write_json('template', ConfigModel().model_dump())
        ConfigModel().write_json('template', ConfigModel().model_dump())
        self.assertTrue((self.root / 'config' / 'template.json').exists())

    def test_real_child_process_stale_writer_preserves_ui_value(self):
        code = '''
import os, sys, time
from pathlib import Path
from module.config.config_model import ConfigModel
os.chdir(sys.argv[1])
model = ConfigModel('trial')
Path('ready').write_text('ok')
deadline = time.monotonic() + 8
while not Path('go').exists():
    if time.monotonic() > deadline: raise RuntimeError('barrier timed out')
    time.sleep(.01)
model.running_task = 'Orochi'
model.orochi.scheduler.priority = 8
model.write_json('trial', model.dict())
'''
        child = subprocess.Popen([sys.executable, '-c', code, str(self.root)], cwd=REPOSITORY,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 8
            while not (self.root / 'ready').exists():
                if time.monotonic() > deadline:
                    self.fail('child did not reach barrier')
                time.sleep(.01)
            self.adapter.save_value(self.service, self.request(211))
            (self.root / 'go').write_text('ok')
            output, error = child.communicate(timeout=10)
            self.assertEqual(child.returncode, 0, error.decode(errors='replace'))
            self.assertEqual(self.raw()['orochi']['orochi_config']['limit_count'], 211)
            self.assertEqual(self.raw()['orochi']['scheduler']['priority'], 8)
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate()


if __name__ == '__main__':
    unittest.main()
