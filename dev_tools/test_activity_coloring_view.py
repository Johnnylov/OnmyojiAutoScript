"""Coloring template geometry, negative controls and private local screenshot replays."""

from pathlib import Path
import os
import tempfile
import unittest

import cv2
import numpy as np

from tasks.ActivityShikigami.coloring_view import ColoringView, PaintingPage, ASSETS, ANCHORS


TEMP = Path(os.environ.get('ACTIVITY_REFERENCE_DIR', tempfile.gettempdir()))
OVERVIEW = TEMP / 'codex-clipboard-a2fe886d-71a0-4747-998c-e4e6d53b1e45.png'
PANEL = TEMP / 'codex-clipboard-dc69021d-2481-42a4-bda6-bba98c26a532.png'
MAP = TEMP / 'codex-clipboard-95beef8e-89c2-4714-b256-9be8932833dc.png'


def rgb(path):
    return cv2.cvtColor(cv2.imread(str(path)), cv2.COLOR_BGR2RGB)


def synthetic(panel=False, omit=()):
    image = np.zeros((538, 956, 3), dtype=np.uint8)
    names = ['title', 'global_label', 'back'] + (['max', 'submit', 'collapse', 'next'] if panel else ['start'])
    for name in names:
        if name in omit:
            continue
        patch = rgb(ASSETS / f'{name}.png')
        x, y = ANCHORS[name]
        image[y:y+patch.shape[0], x:x+patch.shape[1]] = patch
    return image


def transformed(image, scale, offset=(53, 29)):
    patch = cv2.resize(image, None, fx=scale, fy=scale)
    x, y = offset
    result = np.zeros((patch.shape[0] + y + 40, patch.shape[1] + x + 40, 3), np.uint8)
    result[y:y+patch.shape[0], x:x+patch.shape[1]] = patch
    return result


def five_digit_counter(kind, scale=1.):
    """Independent five-digit placements in the visible control bands."""
    image = np.zeros((538, 956, 3), dtype=np.uint8)
    text = '24095'
    (width, _), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, .5, 1)
    if kind == 'amount':
        origin = (round(789 - width / 2), 474)
        color = (218, 48, 54)
    else:
        origin = ((563 if kind == 'panel' else 899) - width, 35)
        color = (215, 212, 196)
    cv2.putText(image, text, origin, cv2.FONT_HERSHEY_SIMPLEX, .5, color, 1, cv2.LINE_AA)
    image = cv2.resize(image, None, fx=scale, fy=scale)
    page = PaintingPage(0, 0, scale, panel=kind != 'overview')
    roi = page.amount_roi if kind == 'amount' else page.currency_roi
    return image, roi


