"""Offline daily soul-selection regressions; no game, device, or ticket use."""

import ast
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import random
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tasks.ActivityShikigami import soul_selection as souls
from tasks.ActivityShikigami.config import ActivityShikigami


DAY = date(2026, 9, 13)
TARGET = frozenset((1, 4, 8, 13))
OWNER = 'account_a\n'


class RecommendationTests(unittest.TestCase):
    # These groups come from the supplied dated chart, not the production table.
    groups = (
        ((9, 15, 21, 27), {1, 4, 10, 13, 14}),
        ((10, 16, 22, 28), {1, 4, 6, 13, 14}),
        ((11, 17, 23, 29), {1, 4, 6, 14}),
        ((12, 18, 24), {1, 4, 6, 10, 13, 14}),
        ((13, 19, 25), {1, 4, 13}),
        ((14, 20, 26), {1, 4, 6, 13, 14}),
    )

    def test_all_twenty_one_dates_follow_chart_and_choose_four_distinct(self):
        visited = set()
        for days, preferred in self.groups:
            for day_number in days:
                day = date(2026, 9, day_number)
                visited.add(day_number)
                with self.subTest(day=day):
                    self.assertEqual(set(souls.recommendations(day)), preferred)
                    for seed in range(30):
                        selected = souls.choose_souls(day, random.Random(seed))
                        self.assertEqual(len(selected), 4)
                        self.assertLessEqual(selected, set(range(1, 16)))
                        if len(preferred) >= 4:
                            self.assertLessEqual(selected, preferred)
                        else:
                            self.assertLessEqual(preferred, selected)
                            self.assertEqual(len(selected - preferred), 1)
        self.assertEqual(visited, set(range(9, 30)))

    def test_sampling_varies_and_can_fill_from_every_other_slot(self):
        selected = {souls.choose_souls(date(2026, 9, 12), random.Random(seed))
                    for seed in range(100)}
        self.assertGreater(len(selected), 1)
        extras = set().union(*(souls.choose_souls(DAY, random.Random(seed)) - {1, 4, 13}
                             for seed in range(500)))
        self.assertEqual(extras, set(range(1, 16)) - {1, 4, 13})

    def test_other_dates_and_other_years_are_disabled(self):
        for day in (date(2026, 9, 8), date(2026, 9, 30), date(2026, 10, 13),
                    date(2025, 9, 13), date(2027, 9, 13), date(2027, 1, 1)):
            with self.subTest(day=day):
                self.assertEqual(souls.recommendations(day), ())
                with self.assertRaises(ValueError):
                    souls.choose_souls(day)

    def test_server_day_uses_china_midnight(self):
        for hour, minute, expected in ((15, 59, DAY), (16, 0, DAY + timedelta(days=1))):
            instant = datetime(2026, 9, 13, hour, minute, tzinfo=timezone.utc)

            class FixedDatetime(datetime):
                @classmethod
                def now(cls, tz=None):
                    if tz is None:
                        raise AssertionError('Calendar must specify the game timezone')
                    return instant.astimezone(tz)

            with self.subTest(instant=instant), patch.object(souls, 'datetime', FixedDatetime):
                self.assertEqual(souls.server_date(), expected)

    def test_daily_record_requires_current_day_and_four_valid_unique_slots(self):
        for recorded_day, slots, expected in (
                (DAY.isoformat(), [1, 4, 8, 13], True),
                ((DAY - timedelta(days=1)).isoformat(), [1, 4, 8, 13], False),
                (DAY.isoformat(), [], False),
                (DAY.isoformat(), [1, 4, 13], False),
                (DAY.isoformat(), [1, 4, 13, 13], False),
                (DAY.isoformat(), [0, 4, 8, 13], False),
                (DAY.isoformat(), [1, 4, 8, 16], False)):
            record = SimpleNamespace(date=recorded_day, slots=slots, owner=OWNER)
            with self.subTest(record=record):
                self.assertEqual(souls.recorded_today(record, DAY, OWNER), expected)

    def test_daily_record_requires_a_nonempty_matching_owner(self):
        for recorded_owner, current_owner in (('', OWNER), ('', ''), (OWNER, ''),
                                             (OWNER, 'account_b\n'), (OWNER, 'account_a\nrole_b')):
            record = SimpleNamespace(date=DAY.isoformat(), slots=sorted(TARGET), owner=recorded_owner)
            with self.subTest(recorded=recorded_owner, current=current_owner):
                self.assertFalse(souls.recorded_today(record, DAY, current_owner))


