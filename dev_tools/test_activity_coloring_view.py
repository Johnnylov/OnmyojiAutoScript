"""Coloring template geometry, negative controls and private local screenshot replays."""

from pathlib import Path
import os
import tempfile
import unittest

import cv2
import numpy as np

from tasks.ActivityShikigami.coloring_view import ColoringView, PaintingPage, ASSETS, ANCHORS, _template


TEMP = Path(os.environ.get('ACTIVITY_REFERENCE_DIR', tempfile.gettempdir()))
OVERVIEW = TEMP / 'codex-clipboard-a2fe886d-71a0-4747-998c-e4e6d53b1e45.png'
PANEL = TEMP / 'codex-clipboard-dc69021d-2481-42a4-bda6-bba98c26a532.png'
MAP = TEMP / 'codex-clipboard-95beef8e-89c2-4714-b256-9be8932833dc.png'
INTRO_FILE = '2026-09-14_17-10-57-470164.png'
INTRO = Path(__file__).resolve().parents[1] / 'log/error/oas2_1789377058127' / INTRO_FILE
FIXTURES = Path(__file__).with_name('fixtures') / 'activity_coloring'


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


def synthetic_intro(confirm=False, omit=()):
    """Only shared chrome/cropped anchors; no full game capture is committed."""
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    for name in ('intro_logo', 'intro_speaker', 'intro_skip'):
        if name in omit:
            continue
        patch = cv2.cvtColor(_template(name), cv2.COLOR_GRAY2RGB)
        x, y = ANCHORS[name]
        image[y:y+patch.shape[0], x:x+patch.shape[1]] = patch
    if confirm:
        image = (image * .5).astype(np.uint8)
        patch = cv2.cvtColor(_template('intro_confirm'), cv2.COLOR_GRAY2RGB)
        x, y = ANCHORS['intro_confirm']
        image[y:y+patch.shape[0], x:x+patch.shape[1]] = patch
    return image


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
    def test_retained_real_reward_fixtures_expose_only_safe_margin_at_multiple_sizes(self):
        for name in ('reward_coin', 'reward_daruma'):
            source = rgb(FIXTURES / f'{name}.png')
            for size in ((1280, 720), (956, 538), (840, 473)):
                with self.subTest(fixture=name, size=size):
                    image = cv2.resize(source, size)
                    view = ColoringView()
                    reward = view.find_reward(image)
                    self.assertIsNotNone(reward)
                    self.assertIsNone(view.find_page(image))
                    self.assertIsNone(view.find_map_entry(image))
                    self.assertIsNone(view.find_intro(image))
                    x, y, w, h = reward.dismiss_roi
                    self.assertGreaterEqual(x, 0)
                    self.assertLess(x+w, size[0] * .09)
                    self.assertGreater(y, size[1] * .3)
                    self.assertLess(y+h, size[1] * .6)
            moved = transformed(source, .9)
            reward = ColoringView().find_reward(moved)
            self.assertIsNotNone(reward)
            self.assertGreaterEqual(reward.dismiss_roi[0], 53)

    def test_reward_needs_title_frame_and_painting_context_not_any_dim_modal(self):
        original = rgb(FIXTURES / 'reward_coin.png')
        # Remove the exact template rectangles, or a distinct background anchor.
        rectangles = [(481, 234, 317, 42), (312, 421, 88, 78),
                      (130, 0, 225, 76), (135, 627, 123, 74), (0, 0, 88, 76)]
        for roi in rectangles:
            with self.subTest(missing=roi):
                image = original.copy()
                x, y, w, h = roi
                image[y:y+h, x:x+w] = 0
                self.assertIsNone(ColoringView().find_reward(image))
        image = original.copy()
        image[175:506, 280:1000] = (image[175:506, 280:1000] * .5).astype(np.uint8)
        self.assertIsNone(ColoringView().find_reward(image))
        for panel in (False, True):
            self.assertIsNone(ColoringView().find_reward(synthetic(panel)))
        self.assertIsNone(ColoringView().find_reward(synthetic_intro()))

    def test_intro_and_contextual_skip_confirmation_follow_scale_and_translation(self):
        for confirm in (False, True):
            for scale in (.75, 1., 1.25):
                with self.subTest(confirm=confirm, scale=scale):
                    image = transformed(synthetic_intro(confirm), scale)
                    intro = ColoringView().find_intro(image)
                    self.assertIsNotNone(intro)
                    self.assertEqual(intro.kind, 'confirm' if confirm else 'skip')
                    name = 'intro_confirm' if confirm else 'intro_skip'
                    rx, ry = ANCHORS[name]
                    h, w = _template(name).shape
                    x, y, rw, rh = intro.action_roi
                    self.assertGreaterEqual(x, 53 + rx*scale - 2)
                    self.assertGreaterEqual(y, 29 + ry*scale - 2)
                    self.assertLessEqual(x+rw, 53+(rx+w)*scale + 2)
                    self.assertLessEqual(y+rh, 29+(ry+h)*scale + 2)

    def test_intro_requires_specific_guide_anchors_and_an_undimmed_action(self):
        for name in ('intro_logo', 'intro_speaker', 'intro_skip'):
            self.assertIsNone(ColoringView().find_intro(synthetic_intro(omit=(name,))))
        for name in ('intro_logo', 'intro_speaker'):
            self.assertIsNone(ColoringView().find_intro(synthetic_intro(True, omit=(name,))))
        for factor in (.5, .7):
            self.assertIsNone(ColoringView().find_intro((synthetic_intro()*factor).astype(np.uint8)))
        self.assertIsNone(ColoringView().find_intro((synthetic_intro(True)*.5).astype(np.uint8)))
        image = synthetic_intro(omit=('intro_skip',))
        patch = cv2.cvtColor(_template('intro_skip'), cv2.COLOR_GRAY2RGB)
        image[37:37+patch.shape[0], 950:950+patch.shape[1]] = patch
        self.assertIsNone(ColoringView().find_intro(image))

    def test_first_entry_story_real_capture_and_normal_pages_are_disjoint(self):
        path = INTRO if INTRO.exists() else TEMP / INTRO_FILE
        if not path.exists():
            self.skipTest('Private first-entry story screenshot is unavailable')
        image = rgb(path)
        for size in ((1280, 720), (956, 538), (840, 473)):
            with self.subTest(size=size):
                frame = cv2.resize(image, size)
                view = ColoringView()
                intro = view.find_intro(frame)
                self.assertIsNotNone(intro)
                self.assertEqual(intro.kind, 'skip')
                self.assertIsNone(view.find_page(frame))
                self.assertIsNone(view.find_map_entry(frame))
        for path in (OVERVIEW, PANEL, MAP):
            if path.exists():
                self.assertIsNone(ColoringView().find_intro(rgb(path)))

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
