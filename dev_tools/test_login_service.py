"""Offline login regressions for screen guards, recovery priority and retries."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from module.exception import GameStuckError, RequestHumanTakeover
from tasks.Component.Login import recovery
from tasks.Component.Login.service import LoginService


class LoginServiceTests(unittest.TestCase):
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

    def test_invalid_frame_cannot_trigger_recognition_or_local_recovery(self):
        task = self.task()
        task._login_screenshot = Mock(side_effect=[False, StopIteration])
        with patch('tasks.Component.Login.service.LoginRecovery') as factory:
            with self.assertRaises(StopIteration):
                task._app_handle_login()
        factory.return_value.handle.assert_not_called()
        task.appear.assert_not_called()
        task.appear_then_click.assert_not_called()

    def test_download_takes_priority_over_login_detection_and_clicks(self):
        task = self.task()
        task._login_screenshot = Mock(side_effect=[True, StopIteration])
        task.appear.side_effect = lambda marker, **_: marker in (
            recovery.DOWNLOAD, task.I_CHECK_MAIN,
        )
        with patch.object(recovery.DOWNLOAD_PROGRESS, 'ocr', return_value=None):
            with self.assertRaises(StopIteration):
                task._app_handle_login()
        task.appear_then_click.assert_not_called()
        task.click.assert_not_called()

    def test_faq_takes_priority_and_uses_the_local_recovery_click(self):
        task = self.task()
        task._login_screenshot = Mock(side_effect=[True, StopIteration])
        task.appear.side_effect = lambda marker, **_: marker in (
            recovery.FAQ_TITLE, recovery.WEBVIEW_REFRESH,
            recovery.WEBVIEW_EXIT, task.I_CHECK_MAIN,
        )
        with self.assertRaises(StopIteration):
            task._app_handle_login()
        task.click.assert_called_once_with(recovery.EXIT_FAQ, interval=2)
        task.appear_then_click.assert_not_called()

    def test_login_failure_keeps_existing_restart_and_retry(self):
        task = self.task()
        task._app_handle_login = Mock(side_effect=[GameStuckError('Login timeout'), True])
        task._save_login_error_screenshot = Mock()
        self.assertTrue(task.app_handle_login())
        self.assertEqual(task._app_handle_login.call_count, 2)
        task._save_login_error_screenshot.assert_called_once_with(1)
        task.device.app_stop.assert_called_once_with()
        task.device.app_start.assert_called_once_with()

    def test_repeated_failure_retains_bounded_login_retries(self):
        task = self.task()
        task._app_handle_login = Mock(side_effect=GameStuckError('Login timeout'))
        task._save_login_error_screenshot = Mock()
        with self.assertRaises(RequestHumanTakeover):
            task.app_handle_login()
        self.assertEqual(task._app_handle_login.call_count, 4)
        self.assertEqual(task.device.app_stop.call_count, 3)
        self.assertEqual(task.device.app_start.call_count, 3)
        self.assertEqual(task._save_login_error_screenshot.call_count, 4)


if __name__ == '__main__':
    unittest.main()