class FakePanel:
    submit_roi = (90, 90, 1, 1)
    close_roi = (95, 95, 1, 1)

    def cell_roi(self, slot):
        return (slot, 0, 1, 1)


class FakeGame:
    """Model the game's four-choice limit and persisted submission separately."""

    def __init__(self, selected=(), panel_open=False):
        self.current = set(selected)
        self.saved = set(selected)
        self.panel_open = panel_open
        self.entry_visible = True
        self.panel = FakePanel()
        self.calls = []
        self.capture_count = 0
        self.sleep = Mock()
        self.day = DAY
        self.drop = set()
        self.submit_saves = True
        self.unreadable = False
        self.alternate = False
        self.on_capture = None
        self.on_click = None

    def capture(self):
        self.capture_count += 1
        if self.capture_count > 160:
            raise AssertionError('Selector exceeded the offline frame budget')
        if self.on_capture:
            self.on_capture(self)
        return SimpleNamespace(panel_open=self.panel_open,
                               selected=frozenset(self.current), number=self.capture_count)

    def find_panel(self, frame):
        return self.panel if frame.panel_open else None

    def find_entry(self, frame):
        return (80, 80, 1, 1) if self.entry_visible and not frame.panel_open else None

    def selected(self, frame, panel):
        if self.unreadable:
            return None
        if self.alternate:
            return frozenset((1,)) if frame.number % 2 else frozenset((4,))
        return frame.selected

    def click(self, roi, name):
        self.calls.append(name)
        if self.on_click:
            self.on_click(self, name)
        if name in self.drop:
            return
        if name == 'daily_souls_open':
            self.panel_open = True
            self.current = self.saved.copy()
        elif name == 'daily_souls_submit':
            assert self.panel_open and len(self.current) == 4
            if self.submit_saves:
                self.saved = self.current.copy()
            self.panel_open = False
        elif name == 'daily_souls_close':
            self.panel_open = False
        elif name.startswith('daily_souls_remove_'):
            slot = int(name.rsplit('_', 1)[1])
            assert self.panel_open and slot in self.current
            assert roi == self.panel.cell_roi(slot)
            self.current.remove(slot)
        elif name.startswith('daily_souls_add_'):
            slot = int(name.rsplit('_', 1)[1])
            assert self.panel_open and slot not in self.current
            assert len(self.current) < 4, 'Old choices must be removed before additions'
            assert roi == self.panel.cell_roi(slot)
            self.current.add(slot)
        else:
            raise AssertionError(f'Unexpected control: {name}')

    def selector(self):
        return souls.DailySoulSelector(self.capture, self.click, self,
                                       today=lambda: self.day, sleep=self.sleep)


