"""Offline regressions for random retreat milestones; no game or RPC is used."""
import ast
from copy import copy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
TaskEnd = type("TaskEnd", (Exception,), {})
TransitionTimeout = type("TransitionTimeout", (Exception,), {})
FAIL = SimpleNamespace(CONTINUE="continue", REFRESH="refresh", EXIT="exit")


def task_class(namespace):
    tree = ast.parse((ROOT / "tasks/RealmRaid/script_task.py").read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ScriptTask")
    names = {"_reset_raid_progress", "_clear_raid_board", "_find_unattacked_targets",
             "_select_exit_four_target", "_attack_target", "run", "find_one", "check_refresh"}
    methods = [node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in methods} == names
    subject = ast.ClassDef(name="Subject", bases=[], keywords=[], body=methods, decorator_list=[])
    module = ast.Module(body=[ast.ImportFrom(module="__future__",
        names=[ast.alias(name="annotations")], level=0), subject], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), "realm_raid_under_test", "exec"), namespace)
    return namespace["Subject"]


class RaidWorld:
    def __init__(self, draws=(0.1,), limit=30, result=True):
        self.random = SimpleNamespace(random=Mock(side_effect=draws),
                                     choice=Mock(side_effect=lambda pool: pool[-1]))
        namespace = dict(random=self.random, copy=copy, logger=Mock(), TaskEnd=TaskEnd,
                         BattleTransitionTimeout=TransitionTimeout, WhenAttackFail=FAIL,
                         page_realm_raid="raid", page_exploration="explore",
                         page_shikigami_records="records")
        self.task = task_class(namespace)()
        task = self.task
        self.config = SimpleNamespace(lock_team_enable=True, quick_exit=False)
        raid = SimpleNamespace(raid_config=SimpleNamespace(
            exit_four=True, number_base=0, three_refresh=False, when_attack_fail=FAIL.CONTINUE),
            general_battle_config=self.config, switch_soul_config=SimpleNamespace(
                enable=False, enable_switch_by_name=False))
        task.config = SimpleNamespace(realm_raid=raid)
        task._reset_raid_progress()
        task._find_unattacked_targets = Mock(return_value=[("random_medal", 7)])
        task.find_one = Mock(return_value=("normal_medal", 1))
        task.goto_page = Mock()
        task.set_next_run = Mock()
        task.screenshot = Mock()
        task.appear = Mock(return_value=False)
        task.ensure_lock = Mock()
        task.is_frog = Mock(return_value=False)
        task.check_medal_is_frog = Mock(return_value=False)
        task.check_ticket = Mock(side_effect=lambda base: task._raid_attack_count < limit)
        task.reward_detect_click = Mock(return_value=False)
        task.fire = Mock(return_value=True)
        task.fire_again = Mock(return_value=True)
        task.build_quick_exit_config = Mock(side_effect=lambda config: SimpleNamespace(
            **dict(vars(config), quick_exit=True)))
        task.run_general_battle = Mock(side_effect=lambda config: False if config.quick_exit else result)
        task.I_FROG_RAID, task.I_RR_THREE = "frog_popup", "three_wins"

    def run(self):
        with unittest.TestCase().assertRaises(TaskEnd):
            self.task.run()


