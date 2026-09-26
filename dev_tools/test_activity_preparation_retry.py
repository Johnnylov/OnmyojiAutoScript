"""Offline scheduler regressions for activity setup that fails before climbing.

Run: toolkit/python.exe -m unittest discover -s dev_tools -p test_activity_preparation_retry.py -v
"""

import ast
import copy
from contextlib import nullcontext
from datetime import date, datetime, time, timedelta
from pathlib import Path
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from module.exception import ActivityPreparationTimeout, BattleTransitionTimeout, TaskEnd


ROOT = Path(__file__).resolve().parents[1]


def source_methods(path, class_name, names, namespace):
    tree = ast.parse((ROOT / path).read_text(encoding='utf-8-sig'))
    original = next(node for node in tree.body
                    if isinstance(node, ast.ClassDef) and node.name == class_name)
    methods = [node for node in original.body
               if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in methods} == set(names)
    subject = ast.ClassDef(name='Subject', bases=[], keywords=[], body=methods, decorator_list=[])
    module = ast.Module(body=[ast.ImportFrom(module='__future__',
                        names=[ast.alias(name='annotations')], level=0), subject], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(ROOT / path), 'exec'), namespace)
    return namespace['Subject']


def source_functions(path, names, namespace):
    tree = ast.parse((ROOT / path).read_text(encoding='utf-8-sig'))
    functions = [node for node in tree.body
                 if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in functions} == set(names)
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(ROOT / path), 'exec'), namespace)


class FrozenDatetime(datetime):
    @classmethod
    def now(cls):
        return cls(2026, 9, 14, 13, 1, 51, 906000)


class EndLoop(BaseException):
    pass