class SelectorTransactionTests(unittest.TestCase):
    def assert_bounded_failure(self, game, target=TARGET):
        with self.assertRaises(souls.SoulSelectionError):
            game.selector().select(DAY, target)
        self.assertLessEqual(game.capture_count, 45)
        self.assertLessEqual(game.sleep.call_count, 35)

    def test_remove_old_choices_before_add_and_verify_persisted_submission(self):
        game = FakeGame((1, 3, 5, 13))
        self.assertEqual(game.selector().select(DAY, TARGET), TARGET)
        self.assertEqual(game.calls, [
            'daily_souls_open', 'daily_souls_remove_3', 'daily_souls_remove_5',
            'daily_souls_add_4', 'daily_souls_add_8', 'daily_souls_submit',
            'daily_souls_open', 'daily_souls_close',
        ])
        self.assertEqual(game.saved, TARGET)
        self.assertFalse(game.panel_open)

    def test_zero_to_three_existing_choices_and_already_correct_panel(self):
        for selected in ((), (1,), (3, 13), (1, 3, 13), TARGET):
            with self.subTest(selected=selected):
                game = FakeGame(selected, panel_open=True)
                self.assertEqual(game.selector().select(DAY, TARGET), TARGET)
                self.assertEqual(game.saved, TARGET)
                self.assertEqual(game.calls.count('daily_souls_submit'), 1)
                self.assertEqual(game.calls.count('daily_souls_open'), 1)
                self.assertFalse(game.panel_open)

    def test_invalid_target_never_touches_ui(self):
        for target in ((), (1, 4, 13), (1, 1, 4, 13), (0, 1, 4, 13), (1, 4, 13, 16)):
            game = FakeGame()
            with self.subTest(target=target), self.assertRaises(ValueError):
                game.selector().select(DAY, target)
            self.assertEqual(game.capture_count, 0)
            self.assertEqual(game.calls, [])

    def test_lost_add_or_remove_click_is_not_submitted(self):
        for dropped in ('daily_souls_remove_3', 'daily_souls_add_4'):
            game = FakeGame((1, 3, 5, 13))
            game.drop.add(dropped)
            with self.subTest(dropped=dropped):
                self.assert_bounded_failure(game)
                self.assertNotIn('daily_souls_submit', game.calls)

    def test_missing_entry_and_lost_open_click_stop_with_bounded_work(self):
        no_entry = FakeGame()
        no_entry.entry_visible = False
        self.assert_bounded_failure(no_entry)
        self.assertEqual(no_entry.calls, [])
        no_open = FakeGame()
        no_open.drop.add('daily_souls_open')
        self.assert_bounded_failure(no_open)
        self.assertEqual(no_open.calls, ['daily_souls_open'])

    def test_unreadable_overfull_and_unstable_states_are_never_submitted(self):
        for mode in ('unreadable', 'overfull', 'unstable'):
            game = FakeGame((1, 2, 3, 4, 5) if mode == 'overfull' else ())
            game.unreadable = mode == 'unreadable'
            game.alternate = mode == 'unstable'
            with self.subTest(mode=mode):
                self.assert_bounded_failure(game)
                self.assertEqual(game.calls, ['daily_souls_open'])

    def test_submission_must_close_and_survive_reopening(self):
        no_submit = FakeGame((1, 3, 5, 13))
        no_submit.drop.add('daily_souls_submit')
        self.assert_bounded_failure(no_submit)
        self.assertEqual(no_submit.calls.count('daily_souls_open'), 1)
        not_saved = FakeGame((1, 3, 5, 13))
        not_saved.submit_saves = False
        self.assert_bounded_failure(not_saved)
        self.assertEqual(not_saved.calls.count('daily_souls_open'), 2)
        self.assertEqual(not_saved.saved, {1, 3, 5, 13})

    def test_final_close_must_be_confirmed(self):
        game = FakeGame()
        game.drop.add('daily_souls_close')
        self.assert_bounded_failure(game)
        self.assertEqual(game.saved, TARGET)

    def test_midnight_before_open_during_edit_and_after_submit_cannot_succeed(self):
        for boundary in ('before_open', 'daily_souls_add_4', 'daily_souls_submit',
                         'daily_souls_close'):
            game = FakeGame((1, 3, 5, 13))
            if boundary == 'before_open':
                game.day += timedelta(days=1)
            else:
                def cross_day(current, name):
                    if name == boundary:
                        current.day += timedelta(days=1)
                game.on_click = cross_day
            with self.subTest(boundary=boundary):
                self.assert_bounded_failure(game)

    def test_midnight_during_final_confirmation_frame_cannot_succeed(self):
        game = FakeGame(TARGET)
        final_closed_frames = []

        def cross_on_last_frame(current):
            if current.calls and current.calls[-1] == 'daily_souls_close':
                final_closed_frames.append(current.capture_count)
                if len(final_closed_frames) == 2:
                    current.day += timedelta(days=1)

        game.on_capture = cross_on_last_frame
        self.assert_bounded_failure(game)


