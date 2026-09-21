"""Offline login regressions: local screen guards precede Chess recovery."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from module.exception import GameStuckError, RequestHumanTakeover
from tasks.Component.Login import recovery
from tasks.Component.Login.service import LoginService
from tasks.GameUi.chess_battle import ChessBattleNavigationMixin


class LoginChessRecoveryTests(unittest.TestCase):
    def task(self):
        task = object.__new__(LoginService)
        task.device = SimpleNamespace(
            image=SimpleNamespace(shape=(720, 1280, 3)),
            screenshot=Mock(), stuck_record_add=Mock(),
            stuck_record_clear=Mock(), click_record_clear=Mock(),
            get_orientation=Mock(), app_stop=Mock(), app_start=Mock(),
        )
        task._burst = Mock()
        task.screenshot = Mock(side_effect=AssertionError('unguarded login screenshot'))
        task.appear = Mock(return_value=False)
        task.appear_then_click = Mock(return_value=False)
        task.click = Mock(return_value=True)
        task.chess_result_flow_visible = Mock(return_value=False)
        task.return_to_chess_lobby = Mock(return_value=True)
        return task

    def test_portrait_screenshot_skips_invites_and_all_recognition(self):
        task = self.task()
        task.device.image.shape = (1280, 720, 3)
        self.assertFalse(task._login_screenshot())
        task.device.screenshot.assert_called_once_with()
        task._burst.assert_not_called()
        task.appear.assert_not_called()

    def test_landscape_screenshot_handles_invites_and_rechecks_the_new_frame(self):
        task = self.task()
        self.assertTrue(task._login_screenshot())
        task._burst.assert_called_once_with()

        def invitation_changes_orientation():
            task.device.image.shape = (1280, 720, 3)

        task._burst.side_effect = invitation_changes_orientation
        self.assertFalse(task._login_screenshot())
        self.assertEqual(task.device.screenshot.call_count, 2)

    def test_invalid_frame_cannot_trigger_chess_or_local_recovery(self):
        task = self.task()
        task._login_screenshot = Mock(side_effect=[False, StopIteration])
        with patch('tasks.Component.Login.service.LoginRecovery') as factory:
            with self.assertRaises(StopIteration):
                task._app_handle_login()
        factory.return_value.handle.assert_not_called()
        task.appear.assert_not_called()
        task.appear_then_click.assert_not_called()
        task.chess_result_flow_visible.assert_not_called()

    def test_download_takes_priority_over_chess_detection_and_clicks(self):
        task = self.task()
        task._login_screenshot = Mock(side_effect=[True, StopIteration])
        task.appear.side_effect = lambda marker, **_: marker in (
            recovery.DOWNLOAD, task.I_CHECK_CHESS,
        )
        with patch.object(recovery.DOWNLOAD_PROGRESS, 'ocr', return_value=None):
            with self.assertRaises(StopIteration):
                task._app_handle_login()
        task.appear_then_click.assert_not_called()
        task.click.assert_not_called()
        task.return_to_chess_lobby.assert_not_called()

    def test_faq_takes_priority_and_uses_the_local_recovery_click(self):
        task = self.task()
        task._login_screenshot = Mock(side_effect=[True, StopIteration])
        task.appear.side_effect = lambda marker, **_: marker in (
            recovery.FAQ_TITLE, recovery.WEBVIEW_REFRESH,
            recovery.WEBVIEW_EXIT, task.I_CHECK_CHESS,
        )
        with self.assertRaises(StopIteration):
            task._app_handle_login()
        task.click.assert_called_once_with(recovery.EXIT_FAQ, interval=2)
        task.appear_then_click.assert_not_called()
        task.return_to_chess_lobby.assert_not_called()

    def test_cancel_return_dialog_waits_for_a_fresh_lobby_frame(self):
        task = self.task()
        task._login_screenshot = Mock(side_effect=[True, True])
        task.appear_then_click.side_effect = [True, False]
        task.appear.side_effect = lambda marker, **_: marker is task.I_CHECK_CHESS
        with patch('tasks.Component.Login.service.LoginRecovery') as factory:
            factory.return_value.handle.return_value = False
            self.assertTrue(task._app_handle_login())
        self.assertEqual(task._login_screenshot.call_count, 2)
        self.assertEqual(task.appear_then_click.call_count, 2)
        task.appear_then_click.assert_called_with(task.I_RETURN_CHESS_CANCEL, interval=0.8)
        task.return_to_chess_lobby.assert_not_called()
        task.screenshot.assert_not_called()

    def test_lobby_completes_login_without_clicking_courtyard_or_results(self):
        task = self.task()
        task._login_screenshot = Mock(return_value=True)
        task.appear.side_effect = lambda marker, **_: marker is task.I_CHECK_CHESS
        with patch('tasks.Component.Login.service.LoginRecovery') as factory:
            factory.return_value.handle.return_value = False
            self.assertTrue(task._app_handle_login())
        task.chess_result_flow_visible.assert_not_called()
        task.return_to_chess_lobby.assert_not_called()
        task.click.assert_not_called()

    def test_chess_results_are_finished_before_reporting_login_success(self):
        task = self.task()
        task._login_screenshot = Mock(return_value=True)
        task.chess_result_flow_visible.return_value = True
        with patch('tasks.Component.Login.service.LoginRecovery') as factory:
            factory.return_value.handle.return_value = False
            self.assertTrue(task._app_handle_login())
        task.return_to_chess_lobby.assert_called_once_with()
        task.appear_then_click.assert_called_once_with(task.I_RETURN_CHESS_CANCEL, interval=0.8)
        task.screenshot.assert_not_called()

    def test_failed_chess_recovery_uses_existing_login_restart_and_retry(self):
        task = self.task()
        task._login_screenshot = Mock(return_value=True)
        task.chess_result_flow_visible.return_value = True
        task.return_to_chess_lobby.side_effect = [GameStuckError('Chess result timeout'), True]
        task._save_login_error_screenshot = Mock()
        with patch('tasks.Component.Login.service.LoginRecovery') as factory:
            factory.return_value.handle.return_value = False
            self.assertTrue(task.app_handle_login())
        self.assertEqual(task.return_to_chess_lobby.call_count, 2)
        task._save_login_error_screenshot.assert_called_once_with(1)
        task.device.app_stop.assert_called_once_with()
        task.device.app_start.assert_called_once_with()

    def test_repeated_chess_failure_retains_bounded_login_retries(self):
        task = self.task()
        task._login_screenshot = Mock(return_value=True)
        task.chess_result_flow_visible.return_value = True
        task.return_to_chess_lobby.side_effect = GameStuckError('Chess result timeout')
        task._save_login_error_screenshot = Mock()
        with patch('tasks.Component.Login.service.LoginRecovery') as factory:
            factory.return_value.handle.return_value = False
            with self.assertRaises(RequestHumanTakeover):
                task.app_handle_login()
        self.assertEqual(task.return_to_chess_lobby.call_count, 4)
        self.assertEqual(task.device.app_stop.call_count, 3)
        self.assertEqual(task.device.app_start.call_count, 3)
        self.assertEqual(task._save_login_error_screenshot.call_count, 4)

    def result_flow_task(self, frames):
        task = self.task()
        clock = SimpleNamespace(now=0.0, frame=-1)

        def screenshot():
            clock.frame += 1
            if clock.frame >= len(frames):
                raise AssertionError('Chess recovery did not finish on the lobby frame')

        def sleep(seconds):
            clock.now += seconds

        task.screenshot = Mock(side_effect=screenshot)
        task.appear.side_effect = lambda marker, **_: marker in frames[clock.frame]
        fake_time = SimpleNamespace(monotonic=lambda: clock.now, sleep=sleep)
        return task, fake_time

    def test_result_recovery_already_at_lobby_does_not_click_return_area(self):
        task, clock = self.result_flow_task([{LoginService.I_CHECK_CHESS}])
        with patch('tasks.GameUi.chess_battle.time', clock):
            self.assertTrue(ChessBattleNavigationMixin.return_to_chess_lobby(task))
        task.appear_then_click.assert_not_called()
        task.click.assert_not_called()
        task.screenshot.assert_called_once_with()

    def test_result_exit_buttons_retry_when_local_click_throttle_declines(self):
        for exit_button in (LoginService.I_CHESS_EXIT_TO_LOBBY,
                            LoginService.I_CHESS_EXIT_TO_LOBBY_2):
            with self.subTest(exit_button=exit_button.name):
                task, clock = self.result_flow_task([
                    {exit_button}, {exit_button},
                    {LoginService.I_CHESS_SHARE}, {LoginService.I_CHECK_CHESS},
                ])
                task.appear_then_click.side_effect = [False, True]
                with patch('tasks.GameUi.chess_battle.time', clock):
                    self.assertTrue(ChessBattleNavigationMixin.return_to_chess_lobby(task))
                self.assertEqual(task.appear_then_click.call_count, 2)
                task.appear_then_click.assert_called_with(exit_button, interval=1.5)

    def test_result_recovery_accepts_direct_lobby_transition_without_share(self):
        task, clock = self.result_flow_task([
            {LoginService.I_CHESS_EXIT_TO_LOBBY}, {LoginService.I_CHECK_CHESS},
        ])
        task.appear_then_click.return_value = True
        with patch('tasks.GameUi.chess_battle.time', clock):
            self.assertTrue(ChessBattleNavigationMixin.return_to_chess_lobby(task))
        task.appear_then_click.assert_called_once_with(task.I_CHESS_EXIT_TO_LOBBY, interval=1.5)
        task.click.assert_not_called()


if __name__ == '__main__':
    unittest.main()
