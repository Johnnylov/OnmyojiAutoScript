"""Mandatory redacted screenshot replays for shared-shop purchase regressions."""

from pathlib import Path
from unittest.mock import patch
import unittest

import cv2
import numpy as np

from dev_tools.test_mystery_shop_purchase import World, LimitedTimer
from tasks.MysteryShop.assets import MysteryShopAssets as Assets
from tasks.MysteryShop import purchase
from tasks.MysteryShop.purchase_view import (
    item_availability, bond_required, purchase_dialog_present, shelf_controls_enabled,
)


FIXTURES = Path(__file__).with_name('fixtures') / 'mystery_shop'


def rgb(name):
    image = cv2.imread(str(FIXTURES / (name + '.png')))
    if image is None:
        raise AssertionError('Required retained MysteryShop fixture is missing: ' + name)
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


class PurchaseViewTests(unittest.TestCase):
    def test_locked_shared_daruma_and_taiko_are_rejected_but_enabled_taiko_is_available(self):
        image = rgb('locked_shared_shelf')
        self.assertEqual(item_availability(image, Assets.I_MS_BLACK), 'disabled')
        self.assertEqual(item_availability(image, Assets.I_MS_TAIKO_4), 'disabled')
        self.assertEqual(item_availability(image, Assets.I_MS_TAIKO_3), 'available')
        self.assertEqual(item_availability(image, Assets.I_MS_BLUE), 'missing')
        self.assertTrue(bond_required(image))
        self.assertFalse(purchase_dialog_present(image))
        self.assertTrue(shelf_controls_enabled(image))

    def test_disabled_item_is_rejected_before_any_click_even_without_the_toast(self):
        image = rgb('locked_shared_shelf')
        image[220:258] = 0
        self.assertFalse(bond_required(image))
        self.assertFalse(purchase_dialog_present(image))
        world = World()
        world.device.image = image
        with patch.object(purchase, 'Timer', LimitedTimer):
            self.assertFalse(purchase.buy_shop_one(world, Assets.I_MS_BLACK, Assets.I_MS_CHECK_BLACK))
        self.assertEqual(world.clicks, [])

    def test_known_bond_toast_stops_opening_without_repeated_clicks(self):
        world = World()
        world.device.image = rgb('locked_shared_shelf')
        with patch.object(purchase, 'Timer', LimitedTimer), \
                patch.object(purchase, 'item_availability', return_value='available'):
            self.assertFalse(purchase.buy_shop_one(world, Assets.I_MS_BLACK, Assets.I_MS_CHECK_BLACK))
        self.assertEqual(world.clicks, [])

    def test_gray_overlay_and_invalid_images_do_not_allow_purchase(self):
        for image in (None, np.zeros((720, 1280, 3), dtype=np.uint8),
                      np.zeros((720, 1280), dtype=np.uint8), np.zeros((720, 1280, 3), dtype=float)):
            self.assertNotEqual(item_availability(image, Assets.I_MS_BLACK), 'available')
            self.assertFalse(bond_required(image))
        dim = (rgb('locked_shared_shelf') * .5).astype(np.uint8)
        self.assertEqual(item_availability(dim, Assets.I_MS_TAIKO_3), 'disabled')

    def test_true_blue_quantity_dialog_matches_at_current_icon_scale(self):
        image = rgb('blue_quantity_dialog')
        rule = purchase.purchase_item_rule(Assets.I_MS_CHECK_BLUE)
        self.assertTrue(rule.multi_scale_template_match(image))
        self.assertEqual(tuple(rule.roi_front), (595, 196, 90, 96))
        self.assertFalse(bond_required(image))
        self.assertTrue(purchase_dialog_present(image))
        self.assertFalse(shelf_controls_enabled(image))
        for other in (Assets.I_MS_CHECK_BLACK, Assets.I_MS_CHECK_TAIKO_3, Assets.I_MS_CHECK_TAIKO_4):
            self.assertFalse(purchase.purchase_item_rule(other).multi_scale_template_match(image))

    def test_popup_match_never_uses_the_surrounding_shelf(self):
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        template = cv2.cvtColor(cv2.imread(Assets.I_MS_CHECK_BLUE.file), cv2.COLOR_BGR2RGB)
        h, w = template.shape[:2]
        image[400:400+h, 860:860+w] = template
        self.assertFalse(purchase.purchase_item_rule(Assets.I_MS_CHECK_BLUE).multi_scale_template_match(image))

    def test_central_shelf_icon_cannot_become_a_purchase_dialog(self):
        image = rgb('locked_shared_shelf')
        template = cv2.cvtColor(cv2.imread(Assets.I_MS_CHECK_BLUE.file), cv2.COLOR_BGR2RGB)
        h, w = template.shape[:2]
        image[196:196+h, 595:595+w] = template
        # A central image match on its own really is insufficient.
        self.assertTrue(purchase.purchase_item_rule(Assets.I_MS_CHECK_BLUE).multi_scale_template_match(image))
        self.assertFalse(purchase_dialog_present(image))
        world = World()
        world.device.image = image
        world.appear = lambda marker, **kwargs: marker.name == Assets.I_MS_CHECK_BLUE.name
        with patch.object(purchase, 'Timer', LimitedTimer):
            self.assertFalse(purchase.buy_shop_one(world, Assets.I_MS_BLUE, Assets.I_MS_CHECK_BLUE))
        self.assertEqual(world.clicks, [])

    def test_background_arrows_alone_do_not_acknowledge_a_dialog_or_dark_overlay(self):
        world = World()
        world._shop_page_visible = lambda: True
        for image in (rgb('blue_quantity_dialog'), (rgb('locked_shared_shelf') * .5).astype(np.uint8)):
            world.device.image = image
            self.assertFalse(purchase._shelf_visible(world))

    def test_actual_blue_dialog_runs_through_quantity_one_purchase(self):
        world = World(count=1)
        original = world.appear
        image = rgb('blue_quantity_dialog')

        def appear(marker, **kwargs):
            if marker.name == Assets.I_MS_CHECK_BLUE.name and world.stage == 'panel':
                return marker.multi_scale_template_match(image)
            return original(marker, **kwargs)

        world.appear = appear
        self.assertTrue(world.run())
        self.assertEqual(world.clicks, ['MS_MS_BLUE', 'ms_buy_quantity_one'])


if __name__ == '__main__':
    unittest.main()
