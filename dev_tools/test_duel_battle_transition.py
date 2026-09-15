"""Replay fast duel results without a device, RPC service or runtime config."""

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import cv2
import numpy as np

from test_image_template_guard import methods

ROOT = Path(__file__).resolve().parents[1]


def victory_frame(name):
    crop = cv2.imread(str(ROOT / 'dev_tools/fixtures/duel' / name))
    assert crop is not None, name
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    image[40:225, 350:1010] = crop
    return image


class World:
    def __init__(self, frames, seconds_per_frame=1):
        self.frames = frames
        self.index = -1
        self.seconds_per_frame = seconds_per_frame
        self.now = 1000
        timer = methods('module/base/timer.py', 'Timer',
                        ['__init__', 'start', 'started', 'current', 'reached',
                         'reset', 'reached_and_reset'],
                        dict(time=SimpleNamespace(time=lambda: self.now)))
        subject = methods('tasks/Duel/script_task.py', 'ScriptTask',
                          ['wait_battle', 'is_battle_win', 'is_battle_lose'],
                          dict(Timer=timer, logger=Mock(), page_duel='duel',
                               random_click=Mock(return_value='safe_continue')))
        self.task = subject()
        for name in ('I_CHECK_DUEL', 'I_D_HELP', 'I_D_WIN_SHARE', 'I_UI_BACK_RED',
                     'I_WIN', 'I_D_VICTORY', 'I_FALSE', 'I_D_FAIL',
                     'O_BATTLE_AUTO', 'O_BATTLE_HAND'):
            setattr(self.task, name, name)
        self.task.screenshot = Mock(side_effect=self.advance)
        self.task.appear = Mock(side_effect=self.appear)
        self.task.appear_then_click = Mock(return_value=False)
        self.task.ocr_appear_click = Mock(side_effect=self.ocr_click)
        self.task.click = Mock()
        self.task.check_and_get_reward = Mock()
        self.task.is_in_real_battle = Mock(side_effect=lambda **kw: 'battle' in self.markers())
        self.task.green_mark = Mock()
        self.task.reset_device = Mock()
        self.task.goto_page = Mock()
        self.task.duel_exit_battle = Mock()
        self.task.ui_click = Mock(side_effect=AssertionError('nested mode wait must not run'))
        self.task.conf = SimpleNamespace(duel_config=SimpleNamespace(green_enable=True,
                                                                   green_mark='left2'))
        # Execute the real local template matcher, not a mocked victory result.
        rule = methods('module/atom/image.py', 'RuleImage',
                       ['corp', '_template_image_invalid', 'template_match', '_update_roi_front'],
                       dict(cv2=cv2, np=np, logger=Mock()))
        self.rule = rule()
        self.rule.roi_front = [433, 76, 100, 100]
        self.rule.roi_back = (433, 76, 100, 100)
        self.rule.image = cv2.imread(str(ROOT / 'tasks/Duel/duel/duel_d_victory.png'))
        self.rule.threshold = 0.8
        self.rule.debug_mode = False

    def advance(self):
        self.index += 1
        self.now += self.seconds_per_frame
        if self.index >= len(self.frames):
            raise AssertionError('wait_battle failed to return after the supplied frames')

    def markers(self):
        frame = self.frames[self.index]
        return frame if isinstance(frame, set) else set()

    def appear(self, marker, **kwargs):
        frame = self.frames[self.index]
        if isinstance(frame, np.ndarray) and marker == 'I_D_VICTORY':
            return self.rule.template_match(frame)
        return marker in self.markers()

    def ocr_click(self, marker, **kwargs):
        if self.appear(marker):
            self.task.click(marker, **kwargs)
            return True
        return False

    def run(self):
        return self.task.wait_battle()


MAIN = {'I_CHECK_DUEL', 'I_D_HELP'}
MANUAL = {'battle', 'O_BATTLE_HAND'}
AUTO = {'battle', 'O_BATTLE_AUTO'}


class DuelBattleTransitionTests(unittest.TestCase):
    def test_both_logged_victories_match_without_any_mode_ocr(self):
        for filename in ('victory_1544.png', 'victory_1804.png'):
            with self.subTest(filename=filename):
                world = World([victory_frame(filename), MAIN])
                self.assertTrue(world.run())
                world.task.ocr_appear_click.assert_not_called()
                world.task.green_mark.assert_not_called()
                world.task.click.assert_called_once_with('safe_continue', interval=1.2)

    def test_fast_victory_after_manual_click_returns_to_result_detection(self):
        world = World([MANUAL, victory_frame('victory_1544.png'), MAIN])
        self.assertTrue(world.run())
        self.assertEqual([c.args[0] for c in world.task.click.call_args_list],
                         ['O_BATTLE_HAND', 'safe_continue'])
        world.task.green_mark.assert_not_called()
        world.task.reset_device.assert_not_called()

    def test_loading_frames_and_missing_mode_text_do_not_hide_result(self):
        world = World([set(), {'battle'}, set(), victory_frame('victory_1804.png'), MAIN])
        self.assertTrue(world.run())
        world.task.ocr_appear_click.assert_called_once_with('O_BATTLE_HAND', interval=0.8)
        world.task.click.assert_called_once_with('safe_continue', interval=1.2)
        world.task.reset_device.assert_not_called()

    def test_normal_auto_switch_and_mark_happen_once_after_auto_confirmation(self):
        world = World([MANUAL, AUTO, AUTO, victory_frame('victory_1544.png'), MAIN])
        self.assertTrue(world.run())
        world.task.green_mark.assert_called_once_with(True, 'left2')
        world.task.reset_device.assert_called_once_with('BATTLE_STATUS_S')
        world.task.ocr_appear_click.assert_called_once_with('O_BATTLE_HAND', interval=0.8)

    def test_already_auto_battle_does_not_click_manual(self):
        world = World([AUTO, victory_frame('victory_1804.png'), MAIN])
        self.assertTrue(world.run())
        world.task.ocr_appear_click.assert_not_called()
        world.task.green_mark.assert_called_once()

    def test_manual_text_outside_battle_is_not_clicked(self):
        world = World([{'O_BATTLE_HAND'}, victory_frame('victory_1544.png'), MAIN])
        self.assertTrue(world.run())
        world.task.ocr_appear_click.assert_not_called()
        world.task.click.assert_called_once_with('safe_continue', interval=1.2)

    def test_loss_during_mode_switch_stays_a_loss(self):
        world = World([MANUAL, {'I_D_FAIL'}, MAIN])
        self.assertFalse(world.run())
        world.task.green_mark.assert_not_called()

    def test_mode_text_absence_keeps_outer_timeout_active(self):
        world = World([{'battle'}] * 5 + [MAIN], seconds_per_frame=300)
        self.assertIsNone(world.run())
        world.task.duel_exit_battle.assert_called()
        world.task.green_mark.assert_not_called()

    def test_mode_attempts_keep_outer_timeout_active(self):
        world = World([MANUAL] * 5 + [MAIN], seconds_per_frame=300)
        self.assertIsNone(world.run())
        world.task.duel_exit_battle.assert_called()
        world.task.green_mark.assert_not_called()


if __name__ == '__main__':
    unittest.main()
