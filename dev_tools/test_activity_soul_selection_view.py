"""Offline soul-selector vision tests. No OCR, configuration or device imports.

Run: toolkit/python.exe -m unittest discover -s dev_tools -p test_activity_soul_selection_view.py
The supplied account screenshots are replayed only when still present locally;
portable tests use the four small, committed visual templates.
"""

from pathlib import Path
import os
import unittest

import cv2
import numpy as np

from tasks.ActivityShikigami.soul_selection_view import Panel, SoulSelectionView


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "tasks/ActivityShikigami/soul_selection"
ENTRY_SCREENSHOT = Path(
    "C:/Users/18523/AppData/Local/Temp/"
    "codex-clipboard-6a1b4b0a-0e80-4173-8fe1-52a020ab8837.png")
PANEL_SCREENSHOT = Path(
    "C:/Users/18523/AppData/Local/Temp/"
    "codex-clipboard-23c1a922-2cb2-408b-a69f-f58ccae7b18a.png")


def read_rgb(path):
    return cv2.cvtColor(cv2.imread(str(path)), cv2.COLOR_BGR2RGB)


def paste(image, name, x, y):
    patch = read_rgb(ASSETS / (name + ".png"))
    image[y:y + patch.shape[0], x:x + patch.shape[1]] = patch


def synthetic_panel(checked=(1, 3, 5, 13), title=True, submit=True):
    image = np.full((493, 790, 3), (202, 192, 162), dtype=np.uint8)
    if title:
        paste(image, "title", 60, 28)
    if submit:
        paste(image, "submit", 636, 396)
    # Independent fixture positions from the supplied 3-by-5 selector.
    for index in checked:
        row, col = divmod(index - 1, 5)
        paste(image, "selected", (140, 262, 383, 505, 626)[col] + 16,
              (120, 224, 329)[row] - 34)
    return image


def transform(image, scale, offset=(113, 39)):
    resized = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR)
    x, y = offset
    result = np.full((resized.shape[0] + y + 57, resized.shape[1] + x + 41, 3),
                     (24, 35, 19), dtype=np.uint8)
    result[y:y + resized.shape[0], x:x + resized.shape[1]] = resized
    return result


