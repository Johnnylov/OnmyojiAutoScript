"""Replay actual task methods without importing device, OCR or game assets."""
import ast
from datetime import datetime
from enum import Enum
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from module.scheduling.task_metrics import (
    COUNT_TARGETS, abyss_progress, begin_battle, execution_for, finish_battle,
    report_count_progress,
)

ROOT = Path(__file__).resolve().parents[2]


def methods(path, names, **namespace):
    tree = ast.parse((ROOT / path).read_text(encoding='utf-8'))
    body = [n for node in tree.body if isinstance(node, ast.ClassDef)
            for n in node.body if isinstance(n, ast.FunctionDef) and n.name in names]
    namespace.update(begin_battle=begin_battle, finish_battle=finish_battle,
                     execution_for=execution_for, abyss_progress=abyss_progress,
                     report_count_progress=report_count_progress, logger=Mock())
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[
        ast.alias(name='annotations')], level=0), *body], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), path, 'exec'), namespace)
    return type('TaskReplay', (), {name: namespace[name] for name in names})


class Metrics:
    def __init__(self):
        self.next_token = 0
        self.pending = None
        self.battles = []
        self.progress = []

    def begin_battle(self):
        self.next_token += 1
        self.pending = self.next_token
        return self.pending

    def finish_battle(self, token, result):
        if token != self.pending:
            return False
        self.pending = None
        self.battles.append(result)
        return True

    def report_progress(self, current, target=None, **kwargs):
        self.progress.append((current, target, kwargs))


class FakeTimer:
    def __init__(self, *args):
        pass

    def start(self):
        return self

    def reached(self):
        return False

    def reset(self):
        pass

    def clear(self):
        pass


