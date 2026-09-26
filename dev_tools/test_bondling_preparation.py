"""Replay preparation against stateful game/shop fakes; no device actions."""
from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from dev_tools.test_activity_preparation_retry import source_methods
from module.exception import TaskDeferred, TaskEnd
from tasks.BondlingFairyland.config import BondlingClass, BondlingMode, UserStatus
from tasks.BondlingFairyland.preparation import BondlingPreparation


class PreparationGame(BondlingPreparation):
    def __init__(self, balance=2014, quota=71, stones=2, target_exists=True):
        self.balance, self.quota, self.stones = balance, quota, stones
        self.target_exists = target_exists
        self.state = 'shop'
        self.quantity = 1
        self.frames = 0
        self.payments, self.summons, self.actions = [], [], []
        self.device = SimpleNamespace(image=None, click_record_clear=Mock())
        self.O_BL_CHECK_MONEY = SimpleNamespace(ocr_digit_counter=lambda _: (self.balance, 4000-self.balance, 4000))
        self.O_MEMORY_QUANTITY = SimpleNamespace(ocr=lambda _: str(self.quantity))
        self.O_MEMORY_PRICE = SimpleNamespace(ocr=lambda _: str(self.quantity * 20))
        self.O_SUMMON_CONFIRM = SimpleNamespace(ocr=lambda _: f'是否消耗{self.quantity}个鸣契石召唤镇墓善？')
        for attr in ('I_MALL_SCCALES', 'I_MALL_SCCALES_CHECK', 'I_MALL_BONDLINGS_SURE', 'I_MALL_BONDLINGS_ON',
                     'I_BL_BUY_SOULS', 'I_STONE_SURE', 'I_STONE_CLOSE', 'I_BALL_HELP', 'I_GI_SURE',
                     'C_STONE_1', 'C_STONE_2', 'C_STONE_3', 'C_STONE_4'):
            setattr(self, attr, attr)

    def screenshot(self):
        self.frames += 1
        assert self.frames <= 3000, 'preparation did not finish'

    def goto_page(self, page, **kwargs):
        self.state = 'search' if 'bondling' in str(page) else 'shop'

    def ui_click(self, *args):
        pass

    def appear(self, rule):
        return (
            rule == self.I_MALL_BONDLINGS_ON and self.state == 'shop' or
            rule == self.I_BL_BUY_SOULS and self.state == 'shop' and self.quota > 0 or
            rule == self.I_BALL_HELP and self.state == 'target' or
            rule == self.I_STONE_SURE and self.state in ('summon', 'confirm') or
            rule == self.I_STONE_CLOSE and self.state == 'summon' or
            rule == self.I_GI_SURE and self.state == 'confirm' or
            rule in (self.I_MEMORY_MAX, self.I_MEMORY_SUB) and self.state == 'offer' or
            rule == self.I_SUMMON_MAX and self.state == 'summon')

    def ocr_appear(self, rule, **kwargs):
        return (rule is self.O_MEMORY_TITLE and self.state == 'offer' or
                rule is self.O_STONE_SHOP and self.state == 'stone_shop')

    def in_search_ui(self):
        return self.state == 'search'

    def ocr_appear_click(self, rule, **kwargs):
        if rule is self.O_SUMMON_ENTRY and self.state == 'target':
            self.state = 'summon'
            self.quantity = 1
            return True
        return False

    def appear_then_click(self, rule, **kwargs):
        if self.appear(rule):
            self.click(rule)
            return True
        return False

    def click(self, rule, **kwargs):
        self.actions.append(rule)
        if rule == self.I_BL_BUY_SOULS:
            self.state, self.quantity = 'offer', 1
        elif rule == self.I_MEMORY_MAX:
            self.quantity = min(25, self.quota)
        elif rule == self.I_MEMORY_SUB:
            self.quantity -= 1
        elif rule == self.C_MEMORY_PAY:
            assert self.state == 'offer', 'must not buy a stone'
            assert self.balance >= self.quantity * 20
            assert 0 < self.quantity <= self.quota
            self.balance -= self.quantity * 20
            self.quota -= self.quantity
            self.payments.append(self.quantity)
            self.state = 'reward'
        elif rule in (self.C_STONE_1, self.C_STONE_2, self.C_STONE_3, self.C_STONE_4):
            self.state = 'target' if self.target_exists else 'summon'
        elif rule == self.I_SUMMON_MAX:
            self.quantity = max(1, min(30, self.stones))
        elif rule == self.I_STONE_SURE:
            self.state = 'confirm' if self.stones else 'stone_shop'
        elif rule == self.I_GI_SURE:
            self.stones -= self.quantity
            self.summons.append(self.quantity)
            self.state, self.target_exists = 'target', True
        elif rule == self.C_PREP_DISMISS:
            self.state = 'summon' if self.state == 'stone_shop' else 'shop'
        elif rule == self.I_STONE_CLOSE:
            self.state = 'target'
        return True

    def ui_reward_appear_click(self):
        if self.state != 'reward':
            return False
        self.state = 'shop'
        return True


