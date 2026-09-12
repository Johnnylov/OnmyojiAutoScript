"""Offline quota/UI regressions; no emulator, account, OCR or RPC is used.

Run: toolkit/python.exe -m unittest discover -s dev_tools -p test_daily_free_summon.py -v
"""
import ast
import copy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
from types import SimpleNamespace
import unicodedata
import unittest
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]


class Marker(str):
    def __new__(cls, value):
        marker = super().__new__(cls, value)
        marker.roi_front = [595, 586, 65, 76]
        return marker


class Counter:
    def __init__(self, world, roi, state='menu'):
        self.world = world
        self.roi = roi
        self.state = state

    def ocr(self, image):
        self.world.read_rois.append(list(self.roi))
        return self.world.ocr(image, self.state)


def load_subject(namespace):
    source = ast.parse((ROOT / 'tasks/Component/Summon/summon.py').read_text(encoding='utf-8'))
    result = next(node for node in source.body if isinstance(node, ast.ClassDef)
                  and node.name == 'FreeSummonResult')
    parent = next(node for node in source.body if isinstance(node, ast.ClassDef) and node.name == 'Summon')
    names = {'_parse_free_summon_count', '_read_free_summon_count', '_event_summon_canvas_appear', '_perform_free_summon',
             '_summon_free_until_empty', 'summon_one', 'back_summon_main'}
    selected = [node for node in parent.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in selected} == names
    parent = ast.ClassDef(name='Summon', bases=[], keywords=[], body=selected, decorator_list=[])
    source = ast.parse((ROOT / 'tasks/DailyTrifles/script_task.py').read_text(encoding='utf-8'))
    child = next(node for node in source.body if isinstance(node, ast.ClassDef) and node.name == 'ScriptTask')
    methods = [node for node in child.body if isinstance(node, ast.FunctionDef)
               and node.name in {'run_one_summon', 'summon_recall'}]
    child = ast.ClassDef(name='Subject', bases=[ast.Name(id='Summon', ctx=ast.Load())],
                        keywords=[], body=methods, decorator_list=[])
    module = ast.Module(body=[result, parent, child], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), 'daily_summon_under_test', 'exec'), namespace)
    return namespace['Subject']


