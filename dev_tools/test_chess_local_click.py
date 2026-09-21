"""Offline checks for local random clicks in Chess and town navigation."""

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


class ChessHandLocalClickTests(unittest.TestCase):
    def task(self, boxes):
        runtime = methods(
            'tasks/Chess/runtime/hand_operations.py', 'ChessHandOperationsMixin',
            ['_discover_named_hand_cards', 'discover_souls_from_hand'], {
                'RuleClick': RuleClick, 'logger': Mock(),
                'time': SimpleNamespace(monotonic=lambda: 0, sleep=Mock()),
            })
        task = runtime()
        attach_local_click(task)
        results = [SimpleNamespace(ocr_text='发现纹章', box=box, score=0.99)
                   for box in boxes]
        task.O_BADGE_AREA = SimpleNamespace(
            roi=(100, 600, 300, 60), detect_and_ocr=Mock(return_value=results))
        task._normalize_ocr_text = lambda value: value
        return task

    def cards(self, task):
        return task._discover_named_hand_cards('发现纹章', allow_fuzzy=False)

    def test_discover_hand_uses_local_random_point_inside_ocr_box(self):
        box = [[20, 10], [100, 10], [100, 30], [20, 30]]
        task = self.task([box])
        cards = self.cards(task)
        self.assertEqual(cards[0]['click_roi'], (140, 615, 40, 10))
        self.assertEqual(cards[0]['position'], (160, 620))
        original_ocr_roi = task.O_BADGE_AREA.roi
        task._discover_badge_hand_cards = lambda: cards
        task._discover_soul_hand_cards = lambda: []
        task._is_preparation_mode = Mock(side_effect=[True, False])
        task.DISCOVER_SOUL_SAFETY_LIMIT = 1
        task.DISCOVER_SOUL_UI_TIMEOUT = 2
        task.SCREENSHOT_INTERVAL = 0.1
        task.screenshot = Mock()
        with patch('module.atom.click.monte_carlo_click_point',
                   return_value=(145, 617)) as sampler:
            self.assertEqual(task.discover_souls_from_hand(), 0)
        sampler.assert_called_once_with((140, 615, 40, 10))
        task.device.click.assert_called_once_with(
            x=145, y=617, control_name='CHESS_DISCOVER_CARD')
        self.assertIs(task.O_BADGE_AREA.roi, original_ocr_roi)
        self.assertEqual(box, [[20, 10], [100, 10], [100, 30], [20, 30]])

    def test_edge_and_single_pixel_boxes_keep_positive_bounded_click_regions(self):
        task = self.task([
            [[-5, -3], [20, -3], [20, 15], [-5, 15]],
            [[295, 55], [310, 55], [310, 70], [295, 70]],
            [[30, 10], [31, 10], [31, 11], [30, 11]],
        ])
        cards = self.cards(task)
        self.assertEqual(len(cards), 3)
        for card in cards:
            x, y, width, height = card['click_roi']
            self.assertGreater(width, 0)
            self.assertGreater(height, 0)
            self.assertGreaterEqual(x, 100)
            self.assertGreaterEqual(y, 600)
            self.assertLessEqual(x + width, 400)
            self.assertLessEqual(y + height, 660)
            click_rule = RuleClick(card['click_roi'], card['click_roi'])
            for _ in range(20):
                px, py = click_rule.coord()
                self.assertTrue(x <= px < x + width)
                self.assertTrue(y <= py < y + height)

    def test_degenerate_or_outside_ocr_boxes_do_not_produce_click_targets(self):
        task = self.task([
            [[10, 10], [10, 10], [10, 20], [10, 20]],
            [[10, 10], [20, 10], [20, 10], [10, 10]],
            [[310, 10], [320, 10], [320, 20], [310, 20]],
            [[-20, 10], [-10, 10], [-10, 20], [-20, 20]],
        ])
        self.assertEqual(self.cards(task), [])


if __name__ == '__main__':
    unittest.main()
