"""Offline coloring workflow tests: no game imports or real clicks."""

import ast
from collections import deque
from pathlib import Path
import re
from types import SimpleNamespace, MethodType
import unittest
from unittest.mock import Mock

import numpy as np

from tasks.ActivityShikigami.coloring import DailyColorer, ColoringError, parse_amount, parse_progress
from module.exception import GameTooManyClickError


def device_click_guard():
    """Run the actual Device counting methods without constructing a device."""
    path = Path(__file__).resolve().parents[1] / 'module/device/device.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    definition = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'Device')
    names = {'click_record_add', 'click_record_check'}
    methods = [node for node in definition.body if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace = {'logger': Mock(), 'GameTooManyClickError': GameTooManyClickError}
    exec(compile(ast.Module(body=methods, type_ignores=[]), str(path), 'exec'), namespace)
    guard = SimpleNamespace(click_record=deque(maxlen=15))
    guard.click_record_clear = Mock(side_effect=guard.click_record.clear)
    for name in names:
        setattr(guard, name, MethodType(namespace[name], guard))
    return guard


class World:
    def __init__(self, stage='map', currency=200, progress=54., capacity=None,
                 outcome='consume', amount=None, characters=None, switch_works=True, guard=None):
        self.stage, self.currency, self.progress = stage, currency, progress
        self.capacity, self.outcome, self.amount_override = capacity, outcome, amount
        self.amount = 0
        self.characters = characters or [('一目连呱', 1.19)]
        self.character_index = 0
        self.switch_works = switch_works
        self.clicks = []
        self.action_names = []
        self.guard = guard
        self.reads = []
        self.view = SimpleNamespace(find_page=self.find_page, find_map_entry=self.find_map_entry,
                                    prepare_counter=lambda image, roi, quantity: np.zeros((2, 2, 3)))

    def capture(self):
        return self.stage

    def find_page(self, image):
        if self.stage not in ('overview', 'panel'):
            return None
        return SimpleNamespace(panel=self.stage == 'panel', global_progress_roi='global',
                               currency_roi='currency', amount_roi='amount', start_roi='start',
                               max_roi='max', submit_roi='submit', back_roi='back', collapse_roi='collapse',
                               character_name_roi='name', character_progress_roi='local_progress',
                               next_roi='next', next_available=True)

    def find_map_entry(self, image):
        return 'entry' if self.stage == 'map' else None

    def read_text(self, image, roi, name='activity_text'):
        self.reads.append(name)
        if name == 'coloring_global_progress':
            return f'{self.progress}%'
        if name == 'coloring_currency':
            return str(self.currency)
        if name == 'coloring_selected_amount':
            return str(self.amount if self.amount_override is None else self.amount_override)
        if name == 'coloring_character_name':
            return self.characters[self.character_index][0]
        if name == 'coloring_character_progress':
            return f'{self.characters[self.character_index][1]}%'
        raise AssertionError(name)

    def click(self, roi, name):
        self.action_names.append(name)
        if self.guard is not None:
            self.guard.click_record_add(name)
            self.guard.click_record_check()
        name = re.sub(r'_\d+_\d+$', '', name)
        self.clicks.append(name)
        if name == 'coloring_open':
            self.stage = 'overview'
        elif name == 'coloring_start':
            self.stage = 'panel'
        elif name == 'coloring_max':
            self.amount = (0 if self.characters[self.character_index][1] == 100 else
                           min(self.currency, self.capacity or self.currency))
        elif name == 'coloring_submit':
            if self.outcome == 'consume':
                self.currency -= self.amount
                self.amount = 0
            elif self.outcome == 'complete':
                self.progress = 100.
                self.currency -= self.amount
                self.amount = 0
            elif self.outcome == 'others':
                self.progress += 1
            elif self.outcome == 'unknown':
                self.stage = 'unknown'
        elif name == 'coloring_collapse':
            self.stage = 'overview'
        elif name == 'coloring_back':
            self.stage = 'map'
        elif name == 'coloring_next_character':
            if self.switch_works:
                self.character_index = (self.character_index + 1) % len(self.characters)
        else:
            raise AssertionError('Unexpected click')

    def runner(self):
        return DailyColorer(self.capture, self.click, self.read_text, self.view, sleep=lambda _: None)


class ColoringTests(unittest.TestCase):
    def test_strict_parsers_reject_missing_units_wrong_values_and_partial_text(self):
        self.assertEqual(parse_progress('54.0%'), 54.)
        self.assertEqual(parse_progress('100％'), 100.)
        for text in (None, '', '540', '101%', '-1%', '1.19% extra'):
            self.assertIsNone(parse_progress(text))
        self.assertEqual(parse_amount('1.2万'), 12000)
        for text in (None, '', '1/100', '-1', '0.5', '1.2', 'max', '1 plus'):
            self.assertIsNone(parse_amount(text))

    def test_opens_painting_and_uses_only_max_then_submit_until_currency_empty(self):
        world = World()
        result = world.runner().run()
        self.assertEqual((result.status, result.submissions, result.global_progress), ('no_currency', 1, 54.))
        self.assertEqual(world.clicks, ['coloring_open', 'coloring_start', 'coloring_max', 'coloring_submit'])

    def test_game_amount_cap_repeats_after_verified_consumption(self):
        world = World(stage='panel', currency=250, capacity=100)
        result = world.runner().run()
        self.assertEqual((result.status, result.submissions), ('no_currency', 3))
        self.assertEqual(world.clicks, ['coloring_max', 'coloring_submit'] * 3)

    def test_complete_global_progress_stops_without_spending(self):
        world = World(stage='panel', progress=100.)
        result = world.runner().run()
        self.assertEqual(result.status, 'complete')
        self.assertEqual(world.clicks, [])

    def test_completion_after_submit_stops_at_once(self):
        world = World(stage='panel', capacity=100, outcome='complete')
        result = world.runner().run()
        self.assertEqual((result.status, result.submissions, result.global_progress), ('complete', 1, 100.))
        self.assertEqual(world.currency, 100)

    def test_zero_currency_overview_or_panel_never_clicks_start_max_or_submit(self):
        for stage in ('overview', 'panel'):
            world = World(stage=stage, currency=0)
            result = world.runner().run()
            self.assertEqual(result.status, 'no_currency')
            self.assertEqual(world.clicks, [])

    def test_zero_quantity_stops_after_max_without_submit(self):
        world = World(stage='panel', amount=0)
        result = world.runner().run()
        self.assertEqual(result.status, 'no_progress')
        self.assertEqual(world.clicks, ['coloring_max'])

    def test_unknown_or_excess_quantity_never_submits(self):
        for amount in ('?', 500):
            world = World(stage='panel', amount=amount)
            with self.assertRaises(ColoringError):
                world.runner().run()
            self.assertEqual(world.clicks, ['coloring_max'])

    def test_unchanged_or_other_players_progress_never_repeats_submit(self):
        for outcome in ('unchanged', 'others', 'unknown'):
            world = World(stage='panel', outcome=outcome)
            result = world.runner().run()
            self.assertEqual((result.status, result.submissions), ('no_progress', 0))
            self.assertEqual(world.clicks, ['coloring_max', 'coloring_submit'])

    def test_unknown_screen_never_receives_a_click(self):
        world = World(stage='unknown')
        self.assertEqual(world.runner().run().status, 'unavailable')
        self.assertEqual(world.clicks, [])

    def test_submission_limit_is_bounded_even_with_unlimited_remaining_currency(self):
        world = World(stage='panel', currency=5000, capacity=1)
        runner = world.runner()
        runner.MAX_SUBMISSIONS = 3
        result = runner.run()
        self.assertEqual(result.submissions, 3)
        self.assertEqual(result.status, 'no_progress')
        self.assertEqual(world.clicks.count('coloring_submit'), 3)

    def test_successful_consumption_continues_beyond_ten_submissions(self):
        world = World(stage='panel', currency=1500, capacity=100)
        result = world.runner().run()
        self.assertEqual((result.status, result.submissions), ('no_currency', 15))

    def test_actual_device_guard_reproduces_old_two_button_limit(self):
        guard = device_click_guard()
        with self.assertRaises(GameTooManyClickError):
            for name in ['coloring_max', 'coloring_submit'] * 6:
                guard.click_record_add(name)
                guard.click_record_check()

    def test_actual_device_guard_accepts_fifteen_verified_transactions_without_history_clear(self):
        guard = device_click_guard()
        world = World(stage='panel', currency=1500, capacity=100, guard=guard)
        result = world.runner().run()
        self.assertEqual((result.status, result.submissions), ('no_currency', 15))
        self.assertEqual(world.action_names, [name for phase in range(15)
                         for name in (f'coloring_max_{phase}_0', f'coloring_submit_{phase}_0')])
        guard.click_record_clear.assert_not_called()

    def test_actual_device_guard_accepts_many_verified_completed_character_switches(self):
        guard = device_click_guard()
        characters = [(f'式神{chr(0x4e00+index)}', 100) for index in range(16)]
        characters.append(('灯笼鬼', 0))
        world = World(stage='panel', characters=characters, guard=guard)
        result = world.runner().run()
        self.assertEqual((result.status, result.submissions), ('no_currency', 1))
        self.assertEqual(world.clicks.count('coloring_next_character'), 16)
        self.assertEqual(world.action_names[-2:], ['coloring_max_0_16', 'coloring_submit_0_16'])
        guard.click_record_clear.assert_not_called()

    def test_unverified_consumption_keeps_one_transaction_and_does_not_rename_retry(self):
        guard = device_click_guard()
        world = World(stage='panel', outcome='unchanged', guard=guard)
        result = world.runner().run()
        self.assertEqual((result.status, result.submissions), ('no_progress', 0))
        self.assertEqual(world.action_names, ['coloring_max_0_0', 'coloring_submit_0_0'])
        guard.click_record_clear.assert_not_called()

    def test_runtime_limit_stops_with_remaining_currency(self):
        world = World(stage='panel', currency=5000, capacity=1)
        runner = world.runner()
        runner.clock = iter((0, 1, 3)).__next__
        runner.MAX_RUNTIME = 2
        result = runner.run()
        self.assertEqual((result.status, result.submissions), ('no_progress', 1))

    def test_completed_character_advances_to_next_verified_name_then_colors(self):
        world = World(stage='panel', characters=[('一目连呱', 100), ('灯笼鬼', 0)])
        result = world.runner().run()
        self.assertEqual((result.status, result.submissions), ('no_currency', 1))
        self.assertEqual(world.clicks, ['coloring_max', 'coloring_next_character',
                                       'coloring_max', 'coloring_submit'])

    def test_completed_character_cycle_or_unchanged_name_stops_without_spending(self):
        for switch_works, switches in ((True, 2), (False, 1)):
            world = World(stage='panel', characters=[('一目连呱', 100), ('灯笼鬼', 100)],
                          switch_works=switch_works)
            result = world.runner().run()
            self.assertEqual((result.status, result.submissions), ('no_progress', 0))
            self.assertEqual(world.clicks.count('coloring_next_character'), switches)
            self.assertNotIn('coloring_submit', world.clicks)

    def test_leave_collapses_then_backs_to_verified_map(self):
        world = World(stage='panel')
        self.assertTrue(world.runner().leave())
        self.assertEqual(world.clicks, ['coloring_collapse', 'coloring_back'])

    def test_leave_already_on_map_does_not_click_and_unknown_page_fails_safely(self):
        for stage, expected in (('map', True), ('unknown', False)):
            world = World(stage=stage)
            self.assertEqual(world.runner().leave(), expected)
            self.assertEqual(world.clicks, [])


if __name__ == '__main__':
    unittest.main()
