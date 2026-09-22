"""Offline regressions for expired image frames; no RPC service or device is started.

Run: toolkit/python.exe -B -m unittest dev_tools.test_image_frame_recovery -v
"""

from datetime import datetime
from pathlib import Path
import pickle
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np
import zerorpc

from dev_tools.test_activity_preparation_retry import source_methods

# Importing the application logger creates a log file. These tests need neither
# the application logger nor deployment settings and must leave profiles alone.
with patch.dict(sys.modules, {'module.logger': SimpleNamespace(logger=Mock())}):
    from module.image.rpc import ImageClient
    from module.image.runtime import ImageRuntime
    from module.base.rpc import call_with_reconnect


class LocalRpc:
    """Exercise the unchanged server API with zerorpc's actual error envelope."""

    def __init__(self, runtime):
        self.runtime = runtime
        self.calls = []

    def __getattr__(self, name):
        remote = getattr(self.runtime, name)

        def call(*args):
            self.calls.append((name, args))
            try:
                return remote(*args)
            except Exception as exc:
                raise zerorpc.RemoteError(type(exc).__name__, str(exc), None) from exc
        return call


class FrameRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.image = np.random.default_rng(7).integers(0, 256, (32, 40, 3), dtype=np.uint8)
        self.template = self.image[12:20, 17:24].copy()
        path = Path(self.temp.name) / 'target.png'
        cv2.imwrite(str(path), cv2.cvtColor(self.template, cv2.COLOR_RGB2BGR))
        self.rule = dict(name='target', file=str(path), method='Template matching',
                         threshold=.99, roi_front=[0, 0, 7, 8], roi_back=[0, 0, 40, 32])
        self.runtime = ImageRuntime({'frame_cache_expire_seconds': 10, 'worker_count': 1})
        self.addCleanup(self.runtime.shutdown)
        self.rpc = LocalRpc(self.runtime)
        # Install an in-process transport, without constructing a network client.
        self.client = ImageClient.__new__(ImageClient)
        self.client.client = self.rpc

    def frame(self):
        return self.client.register_frame(self.image, 'offline')['frame_id']

    def expire(self, frame_id):
        # Reproduce time spent in OCR or a team barrier without sleeping.
        self.runtime._frames[frame_id].last_access_at -= 11

    def match(self, name, frame_id=None):
        kwargs = dict(image=self.image, frame_id=frame_id, threshold=.999)
        if name == 'match_dynamic_template':
            return self.client.match_dynamic_template(self.template, roi_back=[0, 0, 40, 32],
                                                      name='animation', **kwargs)
        if name in ('match_many', 'match_all_any_many'):
            return getattr(self.client, name)([self.rule, self.rule], **kwargs)
        if name in ('match_all', 'match_all_any'):
            kwargs['roi'] = [0, 0, 40, 32]
        return getattr(self.client, name)(self.rule, **kwargs)

    def test_expired_frame_recovers_all_match_endpoints_with_same_results(self):
        endpoints = ('match_rule', 'match_rule_with_brightness_window', 'match_many',
                     'match_all', 'match_all_any', 'match_all_any_many', 'match_dynamic_template')
        for name in endpoints:
            with self.subTest(endpoint=name):
                expected = self.match(name)
                frame_id = self.frame()
                self.expire(frame_id)
                self.rpc.calls.clear()
                self.assertEqual(expected, self.match(name, frame_id))
                self.assertEqual(2, len(self.rpc.calls))
                first, retry = [args for _, args in self.rpc.calls]
                self.assertEqual((frame_id, None), first[1:3])
                self.assertIsNone(retry[1])
                np.testing.assert_array_equal(self.image, pickle.loads(retry[2]))
                self.assertEqual(first[0], retry[0])
                self.assertEqual(first[3:], retry[3:])

    def test_valid_cached_frame_does_not_upload_or_retry(self):
        frame_id = self.frame()
        self.rpc.calls.clear()
        with patch('module.image.rpc.pickle.dumps', side_effect=AssertionError('unexpected upload')):
            self.assertTrue(self.match('match_rule', frame_id)['matched'])
        self.assertEqual(1, len(self.rpc.calls))
        self.assertEqual((frame_id, None), self.rpc.calls[0][1][1:3])

    def test_activity_ocr_delay_does_not_break_the_following_page_match(self):
        proxy_class = source_methods('module/ocr/rpc.py', 'ModelProxy', ['ocr_single_line'],
                                     dict(pickle=pickle, call_with_reconnect=call_with_reconnect))
        proxy = proxy_class()
        frame_id = self.frame()

        def slow_ocr(payload):
            # Activity countdown OCR uses pixels, not an image-server frame ID.
            np.testing.assert_array_equal(self.image, pickle.loads(payload))
            self.expire(frame_id)
            return ('08:59:31', .99)

        proxy.client = SimpleNamespace(ocr_single_line=slow_ocr)
        self.assertEqual(('08:59:31', .99), proxy.ocr_single_line(self.image))
        self.rpc.calls.clear()
        self.assertTrue(self.match('match_rule', frame_id)['matched'])
        self.assertEqual(2, len(self.rpc.calls))

    def test_evicted_frame_uses_original_pixels_without_replacing_new_frame(self):
        old_id = self.frame()
        other_image = np.zeros_like(self.image)
        new_id = self.client.register_frame(other_image, 'offline')['frame_id']
        self.assertNotIn(old_id, self.runtime._frames)
        self.assertTrue(self.match('match_rule', old_id)['matched'])
        self.assertEqual(new_id, self.runtime._config_frames['offline'])
        np.testing.assert_array_equal(other_image, self.runtime._frames[new_id].image)

    def test_server_restart_missing_frame_recovers_without_protocol_change(self):
        frame_id = self.frame()
        self.runtime._frames.clear()
        self.runtime._config_frames.clear()
        self.assertTrue(self.match('match_rule', frame_id)['matched'])
        self.assertFalse(self.runtime._frames)

    def test_missing_frame_without_source_image_preserves_remote_error(self):
        frame_id = self.frame()
        self.expire(frame_id)
        self.rpc.calls.clear()
        with self.assertRaises(zerorpc.RemoteError) as caught:
            self.client.match_rule(self.rule, frame_id=frame_id)
        self.assertEqual('KeyError', caught.exception.name)
        self.assertEqual(1, len(self.rpc.calls))

    def test_unrelated_errors_and_another_frame_are_never_retried(self):
        errors = (
            zerorpc.RemoteError('KeyError', "'missing template field'", None),
            zerorpc.RemoteError('KeyError', "'Unknown frame id: other'", None),
            zerorpc.RemoteError('ValueError', "'Unknown frame id: stale'", None),
            KeyError('Unknown frame id: stale'),
        )
        for error in errors:
            with self.subTest(error=type(error).__name__, message=str(error)):
                remote = Mock(side_effect=error)
                self.client.client = SimpleNamespace(match_rule=remote)
                with self.assertRaises(type(error)) as caught:
                    self.match('match_rule', 'stale')
                self.assertIs(error, caught.exception)
                self.assertEqual(1, remote.call_count)

    def test_retry_error_propagates_and_cannot_recurse(self):
        missing = zerorpc.RemoteError('KeyError', "'Unknown frame id: stale'", None)
        for retry_error in (missing, zerorpc.RemoteError('ValueError', 'bad template', None)):
            with self.subTest(error=retry_error.name):
                remote = Mock(side_effect=[missing, retry_error])
                self.client.client = SimpleNamespace(match_rule=remote)
                with self.assertRaises(zerorpc.RemoteError) as caught:
                    self.match('match_rule', 'stale')
                self.assertIs(retry_error, caught.exception)
                self.assertEqual(2, remote.call_count)

    def test_direct_image_request_is_not_retried_for_frame_error(self):
        error = zerorpc.RemoteError('KeyError', "'Unknown frame id: None'", None)
        remote = Mock(side_effect=error)
        self.client.client = SimpleNamespace(match_rule=remote)
        with self.assertRaises(zerorpc.RemoteError):
            self.match('match_rule')
        self.assertEqual(1, remote.call_count)


