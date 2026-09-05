"""Daily sharing regressions using temporary state and mocked game interaction."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from filelock import FileLock, Timeout
from tasks.Component.GeneralInvite.config_invite import InviteConfig, FindMode

from test_mystery_shop_scroll import ROOT, ShopWorld, class_from_file, function_from_file


def share_state_class():
    path = ROOT / 'tasks/MysteryShop/share_state.py'
    atomic_path = ROOT / 'module/config/atomicwrites.py'
    namespace = {'__name__': '_share_test_atomicwrites', '__file__': str(atomic_path)}
    exec(compile(atomic_path.read_text(encoding='utf-8'), str(atomic_path), 'exec'), namespace)
    namespace.update({'__file__': str(path), 'date': date, 'sha256': sha256,
                      'Path': Path, 'json': json, 'FileLock': FileLock,
                      'Timeout': Timeout, 'logger': Mock()})
    for name in ('read_file', 'write_file'):
        function_from_file(ROOT / 'module/config/utils.py', name, namespace)
    return class_from_file(path, 'MysteryShopShareState', namespace), namespace


class _DailyShareCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='mystery-daily-share-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state_class, self.namespace = share_state_class()
        self.day = date(2026, 9, 5)

    def state(self, account='oas1'):
        return self.state_class(account, root=self.root)


class DailyShareStateTests(_DailyShareCase):
    def test_reservation_and_success_survive_restart(self):
        state = self.state()
        self.assertTrue(state.reserve('Alice', self.day))
        self.assertFalse(self.state().reserve('Alice', self.day))
        state.complete('Alice', self.day)
        self.assertFalse(self.state().reserve('Alice', self.day))

    def test_new_friend_new_day_and_other_account_are_independent(self):
        state = self.state()
        self.assertTrue(state.reserve('Alice', self.day))
        self.assertTrue(state.reserve('Bob', self.day))
        self.assertTrue(self.state('oas2').reserve('Alice', self.day))
        self.assertTrue(state.reserve('Alice', date(2026, 9, 6)))

    def test_normalized_names_share_one_record_without_plaintext_names(self):
        state = self.state()
        self.assertTrue(state.reserve('A　lice ', self.day))
        self.assertFalse(state.reserve('Alice', self.day))
        self.assertNotIn('Alice', state.path.read_text(encoding='utf-8'))

    def test_explicit_failure_can_retry_but_success_cannot_be_released(self):
        state = self.state()
        state.reserve('Alice', self.day)
        state.release('Alice', self.day)
        self.assertTrue(state.reserve('Alice', self.day))
        state.complete('Alice', self.day)
        state.release('Alice', self.day)
        self.assertFalse(state.reserve('Alice', self.day))

    def test_concurrent_reservations_have_one_winner(self):
        states = [self.state(), self.state()]
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda state: state.reserve('Alice', self.day), states))
        self.assertEqual(sorted(results), [False, True])

    def test_corrupt_state_is_not_silently_reset(self):
        state = self.state()
        state.path.write_text('{broken', encoding='utf-8')
        with self.assertRaises(ValueError):
            state.reserve('Alice', self.day)
        self.assertEqual(state.path.read_text(encoding='utf-8'), '{broken')

    def test_invalid_metadata_is_rejected(self):
        state = self.state()
        state.reserve('Alice', self.day)
        valid = json.loads(state.path.read_text(encoding='utf-8'))
        invalid = [dict(valid, version=True), dict(valid, config_name='oas2'),
                   dict(valid, day='bad'), dict(valid, friends={'wrong': 'shared'}),
                   dict(valid, friends={state._friend_key('Alice'): 'invalid'})]
        for data in invalid:
            with self.subTest(data=data):
                state.path.write_text(json.dumps(data), encoding='utf-8')
                with self.assertRaises(ValueError):
                    state.reserve('Alice', self.day)

    def test_failed_completion_write_preserves_pending_reservation(self):
        state = self.state()
        state.reserve('Alice', self.day)
        before = state.path.read_bytes()
        with patch.dict(self.namespace, {'write_file': Mock(side_effect=OSError('disk full'))}):
            with self.assertRaises(OSError):
                state.complete('Alice', self.day)
        self.assertEqual(state.path.read_bytes(), before)
        self.assertFalse(self.state().reserve('Alice', self.day))


class DailyShareFlowTests(_DailyShareCase):
    def world(self, friends='Alice', account='oas1'):
        world = ShopWorld([{}])
        world.task.config.config_name = account
        world.task.config.mystery_shop.invite_config = InviteConfig(
            friend_list=friends, find_mode=FindMode.RECENT_FRIEND
        )
        world.task._daily_share_state = self.state(account)
        world.task.ui_click = Mock()
        world.task.I_SELECTED = SimpleNamespace(match_all_any=Mock(return_value=[]))
        world.task.invite_friends = Mock(return_value=True)
        world.task._wait_shop_stable = Mock()
        return world

    def test_same_friend_is_shared_once_even_after_restart_and_scan_failure(self):
        world = self.world()
        world.task.share()
        world.task.share()
        world.task.invite_friends.assert_called_once()
        restarted = self.world()
        restarted.task.share()
        restarted.task.ui_click.assert_not_called()
        restarted.task.invite_friends.assert_not_called()

    def test_each_friend_is_recorded_before_the_next_one(self):
        world = self.world('Alice\nBob')
        outcomes = [True, OSError('connection lost')]
        world.task.invite_friends.side_effect = outcomes
        with self.assertRaises(OSError):
            world.task.share()
        self.assertEqual([call.args[0].friend_list_v for call in world.task.invite_friends.call_args_list],
                         [['Alice'], ['Bob']])
        restarted = self.world('Alice\nBob\nCharlie')
        restarted.task.share()
        restarted.task.invite_friends.assert_called_once()
        self.assertEqual(restarted.task.invite_friends.call_args.args[0].friend_list_v, ['Charlie'])

    def test_failure_after_share_does_not_cause_resend(self):
        world = self.world()
        world.task._wait_shop_stable.side_effect = world.namespace['GameStuckError']('unstable')
        with self.assertRaises(world.namespace['GameStuckError']):
            world.task.share()
        restarted = self.world()
        restarted.task.share()
        restarted.task.invite_friends.assert_not_called()

    def test_confirmed_not_selected_can_retry_later(self):
        world = self.world()
        world.task.invite_friends.side_effect = [False, True]
        world.task.share()
        world.task.share()
        world.task.share()
        self.assertEqual(world.task.invite_friends.call_count, 2)

    def test_duplicate_and_whitespace_names_do_not_resend(self):
        world = self.world('Alice\nAlice\nA　lice')
        world.task.share()
        world.task.invite_friends.assert_called_once()

    def test_config_is_unchanged_and_invitation_preferences_are_preserved(self):
        world = self.world('Alice\nBob')
        config = world.task.config.mystery_shop.invite_config
        before = vars(config).copy()
        world.task.share(config)
        self.assertEqual(vars(config), before)
        for call in world.task.invite_friends.call_args_list:
            self.assertEqual(call.args[0].find_mode, FindMode.RECENT_FRIEND)
            self.assertEqual(call.args[1:], (False, world.task.I_INVITE_ENSURE))

    def test_new_day_and_other_account_can_share_same_friend(self):
        world = self.world()
        world.task.share()
        other = self.world(account='oas2')
        other.task.share()
        other.task.invite_friends.assert_called_once()
        next_day = self.world()
        next_day.current_date = datetime(2026, 9, 9, 12)
        next_day.task.share()
        next_day.task.invite_friends.assert_called_once()

    def test_corruption_stops_before_opening_share_panel(self):
        world = self.world()
        world.task._daily_share_state.path.write_text('{broken', encoding='utf-8')
        with self.assertRaises(world.namespace['RequestHumanTakeover']):
            world.task.share()
        world.task.ui_click.assert_not_called()
        world.task.invite_friends.assert_not_called()

    def test_reservation_write_failure_stops_before_any_share(self):
        world = self.world()
        with patch.dict(self.namespace, {'write_file': Mock(side_effect=OSError('disk full'))}):
            with self.assertRaises(world.namespace['RequestHumanTakeover']):
                world.task.share()
        world.task.ui_click.assert_not_called()
        world.task.invite_friends.assert_not_called()

    def test_completion_write_failure_leaves_reservation_for_restart(self):
        world = self.world()
        world.task._daily_share_state.complete = Mock(side_effect=OSError('disk full'))
        with self.assertRaises(world.namespace['RequestHumanTakeover']):
            world.task.share()
        restarted = self.world()
        restarted.task.share()
        restarted.task.invite_friends.assert_not_called()

    def test_empty_friend_list_never_creates_state_or_opens_panel(self):
        world = self.world('')
        world.task.share()
        world.task.ui_click.assert_not_called()
        world.task.invite_friends.assert_not_called()
        self.assertFalse(world.task._daily_share_state.path.exists())

    def test_preselected_friends_are_not_accidentally_reshared(self):
        world = self.world('Bob')
        world.task.I_SELECTED.match_all_any.return_value = [object()]
        with self.assertRaises(world.namespace['RequestHumanTakeover']):
            world.task.share()
        world.task.invite_friends.assert_not_called()


if __name__ == '__main__':
    unittest.main()
