"""Dispatch level-up acknowledgements replayed without a device or OCR service."""

from pathlib import Path
import unittest

import cv2
import numpy as np

from tasks.ActivityShikigami.dispatch import DailyDispatcher
from tasks.ActivityShikigami.dispatch_view import DispatchObservation, DispatchView


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).with_name('fixtures') / 'activity_dispatch'
ERRORS = ('oas2_1789663446921', 'oas2_1789663553398', 'oas2_1789663659466',
          'oas2_1789797877521', 'oas2_1789798034843', 'oas2_1789801254574')


def rgb(path):
    return cv2.cvtColor(cv2.imread(str(path)), cv2.COLOR_BGR2RGB)


class DispatchLevelUpTests(unittest.TestCase):
    def assert_level_up(self, image, scale=1., dx=0, dy=0):
        observation = DispatchView().observe(image)
        self.assertEqual(observation.kind, 'level_up')
        self.assertIsNotNone(observation.dismiss_roi)
        x, y, w, h = observation.dismiss_roi
        self.assertGreaterEqual(x, dx + 550 * scale)
        self.assertGreaterEqual(y, dy + 662 * scale)
        self.assertLessEqual(x + w, dx + 720 * scale)
        self.assertLessEqual(y + h, dy + 699 * scale)
        for name in ('submit_roi', 'plus_roi', 'minus_roi', 'close_roi', 'return_id'):
            self.assertIsNone(getattr(observation, name))
        self.assertFalse(observation.empty)
        self.assertFalse(observation.available)
        return observation

    def test_both_level_up_variants_at_runtime_and_smaller_resolution(self):
        for name in ('level_up_4_to_5', 'level_up_6_to_7'):
            frame = rgb(FIXTURES / (name + '.png'))
            for size, image, scale in (('runtime', frame, 1.),
                                        ('smaller', cv2.resize(frame, (840, 473)), 840/1280)):
                with self.subTest(variant=name, size=size):
                    self.assert_level_up(image, scale)

    def test_all_six_original_error_captures(self):
        for name in ERRORS:
            paths = list((ROOT / 'log/error' / name).glob('*.png'))
            if not paths:
                continue  # The two persistent fixtures cover cleared error logs.
            with self.subTest(error=name):
                self.assert_level_up(rgb(paths[0]))

    def test_translated_popup_and_variable_level_text(self):
        source = rgb(FIXTURES / 'level_up_6_to_7.png')
        # The level numerals, arrow and unlock message vary. Keep only the
        # actual common title and dismissal prompt from the captured UI.
        source[300:545, 370:925] = 0
        image = np.zeros((780, 1370, 3), dtype=np.uint8)
        image[25:745, 43:1323] = source
        self.assert_level_up(image, dx=43, dy=25)

    def test_missing_or_misplaced_anchor_never_exposes_a_click(self):
        source = rgb(FIXTURES / 'level_up_4_to_5.png')
        cases = [('missing_title', (390, 185, 510, 80), None),
                 ('missing_prompt', (535, 655, 195, 45), None),
                 ('shifted_prompt', (535, 655, 195, 45), (535, 580)),
                 ('shifted_title', (390, 185, 510, 80), (300, 185))]
        for name, (x, y, w, h), moved in cases:
            with self.subTest(case=name):
                image = source.copy()
                patch = image[y:y+h, x:x+w].copy()
                image[y:y+h, x:x+w] = 0
                if moved is not None:
                    mx, my = moved
                    image[my:my+h, mx:mx+w] = patch
                observation = DispatchView().observe(image)
                self.assertEqual(observation.kind, 'unknown')
                self.assertIsNone(observation.dismiss_roi)
                self.assertIsNone(observation.close_roi)
                self.assertIsNone(observation.submit_roi)

    def test_dimmed_popup_cannot_be_dismissed_through_another_modal(self):
        image = (rgb(FIXTURES / 'level_up_6_to_7.png') * .5).astype(np.uint8)
        observation = DispatchView().observe(image)
        self.assertEqual(observation.kind, 'unknown')
        self.assertIsNone(observation.dismiss_roi)

    def test_actual_overlay_is_closed_once_then_two_map_frames_are_confirmed(self):
        frame = rgb(FIXTURES / 'level_up_4_to_5.png')
        real_view = DispatchView()
        map_frame = object()
        clicks, reads = [], []

        class ReplayView:
            def observe(self, image):
                return (DispatchObservation(kind='map', running=4) if image is map_frame
                        else real_view.observe(image))

        def screenshot():
            reads.append('map' if clicks else 'level_up')
            return map_frame if clicks else frame

        runner = DailyDispatcher(screenshot, lambda roi, name: clicks.append((roi, name)),
                                 view=ReplayView(), sleep=lambda _: None)
        self.assertTrue(runner.restore_map())
        self.assertEqual([name for _, name in clicks], ['dispatch_level_up_close'])
        self.assertEqual(reads, ['level_up', 'map', 'map'])


if __name__ == '__main__':
    unittest.main()
