"""Offline purchase regression: every click and OCR response is mocked."""

from pathlib import Path
from types import SimpleNamespace
import os
import sys
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tasks.MysteryShop import purchase
from tasks.MysteryShop.assets import MysteryShopAssets
from tasks.Component.Buy.assets import BuyAssets
from tasks.GlobalGame.assets import GlobalGameAssets
from module.exception import GameStuckError


class LimitedTimer:
    def __init__(self, seconds):
        self.remaining = 12
    def start(self):
        return self
    def reached(self):
        self.remaining -= 1
        return self.remaining < 0


class World:
    def __init__(self, count=3, legacy=False, rmb=False, outcome=True):
        self.count, self.legacy, self.rmb, self.outcome = count, legacy, rmb, outcome
        self.stage = 'shelf'
        self.device = SimpleNamespace(image=None)
        self.clicks = []
        self.screenshot = Mock()
        for name in ['I_BUY_RMB', 'I_BUY_SUCCESS', 'I_BUY_PLUS', 'I_BUY_SUB',
                     'C_BUY_CANCEL', 'C_BUY_ONE']:
            setattr(self, name, getattr(BuyAssets, name))
        self.I_UI_REWARD = GlobalGameAssets.I_UI_REWARD

    def appear(self, marker, **kwargs):
        if marker.name == MysteryShopAssets.I_MS_CHECK_BLUE.name:
            return self.stage == 'panel'
        if marker == self.I_BUY_RMB:
            return self.stage == 'panel' and self.rmb
        if marker in (self.I_BUY_PLUS, self.I_BUY_SUB):
            return self.stage == 'panel' and not self.legacy
        return False

    def appear_then_click(self, marker, **kwargs):
        if marker == MysteryShopAssets.I_MS_BLUE:
            self.stage = 'panel'
        elif marker == self.I_BUY_SUB:
            self.count -= 1
        else:
            raise AssertionError('Unexpected product or quantity action')
        self.clicks.append(marker.name)
        return True

    def click(self, marker, **kwargs):
        self.clicks.append(marker.name)
        if marker == self.C_BUY_CANCEL:
            self.stage = 'shelf'
        elif marker in (purchase.CONFIRM_QUANTITY, self.C_BUY_ONE):
            if not self.legacy:
                assert self.count == 1
            self.stage = 'panel' if self.outcome == 'stuck' else ('reward' if self.outcome else 'unknown')
        else:
            raise AssertionError('Unexpected confirm')
        return True

    def ui_reward_appear_click(self):
        if self.stage == 'reward':
            self.stage = 'shelf'
            return True
        return False

    def _shop_page_visible(self):
        return self.stage == 'shelf'

    def run(self):
        with patch.object(purchase, 'Timer', LimitedTimer), \
                patch.object(purchase, 'item_availability', return_value='available'), \
                patch.object(purchase, 'bond_required', return_value=False), \
                patch.object(purchase, 'purchase_dialog_present', side_effect=lambda _: self.stage == 'panel'), \
                patch.object(purchase, 'shelf_controls_enabled', side_effect=lambda _: self.stage == 'shelf'), \
                patch.object(purchase.QUANTITY, 'ocr', side_effect=lambda _: self.count):
            return purchase.buy_shop_one(self, MysteryShopAssets.I_MS_BLUE, MysteryShopAssets.I_MS_CHECK_BLUE)


