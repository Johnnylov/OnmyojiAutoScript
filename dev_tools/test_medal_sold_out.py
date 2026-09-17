"""Offline medal-shop regressions; no game, purchase or OCR service is used."""

import ast
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call

from test_image_template_guard import methods


ROOT = Path(__file__).resolve().parents[1]
ITEMS = ('black_daruma', 'mystery_amulet', 'ap_100', 'random_soul',
         'white_daruma', 'challenge_pass', 'red_daruma', 'broken_amulet')
MARKERS = ('BLACK', 'BLUE', 'AP', 'SOULS', 'WHITE', 'CHALLENGE_PASS', 'RED', 'BROKEN')


class MedalSoldOutTests(unittest.TestCase):
    def subject(self, **configured):
        self.logger = Mock()
        self.clock = SimpleNamespace(sleep=Mock())
        subject = methods('tasks/RichMan/mall/medal.py', 'Medal',
                          ['execute_medal', 'count_soldout'],
                          dict(logger=self.logger, time=self.clock))()
        subject.config = SimpleNamespace(rich_man=SimpleNamespace(medal_room=SimpleNamespace(
            enable=True, **{name: configured.get(name, False) for name in ITEMS})))
        subject.device = SimpleNamespace(image=object())
        subject._enter_medal = Mock()
        subject.screenshot = Mock()
        subject.appear = Mock(return_value=True)
        subject.buy_mall_one = Mock()
        subject.buy_mall_more = Mock()
        subject.set_next_run = Mock()
        subject.O_SOLD_OUT = SimpleNamespace(detect_and_ocr=Mock(return_value=[]))
        subject.O_MALL_RESOURCE_3 = 'medal_balance'
        subject.O_MALL_RESOURCE_5 = 'local_soul_balance'
        for name in MARKERS:
            setattr(subject, f'I_ME_{name}', name)
            setattr(subject, f'I_ME_CHECK_{name}', f'CHECK_{name}')
        return subject

    def test_disabled_shop_has_no_actions(self):
        subject = self.subject(black_daruma=True)
        subject.config.rich_man.medal_room.enable = False
        subject.execute_medal()
        subject._enter_medal.assert_not_called()
        subject.screenshot.assert_not_called()
        subject.appear.assert_not_called()
        subject.buy_mall_one.assert_not_called()
        subject.buy_mall_more.assert_not_called()
        subject.O_SOLD_OUT.detect_and_ocr.assert_not_called()
        self.clock.sleep.assert_not_called()

    def test_unselected_items_are_not_checked_or_bought(self):
        subject = self.subject()
        subject.execute_medal()
        subject.screenshot.assert_not_called()
        subject.appear.assert_not_called()
        subject.buy_mall_one.assert_not_called()
        subject.buy_mall_more.assert_not_called()
        subject.O_SOLD_OUT.detect_and_ocr.assert_not_called()

    def test_visible_items_keep_local_purchase_parameters(self):
        subject = self.subject(**dict.fromkeys(ITEMS, True))
        config = subject.config.rich_man.medal_room
        config.challenge_pass, config.red_daruma, config.broken_amulet = 7, 13, 19
        subject.execute_medal()
        self.assertEqual(subject.buy_mall_one.call_args_list, [
            call(buy_button='BLACK', buy_check='CHECK_BLACK', money_ocr='medal_balance', buy_money=480),
            call(buy_button='BLUE', buy_check='CHECK_BLUE', money_ocr='medal_balance', buy_money=180),
            call(buy_button='AP', buy_check='CHECK_AP', money_ocr='medal_balance', buy_money=120),
            call(buy_button='SOULS', buy_check='CHECK_SOULS', money_ocr='local_soul_balance', buy_money=320),
        ])
        self.assertEqual(subject.buy_mall_more.call_args_list, [
            call(buy_button='WHITE', remain_number=True, money_ocr='medal_balance',
                 buy_number=2, buy_max=2, buy_money=100),
            call(buy_button='CHALLENGE_PASS', remain_number=True, money_ocr='medal_balance',
                 buy_number=7, buy_max=10, buy_money=30),
            call(buy_button='RED', remain_number=False, money_ocr='medal_balance',
                 buy_number=13, buy_max=99, buy_money=30),
            call(buy_button='BROKEN', remain_number=False, money_ocr='medal_balance',
                 buy_number=19, buy_max=99, buy_money=20),
        ])
        subject.O_SOLD_OUT.detect_and_ocr.assert_not_called()
        subject.set_next_run.assert_not_called()

    def test_each_item_is_checked_against_a_fresh_screenshot(self):
        subject = self.subject(**dict.fromkeys(ITEMS, True))
        actions = Mock()
        actions.attach_mock(subject.screenshot, 'screenshot')
        actions.attach_mock(subject.appear, 'appear')
        subject.execute_medal()
        self.assertEqual(actions.mock_calls,
                         [action for marker in MARKERS
                          for action in (call.screenshot(), call.appear(marker))])

    def test_every_missing_item_is_skipped_without_purchase(self):
        for item, marker in zip(ITEMS, MARKERS):
            with self.subTest(item=item):
                subject = self.subject(**{item: 1})
                subject.appear.return_value = False
                subject.execute_medal()
                subject.appear.assert_called_once_with(marker)
                subject.buy_mall_one.assert_not_called()
                subject.buy_mall_more.assert_not_called()
                subject.O_SOLD_OUT.detect_and_ocr.assert_called_once_with(
                    subject.device.image, logDisplay=False)
                self.assertIn(item, self.logger.warning.call_args.args[0])
                subject.set_next_run.assert_not_called()

    def test_missing_item_does_not_prevent_later_visible_purchase(self):
        subject = self.subject(black_daruma=True, mystery_amulet=True)
        subject.appear.side_effect = lambda marker: marker == 'BLUE'
        subject.execute_medal()
        subject.buy_mall_one.assert_called_once_with(
            buy_button='BLUE', buy_check='CHECK_BLUE', money_ocr='medal_balance', buy_money=180)
        subject.buy_mall_more.assert_not_called()

    def test_empty_ocr_results_do_not_interrupt_missing_item_handling(self):
        for results in (None, []):
            with self.subTest(results=results):
                subject = self.subject(black_daruma=True)
                subject.appear.return_value = False
                subject.O_SOLD_OUT.detect_and_ocr.return_value = results
                subject.execute_medal()
                self.assertIn('Page sold-out labels: 0', self.logger.warning.call_args.args[0])
                subject.buy_mall_one.assert_not_called()
                subject.set_next_run.assert_not_called()

    def test_sold_out_count_is_a_page_reference_not_item_confirmation(self):
        subject = self.subject(black_daruma=True)
        subject.appear.return_value = False
        subject.O_SOLD_OUT.detect_and_ocr.return_value = [
            SimpleNamespace(ocr_text=text) for text in ('售罄', '已售罄', '剩余1', '勋章')]
        subject.execute_medal()
        message = self.logger.warning.call_args.args[0]
        self.assertIn('Page sold-out labels: 2', message)
        self.assertIn('cannot confirm whether these items are sold out', message)
        subject.set_next_run.assert_not_called()

    def test_ocr_rule_matches_its_generation_source(self):
        metadata = json.loads((ROOT / 'tasks/RichMan/mall/medal/ocr.json').read_text(encoding='utf-8'))
        entry = next(item for item in metadata if item['itemName'] == 'sold_out')
        tree = ast.parse((ROOT / 'tasks/RichMan/assets.py').read_text(encoding='utf-8'))
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef))
        assignment = next(node for node in cls.body if isinstance(node, ast.Assign)
                          and any(isinstance(target, ast.Name) and target.id == 'O_SOLD_OUT'
                                  for target in node.targets))
        namespace = dict(RuleOcr=lambda **kwargs: SimpleNamespace(**kwargs))
        exec(compile(ast.Module(body=[assignment], type_ignores=[]), 'sold_out_asset', 'exec'), namespace)
        rule = namespace['O_SOLD_OUT']
        self.assertEqual(rule.roi, tuple(map(int, entry['roiFront'].split(','))))
        self.assertEqual(rule.area, tuple(map(int, entry['roiBack'].split(','))))
        for field in ('mode', 'method', 'keyword'):
            self.assertEqual(getattr(rule, field), entry[field])
        self.assertEqual(rule.name, entry['itemName'])


if __name__ == '__main__':
    unittest.main()
