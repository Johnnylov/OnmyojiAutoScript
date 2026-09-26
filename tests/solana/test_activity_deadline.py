"""Deadline settings are optional cutoffs, including legacy/shadow dispatch."""
import ast
import copy
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import unittest

from pydantic import ValidationError
from module.scheduling.core import Candidate, FairScheduler, Policy
from module.scheduling.coordinator import Coordinator
from module.scheduling.deadline import deadline_timestamp, is_expired
from module.scheduling.runtime import ExecutionRuntime, DeadlineExpired, SafeBoundaryExit, classify_outcome
from module.server.solana_adapter import ManagerAdapter
from tasks.Component.config_scheduler import Scheduler


class Checkpoints:
    def __init__(self):
        self.data = {}

    def get(self, kind, key):
        return copy.deepcopy(self.data.get((kind, key)))

    def save(self, kind, key, data, **kwargs):
        self.data[kind, key] = copy.deepcopy(data)


class Bridge:
    profile_id, owner_id, device_id = 'deadline-test', 'worker', 'deadline-device'

    def __init__(self, coordinator):
        self.coordinator, self.events = coordinator, []

    def call(self, operation, payload):
        if operation == 'event.append':
            self.events.append(copy.deepcopy(payload))
            return {'seq': len(self.events)}
        return self.coordinator.dispatch(operation, payload)


