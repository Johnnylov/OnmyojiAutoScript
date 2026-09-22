"""Recognition retry and concurrent ZeroRPC regressions; no game/services needed.

The server tests bind private in-process endpoints and use fake slow inference.
They do not connect to or restart the user's running recognition services.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import pickle
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

import gevent
import numpy as np
import zerorpc

from test_image_template_guard import methods

with patch.dict(sys.modules, {'module.logger': SimpleNamespace(logger=Mock())}):
    from module.base.rpc import call_with_reconnect, wait_for_future
    from module.image.rpc import ImageClient
    from module.image.runtime import ImageRuntime
    from module.ocr.rpc import ModelProxy, OcrRuntime


class RetryTests(unittest.TestCase):
    def setUp(self):
        self.image = np.arange(120, dtype=np.uint8).reshape((5, 8, 3))

    def proxy(self, cls, error, method, result):
        proxy = cls.__new__(cls)
        proxy.address = 'tcp://127.0.0.1:1'
        old = Mock()
        getattr(old, method).side_effect = error
        proxy.client = old
        new = Mock()
        getattr(new, method).return_value = result
        return proxy, old, new

    def test_frame_registration_reconnects_once_with_identical_pixels(self):
        for error in (zerorpc.TimeoutExpired(10), zerorpc.LostRemote('lost')):
            with self.subTest(error=type(error).__name__):
                proxy, old, new = self.proxy(ImageClient, error, 'register_frame', {'frame_id': 'fresh'})
                with patch('module.base.rpc.zerorpc.Client', return_value=new) as factory:
                    self.assertEqual(proxy.register_frame(self.image, 'oas2'), {'frame_id': 'fresh'})
                factory.assert_called_once_with(timeout=10)
                old.close.assert_called_once()
                new.connect.assert_called_once_with(proxy.address)
                first = old.register_frame.call_args
                retry = new.register_frame.call_args
                self.assertEqual(first.args, retry.args)
                self.assertEqual(retry.kwargs, {'timeout': 20})
                np.testing.assert_array_equal(pickle.loads(retry.args[0]), self.image)
                self.assertEqual(retry.args[1], 'oas2')

    def test_ocr_retry_preserves_options_and_boxed_results(self):
        result = [{'box': [[0, 0], [4, 0], [4, 3], [0, 3]], 'ocr_text': '挑战', 'score': .99}]
        proxy, old, new = self.proxy(ModelProxy, zerorpc.TimeoutExpired(10), 'detect_and_ocr', result)
        with patch('module.base.rpc.zerorpc.Client', return_value=new):
            received = proxy.detect_and_ocr(self.image, .7, 1.2, .4, True)
        self.assertEqual(new.detect_and_ocr.call_args.args, old.detect_and_ocr.call_args.args)
        self.assertEqual(new.detect_and_ocr.call_args.args[1:], (.7, 1.2, .4, True))
        self.assertEqual(received[0].ocr_text, '挑战')
        np.testing.assert_array_equal(received[0].box, result[0]['box'])

    def test_single_line_ocr_retry_keeps_raw_text_and_score(self):
        proxy, _, new = self.proxy(ModelProxy, zerorpc.TimeoutExpired(10), 'ocr_single_line', ('12/12时', .98))
        with patch('module.base.rpc.zerorpc.Client', return_value=new):
            self.assertEqual(proxy.ocr_single_line(self.image), ('12/12时', .98))

    def test_persistent_timeout_propagates_after_one_retry(self):
        proxy, old, new = self.proxy(ImageClient, zerorpc.TimeoutExpired(10), 'register_frame', None)
        final = zerorpc.TimeoutExpired(20)
        new.register_frame.side_effect = final
        with patch('module.base.rpc.zerorpc.Client', return_value=new) as factory:
            with self.assertRaises(zerorpc.TimeoutExpired) as caught:
                proxy.register_frame(self.image, 'oas1')
        self.assertIs(caught.exception, final)
        self.assertEqual(old.register_frame.call_count, 1)
        self.assertEqual(new.register_frame.call_count, 1)
        factory.assert_called_once()

    def test_remote_errors_and_programming_errors_do_not_reconnect(self):
        for error in (zerorpc.RemoteError('ValueError', 'invalid image', None), ValueError('bad payload')):
            with self.subTest(error=type(error).__name__):
                proxy, old, new = self.proxy(ImageClient, error, 'register_frame', None)
                with patch('module.base.rpc.zerorpc.Client', return_value=new) as factory:
                    with self.assertRaises(type(error)) as caught:
                        proxy.register_frame(self.image, 'oas1')
                self.assertIs(caught.exception, error)
                factory.assert_not_called()
                old.close.assert_not_called()

    def test_reconnect_then_expired_frame_uses_original_image_once(self):
        proxy, old, new = self.proxy(ImageClient, zerorpc.TimeoutExpired(10), 'match_rule', None)
        new.match_rule.side_effect = [zerorpc.RemoteError('KeyError', "'Unknown frame id: old'", None),
                                      {'matched': True}]
        with patch('module.base.rpc.zerorpc.Client', return_value=new):
            self.assertEqual(proxy.match_rule({'name': 'test'}, self.image, 'old', .9), {'matched': True})
        self.assertEqual(old.match_rule.call_count, 1)
        self.assertEqual(new.match_rule.call_count, 2)
        first, fallback = new.match_rule.call_args_list
        self.assertEqual(first.args[1:3], ('old', None))
        self.assertIsNone(fallback.args[1])
        np.testing.assert_array_equal(pickle.loads(fallback.args[2]), self.image)

    def test_failed_registration_clears_previous_screenshot_reference(self):
        image_client = Mock(register_frame=Mock(side_effect=zerorpc.TimeoutExpired(20)))
        cls = methods('module/device/screenshot.py', 'Screenshot', ['screenshot'],
                      dict(datetime=datetime, get_image_client=lambda: image_client))
        device = cls()
        device.image_frame_id = 'previous'
        device._screenshot_interval = Mock()
        device.config = SimpleNamespace(config_name='oas1', script=SimpleNamespace(
            device=SimpleNamespace(screenshot_method='test')))
        device.screenshot_adb = Mock()
        device.screenshot_methods = {'test': lambda: self.image}
        with self.assertRaises(zerorpc.TimeoutExpired):
            device.screenshot()
        self.assertIsNone(device.image_frame_id)
        self.assertIs(device.image, self.image)


class ResponsiveRuntimeTests(unittest.TestCase):
    def test_future_wait_returns_results_and_exceptions_without_an_active_server(self):
        with ThreadPoolExecutor(max_workers=1) as pool:
            self.assertEqual(wait_for_future(pool.submit(lambda: (time.sleep(.02), 42)[1])), 42)
            error = ValueError('worker failure')

            def fail():
                time.sleep(.02)
                raise error

            with self.assertRaises(ValueError) as caught:
                wait_for_future(pool.submit(fail))
            self.assertIs(caught.exception, error)

    def assert_responsive(self, runtime, patch_name, request):
        started, release, finished = threading.Event(), threading.Event(), threading.Event()

        def slow(*args, **kwargs):
            started.set()
            release.wait(2)  # Bounds the regression even with the old blocking implementation.
            finished.set()
            return ('12/12时', .99) if isinstance(runtime, OcrRuntime) else {'matched': True}

        address = 'inproc://recognition-regression-' + uuid4().hex
        server = zerorpc.Server(runtime)
        server.bind(address)
        serving = gevent.spawn(server.run)
        first, second = zerorpc.Client(timeout=1), zerorpc.Client(timeout=1)
        first.connect(address)
        second.connect(address)
        pending = None
        try:
            with patch.object(runtime, patch_name, side_effect=slow):
                pending = gevent.spawn(request, first)
                deadline = time.monotonic() + 1
                while not started.is_set() and time.monotonic() < deadline:
                    gevent.sleep(.005)
                self.assertTrue(started.is_set())
                self.assertFalse(finished.is_set(), 'Recognition blocked the RPC event loop')
                self.assertTrue(second.ping())
                if isinstance(runtime, ImageRuntime):
                    # Mirrors the other account registering a screenshot while
                    # matching is still running for this account.
                    info = second.register_frame(pickle.dumps(np.zeros((3, 4, 3), np.uint8)), 'oas2')
                    self.assertTrue(info['frame_id'])
                self.assertFalse(finished.is_set(), 'A second request waited for slow recognition')
                release.set()
                self.assertIsNotNone(pending.get(timeout=1))
        finally:
            release.set()
            if pending is not None:
                pending.kill()
            first.close()
            second.close()
            server.close()
            serving.kill()
            runtime.shutdown()

    def test_slow_single_image_match_does_not_block_frame_registration(self):
        runtime = ImageRuntime({'worker_count': 2})
        payload = pickle.dumps(np.zeros((3, 4, 3), np.uint8))
        self.assert_responsive(runtime, '_match_rule_payload',
                               lambda client: client.match_rule({}, None, payload, None))

    def test_slow_image_batch_does_not_block_frame_registration(self):
        for endpoint, implementation in (('match_many', '_match_rule_payload'),
                                          ('match_all_any_many', '_match_all_any_payload')):
            with self.subTest(endpoint=endpoint):
                runtime = ImageRuntime({'worker_count': 2})
                payload = pickle.dumps(np.zeros((3, 4, 3), np.uint8))
                self.assert_responsive(runtime, implementation,
                    lambda client: getattr(client, endpoint)([{}, {}], None, payload, None))

    def test_slow_ocr_worker_keeps_rpc_responsive(self):
        runtime = OcrRuntime({'worker_count': 2})
        payload = pickle.dumps(np.zeros((3, 4, 3), np.uint8))
        self.assert_responsive(runtime, '_ocr_single_line', lambda client: client.ocr_single_line(payload))


if __name__ == '__main__':
    unittest.main()
