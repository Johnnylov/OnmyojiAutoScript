"""Menu coverage and real task-loop telemetry without loading a game/device."""
import ast
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from module.scheduling.task_metrics import (
    BATTLE_TASKS, NON_BATTLE_TASKS, TASK_METRIC_COMMANDS,
    report_count_progress, report_task_progress,
)
import test_scheduler_progress as worker
from test_task_metric_hooks import Metrics, methods

ROOT = Path(__file__).resolve().parents[2]


def executable_menu():
    """Use the actual menu constructor, excluding its unrelated import graph."""
    tree = ast.parse((ROOT / 'module/config/config_menu.py').read_text(encoding='utf-8'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ConfigMenu')
    constructor = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '__init__')
    namespace = {}
    exec(compile(ast.Module(body=[constructor], type_ignores=[]), '<menu>', 'exec'), namespace)
    menu = NS()
    namespace['__init__'](menu)
    return {task for group, tasks in menu.menu.items() if group != 'Tools'
            for task in tasks if (ROOT / 'tasks' / task / 'script_task.py').is_file()}


class AllTaskMetricsTests(unittest.TestCase):
    def test_every_current_executable_menu_item_has_a_contract(self):
        self.assertFalse(BATTLE_TASKS & NON_BATTLE_TASKS)
        self.assertEqual(executable_menu(), TASK_METRIC_COMMANDS)

    def test_each_menu_task_publishes_lifecycle_without_preview_and_never_invents_success(self):
        for command in sorted(executable_menu()):
            for outcome in ('succeeded', 'failed', 'interrupted'):
                with self.subTest(command=command, outcome=outcome):
                    harness = worker.ProgressSnapshotTests()
                    harness.setUp()
                    harness.begin(False, command)
                    initial = harness.execution.progress_snapshot()
                    self.assertTrue(initial['count_supported'])
                    self.assertEqual((initial['current_count'], initial['target_count']), (0, 1))
                    self.assertEqual(initial['count_unit'], '项')
                    if command in BATTLE_TASKS:
                        self.assertEqual(initial['battle_count'], 0)
                        token = harness.execution.begin_battle()
                        self.assertEqual(harness.execution.progress_snapshot()['battle_count'], 0)
                        self.assertTrue(harness.execution.finish_battle(token, 'settled'))
                        self.assertFalse(harness.execution.finish_battle(token, 'settled'))
                    else:
                        self.assertIsNone(initial['battle_count'])
                        self.assertFalse(initial['battle_supported'])
                        self.assertEqual(initial['battle_unavailable_reason'], 'not_applicable')
                    harness.execution.finish(outcome)
                    finished = next(e['payload'] for e in harness.bridge.events if e['type'] == 'run.finished')
                    self.assertEqual(finished['current_count'], int(outcome == 'succeeded'))
                    self.assertEqual(finished['battle_count'], 1 if command in BATTLE_TASKS else None)

    def test_phase_and_task_counters_do_not_read_general_battle_counter(self):
        cases = [
            ('Moonlight', {'moonlight_count': 8}, NS(general_config=NS(challenge_limit=50)),
             'moonlight', 8, 50, None),
            ('MetaDemon', {'total_count': 11}, NS(meta_demon_config=NS(limit_count=100)),
             'meta_demon', 11, 100, None),
            ('MartialTournament', {'current_mode': 'ap', 'current_count': 4},
             NS(general_climb=NS(ap_limit=30, pass_limit=20)),
             'martial_tournament', 4, 30, '体力挑战'),
            ('ActivityShikigami', {'count_map': {'pass': 12, 'ap': 7}, 'climb_type': 'ap'},
             NS(general_climb=NS(run_sequence_v=['pass', 'ap'], pass_limit=20, ap_limit=100)),
             'activity_shikigami', 7, 100, '体力挑战'),
        ]
        for command, attributes, options, name, current, target, phase in cases:
            with self.subTest(command=command):
                metrics = Metrics()
                config = NS(task=NS(command=command), solana_execution=metrics, **{name: options})
                task = NS(config=config, **{'current_count': 999, **attributes})
                report_count_progress(task)
                self.assertEqual(metrics.progress, [(current, target, {'unit': '次挑战', 'phase': phase})])
                self.assertEqual(metrics.battles, [])

    def test_six_realms_counts_only_returned_rounds_and_keeps_failure_incomplete(self):
        kinds = Enum('SixRealmsType', 'MOON_SEA PEACOCK_KINGDOM')
        subject = methods('tasks/SixRealms/script_task.py', ['run'], datetime=datetime,
                          SixRealmsType=kinds, report_task_progress=report_task_progress,
                          page_main='main', TaskEnd=RuntimeError)
        task = subject()
        metrics = Metrics()
        gate = NS(limit_count=3, limit_time_v=timedelta(hours=1), six_realms_type=kinds.MOON_SEA)
        task.config = NS(solana_execution=metrics,
                         model=NS(six_realms=NS(six_realms_gate=gate, switch_soul_config=NS())))
        task.start_time = datetime.now()
        task.switch_current_soul = Mock()
        task.moon_sea = NS(run=Mock(side_effect=[None, ValueError('unverified exit')]))
        with self.assertRaisesRegex(ValueError, 'unverified exit'):
            task.run()
        self.assertEqual([p[:2] for p in metrics.progress], [(0, 3), (1, 3)])
        self.assertTrue(all(p[2]['unit'] == '轮六道' for p in metrics.progress))
        self.assertEqual(metrics.battles, [])

    def test_area_boss_keeps_target_order_and_does_not_complete_failed_targets(self):
        subject = methods('tasks/AreaBoss/script_task.py', ['run'],
                          report_task_progress=report_task_progress,
                          page_shikigami_records='records', page_area_boss='boss',
                          page_main='main', TaskEnd=RuntimeError)
        for reward, results, expected in ((False, [True, False, True], [0, 1, 2]),
                                           (True, [False, True], [0, 1, 2])):
            with self.subTest(reward=reward):
                task = subject()
                metrics = Metrics()
                task.config = NS(solana_execution=metrics, area_boss=NS(
                    general_battle=NS(lock_team_enable=True),
                    boss=NS(boss_reward=reward, boss_number=3, use_collect=False),
                    switch_soul=NS(enable=False, enable_switch_by_name=False)))
                task.check_can_run = task.goto_page = task.open_filter = task.switch_to_famous = Mock()
                task.I_BATTLE_1, task.I_BATTLE_2, task.I_BATTLE_3 = 1, 2, 3
                task.fight_reward_boss = Mock(return_value=True)
                task.boss_fight = Mock(side_effect=results)
                task.set_next_run = Mock()
                with self.assertRaises(RuntimeError):
                    task.run()
                self.assertEqual([p[0] for p in metrics.progress], expected)
                self.assertEqual([call.args[0] for call in task.boss_fight.call_args_list],
                                 [1, 2] if reward else [1, 2, 3])
                self.assertEqual(metrics.battles, [])

    def test_realm_raid_uses_verified_ticket_spending_not_deliberate_retreat_count(self):
        subject = methods('tasks/RealmRaid/script_task.py', ['check_ticket'],
                          report_task_progress=report_task_progress)
        task = subject()
        metrics = Metrics()
        task.config = NS(solana_execution=metrics, realm_raid=NS(raid_config=NS(number_attack=3)))
        task.init_tickets = -1
        task.wait_until_appear = task.screenshot = task.reward_detect_click = Mock()
        task.I_BACK_RED = 'back'
        task.device = Mock()
        task.O_NUMBER = NS(ocr=Mock(side_effect=[(4, 26, 30), (2, 28, 30), (99, -69, 30), (0, 30, 30)]))
        for _ in range(4):
            task.check_ticket()
        self.assertEqual([p[:2] for p in metrics.progress], [(0, 3), (2, 3), (4, 3)])
        self.assertTrue(all(p[2]['unit'] == '张突破券' for p in metrics.progress))
        self.assertEqual(metrics.battles, [])

    def test_dye_trials_duplicate_result_and_timeout_are_not_extra_battles(self):
        clock = NS(start=Mock(), reset=Mock(), reached=lambda: task.frame == 'timeout')
        subject = methods('tasks/DyeTrials/script_task.py', ['get_all'],
                          Timer=lambda *_: clock, time=NS(sleep=Mock()),
                          RestartAssets=NS(I_HARVEST_CHAT_CLOSE='chat'),
                          report_task_progress=report_task_progress)
        task = subject()
        metrics = Metrics()
        task.config = NS(solana_execution=metrics, notifier=Mock())
        frames = iter(['challenge', 'reward', 'challenge', 'win', 'win', 'challenge', 'timeout'])
        task.frame = None
        task.screenshot = lambda: setattr(task, 'frame', next(frames))
        task.appear = task.appear_then_click = lambda marker, **kwargs: marker == task.frame
        task.ui_reward_appear_click = lambda: task.frame == 'reward'
        task.ui_click_until_disappear = Mock()
        task.device = Mock()
        task.I_FP_CHALLENGE, task.I_BATTLE_SUCCESS = 'challenge', 'win'
        for name in ('I_FP_ACCESS', 'I_FP_ACCESS_1', 'I_TOGGLE_BUTTON', 'I_FP_CLOSE_GET_SKIN'):
            setattr(task, name, name)
        task.O_BATTLE_NUM = NS(ocr=lambda **kwargs: (0, 50, 50))
        task.get_all()
        self.assertEqual([p[:2] for p in metrics.progress], [(0, 50), (1, 50), (2, 50)])
        self.assertEqual(metrics.battles, ['won'])


if __name__ == '__main__':
    unittest.main()