class ViewTests(unittest.TestCase):
    def test_layouts_fit_translated_and_scaled_screens(self):
        for panel in (False, True):
            for scale in (.8, 1., 1.13, 1280/956, 1.6):
                with self.subTest(panel=panel, scale=scale):
                    page = ColoringView().find_page(transformed(synthetic(panel), scale))
                    self.assertIsNotNone(page)
                    self.assertEqual(page.panel, panel)
                    self.assertEqual(page.next_available, panel)
                    self.assertAlmostEqual(page.scale, scale, delta=.009)
                    self.assertAlmostEqual(page.x, 53, delta=4)
                    self.assertAlmostEqual(page.y, 29, delta=4)
                    x, y, w, h = page.global_progress_roi
                    self.assertGreater(y, 450 * scale)
                    # These ROIs exclude the top currency-purchase plus button.
                    cx, cy, cw, ch = page.currency_roi
                    mx, my, mw, mh = page.max_roi
                    self.assertGreater(my, 400 * scale)
                    self.assertLess(cx+cw, 53 + (580 if panel else 915) * scale)

    def test_incomplete_panels_unknown_pages_and_confirmation_dim_are_rejected(self):
        for image in (synthetic(omit=('global_label',)), synthetic(True, omit=('max',)),
                      synthetic(True, omit=('submit',)), synthetic(True, omit=('collapse',)),
                      (synthetic(True) * .5).astype(np.uint8), np.zeros((720, 1280, 3), np.uint8)):
            self.assertIsNone(ColoringView().find_page(image))

    def test_wrong_geometry_does_not_accept_unrelated_title_and_controls(self):
        image = synthetic(True, omit=('global_label',))
        patch = rgb(ASSETS / 'global_label.png')
        image[400:400+patch.shape[0], 500:500+patch.shape[1]] = patch
        self.assertIsNone(ColoringView().find_page(image))

    def test_invalid_images_are_rejected(self):
        view = ColoringView()
        for image in (None, np.zeros((538, 956), np.uint8), np.zeros((538, 956, 3), float)):
            self.assertIsNone(view.find_page(image))
            self.assertIsNone(view.find_map_entry(image))

    def test_five_digit_counters_fit_fully_and_exclude_neighbor_controls(self):
        for kind in ('overview', 'panel', 'amount'):
            for scale in (1., 1280/956):
                with self.subTest(kind=kind, scale=scale):
                    image, roi = five_digit_counter(kind, scale)
                    rows, cols = np.where(image.max(axis=2) > 125)
                    x, y, w, h = roi
                    self.assertLessEqual(x, cols.min())
                    self.assertGreater(x+w, cols.max())
                    self.assertLessEqual(y, rows.min())
                    self.assertGreater(y+h, rows.max())
                    if kind == 'amount':
                        self.assertGreaterEqual(x, 759*scale)
                        self.assertLessEqual(x+w, 819*scale)
                    else:
                        self.assertLessEqual(x+w, (574 if kind == 'panel' else 911)*scale)
                    self.assertIsNotNone(ColoringView.prepare_counter(image, roi, kind == 'amount'))

    def test_character_progress_keeps_full_completion_text_and_excludes_rank_icon(self):
        for text in ('100%', '100.00%'):
            for scale in (1., 1280/956):
                with self.subTest(text=text, scale=scale):
                    image = np.zeros((538, 956, 3), dtype=np.uint8)
                    (width, _), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, .4, 1)
                    cv2.putText(image, text, (867-width, 41), cv2.FONT_HERSHEY_SIMPLEX,
                                .4, (220, 220, 220), 1, cv2.LINE_AA)
                    image = cv2.resize(image, None, fx=scale, fy=scale)
                    rows, cols = np.where(image.max(axis=2) > 125)
                    x, y, w, h = PaintingPage(0, 0, scale, panel=True).character_progress_roi
                    self.assertLessEqual(x, cols.min())
                    self.assertGreater(x+w, cols.max())
                    self.assertLessEqual(y, rows.min())
                    self.assertGreater(y+h, rows.max())
                    self.assertLessEqual(x+w, round(869*scale))

    @unittest.skipUnless(os.environ.get('ACTIVITY_COLORING_OCR_REPLAY'), 'Optional local OCR replay disabled')
    def test_five_digit_counter_preprocessing_preserves_all_digits_for_real_ocr(self):
        from module.ocr.ppocr import TextSystem
        ocr = TextSystem()
        for kind in ('overview', 'panel', 'amount'):
            for scale in (1., 1280/956):
                with self.subTest(kind=kind, scale=scale):
                    image, roi = five_digit_counter(kind, scale)
                    prepared = ColoringView.prepare_counter(image, roi, kind == 'amount')
                    text, score = ocr.ocr_single_line(prepared)
                    self.assertEqual(text, '24095')
                    self.assertGreaterEqual(score, .6)

    @unittest.skipUnless(os.environ.get('ACTIVITY_COLORING_OCR_REPLAY') and PANEL.exists(),
                         'Optional local character OCR replay disabled')
    def test_real_rule_ocr_reads_character_name_and_progress_at_both_sizes(self):
        from module.atom.ocr import RuleOcr
        from module.ocr.ppocr import TextSystem
        model = TextSystem()
        original = rgb(PANEL)
        for image in (original, cv2.resize(original, (1280, 720))):
            page = ColoringView().find_page(image)
            self.assertIsNotNone(page)
            for name, roi, expected in (
                    ('character_name', page.character_name_roi, '一目连呱'),
                    ('character_progress', page.character_progress_roi, '1.19%')):
                with self.subTest(shape=image.shape, field=name):
                    rule = RuleOcr(roi=roi, area=roi, mode='Single', method='Default',
                                   keyword='', name=name)
                    # Use the production RuleOcr processing with a local model;
                    # no running RPC service or emulator is required.
                    rule.model = model
                    self.assertEqual(rule.ocr(image), expected)

    @unittest.skipUnless(OVERVIEW.exists() and PANEL.exists() and MAP.exists(), 'Private local screenshots absent')
    def test_supplied_screenshots_keep_global_and_individual_progress_separate(self):
        for path, panel in ((OVERVIEW, False), (PANEL, True)):
            for scale in (1., 1280/956):
                image = cv2.resize(rgb(path), None, fx=scale, fy=scale)
                page = ColoringView().find_page(image)
                self.assertIsNotNone(page)
                self.assertEqual(page.panel, panel)
                x, y, w, h = page.global_progress_roi
                self.assertLess(x+w, 200*scale)
                self.assertGreater(y, 470*scale - 4)
                self.assertIsNotNone(ColoringView.prepare_counter(image, page.currency_roi))
                if panel:
                    self.assertIsNotNone(ColoringView.prepare_counter(image, page.amount_roi, True))
        view = ColoringView()
        self.assertIsNotNone(view.find_map_entry(rgb(MAP)))
        self.assertIsNone(view.find_page(rgb(MAP)))
        self.assertIsNone(view.find_map_entry(rgb(OVERVIEW)))


if __name__ == '__main__':
    unittest.main()