class MilestoneTests(unittest.TestCase):
    def test_only_requested_counts_are_eligible(self):
        for count in range(40):
            world = RaidWorld()
            world.task._raid_attack_count = count
            selected = world.task._select_exit_four_target()
            self.assertEqual(selected, ("random_medal", 7) if count in (0, 9, 18, 27) else (None, None))

    def test_skip_is_not_rerolled_and_refresh_preserves_progress(self):
        world = RaidWorld(draws=(0.8,))
        task = world.task
        task._raid_attack_count = 9
        self.assertEqual(task._select_exit_four_target(), (None, None))
        task._raid_attempted = {1, 2}
        task._clear_raid_board()
        self.assertEqual(task._raid_attack_count, 9)
        self.assertEqual(task._select_exit_four_target(), (None, None))
        world.random.random.assert_called_once()
        task._find_unattacked_targets.assert_not_called()

    def test_empty_pool_skips_without_retrying_node(self):
        world = RaidWorld()
        world.task._find_unattacked_targets.return_value = []
        for _ in range(3):
            self.assertEqual(world.task._select_exit_four_target(), (None, None))
        world.random.random.assert_called_once()
        world.random.choice.assert_not_called()

    def test_disabled_means_no_draw_or_candidate_scan(self):
        world = RaidWorld()
        world.task.config.realm_raid.raid_config.exit_four = False
        self.assertEqual(world.task._select_exit_four_target(), (None, None))
        world.random.random.assert_not_called()
        world.task._find_unattacked_targets.assert_not_called()

    def test_probability_boundary(self):
        world = RaidWorld(draws=(0.5,))
        self.assertEqual(world.task._select_exit_four_target(), (None, None))

    def test_new_run_resets_progress(self):
        world = RaidWorld(draws=(0.1, 0.1))
        task = world.task
        task._select_exit_four_target()
        task._raid_attack_count = 27
        task.init_tickets = 14
        task._raid_attempted.add(2)
        task._reset_raid_progress()
        self.assertEqual(task._raid_attack_count, 0)
        self.assertEqual(task.init_tickets, -1)
        self.assertEqual(task._raid_attempted, set())
        self.assertEqual(task._select_exit_four_target(), ("random_medal", 7))


class BattleSequenceTests(unittest.TestCase):
    def test_four_exits_then_one_normal_battle_on_same_target(self):
        world = RaidWorld()
        self.assertTrue(world.task._attack_target(7, world.config, exit_four=True))
        world.task.fire.assert_called_once_with(7)
        self.assertEqual(world.task.fire_again.call_count, 4)
        configs = [call.args[0] if call.args else call.kwargs["config"]
                   for call in world.task.run_general_battle.call_args_list]
        self.assertEqual([config.quick_exit for config in configs], [True] * 4 + [False])
        self.assertFalse(world.config.quick_exit)
        self.assertEqual(world.task._raid_attack_count, 1)
        self.assertEqual(world.task._raid_attempted, {7})

    def test_normal_loss_counts_once(self):
        world = RaidWorld(result=False)
        self.assertFalse(world.task._attack_target(3, world.config))
        self.assertEqual(world.task._raid_attack_count, 1)
        world.task.fire_again.assert_not_called()

    def test_failed_entry_does_not_count_or_retreat(self):
        world = RaidWorld()
        world.task.fire.return_value = False
        self.assertIsNone(world.task._attack_target(7, world.config, exit_four=True))
        self.assertEqual(world.task._raid_attack_count, 0)
        world.task.run_general_battle.assert_not_called()

    def test_retry_timeout_stops_chain_without_counting_normal_attack(self):
        world = RaidWorld()
        world.task.fire_again.side_effect = TransitionTimeout
        with self.assertRaises(TransitionTimeout):
            world.task._attack_target(7, world.config, exit_four=True)
        self.assertEqual(world.task.run_general_battle.call_count, 1)
        self.assertEqual(world.task._raid_attack_count, 0)

    def test_full_run_uses_draws_at_zero_nine_eighteen_twenty_seven(self):
        world = RaidWorld(draws=(0.1, 0.9, 0.1, 0.9))
        world.run()
        self.assertEqual(world.random.random.call_count, 4)
        self.assertEqual(world.task._exit_four_checked, {0, 9, 18, 27})
        self.assertEqual(world.task._raid_attack_count, 30)
        self.assertEqual(world.task.run_general_battle.call_count, 38)
        self.assertEqual(world.task.fire_again.call_count, 8)
        targets = [call.args[0] for call in world.task.fire.call_args_list]
        self.assertEqual([i for i, target in enumerate(targets) if target == 7], [0, 18])
        self.assertEqual(world.task.find_one.call_count, 28)

    def test_no_trigger_on_every_left_top_normal_target(self):
        world = RaidWorld(draws=(0.9, 0.9, 0.9, 0.9))
        world.run()
        self.assertEqual(world.task.run_general_battle.call_count, 30)
        world.task.fire_again.assert_not_called()

    def test_short_run_does_not_force_later_nodes(self):
        world = RaidWorld(draws=(0.9,), limit=3)
        world.run()
        self.assertEqual(world.task._exit_four_checked, {0})
        self.assertEqual(world.task._raid_attack_count, 3)

    def test_team_lock_is_restored_when_random_frog_target_times_out(self):
        world = RaidWorld()
        world.task.check_medal_is_frog.return_value = True
        world.task.fire_again.side_effect = TransitionTimeout
        with self.assertRaises(TransitionTimeout):
            world.task.run()
        self.assertTrue(world.config.lock_team_enable)