class World:
    def __init__(self, quota=2, *, mode='normal', raw=None, fault=None, recorded=False, layout='legacy'):
        self.now = 0.0
        self.quota = quota
        self.total = max(quota, 2)
        self.mode = mode
        self.layout = layout
        self.selection = 'ten'
        self.canvas_raw = None
        self.state = 'menu'
        self.raw = raw
        self.fault = fault
        self.draws = []
        self.clicks = []
        self.read_states = []
        self.read_rois = []
        self.frames = 0
        self.result_marker = 'I_SM_CONFIRM' if mode == 'normal' else 'I_RECALL_SM_CONFIRM'
        namespace = dict(__name__=__name__, copy=copy, dataclass=dataclass, re=re, unicodedata=unicodedata,
                         logger=Mock(), time=SimpleNamespace(monotonic=lambda: self.now, sleep=self.sleep),
                         sleep=self.sleep, datetime=SimpleNamespace(now=lambda: datetime(2026, 9, 11, 12)),
                         SummonType=SimpleNamespace(default='normal', recall='recall'), page_summon='menu')
        task = self.task = load_subject(namespace)()
        for marker in ('I_BLUE_TICKET', 'I_ONE_TICKET', 'I_SM_CONFIRM', 'I_SM_CONFIRM_2',
                       'I_UI_CANCEL', 'I_UI_BACK_BLUE', 'I_UI_BACK_YELLOW', 'I_UI_BACK_RED',
                       'I_RECALL_TICKET', 'I_RECALL_ONE_TICKET', 'I_RECALL_SM_CONFIRM', 'I_EVENT_ONE_TICKET'):
            setattr(task, marker, Marker(marker))
        for index in range(1, 5):
            setattr(task, 'S_RANDOM_SWIPE_' + str(index), SimpleNamespace(name='swipe'))
        for index in range(2, 5):
            setattr(task, 'O_SELECT_SM' + str(index), SimpleNamespace(coord=lambda: (10, 20)))
        task.device = SimpleNamespace(image=None, click=Mock())
        task.screenshot = self.screenshot
        task.appear = self.appear
        task.click = self.click
        task.appear_then_click = self.appear_then_click
        task.summon = lambda: self.draw('normal')
        task.summon_mystery_pattern = lambda: self.draw('pattern')
        task.O_ONE_TICKET = Counter(self, [574, 681, 100, 32])
        task.O_RECALL_TICKET_AREA = Counter(self, [590, 660, 100, 32])
        task.O_EVENT_FREE_QUOTA = Counter(self, [580, 650, 130, 38], 'draw')
        task.O_EVENT_DRAW_PROMPT = SimpleNamespace(ocr=Mock(return_value='画出轨迹召唤式神'))
        task.goto_page = Mock()
        task.check_time = Mock()
        self.record = SimpleNamespace(summon_dt=datetime(2026, 9, 10))
        self.options = SimpleNamespace(summon_type=mode, draw_mystery_pattern=True)
        task.config = SimpleNamespace(daily_trifles=SimpleNamespace(
            today_is_done=Mock(return_value=recorded), trifles_config=self.options, done_record=self.record),
            save=Mock(), notifier=SimpleNamespace(push=Mock()))

    def sleep(self, seconds):
        self.now += seconds

    def screenshot(self):
        self.frames += 1
        if self.frames > 250:
            raise AssertionError('Unbounded UI loop')
        self.now += 1
        self.task.device.image = (self.frames, self.state)

    def appear(self, marker, **kwargs):
        if self.state == 'menu':
            return marker == ('I_BLUE_TICKET' if self.mode == 'normal' else 'I_RECALL_TICKET')
        if self.state == 'draw':
            if self.layout == 'event':
                return marker == 'I_EVENT_ONE_TICKET'
            return marker == ('I_ONE_TICKET' if self.mode == 'normal' else 'I_RECALL_ONE_TICKET')
        if self.state == 'result':
            return marker == self.result_marker
        if self.state == 'after_result':
            return marker == 'I_UI_BACK_BLUE'
        return False

    def click(self, marker, **kwargs):
        self.clicks.append(marker)
        if marker in ('I_BLUE_TICKET', 'I_RECALL_TICKET'):
            if self.fault != 'entry':
                self.state = 'draw'
        elif marker == 'I_EVENT_ONE_TICKET':
            self.selection = 'single'
        elif marker in ('I_SM_CONFIRM', 'I_SM_CONFIRM_2', 'I_RECALL_SM_CONFIRM'):
            if self.fault != 'confirm':
                self.state = 'after_result'
        elif marker in ('I_UI_BACK_BLUE', 'I_UI_BACK_YELLOW'):
            if self.fault != 'back':
                self.state = 'menu'

    def appear_then_click(self, marker, **kwargs):
        if self.appear(marker):
            self.click(marker)
            return True
        return False

    def ocr(self, image, state='menu'):
        self.read_states.append(image)
        # Reproduce the original failure: the post-result frame has no quota.
        if image[1] != state:
            return ''
        if state == 'draw' and self.canvas_raw is not None:
            return self.canvas_raw() if callable(self.canvas_raw) else self.canvas_raw
        if callable(self.raw):
            return self.raw()
        if self.raw is not None:
            return self.raw
        return f'剩余免费次数 {self.quota}/{self.total}'

    def draw(self, kind):
        if self.quota <= 0:
            raise AssertionError('Would spend a paid ticket')
        if self.layout == 'event' and self.selection != 'single':
            raise AssertionError('Would draw with ten selected')
        self.quota -= 1
        self.draws.append(kind)
        if self.fault != 'draw':
            self.state = 'result'


