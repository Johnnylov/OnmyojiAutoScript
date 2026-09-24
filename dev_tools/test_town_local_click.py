"""Offline checks for local random clicks in town navigation."""

from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from module.atom.click import RuleClick
from test_image_template_guard import methods


class ImageRule:
    def __init__(self, roi):
        self.roi_front = list(roi)
        self.name = 'town_target'


class GifRule(ImageRule):
    pass


def attach_local_click(task):
    # Exercise the real BaseTask -> RuleClick.coord -> local sampler path.
    base = methods('tasks/base_task.py', 'BaseTask', ['click'], {
        'RuleClick': RuleClick, 'RuleImage': ImageRule,
        'RuleLongClick': type('LongClick', (), {}),
        'RuleOcr': type('Ocr', (), {}),
    })
    task.click = MethodType(base.click, task)
    task.device = SimpleNamespace(click=Mock(), image=None)


class TownLocalClickTests(unittest.TestCase):
    def task(self, source_key='page_town', confirmed=True, roi=(201, 144, 35, 26)):
        class ExpiredTimer:
            def __init__(self, limit):
                pass
            def start(self):
                return self
            def reached(self):
                return True

        runtime = methods('tasks/GameUi/navigator.py', 'GameUi',
                          ['_execute_transition'], {
            'Timer': ExpiredTimer, 'RuleImage': ImageRule, 'RuleGif': GifRule,
            'RuleClick': RuleClick, 'logger': Mock(),
        })
        task = runtime()
        attach_local_click(task)
        task.confirm_page = Mock(return_value=confirmed)
        task._action_name = lambda rule: rule.name
        task._run_hooks = Mock()
        task._wait_for_destination = Mock(return_value=True)
        task._mark_page_entered = Mock()
        task._detect_current_page_with_fallback = Mock(return_value=None)
        task._navigation_detect_categories = Mock(return_value=[])
        task.navigator = SimpleNamespace(current_page=None,
                                         add_penalty=Mock(return_value=1))
        source = SimpleNamespace(key=source_key, on_leave_success=[],
                                 on_leave_failure=[])
        destination = SimpleNamespace(on_enter_success=[], on_enter_failure=[])
        transition = SimpleNamespace(
            key='town_edge', source=source, destination=destination,
            action=ImageRule(roi), on_leave_success=[], on_leave_failure=[],
            on_enter_success=[], on_enter_failure=[])
        return task, transition

    def test_town_fallback_uses_off_center_local_point_without_mutating_template(self):
        task, edge = self.task()
        original_roi = edge.action.roi_front
        with patch('module.atom.click.monte_carlo_click_point',
                   return_value=(205, 147)) as sampler:
            self.assertTrue(task._execute_transition(edge))
        sampler.assert_called_once_with((201, 144, 35, 26))
        task.device.click.assert_called_once_with(
            x=205, y=147, control_name='TOWN_FALLBACK_town_target')
        self.assertIs(edge.action.roi_front, original_roi)
        self.assertEqual(original_roi, [201, 144, 35, 26])

    def test_fallback_requires_reconfirmed_town_page(self):
        for source_key, confirmed in [('page_town', False), ('page_main', True)]:
            with self.subTest(source=source_key, confirmed=confirmed):
                task, edge = self.task(source_key=source_key, confirmed=confirmed)
                self.assertFalse(task._execute_transition(edge))
                task.device.click.assert_not_called()

    def test_empty_or_negative_target_region_is_not_clicked(self):
        for roi in [(0, 0, 0, 0), (201, 144, 0, 26), (201, 144, 35, -1)]:
            with self.subTest(roi=roi):
                task, edge = self.task(roi=roi)
                edge.action = GifRule(roi)
                self.assertFalse(task._execute_transition(edge))
                task.device.click.assert_not_called()


if __name__ == '__main__':
    unittest.main()