class CandidateTests(unittest.TestCase):
    def setup_task(self, matches, failed=()):
        world = RaidWorld()
        task = world.task
        del task._find_unattacked_targets
        task.partition = [SimpleNamespace(roi_front=(i * 100, 0, 100, 100),
                                         roi_back=(i * 100, 0, 100, 100)) for i in range(9)]
        task.device = SimpleNamespace(image=np.ones((100, 900, 3), dtype=np.uint8), image_frame_id="frame")
        task.false_roi = [(i, 1, 20, 20) for i in range(1, 10)]
        task.false_image = SimpleNamespace(roi_back=(900, 900, 20, 20))
        task.appear = Mock(side_effect=lambda marker: marker.roi_back[0] in failed)
        task.exit_four_medals = SimpleNamespace(find_everyone=Mock(return_value=matches))
        return task

    def match(self, index, score=0.9, name=None):
        return (name or "medal_" + str(index), score, ((index - 1) * 100 + 20, 20, 30, 30))

    def test_excludes_failed_attempted_and_absent_cells_and_deduplicates(self):
        task = self.setup_task([self.match(1), self.match(2), self.match(2, .95, "best"),
                                self.match(4), self.match(9)], failed={2})
        task._raid_attempted.add(4)
        self.assertEqual(task._find_unattacked_targets(), [("medal_1", 1), ("medal_9", 9)])
        self.assertEqual(task.false_image.roi_back, (900, 900, 20, 20))
        self.assertTrue(np.all(task.device.image == 1))

    def test_duplicate_matches_do_not_weight_an_opponent(self):
        task = self.setup_task([self.match(3, .8), self.match(3, .95, "best"), self.match(8)])
        self.assertEqual(task._find_unattacked_targets(), [("best", 3), ("medal_8", 8)])

    def test_all_failed_means_empty_pool_even_in_refresh_mode(self):
        task = self.setup_task([self.match(2), self.match(6)], failed={2, 6})
        task.config.realm_raid.raid_config.when_attack_fail = FAIL.REFRESH
        self.assertEqual(task._find_unattacked_targets(), [])

    def test_full_fresh_board_clears_old_board_history_only(self):
        task = self.setup_task([self.match(i) for i in range(1, 10)])
        task._raid_attempted = {1, 2}
        task._raid_won_indices = {1}
        task._raid_attack_count = 9
        task._exit_four_checked = {0}
        self.assertEqual(len(task._find_unattacked_targets()), 9)
        self.assertEqual(task._raid_attack_count, 9)
        self.assertEqual(task._exit_four_checked, {0})

    def test_partial_new_board_does_not_keep_old_attempted_cells(self):
        task = self.setup_task([self.match(4), self.match(7), self.match(8)], failed={8})
        task._raid_attempted = {4, 8}
        task._raid_won_indices = {4}
        task._raid_attack_count = 9
        self.assertEqual(task._find_unattacked_targets(), [("medal_4", 4), ("medal_7", 7)])
        self.assertEqual(task._raid_attack_count, 9)

    def test_successful_manual_refresh_keeps_milestone_decisions(self):
        task = self.setup_task([])
        task._raid_attack_count = 9
        task._exit_four_checked = {0, 9}
        task._raid_attempted = {4}
        task.I_FRESH, task.I_FRESH_ENSURE = "refresh", "confirm"
        task.appear = Mock(side_effect=[True, True, False])
        self.assertTrue(task.check_refresh())
        self.assertEqual(task._raid_attempted, set())
        self.assertEqual(task._raid_attack_count, 9)
        self.assertEqual(task._exit_four_checked, {0, 9})

    def test_normal_failed_target_filter_does_not_modify_screenshot(self):
        task = self.setup_task([], failed={2})
        task.order_medal = SimpleNamespace(find_anyone=Mock(return_value=None))
        del task.find_one
        self.assertEqual(task.find_one(False), (None, None))
        filtered = task.order_medal.find_anyone.call_args.args[0]
        self.assertTrue(np.all(task.device.image == 1))
        self.assertTrue(np.all(filtered[:, 100:200] == 0))


if __name__ == "__main__":
    unittest.main()
