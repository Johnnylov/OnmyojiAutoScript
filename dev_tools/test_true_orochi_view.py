"""Image regressions for cropped/scaled screenshots, with no game connection."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tasks.TrueOrochi.view import TrueOrochiView, parse_entries, parse_rewards


FIXTURES = Path(__file__).with_name('fixtures') / 'true_orochi'


def screenshot(name):
    return np.array(Image.open(FIXTURES / f'{name}.png').convert('RGB'))


class TrueOrochiViewTests(unittest.TestCase):
    def test_supplied_screens_have_distinct_states(self):
        for name in ('entry', 'detail', 'confirm', 'private', 'room', 'prepare'):
            with self.subTest(name=name):
                view = TrueOrochiView(screenshot(name))
                for state in ('entry', 'detail', 'confirm', 'private', 'room', 'prepare'):
                    self.assertEqual(bool(getattr(view, state)()), name == state, (name, state))

    def test_translation_and_scaling_preserve_located_panels(self):
        for name in ('entry', 'detail', 'confirm', 'private', 'room'):
            for scale in (.8, 1.125, 1.25):
                with self.subTest(name=name, scale=scale):
                    image = cv2.resize(screenshot(name), None, fx=scale, fy=scale)
                    h, w = image.shape[:2]
                    canvas = np.full((h+47, w+93, 3), 55, dtype=np.uint8)
                    canvas[23:23+h, 37:37+w] = image
                    panel = getattr(TrueOrochiView(canvas), name)()
                    self.assertIsNotNone(panel)
                    self.assertAlmostEqual(panel.x, 37, delta=3)
                    self.assertAlmostEqual(panel.y, 23, delta=3)

    def test_scaled_configuration_locates_ready_inside_the_button(self):
        # Its small top-center header has nearly identical neighboring scales;
        # validate the actual distant click region instead of the extrapolated
        # top-left panel origin, which is not a control on this screen.
        for scale in (.8, 1., 1.125, 1.25):
            with self.subTest(scale=scale):
                image = cv2.resize(screenshot('prepare'), None, fx=scale, fy=scale)
                h, w = image.shape[:2]
                canvas = np.full((h+47, w+93, 3), 55, dtype=np.uint8)
                canvas[23:23+h, 37:37+w] = image
                panel = TrueOrochiView(canvas).prepare()
                self.assertIsNotNone(panel)
                x, y, width, height = panel.roi(1011, 518, 98, 52)
                self.assertAlmostEqual(x, 37+1011*scale, delta=7)
                self.assertAlmostEqual(y, 23+518*scale, delta=7)
                self.assertTrue(x+width < 37+1130*scale and y+height < 23+600*scale)

    def test_private_must_have_blue_tick(self):
        image = screenshot('private')
        view = TrueOrochiView(image)
        self.assertTrue(view.private_selected(view.private()))
        # Replace the private radio with the actual unchecked first radio.
        image[242:274, 47:79] = image[128:160, 47:79]
        view = TrueOrochiView(image)
        self.assertIsNotNone(view.private())
        self.assertFalse(view.private_selected(view.private()))

    def test_empty_slot_disappears_after_occupancy(self):
        image = screenshot('room')
        view = TrueOrochiView(image)
        self.assertTrue(view.empty_slot(view.room()))
        image[180:280, 530:625] = 90
        view = TrueOrochiView(image)
        self.assertIsNotNone(view.room())
        self.assertFalse(view.empty_slot(view.room()))

    def test_room_label_alone_is_not_a_true_orochi_room(self):
        image = screenshot('room')
        image[565:615, 825:980] = 45
        self.assertIsNone(TrueOrochiView(image).room())

    def test_supplied_joined_room_has_one_partner_despite_unused_third_slot(self):
        for size in (None, (1280, 720)):
            image = screenshot('room_joined')
            if size:
                image = cv2.resize(image, size)
            view = TrueOrochiView(image)
            self.assertIsNotNone(view.room())
            self.assertFalse(view.empty_slot(view.room()))
            self.assertIsNone(view.prepare())

    def test_prepare_requires_both_configuration_header_and_ready_button(self):
        for box in ((530, 0, 625, 90), (990, 490, 1135, 610)):
            image = screenshot('prepare')
            x1, y1, x2, y2 = box
            image[y1:y2, x1:x2] = 45
            self.assertIsNone(TrueOrochiView(image).prepare())
        dimmed = (screenshot('prepare').astype(float)*.4).astype(np.uint8)
        self.assertIsNone(TrueOrochiView(dimmed).prepare())

    def test_no_blind_fallback_for_unreadable_red_badge(self):
        view = TrueOrochiView(screenshot('entry'))
        self.assertEqual(view.entry_count(lambda _: '2'), 2)
        self.assertEqual(view.entry_count(lambda _: '1'), 1)
        self.assertIsNone(view.entry_count(lambda _: ''))
        self.assertIsNone(view.entry_count(lambda _: '7'))

    def test_icon_without_stack_badge_is_one_entry(self):
        image = screenshot('entry')
        image[0:27, 65:96] = 25
        read = Mock()
        self.assertEqual(TrueOrochiView(image).entry_count(read), 1)
        read.assert_not_called()

    def test_reward_count_is_not_the_used_counter(self):
        for text, expected in [('2/2', 2), ('1/2', 1), ('0/2', 0),
                               ('本周剩余奖励次数：２／２', 2),
                               ('（本周剩余奖励次数：1）', 1)]:
            self.assertEqual(parse_rewards(text), expected)
        for text in ('', '2', '2/3', '3/2', '12/2', '每周前2次挑战'):
            self.assertIsNone(parse_rewards(text))
        for text in ('', '0', '10', '?'):
            self.assertIsNone(parse_entries(text))

    def test_actual_local_ocr_reads_all_three_counters(self):
        from module.ocr.ppocr import TextSystem
        model = TextSystem()
        def read(image):
            text, score = model.ocr_single_line(cv2.resize(image, None, fx=2, fy=2))
            return text if score >= .6 else ''
        self.assertEqual(TrueOrochiView(screenshot('entry')).entry_count(read), 2)
        for name in ('detail', 'confirm'):
            self.assertEqual(TrueOrochiView(screenshot(name)).rewards(read), 2)


if __name__ == '__main__':
    unittest.main()
