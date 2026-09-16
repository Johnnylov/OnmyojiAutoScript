"""Offline regressions for raid transitions; no game, account, or RPC is used.

Run: toolkit/python.exe -m unittest discover -s dev_tools -p test_raid_transitions.py -v
"""

import ast
from datetime import date, datetime, timedelta
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
PREPARE, BATTLE, RESULT, REWARD = 'prepare', 'battle', 'result', 'reward'


class TransitionTimeout(Exception):
    pass


def methods_from_source(relative_path, class_name, names, namespace):
    tree = ast.parse((ROOT / relative_path).read_text(encoding='utf-8-sig'))
    source_class = next(node for node in tree.body
                        if isinstance(node, ast.ClassDef) and node.name == class_name)
    selected = [node for node in source_class.body
                if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in selected} == set(names)
    cls = ast.ClassDef(name='Subject', bases=[], keywords=[], body=selected, decorator_list=[])
    module = ast.Module(body=[ast.ImportFrom(module='__future__',
                        names=[ast.alias(name='annotations')], level=0), cls], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(ROOT / relative_path), 'exec'), namespace)
    return namespace['Subject']


class World:
    def __init__(self, frames, kind='raid'):
        self.frames = frames
        self.frame = (None, set())
        self.index = -1
        self.now = 0.0
        self.clicks = []
        self.actions = SimpleNamespace(EXIT_WIN='win', EXIT_LOSE='lose', QUICK_EXIT='quick')
        namespace = dict(time=SimpleNamespace(monotonic=lambda: self.now), logger=Mock(),
                         page_battle_prepare=PREPARE, page_battle=BATTLE,
                         page_battle_result=RESULT, page_reward=REWARD,
                         BattleTransitionTimeout=TransitionTimeout, BattleAction=self.actions,
                         QUICK_EXIT_WAIT_TIMEOUT=30,
                         GameUi=SimpleNamespace(detect_page_in=self.detect))
        if kind == 'raid':
            cls = methods_from_source('tasks/RealmRaid/script_task.py', 'ScriptTask',
                                      ['_raid_battle_started', 'fire', 'fire_again'], namespace)
        else:
            cls = methods_from_source('tasks/Component/GeneralBattle/general_battle.py',
                                      'GeneralBattle', ['exit_battle', '_resolve_action'], namespace)
        self.task = cls()
        self.task._check_battle_connection = Mock()
        self.task.screenshot = self.screenshot
        self.task.appear = lambda marker: marker in self.frame[1]
        self.task.click = self.click
        self.task.device = SimpleNamespace(screenshot_interval_set=Mock())
        self.task.partition = ['target_1']
        for marker in ('I_RR_PERSON', 'I_FIRE', 'I_FIRE_AGAIN', 'I_SHOW_AGAIN',
                       'I_FRESH_ENSURE', 'I_EXIT', 'I_EXIT_ENSURE'):
            setattr(self.task, marker, marker)

    def screenshot(self):
        self.index += 1
        self.now += 1
        if self.index > 100:
            raise AssertionError('Unbounded screenshot loop')
        self.frame = self.frames[min(self.index, len(self.frames) - 1)]

    def detect(self, task, *pages, include_global=False):
        visible = self.frame[0] if isinstance(self.frame[0], tuple) else (self.frame[0],)
        return next((page for page in pages if page in visible), None)

    def click(self, marker):
        self.clicks.append(marker)


def frame(page=None, *markers):
    return page, set(markers)


