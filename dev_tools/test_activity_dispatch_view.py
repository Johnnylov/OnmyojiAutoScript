"""Dispatch vision regressions using tiny assets and optional supplied screenshots."""

from pathlib import Path
import os
import tempfile
import unittest

import cv2
import numpy as np

from tasks.ActivityShikigami.dispatch_view import DispatchView, parse_duration, ASSETS


TEMP = Path(os.environ.get('ACTIVITY_REFERENCE_DIR', tempfile.gettempdir()))
FILES = {
    'idle': 'e233c8c3-5d2a-4f81-84e3-26f72d399de1',
    'portraits': '86eb7849-a5ab-4404-aa7f-e1b276e75279',
    'setup': '4909a6ae-dd6d-416e-817f-3962c2642354',
    'running': '95beef8e-89c2-4714-b256-9be8932833dc',
    'running_later': '4be37c8e-260c-41e3-b9f5-d1c0befee047',
}


def rgb(path):
    return cv2.cvtColor(cv2.imread(str(path)), cv2.COLOR_BGR2RGB)


def paste(image, name, x, y):
    patch = rgb(ASSETS / (name + '.png'))
    image[y:y+patch.shape[0], x:x+patch.shape[1]] = patch


def synthetic_map():
    image = np.full((625, 1111, 3), (41, 30, 24), dtype=np.uint8)
    paste(image, 'map_name', 385, 237)
    paste(image, 'empty', 205, 59)
    for x, y in ((148, 211), (158, 319), (385, 302)):
        paste(image, 'locked', x, y)
    return image


