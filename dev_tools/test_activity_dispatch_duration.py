"""Real two-digit dispatch setup and bounded panel recovery regressions."""

from pathlib import Path
import os
import unittest
from unittest.mock import Mock

import cv2
import numpy as np

from tasks.ActivityShikigami.dispatch import DailyDispatcher, DispatchError
from tasks.ActivityShikigami.dispatch_view import DispatchView
from test_activity_dispatch_view import synthetic_map


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'dev_tools/fixtures/activity_dispatch/setup_12_hours.png'


def frame():
    return cv2.cvtColor(cv2.imread(str(FIXTURE)), cv2.COLOR_BGR2RGB)


class DispatchDurationTests(unittest.TestCase):
    def test_two_digit_setup_and_counter_crop_at_supported_resolutions(self):
        for width, height in ((1280, 720), (1111, 625), (840, 473)):
            with self.subTest(size=(width, height)):
                read = Mock(return_value='放置时间12/12时')
                observation = DispatchView(read).observe(cv2.resize(frame(), (width, height)))
                self.assertEqual((observation.kind, observation.selected, observation.current,
                                  observation.maximum), ('setup', 0, 12, 12))
                self.assertEqual(len(observation.available), 6)
                x, y, w, h = read.call_args.args[1]
                scale = width / 1280
                self.assertLessEqual(x, 950 * scale)
                self.assertGreaterEqual(x + w, 1130 * scale)
                self.assertLessEqual(x + w, 1145 * scale)
                self.assertIsNotNone(observation.close_roi)

    def test_original_error_frame_is_setup_instead_of_unknown(self):
        paths = list((ROOT / 'log/error/oas1_1790042509872').glob('*.png'))
        if not paths:
            self.skipTest('Original capture cleared; retained fixture is tested separately')
        image = cv2.cvtColor(cv2.imread(str(paths[0])), cv2.COLOR_BGR2RGB)
        self.assertEqual(DispatchView(lambda *_: '12/12时').observe(image).kind, 'setup')

    def test_unknown_duration_remains_unusable_and_only_collapses_for_recovery(self):
        view = DispatchView(lambda *_: '12/?时')
        observation = view.observe(frame())
        self.assertEqual(observation.kind, 'setup')
        self.assertIsNone(observation.current)
        clicks = Mock()
        worker = DailyDispatcher(Mock(), clicks, view=view, sleep=lambda _: None)
        with self.assertRaises(DispatchError):
            worker._duration(0, observation)
        clicks.assert_not_called()

    def test_recovery_collapses_real_drawer_once_before_confirmed_map(self):
        screenshot = Mock(side_effect=[frame(), synthetic_map(), synthetic_map()])
        clicks = Mock(return_value=True)
        worker = DailyDispatcher(screenshot, clicks,
                                 view=DispatchView(lambda *_: '12/12时'), sleep=lambda _: None)
        self.assertTrue(worker.restore_map())
        clicks.assert_called_once()
        self.assertEqual(clicks.call_args.args[1], 'dispatch_collapse')
        x, y, w, h = clicks.call_args.args[0]
        self.assertTrue(620 <= x < x+w <= 660 and 500 <= y < y+h <= 536)

    def test_setup_under_dark_overlay_is_not_actionable(self):
        observation = DispatchView(lambda *_: '12/12时').observe((frame() * .5).astype(np.uint8))
        self.assertEqual(observation.kind, 'unknown')
        self.assertIsNone(observation.submit_roi)
        self.assertIsNone(observation.close_roi)

    @unittest.skipUnless(os.environ.get('ACTIVITY_OCR_REPLAY') == '1',
                         'Set ACTIVITY_OCR_REPLAY=1 for the bundled local OCR model')
    def test_real_ocr_reads_one_and_two_digit_duration_without_decoration(self):
        from module.atom.ocr import RuleOcr
        from module.ocr.ppocr import TextSystem
        model = TextSystem(ort_providers=['CPUExecutionProvider'])

        def read(image, roi):
            rule = RuleOcr(roi=roi, area=roi, mode='Single', method='Default',
                           keyword='', name='duration_regression')
            rule.model = model
            return rule.ocr(image)

        for hours in (9, 12):
            original = cv2.cvtColor(cv2.imread(str(FIXTURE.with_name(f'setup_{hours}_hours.png'))),
                                    cv2.COLOR_BGR2RGB)
            for width, height in ((1280, 720), (840, 473)):
                with self.subTest(hours=hours, size=(width, height)):
                    observation = DispatchView(read).observe(cv2.resize(original, (width, height)))
                    self.assertEqual(observation.kind, 'setup')
                    self.assertEqual((observation.current, observation.maximum), (hours, hours))


if __name__ == '__main__':
    unittest.main()