class TaskMetricHooksTests(unittest.TestCase):
    def test_named_count_targets_report_actual_attempts_not_victories(self):
        for command, path in COUNT_TARGETS.items():
            with self.subTest(command=command):
                metrics = Metrics()
                config = NS(task=NS(command=command), solana_execution=metrics)
                branch = config
                for part in path[:-1]:
                    child = NS()
                    setattr(branch, part, child)
                    branch = child
                setattr(branch, path[-1], 18)
                task = NS(config=config, current_count=3, limit_count=None)
                report_count_progress(task)
                self.assertEqual(metrics.progress, [(3, 18, {'unit': '次挑战'})])
                self.assertEqual(metrics.battles, [])

    def test_unrelated_count_is_not_reinterpreted_and_zero_limit_is_finite(self):
        metrics = Metrics()
        task = NS(config=NS(task=NS(command='DemonEncounter'), solana_execution=metrics),
                  current_count=4, limit_count=20)
        report_count_progress(task)
        self.assertEqual(metrics.progress, [])
        task.config.task.command = 'Orochi'
        task.limit_count = 0
        report_count_progress(task)
        self.assertEqual(metrics.progress[-1][:2], (4, 0))

    def common_battle(self, frames, fail=False):
        metrics = Metrics()
        frames = iter(frames)
        subject = methods('tasks/Component/GeneralBattle/general_battle.py',
                          ['run_general_battle'],
                          GeneralBattleConfig=lambda: NS(),
                          GameUi=NS(detect_page_in=lambda *a, **k: next(frames)),
                          page_battle_prepare='prepare', page_battle='battle',
                          page_battle_result='result', page_reward='reward')
        task = subject()
        task.config = NS(task=NS(command='FallenSun'), solana_execution=metrics)
        task.current_count, task.limit_count = 0, 20
        task._custom_pages_registered = True
        task.device = Mock()
        task.screenshot = Mock()
        task._check_battle_connection = Mock()
        task._tick_long_battle = Mock()
        task._tick_timeout = Mock(side_effect=RuntimeError('timeout') if fail else None)
        task._build_context = lambda *a: NS(metric_battle_token=begin_battle(task),
            reward_no_battle_ts=None, quick_exit=False, last_page=None)
        task._exit_matcher = Mock()
        task._sync_prepare_click_timer = Mock()
        task._ensure_battle_stuck_guard = Mock()
        task.gb_page_handle_dict = {p: Mock(return_value=None)
                                   for p in ('prepare', 'battle', 'result', 'reward')}
        task._handle_missing_battle_page = Mock(return_value=False)
        task._resolve_action = lambda action, context: action
        if fail:
            with self.assertRaises(RuntimeError):
                task.run_general_battle()
        else:
            task.run_general_battle()
        self.assertIsNone(task._battle_context)
        return metrics

    def test_common_results_rewards_and_custom_handlers_count_once(self):
        metrics = self.common_battle(['prepare', 'battle', 'result', 'result', 'reward', None])
        self.assertEqual(metrics.battles, ['settled'])
        self.assertEqual(metrics.progress[-1], (1, 20, {'unit': '次挑战'}))

    def test_entry_unknown_exit_and_timeout_do_not_count_battle(self):
        self.assertEqual(self.common_battle(['prepare', 'battle', None]).battles, [])
        self.assertEqual(self.common_battle([], fail=True).battles, [])

    def test_continuous_round_gets_new_token_without_recounting_settlement(self):
        subject = methods('tasks/Component/GeneralBattle/general_battle.py',
                          ['_reset_round_context'], Timer=FakeTimer,
                          BattleBehaviorState=lambda: NS())
        task = subject()
        metrics = Metrics()
        task.config = NS(task=NS(command='EternitySea'), solana_execution=metrics)
        task.current_count, task.limit_count = 1, 10
        task._resolve_battle_timeout = lambda config: 300
        context = NS(prepare_click_timer=FakeTimer(), metric_battle_token=begin_battle(task))
        finish_battle(task, context.metric_battle_token)
        task._reset_round_context(context, NS(quick_exit=False), continuous_count=2)
        finish_battle(task, context.metric_battle_token)
        finish_battle(task, context.metric_battle_token)
        self.assertEqual(metrics.battles, ['settled', 'settled'])

    def test_abyss_unique_valid_planned_targets_exclude_unavailable_from_done(self):
        self.assertEqual(abyss_progress(['A-1', 'A-1', 'A-2', 'B-1', 'invalid'],
                                      ['A-1', 'A-1', 'D-1'], ['A-2', 'B-1']), (1, 3, 2))

    def test_abyss_saved_progress_and_dynamic_extra_target(self):
        subject = methods('tasks/AbyssShadows/script_task.py',
                          ['init_list_from_cfg', '_report_abyss_progress'],
                          datetime=datetime, CodeList=lambda text: list(filter(None, text.split(';'))))
        task = subject()
        metrics = Metrics()
        saved = NS(save_date=datetime.today().strftime('%Y-%m-%d'),
                   done='A-1;A-1', unavailable='A-2')
        cfg = NS(saved_params=saved,
                 process_manage=NS(attack_order='A-1;A-2;B-1', try_complete_enemy_count=True))
        task.config = NS(solana_execution=metrics, model=NS(abyss_shadows=cfg))
        task._metric_planned_targets = set()
        task.init_list_from_cfg()
        self.assertEqual(metrics.progress[-1],
                         (1, 3, {'unit': '个目标', 'phase': '目标处理（不可用 1 个）'}))
        task._report_abyss_progress('D-1')
        task.done_list.append('D-1')
        task._report_abyss_progress()
        self.assertEqual(metrics.progress[-1][:2], (2, 4))

    def abyss_battle(self, frames, quit_condition=False):
        subject = methods('tasks/AbyssShadows/script_task.py', ['run_battle', 'quit_battle'],
                          Timer=FakeTimer, EnemyType=Enum('EnemyType', 'BOSS GENERAL ELITE'))
        task = subject()
        metrics = Metrics()
        condition = NS(is_need_damage_value=lambda: False, is_valid=lambda damage: quit_condition,
                       is_passed=lambda: quit_condition)
        settings = NS(enable_switch_preset_in_as=False,
                      generate_quit_condition=lambda enemy: condition,
                      is_need_mark_main=lambda enemy: False)
        task.config = NS(solana_execution=metrics, model=NS(abyss_shadows=NS(process_manage=settings)))
        task.device = Mock()
        frames = iter(frames)
        task.frame = None
        task.screenshot = lambda: setattr(task, 'frame', next(frames))
        task.appear = lambda marker, **kwargs: marker == task.frame
        task.appear_then_click = task.appear
        task.click = Mock(return_value=True)
        task.wait_until_appear = Mock()
        task.ui_click_until_disappear = Mock()
        for name in ('I_PREPARE_HIGHLIGHT', 'I_WIN', 'I_REWARD', 'I_ABYSS_NAVIGATION',
                     'I_EXIT_ENSURE', 'I_EXIT'):
            setattr(task, name, name)
        task.run_battle(NS(get_enemy_type=lambda: NS(name='test')))
        return metrics

    def test_abyss_battle_result_and_reward_count_once(self):
        metrics = self.abyss_battle(['I_WIN', 'I_REWARD', 'I_ABYSS_NAVIGATION'])
        self.assertEqual(metrics.battles, ['won'])

    def test_abyss_condition_exit_without_settlement_is_not_a_completed_battle(self):
        metrics = self.abyss_battle(['battle', 'I_ABYSS_NAVIGATION'], quit_condition=True)
        self.assertEqual(metrics.battles, [])

    def test_abyss_settlement_observed_during_condition_exit_is_counted_once(self):
        metrics = self.abyss_battle(['battle', 'I_WIN', 'I_REWARD', 'I_ABYSS_NAVIGATION'],
                                    quit_condition=True)
        self.assertEqual(metrics.battles, ['won'])

    def test_absent_runtime_preserves_standalone_task_behavior(self):
        task = NS(config=NS())
        self.assertIsNone(begin_battle(task))
        self.assertFalse(finish_battle(task, None))
        report_count_progress(task)


if __name__ == '__main__':
    unittest.main()
