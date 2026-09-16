"""Real disconnect-dialog replay and battle/scheduler recovery without a device."""

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import cv2
import numpy as np

from test_image_template_guard import methods
from test_raid_transitions import World, frame, BATTLE, PREPARE, RESULT, REWARD


ROOT = Path(__file__).resolve().parents[1]
BATTLE_SOURCE = 'tasks/Component/GeneralBattle/general_battle.py'


class Disconnected(Exception):
    pass


def popup_frame():
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    path = ROOT / 'dev_tools/fixtures/battle_disconnect/2026-09-16_server_disconnect.png'
    popup = cv2.imread(str(path))
    assert popup is not None and popup.shape == (208, 416, 3)
    image[253:461, 431:847] = popup
    return image


def network_rule():
    cls = methods('module/atom/image.py', 'RuleImage',
                  ['corp', '_template_image_invalid', 'template_match', '_update_roi_front'],
                  dict(cv2=cv2, np=np, logger=Mock()))
    rule = cls()
    rule.roi_front = [431, 253, 416, 208]
    rule.roi_back = tuple(rule.roi_front)
    rule.threshold = 0.8
    rule.debug_mode = False
    rule.image = cv2.imread(str(ROOT / 'tasks/GlobalGame/gg/gg_network_error.png'))
    assert rule.image is not None
    return rule


def install_guard(task):
    cls = methods(BATTLE_SOURCE, 'GeneralBattle', ['_check_battle_connection'],
                  dict(cv2=cv2, GameStuckError=Disconnected))
    task._check_battle_connection = cls._check_battle_connection.__get__(task)
    task.I_NETWORK_ERROR = network_rule()
    old_appear = getattr(task, 'appear', Mock(return_value=False))
    task.appear = lambda marker: (marker.template_match(task.device.image)
                                  if marker is task.I_NETWORK_ERROR else old_appear(marker))


class DisconnectVisionTests(unittest.TestCase):
    def setUp(self):
        self.task = SimpleNamespace(device=SimpleNamespace(image=popup_frame()))
        install_guard(self.task)

    def test_actual_error_popup_raises_restartable_error(self):
        with self.assertRaisesRegex(Disconnected, 'server disconnected'):
            self.task._check_battle_connection()

    def test_shared_scroll_frame_without_disconnect_words_is_not_enough(self):
        self.task.device.image[309:347, 437:840] = (205, 185, 159)
        # Prove the old whole-dialog match alone would be a false positive.
        self.assertTrue(self.task.I_NETWORK_ERROR.template_match(self.task.device.image))
        self.task._check_battle_connection()

    def test_disconnect_words_without_the_dialog_are_not_enough(self):
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        image[309:347, 437:840] = self.task.device.image[309:347, 437:840]
        self.task.device.image = image
        self.task._check_battle_connection()

    def test_blank_and_transient_connecting_frames_are_not_disconnects(self):
        self.task.device.image[:] = 0
        self.task._check_battle_connection()
        connecting = cv2.imread(str(ROOT / 'tasks/GlobalGame/gg/gg_network_abnormal.png'))
        h, w = connecting.shape[:2]
        self.task.device.image[330:330+h, 583:583+w] = connecting
        self.task._check_battle_connection()