class TeamFrameRefreshTests(unittest.TestCase):
    def setUp(self):
        cls = source_methods('tasks/base_task.py', 'BaseTask', ['wait_local_team_ready'],
                             dict(datetime=datetime))
        self.task = cls()
        self.coordinator = Mock(session_id='team')
        self.task.config = SimpleNamespace(team_sync=self.coordinator)
        self.task.device = Mock()
        self.task.start_time = datetime(2000, 1, 1)

    def test_barrier_clears_stuck_records_before_taking_fresh_screenshot(self):
        calls = Mock()
        calls.attach_mock(self.coordinator.ready, 'ready')
        calls.attach_mock(self.task.device, 'device')
        self.task.wait_local_team_ready()
        self.assertEqual(['ready', 'device.stuck_record_clear', 'device.click_record_clear',
                          'device.screenshot'], [item[0] for item in calls.mock_calls])
        self.assertGreater(self.task.start_time, datetime(2000, 1, 1))

    def test_failed_barrier_cannot_refresh_or_reset_timers(self):
        self.coordinator.ready.side_effect = RuntimeError('team cancelled')
        with self.assertRaisesRegex(RuntimeError, 'team cancelled'):
            self.task.wait_local_team_ready()
        self.assertFalse(self.task.device.mock_calls)
        self.assertEqual(datetime(2000, 1, 1), self.task.start_time)

    def test_unconfigured_task_keeps_existing_frame(self):
        self.coordinator.session_id = None
        self.task.wait_local_team_ready()
        self.coordinator.ready.assert_not_called()
        self.assertFalse(self.task.device.mock_calls)
        self.assertEqual(datetime(2000, 1, 1), self.task.start_time)


if __name__ == '__main__':
    unittest.main()
