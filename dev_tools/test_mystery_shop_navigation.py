"""Shop switches must prove a new shop, not just lose the previous labels."""

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import cv2
import numpy as np

from test_mystery_shop_scroll import ShopWorld, class_from_file, ROOT


class NavigationWorld(ShopWorld):
    def __init__(self, states):
        super().__init__([{}])
        self.states = states
        self.index = 0
        self.task._wait_shop_stable = Mock()
        self.task._shop_page_visible = lambda: self.state['page'] == 'shop'
        self.task.O_MS_FRIEND_NAME = SimpleNamespace(ocr=lambda image: self.state.get('name', ''))
        self.task.click = Mock()
        self.task.appear = Mock(side_effect=self.sees)
        self.task.screenshot = Mock(side_effect=self.advance)
        self.task.I_CHECK_MAIN = 'main_marker'
        self.task.I_CHECK_MALL = 'mall_marker'
        self.task.goto_page = Mock()
        self.task._enter_shop = Mock()
        self.namespace['page_mall'] = 'mall'
        self.task.share = Mock()

    @property
    def state(self):
        return self.states[self.index]

    def advance(self):
        self.now += .3
        self.index = min(self.index + 1, len(self.states)-1)
        return self.task.device.image

    def sees(self, rule, **kwargs):
        if rule == 'main_marker':
            return self.state['page'] == 'main'
        if rule == 'mall_marker':
            return self.state['page'] == 'mall'
        if self.state['page'] != 'shop':
            return False
        if rule == self.task.I_MS_SHARE:
            return self.state.get('own', False)
        if rule == self.task.I_MS_NEXT:
            return self.state.get('next', True)
        return False


def shop(name='', own=False, next=True):
    return dict(page='shop', name=name, own=own, next=next)


class MysteryShopNavigationTests(unittest.TestCase):
    def test_own_shop_missing_share_during_transition_does_not_finish_switch(self):
        world = NavigationWorld([shop(own=True), {'page': 'unknown'}, shop('Alice'), shop('Alice')])
        self.assertTrue(world.task.next_one())
        self.assertEqual(world.index, 3)
        world.task.click.assert_called_once_with(world.task.I_MS_NEXT)

    def test_friend_change_requires_nonblank_stable_name(self):
        world = NavigationWorld([shop('Alice'), shop(''), shop('Bob'), shop('B0b'), shop('Bob'), shop('Bob')])
        self.assertTrue(world.task.next_one())
        self.assertEqual(world.index, 5)
        world.task.click.assert_called_once()

    def test_courtyard_after_own_shop_is_not_a_next_friend(self):
        world = NavigationWorld([shop(own=True), {'page': 'main'}])
        with self.assertRaises(world.namespace['GameStuckError']):
            world.task.next_one()
        world.task.click.assert_called_once()
        self.assertLess(world.now, 15)

    def test_courtyard_blank_name_is_not_a_changed_friend(self):
        world = NavigationWorld([shop('Alice'), {'page': 'main'}])
        with self.assertRaises(world.namespace['GameStuckError']):
            world.task.next_one()
        world.task.click.assert_called_once()

    def test_unknown_initial_friend_never_clicks_next(self):
        world = NavigationWorld([shop('')])
        with self.assertRaises(world.namespace['GameStuckError']):
            world.task.next_one()
        world.task.click.assert_not_called()

    def test_lost_arrow_click_does_not_repeat_and_skip_a_shop(self):
        world = NavigationWorld([shop('Alice')])
        with self.assertRaises(world.namespace['GameStuckError']):
            world.task.next_one()
        world.task.click.assert_called_once()

    def test_last_shop_requires_second_frame_to_still_be_a_shop(self):
        world = NavigationWorld([shop('Alice', next=False), {'page': 'main'}])
        with self.assertRaises(world.namespace['GameStuckError']):
            world.task.next_one()
        world.task.click.assert_not_called()
        world = NavigationWorld([shop('Alice', next=False)])
        self.assertFalse(world.task.next_one())
        world.task.click.assert_not_called()

    def test_only_known_parent_pages_can_reenter_and_only_once(self):
        for page in ('main', 'mall', 'unknown', 'shop'):
            with self.subTest(page=page):
                world = NavigationWorld([{'page': page}])
                recovered = world.task._recover_shop_page()
                self.assertEqual(recovered, page in ('main', 'mall'))
                if recovered:
                    world.task.goto_page.assert_called_once_with('mall')
                    world.task._enter_shop.assert_called_once()
                else:
                    world.task.goto_page.assert_not_called()
                self.assertFalse(world.task._recover_shop_page())
                world.task.share.assert_not_called()

    def test_real_logged_courtyard_anchor_allows_one_verified_reentry(self):
        world = NavigationWorld([{'page': 'main'}])
        assets = class_from_file(ROOT/'tasks/GameUi/assets.py', 'GameUiAssets', world.namespace)
        marker = assets.I_CHECK_MAIN
        patch = cv2.imread(str(ROOT/'dev_tools/fixtures/mystery_shop/courtyard_marker.png'))
        self.assertIsNotNone(patch)
        image = np.zeros((720, 1280, 3), np.uint8)
        image[108:153, 807:883] = cv2.cvtColor(patch, cv2.COLOR_BGR2RGB)
        marker._image = cv2.cvtColor(cv2.imread(str(ROOT/marker.file)), cv2.COLOR_BGR2RGB)
        world.task.I_CHECK_MAIN = marker
        world.task.appear = Mock(side_effect=lambda rule, **kwargs:
                                 rule is marker and marker.template_match(image))
        self.assertTrue(world.task._recover_shop_page())
        world.task._enter_shop.assert_called_once()

    def test_run_recovers_scan_once_without_repeating_daily_share(self):
        world = NavigationWorld([{'page': 'main'}])
        task = world.task
        task._ensure_shop_due = Mock()
        task.run_shop = Mock(side_effect=[world.namespace['GameStuckError']('page lost'), None])
        task.next_one = Mock(return_value=False)
        task.shop_reward = Mock()
        task.back_mall = Mock()
        task.next_time = Mock()
        task.run()
        self.assertEqual(task.run_shop.call_count, 2)
        self.assertEqual(task._enter_shop.call_count, 2)
        task.share.assert_called_once()
        task.next_time.assert_called_once_with(True)

    def test_second_page_loss_propagates_without_reporting_task_success(self):
        world = NavigationWorld([{'page': 'main'}])
        task = world.task
        task._ensure_shop_due = Mock()
        task.run_shop = Mock(side_effect=world.namespace['GameStuckError']('page lost'))
        task.shop_reward, task.next_time = Mock(), Mock()
        with self.assertRaises(world.namespace['GameStuckError']):
            task.run()
        task.next_time.assert_not_called()
        task.shop_reward.assert_not_called()
        task.share.assert_called_once()


if __name__ == '__main__':
    unittest.main()
