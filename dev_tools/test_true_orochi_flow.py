"""Offline workflow tests: privacy gate, invitation barrier, ten-floor finish."""
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import Mock, patch
from concurrent.futures import ThreadPoolExecutor
from copy import copy
import tempfile
import time

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tasks.TrueOrochi import script_task as runtime
from tasks.TrueOrochi.config import TrueOrochi
from tasks.TrueOrochi.script_task import ScriptTask, TrueOrochiError
from tasks.TrueOrochi.view import Panel, TrueOrochiView
from tasks.TrueOrochi.team import LocalTeam
from tasks.TrueOrochi.team import TeamSyncError
from module.exception import TaskDeferred, TaskEnd


class TrueOrochiFlowTests(unittest.TestCase):
    def task(self, frames):
        task = ScriptTask.__new__(ScriptTask)
        clock = SimpleNamespace(now=0, index=-1)
        class VirtualTimer:
            def __init__(self, limit):
                self.limit = limit
            def start(self):
                self.started = clock.now
                return self
            reset = start
            def reached(self):
                return clock.now-self.started >= self.limit
        self.addCleanup(patch.stopall)
        patch.object(runtime, 'Timer', VirtualTimer).start()
        patch.object(runtime, 'sleep', lambda seconds: setattr(clock, 'now', clock.now+seconds)).start()
        task.config = SimpleNamespace(true_orochi=TrueOrochi(), save=Mock(), config_name='a')
        task.device = SimpleNamespace(image=np.zeros((720, 1280, 3), np.uint8),
                                      detect_record=set(), stuck_record_add=Mock(),
                                      stuck_record_clear=Mock(), click_record_clear=Mock())
        task.goto_page = Mock()
        task.set_next_run = Mock()
        task.click = Mock(return_value=True)
        task._tap = Mock(return_value=True)
        task._at_orochi_or_home = Mock(return_value=False)
        task._click_reward_exit = Mock()
        task._true_battling = False
        def screenshot():
            clock.now += .5
            clock.index = min(clock.index+1, len(frames)-1)
            frame = frames[clock.index]
            task._true_view = Mock()
            for name in ('entry', 'detail', 'confirm', 'private', 'room', 'prepare'):
                getattr(task._true_view, name).return_value = Panel(0, 0, 1) if frame.get(name) else None
            task._true_view.private_selected.return_value = frame.get('selected', False)
            task._true_view.empty_slot.return_value = frame.get('empty', True)
            task._true_view.rewards.return_value = frame.get('rewards')
        task.screenshot = Mock(side_effect=screenshot)
        def appear(rule, **_):
            return rule.name in frames[max(0, clock.index)].get('markers', ())
        task.appear = Mock(side_effect=appear)
        task.appear_then_click = Mock(side_effect=appear)
        task.is_in_real_battle = Mock(return_value=False)
        return task, clock

    def runnable_task(self):
        task, _ = self.task([{}])
        task.config.true_orochi.team_config.enable = True
        task._connect_team = Mock(return_value=SimpleNamespace(close=Mock()))
        task.switch_true_orochi_souls = Mock()
        task._run_team = Mock(return_value=True)
        task._leave_true_room = Mock()
        return task

    def test_exploration_icon_opens_details_and_private_room(self):
        # Replay all five supplied screens. The True Orochi icon is available
        # only on Exploration; ordinary soul navigation loses that entry.
        task, clock = self.task([{}])
        fixtures = Path(__file__).with_name('fixtures') / 'true_orochi'
        screens = {name: np.array(Image.open(fixtures / (name+'.png')).convert('RGB'))
                   for name in ('entry', 'detail', 'confirm', 'private', 'room')}
        exploration = np.full((720, 1280, 3), 55, dtype=np.uint8)
        icon_x, icon_y = 250, 620
        h, w = screens['entry'].shape[:2]
        exploration[icon_y:icon_y+h, icon_x:icon_x+w] = screens['entry']
        screens['entry'] = exploration
        screens['ordinary_souls'] = np.zeros_like(exploration)
        state = SimpleNamespace(page='entry')
        actions = []

        def screenshot():
            clock.now += .5
            task.device.image = screens[state.page]
            task._true_view = TrueOrochiView(task.device.image)

        def navigate(page):
            actions.append(('navigate', page.name))
            state.page = 'entry' if page is runtime.page_exploration else 'ordinary_souls'

        transitions = {
            ('entry', 'TRUE_OROCHI_ENTRY'): 'detail',
            ('detail', 'TRUE_OROCHI_CLOSE'): 'entry',
            ('detail', 'TRUE_OROCHI_CHALLENGE'): 'confirm',
            ('confirm', 'TRUE_OROCHI_CONFIRM'): 'private',
            ('private', 'TRUE_OROCHI_CREATE'): 'room',
        }
        def tap(region, name):
            actions.append(('click', name))
            if name == 'TRUE_OROCHI_ENTRY':
                # A translated screenshot must still click inside image 1's
                # dragon icon, away from the adjacent ordinary soul button.
                x, y, width, height = region
                self.assertTrue(icon_x < x < x+width < icon_x+w)
                self.assertTrue(icon_y < y < y+height < icon_y+h)
            state.page = transitions[(state.page, name)]
            return True

        task.goto_page.side_effect = navigate
        task.screenshot.side_effect = screenshot
        task._tap.side_effect = tap
        task._read_text = Mock(side_effect=lambda _: '2' if state.page == 'entry' else '2/2')
        self.assertEqual(task._inspect_counts(), (2, 2))
        task._create_true_room(2)
        self.assertEqual(state.page, 'room')
        self.assertEqual(actions, [
            ('navigate', runtime.page_exploration.name),
            ('click', 'TRUE_OROCHI_ENTRY'),
            ('click', 'TRUE_OROCHI_CLOSE'),
            ('navigate', runtime.page_exploration.name),
            ('click', 'TRUE_OROCHI_ENTRY'),
            ('click', 'TRUE_OROCHI_CHALLENGE'),
            ('click', 'TRUE_OROCHI_CONFIRM'),
            ('click', 'TRUE_OROCHI_CREATE'),
        ])
        self.assertEqual(task.config.true_orochi.true_orochi_config.current_success, 0)

    def test_one_entry_uses_actual_ocr_and_opens_the_top_left_dragon(self):
        from module.ocr.ppocr import TextSystem
        task, clock = self.task([{}])
        task.O_TRUE_TEXT = copy(ScriptTask.O_TRUE_TEXT)
        task.O_TRUE_TEXT.model = TextSystem()
        fixtures = Path(__file__).with_name('fixtures') / 'true_orochi'
        screens = {name: np.array(Image.open(fixtures / f'{name}.png').convert('RGB'))
                   for name in ('exploration_one', 'detail')}
        state = SimpleNamespace(page='exploration_one')
        task._leave_true_room = Mock()
        def screenshot():
            clock.now += .5
            task.device.image = screens[state.page]
            task._true_view = TrueOrochiView(task.device.image)
        def tap(region, name):
            self.assertEqual(name, 'TRUE_OROCHI_ENTRY')
            x, y, width, height = region
            self.assertTrue(0 < x < x+width < 100 and 100 < y < y+height < 185)
            state.page = 'detail'
            return True
        task.screenshot.side_effect = screenshot
        task._tap.side_effect = tap
        # One available entry is independent of the detail's reward allowance.
        self.assertEqual(task._inspect_counts(), (1, 2))
        task.goto_page.assert_called_once_with(runtime.page_exploration)
        task._tap.assert_called_once()
        self.assertEqual(state.page, 'detail')

    def test_rejected_connection_defers_without_touching_game_or_success_count(self):
        task = self.runnable_task()
        task._connect_team.side_effect = TeamSyncError('duplicate runner')
        with self.assertRaises(TaskDeferred) as raised:
            task.run()
        self.assertEqual(raised.exception.retry_after, 120)
        self.assertEqual(task.config.true_orochi.true_orochi_config.current_success, 0)
        task.switch_true_orochi_souls.assert_not_called()
        task._run_team.assert_not_called()
        task._leave_true_room.assert_not_called()
        task.goto_page.assert_not_called()
        task.set_next_run.assert_not_called()

    def test_interrupted_partner_defers_and_preserves_earned_reward(self):
        task = self.runnable_task()
        task.config.true_orochi.true_orochi_config.current_success = 1
        task._run_team.side_effect = TeamSyncError('peer stopped')
        sync = task._connect_team.return_value
        with self.assertRaises(TaskDeferred) as raised:
            task.run()
        self.assertEqual(raised.exception.retry_after, 120)
        self.assertEqual(task.config.true_orochi.true_orochi_config.current_success, 1)
        sync.close.assert_called_once_with('peer stopped')
        task._leave_true_room.assert_called_once()
        self.assertIsNone(task._team_sync)
        task.set_next_run.assert_not_called()

    def test_unknown_counts_defer_instead_of_ending_as_completed(self):
        task = self.runnable_task()
        task._run_team.side_effect = TrueOrochiError('unknown counts')
        with self.assertRaises(TaskDeferred):
            task.run()
        task.set_next_run.assert_not_called()
        self.assertEqual(task.config.true_orochi.true_orochi_config.current_success, 0)

    def test_no_entries_retains_failure_interval_without_reporting_completion(self):
        task = self.runnable_task()
        task._run_team.return_value = False
        with self.assertRaises(TaskDeferred) as raised:
            task.run()
        self.assertEqual(raised.exception.retry_after,
                         task.config.true_orochi.scheduler.failure_interval.total_seconds())
        self.assertEqual(task.config.true_orochi.true_orochi_config.current_success, 0)

    def test_successful_run_still_ends_and_schedules_normally(self):
        task = self.runnable_task()
        task.config.true_orochi.true_orochi_config.current_success = 2
        with self.assertRaises(TaskEnd):
            task.run()
        task.set_next_run.assert_called_once()
        task._connect_team.return_value.close.assert_called_once_with('')

    def test_cleanup_timeout_is_unfinished(self):
        task = self.runnable_task()
        task._leave_true_room.side_effect = TrueOrochiError('room cleanup timeout')
        with self.assertRaises(TaskDeferred):
            task.run()
        task.set_next_run.assert_not_called()
        task._connect_team.return_value.close.assert_called_once_with('room cleanup timeout')

    def test_account_stays_exclusive_until_room_cleanup_finishes(self):
        task = self.runnable_task()
        with tempfile.TemporaryDirectory() as directory:
            team = LocalTeam('a', 'b', 'a', 'true_orochi_random', directory)
            task._connect_team.return_value = team
            def leave():
                self.assertIsNone(task._team_sync)
                with self.assertRaises(TeamSyncError):
                    LocalTeam('a', 'b', 'a', 'true_orochi_random', directory)
            task._leave_true_room.side_effect = leave
            try:
                with self.assertRaises(TaskEnd):
                    task.run()
                resumed = LocalTeam('a', 'b', 'a', 'true_orochi_random', directory)
                resumed.close()
            finally:
                team.close()

    def test_private_tick_is_verified_before_create(self):
        task, clock = self.task([{'private': True}, {'private': True, 'selected': True}, {'room': True}])
        task._create_true_room(2)
        self.assertEqual([call.args[1] for call in task._tap.call_args_list],
                         ['TRUE_OROCHI_PRIVATE', 'TRUE_OROCHI_CREATE'])
        self.assertEqual(clock.index, 2)

    def test_unverified_privacy_times_out_without_creating(self):
        task, _ = self.task([{'private': True}])
        with self.assertRaises(TrueOrochiError):
            task._create_true_room(2)
        self.assertTrue(task._tap.called)
        self.assertTrue(all(call.args[1] == 'TRUE_OROCHI_PRIVATE' for call in task._tap.call_args_list))

    def test_changed_or_unknown_reward_count_stops_before_spending(self):
        for count in (None, 0, 1):
            task, _ = self.task([{'detail': True, 'rewards': count}])
            with self.assertRaises(TrueOrochiError):
                task._create_true_room(2)
            task._tap.assert_not_called()

    def test_ocr_invitation_failure_does_not_continue(self):
        task, _ = self.task([{'markers': [ScriptTask.I_INVITE_ENSURE.name]}])
        task.invite_friends = Mock(return_value=False)
        with self.assertRaises(TrueOrochiError):
            task._invite_true_friend()
        task.invite_friends.assert_called_once_with(task.config.true_orochi.invite_config, open_invite=False)
        task._tap.assert_not_called()

    def test_invitation_popup_is_cancelled_before_leaving_the_room(self):
        task, clock = self.task([
            {'room': True, 'markers': [ScriptTask.I_INVITE_ENSURE.name]},
            {'room': True}, {},
        ])
        actions = []
        task.ocr_appear_click = Mock(side_effect=lambda *a, **kw: actions.append(('cancel', clock.index)) or True)
        task._tap.side_effect = lambda *a: actions.append(('leave', clock.index)) or True
        task._leave_true_room()
        self.assertEqual(actions, [('cancel', 0), ('leave', 1)])
        task.ocr_appear_click.assert_called_once_with(task.O_TRUE_INVITE_CANCEL, interval=1, exact=True)

    def test_leader_waits_for_acknowledged_and_occupied_room(self):
        task, clock = self.task([
            {'room': True, 'empty': True},
            {'room': True, 'empty': True},
            {'room': True, 'empty': False},
            {'markers': [ScriptTask.I_ST_FIRE_PREPARE.name]},
        ])
        task._team_sync = SimpleNamespace(peer='b', round_state=lambda _: {'joined': [] if clock.index == 0 else ['b']})
        clicked_frames = []
        task._tap.side_effect = lambda *_: clicked_frames.append(clock.index) or True
        task._start_true_room(0)
        self.assertEqual(clicked_frames, [2])

    def test_member_joins_without_needing_own_entry(self):
        task, _ = self.task([
            {'markers': [ScriptTask.I_I_ACCEPT.name]}, {'room': True},
            {'markers': [ScriptTask.I_ST_FIRE_PREPARE.name]},
        ])
        task._team_sync = SimpleNamespace(joined=Mock())
        task._wait_true_invitation(0)
        task._team_sync.joined.assert_called_once_with(0)
        task._tap.assert_not_called()

    def test_member_can_reach_configuration_without_recognizing_room_footer(self):
        task, _ = self.task([{}, {'prepare': True}])
        task._team_sync = SimpleNamespace(joined=Mock())
        task._wait_true_invitation(0)
        task._team_sync.joined.assert_called_once_with(0)

    def test_visual_member_verification_requires_two_consecutive_frames(self):
        task, clock = self.task([
            {'room': True, 'empty': False},
            {'room': True, 'empty': True},
            {'room': True, 'empty': False},
            {'room': True, 'empty': False},
            {'prepare': True},
        ])
        task._team_sync = SimpleNamespace(peer='b', round_state=lambda _: {'ready': {'b': {}}, 'joined': []})
        task._room_friend_present = Mock(return_value=True)
        clicks = []
        task._tap.side_effect = lambda *_: clicks.append(clock.index) or True
        task._start_true_room(0)
        self.assertEqual(clicks, [3])

    def test_occupied_slot_without_exact_friend_does_not_start_or_reinvite(self):
        task, _ = self.task([{'room': True, 'empty': False}])
        task.config.true_orochi.invite_config.wait_time = datetime.min.time().replace(second=25)
        task._team_sync = SimpleNamespace(peer='b', round_state=lambda _: {'ready': {'b': {}}, 'joined': []})
        task._room_friend_present = Mock(return_value=False)
        task._invite_true_friend = Mock()
        with self.assertRaises(TrueOrochiError):
            task._start_true_room(0)
        task._tap.assert_not_called()
        task._invite_true_friend.assert_not_called()

    def test_ten_floor_auto_and_frame_cleanup_before_success(self):
        task, clock = self.task([
            {'markers': [ScriptTask.I_ST_FIRE_PREPARE.name]},
            {'markers': [ScriptTask.I_BUFF.name, ScriptTask.I_ST_AUTO_FALSE.name]},
            {}, {'markers': [ScriptTask.I_GREED_GHOST.name]}, {},
            {'markers': [ScriptTask.I_ST_FRAME.name]}, {'room': True},
        ])
        self.assertTrue(task.run_true_orochi_battle())
        self.assertEqual(clock.index, 6)
        task.appear_then_click.assert_any_call(ScriptTask.I_ST_AUTO_FALSE, interval=1.8)
        task.appear_then_click.assert_any_call(ScriptTask.I_ST_FRAME, interval=1)
        task._click_reward_exit.assert_called_once()
        self.assertFalse(task._true_battling)

    def test_defeat_is_not_success(self):
        task, _ = self.task([{'markers': [ScriptTask.I_FALSE.name]}, {'room': True}])
        self.assertFalse(task.run_true_orochi_battle())

    def test_ready_and_auto_clicks_restore_long_battle_guard(self):
        task, _ = self.task([
            {'markers': [ScriptTask.I_ST_FIRE_PREPARE.name]},
            {'markers': [ScriptTask.I_BUFF.name, ScriptTask.I_ST_AUTO_FALSE.name]},
            {}, {'markers': [ScriptTask.I_GREED_GHOST.name]}, {'room': True},
        ])
        task.device.stuck_record_add.side_effect = task.device.detect_record.add
        task.device.stuck_record_clear.side_effect = task.device.detect_record.clear
        original_screenshot = task.screenshot.side_effect
        def screenshot():
            self.assertIn('BATTLE_STATUS_S', task.device.detect_record)
            original_screenshot()
        task.screenshot.side_effect = screenshot
        original_click = task.appear_then_click.side_effect
        def click(rule, **kwargs):
            clicked = original_click(rule, **kwargs)
            if clicked:
                task.device.detect_record.clear()
            return clicked
        task.appear_then_click.side_effect = click
        self.assertTrue(task.run_true_orochi_battle())
        self.assertGreaterEqual(task.device.stuck_record_add.call_count, 4)

    def test_frame_popup_alone_is_not_proof_of_rewards(self):
        task, clock = self.task([{'markers': [ScriptTask.I_ST_FRAME.name]}, {'room': True}])
        original = task.screenshot.side_effect
        def fast_screenshot():
            original()
            clock.now += 100
        task.screenshot.side_effect = fast_screenshot
        with self.assertRaises(TrueOrochiError):
            task.run_true_orochi_battle()

    def test_schedule_does_not_increment_or_reset_success_counter(self):
        task, _ = self.task([{}])
        conf = task.config.true_orochi.true_orochi_config
        conf.current_success = 2
        class Sunday(datetime):
            @classmethod
            def now(cls):
                return cls(2026, 9, 20, 23, 55)
        with patch.object(runtime, 'datetime', Sunday):
            task.check_times(True)
        self.assertEqual(conf.current_success, 2)
        self.assertEqual(task.set_next_run.call_args.kwargs['target'], datetime(2026, 9, 21, 0, 5))
        conf.current_success = 0
        task.check_times(False)
        self.assertEqual(conf.current_success, 0)

    def test_actual_new_week_resets_and_success_is_capped(self):
        task, _ = self.task([{}])
        conf = task.config.true_orochi.true_orochi_config
        conf.current_success, conf.success_week = 2, '2026-37'
        with patch.object(runtime, 'week_key', return_value='2026-38'):
            task._reset_week()
            self.assertEqual(conf.current_success, 0)
            task._record_success(); task._record_success(); task._record_success()
        self.assertEqual(conf.current_success, 2)
        self.assertEqual(conf.success_week, '2026-38')

    def test_original_soul_settings_survive_config_upgrade(self):
        original = {'enable': True, 'switch_group_team': '2,3',
                    'enable_switch_by_name': True, 'group_name': '真蛇',
                    'team_name': '双开', 'enable_switch_layer_soul': True}
        config = TrueOrochi(switch_soul=original)
        self.assertEqual(config.switch_soul.model_dump(), original)
        self.assertFalse(config.team_config.enable)
        self.assertEqual(config.team_config.hosting_mode, 'true_orochi_random')

    def test_two_task_instances_finish_with_reverse_invitation(self):
        # Exercise the real task orchestrator and real journal together. Only
        # the device/UI boundaries are replaced, so round barriers stay real.
        with tempfile.TemporaryDirectory() as directory:
            actions = []
            tasks = []
            for name, peer, entries in [('a', 'b', [1, 0]), ('b', 'a', [2, 2])]:
                task = ScriptTask.__new__(ScriptTask)
                config = TrueOrochi()
                config.invite_config.wait_time = datetime.min.time().replace(second=3)
                task.config = SimpleNamespace(config_name=name, true_orochi=config, save=Mock())
                task._team_sync = LocalTeam(name, peer, 'a', 'true_orochi_leader_twice', directory)
                task.device = SimpleNamespace(detect_record=set(), stuck_record_add=Mock())
                task.screenshot = Mock(side_effect=task._team_sync.heartbeat)
                task._inspect_counts = Mock(side_effect=[(entries[0], 2), (entries[1], 1)])
                task._create_true_room = Mock(side_effect=lambda _, name=name: actions.append(('create', name)))
                task._invite_true_friend = Mock(side_effect=lambda name=name: actions.append(('invite', name)))
                task._wait_true_invitation = task._team_sync.joined
                def start(number, task=task):
                    deadline = time.monotonic()+3
                    while time.monotonic() < deadline:
                        if task._team_sync.peer in task._team_sync.round_state(number)['joined']:
                            return
                        time.sleep(.01)
                    raise AssertionError('Peer never joined')
                task._start_true_room = start
                task.run_true_orochi_battle = Mock(return_value=True)
                task._leave_true_room = Mock()
                task.goto_page = Mock()
                tasks.append(task)
            try:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = [pool.submit(task._run_team) for task in tasks]
                    self.assertEqual([future.result(timeout=10) for future in results], [True, True])
                self.assertEqual(actions, [('create', 'a'), ('invite', 'a'), ('create', 'b'), ('invite', 'b')])
                for task in tasks:
                    self.assertEqual(task.config.true_orochi.true_orochi_config.current_success, 2)
                    self.assertEqual(task.run_true_orochi_battle.call_count, 2)
                    self.assertEqual(task._leave_true_room.call_count, 2)
            finally:
                for task in tasks:
                    task._team_sync.close()

    def test_room_timeout_precedes_game_auto_start(self):
        task, _ = self.task([{'room': True, 'empty': True}])
        task._true_room_created_at = runtime.monotonic()-241
        task._team_sync = SimpleNamespace(peer='b')
        with self.assertRaises(TrueOrochiError):
            task._start_true_room(0)
        task._tap.assert_not_called()


if __name__ == '__main__':
    unittest.main()