class RaidEntryTests(unittest.TestCase):
    def test_entry_waits_through_loading_for_positive_battle_match(self):
        world = World([frame('raid', 'I_RR_PERSON'),
                       frame('raid', 'I_RR_PERSON', 'I_FIRE'), frame(), frame(PREPARE)])
        self.assertTrue(world.task.fire(1))
        self.assertEqual(world.clicks, ['target_1', 'I_FIRE'])
        self.assertEqual(world.index, 3)

    def test_disappearing_person_marker_is_not_entry_success(self):
        world = World([frame('raid', 'I_RR_PERSON'), frame()])
        with self.assertRaises(TransitionTimeout):
            world.task.fire(1)
        self.assertEqual(world.clicks, ['target_1'])

    def test_lingering_challenge_is_not_clicked_or_reselected_after_submission(self):
        world = World([frame('raid', 'I_RR_PERSON', 'I_FIRE')])
        with self.assertRaises(TransitionTimeout):
            world.task.fire(1)
        self.assertEqual(world.clicks, ['I_FIRE'])

    def test_target_selection_has_a_finite_budget(self):
        world = World([frame('raid', 'I_RR_PERSON')])
        with self.assertRaises(TransitionTimeout):
            world.task.fire(1)
        self.assertEqual(world.clicks, ['target_1'] * 3)

    def test_unknown_page_with_false_challenge_match_is_not_clicked(self):
        world = World([frame(None, 'I_FIRE')])
        with self.assertRaises(TransitionTimeout):
            world.task.fire(1)
        self.assertEqual(world.clicks, [])

    def test_existing_battle_needs_no_extra_click(self):
        for page in (PREPARE, BATTLE):
            with self.subTest(page=page):
                world = World([frame(page)])
                self.assertTrue(world.task.fire(1))
                self.assertEqual(world.clicks, [])

    def test_settlement_is_not_mistaken_for_new_battle(self):
        world = World([frame(RESULT)])
        with self.assertRaises(TransitionTimeout):
            world.task.fire(1)
        self.assertEqual(world.clicks, [])


class RaidRetryTests(unittest.TestCase):
    def test_retry_waits_for_prepare_after_button_disappears(self):
        world = World([frame(RESULT, 'I_FIRE_AGAIN'), frame(), frame(PREPARE)])
        self.assertTrue(world.task.fire_again())
        self.assertEqual(world.clicks, ['I_FIRE_AGAIN'])
        self.assertEqual(world.index, 2)

    def test_missing_retry_marker_is_not_success(self):
        world = World([frame(RESULT, 'I_FIRE_AGAIN'), frame()])
        with self.assertRaises(TransitionTimeout):
            world.task.fire_again()
        self.assertEqual(world.clicks, ['I_FIRE_AGAIN'])

    def test_lingering_retry_is_submitted_only_once_without_prompt(self):
        world = World([frame(RESULT, 'I_FIRE_AGAIN')])
        with self.assertRaises(TransitionTimeout):
            world.task.fire_again()
        self.assertEqual(world.clicks, ['I_FIRE_AGAIN'])

    def test_prompt_is_confirmed_once_before_one_additional_submission(self):
        prompt = frame(None, 'I_FRESH_ENSURE', 'I_SHOW_AGAIN', 'I_FIRE_AGAIN')
        world = World([frame(RESULT, 'I_FIRE_AGAIN'), prompt, prompt, prompt,
                       frame(RESULT, 'I_FIRE_AGAIN'), frame(PREPARE)])
        self.assertTrue(world.task.fire_again())
        self.assertEqual(world.clicks, ['I_FIRE_AGAIN', 'I_SHOW_AGAIN',
                                        'I_FRESH_ENSURE', 'I_FIRE_AGAIN'])

    def test_prompt_without_checkbox_can_enter_battle_directly(self):
        world = World([frame(RESULT, 'I_FIRE_AGAIN'), frame(None, 'I_FRESH_ENSURE'),
                       frame(), frame(BATTLE)])
        self.assertTrue(world.task.fire_again())
        self.assertEqual(world.clicks, ['I_FIRE_AGAIN', 'I_FRESH_ENSURE'])

    def test_persistent_prompt_is_not_confirmed_repeatedly(self):
        world = World([frame(RESULT, 'I_FIRE_AGAIN'),
                       frame(None, 'I_FRESH_ENSURE', 'I_SHOW_AGAIN', 'I_FIRE_AGAIN')])
        with self.assertRaises(TransitionTimeout):
            world.task.fire_again()
        self.assertEqual(world.clicks, ['I_FIRE_AGAIN', 'I_SHOW_AGAIN', 'I_FRESH_ENSURE'])

    def test_unrelated_confirmation_is_not_clicked_without_retry_request(self):
        world = World([frame(None, 'I_FRESH_ENSURE', 'I_SHOW_AGAIN')])
        with self.assertRaises(TransitionTimeout):
            world.task.fire_again()
        self.assertEqual(world.clicks, [])

    def test_retry_budget_stops_after_second_submission(self):
        world = World([frame(RESULT, 'I_FIRE_AGAIN'), frame(None, 'I_FRESH_ENSURE'),
                       frame(RESULT, 'I_FIRE_AGAIN')])
        with self.assertRaises(TransitionTimeout):
            world.task.fire_again()
        self.assertEqual(world.clicks, ['I_FIRE_AGAIN', 'I_FRESH_ENSURE', 'I_FIRE_AGAIN'])


