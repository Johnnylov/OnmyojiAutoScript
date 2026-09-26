"""Real file-store projections with simulated lifecycle events, never gameplay."""
from datetime import datetime, timedelta
from tempfile import TemporaryDirectory
import unittest
import uuid
from types import SimpleNamespace

from module.observability import EventStore
from module.server.solana_adapter import ManagerAdapter
from module.server.solana_runtime import RuntimeService
from module.scheduling.runtime import ExecutionRuntime


class Adapter:
    def names(self):
        return ['a', 'b']

    def device(self, name):
        return {'device_id': name}

    def planned_tasks(self, name):
        return ['Orochi', 'AbyssShadows', 'Guild'] if name == 'a' else ['Guild']


class MetricsProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.store = EventStore(self.temp.name)
        self.service = RuntimeService(self.store, Adapter())
        self.identity = self.service.register_process('a', 'owner-a')
        self.profile = self.identity['profile_id']

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def emit(self, kind, run='r1', task='Orochi', payload=None, owner='owner-a', **extra):
        return self.service.dispatch(owner, 'event.append', {
            'event_id': str(uuid.uuid4()), 'type': kind, 'run_id': run,
            'task_id': task, 'segment_id': 'segment-1', 'payload': payload or {}, **extra})

    def cycle(self, profile=None):
        return next(c for c in self.service.overview()['execution_cycles']
                    if c['profile_id'] == (profile or self.profile))

    def current_run(self):
        return next(r for r in self.service.overview()['current_runs'] if r['run_id'] == 'r1')

    def test_session_counts_distinct_successes_not_attempts(self):
        self.assertEqual((self.cycle()['completed_tasks'], self.cycle()['total_tasks']), (0, 3))
        self.emit('run.started')
        self.emit('run.finished', payload={'outcome': 'failed'})
        self.assertEqual(self.cycle()['completed_tasks'], 0)
        self.assertEqual(self.cycle()['failed_tasks'], 1)
        for run in ['retry', 'repeat']:
            self.emit('run.started', run=run)
            self.emit('run.finished', run=run, payload={'outcome': 'succeeded'})
        self.assertEqual(self.cycle()['completed_tasks'], 1)
        self.assertEqual(self.cycle()['failed_tasks'], 0)
        self.emit('run.started', run='new', task='DailyTrifles')
        self.assertEqual(self.cycle()['total_tasks'], 4)

    def test_profiles_pause_and_service_replay_keep_their_own_cycle(self):
        other = self.service.register_process('b', 'owner-b')['profile_id']
        self.emit('run.started')
        self.emit('run.paused')
        self.emit('run.resumed')
        self.emit('run.finished', payload={'outcome': 'succeeded'})
        before = self.cycle()
        self.assertEqual(self.cycle(other)['completed_tasks'], 0)
        self.service.close()
        self.store = EventStore(self.temp.name)
        self.service = RuntimeService(self.store, Adapter())
        self.assertEqual(self.cycle(), before)
        self.assertEqual(self.cycle(other)['total_tasks'], 1)

    def test_new_executor_cycle_does_not_count_old_uncertain_run_resolution(self):
        self.emit('run.started')
        self.service.process_exited('owner-a', -1)
        self.service.register_process('a', 'new-owner')
        self.service.emit({'type': 'run.finished', 'run_id': 'r1', 'profile_id': self.profile,
                           'task_id': 'Orochi', 'payload': {'outcome': 'interrupted'}})
        self.assertEqual(self.cycle()['cycle_id'], 'new-owner')
        self.assertEqual(self.cycle()['completed_tasks'], 0)
        self.assertEqual(self.cycle()['failed_tasks'], 0)

    def test_replayed_finish_does_not_double_count_even_after_run_checkpoint(self):
        self.emit('run.started')
        self.emit('run.finished', payload={'outcome': 'succeeded'})
        before = self.cycle()
        events = self.store.query(filters={'type': 'run.finished'}, limit=20)['items']
        event = next(e for e in events if e.get('run_id') == 'r1')
        self.service._reduce(event)
        self.assertEqual(self.cycle(), before)

    def test_business_deferred_task_is_not_claimed_completed(self):
        self.emit('scheduler.skipped', run=None, task='Guild')
        self.assertEqual((self.cycle()['completed_tasks'], self.cycle()['total_tasks']), (0, 2))
        self.assertEqual(self.cycle()['deferred_tasks'], 1)
        self.emit('run.started', task='Guild')
        self.assertEqual(self.cycle()['total_tasks'], 3)
        self.assertEqual(self.cycle()['deferred_tasks'], 0)

    def test_progress_without_a_preview_and_stale_preview_are_truthful(self):
        self.emit('run.started')
        metrics = {'current_count': 18, 'target_count': 50, 'count_unit': '次挑战',
                   'count_supported': True, 'battle_count': 17, 'battle_supported': True,
                   'progress_phase': '副将', 'execution_seconds': 100,
                   'observed_at': '2026-09-26T01:00:05+00:00'}
        self.emit('run.progress_observed', payload=metrics)
        self.assertEqual(self.current_run()['current_count'], 18)
        self.assertEqual(self.current_run()['battle_count'], 17)
        self.assertEqual(self.current_run()['execution_seconds'], 100)
        self.service.record_preview('owner-a', {'mime_type': 'image/jpeg', 'image_base64': '',
            'occurred_at': '2026-09-26T01:00:04+00:00',
            'progress': {**metrics, 'run_id': 'r1', 'segment_id': 'segment-1',
                         'current_count': 0, 'observed_at': '2026-09-26T01:00:04+00:00'}})
        self.assertEqual(self.current_run()['current_count'], 18)
        self.emit('run.paused', payload=metrics)
        self.assertEqual(self.current_run()['current_count'], 18)
        self.assertEqual(self.current_run()['battle_count'], 17)

    def test_live_elapsed_is_not_added_again_at_segment_end(self):
        self.emit('run.started')
        metrics = {'current_count': 1, 'target_count': 3, 'count_supported': True,
                   'execution_seconds': 10, 'observed_at': '2026-09-26T01:00:10+00:00'}
        self.emit('run.progress_observed', payload=metrics)
        self.emit('segment.finished', payload={**metrics, 'duration_seconds': 10})
        self.assertEqual(self.current_run()['execution_seconds'], 10)
        self.emit('run.resumed', segment_id='segment-2')
        self.emit('run.progress_observed', segment_id='segment-2', payload={**metrics, 'execution_seconds': 15})
        self.emit('segment.finished', segment_id='segment-2', payload={**metrics, 'execution_seconds': 15, 'duration_seconds': 5})
        self.assertEqual(self.current_run()['execution_seconds'], 15)
        self.assertEqual(self.current_run()['accounted_execution_seconds'], 15)

    def test_clock_adjustment_does_not_hide_newer_durable_metrics(self):
        self.emit('run.started')
        self.emit('run.progress_observed', payload={'current_count': 1, 'metric_revision': 1,
                  'observed_at': '2026-09-26T02:00:00+00:00'})
        self.emit('run.progress_observed', payload={'current_count': 2, 'metric_revision': 2,
                  'observed_at': '2026-09-26T01:00:00+00:00'})
        self.service.record_preview('owner-a', {'mime_type': 'image/jpeg', 'image_base64': '',
            'occurred_at': '2026-09-26T02:00:01+00:00', 'progress': {'run_id': 'r1',
             'segment_id': 'segment-1', 'current_count': 1, 'metric_revision': 1,
             'observed_at': '2026-09-26T02:00:01+00:00'}})
        self.assertEqual(self.current_run()['current_count'], 2)

    def test_worker_settlements_reach_all_three_overview_metrics(self):
        self.service.process_exited('owner-a', 0)
        self.service.adapter.planned_tasks = lambda _: ['Orochi'] + [f'Task{i}' for i in range(1, 12)]
        identity = self.service.register_process('a', 'metrics-worker')
        for i in range(1, 4):
            self.emit('run.started', run=f'done-{i}', task=f'Task{i}', owner='metrics-worker')
            self.emit('run.finished', run=f'done-{i}', task=f'Task{i}', owner='metrics-worker',
                      payload={'outcome': 'succeeded'})
        bridge = SimpleNamespace(**identity)
        bridge.call = lambda operation, payload: self.service.dispatch('metrics-worker', operation, payload)
        execution = ExecutionRuntime(bridge, 'a')
        execution.lease = bridge.call('scheduling.acquire', {
            'candidates': [{'task': 'Orochi', 'cooperative': True}], 'legacy_order': ['Orochi']})['lease']
        execution.lease_started = execution.monotonic()
        execution.begin('Orochi', {'orochi_config': {'limit_count': 50, 'limit_time': '00:35:00'}}, True)
        for i in range(18):
            token = execution.begin_battle()
            execution.finish_battle(token, 'settled')
            execution.finish_battle(token, 'settled')  # duplicated observation
            execution.record_progress(i + 1)
        overview = self.service.overview()
        run = next(r for r in overview['current_runs'] if r['run_id'] == execution.active['run_id'])
        self.assertEqual((run['current_count'], run['target_count'], run['battle_count']), (18, 50, 18))
        self.assertEqual((self.cycle()['completed_tasks'], self.cycle()['total_tasks']), (3, 12))
        self.assertTrue(run['count_supported'])
        self.assertTrue(run['battle_supported'])


class PlanSelectionTests(unittest.TestCase):
    def test_initial_plan_contains_only_enabled_due_business_eligible_tasks(self):
        now = datetime(2026, 9, 26, 12)
        def task(command, due=True, enable=True):
            return {'scheduler': {'command': command, 'enable': enable,
                    'next_run': (now + timedelta(hours=-1 if due else 1)).isoformat()}}
        adapter = ManagerAdapter()
        adapter.raw = lambda _: {'orochi': task('Orochi'), 'guild': task('Guild', due=False),
            'disabled': task('DailyTrifles', enable=False), 'restart': task('Restart'),
            'demon': task('DemonEncounter'), 'alias': task('Orochi')}
        self.assertEqual(adapter.planned_tasks('profile', now), ['Orochi'])


if __name__ == '__main__':
    unittest.main()
