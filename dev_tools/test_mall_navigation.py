"""Offline mall recognition/navigation regressions; never connect to the game.

Set MALL_REPLAY_IMAGES to local screenshots separated by os.pathsep for replay.
Private account screenshots are not stored in the repository.
"""

from itertools import combinations
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tasks.GameUi.assets import GameUiAssets
from tasks.GameUi.mall import is_mall_page
from tasks.GameUi.default_pages import page_mall, page_mall_recommend
from tasks.GameUi.navigator import GameUi
from tasks.RichMan.assets import RichManAssets
from tasks.RichMan.mall.navbar import MallNavbar
from tasks.MysteryShop.assets import MysteryShopAssets


ICONS = (RichManAssets.I_MALL_CONSIGNMENT, RichManAssets.I_MALL_SCCALES,
         RichManAssets.I_MALL_SUNDRY)


class Frames:
    def __init__(self, image):
        self.device = SimpleNamespace(image=image)
        self.scores = {}

    def appear(self, marker, threshold=None):
        image = self.device.image
        template = cv2.imdecode(np.fromfile(ROOT / marker.file, dtype=np.uint8), cv2.IMREAD_COLOR)
        x, y, w, h = marker.roi_back
        roi = image[y:y+h, x:x+w]
        if roi.shape[0] < template.shape[0] or roi.shape[1] < template.shape[1]:
            return False
        score = float(cv2.minMaxLoc(cv2.matchTemplate(roi, template, cv2.TM_CCOEFF_NORMED))[1])
        self.scores[marker.name] = score
        return score >= (marker.threshold if threshold is None else threshold)


def blank():
    return np.zeros((720, 1280, 3), dtype=np.uint8)


def paste(image, marker):
    template = cv2.imdecode(np.fromfile(ROOT / marker.file, dtype=np.uint8), cv2.IMREAD_COLOR)
    x, y = marker.roi_back[:2]
    image[y:y+template.shape[0], x:x+template.shape[1]] = template


class MallRecognitionTests(unittest.TestCase):
    def test_any_two_distinct_bottom_icons_are_required(self):
        for icons in combinations(ICONS, 2):
            image = blank()
            for icon in icons:
                paste(image, icon)
            self.assertTrue(is_mall_page(Frames(image)))

    def test_one_bottom_icon_or_unknown_page_is_not_mall(self):
        self.assertFalse(is_mall_page(Frames(blank())))
        for icon in ICONS:
            image = blank()
            paste(image, icon)
            self.assertFalse(is_mall_page(Frames(image)))

    def test_legacy_roof_match_is_preserved(self):
        image = blank()
        paste(image, GameUiAssets.I_CHECK_MALL)
        self.assertTrue(is_mall_page(Frames(image)))

    def test_recommend_page_takes_precedence_over_background(self):
        image = blank()
        for icon in (*ICONS, GameUiAssets.I_CHECK_MALL, GameUiAssets.I_CHECK_MALL_RECOMMEND):
            paste(image, icon)
        self.assertFalse(is_mall_page(Frames(image)))

    def test_portrait_and_missing_frames_never_match(self):
        for image in [None, np.zeros((1280, 720, 3)), np.zeros((720, 1280))]:
            task = SimpleNamespace(device=SimpleNamespace(image=image), appear=Mock())
            self.assertFalse(is_mall_page(task))
            task.appear.assert_not_called()

    def test_fallback_uses_high_threshold_without_mutating_assets(self):
        seen = []
        def appear(marker, threshold=None):
            seen.append((marker, threshold))
            return threshold == 0.9
        task = SimpleNamespace(device=SimpleNamespace(image=blank()), appear=appear)
        self.assertTrue(is_mall_page(task))
        self.assertEqual([threshold for marker, threshold in seen if marker in ICONS], [0.9]*3)
        self.assertTrue(all(marker.threshold == 0.8 for marker in ICONS))

    def test_registered_page_uses_shared_recognition_and_keeps_its_key(self):
        image = blank()
        for icon in ICONS:
            paste(image, icon)
        self.assertEqual(page_mall.key, 'page_mall')
        self.assertTrue(page_mall.recognizer.evaluate(Frames(image)))
        self.assertFalse(page_mall_recommend.recognizer.evaluate(Frames(image)))

    def test_return_from_mystery_shop_uses_page_navigation(self):
        task = SimpleNamespace(goto_page=Mock(), ui_click=Mock())
        MallNavbar.back_mall(task)
        task.goto_page.assert_called_once_with(page_mall)
        task.ui_click.assert_not_called()

    def test_arrival_on_new_layout_does_not_click_back_again(self):
        image = blank()
        for icon in ICONS:
            paste(image, icon)
        self.assert_arrival_without_clicks(image)

    def test_recommend_exit_reaches_street_with_one_back_action(self):
        image = blank()
        for icon in ICONS:
            paste(image, icon)
        task = Frames(image)
        transition = next(edge for edge in page_mall_recommend.transitions
                          if edge.destination == page_mall)
        task.navigator = SimpleNamespace(current_page=page_mall_recommend)
        task._execute_action = Mock(return_value=True)
        task.confirm_page = lambda page, **kwargs: page.recognizer.evaluate(task)
        task._wait_for_destination = lambda page: GameUi._wait_for_destination(task, page)
        task._run_hooks = Mock()
        task._mark_page_entered = Mock()
        self.assertTrue(GameUi._execute_transition(task, transition))
        task._execute_action.assert_called_once_with(
            transition.action, interval=0.8, skip_first_screenshot=False)
        self.assertEqual(task.navigator.current_page, page_mall)

    def assert_arrival_without_clicks(self, image):
        task = Frames(image)
        task.navigator = SimpleNamespace(resolve_page=lambda page: page)
        task._refresh_current_page = lambda page, skip: page if page.recognizer.evaluate(task) else None
        task._run_enter_success_hooks_if_needed = Mock()
        task._finalize_arrival = Mock(return_value=True)
        task.close_unknown_pages = Mock(side_effect=AssertionError('must not click back from mall'))
        task._build_path = Mock(side_effect=AssertionError('already at destination'))
        self.assertTrue(GameUi.goto_page(task, page_mall))
        task.close_unknown_pages.assert_not_called()
        task._build_path.assert_not_called()

    @unittest.skipUnless(os.environ.get('MALL_REPLAY_IMAGES'), 'No local error screenshots supplied')
    def test_real_error_frame_is_mall_and_entry_remains_visible(self):
        for name in os.environ['MALL_REPLAY_IMAGES'].split(os.pathsep):
            with self.subTest(image=name):
                image = cv2.imdecode(np.fromfile(name, dtype=np.uint8), cv2.IMREAD_COLOR)
                self.assertIsNotNone(image)
                task = Frames(image)
                self.assertTrue(is_mall_page(task))
                self.assertTrue(task.appear(MysteryShopAssets.I_ME_ENTER))
                self.assertTrue(page_mall.recognizer.evaluate(task))
                self.assert_arrival_without_clicks(image)


if __name__ == '__main__':
    unittest.main()
