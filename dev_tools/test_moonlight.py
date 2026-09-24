"""Exercise the local Moonlight implementation without a device or OCR service."""
from datetime import datetime, timedelta
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from module.exception import GamePageUnknownError, GameStuckError, TaskEnd
from tasks.base_task import BaseTask
from tasks.Component.GeneralBattle.general_battle import GeneralBattle
from tasks.Component.RightActivity.assets import RightActivityAssets
from tasks.GameUi.page import page_battle_prepare, page_main
from tasks.Moonlight.activity import MoonlightAct
from tasks.Moonlight.assets import MoonlightAssets
from tasks.Moonlight.config import Moonlight
from tasks.Moonlight.page import enter_moonlight, page_moon_battle, page_moon_lobby
from tasks.Moonlight.script_task import ScriptTask


class Clock:
    def __init__(self):
        self.now = 0

    def monotonic(self):
        self.now += .1
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def task():
    obj = object.__new__(ScriptTask)
    obj.conf = Moonlight()
    obj.device = SimpleNamespace(image=object())
    obj.start_time = datetime.now()
    obj._battle_shared_state = {}
    obj.frame = 0
    def screenshot():
        obj.frame += 1
        return obj.device.image
    obj.screenshot = Mock(side_effect=screenshot)
    obj.appear = Mock(side_effect=lambda marker: marker is obj.I_MOON_BATTLE)
    obj.appear_then_click = Mock(return_value=True)
    obj.O_MOON_RESOURCE = SimpleNamespace(ocr_digit=Mock(return_value=60))
    obj.O_MOON_REWARD_NOTICE = SimpleNamespace(detect_and_ocr=Mock(return_value=[]))
    obj.detect_page_in = Mock(return_value=page_battle_prepare)
    obj.goto_page = Mock(return_value=True)
    return obj


class MoonlightTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.time_patch = patch('tasks.Moonlight.activity.time', self.clock)
        self.time_patch.start()
        self.addCleanup(self.time_patch.stop)

    def test_defaults_are_separate_and_opt_in(self):
        first, second = Moonlight(), Moonlight()
        self.assertFalse(first.scheduler.enable)
        self.assertEqual(first.general_config.challenge_limit, 1)
        self.assertEqual(first.general_config.limit_time_v, timedelta(minutes=90))
        first.general_config.challenge_limit = 7
        self.assertEqual(second.general_config.challenge_limit, 1)
        self.assertIs(ScriptTask.run_general_battle, GeneralBattle.run_general_battle)
        self.assertIs(ScriptTask.appear_then_click, BaseTask.appear_then_click)

    def test_wrong_page_never_clicks_a_challenge(self):
        obj = task()
        obj.appear = Mock(return_value=False)
        with self.assertRaises(GameStuckError):
            obj._enter_moonlight_battle()
        obj.appear_then_click.assert_not_called()
        obj.O_MOON_RESOURCE.ocr_digit.assert_not_called()

    def test_two_low_resource_reads_stop_without_a_click(self):
        obj = task()
        obj.O_MOON_RESOURCE.ocr_digit.side_effect = [5, 4]
        self.assertFalse(obj._enter_moonlight_battle())
        self.assertEqual(obj.screenshot.call_count, 2)
        obj.appear_then_click.assert_not_called()

    def test_low_first_read_recovers_and_enters_once(self):
        obj = task()
        obj.O_MOON_RESOURCE.ocr_digit.side_effect = [0, 6]
        self.assertTrue(obj._enter_moonlight_battle())
        self.assertEqual(obj.O_MOON_RESOURCE.ocr_digit.call_count, 2)
        obj.appear_then_click.assert_called_once_with(obj.I_MOON_CHALLENGE, interval=0)

    def test_second_resource_read_requires_same_page(self):
        obj = task()
        obj.O_MOON_RESOURCE.ocr_digit.return_value = 0
        obj.appear.side_effect = lambda marker: marker is obj.I_MOON_BATTLE and obj.frame == 1
        with self.assertRaises(GameStuckError):
            obj._enter_moonlight_battle()
        obj.appear_then_click.assert_not_called()
        self.assertEqual(obj.O_MOON_RESOURCE.ocr_digit.call_count, 1)

    def test_failed_challenge_click_does_not_wait_or_repeat(self):
        obj = task()
        obj.appear_then_click.return_value = False
        with self.assertRaises(GameStuckError):
            obj._enter_moonlight_battle()
        obj.detect_page_in.assert_not_called()
        self.assertEqual(obj.appear_then_click.call_count, 1)

    def test_no_entry_times_out_without_reclicking(self):
        obj = task()
        obj.detect_page_in.return_value = None
        with self.assertRaises(GameStuckError):
            obj._enter_moonlight_battle()
        self.assertLess(self.clock.now, 31)
        obj.appear_then_click.assert_called_once_with(obj.I_MOON_CHALLENGE, interval=0)

    def test_unknown_dialog_on_challenge_page_cancels_before_challenge(self):
        obj = task()
        obj.appear.side_effect = lambda marker: marker in (obj.I_MOON_BATTLE, obj.I_UI_CONFIRM)
        self.assertFalse(obj._enter_moonlight_battle())
        obj.appear_then_click.assert_called_once_with(obj.I_UI_CANCEL, interval=0)
        obj.O_MOON_RESOURCE.ocr_digit.assert_not_called()

    def test_resource_purchase_dialog_cancels_even_if_battle_is_behind_it(self):
        obj = task()
        obj.appear.side_effect = lambda marker: (marker is obj.I_MOON_BATTLE
                                                  or (marker is obj.I_UI_CONFIRM and obj.frame > 1))
        obj.O_MOON_REWARD_NOTICE.detect_and_ocr.return_value = [SimpleNamespace(ocr_text='是否购买体力？')]
        self.assertFalse(obj._enter_moonlight_battle())
        self.assertEqual([call.args[0] for call in obj.appear_then_click.call_args_list],
                         [obj.I_MOON_CHALLENGE, obj.I_UI_CANCEL])
        obj.detect_page_in.assert_not_called()

    def test_unknown_dialog_without_cancel_raises(self):
        obj = task()
        obj.appear.side_effect = lambda marker: marker in (obj.I_MOON_BATTLE, obj.I_UI_CONFIRM)
        obj.appear_then_click.return_value = False
        with self.assertRaises(GameStuckError):
            obj._enter_moonlight_battle()
        self.assertEqual([call.args[0] for call in obj.appear_then_click.call_args_list],
                         [obj.I_UI_CANCEL, obj.I_UI_CANCEL_SAMLL])

    def test_reward_cap_is_confirmed_once_and_waits_for_battle(self):
        obj = task()
        obj.appear.side_effect = lambda marker: (marker is obj.I_MOON_BATTLE
                                                  or (marker is obj.I_UI_CONFIRM and 2 <= obj.frame <= 4))
        obj.O_MOON_REWARD_NOTICE.detect_and_ocr.return_value = [
            SimpleNamespace(ocr_text='今日奖励已达获取上限，'),
            SimpleNamespace(ocr_text='是否继续挑战？'),
        ]
        self.assertTrue(obj._enter_moonlight_battle())
        self.assertEqual([call.args[0] for call in obj.appear_then_click.call_args_list],
                         [obj.I_MOON_CHALLENGE, obj.I_UI_CONFIRM])

    def test_partial_reward_notice_never_confirms(self):
        obj = task()
        obj.appear.side_effect = lambda marker: (marker is obj.I_MOON_BATTLE
                                                  or (marker is obj.I_UI_CONFIRM and obj.frame > 1))
        obj.O_MOON_REWARD_NOTICE.detect_and_ocr.return_value = [SimpleNamespace(ocr_text='是否继续挑战？')]
        self.assertFalse(obj._enter_moonlight_battle())
        self.assertNotIn(obj.I_UI_CONFIRM, [call.args[0] for call in obj.appear_then_click.call_args_list])

    def test_generic_reward_is_not_in_entry_detection(self):
        from tasks.GameUi.page import page_reward
        obj = task()
        self.assertTrue(obj._enter_moonlight_battle())
        self.assertNotIn(page_reward, obj.detect_page_in.call_args.args)
        self.assertFalse(obj.detect_page_in.call_args.kwargs['include_global'])

    def test_count_limits_and_local_battle_settings_are_not_mutated(self):
        obj = task()
        obj.conf.general_config.challenge_limit = 2
        original = obj.conf.moonlight_battle_conf
        original.preset_enable = original.lock_team_enable = original.continuous_battle = True
        original.max_continuous = 10
        obj._enter_moonlight_battle = Mock(return_value=True)
        obj.run_general_battle = Mock(side_effect=[False, True])
        obj._battle_shared_state['moonlight'] = object()
        obj.run_moonlight()
        self.assertEqual(obj.moonlight_count, 2)
        self.assertEqual(obj._enter_moonlight_battle.call_count, 2)
        self.assertEqual([call.args[0].preset_enable for call in obj.run_general_battle.call_args_list], [True, False])
        for call in obj.run_general_battle.call_args_list:
            self.assertFalse(call.args[0].lock_team_enable)
            self.assertFalse(call.args[0].continuous_battle)
            self.assertEqual(call.args[0].max_continuous, 0)
            self.assertEqual(call.kwargs, {'battle_key': 'moonlight', 'exit_matcher': obj.I_MOON_BATTLE})
        self.assertTrue(original.continuous_battle)
        self.assertEqual(original.max_continuous, 10)
        self.assertEqual(obj.goto_page.call_count, 4)
        self.assertNotIn('moonlight', obj._battle_shared_state)

    def test_failed_entry_has_no_count_or_battle_call(self):
        obj = task()
        obj._enter_moonlight_battle = Mock(return_value=False)
        obj.run_general_battle = Mock()
        obj.run_moonlight()
        self.assertEqual(obj.moonlight_count, 0)
        obj.run_general_battle.assert_not_called()

    def test_same_instance_rerun_resets_time_and_real_preset_scope(self):
        obj = task()
        obj.conf.moonlight_battle_conf.preset_enable = True
        obj.conf.general_config.challenge_limit = 2
        obj._enter_moonlight_battle = Mock(return_value=True)
        obj.set_next_run = Mock()
        preset = Mock()
        def battle(config, battle_key, **kwargs):
            obj._battle_context = obj._build_context(config, None, battle_key)
            obj._run_battle_behavior_once(behavior_name='preset', action=preset)
            return True
        obj.run_general_battle = Mock(side_effect=battle)
        for _ in range(2):
            obj.start_time = datetime.now() - timedelta(hours=2)
            with self.assertRaises(TaskEnd):
                obj.run()
            self.assertLess(datetime.now() - obj.start_time, timedelta(seconds=1))
            self.assertEqual(obj.moonlight_count, 2)
        self.assertEqual(preset.call_count, 2)
        self.assertEqual(obj.run_general_battle.call_count, 4)

    def test_zero_count_or_expired_time_never_navigates(self):
        for expired in (False, True):
            obj = task()
            if expired:
                obj.start_time -= timedelta(hours=2)
            else:
                obj.conf.general_config.challenge_limit = 0
            obj.run_moonlight()
            obj.goto_page.assert_not_called()

    def test_time_expiring_during_random_sleep_prevents_challenge(self):
        obj = task()
        obj.conf.general_config.random_sleep = True
        obj._enter_moonlight_battle = Mock()
        def expire(**kwargs):
            obj.start_time -= timedelta(hours=2)
        with patch('tasks.Moonlight.activity.random_sleep', side_effect=expire):
            obj.run_moonlight()
        obj._enter_moonlight_battle.assert_not_called()

    def test_failed_return_after_battle_prevents_second_challenge(self):
        obj = task()
        obj.conf.general_config.challenge_limit = 2
        obj.goto_page.side_effect = [True, GamePageUnknownError('return failed')]
        obj._enter_moonlight_battle = Mock(return_value=True)
        obj.run_general_battle = Mock(return_value=True)
        with self.assertRaises(GamePageUnknownError):
            obj.run_moonlight()
        self.assertEqual(obj.moonlight_count, 1)
        obj._enter_moonlight_battle.assert_called_once()
        self.assertIsNone(obj._moon_navigation_deadline)

    def test_navigation_total_deadline_survives_continuing_progress(self):
        obj = task()
        del obj.screenshot
        def keep_navigating(*args, **kwargs):
            while True:
                obj.screenshot()
        obj.goto_page.side_effect = keep_navigating
        with patch.object(BaseTask, 'screenshot', return_value=obj.device.image):
            with self.assertRaises(GamePageUnknownError):
                obj._goto_moonlight(page_moon_battle)
        self.assertLess(self.clock.now, 46)
        self.assertIsNone(obj._moon_navigation_deadline)

    def test_navigation_cancels_overlay_before_exposing_underlying_page(self):
        obj = task()
        del obj.screenshot
        obj._moon_navigation_deadline = 45
        obj._moon_confirmation_visible = Mock(side_effect=[True, True, False])
        with patch.object(BaseTask, 'screenshot', return_value=obj.device.image) as capture:
            self.assertIs(obj.screenshot(), obj.device.image)
        self.assertEqual(capture.call_count, 3)
        obj.appear_then_click.assert_called_once_with(obj.I_UI_CANCEL, interval=0)

    def test_normal_battle_screenshots_are_not_changed(self):
        obj = task()
        del obj.screenshot
        obj._moon_confirmation_visible = Mock()
        with patch.object(BaseTask, 'screenshot', return_value=obj.device.image):
            self.assertIs(obj.screenshot(), obj.device.image)
        obj._moon_confirmation_visible.assert_not_called()

    def test_finish_returns_main_and_only_schedules_own_task(self):
        obj = task()
        obj.run_moonlight = Mock()
        obj.set_next_run = Mock()
        with self.assertRaises(TaskEnd):
            obj.run()
        obj.goto_page.assert_called_once_with(page_main, skip_first_screenshot=False, timeout=20)
        obj.set_next_run.assert_called_once_with(task='Moonlight', success=True)

    def test_soul_cleanup_is_only_scheduled_when_enabled(self):
        obj = task()
        obj.conf.general_config.active_souls_clean = True
        obj.run_moonlight = Mock()
        obj.set_next_run = Mock()
        with self.assertRaises(TaskEnd):
            obj.run()
        self.assertEqual([call.kwargs['task'] for call in obj.set_next_run.call_args_list],
                         ['SoulsTidy', 'Moonlight'])


