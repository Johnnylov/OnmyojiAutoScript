"""Offline regressions for resource downloads and the login FAQ webview."""

import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

from tasks.Component.Login import recovery
from tasks.Component.Login.service import LoginService


ROOT = Path(__file__).resolve().parents[1]


def task_with(*markers):
    return SimpleNamespace(
        device=SimpleNamespace(image=np.zeros((720, 1280, 3), dtype=np.uint8),
                               stuck_record_clear=Mock(), stuck_record_add=Mock()),
        appear=lambda marker: marker in markers, click=Mock())


class ProgressTests(unittest.TestCase):
    def test_counter_parser_requires_download_label_units_and_valid_amounts(self):
        parse = recovery.parse_download_progress
        self.assertEqual(parse('正在下载: 374.00M / 477.23M'),
                         (374 * 1024 ** 2, 477.23 * 1024 ** 2))
        self.assertEqual(parse('正在下载：512KB/1MB'), (512 * 1024, 1024 ** 2))
        for value in (None, 5, '', '374/477', '正在连接服务器', '374M/477M',
                      '正在下载:abc/477M', '正在下载:478M/477M', '正在下载:0M/0M'):
            with self.subTest(value=value):
                self.assertIsNone(parse(value))

    def run_progress(self, values):
        task = task_with(recovery.DOWNLOAD)
        handler = recovery.LoginRecovery()
        handler.progress_timer = Mock(reached=Mock(return_value=True))
        with patch.object(recovery.DOWNLOAD_PROGRESS, 'ocr', side_effect=values):
            for _ in values:
                self.assertTrue(handler.handle(task))
        task.click.assert_not_called()
        return task

    def test_active_download_renews_stuck_grace_but_never_clicks(self):
        task = self.run_progress(['正在下载:374M/477M', '正在下载:375M/477M',
                                  '正在下载:376M/477M'])
        self.assertEqual(task.device.stuck_record_clear.call_count, 3)
        self.assertEqual(task.device.stuck_record_add.call_count, 3)
        task.device.stuck_record_add.assert_called_with('LOGIN_CHECK')

    def test_stalled_unreadable_and_regressing_counters_keep_timeout_running(self):
        task = self.run_progress(['正在下载:374M/477M', None, '正在下载:374M/477M',
                                  '正在下载:373M/477M', '正在下载:374M/477M'])
        task.device.stuck_record_clear.assert_called_once_with()
        task = self.run_progress([None, '', 'garbled'])
        task.device.stuck_record_clear.assert_not_called()

    def test_changed_total_and_ocr_oscillation_cannot_reset_high_water(self):
        task = self.run_progress(['正在下载:374M/477M', '正在下载:10M/100M',
                                  '正在下载:373M/477M', '正在下载:374M/477M',
                                  '正在下载:10M/100M', '正在下载:11M/100M'])
        self.assertEqual(task.device.stuck_record_clear.call_count, 2)

    def test_download_ocr_is_throttled_and_requires_visual_anchor(self):
        handler = recovery.LoginRecovery()
        handler.progress_timer = Mock(reached=Mock(return_value=False))
        with patch.object(recovery.DOWNLOAD_PROGRESS, 'ocr') as ocr:
            self.assertTrue(handler.handle(task_with(recovery.DOWNLOAD)))
            self.assertFalse(handler.handle(task_with()))
            ocr.assert_not_called()


class FaqTests(unittest.TestCase):
    def test_faq_closes_using_its_exit_when_all_three_anchors_match(self):
        task = task_with(recovery.FAQ_TITLE, recovery.WEBVIEW_REFRESH, recovery.WEBVIEW_EXIT)
        self.assertTrue(recovery.LoginRecovery().handle(task))
        task.click.assert_called_once_with(recovery.EXIT_FAQ, interval=2)
        task.device.stuck_record_clear.assert_not_called()

    def test_partial_toolbar_other_webpages_and_unknown_screens_are_not_clicked(self):
        for markers in ((), (recovery.FAQ_TITLE,), (recovery.WEBVIEW_EXIT,),
                        (recovery.WEBVIEW_REFRESH, recovery.WEBVIEW_EXIT),
                        (recovery.FAQ_TITLE, recovery.WEBVIEW_EXIT)):
            task = task_with(*markers)
            self.assertFalse(recovery.LoginRecovery().handle(task))
            task.click.assert_not_called()

    def test_login_loop_handles_download_before_animation_or_login_clicks(self):
        task = object.__new__(LoginService)
        task.device = SimpleNamespace(stuck_record_add=Mock(), get_orientation=Mock())
        # Stop after the recovery frame: no game/config/device is instantiated.
        task._login_screenshot = Mock(side_effect=[True, StopIteration])
        task.appear_then_click = Mock(side_effect=AssertionError('blind login click'))
        handler = Mock(handle=Mock(return_value=True))
        with patch('tasks.Component.Login.service.LoginRecovery', return_value=handler):
            with self.assertRaises(StopIteration):
                task._app_handle_login()
        handler.handle.assert_called_once_with(task)
        task.appear_then_click.assert_not_called()

    @unittest.skipUnless(os.environ.get('LOGIN_REPLAY_DIR'), 'Local screenshots not supplied')
    def test_real_error_frames_match_only_their_recovery_controls(self):
        folder = Path(os.environ['LOGIN_REPLAY_DIR'])
        download = cv2.imread(str(folder / 'oas1_login_1_1788918553317.png'))
        faq = cv2.imread(str(folder / 'oas2_login_1_1788746148647.png'))

        def matches(image, marker):
            template = cv2.imread(str(ROOT / marker.file))
            x, y, w, h = marker.roi_back
            score = cv2.minMaxLoc(cv2.matchTemplate(image[y:y+h, x:x+w], template,
                                                  cv2.TM_CCOEFF_NORMED))[1]
            return score >= marker.threshold

        self.assertTrue(matches(download, recovery.DOWNLOAD))
        self.assertFalse(matches(faq, recovery.DOWNLOAD))
        for marker in (recovery.FAQ_TITLE, recovery.WEBVIEW_REFRESH, recovery.WEBVIEW_EXIT):
            self.assertTrue(matches(faq, marker))
            self.assertFalse(matches(download, marker))


if __name__ == '__main__':
    unittest.main()
