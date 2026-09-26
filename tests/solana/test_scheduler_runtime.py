import ast
import copy
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import unittest

from module.scheduling.coordinator import Coordinator
from module.scheduling.runtime import (ExecutionRuntime, SafeBoundaryExit, ReconciliationRequired,
                                       DispatchStopped, classify_outcome)


class MemoryCheckpoints:
    def __init__(self):
        self.data = {}

    def get(self, kind, key):
        return copy.deepcopy(self.data.get((kind, key)))

    def save(self, kind, key, data, event_seq=None):
        self.data[kind, key] = copy.deepcopy(data)

    def list(self, kind):
        return [{'data': copy.deepcopy(data)} for (category, _), data in self.data.items() if category == kind]


class Bridge:
    profile_id, owner_id, device_id = 'profile', 'worker', 'device'

    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.events = []
        self.fail_events = False

    def call(self, op, payload):
        if op == 'event.append':
            if self.fail_events:
                raise OSError('storage unavailable')
            event = copy.deepcopy(payload)
            event['seq'] = len(self.events) + 1
            self.events.append(event)
            return event
        return self.coordinator.dispatch(op, payload)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.checkpoints = MemoryCheckpoints()
        self.coordinator = Coordinator(self.checkpoints, clock=lambda: 1, monotonic=lambda: self.now)
        self.coordinator.set_policy({'mode': 'eevdf'})
        self.bridge = Bridge(self.coordinator)
        self.runtime = ExecutionRuntime(self.bridge, 'profile', monotonic=lambda: self.now)

    def acquire(self):
        result = self.coordinator.acquire({'profile_id': 'profile', 'owner_id': 'worker', 'device_id': 'device',
            'candidates': [{'task': 'Orochi', 'cooperative': True}], 'legacy_order': ['Orochi']})
        self.runtime.lease = result['lease']
        self.runtime.lease_started = self.now
        self.runtime.mode = result['mode']

    def test_segments_share_run_and_pause_is_not_success(self):
        self.acquire()
        self.runtime.begin('Orochi', {'count': 5}, cooperative=True)
        run = self.runtime.active['run_id']
        self.now = 120
        with self.assertRaises(SafeBoundaryExit):
            self.runtime.safe_boundary(3, True, 'yielded')
        self.runtime.finish('yielded')
        self.acquire()
        self.runtime.begin('Orochi', {'count': 5}, cooperative=True)
        self.assertEqual(self.runtime.active['run_id'], run)
        self.assertEqual(self.runtime.count, 3)
        self.now = 200
        self.runtime.finish('succeeded')
        kinds = [e['type'] for e in self.bridge.events]
        self.assertEqual(kinds.count('run.started'), 1)
        self.assertEqual(kinds.count('segment.started'), 2)
        self.assertEqual(kinds.count('run.finished'), 1)
        final = next(e for e in self.bridge.events if e['type'] == 'run.finished')
        self.assertEqual(final['payload']['execution_seconds'], 200)

    def test_unverified_boundary_and_inflight_crash_are_not_resumed(self):
        self.acquire()
        self.runtime.begin('Orochi', {}, cooperative=True)
        self.runtime.before_battle(2)
        with self.assertRaises(ReconciliationRequired):
            self.runtime.safe_boundary(3, False, 'paused')
        self.runtime.release('interrupted')
        other = ExecutionRuntime(self.bridge, 'profile', monotonic=lambda: self.now)
        self.acquire()
        other.lease, other.lease_started = self.runtime.lease, self.now
        with self.assertRaises(ReconciliationRequired):
            other.begin('Orochi', {}, cooperative=True)
        saved = self.checkpoints.get('runs', other._key('Orochi'))
        self.assertEqual(saved['state'], 'needs_reconciliation')
        self.assertTrue(saved['in_flight'])

    def test_config_change_requires_explicit_reconciliation(self):
        self.acquire()
        self.runtime.begin('Orochi', {'target': 5}, cooperative=True)
        with self.assertRaises(SafeBoundaryExit):
            self.runtime.safe_boundary(2, True, 'yielded')
        self.runtime.finish('yielded')
        self.acquire()
        with self.assertRaises(ReconciliationRequired):
            self.runtime.begin('Orochi', {'target': 50}, cooperative=True)

    def test_false_success_legacy_signals(self):
        for status in ('retry_scheduled', 'recovery_requested', 'server_update_delayed'):
            self.assertEqual(classify_outcome(True, {'status': status}), 'recovery_requested')
        self.assertEqual(classify_outcome(True, {'status': 'team_preempted'}), 'interrupted')
        self.assertEqual(classify_outcome(True, business_success=False), 'failed')

    def test_safe_stop_paused_run_emits_one_cancel_without_second_segment(self):
        self.acquire()
        self.runtime.begin('Orochi', {}, cooperative=True)
        self.now = 20
        with self.assertRaises(SafeBoundaryExit):
            self.runtime.safe_boundary(1, True, 'paused')
        self.runtime.finish('paused')
        with self.assertRaises(DispatchStopped):
            self.runtime.stop_at_boundary()
        terminal = [e for e in self.bridge.events if e['type'] == 'run.finished']
        self.assertEqual(len(terminal), 1)
        self.assertEqual(terminal[0]['payload']['outcome'], 'cancelled')
        self.assertEqual(len([e for e in self.bridge.events if e['type'] == 'segment.finished']), 1)

    def test_storage_failure_does_not_prevent_safe_stop(self):
        self.acquire()
        self.runtime.begin('Orochi', {}, cooperative=True)
        self.bridge.fail_events = True
        with self.assertRaises(DispatchStopped):
            self.runtime.stop_at_boundary()
        self.assertTrue(self.runtime.storage_failed)

    def test_orochi_real_solo_loop_yields_then_keeps_count(self):
        # Execute the actual task method against a deterministic fake game; no
        # imports of ADB, OCR, image servers or game launchers are needed.
        source = Path('tasks/Orochi/script_task.py').read_text(encoding='utf-8-sig')
        cls = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef))
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'run_alone')
        globals_ = {'datetime': datetime, 'timedelta': timedelta, 'page_orochi': 'orochi', 'page_main': 'main',
                    'logger': SimpleNamespace(info=lambda *args: None)}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), '<Orochi>', 'exec'), globals_)
        self.acquire()
        self.runtime.begin('Orochi', {'limit': 5}, cooperative=True)
        task = SimpleNamespace(current_count=0, limit_count=5, limit_time=timedelta(minutes=30),
            start_time=datetime.now(), page='orochi', in_battle=False,
            I_OROCHI_FIRE='fire', I_PET_PRESENT='pet', C_RANDOM_RIGHT='random',
            I_OROCHI_LOCK='lock', I_OROCHI_UNLOCK='unlock')
        task.config = SimpleNamespace(solana_execution=self.runtime, orochi=SimpleNamespace(
            orochi_config=SimpleNamespace(layer=11, soul_buff_enable=False),
            general_battle_config=SimpleNamespace(lock_team_enable=False)))
        task.navigator = SimpleNamespace(resolve_page=lambda page: page)
        task.goto_page = lambda page: setattr(task, 'page', page)
        task.screenshot = lambda: None
        task.check_layer = task.check_lock = lambda *a, **kw: None
        task.appear = lambda image: image == 'fire' and not task.in_battle
        task.match_page_once = lambda page: task.page == page
        def click(image, **kwargs):
            if image == 'fire':
                task.in_battle = True
                return True
            return False
        task.appear_then_click = click
        task._orochi_battle_key = lambda: 'solo'
        def battle(**kwargs):
            self.now += 40
            task.current_count += 1
            task.in_battle = False
        task.run_general_battle = battle
        with self.assertRaises(SafeBoundaryExit) as result:
            globals_['run_alone'](task)
        self.assertEqual(result.exception.outcome, 'yielded')
        self.assertEqual(task.page, 'main')
        self.assertEqual(self.runtime.count, 3)
        self.runtime.finish('yielded')
        self.acquire()
        self.runtime.begin('Orochi', {'limit': 5}, cooperative=True)
        task.current_count = self.runtime.count
        globals_['run_alone'](task)
        self.assertEqual(task.current_count, 5)
        self.runtime.finish('succeeded')


if __name__ == '__main__':
    unittest.main()