class BattleExitTests(unittest.TestCase):
    def world(self, frames):
        return World(frames, kind='battle')

    def test_existing_settlement_wins_over_false_exit_confirmation(self):
        for page in (RESULT, REWARD):
            with self.subTest(page=page):
                world = self.world([frame(page, 'I_EXIT', 'I_EXIT_ENSURE')])
                self.assertTrue(world.task.exit_battle())
                self.assertEqual(world.clicks, [])

    def test_overlapping_battle_and_result_markers_prefer_settlement(self):
        world = self.world([frame(BATTLE, 'I_EXIT'), frame(BATTLE, 'I_EXIT'),
                            frame((BATTLE, RESULT), 'I_EXIT_ENSURE')])
        self.assertTrue(world.task.exit_battle())
        self.assertEqual(world.clicks, ['I_EXIT'])

    def test_settlement_during_exit_prevents_any_more_confirmation_clicks(self):
        world = self.world([frame(BATTLE, 'I_EXIT'), frame(BATTLE, 'I_EXIT'),
                            frame(RESULT, 'I_EXIT_ENSURE')])
        self.assertTrue(world.task.exit_battle())
        self.assertEqual(world.clicks, ['I_EXIT'])

    def test_exit_and_confirmation_are_each_submitted_once(self):
        world = self.world([frame(BATTLE, 'I_EXIT'), frame(BATTLE, 'I_EXIT'),
                            frame(None, 'I_EXIT_ENSURE'), frame(None, 'I_EXIT_ENSURE'),
                            frame(REWARD, 'I_EXIT_ENSURE')])
        self.assertTrue(world.task.exit_battle())
        self.assertEqual(world.clicks, ['I_EXIT', 'I_EXIT_ENSURE'])

    def test_stalled_confirmation_ends_the_current_click_loop(self):
        world = self.world([frame(BATTLE, 'I_EXIT'), frame(BATTLE, 'I_EXIT'),
                            frame(None, 'I_EXIT_ENSURE')])
        with self.assertRaises(TransitionTimeout):
            world.task.exit_battle()
        self.assertEqual(world.clicks, ['I_EXIT', 'I_EXIT_ENSURE'])

    def test_exit_button_without_confirmation_is_not_repeated(self):
        world = self.world([frame(BATTLE, 'I_EXIT')])
        with self.assertRaises(TransitionTimeout):
            world.task.exit_battle()
        self.assertEqual(world.clicks, ['I_EXIT'])

    def test_unknown_page_does_not_receive_exit_click(self):
        world = self.world([frame(None, 'I_EXIT', 'I_EXIT_ENSURE')])
        self.assertFalse(world.task.exit_battle())
        self.assertEqual(world.clicks, [])

    def test_default_refreshes_a_stale_battle_frame(self):
        world = self.world([frame(RESULT, 'I_EXIT_ENSURE')])
        world.frame = frame(BATTLE, 'I_EXIT')
        self.assertTrue(world.task.exit_battle())
        self.assertEqual(world.index, 0)
        self.assertEqual(world.clicks, [])

    def test_skip_first_reuses_current_settlement(self):
        world = self.world([frame(BATTLE, 'I_EXIT')])
        world.frame = frame(RESULT, 'I_EXIT_ENSURE')
        self.assertTrue(world.task.exit_battle(skip_first=True))
        self.assertEqual(world.index, -1)
        self.assertEqual(world.clicks, [])

    def test_outer_quick_exit_timeout_is_recoverable(self):
        world = self.world([frame()])
        world.task.exit_battle = Mock(return_value=False)
        context = SimpleNamespace(quick_exit_timer=SimpleNamespace(reached=lambda: True))
        with self.assertRaises(TransitionTimeout):
            world.task._resolve_action(world.actions.QUICK_EXIT, context)