class CounterTests(unittest.TestCase):
    def test_remaining_fraction_and_explicit_free_labels(self):
        parse = World().task._parse_free_summon_count
        for raw, expected in [('剩余免费次数 1/2', 1), ('剩余免费次数 2/2', 2),
                              ('剩余免费次数 0/2', 0), ('1/2', 1), ('0/2', 0),
                              ('剩余免费次数：１／２', 1), ('免费1次', 1),
                              ('今日免费次数：2', 2), ('免费次数已用完', 0)]:
            with self.subTest(raw=raw):
                self.assertEqual(parse(raw), expected)

    def test_paid_ticket_counts_and_ambiguous_text_cannot_authorize_a_draw(self):
        parse = World().task._parse_free_summon_count
        for raw in (None, '', ' ', '2', '128', '蓝票 2', '蓝票1/2', '勾玉100',
                    '1/0', '3/2', '-1/2', '99/99', '1/2/3', '免费', '免费次数O/2', 2):
            with self.subTest(raw=raw):
                self.assertIsNone(parse(raw))


class SummonFlowTests(unittest.TestCase):
    def test_quota_follows_matched_ticket_on_every_frame(self):
        world = World()
        original_appear = world.appear

        def appear(marker):
            if marker is world.task.I_BLUE_TICKET:
                marker.roi_front = [630 + world.frames, 577, 65, 76]
            return original_appear(marker)

        world.task.appear = appear
        self.assertEqual(world.task._read_free_summon_count(
            world.task.O_ONE_TICKET, world.task.I_BLUE_TICKET), 2)
        self.assertEqual(world.read_rois, [[610, 672, 100, 32], [611, 672, 100, 32]])
        self.assertEqual(world.task.O_ONE_TICKET.roi, [574, 681, 100, 32])

    def test_default_ticket_position_preserves_calibrated_caption_roi(self):
        world = World()
        world.task._read_free_summon_count(world.task.O_ONE_TICKET, world.task.I_BLUE_TICKET)
        self.assertEqual(world.read_rois, [[574, 681, 100, 32]] * 2)

    def test_recall_keeps_its_own_counter_roi(self):
        world = World(mode='recall')
        world.task._read_free_summon_count(
            world.task.O_RECALL_TICKET_AREA, world.task.I_RECALL_TICKET)
        self.assertEqual(world.read_rois, [[590, 660, 100, 32]] * 2)

    def test_two_free_summons_return_to_menu_between_draws(self):
        world = World()
        result = world.task.summon_one(draw_mystery_pattern=True)
        self.assertEqual(result.completed, 2)
        self.assertTrue(result.exhausted)
        self.assertEqual(world.draws, ['pattern', 'normal'])
        self.assertEqual(world.clicks.count('I_BLUE_TICKET'), 2)
        self.assertEqual(world.clicks.count('I_UI_BACK_BLUE'), 2)
        self.assertEqual(world.state, 'menu')
        self.assertTrue(all(state == 'menu' for _, state in world.read_states))
        self.assertEqual(len({number for number, _ in world.read_states}), 4)

    def test_one_remaining_does_not_use_the_total_as_draw_count(self):
        world = World(quota=1)
        result = world.task.summon_one()
        self.assertTrue(result.exhausted)
        self.assertEqual(result.completed, 1)
        self.assertEqual(len(world.draws), 1)

    def test_zero_remaining_with_nonzero_total_does_not_draw(self):
        world = World(quota=0)
        result = world.task.summon_one()
        self.assertTrue(result.exhausted)
        self.assertEqual(world.draws, [])
        self.assertEqual(world.clicks, [])

    def test_unknown_and_paid_ticket_numbers_do_not_draw_or_report_exhausted(self):
        for raw in ('', None, '234'):
            world = World(raw=lambda: raw)
            result = world.task.summon_one()
            self.assertFalse(result.exhausted)
            self.assertEqual(world.draws, [])
            self.assertEqual(world.clicks, [])

    def test_transient_blank_quota_is_retried(self):
        values = iter(['', '2/2', '2/2', '', '1/2', '1/2', '', '0/2', '0/2'])
        world = World(raw=lambda: next(values))
        result = world.task.summon_one()
        self.assertTrue(result.exhausted)
        self.assertEqual(result.completed, 2)

    def test_stale_caption_after_first_draw_waits_for_decrease(self):
        values = iter(['2/2', '2/2', '2/2', '2/2', '1/2', '1/2', '1/2', '0/2', '0/2'])
        world = World(raw=lambda: next(values))
        result = world.task.summon_one()
        self.assertTrue(result.exhausted)
        self.assertEqual(result.completed, 2)

    def test_persistently_stale_or_increased_caption_stops_after_first_draw(self):
        for after in ('2/2', '3/3'):
            world = World()
            world.raw = lambda: '2/2' if not world.draws else after
            result = world.task.summon_one()
            self.assertFalse(result.exhausted)
            self.assertEqual(result.completed, 1)
            self.assertEqual(len(world.draws), 1)

    def test_ui_timeouts_do_not_repeat_draws_or_confirmations(self):
        for fault in ('entry', 'draw', 'confirm', 'back'):
            with self.subTest(fault=fault):
                world = World(fault=fault)
                result = world.task.summon_one()
                self.assertFalse(result.exhausted)
                self.assertLessEqual(len(world.draws), 1)
                self.assertLessEqual(world.clicks.count('I_SM_CONFIRM'), 1)
                self.assertLess(world.frames, 250)

    def test_recall_uses_its_own_menu_and_quota_for_both_draws(self):
        world = World(mode='recall')
        result = world.task.summon_recall()
        self.assertTrue(result.exhausted)
        self.assertEqual(result.completed, 2)
        self.assertEqual(world.clicks.count('I_RECALL_TICKET'), 2)
        self.assertNotIn('I_BLUE_TICKET', world.clicks)

    def test_alternate_result_layout_is_confirmed(self):
        world = World()
        world.result_marker = 'I_SM_CONFIRM_2'
        result = world.task.summon_one()
        self.assertTrue(result.exhausted)
        self.assertEqual(world.clicks.count('I_SM_CONFIRM_2'), 2)

    def test_quota_is_never_read_from_non_menu_frames(self):
        world = World()
        world.state = 'after_result'
        result = world.task._read_free_summon_count(world.task.O_ONE_TICKET, 'I_BLUE_TICKET')
        self.assertIsNone(result)
        self.assertEqual(world.read_states, [])


