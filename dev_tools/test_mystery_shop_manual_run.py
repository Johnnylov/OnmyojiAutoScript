"""Replay the OASX run-now API through the real queue and shop admission guard.

All persisted state is temporary. No user config, device or API server is loaded.
"""

import ast
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, time
import json
import re
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import sys

import test_mystery_shop_scroll as helpers


class ManualRunTests(unittest.TestCase):
    setUp = helpers.MysteryShopIndependentStoreTests.setUp
    make_world = helpers.MysteryShopIndependentStoreTests.make_world
    make_queue = helpers.MysteryShopIndependentStoreTests.make_queue

    def world(self, account='oas2'):
        world = self.make_world(account)
        world.current_date = datetime(2026, 9, 16, 9, 39, 55)
        world.task.config.mystery_shop.shop_config.time_of_mystery = time(6)
        world.task._independent_schedule.write_next_run(datetime(2026, 9, 19, 6))
        return world

    def api(self, worlds):
        configs = {}
        for world in worlds:
            config, _, data = self.make_queue(world, datetime(2026, 9, 19, 6))
            config.model.script_set_arg = Mock(return_value=True)
            configs[world.task.config.config_name] = config
        namespace = {'datetime': worlds[0].clock_datetime(), 'logger': Mock(), 're': re,
                     'mm': SimpleNamespace(config_cache=lambda name: configs[name]),
                     # This AST fixture tests shop admission. Full durable audit
                     # and HTTP behavior use real routes in test_legacy_audit.
                     'run_config_mutation': lambda action, names, callback, **kwargs: callback()}

        class HTTPException(Exception):
            def __init__(self, status_code, detail):
                self.status_code = status_code
                super().__init__(detail)

        namespace['HTTPException'] = HTTPException
        helpers.function_from_file(helpers.ROOT / 'module/config/utils.py',
                                   'convert_to_underscore', namespace)
        path = helpers.ROOT / 'module/server/script_router.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        handler = next(node for node in tree.body
                       if isinstance(node, ast.AsyncFunctionDef) and node.name == 'script_task'
                       and any(arg.arg == 'types' for arg in node.args.args))
        handler.decorator_list = []
        exec(compile(ast.fix_missing_locations(ast.Module(body=[handler], type_ignores=[])),
                     str(path), 'exec'), namespace)
        schedule_module = ModuleType('tasks.MysteryShop.schedule')
        schedule_module.MysteryShopSchedule = lambda name: self.store_class(name, root=self.root)

        def invoke(account='oas2', task='MysteryShop', group='scheduler', argument='next_run',
                   types='next_run', value='2026-09-15 09:39:55'):
            with patch.dict(sys.modules, {'tasks.MysteryShop.schedule': schedule_module}):
                return asyncio.run(namespace['script_task'](
                    account, task, group, argument, types, value))

        return invoke, configs, HTTPException

    def test_oas2_button_becomes_pending_runs_once_then_restores_automatic_time(self):
        world = self.world()
        store = world.task._independent_schedule
        automatic_bytes = store.path.read_bytes()
        invoke, configs, _ = self.api([world])
        config = configs['oas2']
        config.update_scheduler()
        self.assertNotIn('MysteryShop', [task.command for task in config.pending_task])
        self.assertTrue(invoke())
        config.model.script_set_arg.assert_called_once_with(
            'MysteryShop', 'scheduler', 'next_run', datetime(2026, 9, 15, 9, 39, 55))
        world.now += 1
        config.update_scheduler()
        self.assertIn('MysteryShop', [task.command for task in config.pending_task])
        # Queue refreshes are read-only and cannot spend the request.
        config.update_scheduler()
        self.assertIsNotNone(store.read_manual_run(world.current_date))
        world.task._ensure_shop_due()
        self.assertIsNone(store.read_manual_run(world.current_date))
        self.assertEqual(store.path.read_bytes(), automatic_bytes)
        with self.assertRaises(world.namespace['TaskEnd']):
            world.task._ensure_shop_due()
        config.update_scheduler()
        self.assertNotIn('MysteryShop', [task.command for task in config.pending_task])
        self.assertEqual(store.read_next_run(), datetime(2026, 9, 19, 6))

    def test_manual_oas2_request_does_not_affect_oas1(self):
        oas1, oas2 = self.world('oas1'), self.world('oas2')
        invoke, configs, _ = self.api([oas1, oas2])
        invoke()
        for world in (oas1, oas2):
            world.now += 1
            configs[world.task.config.config_name].update_scheduler()
        self.assertNotIn('MysteryShop', [task.command for task in configs['oas1'].pending_task])
        self.assertIn('MysteryShop', [task.command for task in configs['oas2'].pending_task])
        self.assertFalse(oas1.task._independent_schedule.consume_manual_run(oas1.current_date))
        self.assertTrue(oas2.task._independent_schedule.consume_manual_run(oas2.current_date))

    def test_ordinary_date_edit_and_other_task_never_grant_manual_override(self):
        world = self.world()
        invoke, configs, _ = self.api([world])
        for kwargs in ({'types': 'date_time'}, {'task': 'SoulZone'},
                       {'types': 'next_run', 'value': '2026-09-19 06:00:00'}):
            with self.subTest(kwargs=kwargs):
                self.assertTrue(invoke(**kwargs))
                self.assertFalse(world.task._independent_schedule.manual_path.exists())
        configs['oas2'].update_scheduler()
        self.assertNotIn('MysteryShop', [task.command for task in configs['oas2'].pending_task])

    def test_failed_config_save_and_invalid_date_do_not_create_request(self):
        world = self.world()
        invoke, configs, error = self.api([world])
        configs['oas2'].model.script_set_arg.return_value = False
        self.assertFalse(invoke())
        with self.assertRaises(error) as caught:
            invoke(value='invalid')
        self.assertEqual(caught.exception.status_code, 400)
        self.assertFalse(world.task._independent_schedule.manual_path.exists())

    def test_request_survives_restart_and_only_one_consumer_wins(self):
        world = self.world()
        store = world.task._independent_schedule
        store.request_manual_run(world.current_date)
        stores = [self.store_class('oas2', root=self.root) for _ in range(2)]
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda state: state.consume_manual_run(world.current_date), stores))
        self.assertEqual(sorted(results), [False, True])
        self.assertFalse(self.store_class('oas2', root=self.root).consume_manual_run(world.current_date))

    def test_ignored_stale_and_future_requests_do_not_bypass_schedule(self):
        world = self.world()
        store = world.task._independent_schedule
        for requested in (world.current_date - timedelta(days=1),
                          world.current_date + timedelta(hours=1)):
            with self.subTest(requested=requested):
                store.request_manual_run(requested)
                self.assertIsNone(store.read_manual_run(world.current_date))
                with self.assertRaises(world.namespace['TaskEnd']):
                    world.task._ensure_shop_due()

    def test_closed_day_still_exits_before_entering_shop(self):
        world = self.world()
        world.current_date += timedelta(days=1)
        world.task._independent_schedule.request_manual_run(world.current_date)
        world.task.goto_page = Mock()
        with self.assertRaises(world.namespace['TaskEnd']):
            world.task.run()
        world.task.goto_page.assert_not_called()
        self.assertFalse(world.task._independent_schedule.consume_manual_run(world.current_date))

    def test_corrupt_automatic_state_still_blocks_explicit_request(self):
        world = self.world()
        store = world.task._independent_schedule
        store.request_manual_run(world.current_date)
        store.path.write_text('{broken', encoding='utf-8')
        world.task.goto_page = Mock()
        with self.assertRaises(world.namespace['RequestHumanTakeover']):
            world.task.run()
        world.task.goto_page.assert_not_called()
        self.assertIsNotNone(store.read_manual_run(world.current_date))

    def test_consumption_write_failure_blocks_entry_and_does_not_fake_success(self):
        world = self.world()
        store = world.task._independent_schedule
        store.request_manual_run(world.current_date)
        world.task.goto_page = Mock()
        with patch.dict(store.consume_manual_run.__globals__,
                        {'write_file': Mock(side_effect=OSError('read only'))}):
            with self.assertRaises(world.namespace['RequestHumanTakeover']):
                world.task.run()
        world.task.goto_page.assert_not_called()
        self.assertIsNotNone(store.read_manual_run(world.current_date))

    def test_invalid_manual_account_or_metadata_blocks_entry(self):
        world = self.world()
        store = world.task._independent_schedule
        store.request_manual_run(world.current_date)
        valid = json.loads(store.manual_path.read_text(encoding='utf-8'))
        for data in (dict(valid, config_name='oas1'), dict(valid, pending='yes'),
                     dict(valid, version=True), dict(valid, requested_at='invalid')):
            with self.subTest(data=data):
                store.manual_path.write_text(json.dumps(data), encoding='utf-8')
                with self.assertRaises(world.namespace['RequestHumanTakeover']):
                    world.task._ensure_shop_due()


if __name__ == '__main__':
    unittest.main()
