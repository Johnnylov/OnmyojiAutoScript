"""Offline coverage of the global scheduler rendezvous and TrueOrochi protocol."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import json
import time
import unittest
from unittest.mock import Mock

from dev_tools.test_activity_preparation_retry import source_methods
from dev_tools import test_local_team_sync as team_tests
from module.base.timer import Timer
from module.exception import TaskDeferred, TaskEnd
from module.script.team_sync import TeamSyncUnavailable
from tasks.TrueOrochi.config import TrueOrochi, TeamRole
from tasks.TrueOrochi.team import LocalTeam, TeamSyncError


class TrueOrochiLinkTests(team_tests.TeamFixture):
    def setUp(self):
        super().setUp()
        for index, (name, data) in enumerate(self.configs.items()):
            peer = 'oas2' if name == 'oas1' else 'oas1'
            data['global_game']['local_team']['sync_true_orochi'] = True
            data['script'] = {'device': {'serial': f'127.0.0.1:{16000 + index}'}}
            data['true_orochi'] = {
                'scheduler': dict(enable=True, priority=10, next_run='2030-01-01 00:00:00'),
                'team_config': dict(enable=True, teammate_config=peer,
                                    user_status='leader' if index == 0 else 'member',
                                    hosting_mode='true_orochi_split'),
                'invite_config': dict(friend_list=f'{peer}游戏名'),
            }
        self.save_profiles()

    def test_member_requests_future_leader_without_changing_configs(self):
        before = {p.name: p.read_bytes() for p in (self.root / 'config').glob('*.json')}
        self.assertEqual(self.b.request('TrueOrochi'), 'TrueOrochi')
        self.assertEqual(self.a.pending_task(), 'TrueOrochi')
        self.assertEqual(before, {p.name: p.read_bytes() for p in (self.root / 'config').glob('*.json')})

    def test_old_and_disabled_settings_preserve_direct_true_orochi(self):
        for value in (None, False):
            settings = self.configs['oas1']['global_game']['local_team']
            if value is None:
                settings.pop('sync_true_orochi', None)
            else:
                settings['sync_true_orochi'] = value
            self.save_profiles()
            self.assertEqual('TrueOrochi', self.a.request('TrueOrochi'))
            self.assertFalse(self.a.state.path.exists())

    def test_existing_roles_bindings_task_enable_and_invitation_are_required(self):
        changes = [
            ('true_orochi', 'scheduler', 'enable', False),
            ('true_orochi', 'team_config', 'enable', False),
            ('true_orochi', 'team_config', 'teammate_config', 'other'),
            ('true_orochi', 'team_config', 'user_status', 'leader'),
            ('true_orochi', 'invite_config', 'friend_list', ''),
            ('true_orochi', 'invite_config', 'friend_list', 'friend1\nfriend2'),
            ('global_game', 'local_team', 'sync_true_orochi', False),
            ('script', 'device', 'serial', '127.0.0.1:16000'),
        ]
        original = json.loads(json.dumps(self.configs['oas2']))
        for group, section, field, value in changes:
            with self.subTest(field=field, value=value):
                self.configs['oas2'] = json.loads(json.dumps(original))
                self.configs['oas2'][group][section][field] = value
                self.save_profiles()
                with self.assertRaises(TeamSyncUnavailable):
                    self.a.request('TrueOrochi')
                self.assertFalse(self.a.state.path.exists())

    def test_disabled_peer_and_wait_timeout_release_session(self):
        self.joined('TrueOrochi')
        self.now[0] += 601
        self.a._heartbeat()
        self.b._heartbeat()
        with self.assertRaisesRegex(TeamSyncUnavailable, '超时'):
            self.a.ready()
        self.assertIsNone(self.b.pending_task())
        with self.assertRaises(TeamSyncUnavailable):
            self.b.request('TrueOrochi')
        self.now[0] += 121
        self.assertEqual('TrueOrochi', self.b.request('TrueOrochi'))

    def test_stopped_peer_and_close_cancel_wait(self):
        self.joined('TrueOrochi')
        with self.a.state.transaction() as state:
            del state['peers']['oas2']
        with self.assertRaisesRegex(TeamSyncUnavailable, '停止或重启'):
            self.a.ready()
        self.assertIsNone(self.b.pending_task())

    def scheduler(self, coordinator):
        scheduler = team_tests.IntegrationTests.scheduler(self, coordinator)
        self.namespace['convert_to_underscore'] = lambda task: {
            'Orochi': 'orochi', 'BondlingFairyland': 'bondling_fairyland',
            'TrueOrochi': 'true_orochi'}[task]
        scheduler.config.model.true_orochi = SimpleNamespace(model_dump=Mock(
            return_value=self.configs[coordinator.name]['true_orochi']))
        return scheduler

    def task(self, coordinator):
        namespace = dict(
            datetime=datetime, Path=lambda name: self.root / name, json=json,
            TrueOrochi=TrueOrochi, TeamRole=TeamRole, TrueOrochiError=ValueError,
            TeamSyncError=TeamSyncError, TaskDeferred=TaskDeferred, TaskEnd=TaskEnd,
            logger=Mock(), Timer=Timer, sleep=time.sleep,
            LocalTeam=lambda own, peer, leader, mode: LocalTeam(
                own, peer, leader, mode, directory=self.root / 'rounds'),
        )
        cls = source_methods('tasks/TrueOrochi/script_task.py', 'ScriptTask',
                             ['_connect_team', '_wait_scheduled_team', 'run',
                              '_run_team', '_wait_sync'], namespace)
        task = cls()
        data = self.configs[coordinator.name]
        task.config = SimpleNamespace(config_name=coordinator.name, team_sync=coordinator,
                                      true_orochi=TrueOrochi(**data['true_orochi']),
                                      script=SimpleNamespace(device=SimpleNamespace(**data['script']['device'])))
        task._team_sync = None
        task.device = Mock()
        task._reset_week = Mock()
        task.switch_true_orochi_souls = Mock()
        task._leave_true_room = Mock()
        task.goto_page = Mock()
        task.check_times = Mock()
        task._keep_long_wait = Mock()
        task.screenshot = Mock()
        task._inspect_counts = Mock(side_effect=[(2, 2), (1, 1)])
        task._create_true_room = Mock()
        task._invite_true_friend = Mock()
        task._start_true_room = Mock()
        task._wait_true_invitation = Mock()
        task._record_success = Mock()
        task.run_true_orochi_battle = Mock(return_value=True)
        namespace['page_main'] = object()
        return task

    def test_scheduler_wakes_both_then_existing_round_protocol_rotates_hosts(self):
        self.b.request('TrueOrochi')
        schedulers = [self.scheduler(coordinator) for coordinator in (self.a, self.b)]
        self.assertEqual(['TrueOrochi', 'TrueOrochi'], [s.get_next_task() for s in schedulers])
        tasks = [self.task(coordinator) for coordinator in (self.a, self.b)]
        for scheduler, coordinator in zip(schedulers, (self.a, self.b)):
            coordinator.begin('TrueOrochi')

        def run(task, coordinator):
            with self.assertRaises(TaskEnd):
                task.run()
            coordinator.finish('TrueOrochi', True)

        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(run, task, coordinator) for task, coordinator in zip(tasks, (self.a, self.b))]
            for future in futures:
                future.result(timeout=10)
        self.assertEqual('finished', self.session()['status'])
        for task in tasks:
            self.assertEqual(2, task.run_true_orochi_battle.call_count)
            self.assertEqual(2, task._record_success.call_count)
            task._create_true_room.assert_called_once()
            task._wait_true_invitation.assert_called_once()
            task.device.screenshot.assert_called_once()
            task.check_times.assert_called_once_with(True)

    def test_safe_stop_during_barrier_performs_no_game_input_or_round_registration(self):
        self.a.request('TrueOrochi')
        self.a.begin('TrueOrochi')
        task = self.task(self.a)
        task.config.solana_execution = Mock(
            control_status=Mock(return_value={'control': 'safe_stop'}),
            stop_at_boundary=Mock(side_effect=SystemExit(0)))
        with self.assertRaises(SystemExit):
            task.run()
        task.switch_true_orochi_souls.assert_not_called()
        task._create_true_room.assert_not_called()
        self.assertFalse((self.root / 'rounds').exists())
        self.a.finish('TrueOrochi', False)
        self.assertEqual('cancelled', self.session()['status'])


if __name__ == '__main__':
    unittest.main()