class MoonlightEntranceTests(unittest.TestCase):
    def test_unknown_page_never_clicks_an_entry(self):
        obj = SimpleNamespace(match_page_once=Mock(return_value=False), appear=Mock(), appear_then_click=Mock())
        self.assertFalse(enter_moonlight(obj))
        obj.appear.assert_not_called()
        obj.appear_then_click.assert_not_called()

    def test_visible_entry_uses_local_click_and_does_not_toggle(self):
        for clicked in (False, True):
            obj = SimpleNamespace(match_page_once=Mock(return_value=True), appear=Mock(return_value=True),
                                  appear_then_click=Mock(return_value=clicked))
            self.assertEqual(enter_moonlight(obj), clicked)
            obj.appear_then_click.assert_called_once_with(MoonlightAssets.I_MOON_ENTRY, interval=0.8)

    def test_missing_entry_only_advances_column(self):
        obj = SimpleNamespace(match_page_once=Mock(return_value=True), appear=Mock(return_value=False),
                              appear_then_click=Mock(return_value=True))
        self.assertFalse(enter_moonlight(obj))
        obj.appear_then_click.assert_called_once_with(RightActivityAssets.I_TOGGLE_BUTTON, interval=2)

    def test_pages_connect_main_lobby_and_challenge(self):
        edge = next(edge for edge in page_main.transitions if edge.key == 'main->moon_lobby')
        self.assertIs(edge.action, enter_moonlight)
        self.assertIs(edge.destination, page_moon_lobby)
        self.assertTrue(any(edge.destination is page_moon_battle for edge in page_moon_lobby.transitions))
        self.assertTrue(any(edge.destination is page_moon_lobby for edge in page_moon_battle.transitions))


if __name__ == '__main__':
    unittest.main()