class DispatchVisionTests(unittest.TestCase):
    def load(self, name):
        path = TEMP / ('codex-clipboard-' + FILES[name] + '.png')
        if not path.exists():
            self.skipTest('Supplied local reference screenshots are unavailable')
        return rgb(path)

    def view(self, setup=False):
        return DispatchView(lambda image, roi: '放置时间9/9时' if setup else '11:56:40')

    def test_duration_parser_rejects_ambiguous_or_impossible_counters(self):
        for text, expected in (('放置时间 9 / 9时', (9, 9)), ('12/12时', (12, 12)),
                               ('放置时间0/0时', (0, 0)), ('8/18小时', (8, 18))):
            self.assertEqual(parse_duration(text), expected)
        for text in ('', None, '9', '9/8时', '99/99时', '9/9/9', '-1/9', '消耗9/9', '9/9时购买'):
            self.assertIsNone(parse_duration(text))

    def test_portable_map_requires_labels_and_four_identified_slots(self):
        observation = self.view().observe(synthetic_map())
        self.assertEqual(observation.kind, 'map')
        self.assertTrue(observation.all_slots_known)
        self.assertEqual((len(observation.empty), observation.locked, observation.running), (1, 3, 0))
        image = synthetic_map()
        image[237:262, 385:482] = 0
        self.assertEqual(self.view().observe(image).kind, 'unknown')

    def test_map_and_slot_coordinates_follow_scale_and_translation(self):
        image = cv2.resize(synthetic_map(), None, fx=.75, fy=.75)
        frame = np.zeros((image.shape[0] + 90, image.shape[1] + 140, 3), dtype=np.uint8)
        frame[35:35+image.shape[0], 75:75+image.shape[1]] = image
        observation = self.view().observe(frame)
        self.assertTrue(observation.all_slots_known)
        x, y, w, h = observation.empty[0]
        self.assertAlmostEqual(x + w/2, 75 + 225.5 * .75, delta=4)
        self.assertAlmostEqual(y + h/2, 35 + 84.5 * .75, delta=4)

    def test_supplied_native_and_runtime_frames(self):
        for name in FILES:
            image = self.load(name)
            for label, frame in (('native', image), ('runtime', cv2.resize(image, (1280, 720)))):
                with self.subTest(name=name, size=label):
                    observation = self.view(name == 'setup').observe(frame)
                    if name == 'idle':
                        self.assertTrue(observation.all_slots_known)
                        self.assertEqual((len(observation.empty), observation.locked), (1, 3))
                    elif name.startswith('running'):
                        self.assertTrue(observation.all_slots_known)
                        self.assertEqual((len(observation.empty), observation.running), (0, 4))
                    elif name == 'portraits':
                        self.assertEqual(observation.kind, 'portraits')
                        self.assertEqual(len(observation.available), 6)
                        self.assertIsNone(observation.selected)
                    else:
                        self.assertEqual(observation.kind, 'setup')
                        self.assertEqual((observation.selected, observation.current, observation.maximum), (1, 9, 9))
                        for roi in (observation.plus_roi, observation.minus_roi, observation.submit_roi):
                            self.assertGreater(roi[1], frame.shape[0] * .4)

    def test_translated_setup_uses_its_panel_controls(self):
        image = self.load('setup')
        frame = np.zeros((image.shape[0] + 85, image.shape[1] + 100, 3), dtype=np.uint8)
        frame[41:41+image.shape[0], 63:63+image.shape[1]] = image
        observation = self.view(True).observe(frame)
        self.assertEqual(observation.kind, 'setup')
        self.assertEqual(observation.selected, 1)
        x, y, w, h = observation.plus_roi
        self.assertAlmostEqual(x + w/2, 63 + 1016, delta=3)
        self.assertAlmostEqual(y + h/2, 41 + 320, delta=3)

    def test_dimmed_map_or_setup_is_not_actionable(self):
        for name in ('idle', 'setup'):
            image = self.load(name)
            for factor in (.5, .75):
                with self.subTest(name=name, factor=factor):
                    observation = self.view(True).observe((image * factor).astype(np.uint8))
                    self.assertEqual(observation.kind, 'unknown')
                    self.assertFalse(observation.empty)
                    self.assertIsNone(observation.submit_roi)
                    self.assertIsNone(observation.close_roi)

    def test_desaturated_busy_portrait_is_excluded(self):
        image = self.load('portraits')
        gray = cv2.cvtColor(image[470:618, 47:194], cv2.COLOR_RGB2GRAY)
        image[470:618, 47:194] = cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)
        observation = self.view().observe(image)
        self.assertEqual(observation.kind, 'portraits')
        self.assertNotIn(0, [index for index, _ in observation.available])
        self.assertEqual(len(observation.available), 5)

    def test_missing_duration_controls_cannot_create_a_setup(self):
        image = self.load('setup')
        image[300:342, 995:1037] = 0
        observation = self.view(True).observe(image)
        self.assertEqual(observation.kind, 'unknown')
        self.assertIsNone(observation.submit_roi)
        self.assertIsNotNone(observation.close_roi)

    def test_unreadable_or_non_countdown_text_never_confirms_running(self):
        image = self.load('running')
        for text in ('', '领取奖励', '11:99:20'):
            observation = DispatchView(lambda image, roi: text).observe(image)
            self.assertEqual(observation.running, 0)
            self.assertFalse(observation.all_slots_known)

    def test_unreadable_setup_duration_remains_nonactionable_to_runner(self):
        observation = DispatchView().observe(self.load('setup'))
        self.assertEqual(observation.kind, 'setup')
        self.assertIsNone(observation.current)
        self.assertIsNone(observation.maximum)

    @unittest.skipUnless(os.environ.get('ACTIVITY_OCR_REPLAY') == '1',
                         'Set ACTIVITY_OCR_REPLAY=1 for the bundled local OCR model')
    def test_actual_ocr_reads_duration_and_all_running_timers(self):
        # Use the production RuleOcr pipeline with a local model. This does
        # not connect to OCR RPC, instantiate a device, or launch the game.
        from module.atom.ocr import RuleOcr
        from module.ocr.ppocr import TextSystem

        model = TextSystem(ort_providers=['CPUExecutionProvider'])

        def read(image, roi):
            rule = RuleOcr(roi=roi, area=roi, mode='Single', method='Default',
                           keyword='', name='dispatch_ocr_regression')
            rule.model = model
            return rule.ocr(image)

        for name in ('setup', 'running', 'running_later'):
            image = self.load(name)
            for label, frame in (('native', image), ('runtime', cv2.resize(image, (1280, 720)))):
                with self.subTest(name=name, size=label):
                    observation = DispatchView(read_text=read).observe(frame)
                    if name == 'setup':
                        self.assertEqual(observation.kind, 'setup')
                        self.assertEqual((observation.current, observation.maximum), (9, 9))
                    else:
                        self.assertTrue(observation.all_slots_known)
                        self.assertEqual(observation.running, 4)

    def test_unknown_and_invalid_images_have_no_controls(self):
        for image in (None, np.zeros((0, 0, 3), dtype=np.uint8),
                      np.zeros((720, 1280, 3), dtype=np.uint8)):
            observation = self.view().observe(image)
            self.assertEqual(observation.kind, 'unknown')
            self.assertIsNone(observation.close_roi)
            self.assertFalse(observation.empty)


if __name__ == '__main__':
    unittest.main()