class ActivityPreparationRetryTests(unittest.TestCase):
    def setUp(self):
        self.random_delay = Mock(return_value=0)
        namespace = dict(
            logger=Mock(), TaskEnd=TaskEnd, ActivityPreparationTimeout=ActivityPreparationTimeout,
            BattleTransitionTimeout=BattleTransitionTimeout, datetime=FrozenDatetime,
            timedelta=timedelta, date=date, time=time, _log_switch_lock=nullcontext(), IS_WINDOWS=False,
            ScriptRuntimeDecision=SimpleNamespace(RESCHEDULE='reschedule', FAILED='failed'),
            del_cached_property=Mock(), inflection=SimpleNamespace(camelize=lambda name: name),
            I18n=SimpleNamespace(trans_zh_cn=lambda name: name),
            convert_to_underscore=lambda _: 'activity_shikigami',
            random=SimpleNamespace(randint=self.random_delay),
        )
        source_functions('module/config/utils.py',
                         ['nearest_future', 'dict_to_kv', 'parse_tomorrow_server'], namespace)
        config_class = source_methods('module/config/config.py', 'Config', ['task_delay'], namespace)
        script_class = source_methods('script.py', 'Script',
                                      ['_handle_task_exception', '_set_task_runtime_outcome', 'loop'], namespace)
        self.script = script_class()
        self.script.config_name = 'offline'
        self.script.save_error_log = Mock()
        self.schedule = SimpleNamespace(
            enable=True, next_run=FrozenDatetime.now() - timedelta(minutes=1),
            success_interval=timedelta(days=1), failure_interval=timedelta(days=1),
            server_update=time(13, 0), delay_date=1, float_time=time(0, 0),
        )
        self.script.config = SimpleNamespace(
            config_name='offline',
            model=SimpleNamespace(activity_shikigami=SimpleNamespace(scheduler=self.schedule)),
            reload=Mock(), save=Mock(), lock_config=Mock(), task_call=Mock(), notifier=Mock(),
            script=SimpleNamespace(device=SimpleNamespace(run_background_only=True),
                                   error=SimpleNamespace(handle_error=True, error_repeated=False)),
        )
        # The extracted task_delay now reads a separate scheduling snapshot.
        # Keep this offline fixture independent from real configuration files.
        namespace['ConfigModel'] = lambda config_name: SimpleNamespace(
            activity_shikigami=SimpleNamespace(scheduler=copy.deepcopy(self.schedule)), save=Mock())
        self.real_delay = MethodType(config_class.task_delay, self.script.config)
        self.script.config.task_delay = Mock(wraps=self.real_delay)
        self.script.anti_ban_guard = Mock()
        self.script.device = Mock()
        self.script.runtime = SimpleNamespace(prepare_task_execution=Mock(return_value='ready'))
        self.script.is_first_task = False
        self.script.failure_record = {}
        self.error = ActivityPreparationTimeout('Dispatch submitted but the map was not recovered')
        self.retry_at = FrozenDatetime(2026, 9, 14, 13, 2, 51)

    def test_preparation_error_is_a_specific_transition_error(self):
        self.assertIsInstance(self.error, BattleTransitionTimeout)

    def test_unstarted_activity_retries_in_one_minute_despite_daily_failure_schedule(self):
        self.assertFalse(self.script._handle_task_exception(self.error, 'ActivityShikigami'))
        self.script.config.task_delay.assert_called_once_with(
            task='ActivityShikigami', target=self.retry_at, server=False)
        self.assertEqual(self.schedule.next_run, self.retry_at)
        self.assertTrue(self.schedule.enable)
        self.assertEqual(self.schedule.failure_interval, timedelta(days=1))
        self.script.config.task_call.assert_called_once_with('Restart')
        self.script.save_error_log.assert_called_once()
        self.assertEqual(self.script.last_task_runtime_outcome,
                         dict(task='ActivityShikigami', status='retry_scheduled', wait_until=self.retry_at))
        self.assertEqual(self.script.config.task_runtime_outcome, self.script.last_task_runtime_outcome)
        self.script.config.reload.assert_not_called()

    def test_default_server_time_does_not_add_random_delay_to_retry(self):
        self.schedule.server_update = time(9, 0)
        self.schedule.float_time = time(0, 30)
        self.random_delay.return_value = 137
        self.assertFalse(self.script._handle_task_exception(self.error, 'ActivityShikigami'))
        self.assertEqual(self.schedule.next_run, self.retry_at)
        self.random_delay.assert_not_called()

    def test_real_battle_timeout_retains_daily_skip_semantics(self):
        self.assertTrue(self.script._handle_task_exception(
            BattleTransitionTimeout('Battle entry timed out'), 'ActivityShikigami'))
        self.script.config.task_delay.assert_called_once_with(task='ActivityShikigami', success=False)
        self.assertEqual(self.schedule.next_run, FrozenDatetime(2026, 9, 15, 13, 0))
        self.assertEqual(self.script.last_task_runtime_outcome['status'], 'skipped')

    def configure_loop(self, failures):
        pending = ['ActivityShikigami']
        executed = []
        failure_counts = []
        self.script.config.task_call = Mock(side_effect=lambda name: pending.insert(0, name))

        def delay(**kwargs):
            self.real_delay(**kwargs)
            pending.append(kwargs['task'])

        def get_next_task():
            if not pending:
                raise EndLoop()
            return pending.pop(0)

        def run(command):
            executed.append(command)
            if command == 'ActivityShikigami':
                failure_counts.append(self.script.failure_record.get(command, 0))
                if executed.count(command) <= failures:
                    return self.script._handle_task_exception(self.error, command)
            return True

        self.script.config.task_delay = Mock(side_effect=delay)
        self.script.get_next_task = get_next_task
        self.script.run = run
        return executed, failure_counts

    def test_actual_loop_restarts_and_retries_unstarted_climb(self):
        executed, counts = self.configure_loop(failures=1)
        with patch('builtins.exit', side_effect=AssertionError('Unexpected scheduler termination')):
            with self.assertRaises(EndLoop):
                self.script.loop()
        self.assertEqual(executed, ['ActivityShikigami', 'Restart', 'ActivityShikigami'])
        self.assertEqual(counts, [0, 1])
        self.assertEqual(self.script.failure_record, {'ActivityShikigami': 0, 'Restart': 0})
        self.script.config.notifier.push.assert_not_called()

    def test_actual_loop_stops_after_three_setup_failures_even_when_restart_succeeds(self):
        executed, counts = self.configure_loop(failures=3)
        with patch('builtins.exit', side_effect=SystemExit) as terminate:
            with self.assertRaises(SystemExit):
                self.script.loop()
        self.assertEqual(executed, ['ActivityShikigami', 'Restart', 'ActivityShikigami',
                                    'Restart', 'ActivityShikigami'])
        self.assertEqual(counts, [0, 1, 2])
        self.assertEqual(self.script.failure_record['ActivityShikigami'], 3)
        self.assertEqual(self.script.config.task_call.call_count, 3)
        self.assertEqual(self.script.config.task_delay.call_count, 3)
        self.script.config.notifier.push.assert_called_once()
        terminate.assert_called_once_with(1)

    def test_disabled_error_recovery_still_stops_on_first_setup_failure(self):
        self.script.config.script.error.handle_error = False
        executed, counts = self.configure_loop(failures=3)
        self.script.loop()
        self.assertEqual(executed, ['ActivityShikigami'])
        self.assertEqual(counts, [0])
        self.assertEqual(self.script.failure_record['ActivityShikigami'], 1)


if __name__ == '__main__':
    unittest.main()