class TimeoutSchedulerTests(unittest.TestCase):
    def setUp(self):
        namespace = dict(logger=Mock(), TaskEnd=type('TaskEnd', (Exception,), {}),
                         BattleTransitionTimeout=TransitionTimeout,
                         ActivityPreparationTimeout=type('ActivityPreparationTimeout', (TransitionTimeout,), {}),
                         datetime=datetime, timedelta=timedelta, date=date,
                         _log_switch_lock=nullcontext(), IS_WINDOWS=False,
                         ScriptRuntimeDecision=SimpleNamespace(RESCHEDULE='reschedule', FAILED='failed'),
                         del_cached_property=Mock(), inflection=SimpleNamespace(camelize=lambda name: name),
                         convert_to_underscore=lambda _: 'realm_raid')
        cls = methods_from_source('script.py', 'Script',
                                  ['_handle_task_exception', 'loop'], namespace)
        self.script = cls()
        self.script.save_error_log = Mock()
        self.script._set_task_runtime_outcome = Mock()
        self.schedule = SimpleNamespace(next_run=datetime.now() + timedelta(hours=8))
        self.script.config = SimpleNamespace(
            model=SimpleNamespace(realm_raid=SimpleNamespace(scheduler=self.schedule)),
            task_delay=Mock(), task_call=Mock(),
        )

    def test_transition_timeout_defers_and_continues_without_fatal_exit(self):
        with patch('builtins.exit', side_effect=AssertionError('Must keep scheduler running')):
            self.assertTrue(self.script._handle_task_exception(
                TransitionTimeout('entry timed out'), 'RealmRaid'))
        self.script.config.task_delay.assert_called_once_with(task='RealmRaid', success=False)
        self.script.config.task_call.assert_called_once_with('Restart')
        self.script.save_error_log.assert_called_once()
        self.script._set_task_runtime_outcome.assert_called_once_with(
            task='RealmRaid', status='skipped', wait_until=self.schedule.next_run)

    def test_expired_failure_interval_is_clamped_to_future(self):
        self.schedule.next_run = datetime.now() - timedelta(seconds=1)
        before = datetime.now().replace(microsecond=0)
        self.assertTrue(self.script._handle_task_exception(
            TransitionTimeout('exit timed out'), 'RealmRaid'))
        calls = self.script.config.task_delay.call_args_list
        self.assertEqual(len(calls), 2)
        target = calls[1].kwargs['target']
        self.assertGreaterEqual(target, before + timedelta(minutes=1))
        self.assertEqual(calls[1].kwargs, dict(task='RealmRaid', target=target, server=False))
        self.script._set_task_runtime_outcome.assert_called_once_with(
            task='RealmRaid', status='skipped', wait_until=target)

    def test_actual_loop_runs_recovery_and_next_task_after_timeout(self):
        class EndLoop(BaseException):
            pass

        pending = ['RealmRaid', 'SoulsTidy']
        executed = []
        self.script.config_name = 'offline'
        self.script.config.script = SimpleNamespace(
            device=SimpleNamespace(run_background_only=True),
            error=SimpleNamespace(handle_error=False),
        )
        self.script.anti_ban_guard = Mock()
        self.script.device = Mock()
        self.script.runtime = SimpleNamespace(prepare_task_execution=Mock(return_value='ready'))
        self.script.is_first_task = False
        self.script.failure_record = {'RealmRaid': 2}
        self.script.config.task_call = Mock(side_effect=lambda name: pending.insert(0, name))

        def get_next_task():
            if not pending:
                raise EndLoop()
            return pending.pop(0)

        def run(command):
            executed.append(command)
            if command == 'RealmRaid':
                return self.script._handle_task_exception(TransitionTimeout('timed out'), command)
            return True

        self.script.get_next_task = get_next_task
        self.script.run = run
        with patch('builtins.exit', side_effect=AssertionError('Must not terminate scheduler')):
            with self.assertRaises(EndLoop):
                self.script.loop()
        self.assertEqual(executed, ['RealmRaid', 'Restart', 'SoulsTidy'])
        self.assertEqual(self.script.failure_record['RealmRaid'], 0)

    def test_click_timeout_reaches_scheduler_as_a_skip(self):
        world = World([frame('raid', 'I_RR_PERSON', 'I_FIRE')])
        try:
            world.task.fire(1)
        except TransitionTimeout as error:
            self.assertTrue(self.script._handle_task_exception(error, 'RealmRaid'))
        else:
            self.fail('Unconfirmed battle must end the current task')
        self.assertEqual(world.clicks, ['I_FIRE'])
        self.script.config.task_delay.assert_called_once_with(task='RealmRaid', success=False)
        self.script.config.task_call.assert_called_once_with('Restart')


if __name__ == '__main__':
    unittest.main()