class ActivityDeadlineTests(unittest.TestCase):
    def test_optional_and_legacy_datetime_settings_round_trip(self):
        self.assertEqual(Scheduler().real_deadline, '')
        self.assertEqual(Scheduler(real_deadline='  ').real_deadline, '')
        for value in ('2026-09-26 23:59:59', '2026-09-26T23:59:59+08:00', '2026-09-26T15:59:59Z'):
            with self.subTest(value=value):
                setting = Scheduler(real_deadline=value)
                self.assertEqual(setting.model_dump()['real_deadline'], value)
                self.assertIsNotNone(deadline_timestamp(value))
        self.assertEqual(deadline_timestamp('2026-09-26T23:59:59+08:00'),
                         deadline_timestamp('2026-09-26T15:59:59Z'))
        with self.assertRaises(ValidationError):
            Scheduler(real_deadline='not-a-date')

    def test_all_modes_exclude_expired_and_still_dispatch_other_tasks(self):
        for mode in ('legacy', 'eevdf_shadow', 'eevdf'):
            with self.subTest(mode=mode):
                coordinator = Coordinator(clock=lambda: 100, monotonic=lambda: 100)
                coordinator.set_policy({'mode': mode})
                result = coordinator.acquire({'profile_id': 'p', 'owner_id': 'w', 'device_id': 'd',
                    'candidates': [{'task': 'Expired', 'deadline': 100}, {'task': 'Normal'}],
                    'legacy_order': ['Expired', 'Normal']})
                self.assertEqual(result['lease']['task'], 'Normal')
                self.assertEqual(result['decision']['blocked']['p:Expired'], 'deadline_expired')

    def test_only_expired_tasks_wait_without_issuing_a_lease(self):
        coordinator = Coordinator(clock=lambda: 100)
        result = coordinator.acquire({'profile_id': 'p', 'owner_id': 'w', 'device_id': 'd',
            'candidates': [{'task': 'Expired', 'deadline': 99}], 'legacy_order': ['Expired']})
        self.assertEqual(result['status'], 'waiting')
        self.assertEqual(result['decision']['reason'], 'deadline_expired')
        self.assertIsNone(coordinator.devices['d']['lease'])

    def test_upcoming_deadline_still_gets_fair_urgency_until_exact_cutoff(self):
        engine, policy = FairScheduler(), Policy(mode='eevdf')
        candidate = Candidate('a', 'p', 'Activity', quantum=10, deadline=110)
        self.assertEqual(engine.choose([candidate], 100, 100, policy)['reason'], 'real_deadline')
        self.assertIsNone(engine.choose([candidate], 110, 110, policy))
        self.assertFalse(Candidate('b', 'p', 'Activity', deadline=100).runnable(100))
        self.assertTrue(Candidate('b', 'p', 'Activity').runnable(100))

    def runtime(self, now, *, cooperative=True):
        store = Checkpoints()
        coordinator = Coordinator(store, clock=lambda: now[0], monotonic=lambda: now[0])
        bridge = Bridge(coordinator)
        execution = ExecutionRuntime(bridge, bridge.profile_id, clock=lambda: now[0], monotonic=lambda: now[0])
        result = coordinator.acquire({'profile_id': bridge.profile_id, 'owner_id': bridge.owner_id,
            'device_id': bridge.device_id, 'candidates': [{'task': 'Orochi', 'cooperative': cooperative,
            'deadline': 110}], 'legacy_order': ['Orochi']})
        execution.lease, execution.lease_started = result['lease'], now[0]
        return execution, coordinator, bridge

    def test_cutoff_crossed_between_admission_and_begin_has_no_run(self):
        now = [100]
        execution, coordinator, bridge = self.runtime(now)
        now[0] = 110
        with self.assertRaises(DeadlineExpired):
            execution.begin('Orochi', {'scheduler': {'real_deadline': '1970-01-01T00:01:50Z'}})
        self.assertIsNone(execution.active)
        self.assertIsNone(coordinator.devices[bridge.device_id]['lease'])
        self.assertFalse(any(e['type'] == 'run.started' for e in bridge.events))
        self.assertEqual(bridge.events[-1]['payload']['reason'], 'deadline_expired')

    def test_cutoff_at_safe_boundary_cancels_only_activity_and_does_not_count_success(self):
        now = [100]
        execution, coordinator, bridge = self.runtime(now)
        execution.begin('Orochi', {'scheduler': {'real_deadline': '1970-01-01T00:01:50Z'}}, cooperative=True)
        self.assertIsNone(execution.boundary_request())
        now[0] = 110
        request = execution.boundary_request()
        self.assertEqual(request, 'deadline_expired')
        with self.assertRaises(SafeBoundaryExit):
            execution.safe_boundary(2, True, request)
        outcome = classify_outcome(True, {'status': request})
        self.assertEqual(outcome, 'cancelled')
        execution.finish(outcome, request)
        self.assertFalse(coordinator.controls.get(bridge.profile_id))
        final = next(e for e in bridge.events if e['type'] == 'run.finished')
        self.assertEqual(final['payload']['outcome'], 'cancelled')
        self.assertEqual(final['payload']['reason'], 'deadline_expired')
        result = coordinator.acquire({'profile_id': bridge.profile_id, 'owner_id': bridge.owner_id,
            'device_id': bridge.device_id, 'candidates': [{'task': 'Other'}]})
        self.assertEqual(result['status'], 'acquired')

    def test_overview_and_launch_plan_show_expired_waiting_and_exclude_it(self):
        adapter = ManagerAdapter()
        adapter.names = lambda: ['p']
        adapter.raw = lambda name: {
            'orochi': {'scheduler': {'enable': True, 'next_run': '2000-01-01 00:00:00',
                                    'real_deadline': '2000-01-02T00:00:00+08:00'}},
            'area_boss': {'scheduler': {'enable': True, 'next_run': '2000-01-01 00:00:00'}}}
        service = SimpleNamespace(runs={}, owners={'w': {'profile_id': 'p', 'alive': True}},
                                  profile_id=lambda n: n, profile_state={})
        queues = adapter.queues(service)
        self.assertEqual([item['task_id'] for item in queues['ready']], ['area_boss'])
        self.assertEqual(queues['waiting'][0]['reason'], 'deadline_expired')
        self.assertEqual(adapter.planned_tasks('p'), ['AreaBoss'])

    def test_legacy_worker_queue_keeps_expired_out_of_pending(self):
        source = ast.parse(Path('module/config/config.py').read_text(encoding='utf-8-sig'))
        node = next(n for n in ast.walk(source) if isinstance(n, ast.FunctionDef) and n.name == 'update_scheduler')
        class Function:
            def __init__(self, key, value):
                self.command, self.enable = key, True
                self.next_run = datetime(2000, 1, 1)
                self.scheduling = {'deadline': value}
        now = datetime.now().timestamp()
        config = SimpleNamespace(model=SimpleNamespace(dict=lambda: {'expired': now - 1, 'live': None},
            script=SimpleNamespace(optimization=SimpleNamespace(schedule_rule=None)), running_task=''))
        scope = {'Function': Function, 'datetime': datetime, 'is_expired': is_expired,
                 'TaskScheduler': SimpleNamespace(schedule=lambda **kw: kw['pending'])}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), '<update>', 'exec'), scope)
        scope['update_scheduler'](config)
        self.assertEqual([t.command for t in config.pending_task], ['live'])
        self.assertEqual([t.command for t in config.expired_task], ['expired'])


if __name__ == '__main__':
    unittest.main()