class PurchaseTests(unittest.TestCase):
    def test_new_dialog_reduces_three_to_one_and_uses_lower_button(self):
        world = World()
        self.assertTrue(world.run())
        self.assertEqual(world.clicks, ['MS_MS_BLUE', 'BUY_BUY_SUB', 'BUY_BUY_SUB', 'ms_buy_quantity_one'])

    def test_already_one_never_clicks_add_or_max(self):
        world = World(count=1)
        self.assertTrue(world.run())
        self.assertEqual(world.clicks, ['MS_MS_BLUE', 'ms_buy_quantity_one'])

    def test_old_dialog_retains_old_confirm_position(self):
        world = World(legacy=True)
        self.assertTrue(world.run())
        self.assertEqual(world.clicks, ['MS_MS_BLUE', 'buy_one'])

    def test_paid_currency_is_cancelled_without_confirm(self):
        world = World(rmb=True)
        self.assertFalse(world.run())
        self.assertEqual(world.clicks, ['MS_MS_BLUE', 'buy_cancel'])

    def test_invalid_quantity_never_confirms(self):
        for value in [None, 0, -1, '1', True]:
            world = World(count=value)
            with self.assertRaises(GameStuckError):
                world.run()
            self.assertEqual(world.clicks, ['MS_MS_BLUE'])

    def test_missing_ack_does_not_reopen_product_or_click_background(self):
        world = World(count=1, outcome=False)
        with self.assertRaises(GameStuckError):
            world.run()
        self.assertEqual(world.clicks, ['MS_MS_BLUE', 'ms_buy_quantity_one'])

    def test_unchanged_purchase_dialog_never_receives_a_second_submit(self):
        for legacy in (False, True):
            world = World(count=1, legacy=legacy, outcome='stuck')
            with self.assertRaises(GameStuckError):
                world.run()
            self.assertEqual(world.clicks, ['MS_MS_BLUE', 'buy_one' if legacy else 'ms_buy_quantity_one'])

    def test_unresponsive_shelf_gets_only_one_click_and_is_skipped(self):
        world = World()
        world.appear_then_click = lambda marker, **kwargs: world.clicks.append(marker.name) or True
        self.assertFalse(world.run())
        self.assertEqual(world.clicks, ['MS_MS_BLUE'])

    def test_quantity_minus_requires_verified_one_step_change_before_next_click(self):
        world = World(count=3)
        original = world.appear_then_click

        def click(marker, **kwargs):
            if marker == world.I_BUY_SUB:
                world.clicks.append(marker.name)
                return True
            return original(marker, **kwargs)

        world.appear_then_click = click
        with self.assertRaises(GameStuckError):
            world.run()
        self.assertEqual(world.clicks, ['MS_MS_BLUE', 'BUY_BUY_SUB'])

    def test_paid_dialog_cancellation_must_be_confirmed_before_scanning_resumes(self):
        world = World(rmb=True)
        world.click = lambda marker, **kwargs: world.clicks.append(marker.name) or True
        with self.assertRaises(GameStuckError):
            world.run()
        self.assertEqual(world.clicks, ['MS_MS_BLUE', 'buy_cancel'])

    def test_a_stale_success_toast_does_not_confirm_a_new_purchase(self):
        world = World(count=1)
        original = world.appear
        world.appear = lambda marker, **kwargs: (world.stage == 'panel' if marker == world.I_BUY_SUCCESS
                                                 else original(marker, **kwargs))
        with self.assertRaises(GameStuckError):
            world.run()
        self.assertEqual(world.clicks, ['MS_MS_BLUE'])

    def test_wrong_product_dialog_does_not_click_underlying_shelf(self):
        world = World()
        world.stage = 'panel'
        original = world.appear
        world.appear = lambda marker, **kwargs: False if marker.name == MysteryShopAssets.I_MS_CHECK_BLUE.name else original(marker, **kwargs)
        with self.assertRaises(GameStuckError):
            world.run()
        self.assertEqual(world.clicks, [])

    def test_shared_assets_are_unchanged(self):
        original = tuple(MysteryShopAssets.I_MS_CHECK_BLUE.roi_back)
        self.assertTrue(World().run())
        self.assertEqual(tuple(MysteryShopAssets.I_MS_CHECK_BLUE.roi_back), original)
        self.assertEqual(tuple(BuyAssets.C_BUY_ONE.roi_front), (551,506,174,36))

    @unittest.skipUnless(os.environ.get('MYSTERY_PURCHASE_IMAGE'), 'No local purchase screenshot')
    def test_user_screenshot_item_controls_and_button_roi(self):
        path = os.environ['MYSTERY_PURCHASE_IMAGE']
        image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), 1)
        image = cv2.resize(image, (1280, 720))
        for marker, roi in [(MysteryShopAssets.I_MS_CHECK_BLUE, (455,175,280,210)),
                            (BuyAssets.I_BUY_PLUS, BuyAssets.I_BUY_PLUS.roi_back),
                            (BuyAssets.I_BUY_SUB, BuyAssets.I_BUY_SUB.roi_back)]:
            x,y,w,h = roi
            template = cv2.imdecode(np.fromfile(marker.file, dtype=np.uint8), 1)
            score = cv2.minMaxLoc(cv2.matchTemplate(image[y:y+h,x:x+w], template, cv2.TM_CCOEFF_NORMED))[1]
            self.assertGreater(score, 0.8)
        # Button interior in the supplied 831x468 screenshot is x=365..470,y=352..384.
        x,y,w,h = purchase.CONFIRM_QUANTITY.roi_front
        self.assertGreater(x*831/1280, 365)
        self.assertLess((x+w)*831/1280, 470)
        self.assertGreater(y*468/720, 352)
        self.assertLess((y+h)*468/720, 384)


if __name__ == '__main__':
    unittest.main()
