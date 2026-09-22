"""Replay cache dialogs and navigation recovery without a device or RPC."""

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import cv2
import numpy as np

from test_image_template_guard import methods
from tasks.GameUi import cache_cleanup as cache


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'dev_tools/fixtures/game_ui/cache_cleanup.png'


def matches(frame, marker):
    x, y, w, h = marker.roi_back
    template = cv2.imread(str(ROOT / marker.file))
    patch = frame[y:y+h, x:x+w]
    return cv2.minMaxLoc(cv2.matchTemplate(patch, template, cv2.TM_CCOEFF_NORMED))[1] >= marker.threshold


class CacheCleanupTests(unittest.TestCase):
    def task(self, frame):
        namespace = dict(cache_cleanup_visible=cache.cache_cleanup_visible,
                         CACHE_CLEANUP_CANCEL=cache.CANCEL, logger=Mock(),
                         time=Mock(), random=Mock(randrange=Mock(return_value=10)),
                         run_once=lambda fn: fn)
        cls = methods('tasks/GameUi/navigator.py', 'GameUi',
                      ['close_unknown_pages', '_record_unknown_close_event'], namespace)
        task = cls()
        task.device = SimpleNamespace(app_is_running=Mock(return_value=True), get_orientation=Mock())
        task.config = SimpleNamespace(script=SimpleNamespace(device=SimpleNamespace(control_method='adb')))
        task.navigator = SimpleNamespace(local_unknown_closers=['local_confirm'], unknown_close_history=[])
        task.DEFAULT_UNKNOWN_CLOSERS = ['generic_confirm']
        task.maybe_screenshot = Mock()
        task.appear = lambda marker: matches(frame, marker)
        task._action_name = lambda action: action.name if action == cache.CANCEL else str(action)
        task.clicked = []

        def execute(action, **kwargs):
            if action == cache.CANCEL and task.appear(action):
                task.clicked.append(action.name)
                return True
            if action != cache.CANCEL:
                raise AssertionError('A generic closer was attempted over cache management')
            return False

        task._execute_action = Mock(side_effect=execute)
        return task

    def test_retained_popup_cancels_before_generic_or_local_closers(self):
        task = self.task(cv2.imread(str(FIXTURE)))
        self.assertTrue(task.close_unknown_pages())
        self.assertEqual(task.clicked, [cache.CANCEL.name])
        self.assertEqual(task.navigator.unknown_close_history, [cache.CANCEL.name])
        self.assertEqual(task._execute_action.call_count, 1)

    def test_all_five_archived_frames_only_recognize_the_two_cache_errors(self):
        errors = ('oas2_1790013339788', 'oas2_1790035255496', 'oas1_1790037845244',
                  'oas1_1790042509872', 'oas2_1790042047724')
        frames = [p for error in errors for p in (ROOT / 'log/error' / error).glob('*.png')]
        if not frames:
            self.skipTest('Archived error captures have been cleared; retained fixture is tested separately')
        positives = {'oas2_1790013339788', 'oas2_1790035255496'}
        for path in frames:
            with self.subTest(error=path.parent.name):
                task = self.task(cv2.imread(str(path)))
                self.assertEqual(cache.cache_cleanup_visible(task), path.parent.name in positives)
                if path.parent.name in positives:
                    self.assertTrue(task.close_unknown_pages())
                    self.assertEqual(task.clicked, [cache.CANCEL.name])

    def test_partial_anchors_do_not_identify_other_dialogs(self):
        for marker in (cache.TITLE, cache.VIDEO, cache.IMAGES):
            frame = cv2.imread(str(FIXTURE))
            x, y, w, h = marker.roi_back
            frame[y:y+h, x:x+w] = 0
            with self.subTest(missing=marker.name):
                self.assertFalse(cache.cache_cleanup_visible(self.task(frame)))
        self.assertFalse(cache.cache_cleanup_visible(self.task(np.zeros((720, 1280, 3), np.uint8))))

    def test_missing_cancel_never_falls_through_to_confirm(self):
        frame = cv2.imread(str(FIXTURE))
        x, y, w, h = cache.CANCEL.roi_back
        frame[y:y+h, x:x+w] = 0
        task = self.task(frame)
        self.assertFalse(task.close_unknown_pages())
        self.assertEqual(task.clicked, [])
        self.assertEqual(task._execute_action.call_count, 1)

    def test_unresponsive_popup_stops_after_three_cancel_clicks(self):
        task = self.task(cv2.imread(str(FIXTURE)))
        for _ in range(3):
            self.assertTrue(task.close_unknown_pages())
        for _ in range(4):
            self.assertFalse(task.close_unknown_pages())
        self.assertEqual(task.clicked, [cache.CANCEL.name] * 3)

    def test_other_pages_keep_existing_unknown_closers(self):
        task = self.task(np.zeros((720, 1280, 3), np.uint8))
        task._execute_action.side_effect = [False, True]
        self.assertTrue(task.close_unknown_pages())
        self.assertEqual([c.args[0] for c in task._execute_action.call_args_list],
                         ['local_confirm', 'generic_confirm'])


if __name__ == '__main__':
    unittest.main()