class BondlingPreparationTests(unittest.TestCase):
    def setUp(self):
        sleep = patch('tasks.BondlingFairyland.preparation.sleep')
        sleep.start()
        self.addCleanup(sleep.stop)

    def test_exchange_respects_weekly_limit_then_reads_new_baseline(self):
        game = PreparationGame(balance=2014, quota=71)
        self.assertEqual(game.prepare_bond_memory(), 594)
        self.assertEqual(game.payments, [25, 25, 21])
        self.assertEqual(game.initial_bond_memory, 594)

    def test_exchange_respects_balance_and_leaves_remainder(self):
        game = PreparationGame(balance=54, quota=71)
        self.assertEqual(game.prepare_bond_memory(), 14)
        self.assertEqual(game.payments, [2])

    def test_full_balance_still_exchanges_before_limit_decision(self):
        game = PreparationGame(balance=4000, quota=200)
        self.assertEqual(game.prepare_bond_memory(), 0)
        self.assertEqual(sum(game.payments), 200)

    def test_zero_low_balance_and_sold_out_do_not_buy(self):
        for balance, quota in [(0, 71), (14, 71), (2000, 0)]:
            with self.subTest(balance=balance, quota=quota):
                game = PreparationGame(balance=balance, quota=quota)
                self.assertEqual(game.prepare_bond_memory(), balance)
                self.assertEqual(game.payments, [])

    def test_unreadable_balance_does_not_mean_zero_or_trigger_purchase(self):
        for value in [(0, 0, 0), (12000, -8000, 4000), None]:
            with self.subTest(value=value):
                game = PreparationGame()
                game.O_BL_CHECK_MONEY.ocr_digit_counter = lambda _: value
                with self.assertRaises(TaskDeferred):
                    game.prepare_bond_memory()
                self.assertEqual(game.payments, [])

    def test_offer_price_must_match_quantity_before_spending(self):
        game = PreparationGame()
        game.O_MEMORY_PRICE.ocr = lambda _: '30'
        with self.assertRaises(TaskDeferred):
            game.prepare_bond_memory()
        self.assertEqual(game.payments, [])

    def test_delayed_payment_response_is_never_resubmitted(self):
        game = PreparationGame()
        game.ui_reward_appear_click = lambda: False
        with self.assertRaises(TaskDeferred):
            game.prepare_bond_memory()
        self.assertEqual(game.payments, [25])

    def test_uncertain_balance_change_stops_before_second_exchange(self):
        game = PreparationGame()
        game.read_bond_memory = Mock(side_effect=[2014, 2014])
        with self.assertRaises(TaskDeferred):
            game.prepare_bond_memory()
        self.assertEqual(game.payments, [25])

    def test_summons_existing_stones_even_when_target_already_exists(self):
        for exists in (True, False):
            with self.subTest(existing_target=exists):
                game = PreparationGame(stones=2, target_exists=exists)
                game.state = 'search'
                game.prepare_bond_summons(1, '镇墓兽')
                self.assertEqual(game.stones, 0)
                self.assertEqual(game.summons, [2])
                self.assertEqual(game.payments, [])
                self.assertEqual(game.state, 'search')

    def test_zero_stones_attempts_then_closes_shop_without_buying(self):
        game = PreparationGame(stones=0)
        game.state = 'search'
        game.prepare_bond_summons(1, '镇墓兽')
        self.assertIn(game.I_STONE_SURE, game.actions)
        self.assertIn(game.C_PREP_DISMISS, game.actions)
        self.assertNotIn(game.C_MEMORY_PAY, game.actions)
        self.assertEqual(game.summons, [])
        self.assertEqual(game.state, 'search')

    def test_summon_uses_more_than_one_batch_when_game_clamps_quantity(self):
        game = PreparationGame(stones=32)
        game.state = 'search'
        game.prepare_bond_summons(1, '镇墓兽')
        self.assertEqual(game.summons, [30, 2])

    def test_wrong_species_confirmation_is_not_accepted(self):
        game = PreparationGame()
        game.state = 'search'
        with self.assertRaises(TaskDeferred):
            game.prepare_bond_summons(2, '火灵')
        self.assertEqual(game.stones, 2)
        self.assertEqual(game.summons, [])

    def test_unknown_summon_page_times_out_without_consuming(self):
        game = PreparationGame()
        game.state = 'unknown'
        with self.assertRaises(TaskDeferred):
            game.prepare_bond_summons(1, '镇墓兽')
        self.assertEqual(game.stones, 2)

    def task(self, role, balance=14, mode=BondlingMode.MODE2):
        namespace = dict(logger=Mock(), TaskEnd=TaskEnd, BondlingMode=BondlingMode,
                         BondlingClass=BondlingClass, UserStatus=UserStatus,
                         page_main='main', page_bondling_fairyland='bondling')
        cls = source_methods('tasks/BondlingFairyland/script_task.py', 'ScriptTask', ['run'], namespace)
        task = cls()
        cfg = SimpleNamespace(check_enable=True, limit_num=2000, limit_count=30,
                              bondling_mode=mode, user_status=role,
                              bondling_stone_class=BondlingClass.TOMB_GUARD)
        task.config = SimpleNamespace(bondling_fairyland=SimpleNamespace(bondling_config=cfg))
        task.events = []
        task.prepare_bond_memory = Mock(side_effect=lambda: (task.events.append('redeem'), balance)[1])
        for method in ('switch_soul', 'goto_page', 'goto_ball_area', 'prepare_bond_summons',
                       'wait_local_team_ready', 'switch_ball', 'run_member', 'set_next_run', 'run_search'):
            setattr(task, method, Mock(side_effect=lambda *_, name=method, **__: task.events.append(name)))
        return task

    def test_each_capture_role_prepares_before_team_barrier(self):
        for role in UserStatus:
            with self.subTest(role=role):
                task = self.task(role)
                task.run()
                self.assertEqual(task.events[0], 'redeem')
                self.assertLess(task.events.index('prepare_bond_summons'), task.events.index('wait_local_team_ready'))
                task.prepare_bond_summons.assert_called_once_with(1, '镇墓兽')

    def test_only_post_exchange_balance_can_skip_this_round(self):
        task = self.task(UserStatus.MEMBER, balance=2000)
        with self.assertRaises(TaskEnd):
            task.run()
        task.prepare_bond_memory.assert_called_once()
        task.prepare_bond_summons.assert_not_called()
        task.run_member.assert_not_called()

    def test_search_only_mode_never_spends_stones(self):
        task = self.task(UserStatus.ALONE, mode=BondlingMode.MODE1)
        with self.assertRaises(TaskEnd):
            task.run()
        task.prepare_bond_summons.assert_not_called()
        task.run_search.assert_called_once()


if __name__ == '__main__':
    unittest.main()