class FakeBattleTimeout(RuntimeError):
    pass


def integration_class(relative, class_name, names, namespace, base=None):
    """Compile the real integration methods without importing any device stack."""
    tree = ast.parse((ROOT / relative).read_text(encoding='utf-8-sig'))
    source = next(node for node in tree.body
                  if isinstance(node, ast.ClassDef) and node.name == class_name)
    methods = [node for node in source.body
               if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in methods} == names
    bases = [ast.Name(id=base, ctx=ast.Load())] if base else []
    cls = ast.ClassDef(name=class_name, bases=bases,
                       keywords=[], body=methods, decorator_list=[])
    module = ast.Module(body=[cls], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), relative, 'exec'), namespace)
    return namespace[class_name]


def fake_model(conf, character=''):
    return SimpleNamespace(activity_shikigami=conf, restart=SimpleNamespace(
        login_character_config=SimpleNamespace(character=character)))


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        events = self.events
        base_entry = integration_class(
            'tasks/ActivityShikigami/base_act.py', 'BaseAct',
            {'enter_battle', '_before_challenge'},
            dict(random=random, logger=Mock(), TicketsNotEnough=RuntimeError),
        )

        class ParentBattle(base_entry):
            def _run_common(self):
                events.append('battle')
                return 'battle result'

        self.clock = Mock(return_value=DAY)
        self.selector = Mock()
        self.selector.select.side_effect = lambda day, target: events.append('verified') or target
        self.factory = Mock(return_value=self.selector)
        self.chooser = Mock(return_value=TARGET)
        self.namespace = dict(
            BaseAct=ParentBattle, server_date=self.clock, recommendations=souls.recommendations,
            recorded_today=souls.recorded_today, choose_souls=self.chooser, logger=Mock(),
            DailySoulSelector=self.factory, SoulSelectionError=souls.SoulSelectionError,
            BattleTransitionTimeout=FakeBattleTimeout, RuleClick=lambda **kwargs: SimpleNamespace(**kwargs),
        )
        self.subject_type = integration_class(
            'tasks/ActivityShikigami/activities/normal.py', 'NormalClimbAct',
            {'_select_daily_souls', '_run_common', '_before_challenge'},
            self.namespace, base='BaseAct',
        )
        self.subject = self.subject_type()
        self.subject.conf = ActivityShikigami()
        self.subject.conf.general_climb.auto_select_souls = True
        self.subject.config = SimpleNamespace(
            config_name='account_a', model=fake_model(self.subject.conf),
            save=Mock(side_effect=lambda: events.append('save')),
        )
        self.subject.screenshot = Mock()
        self.subject.click = Mock()
        self.subject._soul_selection_view = object()
        self.subject.climb_type = 'pass'

    def record(self, day=DAY, slots=TARGET):
        self.subject.conf.soul_selection_record.date = day.isoformat()
        self.subject.conf.soul_selection_record.slots = sorted(slots)
        self.subject.conf.soul_selection_record.owner = OWNER

    def prepare_entry(self, days):
        def screenshot():
            self.clock.return_value = next(day_sequence)

        day_sequence = iter(days)
        self.subject.screenshot.side_effect = screenshot
        self.subject.is_in_battle = Mock(side_effect=[False] * (len(days) - 1) + [True])
        self.subject.appear = Mock(return_value=False)
        self.subject.appear_then_click = Mock(return_value=False)
        self.subject.I_UI_BACK_RED = self.subject.I_UI_CONFIRM_SAMLL = self.subject.I_UI_CONFIRM = object()
        self.subject.O_FIRE = object()
        self.subject.ocr_appear_click = Mock(side_effect=lambda *args, **kwargs:
                                           self.events.append(f'challenge {self.clock.return_value}') or True)
        self.subject.device = SimpleNamespace(click_record_clear=Mock())

    def test_disabled_option_does_no_selection_or_save(self):
        self.subject.conf.general_climb.auto_select_souls = False
        self.assertEqual(self.subject._run_common(), 'battle result')
        self.factory.assert_not_called()
        self.subject.screenshot.assert_not_called()
        self.subject.click.assert_not_called()
        self.subject.config.save.assert_not_called()
        self.assertEqual(self.events, ['battle'])

    def test_outside_event_does_no_selection(self):
        for day in (date(2026, 9, 8), date(2026, 9, 30), date(2027, 9, 13)):
            self.clock.return_value = day
            self.subject._select_daily_souls()
        self.factory.assert_not_called()
        self.subject.config.save.assert_not_called()

    def test_verified_selection_is_saved_before_battle_and_once_per_day(self):
        for climb_type in ('pass', 'ap'):
            self.subject.climb_type = climb_type
            self.subject._run_common()
        self.assertEqual(self.events, ['verified', 'save', 'battle', 'battle'])
        self.assertEqual(self.subject.conf.soul_selection_record.date, DAY.isoformat())
        self.assertEqual(self.subject.conf.soul_selection_record.slots, sorted(TARGET))
        self.assertEqual(self.subject.conf.soul_selection_record.owner, OWNER)
        self.selector.select.assert_called_once_with(DAY, TARGET)
        self.subject.config.save.assert_called_once_with()

    def test_existing_daily_record_survives_new_task_instance_without_reroll(self):
        self.record()
        config_json = self.subject.conf.model_dump_json()
        self.subject.conf = ActivityShikigami.model_validate_json(config_json)
        self.subject.config.model.activity_shikigami = self.subject.conf
        self.subject._run_common()
        self.factory.assert_not_called()
        self.chooser.assert_not_called()
        self.subject.config.save.assert_not_called()

    def test_next_day_and_invalid_record_trigger_new_selection(self):
        for prior_day, slots in ((DAY - timedelta(days=1), TARGET), (DAY, (1, 4, 13)),
                                (DAY, (1, 4, 13, 13))):
            self.record(prior_day, slots)
            self.subject._select_daily_souls()
        self.assertEqual(self.selector.select.call_count, 3)
        self.assertEqual(self.subject.config.save.call_count, 3)

    def test_midnight_between_battles_selects_new_day(self):
        self.subject._run_common()
        tomorrow = DAY + timedelta(days=1)
        self.clock.return_value = tomorrow
        self.subject._run_common()
        self.assertEqual(self.events, ['verified', 'save', 'battle', 'verified', 'save', 'battle'])
        self.assertEqual(self.subject.conf.soul_selection_record.date, tomorrow.isoformat())
        self.selector.select.assert_called_with(tomorrow, TARGET)

    def test_each_challenge_retry_checks_day_before_clicking(self):
        tomorrow = DAY + timedelta(days=1)
        self.prepare_entry((DAY, tomorrow, tomorrow))
        self.assertTrue(self.subject.enter_battle())
        self.assertEqual(self.events, ['verified', 'save', f'challenge {DAY}',
                                      'verified', 'save', f'challenge {tomorrow}'])
        self.assertEqual(self.subject.conf.soul_selection_record.date, tomorrow.isoformat())
        self.selector.select.assert_called_with(tomorrow, TARGET)
        self.assertEqual(self.subject.ocr_appear_click.call_count, 2)

    def test_failed_selection_at_battle_entry_prevents_entry(self):
        self.prepare_entry((DAY, DAY))
        self.selector.select.side_effect = souls.SoulSelectionError('unconfirmed selection')
        with self.assertRaises(FakeBattleTimeout):
            self.subject.enter_battle()
        self.subject.config.save.assert_not_called()
        self.subject.ocr_appear_click.assert_not_called()

    def test_reloaded_configuration_receives_verified_record(self):
        old_conf = self.subject.conf
        new_conf = ActivityShikigami.model_validate_json(old_conf.model_dump_json())

        def reload_during_selection(day, target):
            self.subject.config.model = fake_model(new_conf)
            self.events.append('verified')
            return target

        self.selector.select.side_effect = reload_during_selection
        self.subject._select_daily_souls()
        self.assertEqual(new_conf.soul_selection_record.date, DAY.isoformat())
        self.assertEqual(new_conf.soul_selection_record.slots, sorted(TARGET))
        self.assertEqual(new_conf.soul_selection_record.owner, OWNER)
        self.assertEqual(old_conf.soul_selection_record.date, '')
        self.assertEqual(old_conf.soul_selection_record.slots, [])
        self.subject.config.save.assert_called_once_with()
        self.subject._select_daily_souls()
        self.selector.select.assert_called_once()

    def test_reloaded_disable_option_overrides_cached_configuration(self):
        new_conf = ActivityShikigami()
        self.subject.config.model = fake_model(new_conf)
        self.subject._select_daily_souls()
        self.assertTrue(self.subject.conf.general_climb.auto_select_souls)
        self.factory.assert_not_called()
        self.subject.config.save.assert_not_called()

    def test_force_recovery_reuses_valid_daily_selection(self):
        existing = frozenset((1, 4, 11, 13))
        self.record(slots=existing)
        self.subject._select_daily_souls(force=True)
        self.selector.select.assert_called_once_with(DAY, existing)
        self.chooser.assert_not_called()

    def test_copied_record_changed_character_and_legacy_record_trigger_selection(self):
        for config_name, character, recorded_owner in (
                ('account_b', '', OWNER), ('account_a', 'role_b', OWNER), ('account_a', '', '')):
            with self.subTest(config=config_name, character=character, owner=recorded_owner):
                self.record()
                self.subject.conf.soul_selection_record.owner = recorded_owner
                self.subject.config.config_name = config_name
                self.subject.config.model.restart.login_character_config.character = character
                self.subject._select_daily_souls()
                self.assertEqual(self.subject.conf.soul_selection_record.owner,
                                 config_name + '\n' + character)
        self.assertEqual(self.selector.select.call_count, 3)
        self.assertEqual(self.subject.config.save.call_count, 3)

    def test_failed_selection_preserves_record_and_prevents_parent_battle(self):
        self.record(DAY - timedelta(days=1))
        before = self.subject.conf.soul_selection_record.model_dump()
        self.selector.select.side_effect = souls.SoulSelectionError('unconfirmed selection')
        with self.assertRaises(FakeBattleTimeout):
            self.subject._run_common()
        self.assertEqual(self.subject.conf.soul_selection_record.model_dump(), before)
        self.subject.config.save.assert_not_called()
        self.assertNotIn('battle', self.events)

    def test_unexpected_ui_error_also_cannot_save_or_start_battle(self):
        self.selector.select.side_effect = OSError('capture failed')
        with self.assertRaises(OSError):
            self.subject._run_common()
        self.subject.config.save.assert_not_called()
        self.assertNotIn('battle', self.events)
        self.assertEqual(self.subject.conf.soul_selection_record.date, '')

    def test_selection_is_skipped_for_other_battle_pages(self):
        for climb_type in ('boss', 'ap100'):
            self.subject.climb_type = climb_type
            self.subject._run_common()
        self.factory.assert_not_called()
        self.assertEqual(self.events, ['battle', 'battle'])

    def test_selection_click_adapter_keeps_roi_and_control_name(self):
        self.subject._select_daily_souls()
        capture, click, view = self.factory.call_args.args
        self.assertIs(capture, self.subject.screenshot)
        self.assertIs(view, self.subject._soul_selection_view)
        roi = (10, 20, 30, 40)
        click(roi, 'daily_souls_open')
        rule = self.subject.click.call_args.args[0]
        self.assertEqual((rule.roi_front, rule.roi_back, rule.name), (roi, roi, 'daily_souls_open'))

    def test_accounts_do_not_share_selection_records(self):
        first, second = ActivityShikigami(), ActivityShikigami()
        first.soul_selection_record.date = DAY.isoformat()
        first.soul_selection_record.slots.append(1)
        self.assertEqual(second.soul_selection_record.date, '')
        self.assertEqual(second.soul_selection_record.slots, [])


if __name__ == '__main__':
    unittest.main()