class SoulSelectionViewTests(unittest.TestCase):
    def setUp(self):
        self.view = SoulSelectionView()

    def assert_panel(self, image, scale=1., offset=(0, 0), checked=(1, 3, 5, 13)):
        panel = self.view.find_panel(image)
        self.assertIsNotNone(panel)
        self.assertAlmostEqual(panel.scale, scale, delta=.008)
        self.assertAlmostEqual(panel.x, offset[0], delta=3)
        self.assertAlmostEqual(panel.y, offset[1], delta=3)
        self.assertEqual(self.view.selected(image, panel), frozenset(checked))
        # Verify click targets actually remain inside the corresponding icon,
        # submit button and close button after fitting the distant anchors.
        for index in (1, 5, 8, 11, 15):
            row, col = divmod(index - 1, 5)
            cx = (140, 262, 383, 505, 626)[col] * scale + offset[0]
            cy = (120, 224, 329)[row] * scale + offset[1]
            x, y, w, h = panel.cell_roi(index)
            self.assertLess(abs(x + w / 2 - cx), 4)
            self.assertLess(abs(y + h / 2 - cy), 4)
        for roi, center in ((panel.submit_roi, (663, 429)),
                            (panel.close_roi, (707.5, 56))):
            x, y, w, h = roi
            self.assertLess(abs(x + w / 2 - (offset[0] + center[0] * scale)), 4)
            self.assertLess(abs(y + h / 2 - (offset[1] + center[1] * scale)), 4)

    def test_synthetic_panel_translation_and_nonstandard_scales(self):
        original = synthetic_panel()
        for scale in (.65, .8, 1., 1.13, 1.37, 1.62, 2.):
            with self.subTest(scale=scale):
                self.assert_panel(transform(original, scale), scale, (113, 39))

    def test_empty_selection_and_disabled_submit_are_locatable(self):
        image = synthetic_panel(checked=())
        # Desaturating/dimming the button simulates its disabled appearance;
        # the templates contain text, not the four occupied selection slots.
        button = image[396:462, 636:697]
        gray = cv2.cvtColor(button, cv2.COLOR_RGB2GRAY)
        button[:] = (np.repeat(gray[:, :, None], 3, axis=2) * .55).astype(np.uint8)
        self.assert_panel(image, checked=())

    def test_both_anchors_and_their_geometry_are_required(self):
        for image in (synthetic_panel(title=False), synthetic_panel(submit=False),
                      np.zeros((720, 1280, 3), dtype=np.uint8)):
            self.assertIsNone(self.view.find_panel(image))
        wrong_position = synthetic_panel(submit=False)
        paste(wrong_position, "submit", 510, 385)
        self.assertIsNone(self.view.find_panel(wrong_position))

    def test_cropped_dialog_and_invalid_input_fail_safely(self):
        self.assertIsNone(self.view.find_panel(synthetic_panel()[:, :690]))
        for value in (None, np.zeros((493, 790), np.uint8),
                      np.zeros((493, 790, 3), np.float32)):
            self.assertIsNone(self.view.find_entry(value))
            self.assertIsNone(self.view.find_panel(value))
            self.assertIsNone(self.view.selected(value, Panel(0, 0, 1)))
        self.assertIsNone(self.view.selected(synthetic_panel(), Panel(-150, 0, 1)))

    def test_one_based_cell_index_validation(self):
        for value in (0, 16, -1, True, 1.5, "1"):
            with self.assertRaises(ValueError):
                Panel(0, 0, 1).cell_roi(value)

    def test_entry_click_targets_switch_arrows_after_translation(self):
        image = np.zeros((552, 983, 3), np.uint8)
        paste(image, "entry", 9, 357)
        for scale in (.8, 1., 1.3, 2.):
            with self.subTest(scale=scale):
                roi = self.view.find_entry(transform(image, scale))
                self.assertIsNotNone(roi)
                x, y, w, h = roi
                self.assertLess(abs(x + w / 2 - (113 + 173 * scale)), 5)
                self.assertLess(abs(y + h / 2 - (39 + 371 * scale)), 5)

    @unittest.skipUnless(PANEL_SCREENSHOT.exists(), "Original selector screenshot is local only")
    def test_original_screenshot_and_scaled_replays(self):
        original = read_rgb(PANEL_SCREENSHOT)
        self.assert_panel(original)
        for scale in (.65, 1.13, 1.62, 2.):
            with self.subTest(scale=scale):
                self.assert_panel(transform(original, scale), scale, (113, 39))

    @unittest.skipUnless(ENTRY_SCREENSHOT.exists(), "Original entry screenshot is local only")
    def test_original_entry_screenshot_has_no_selector_panel(self):
        original = read_rgb(ENTRY_SCREENSHOT)
        self.assertIsNone(self.view.find_panel(original))
        x, y, w, h = self.view.find_entry(original)
        self.assertLess(abs(x + w / 2 - 173), 4)
        self.assertLess(abs(y + h / 2 - 371), 4)

    @unittest.skipUnless(os.environ.get('SOUL_SELECTION_ERROR_IMAGE'),
                         'Local interrupted-climb screenshot not supplied')
    def test_interrupted_climb_error_has_an_empty_recoverable_selector(self):
        image = read_rgb(os.environ['SOUL_SELECTION_ERROR_IMAGE'])
        panel = self.view.find_panel(image)
        self.assertIsNotNone(panel)
        self.assertEqual(self.view.selected(image, panel), frozenset())
        # The recorded error has a visible exit at (1075, 118), not the
        # underlying mode switch at (1245, 558) which caused repeated clicks.
        x, y, w, h = panel.close_roi
        self.assertLess(abs(x + w / 2 - 1075), 8)
        self.assertLess(abs(y + h / 2 - 118), 8)


if __name__ == "__main__":
    unittest.main()
