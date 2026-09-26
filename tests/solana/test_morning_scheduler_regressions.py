"""Replay the 06:15/09:30 preparation deadlock without opening a game."""
import ast
import copy
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from types import MethodType, SimpleNamespace
import threading
import unittest

from module.scheduling.core import Candidate, FairScheduler, Policy
from module.scheduling.coordinator import Coordinator
from module.scheduling.runtime import ExecutionRuntime, ReconciliationRequired, DispatchStopped


def method(path, name, scope):
    tree = ast.parse(Path(path).read_text(encoding='utf-8-sig'))
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), path, 'exec'), scope)
    return scope[name]


class Decisions(str, Enum):
    READY = 'ready'
    RESCHEDULE = 'reschedule'
    FAILED = 'failed'


class Checkpoints:
    def __init__(self):
        self.data = {}

    def get(self, kind, key):
        return copy.deepcopy(self.data.get((kind, key)))

    def save(self, kind, key, data, **kwargs):
        self.data[kind, key] = copy.deepcopy(data)


class Bridge:
    profile_id, owner_id, device_id = 'morning', 'worker', 'morning-regression'

    def __init__(self, coordinator):
        self.coordinator, self.events = coordinator, []

    def call(self, operation, payload):
        if operation == 'event.append':
            self.events.append(copy.deepcopy(payload))
            return {'seq': len(self.events)}
        return self.coordinator.dispatch(operation, payload)