class EventCanvasTests(unittest.TestCase):
    def test_last_confirmed_attempt_finishes_when_free_caption_disappears(self):
        world = World(quota=1, layout='event')
        world.raw = lambda: '免费1/2' if not world.draws else '神秘召唤'
        result = world.task.summon_one()
        self.assertTrue(result.exhausted)
        self.assertEqual(result.completed, 1)
        self.assertEqual(world.state, 'menu')

    def test_last_attempt_still_requires_result_confirmation_and_return(self):
        for fault in ('draw', 'confirm', 'back'):
            with self.subTest(fault=fault):
                world = World(quota=1, layout='event', fault=fault)
                result = world.task.summon_one()
                self.assertFalse(result.exhausted)
                self.assertEqual(len(world.draws), 1)

    def test_event_skin_draws_each_verified_free_attempt(self):
        world = World(layout='event')
        result = world.task.summon_one()
        self.assertTrue(result.exhausted)
        self.assertEqual(result.completed, 2)
        self.assertEqual(world.draws, ['normal', 'normal'])
        self.assertEqual(world.clicks.count('I_EVENT_ONE_TICKET'), 2)
        self.assertEqual(world.clicks.count('I_BLUE_TICKET'), 2)
        self.assertEqual(sum(state == 'draw' for _, state in world.read_states), 4)

    def test_already_open_event_canvas_selects_single_and_draws_once(self):
        world = World(quota=1, layout='event')
        world.state = 'draw'
        self.assertTrue(world.task._perform_free_summon(
            world.task.I_BLUE_TICKET, world.task.I_ONE_TICKET,
            (world.task.I_SM_CONFIRM, world.task.I_SM_CONFIRM_2)))
        self.assertNotIn('I_BLUE_TICKET', world.clicks)
        self.assertEqual(world.clicks.count('I_EVENT_ONE_TICKET'), 1)
        self.assertEqual(world.draws, ['normal'])
        self.assertEqual(world.quota, 0)

    def test_no_drawing_when_event_canvas_quota_is_unknown_zero_or_paid(self):
        for raw in ('', '免费', '0/2', '21', None):
            with self.subTest(raw=raw):
                world = World(layout='event')
                world.canvas_raw = lambda: raw
                result = world.task.summon_one()
                self.assertFalse(result.exhausted)
                self.assertEqual(world.draws, [])
                self.assertEqual(world.clicks.count('I_EVENT_ONE_TICKET'), 1)

    def test_event_button_without_drawing_prompt_cannot_authorize_drawing(self):
        world = World(layout='event')
        world.task.O_EVENT_DRAW_PROMPT.ocr.return_value = '确定召唤十次'
        result = world.task.summon_one()
        self.assertFalse(result.exhausted)
        self.assertEqual(world.draws, [])
        self.assertNotIn('I_EVENT_ONE_TICKET', world.clicks)

    def test_event_prompt_without_single_button_is_not_a_canvas(self):
        world = World(layout='event')
        world.task.appear = Mock(return_value=False)
        self.assertFalse(world.task._event_summon_canvas_appear())
        world.task.O_EVENT_DRAW_PROMPT.ocr.assert_not_called()

    def test_event_skin_keeps_the_mystery_pattern_option(self):
        world = World(layout='event')
        world.task.summon_one(draw_mystery_pattern=True)
        self.assertEqual(world.draws, ['pattern', 'normal'])

    def test_legacy_skin_never_selects_event_button_or_reads_event_prompt(self):
        world = World()
        result = world.task.summon_one()
        self.assertTrue(result.exhausted)
        self.assertNotIn('I_EVENT_ONE_TICKET', world.clicks)
        world.task.O_EVENT_DRAW_PROMPT.ocr.assert_not_called()


