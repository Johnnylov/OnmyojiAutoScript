"""Offline regression for delayed preset confirmation, using a virtual clock."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from test_image_template_guard import methods


class PresetConfirmationTests(unittest.TestCase):
    def task(self, frames, clicked=True):
        clock = SimpleNamespace(now=0.0, frame=-1, timers=[])
        class Timer:
            def __init__(self, limit):
                self.limit = limit
                clock.timers.append(self)
            def start(self):
                self.started = clock.now
                return self
            def reached(self):
                return clock.now - self.started >= self.limit
        logger = Mock()
        def sleep(seconds):
            clock.now += seconds
        cls = methods('tasks/Component/GeneralBattle/general_battle.py', 'GeneralBattle',
                      ['switch_preset_team'], dict(Timer=Timer, logger=logger,
                       time=SimpleNamespace(sleep=sleep), get_color=Mock(),
                       color_similar=lambda *_: False))
        task = cls()
        for name in ('I_PRESET_ENSURE', 'I_PRESENT_LESS_THAN_5', 'I_PRESET',
                     'I_PRESET_WIT_NUMBER', 'O_PRESET', 'O_PRESET_FULL'):
            setattr(task, name, name)
        task.C_PRESET_GROUP_1 = SimpleNamespace(roi_back=(10, 10, 20, 20))
        task.C_PRESET_TEAM_1 = SimpleNamespace(roi_back=(30, 30, 20, 20))
        task.device = SimpleNamespace(image=None)
        def screenshot():
            clock.now += 0.25
            if len(clock.timers) == 4:
                clock.frame += 1
        task.screenshot = Mock(side_effect=screenshot)
        def appear(marker):
            if marker != 'I_PRESET_ENSURE':
                return False
            if len(clock.timers) < 4:
                return True
            return frames[min(clock.frame, len(frames) - 1)]
        task.appear = Mock(side_effect=appear)
        task.click = Mock(return_value=True)
        task.appear_then_click = Mock(side_effect=lambda marker, **_: appear(marker) and clicked)
        return task, clock, logger

    def test_initially_missing_confirm_is_waited_for_and_clicked_before_success(self):
        task, clock, logger = self.task([False, False, True, False])
        task.switch_preset_team(enable=True)
        self.assertEqual(clock.frame, 3)
        self.assertEqual(task.appear_then_click.call_count, 3)
        task.appear_then_click.assert_called_with('I_PRESET_ENSURE', interval=1)
        logger.info.assert_any_call('Click preset ensure success')
        logger.warning.assert_not_called()

    def test_confirm_already_visible_must_disappear_after_click(self):
        task, clock, logger = self.task([True, True, False])
        task.switch_preset_team(enable=True)
        self.assertEqual(clock.frame, 2)
        self.assertEqual(task.appear_then_click.call_count, 2)
        logger.info.assert_any_call('Click preset ensure success')
        logger.warning.assert_not_called()

    def test_confirmation_never_appearing_stops_at_bounded_timeout(self):
        task, clock, logger = self.task([False])
        task.switch_preset_team(enable=True)
        self.assertGreaterEqual(clock.now - clock.timers[-1].started, 3)
        self.assertLessEqual(clock.frame, 12)
        logger.warning.assert_called_once_with('Switch preset timeout, use current team')
        self.assertNotIn(('Click preset ensure success',), [call.args for call in logger.info.call_args_list])

    def test_throttled_click_does_not_report_false_success(self):
        task, clock, logger = self.task([True, False], clicked=False)
        task.switch_preset_team(enable=True)
        logger.warning.assert_called_once_with('Switch preset timeout, use current team')
        self.assertNotIn(('Click preset ensure success',), [call.args for call in logger.info.call_args_list])

    def test_disabled_preset_does_not_touch_the_screen(self):
        task, clock, logger = self.task([True])
        task.switch_preset_team(enable=False)
        task.screenshot.assert_not_called()
        task.click.assert_not_called()
        task.appear_then_click.assert_not_called()
        self.assertEqual(clock.timers, [])


if __name__ == '__main__':
    unittest.main()
