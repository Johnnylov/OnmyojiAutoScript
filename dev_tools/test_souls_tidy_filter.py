"""Offline regressions for the obscured soul-disposal filter and safe retries."""

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import cv2
import numpy as np

from test_image_template_guard import methods


ROOT = Path(__file__).resolve().parents[1]


class TaskEnd(Exception):
    pass


class FrameTimer:
    def __init__(self, seconds):
        self.frames = 20

    def start(self):
        return self

    def started(self):
        return False

    def reached(self):
        self.frames -= 1
        return self.frames < 0


class SoulsTidyFilterTests(unittest.TestCase):
    def setUp(self):
        cls = methods('tasks/SoulsTidy/script_task.py', 'ScriptTask',
                      ['abandoned_selected', 'ensure_abandoned_selected',
                       'defer_disposal', 'require_abandoned_selected',
                       'greed_maneki', 'find_discard_souls'],
                      dict(cv2=cv2, Timer=FrameTimer, logger=Mock(), TaskEnd=TaskEnd))
        self.task = cls()
        self.template = cv2.cvtColor(cv2.imread(str(
            ROOT / 'tasks/SoulsTidy/simple/simple_st_abandoned_selected.png')),
            cv2.COLOR_BGR2RGB)
        self.task.I_ST_ABANDONED_SELECTED = SimpleNamespace(
            image=self.template, corp=lambda image: image[89:176, 15:154])
        self.task.I_ST_CAT = 'cat'
        self.task.I_UI_BACK_RED = 'chat-back'
        self.task.C_ST_ABANDONED_TAB = 'safe-tab'
        self.task.I_ST_SOUL_STACK = 'stack'
        self.task.I_ST_SOUL_STACK_1 = 'stack-one'
        self.task.I_ST_LEVEL_0 = 'level-zero'
        self.task.L_ONE = 'select-soul'
        self.task.I_ST_DONATE = 'donate'
        self.task.appear = Mock(return_value=False)
        self.task.click = Mock(return_value=True)
        self.task.screenshot = Mock()
        self.task.set_next_run = Mock()
        self.task.device = SimpleNamespace(image=self.frame())

    def frame(self):
        frame = np.full((720, 1280, 3), (61, 43, 35), dtype=np.uint8)
        frame[112:164, 32:139] = self.template
        return frame

    def test_right_hand_notification_does_not_hide_selected_tab(self):
        self.task.device.image[111:181, 110:164] = (230, 220, 210)
        source = self.task.I_ST_ABANDONED_SELECTED.corp(self.task.device.image)
        old_score = cv2.minMaxLoc(cv2.matchTemplate(
            source, self.template, cv2.TM_CCOEFF_NORMED))[1]
        self.assertLess(old_score, 0.8)
        self.assertTrue(self.task.abandoned_selected())

    def test_text_without_selected_highlight_is_rejected(self):
        unselected = self.template.copy()
        hsv = cv2.cvtColor(unselected, cv2.COLOR_RGB2HSV)
        highlight = ((hsv[:, :, 0] > 5) & (hsv[:, :, 0] < 45)
                     & (hsv[:, :, 1] > 50) & (hsv[:, :, 2] > 80))
        unselected[highlight] = (80, 70, 67)
        self.task.device.image[112:164, 32:139] = unselected
        self.assertFalse(self.task.abandoned_selected())

    def test_invalid_or_wrong_region_is_rejected(self):
        for frame in (np.zeros((20, 20, 3), dtype=np.uint8),
                      np.zeros((720, 1280, 3), dtype=np.uint8)):
            self.task.device.image = frame
            self.assertFalse(self.task.abandoned_selected())

    def test_supplied_error_frames_recognize_selected_filter(self):
        folders = ('oas2_1789353004523', 'oas2_1789353099064', 'oas2_1789353185402')
        frames = [path for folder in folders
                  for path in (ROOT / 'log/error' / folder).glob('*.png')]
        if not frames:
            self.skipTest('Local error screenshots have been cleaned up')
        for path in frames:
            with self.subTest(frame=path.name):
                self.task.device.image = cv2.cvtColor(cv2.imread(str(path)), cv2.COLOR_BGR2RGB)
                self.assertTrue(self.task.abandoned_selected())

    def test_already_selected_obscured_tab_needs_no_click(self):
        self.task.device.image[111:181, 110:164] = (230, 220, 210)
        self.task.appear.side_effect = lambda rule: rule == 'cat'
        self.assertTrue(self.task.ensure_abandoned_selected())
        self.task.click.assert_not_called()

    def test_unconfirmed_filter_has_at_most_three_safe_clicks(self):
        self.task.device.image[:] = 0
        self.task.appear.side_effect = lambda rule: rule == 'cat'
        self.assertFalse(self.task.ensure_abandoned_selected())
        self.assertEqual(self.task.click.call_count, 3)
        self.task.click.assert_called_with('safe-tab', interval=1.5)

    def test_unknown_page_never_receives_tab_clicks(self):
        self.assertFalse(self.task.ensure_abandoned_selected())
        self.task.click.assert_not_called()

    def test_chat_is_closed_before_filter_detection_or_clicks(self):
        visible = {'chat-back'}
        self.task.appear.side_effect = lambda rule: rule in visible if isinstance(rule, str) else False

        def close_chat(rule, interval):
            self.assertEqual(rule, 'chat-back')
            visible.clear()
            visible.add('cat')
            return True

        self.task.click.side_effect = close_chat
        self.assertTrue(self.task.ensure_abandoned_selected())
        self.task.click.assert_called_once_with('chat-back', interval=0.8)

    def test_lost_filter_defers_without_donation(self):
        self.task.device.image[:] = 0
        self.task.appear.side_effect = lambda rule: rule == 'cat'
        with self.assertRaises(TaskEnd):
            self.task.require_abandoned_selected()
        self.task.click.assert_not_called()
        self.task.set_next_run.assert_called_once_with(
            task='SoulsTidy', success=False, finish=True)

    def prepare_donation(self):
        self.task.config = SimpleNamespace(souls_tidy=SimpleNamespace(
            simple_tidy=SimpleNamespace(enable_greed=False, enable_maneki=True)))
        self.task.appear.side_effect = lambda rule: rule == 'cat'
        self.task.pre_confirm = Mock()
        self.task.O_ST_GOLD = SimpleNamespace(ocr=Mock(return_value=1000))
        self.task.O_ST_FIRST_LEVEL = SimpleNamespace(ocr=Mock(return_value=''))
        self.task.donate_and_collect_reward = Mock()

    def test_empty_abandoned_list_finishes_without_selecting_or_donating(self):
        self.prepare_donation()
        self.task.greed_maneki()
        self.task.click.assert_not_called()
        self.task.donate_and_collect_reward.assert_not_called()
        self.task.set_next_run.assert_not_called()

    def test_lost_filter_blocks_even_a_visible_stack(self):
        self.task.device.image[:] = 0
        self.task.appear.side_effect = lambda rule: rule in ('cat', 'stack')
        with self.assertRaises(TaskEnd):
            self.task.find_discard_souls()
        self.task.click.assert_not_called()

    def test_failed_filter_selection_does_not_sort_select_or_donate(self):
        self.prepare_donation()
        self.task.ensure_abandoned_selected = Mock(return_value=False)
        with self.assertRaises(TaskEnd):
            self.task.greed_maneki()
        self.task.pre_confirm.assert_not_called()
        self.task.click.assert_not_called()
        self.task.donate_and_collect_reward.assert_not_called()

    def test_filter_is_rechecked_after_selecting_souls(self):
        self.prepare_donation()
        self.task.ensure_abandoned_selected = Mock(return_value=True)
        self.task.find_discard_souls = Mock(return_value=True)
        self.task.click.side_effect = lambda *args, **kwargs: self.task.device.image.fill(0)
        with self.assertRaises(TaskEnd):
            self.task.greed_maneki()
        self.task.click.assert_called_once_with('select-soul', interval=2.5)
        self.task.O_ST_GOLD.ocr.assert_not_called()
        self.task.donate_and_collect_reward.assert_not_called()


if __name__ == '__main__':
    unittest.main()
