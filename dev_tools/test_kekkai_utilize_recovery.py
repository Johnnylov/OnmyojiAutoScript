"""Offline foster recovery and selection checks; no game or notifications are used."""

from datetime import datetime, timedelta
from enum import Enum
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from test_image_template_guard import methods


class Friend(Enum):
    SAME_SERVER = 'same'
    DIFFERENT_SERVER = 'different'


class Rule(Enum):
    DEFAULT = 'default'
    TAIKO = 'taiko'
    FISH = 'fish'


class PageError(Exception):
    pass


class StuckError(Exception):
    pass


class FosterRecoveryTests(unittest.TestCase):
    def setUp(self):
        namespace = dict(logger=Mock(), datetime=datetime, timedelta=timedelta,
                         SelectFriendList=Friend, UtilizeRule=Rule,
                         ShikigamiClass=SimpleNamespace(N='N'),
                         GamePageUnknownError=PageError, GameStuckError=StuckError,
                         page_friend_utilize='friend-realm')
        cls = methods('tasks/KekkaiUtilize/script_task.py', 'ScriptTask',
                      ['run_utilize', '_record_utilize_failure', '_finish_low_value_utilize',
                       '_select_optimal_resource_card'], namespace)
        self.task = task = cls()
        task.config = SimpleNamespace(kekkai_utilize=SimpleNamespace(
            utilize_config=SimpleNamespace(utilize_rule=Rule.DEFAULT)))
        task.utilize_failed_count = 0
        task.utilize_entered_failed_count = 0
        task.utilize_terminal_failure = False
        task.utilize_lazy_mode_active = False
        task.utilize_add_count = 0
        task.screenshot = Mock()
        task.save_image = Mock()
        task.push_notify = Mock()
        task.set_next_run = Mock()
        task.goto_page = Mock()
        task._reset_utilize_friend_list = Mock()
        task._select_optimal_resource_card = Mock(return_value=True)
        task._select_lazy_resource_card = Mock(return_value=True)
        task.switch_shikigami_class = Mock()
        task.set_shikigami = Mock(return_value=True)
        for name in ('I_U_ENTER_REALM', 'I_U_ADD_1', 'I_U_ADD_2'):
            setattr(task, name, name)
        task.appear = Mock(side_effect=lambda marker: marker in ('I_U_ENTER_REALM', 'I_U_ADD_1'))

    def test_failed_placement_is_not_success_and_counts_toward_exit(self):
        self.task.set_shikigami.return_value = False
        self.assertFalse(self.task.run_utilize())
        self.assertEqual(self.task.utilize_entered_failed_count, 1)
        self.assertFalse(self.task.utilize_terminal_failure)
        before = datetime.now()
        self.assertFalse(self.task.run_utilize())
        self.assertTrue(self.task.utilize_terminal_failure)
        target = self.task.set_next_run.call_args.kwargs['target']
        self.assertGreaterEqual(target, before + timedelta(minutes=10))
        self.assertEqual(self.task.set_shikigami.call_count, 2)

    def test_occupied_slot_never_attempts_placement(self):
        self.task.appear.side_effect = lambda marker: marker == 'I_U_ENTER_REALM'
        self.assertFalse(self.task.run_utilize())
        self.task.set_shikigami.assert_not_called()
        self.assertEqual(self.task.utilize_entered_failed_count, 1)

    def test_placement_exception_uses_same_bounded_recovery(self):
        self.task.set_shikigami.side_effect = StuckError('timed out')
        self.assertFalse(self.task.run_utilize())
        self.assertEqual(self.task.utilize_entered_failed_count, 1)

    def test_success_resets_failure_counters(self):
        self.task.utilize_failed_count = 2
        self.task.utilize_entered_failed_count = 1
        self.assertTrue(self.task.run_utilize())
        self.assertEqual(self.task.utilize_failed_count, 0)
        self.assertEqual(self.task.utilize_entered_failed_count, 0)
        self.task.set_next_run.assert_not_called()

    def test_empty_preferred_group_checks_fallback(self):
        self.task._select_optimal_resource_card.side_effect = [None, True]
        self.assertTrue(self.task.run_utilize())
        self.assertEqual([call.args[0] for call in self.task._select_optimal_resource_card.call_args_list],
                         [Friend.SAME_SERVER, Friend.DIFFERENT_SERVER])

    def test_failed_selection_does_not_claim_group_empty(self):
        self.task._select_optimal_resource_card.return_value = False
        self.assertFalse(self.task.run_utilize())
        self.assertEqual(self.task._select_optimal_resource_card.call_count, 1)
        self.task.set_shikigami.assert_not_called()

    def test_lazy_mode_only_scans_preferred_group(self):
        self.task.utilize_lazy_mode_active = True
        self.task._select_lazy_resource_card.return_value = None
        self.assertFalse(self.task.run_utilize())
        self.task._select_lazy_resource_card.assert_called_once_with(Friend.SAME_SERVER)
        self.task._select_optimal_resource_card.assert_not_called()
        self.assertTrue(self.task.utilize_terminal_failure)

    def test_recorded_best_is_reselected_before_claiming_success(self):
        task = self.task
        del task._select_optimal_resource_card
        def scan():
            task.ap_max_num, task.jade_max_num = 134, 76
            task.utilize_current_group_has_eligible_card = True
            task.utilize_current_group_scan_completed = True
            return False
        task._current_select_best = Mock(side_effect=scan)
        task._locate_recorded_resource_card = Mock(return_value=True)
        self.assertTrue(task._select_optimal_resource_card(Friend.SAME_SERVER))
        task._reset_utilize_friend_list.assert_called_once_with(Friend.SAME_SERVER)
        task._locate_recorded_resource_card.assert_called_once_with('太鼓', 76)
        self.assertEqual((task.ap_max_num, task.jade_max_num), (0, 0))

    def test_failed_reselection_is_not_success(self):
        task = self.task
        del task._select_optimal_resource_card
        def scan():
            task.ap_max_num, task.jade_max_num = 151, 67
            task.utilize_current_group_has_eligible_card = True
            task.utilize_current_group_scan_completed = True
            return False
        task._current_select_best = Mock(side_effect=scan)
        task._locate_recorded_resource_card = Mock(return_value=False)
        self.assertFalse(task._select_optimal_resource_card(Friend.SAME_SERVER))
        task._locate_recorded_resource_card.assert_called_once_with('斗鱼', 151)
        self.assertEqual((task.ap_max_num, task.jade_max_num), (0, 0))


if __name__ == '__main__':
    unittest.main()
