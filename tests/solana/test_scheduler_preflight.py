"""Proven business prerequisites must not manufacture failed attempts."""
import ast
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MethodType, SimpleNamespace
import unittest

from module.observability import EventStore
from module.scheduling.preflight import BusinessSkip, business_preflight
from module.scheduling.runtime import ExecutionRuntime, classify_outcome
from module.server.solana_runtime import RuntimeService


def method(path, name, globals_):
    tree = ast.parse(Path(path).read_text(encoding='utf-8-sig'))
    value = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[value], type_ignores=[])), path, 'exec'), globals_)
    return globals_[name]


class Clock(datetime):
    value = datetime(2026, 9, 24, 8)

    @classmethod
    def now(cls):
        return cls.value


class Adapter:
    def names(self):
        return ['preflight-test']

    def device(self, name):
        return {'device_id': 'preflight-test-device'}


class Bridge:
    def __init__(self, service, identity):
        self.service = service
        self.__dict__.update(identity)

    def call(self, operation, payload):
        return self.service.dispatch(self.owner_id, operation, payload)


class BusinessPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.store = EventStore(self.temp.name)
        self.service = RuntimeService(self.store, Adapter())
        identity = self.service.register_process('preflight-test', 'preflight-owner')
        self.now = 0.0
        self.execution = ExecutionRuntime(Bridge(self.service, identity), 'preflight-test', monotonic=lambda: self.now)
        result = self.service.dispatch('preflight-owner', 'scheduling.acquire', {
            'candidates': [{'task': 'DemonEncounter'}], 'legacy_order': ['DemonEncounter']})
        self.execution.lease = result['lease']
        self.execution.lease_started = self.now

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def test_before_window_is_audited_without_start_or_failed_denominator(self):
        Clock.value = datetime(2026, 9, 24, 8)
        schedule_writes = []
        script = SimpleNamespace(solana_execution=self.execution,
            config=SimpleNamespace(task_delay=lambda **value: schedule_writes.append(value)))
        defer = method('script.py', '_defer_business_precondition', {'datetime': Clock})
        self.now = 3.0
        self.assertTrue(defer(script, 'DemonEncounter'))
        self.assertEqual(schedule_writes[0]['target'], datetime(2026, 9, 24, 17, 30))
        self.assertIsNone(self.execution.active)
        self.assertIsNone(self.execution.lease)
        totals = self.store.statistics()['totals']
        self.assertEqual((totals['started'], totals['succeeded'], totals['failed'], totals['cancelled']), (0, 0, 0, 0))
        events = self.store.query()['items']
        self.assertFalse(any(item['type'].startswith('run.') for item in events))
        skipped = next(item for item in events if item['type'] == 'scheduler.skipped')
        self.assertEqual(skipped['task_id'], 'DemonEncounter')
        self.assertEqual(skipped['payload']['reason'], 'activity_window_closed')
        self.assertEqual(skipped['payload']['device_seconds'], 3.0)
        snapshot = self.service.coordinator.snapshot()[self.execution.device_id]
        key = self.execution.profile_id + ':DemonEncounter'
        self.assertEqual(snapshot['entities'][key]['service'], 3.0)

    def test_window_boundary_and_unrelated_task_keep_existing_semantics(self):
        self.assertIsNone(business_preflight('DemonEncounter', datetime(2026, 9, 24, 17)))
        self.assertIsNone(business_preflight('DemonEncounter', datetime(2026, 9, 24, 22, 59, 59)))
        after = business_preflight('DemonEncounter', datetime(2026, 9, 24, 23))
        self.assertEqual(after.next_run, datetime(2026, 9, 25, 17, 30))
        self.assertIsNone(business_preflight('Orochi', datetime(2026, 9, 24, 8)))
        self.assertEqual(classify_outcome(False), 'failed')
        self.assertEqual(classify_outcome(True, business_success=False), 'failed')
        self.assertEqual(classify_outcome(True, {'status': 'skipped'}), 'failed')

    def test_window_closes_after_prepare_explicitly_cancels_and_preserves_device_time(self):
        self.execution.begin('DemonEncounter', {})
        Clock.value = datetime(2026, 9, 24, 23)
        writes = []
        task = SimpleNamespace(start_time=datetime(2026, 9, 24, 22, 59, 59),
            config=SimpleNamespace(solana_execution=self.execution, task_delay=lambda *a, **kw: writes.append(kw)))
        scope = {'datetime': Clock, 'timedelta': timedelta,
                 'logger': SimpleNamespace(info=lambda *a: None)}
        task.set_next_run = MethodType(method('tasks/base_task.py', 'set_next_run', dict(scope)), task)
        task.mark_business_skipped = MethodType(method('tasks/base_task.py', 'mark_business_skipped', dict(scope)), task)
        check_time = method('tasks/DemonEncounter/script_task.py', 'check_time', scope)
        self.assertFalse(check_time(task))
        self.assertEqual(writes[0]['target'], datetime(2026, 9, 25, 17, 30))
        outcome = task.config.task_runtime_outcome
        self.assertEqual(outcome['status'], 'business_skipped')
        self.assertEqual(classify_outcome(True, outcome, self.execution.business_success), 'cancelled')
        self.now = 4.0
        self.execution.event('scheduler.skipped', {'reason': outcome['reason'], 'phase': 'business_precondition_after_prepare'})
        self.execution.finish('cancelled', 'business_precondition:' + outcome['reason'])
        totals = self.store.statistics()['totals']
        self.assertEqual((totals['started'], totals['failed'], totals['succeeded'], totals['cancelled']), (1, 0, 0, 1))
        self.assertEqual(totals['device_seconds'], 4.0)
        self.assertIsNone(totals['success_rate'])

    def test_preflight_skip_requires_explicit_marker_and_cannot_erase_active_run(self):
        with self.assertRaises(ValueError):
            self.execution.skip_before_begin('DemonEncounter', {'reason': 'guessed'})
        self.execution.begin('DemonEncounter', {})
        with self.assertRaises(ValueError):
            self.execution.skip_before_begin('DemonEncounter', BusinessSkip('window', datetime(2026, 9, 25)))


if __name__ == '__main__':
    unittest.main()