class DailyCompletionTests(unittest.TestCase):
    def test_today_record_does_not_hide_a_remaining_event_attempt(self):
        world = World(quota=1, recorded=True)
        world.task.run_one_summon()
        self.assertEqual(len(world.draws), 1)
        self.assertEqual(world.record.summon_dt, datetime(2026, 9, 11, 12))
        world.task.config.save.assert_called_once()

    def test_completion_is_not_written_for_unknown_quota(self):
        world = World(raw='')
        world.task.run_one_summon()
        self.assertEqual(world.record.summon_dt, datetime(2026, 9, 10))
        world.task.config.save.assert_not_called()
        world.task.check_time.assert_not_called()

    def test_partial_success_preserves_pattern_progress_but_not_daily_completion(self):
        world = World()
        world.raw = lambda: '2/2' if not world.draws else ''
        world.task.run_one_summon()
        self.assertEqual(len(world.draws), 1)
        world.task.check_time.assert_called_once()
        self.assertEqual(world.record.summon_dt, datetime(2026, 9, 10))

    def test_rechecking_exhausted_day_never_spends_tickets(self):
        world = World(quota=0, recorded=True)
        world.task.run_one_summon()
        self.assertEqual(world.draws, [])
        world.task.check_time.assert_not_called()

    def test_recall_completion_uses_recall_menu(self):
        world = World(mode='recall')
        world.task.run_one_summon()
        self.assertEqual(world.record.summon_dt, datetime(2026, 9, 11, 12))
        self.assertEqual(len(world.draws), 2)
        world.task.check_time.assert_not_called()


if __name__ == '__main__':
    unittest.main()