class MorningSchedulerRegressions(unittest.TestCase):
    def test_successful_login_returns_to_normal_queue_without_failure_recovery(self):
        now = [0.0]
        coordinator = Coordinator(Checkpoints(), clock=lambda: 2000000000, monotonic=lambda: now[0])
        coordinator.set_policy({'mode': 'eevdf'})
        bridge = Bridge(coordinator)
        execution = ExecutionRuntime(bridge, 'morning', monotonic=lambda: now[0])
        config = SimpleNamespace(
            script=SimpleNamespace(device=SimpleNamespace(model_dump=lambda **kw: {'device_id': bridge.device_id})),
            pending_task=[SimpleNamespace(command='AreaBoss', next_run=datetime(2000, 1, 1))], waiting_task=[])
        self.assertEqual(execution.acquire(config)['status'], 'acquired')
        execution.begin('AreaBoss', {'target': 3})
        run_id = execution.active['run_id']
        checks = iter([False, True])
        owner = SimpleNamespace(run=lambda command: command == 'Restart')
        runtime = SimpleNamespace(script=owner, preparation_recovered=False,
            device=SimpleNamespace(app_is_running=lambda: next(checks)),
            _wait_for_server_update_window=lambda reason: Decisions.READY,
            _ensure_emulator_running=lambda reason: None,
            _consume_server_update_delay_outcome=lambda reason: None)
        scope = {'ScriptRuntimeDecision': Decisions, 'logger': SimpleNamespace(info=lambda *a: None,
            warning=lambda *a: None), 'RequestHumanTakeover': RuntimeError, 'GameNotRunningError': RuntimeError}
        runtime._run_restart_recovery = MethodType(method('module/script/runtime_controller.py',
            '_run_restart_recovery', dict(scope)), runtime)
        prepare = method('module/script/runtime_controller.py', 'prepare_task_execution', dict(scope))
        self.assertEqual(prepare(runtime, 'AreaBoss'), Decisions.RESCHEDULE)
        self.assertTrue(runtime.preparation_recovered)
        now[0] = 26
        finish = method('script.py', '_finish_preparation_reschedule', {})
        finish(SimpleNamespace(solana_execution=execution, runtime=runtime))
        self.assertEqual(execution.recovery_tasks, set())
        self.assertFalse(any(event['type'] == 'recovery.requested' for event in bridge.events))
        # Match the historical 204-second AreaBoss estimate. It can still run
        # normally even though the emergency budget is only 120 seconds.
        coordinator.devices[bridge.device_id]['engine'].entities['morning:AreaBoss']['estimate_seconds'] = 204.65
        try:
            self.assertEqual(execution.acquire(config)['status'], 'acquired')
            execution.begin('AreaBoss', {'target': 3})
            self.assertEqual(execution.active['run_id'], run_id)
            runtime.device.app_is_running = lambda: True
            self.assertEqual(prepare(runtime, 'AreaBoss'), Decisions.READY)
            self.assertFalse(runtime.preparation_recovered, 'success marker cannot leak into the next prepare')
            execution.finish('succeeded')
        finally:
            execution.release('interrupted')

    def test_unverified_reschedule_keeps_recovery_fence(self):
        calls = []
        execution = SimpleNamespace(active={'task': 'Orochi'}, recovery_tasks={'Orochi'},
            finish=lambda *args: calls.append(args))
        finish = method('script.py', '_finish_preparation_reschedule', {})
        finish(SimpleNamespace(solana_execution=execution,
            runtime=SimpleNamespace(preparation_recovered=False)))
        self.assertEqual(calls, [('recovery_requested', 'runtime_preparation')])
        self.assertEqual(execution.recovery_tasks, {'Orochi'})

    def test_failed_runtime_preparation_uses_bounded_recovery_path(self):
        runtime = SimpleNamespace(script=SimpleNamespace(run=lambda command: False),
                                  preparation_recovered=False)
        scope = {'ScriptRuntimeDecision': Decisions,
                 'logger': SimpleNamespace(info=lambda *a: None, warning=lambda *a: None)}
        recover = method('module/script/runtime_controller.py', '_run_restart_recovery', scope)
        self.assertEqual(recover(runtime, 'prepare'), Decisions.FAILED)
        self.assertFalse(runtime.preparation_recovered)
        calls = []
        execution = SimpleNamespace(active=None, lease=None, recovery_tasks={'AreaBoss'})
        def begin(*args, **kwargs):
            execution.active = {'task': 'AreaBoss', 'state': 'running'}
        def finish(*args):
            calls.append(args)
            execution.active = None
        execution.begin, execution.finish = begin, finish
        selected = []
        def get_next():
            if selected:
                raise DispatchStopped(0)
            selected.append('AreaBoss')
            return 'AreaBoss'
        runtime.prepare_task_execution = lambda task: Decisions.FAILED
        script = SimpleNamespace(solana_execution=execution, runtime=runtime,
            config_name='regression', is_first_task=False, get_next_task=get_next,
            _defer_business_precondition=lambda task: False,
            anti_ban_guard=SimpleNamespace(reset=lambda: None),
            config=SimpleNamespace(model=SimpleNamespace(area_boss={}),
                script=SimpleNamespace(device=SimpleNamespace(run_background_only=True,
                    model_dump=lambda **kw: {'serial': 'test'}))))
        script._finish_preparation_reschedule = MethodType(method('script.py', '_finish_preparation_reschedule', {}), script)
        log = SimpleNamespace(set_file_logger=lambda *a, **kw: None, info=lambda *a: None,
                              warning=lambda *a: None, error=lambda *a: None)
        loop = method('script.py', 'loop', {'ScriptRuntimeDecision': Decisions, 'logger': log,
            '_log_switch_lock': threading.Lock(), 'date': date, 'IS_WINDOWS': False,
            'convert_to_underscore': lambda name: 'area_boss', 'cooperative_task': lambda *a: False,
            'del_cached_property': lambda *a: None})
        with self.assertRaises(DispatchStopped):
            loop(script)
        self.assertEqual(calls, [('recovery_requested', 'runtime_preparation')])

    def test_oversized_recovery_uses_one_empty_window_and_charges_actual_time(self):
        policy, engine = Policy(mode='eevdf'), FairScheduler()
        candidate = Candidate('p:Orochi', 'p', 'Orochi', quantum=617.65, recovery=True)
        first = engine.choose([candidate], 0, 0, policy)
        self.assertEqual((first['key'], first['reason']), ('p:Orochi', 'recovery'))
        engine.account(first['key'], 617.65, 617.65, 617.65, first['reason'], True, policy)
        backoff = engine.choose([candidate], 620, 620, policy)
        self.assertIsNone(backoff['key'])
        self.assertEqual(backoff['blocked'][candidate.key], 'recovery_backoff')
        budget = engine.choose([candidate], 700, 700, policy)
        self.assertIsNone(budget['key'])
        self.assertEqual(budget['blocked'][candidate.key], 'urgent_budget_exhausted')
        next_window = engine.choose([candidate], 1218, 1218, policy)
        self.assertEqual(next_window['key'], candidate.key)
        self.assertEqual(engine.overrides, [])

    def test_waiting_limit_does_not_bypass_recovery_attempt_limit(self):
        policy = Policy(mode='eevdf', max_wait_seconds=5, urgent_budget_seconds=1000,
                        recovery_backoff_seconds=1, recovery_limit=1)
        engine = FairScheduler()
        item = Candidate('p:A', 'p', 'A', quantum=10, recovery=True, ready_since=0)
        result = engine.choose([item], 10, 10, policy)
        self.assertEqual(result['reason'], 'recovery')
        engine.account(item.key, 10, 20, 20, result['reason'], True, policy)
        blocked = engine.choose([item], 100, 100, policy)
        self.assertIsNone(blocked['key'])
        self.assertEqual(blocked['blocked'][item.key], 'recovery_budget_exhausted')

    def test_zero_recovery_budget_is_an_explicit_non_retryable_condition(self):
        item = Candidate('p:AreaBoss', 'p', 'AreaBoss', quantum=200, recovery=True)
        blocked = FairScheduler().choose([item], 0, 0, Policy(mode='eevdf', urgent_budget_seconds=0))
        self.assertEqual(blocked['blocked'][item.key], 'recovery_budget_disabled')
        wait = method('script.py', '_wait_for_dispatch', {'ReconciliationRequired': ReconciliationRequired})
        with self.assertRaisesRegex(ReconciliationRequired, '预算为零'):
            wait(SimpleNamespace(solana_execution=SimpleNamespace(profile_id='p')), {
                'status': 'waiting', 'decision': blocked})

    def test_ui_snake_case_weights_apply_to_worker_task_names_and_profile_scope(self):
        policy = Policy(weights={'area_boss': 3, 'other:area_boss': 8, 'p:area_boss': 5})
        engine = FairScheduler()
        candidates = [Candidate('p:AreaBoss', 'p', 'AreaBoss'), Candidate('q:AreaBoss', 'q', 'AreaBoss')]
        engine.sync(candidates, 0, 0, policy)
        self.assertEqual(engine.entities['p:AreaBoss']['weight'], 5)
        self.assertEqual(engine.entities['q:AreaBoss']['weight'], 3)

    def team_scheduler(self, request):
        coordinator = Coordinator(Checkpoints(), clock=lambda: 2000000000)
        coordinator.set_policy({'mode': 'eevdf'})
        execution = ExecutionRuntime(Bridge(coordinator), 'morning')
        ordinary = SimpleNamespace(command='AreaBoss', next_run=datetime(2000, 1, 1),
                                   scheduling={'estimated_batch_seconds': 120})
        team = SimpleNamespace(command='TrueOrochi', next_run=datetime(2000, 1, 1),
                              scheduling={'estimated_batch_seconds': 10})
        config = SimpleNamespace(pending_task=[ordinary, team], waiting_task=[],
            get_next=lambda: ordinary, script=SimpleNamespace(anti_ban=None,
                device=SimpleNamespace(model_dump=lambda **kw: {'device_id': execution.device_id})))
        script = SimpleNamespace(config=config, solana_execution=execution, state_queue=None,
            team_sync=SimpleNamespace(pending_task=lambda: None, request=request),
            anti_ban_guard=SimpleNamespace(wake_time=lambda *a: None), _hoard_next_task=lambda task, now: task)
        return script, coordinator

    def test_team_request_is_for_actual_fair_selection_not_legacy_queue_head(self):
        requested = []
        script, coordinator = self.team_scheduler(lambda task: requested.append(task) or task)
        get_next = method('script.py', 'get_next_task', {'datetime': datetime})
        try:
            self.assertEqual(get_next(script), 'TrueOrochi')
            self.assertEqual(requested, ['TrueOrochi'])
        finally:
            script.solana_execution.release('interrupted')

    def test_rejected_team_request_releases_lease_before_error_propagates(self):
        def reject(task):
            raise ValueError('peer unavailable')
        script, coordinator = self.team_scheduler(reject)
        get_next = method('script.py', 'get_next_task', {'datetime': datetime})
        with self.assertRaisesRegex(ValueError, 'peer unavailable'):
            get_next(script)
        self.assertIsNone(script.solana_execution.lease)
        self.assertIsNone(coordinator.snapshot()[script.solana_execution.device_id]['lease'])

    def test_pending_team_rendezvous_cannot_be_replaced_by_other_fair_task(self):
        script, coordinator = self.team_scheduler(lambda task: task)
        # Make an unrelated task more attractive to EEVDF than the pending team.
        coordinator.set_policy({'weights': {'area_boss': 100}})
        execution = script.solana_execution
        try:
            result = execution.acquire(script.config, preferred_task='TrueOrochi')
            self.assertEqual(result['lease']['task'], 'TrueOrochi')
            record = coordinator.devices[execution.device_id]['profiles'][execution.profile_id]
            area = next(task for task in record['candidates'] if task.task == 'AreaBoss')
            self.assertEqual(area.blocked_reason, 'team_rendezvous_pending')
        finally:
            execution.release('interrupted')

    def test_unchanged_waits_back_off_but_safe_stop_remains_immediate(self):
        delays, logs = [], []
        execution = SimpleNamespace(profile_id='p', control_status=lambda: {})
        def stopped():
            raise DispatchStopped(0)
        execution.stop_at_boundary = stopped
        script = SimpleNamespace(solana_execution=execution)
        scope = {'time': SimpleNamespace(monotonic=lambda: 100, sleep=lambda interval: delays.append(interval)),
                 'logger': SimpleNamespace(info=lambda message: logs.append(message)),
                 'ReconciliationRequired': ReconciliationRequired}
        wait = method('script.py', '_wait_for_dispatch', scope)
        result = {'status': 'waiting_resource', 'reason': 'device_owned'}
        totals = []
        for _ in range(5):
            before = sum(delays)
            wait(script, result)
            totals.append(sum(delays) - before)
        self.assertEqual(totals, [.5, 1, 2, 4, 5])
        self.assertEqual(len(logs), 1)
        execution.control_status = lambda: {'control': 'safe_stop'}
        before = len(delays)
        with self.assertRaises(DispatchStopped):
            wait(script, result)
        self.assertEqual(len(delays), before)

    def test_exhausted_local_recovery_stops_instead_of_polling_forever(self):
        wait = method('script.py', '_wait_for_dispatch', {'ReconciliationRequired': ReconciliationRequired})
        with self.assertRaises(ReconciliationRequired):
            wait(SimpleNamespace(solana_execution=SimpleNamespace(profile_id='p')), {
                'status': 'waiting', 'decision': {'reason': 'recovery_blocked',
                    'blocked': {'p:AreaBoss': 'recovery_budget_exhausted'}}})


if __name__ == '__main__':
    unittest.main()
