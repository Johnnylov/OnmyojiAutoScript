"""Offline coordination, scheduler, and safe interruption regressions.

Run: toolkit/python.exe -m unittest discover -s dev_tools -p test_local_team_sync.py -v
All profiles and coordination files in these tests live in temporary directories.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
import multiprocessing
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from dev_tools.test_activity_preparation_retry import source_methods
from module.exception import TaskEnd
from module.script.team_sync import (
    LocalTeamCoordinator, TeamPartnerFinished, TeamSyncUnavailable, TeamTaskSwitch,
)


def profile(name, partner, role, handoff):
    scheduler = dict(enable=True, priority=5, next_run='2026-01-01 00:00:00')
    return {
        'config_name': name,
        'global_game': {'local_team': dict(enable=True, partner_config=partner,
                                         sync_orochi=True, sync_bondling=True, ready_timeout=600)},
        'orochi': {'scheduler': scheduler.copy(), 'orochi_config': dict(user_status=role, layer='悲鸣')},
        'bondling_fairyland': {'scheduler': scheduler.copy(), 'bondling_config': dict(
            user_status=handoff, bondling_mode='只刷契灵(低级式盘)', bondling_stone_class='镇墓兽')},
    }


def process_request(root, name, command, barrier, release, results):
    coordinator = LocalTeamCoordinator(name, Path(root))
    try:
        coordinator.start()
        barrier.wait(timeout=10)
        selected = coordinator.request(command)
        with coordinator.state.transaction() as state:
            session = next(iter(state['sessions'].values()))
            results.put((selected, session['id']))
        release.wait(timeout=10)
    finally:
        coordinator.close()


class TeamFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'config').mkdir()
        self.configs = {'oas1': profile('oas1', 'oas2', 'leader', 'handoff1'),
                        'oas2': profile('oas2', 'oas1', 'member', 'handoff2')}
        self.now = [1000.0]
        self.a = LocalTeamCoordinator('oas1', self.root, clock=lambda: self.now[0])
        self.b = LocalTeamCoordinator('oas2', self.root, clock=lambda: self.now[0])
        for coordinator in (self.a, self.b):
            # Most tests drive liveness deterministically without a timer thread.
            coordinator.start = coordinator._heartbeat
        self.save_profiles()

    def save_profiles(self):
        for name, data in self.configs.items():
            (self.root / 'config' / f'{name}.json').write_text(json.dumps(data), encoding='utf-8')
        self.a._config_cache.clear()
        self.b._config_cache.clear()

    def session(self):
        with self.a.state.transaction() as state:
            return json.loads(json.dumps(next(iter(state['sessions'].values()))))

    def joined(self, task='Orochi'):
        self.a.start()
        self.b.start()
        self.a.request(task)
        self.a.begin(task)
        self.b.begin(task)

    def ready_both(self):
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(c.ready) for c in (self.a, self.b)]
            for future in futures:
                future.result(timeout=5)


class CoordinatorTests(TeamFixture):
    def test_independent_processes_agree_on_one_request(self):
        context = multiprocessing.get_context('spawn')
        barrier, release, results = context.Barrier(2), context.Event(), context.Queue()
        children = [context.Process(target=process_request, args=(
            str(self.root), name, command, barrier, release, results))
            for name, command in (('oas1', 'Orochi'), ('oas2', 'BondlingFairyland'))]
        try:
            for child in children:
                child.start()
            self.assertEqual(results.get(timeout=15), results.get(timeout=15))
        finally:
            release.set()
            for child in children:
                child.join(timeout=10)
                if child.is_alive():
                    child.terminate()
                    child.join(timeout=5)
            results.close()
            results.join_thread()
        self.assertTrue(all(child.exitcode == 0 for child in children))

    def test_member_can_wake_leader_without_rewriting_profiles(self):
        before = {p.name: p.read_bytes() for p in (self.root / 'config').glob('*.json')}
        self.assertEqual(self.b.request('Orochi'), 'Orochi')
        self.assertEqual(self.a.pending_task(), 'Orochi')
        self.assertEqual(self.b.pending_task(), 'Orochi')
        self.assertEqual(before, {p.name: p.read_bytes() for p in (self.root / 'config').glob('*.json')})

    def test_handoff_can_wake_either_side(self):
        self.assertEqual(self.a.request('BondlingFairyland'), 'BondlingFairyland')
        self.assertEqual(self.b.pending_task(), 'BondlingFairyland')

    def test_simultaneous_different_requests_choose_one_session(self):
        barrier = threading.Barrier(2)
        def request(coordinator, command):
            barrier.wait(timeout=3)
            return coordinator.request(command)
        with ThreadPoolExecutor(2) as pool:
            a = pool.submit(request, self.a, 'Orochi')
            b = pool.submit(request, self.b, 'BondlingFairyland')
            self.assertEqual(a.result(timeout=5), b.result(timeout=5))
        self.assertEqual(self.a.pending_task(), self.b.pending_task())

    def test_repeated_request_does_not_extend_deadline_or_change_task(self):
        self.a.request('Orochi')
        original = self.session()
        self.now[0] += 2
        self.b.request('BondlingFairyland')
        self.assertEqual(original['id'], self.session()['id'])
        self.assertEqual(original['deadline'], self.session()['deadline'])
        self.assertEqual('Orochi', self.session()['task'])

    def test_barrier_does_not_release_before_both_are_ready(self):
        self.joined()
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(self.a.ready)
            deadline = time.monotonic() + 3
            while not self.session()['ready'] and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertFalse(first.done())
            second = pool.submit(self.b.ready)
            first.result(timeout=5)
            second.result(timeout=5)
        self.assertEqual('running', self.session()['status'])

    def test_not_started_peer_is_not_launched_and_wait_times_out(self):
        self.a.request('Orochi')
        self.a.begin('Orochi')
        self.now[0] += 601
        self.a._heartbeat()
        with self.assertRaisesRegex(TeamSyncUnavailable, '超时'):
            self.a.ready()
        self.assertEqual('cancelled', self.session()['status'])
        self.assertIsNone(self.b.pending_task())

    def test_stopped_peer_cancels_request(self):
        self.joined()
        with self.a.state.transaction() as state:
            del state['peers']['oas2']
        with self.assertRaisesRegex(TeamSyncUnavailable, '停止或重启'):
            self.a.ready()
        self.assertIsNone(self.a.pending_task())

    def test_new_process_token_cannot_consume_previous_request(self):
        self.joined()
        restarted = LocalTeamCoordinator('oas2', self.root, clock=lambda: self.now[0])
        restarted.start = restarted._heartbeat
        self.assertIsNone(restarted.pending_task())
        self.assertEqual('cancelled', self.session()['status'])

    def test_disabled_task_is_not_force_enabled(self):
        self.configs['oas2']['orochi']['scheduler']['enable'] = False
        self.save_profiles()
        with self.assertRaisesRegex(TeamSyncUnavailable, '任务已关闭'):
            self.a.request('Orochi')
        self.assertFalse(self.configs['oas2']['orochi']['scheduler']['enable'])

    def test_binding_must_be_reciprocal(self):
        self.configs['oas2']['global_game']['local_team']['partner_config'] = 'oas3'
        self.save_profiles()
        with self.assertRaisesRegex(TeamSyncUnavailable, '相互绑定'):
            self.a.request('Orochi')

    def test_invalid_partner_path_is_rejected(self):
        self.configs['oas1']['global_game']['local_team']['partner_config'] = '../oas2'
        self.save_profiles()
        with self.assertRaisesRegex(TeamSyncUnavailable, '名称无效'):
            self.a.request('Orochi')

    def test_wrong_roles_layer_and_mode_are_rejected(self):
        for group, field, value in [('orochi_config', 'user_status', 'leader'),
                                    ('orochi_config', 'layer', '拾层'),
                                    ('bondling_config', 'bondling_mode', '只刷探查(仅限单刷)')]:
            with self.subTest(field=field):
                task, key = ('Orochi', 'orochi') if group == 'orochi_config' else ('BondlingFairyland', 'bondling_fairyland')
                original = self.configs['oas2'][key][group][field]
                self.configs['oas2'][key][group][field] = value
                self.save_profiles()
                with self.assertRaises(TeamSyncUnavailable):
                    self.a.request(task)
                self.configs['oas2'][key][group][field] = original

    def test_disabled_feature_and_true_orochi_do_not_create_requests(self):
        self.assertEqual('TrueOrochi', self.a.request('TrueOrochi'))
        self.configs['oas1']['global_game']['local_team']['enable'] = False
        self.save_profiles()
        self.assertEqual('Orochi', self.a.request('Orochi'))
        self.assertFalse(self.a.state.path.exists())

    def test_disabling_partner_during_wait_cancels_session(self):
        self.joined()
        self.configs['oas2']['global_game']['local_team']['enable'] = False
        self.save_profiles()
        self.assertIsNone(self.a.pending_task())
        self.assertEqual('cancelled', self.session()['status'])

    def test_disabling_self_after_join_cancels_session(self):
        self.joined()
        self.configs['oas1']['global_game']['local_team']['enable'] = False
        self.save_profiles()
        self.assertIsNone(self.a.pending_task())
        self.assertEqual('cancelled', self.session()['status'])

    def test_early_capacity_skip_releases_waiting_partner(self):
        self.joined('BondlingFairyland')
        self.a.finish('BondlingFairyland', True)
        with self.assertRaises(TeamPartnerFinished):
            self.b.ready()
        self.assertIsNone(self.b.pending_task())

    def test_capacity_skip_is_not_repeated_by_a_late_peer(self):
        self.configs['oas1']['bondling_fairyland']['scheduler']['next_run'] = '2030-01-01 09:00:00'
        self.save_profiles()
        self.joined('BondlingFairyland')
        self.a.finish('BondlingFairyland', True)
        with self.assertRaises(TeamPartnerFinished):
            self.b.request('BondlingFairyland')

    def test_completion_is_consumed_once_and_signals_partner_schedule(self):
        self.joined()
        self.ready_both()
        next_run = '2030-01-01 09:30:00'
        self.configs['oas1']['orochi']['scheduler']['next_run'] = next_run
        self.save_profiles()
        self.a.finish('Orochi', True)
        self.assertIsNone(self.a.pending_task())
        reason = self.b.interruption()
        self.assertIsInstance(reason, TeamPartnerFinished)
        self.assertEqual(datetime.fromisoformat(next_run), reason.next_run)
        self.b.finish('Orochi', True)
        self.assertEqual('finished', self.session()['status'])
        self.assertIsNone(self.b.pending_task())

    def test_ready_failed_session_has_bounded_retry(self):
        self.joined()
        self.a.finish('Orochi', False)
        with self.assertRaises(TeamSyncUnavailable):
            self.b.request('Orochi')
        self.now[0] += 121
        self.assertEqual('Orochi', self.b.request('Orochi'))
        self.assertEqual('waiting', self.session()['status'])

    def test_inflight_true_orochi_and_recovery_are_not_interrupted(self):
        self.a.request('Orochi')
        for task in ('TrueOrochi', 'Restart', 'GotoMain'):
            self.b.begin(task)
            self.assertIsNone(self.b.interruption())

    def test_real_heartbeat_is_removed_on_close(self):
        coordinator = LocalTeamCoordinator('oas1', self.root)
        try:
            coordinator.start()
            with coordinator.state.transaction() as state:
                self.assertEqual(coordinator.token, state['peers']['oas1']['token'])
        finally:
            coordinator.close()
        with coordinator.state.transaction() as state:
            self.assertNotIn('oas1', state['peers'])


class IntegrationTests(TeamFixture):
    """Run the real scheduler methods with mock game devices and temporary profiles."""

    def scheduler(self, coordinator, selected='DailyTrifles', *, future=False):
        namespace = dict(logger=Mock(), datetime=datetime, timedelta=timedelta, time=time,
                         convert_to_underscore=lambda name: {'Orochi': 'orochi', 'BondlingFairyland': 'bondling_fairyland'}[name],
                         ScriptRuntimeDecision=SimpleNamespace(RESCHEDULE='reschedule', FAILED='failed'),
                         del_cached_property=Mock(), TaskEnd=TaskEnd, Path=Path, load_module=Mock())
        self.namespace = namespace
        cls = source_methods('script.py', 'Script', ['get_next_task', 'wait_until',
                             '_handle_task_exception', '_set_task_runtime_outcome',
                             '_reset_task_runtime_outcome', 'run'], namespace)
        scheduler = cls()
        self.models = SimpleNamespace(**{key: SimpleNamespace(model_dump=Mock(return_value=value))
            for key, value in self.configs[coordinator.name].items() if key in ('orochi', 'bondling_fairyland')})
        task = SimpleNamespace(command=selected,
                               next_run=datetime.now() + timedelta(days=1) if future else datetime(2020, 1, 1))
        scheduler.config = SimpleNamespace(get_next=Mock(return_value=task), model=self.models,
            pending_task=[], waiting_task=[], script=SimpleNamespace(anti_ban=None),
            task_delay=Mock(), start_watching=Mock(), should_reload=Mock(return_value=False))
        scheduler.team_sync = coordinator
        scheduler.anti_ban_guard = Mock(wake_time=Mock(return_value=None))
        scheduler._hoard_next_task = Mock(side_effect=lambda task, now: task)
        scheduler.state_queue = None
        scheduler.runtime = Mock()
        scheduler.device = Mock()
        scheduler._capture_task_runtime_outcome = Mock()
        return scheduler

    def test_request_overrides_other_pending_task_and_hoarding(self):
        self.a.request('Orochi')
        scheduler = self.scheduler(self.b)
        self.assertEqual('Orochi', scheduler.get_next_task())
        scheduler._hoard_next_task.assert_not_called()
        scheduler.config.task_delay.assert_not_called()
        self.assertEqual('Orochi', scheduler.config.pending_task[0].command)

    def test_future_restart_does_not_block_request(self):
        self.a.request('Orochi')
        scheduler = self.scheduler(self.b, 'Restart', future=True)
        self.assertEqual('Orochi', scheduler.get_next_task())

    def test_due_restart_precedes_requested_task(self):
        self.a.request('Orochi')
        scheduler = self.scheduler(self.b, 'Restart')
        self.assertEqual('Restart', scheduler.get_next_task())

    def test_idle_wait_wakes_for_new_request_without_config_edit(self):
        self.a.request('Orochi')
        scheduler = self.scheduler(self.b)
        self.assertFalse(scheduler.wait_until(datetime.now() + timedelta(days=1)))
        scheduler.config.should_reload.assert_not_called()

    def test_preemption_does_not_change_task_schedule(self):
        scheduler = self.scheduler(self.b)
        self.assertTrue(scheduler._handle_task_exception(TeamTaskSwitch('Orochi'), 'DailyTrifles'))
        scheduler.config.task_delay.assert_not_called()
        self.assertEqual('team_preempted', scheduler.last_task_runtime_outcome['status'])

    def test_wait_failure_retries_shortly_instead_of_consuming_weekly_interval(self):
        scheduler = self.scheduler(self.b)
        before = datetime.now().replace(microsecond=0)
        self.assertTrue(scheduler._handle_task_exception(TeamSyncUnavailable('offline'), 'BondlingFairyland'))
        call = scheduler.config.task_delay.call_args.kwargs
        self.assertEqual('BondlingFairyland', call['task'])
        self.assertLessEqual(abs((call['target'] - before).total_seconds() - 120), 1)
        self.assertFalse(call['server'])

    def test_task_run_enters_and_finishes_the_requested_session(self):
        self.joined()
        scheduler = self.scheduler(self.b)
        def task_body():
            self.assertEqual('Orochi', self.b.current_task)
            self.b.ready()
            raise TaskEnd()
        self.namespace['load_module'].return_value.ScriptTask.return_value.run.side_effect = task_body
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(self.a.ready)
            second = pool.submit(scheduler.run, 'Orochi')
            first.result(timeout=5)
            self.assertTrue(second.result(timeout=5))
        self.assertEqual('', self.b.current_task)
        self.assertIn('oas2', self.session()['done'])

    def test_wait_with_known_request_honors_existing_rest_window(self):
        self.a.request('Orochi')
        scheduler = self.scheduler(self.b)
        scheduler._waiting_team_request = 'Orochi'
        scheduler.config.should_reload.return_value = True
        with patch('time.sleep') as sleep:
            self.assertFalse(scheduler.wait_until(datetime.now() + timedelta(days=1)))
        sleep.assert_called_once_with(1)
        scheduler.config.should_reload.assert_called_once()

    def test_error_before_get_next_task_returns_uses_selected_task(self):
        scheduler = self.scheduler(self.b)
        scheduler.config.task = SimpleNamespace(command='Orochi')
        scheduler._handle_task_exception(TeamSyncUnavailable('peer disabled'), '')
        self.assertEqual('Orochi', scheduler.config.task_delay.call_args.kwargs['task'])


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        cls = source_methods('tasks/base_task.py', 'BaseTask',
                             ['local_team_checkpoint', 'wait_local_team_ready'], dict(datetime=datetime))
        self.task = cls()
        self.coordinator = Mock(session_id='session')
        self.coordinator.interruption.return_value = TeamTaskSwitch('Orochi')
        self.task.config = SimpleNamespace(team_sync=self.coordinator)
        self.task.navigator = object()
        self.task.device = Mock()
        self.task.detect_page_in = Mock(return_value=None)
        self.task.get_current_page = Mock(return_value=SimpleNamespace(name='page_main'))

    def test_battle_context_prevents_interrupting_inflight_battle(self):
        self.task._battle_context = object()
        self.task.local_team_checkpoint()
        self.coordinator.interruption.assert_not_called()

    def test_battle_or_reward_overlay_prevents_switch(self):
        self.task.detect_page_in.return_value = object()
        self.task.local_team_checkpoint()
        self.task.get_current_page.assert_not_called()

    def test_unknown_page_prevents_switch(self):
        self.task.get_current_page.return_value = None
        self.task.local_team_checkpoint()

    def test_confirmed_navigation_page_yields_and_clears_reentry_guard(self):
        with self.assertRaises(TeamTaskSwitch):
            self.task.local_team_checkpoint()
        self.assertFalse(self.task._checking_local_team)

    def test_confirmed_team_room_can_yield_between_battles(self):
        self.task.get_current_page.return_value = SimpleNamespace(name='page_battle_team')
        with self.assertRaises(TeamTaskSwitch):
            self.task.local_team_checkpoint()

    def test_battle_that_started_during_detection_is_not_interrupted(self):
        self.task.get_current_page.return_value = SimpleNamespace(name='page_battle')
        self.task.local_team_checkpoint()

    def test_nested_screenshot_during_detection_does_not_reenter(self):
        self.task.detect_page_in.side_effect = lambda *args, **kwargs: self.task.local_team_checkpoint()
        with self.assertRaises(TeamTaskSwitch):
            self.task.local_team_checkpoint()
        self.coordinator.interruption.assert_called_once()

    def test_unconfigured_wait_preserves_existing_timers(self):
        self.coordinator.session_id = None
        self.task.start_time = datetime(2020, 1, 1)
        self.task.wait_local_team_ready()
        self.coordinator.ready.assert_not_called()
        self.task.device.stuck_record_clear.assert_not_called()
        self.assertEqual(datetime(2020, 1, 1), self.task.start_time)

    def test_wait_resets_task_time_only_after_barrier_passes(self):
        self.task.start_time = datetime(2020, 1, 1)
        self.task.wait_local_team_ready()
        self.coordinator.ready.assert_called_once()
        self.assertGreater(self.task.start_time, datetime(2020, 1, 1))
        self.task.device.stuck_record_clear.assert_called_once()


if __name__ == '__main__':
    unittest.main()
