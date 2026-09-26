"""Stateful replay of the maximum-batch exchange without operating a game."""
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from module.exception import TaskDeferred, RequestHumanTakeover
from tasks.RichMan.mall.plain_souls import PlainSoulExchange
from tasks.RichMan.config import Scales


class Shop(PlainSoulExchange):
    I_BUY_PLUS, I_BUY_SUB, I_SCA_SIX_STAR, I_SCA_REWARD, C_SCA_SOULS_GET = range(5)

    def __init__(self, balance=8000, maximum=40):
        self.state, self.balance, self.maximum, self.quantity = 'shop', balance, maximum, 1
        self.device = NS(image=None)
        self.payments, self.clicks = [], []
        self.after_pay, self.price_offset, self.keep_balance = 'reward', 0, False
        self.title = '朴素的御魂礼盒'
        self.O_PLAIN_TITLE = NS(ocr=lambda _: self.title if self.state == 'offer' else '')
        self.O_PLAIN_CARD = NS(ocr=lambda _: '朴素的御魂礼盒' if self.state in ('shop', 'offer') else '')
        self.O_PLAIN_QUANTITY = NS(ocr=lambda _: str(self.quantity))
        self.O_PLAIN_PRICE = NS(ocr=lambda _: str(self.quantity * 50 + self.price_offset))
        self.O_PLAIN_BALANCE = NS(ocr_digit_counter=lambda _: (self.balance, 8000 - self.balance, 8000))

    def screenshot(self):
        pass

    def click(self, rule, **_):
        self.clicks.append(rule)
        if rule == self.C_PLAIN_OPEN:
            self.state = 'offer'
        elif rule == self.I_BUY_PLUS:
            self.quantity = self.maximum
        elif rule == self.I_BUY_SUB:
            self.quantity -= 1
        elif rule == self.C_PLAIN_PAY:
            assert self.state == 'offer'
            assert 0 < self.quantity * 50 <= self.balance
            self.payments.append(self.quantity)
            if not self.keep_balance:
                self.balance -= self.quantity * 50
            self.state = self.after_pay
        elif rule in (self.C_PLAIN_DISMISS, self.C_SCA_SOULS_GET):
            self.state = 'shop'
        return True

    def appear_then_click(self, rule, **kwargs):
        return self.click(rule, **kwargs)

    def appear(self, rule):
        return rule == self.I_SCA_SIX_STAR and self.state == 'reward'

    def ui_reward_appear_click(self):
        return False


class PlainSoulExchangeTests(unittest.TestCase):
    def setUp(self):
        mock_sleep = patch('tasks.RichMan.mall.plain_souls.sleep')
        mock_sleep.start()
        self.addCleanup(mock_sleep.stop)

    def test_maximum_is_one_batch_not_all_remaining_currency(self):
        game = Shop()
        self.assertEqual(game.exchange_plain_souls(True), 40)
        self.assertEqual(game.payments, [40])
        self.assertEqual((game.balance, game.state), (6000, 'shop'))

    def test_disabled_and_insufficient_currency_never_open_an_offer(self):
        for enabled, balance in [(False, 8000), (True, 0), (True, 49)]:
            game = Shop(balance)
            self.assertEqual(game.exchange_plain_souls(enabled), 0)
            self.assertEqual(game.clicks, [])

    def test_game_weekly_remainder_and_affordable_remainder_are_respected(self):
        for balance, maximum, expected in [(8000, 7, 7), (125, 40, 2), (50, 40, 1)]:
            game = Shop(balance, maximum)
            self.assertEqual(game.exchange_plain_souls(True), expected)
            self.assertEqual(game.payments, [expected])

    def test_zero_maximum_closes_without_payment(self):
        game = Shop(maximum=0)
        self.assertEqual(game.exchange_plain_souls(True), 0)
        self.assertEqual(game.state, 'shop')
        self.assertEqual(game.payments, [])

    def test_unreadable_or_icon_prefixed_counter_is_not_a_balance(self):
        for bad in [(18000, -10000, 8000), (0, 0, 0), None]:
            game = Shop()
            game.O_PLAIN_BALANCE = NS(ocr_digit_counter=lambda _: bad)
            with self.assertRaises(TaskDeferred):
                game.exchange_plain_souls(True)
            self.assertEqual(game.clicks, [])

    def test_invalid_quantity_price_or_wrong_product_never_pay(self):
        for condition in ('quantity', 'price', 'product', 'huge_quantity'):
            game = Shop()
            if condition == 'quantity':
                game.O_PLAIN_QUANTITY = NS(ocr=lambda _: '')
            elif condition == 'price':
                game.price_offset = 1
            elif condition == 'product':
                game.title = '华丽的御魂礼盒'
            else:
                game.maximum = 999
            with self.assertRaises(TaskDeferred):
                game.exchange_plain_souls(True)
            self.assertEqual(game.payments, [])

    def test_changed_quantity_blocks_payment(self):
        game = Shop(balance=125)
        original = game.appear_then_click
        game.appear_then_click = lambda rule, **kwargs: True if rule == game.I_BUY_SUB else original(rule, **kwargs)
        with self.assertRaisesRegex(TaskDeferred, '未按预期减少'):
            game.exchange_plain_souls(True)
        self.assertEqual(game.payments, [])

    def test_lost_payment_response_is_never_retried(self):
        game = Shop()
        game.after_pay = 'offer'
        with self.assertRaises(RequestHumanTakeover):
            game.exchange_plain_souls(True)
        self.assertEqual(game.payments, [40])

    def test_unverified_deduction_is_not_success_or_another_purchase(self):
        game = Shop()
        game.keep_balance = True
        with self.assertRaises(RequestHumanTakeover):
            game.exchange_plain_souls(True)
        self.assertEqual(game.payments, [40])

    def test_legacy_positive_quantities_load_as_enabled_checkbox(self):
        for old, expected in [(0, False), (40, True), (7, True), (True, True), (False, False)]:
            self.assertIs(Scales(orochi_scales=old).orochi_scales, expected)
        self.assertEqual(Scales.model_json_schema()['properties']['orochi_scales']['type'], 'boolean')

    def test_enter_scales_accepts_new_card_without_the_old_artwork(self):
        from tasks.RichMan.mall.navbar import MallNavbar
        task = NS(device=NS(image='screen'), screenshot=Mock(), appear=Mock(return_value=False),
                  I_MALL_SCCALES_CHECK='legacy', I_MALL_SCCALES='entry', appear_then_click=Mock())
        with patch.object(PlainSoulExchange.O_PLAIN_CARD, 'ocr', return_value='朴素的御魂礼盒'):
            MallNavbar._enter_scales(task)
        task.appear_then_click.assert_not_called()


if __name__ == '__main__':
    unittest.main()