class DisconnectBattleTests(unittest.TestCase):
    def world(self, frames, disconnect_at):
        world = World(frames, kind='battle')
        normal = np.zeros((720, 1280, 3), dtype=np.uint8)
        world.task.device.image = normal
        install_guard(world.task)
        old_screenshot = world.task.screenshot
        def screenshot():
            old_screenshot()
            world.task.device.image = popup_frame() if world.index >= disconnect_at else normal
        world.task.screenshot = screenshot
        return world

    def test_disconnect_prevents_exit_or_confirmation_at_every_exit_stage(self):
        frames = [frame(BATTLE, 'I_EXIT'), frame(BATTLE, 'I_EXIT'),
                  frame(None, 'I_EXIT_ENSURE'), frame(None, 'I_EXIT_ENSURE')]
        for at, clicks in ((0, []), (1, []), (2, ['I_EXIT']),
                           (3, ['I_EXIT', 'I_EXIT_ENSURE'])):
            with self.subTest(disconnect_frame=at):
                world = self.world(frames, at)
                with self.assertRaises(Disconnected):
                    world.task.exit_battle()
                self.assertEqual(world.clicks, clicks)
                self.assertEqual(world.index, at)

    def test_disconnect_precedes_false_settlement_background(self):
        for page in (RESULT, REWARD):
            with self.subTest(page=page):
                world = self.world([frame(page, 'I_EXIT_ENSURE')], 0)
                with self.assertRaises(Disconnected):
                    world.task.exit_battle()
                self.assertEqual(world.clicks, [])

    def test_skip_first_still_checks_existing_disconnect_popup(self):
        world = self.world([frame(BATTLE, 'I_EXIT')], 0)
        world.task.screenshot()
        with self.assertRaises(Disconnected):
            world.task.exit_battle(skip_first=True)
        self.assertEqual(world.index, 0)
        self.assertEqual(world.clicks, [])

    def test_main_battle_loop_restarts_before_timer_refresh_or_background_actions(self):
        detect = Mock(return_value=BATTLE)
        cls = methods(BATTLE_SOURCE, 'GeneralBattle', ['run_general_battle'],
                      dict(logger=Mock(), GameUi=SimpleNamespace(detect_page_in=detect),
                           page_battle_prepare=PREPARE, page_battle=BATTLE,
                           page_battle_result=RESULT, page_reward=REWARD))
        task = cls()
        task._custom_pages_registered = True
        task.current_count = 0
        task.device = SimpleNamespace(image=popup_frame(), stuck_record_add=Mock(),
                                      click_record_clear=Mock(), screenshot_interval_set=Mock())
        task.screenshot = Mock()
        task._build_context = Mock(return_value=SimpleNamespace())
        task._exit_matcher = Mock(return_value=None)
        task._tick_long_battle = Mock()
        task._tick_timeout = Mock()
        task._resolve_action = Mock()
        install_guard(task)
        with self.assertRaises(Disconnected):
            task.run_general_battle(config=SimpleNamespace())
        task.screenshot.assert_called_once()
        task._tick_long_battle.assert_not_called()
        task._tick_timeout.assert_not_called()
        task._resolve_action.assert_not_called()
        detect.assert_not_called()
        self.assertIsNone(task._battle_context)
        task.device.screenshot_interval_set.assert_called_once_with()


class DisconnectSchedulerTests(unittest.TestCase):
    def test_disconnect_schedules_restart_without_daily_failure_delay(self):
        namespace = dict(logger=Mock(), GameStuckError=Disconnected,
                         I18n=SimpleNamespace(trans_zh_cn=lambda value: value))
        for name in ('TaskEnd', 'ActivityPreparationTimeout', 'BattleTransitionTimeout',
                     'GameNotRunningError', 'GameTooManyClickError'):
            namespace[name] = type(name, (Exception,), {})
        cls = methods('script.py', 'Script', ['_handle_task_exception'], namespace)
        task = cls()
        task.config_name = 'offline'
        task.config = SimpleNamespace(task_call=Mock(), task_delay=Mock(),
                                      notifier=SimpleNamespace(push=Mock()))
        task.device = SimpleNamespace(package='offline.game', sleep=Mock())
        task.save_error_log = Mock()
        task.exception_handler = Mock()
        task._set_task_runtime_outcome = Mock()
        error = Disconnected('server disconnected')
        self.assertFalse(task._handle_task_exception(error, 'ActivityShikigami'))
        task.config.task_call.assert_called_once_with('Restart')
        task.config.task_delay.assert_not_called()
        task._set_task_runtime_outcome.assert_not_called()
        task.save_error_log.assert_called_once()
        task.exception_handler.assert_called_once_with(e=error, command='ActivityShikigami')


if __name__ == '__main__':
    unittest.main()
