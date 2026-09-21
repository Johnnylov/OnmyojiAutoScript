"""Offline regression coverage for the weekly touch-fish save flow."""

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]


class FakeClock:
    def __init__(self):
        self.now = 1.0

    def sleep(self, duration):
        self.now += duration


class TouchFishHarness:
    def __init__(self, clock, **options):
        self.clock = clock
        self.options = options
        self.device = SimpleNamespace(image=object())
        self.pages = []
        self.clicks = []
        self.save_attempts = 0
        self.ticket_reads = 0
        self.cost_reads = 0
        self.save_at = None
        self.confirm_at = None
        self.happy_dismissed = False
        self.phase_at = clock.now
        self.phase = None
        for name in (
            'I_WT_OPEN_FOLD_WINDOW', 'I_WT_FOLD_WINDOW', 'I_WT_LAST_SAVE',
            'I_WT_HAPPY_GET', 'I_WT_SAVE_ALL', 'I_WT_TF_CONFIRM',
            'I_WT_TF_SAVE_SUCCESS',
        ):
            setattr(self, name, name)
        self.O_WT_LUCKY_TICKETS = SimpleNamespace(ocr=self.read_tickets)
        self.O_WT_SAVE_COST = SimpleNamespace(ocr=self.read_cost)

    def goto_page(self, page):
        self.pages.append(page)
        self.phase = page
        self.phase_at = self.clock.now

    def screenshot(self):
        pass

    def read_tickets(self, image):
        self.ticket_reads += 1
        return self.options.get('tickets', (2, 8, 10))

    def read_cost(self, image):
        self.cost_reads += 1
        cost = self.options.get('cost', 3)
        if isinstance(cost, list):
            return cost[min(self.cost_reads - 1, len(cost) - 1)]
        return cost

    def appear(self, rule):
        elapsed = self.clock.now - self.phase_at
        if self.phase == 'guild':
            fold_after = self.options.get('fold_after', 0)
            folded = fold_after is None or elapsed < fold_after
            return (
                rule == self.I_WT_OPEN_FOLD_WINDOW and folded
                or rule == self.I_WT_FOLD_WINDOW and not folded
            )
        if self.phase != 'fish':
            return False
        last_save = elapsed < self.options.get('last_save_until', 0)
        happy_get = self.options.get('happy_get', False) and not self.happy_dismissed
        if rule == self.I_WT_LAST_SAVE:
            return last_save
        if rule == self.I_WT_HAPPY_GET:
            return happy_get
        if last_save or happy_get:
            return False
        if rule == self.I_WT_SAVE_ALL:
            return (
                not self.options.get('already_saved', False)
                and elapsed >= self.options.get('ready_after', 0)
                and self.confirm_at is None
            )
        if rule == self.I_WT_TF_CONFIRM:
            return (
                self.save_at is not None
                and self.clock.now - self.save_at >= self.options.get('confirm_delay', 0.5)
                and self.confirm_at is None
            )
        if rule == self.I_WT_TF_SAVE_SUCCESS:
            return self.options.get('already_saved', False) or (
                self.confirm_at is not None
                and self.clock.now - self.confirm_at >= self.options.get('success_delay', 0.5)
            )
        return False

    def appear_then_click(self, rule, **kwargs):
        if not self.appear(rule):
            return False
        if rule == self.I_WT_SAVE_ALL:
            self.save_attempts += 1
            if self.save_attempts <= self.options.get('throttled_saves', 0):
                return False
            self.save_at = self.clock.now
        if rule == self.I_WT_TF_CONFIRM:
            self.confirm_at = self.clock.now
        if rule == self.I_WT_HAPPY_GET and not self.options.get('persistent_happy', False):
            self.happy_dismissed = True
        self.clicks.append(rule)
        return True

    def click(self, rule, **kwargs):
        self.clicks.append(rule)
        return True


class WeeklyTouchFishTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.warnings = []
        clock = self.clock

        class FakeTimer:
            def __init__(self, duration):
                self.duration = duration
                self.started_at = None

            def start(self):
                self.started_at = clock.now
                return self

            def reached(self):
                return clock.now - self.started_at >= self.duration

        tree = ast.parse((ROOT / 'tasks/WeeklyTrifles/script_task.py').read_text(encoding='utf-8'))
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef))
        method = next(node for node in cls.body if isinstance(node, ast.FunctionDef)
                      and node.name == '_save_touch_fish')
        namespace = {
            'Timer': FakeTimer,
            'sleep': clock.sleep,
            'logger': SimpleNamespace(hr=lambda message: None, info=lambda message: None,
                                      warning=self.warnings.append),
            'page_guild': 'guild', 'page_touch_fish': 'fish', 'page_main': 'main',
            'random_click': lambda **kwargs: ('local_random', kwargs['ltrb']),
        }
        exec(compile(ast.Module(body=[method], type_ignores=[]), str(ROOT), 'exec'), namespace)
        self.save = namespace['_save_touch_fish']

    def run_flow(self, **options):
        task = TouchFishHarness(self.clock, **options)
        result = self.save(task)
        self.assertEqual(task.pages[-1], 'main')
        self.assertLessEqual(self.clock.now, 28)
        return task, result

    def test_delayed_confirmation_and_success_are_waited_for(self):
        task, result = self.run_flow(confirm_delay=3, success_delay=2)
        self.assertTrue(result)
        self.assertEqual(task.clicks.count(task.I_WT_SAVE_ALL), 1)
        self.assertEqual(task.clicks.count(task.I_WT_TF_CONFIRM), 1)
        self.assertGreaterEqual(task.confirm_at - task.save_at, 3)
        self.assertFalse(self.warnings)

    def test_insufficient_tickets_do_not_attempt_save(self):
        task, result = self.run_flow(tickets=(9, 1, 10), cost=3)
        self.assertFalse(result)
        self.assertNotIn(task.I_WT_SAVE_ALL, task.clicks)
        self.assertIn('not enough', self.warnings[-1])

    def test_zero_available_is_a_valid_insufficient_balance(self):
        task, result = self.run_flow(tickets=(10, 0, 10))
        self.assertFalse(result)
        self.assertEqual(task.ticket_reads, 1)
        self.assertIn('not enough', self.warnings[-1])

    def test_invalid_ocr_never_attempts_save_or_confirmation(self):
        for tickets, cost in (
            ((0, 0, 0), 3), ((2, 8, 10), 0), ((2, 8, 10), None),
            (None, 3), ((2, 8), 3), ((2, -1, 1), 3),
            ((2, 8, 99), 3), (('2', 8, 10), 3), ((2, 8, 10), True),
        ):
            with self.subTest(tickets=tickets, cost=cost):
                self.setUp()
                task, result = self.run_flow(tickets=tickets, cost=cost)
                self.assertFalse(result)
                self.assertEqual(task.ticket_reads, 3)
                self.assertNotIn(task.I_WT_SAVE_ALL, task.clicks)
                self.assertNotIn(task.I_WT_TF_CONFIRM, task.clicks)
                self.assertIn('Cannot read', self.warnings[-1])

    def test_transient_zero_cost_can_recover_before_saving(self):
        task, result = self.run_flow(cost=[0, 0, 3])
        self.assertTrue(result)
        self.assertEqual(task.cost_reads, 3)
        self.assertEqual(task.clicks.count(task.I_WT_SAVE_ALL), 1)

    def test_missing_or_unresponsive_folded_entrance_is_bounded(self):
        task, result = self.run_flow(fold_after=None)
        self.assertFalse(result)
        self.assertEqual(task.pages, ['guild', 'main'])
        self.assertEqual(self.clock.now, 13)
        self.assertIn('entrance is unavailable', self.warnings[-1])

    def test_folded_entrance_can_open_after_delay(self):
        task, result = self.run_flow(fold_after=3)
        self.assertTrue(result)
        self.assertEqual(task.clicks.count(task.I_WT_OPEN_FOLD_WINDOW), 2)

    def test_persistent_last_week_overlay_cannot_bypass_timeout(self):
        task, result = self.run_flow(last_save_until=float('inf'))
        self.assertFalse(result)
        self.assertTrue(task.clicks)
        self.assertTrue(all(click == ('local_random', (True, False, False, False))
                            for click in task.clicks))
        self.assertIn('timeout', self.warnings[-1])

    def test_persistent_first_entry_overlay_cannot_bypass_timeout(self):
        task, result = self.run_flow(happy_get=True, persistent_happy=True)
        self.assertFalse(result)
        self.assertNotIn(task.I_WT_SAVE_ALL, task.clicks)
        self.assertIn('timeout', self.warnings[-1])

    def test_first_entry_overlay_is_handled_before_save_button_appears(self):
        task, result = self.run_flow(happy_get=True)
        self.assertTrue(result)
        self.assertEqual(task.clicks[0], task.I_WT_HAPPY_GET)
        self.assertEqual(task.ticket_reads, 1)

    def test_throttled_save_does_not_enter_confirmation_state(self):
        task, result = self.run_flow(throttled_saves=2)
        self.assertTrue(result)
        self.assertEqual(task.save_attempts, 3)
        self.assertEqual(task.clicks.count(task.I_WT_SAVE_ALL), 1)
        self.assertEqual(task.clicks.count(task.I_WT_TF_CONFIRM), 1)

    def test_late_save_button_is_waited_for(self):
        task, result = self.run_flow(ready_after=3)
        self.assertTrue(result)
        self.assertEqual(task.ticket_reads, 1)

    def test_already_saved_is_recognized_without_spending(self):
        task, result = self.run_flow(already_saved=True)
        self.assertTrue(result)
        self.assertFalse(task.clicks)
        self.assertEqual(task.ticket_reads, 0)

    def test_unresponsive_confirmation_times_out_without_repeating_save(self):
        task, result = self.run_flow(confirm_delay=float('inf'))
        self.assertFalse(result)
        self.assertEqual(task.clicks.count(task.I_WT_SAVE_ALL), 1)
        self.assertNotIn(task.I_WT_TF_CONFIRM, task.clicks)
        self.assertIn('timeout', self.warnings[-1])

    def test_delayed_completion_does_not_report_insufficient_balance(self):
        task, result = self.run_flow(success_delay=float('inf'))
        self.assertFalse(result)
        self.assertEqual(task.clicks.count(task.I_WT_TF_CONFIRM), 1)
        self.assertIn('timeout', self.warnings[-1])
        self.assertFalse(any('not enough' in warning for warning in self.warnings))


if __name__ == '__main__':
    unittest.main()
