"""Offline regressions for adapted upstream fixes; no emulator or game is controlled."""

from copy import copy
from pathlib import Path
import re
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from module.atom.ocr import RuleOcr
from module.device.platform2.handlers.mumu12 import MuMu12Handler
from test_image_template_guard import methods


class ExactNicknameTests(unittest.TestCase):
    def rule(self, words):
        rule = RuleOcr(roi=(10, 20, 300, 300), area=(10, 20, 300, 300),
                       mode='Full', method='Default', keyword='阿明', name='friend')
        rule.detect_and_ocr = Mock(return_value=[SimpleNamespace(
            ocr_text=word, box=np.array([[0, i * 30], [40, i * 30],
                                      [40, i * 30 + 20], [0, i * 30 + 20]]))
            for i, word in enumerate(words)])
        return rule

    def test_exact_name_skips_similar_longer_name(self):
        rule = self.rule(['阿明的朋友', '阿明'])
        self.assertEqual(rule.ocr(None, exact=True), (10, 50, 40, 20))

    def test_partial_name_alone_is_not_selected(self):
        rule = self.rule(['阿明的朋友'])
        self.assertEqual(rule.ocr(None, exact=True), (0, 0, 0, 0))

    def test_default_keeps_partial_matching_for_existing_callers(self):
        rule = self.rule(['阿明的朋友'])
        self.assertEqual(rule.ocr(None), (10, 20, 40, 20))


class MuMuCommandsTests(unittest.TestCase):
    def setUp(self):
        self.handler = MuMu12Handler()
        self.handler._resolve_console = Mock(return_value='C:/MuMu/MuMuManager.exe')

    def test_android15_targets_named_instance_and_version(self):
        for name in ('MuMuPlayer-15.0-3', 'MuMuPlayerGlobal-15.0-3'):
            with self.subTest(name=name):
                instance = SimpleNamespace(name=name)
                self.assertEqual(self.handler.build_start_command(instance),
                                 '"C:/MuMu/MuMuManager.exe" control -v 3 --version 15 launch')
                self.assertEqual(self.handler.build_stop_command(instance),
                                 '"C:/MuMu/MuMuManager.exe" control -v 3 --version 15 shutdown')
                self.assertEqual(self.handler.stop_command_timeout(instance), 30)

    def test_android12_keeps_existing_command(self):
        instance = SimpleNamespace(name='MuMuPlayer-12.0-2')
        self.assertEqual(self.handler.build_start_command(instance),
                         '"C:/MuMu/MuMuManager.exe" control -v 2 launch')
        self.assertIsNone(self.handler.stop_command_timeout(instance))

    def test_unknown_instance_never_defaults_to_another_instance(self):
        instance = SimpleNamespace(name='unknown')
        self.assertIsNone(self.handler.build_start_command(instance))
        self.assertIsNone(self.handler.build_stop_command(instance))
        self.handler._resolve_console.assert_not_called()

    def test_shutdown_completion_is_waited_before_return(self):
        cls = methods('module/device/platform2/platform_windows.py', 'PlatformWindows',
                      ['_emulator_stop'], dict(EmulatorUnknown=RuntimeError))
        obj = cls()
        instance = SimpleNamespace(name='MuMuPlayer-15.0-3')
        obj._get_handler = Mock(return_value=self.handler)
        obj.execute = Mock(return_value=SimpleNamespace(wait=Mock()))
        self.assertTrue(obj._emulator_stop(instance))
        obj.execute.return_value.wait.assert_called_once_with(timeout=30)
        obj.execute.assert_called_once_with(self.handler.build_stop_command(instance))


class TaskFixTests(unittest.TestCase):
    def test_demon_count_recognizes_finished_without_guessing_unreadable_text(self):
        cls = methods('tasks/DemonEncounter/script_task.py', 'ScriptTask',
                      ['check_challenge_done'], dict(re=re, logger=Mock()))
        obj = cls()
        obj.device = SimpleNamespace(image=None)
        obj.O_DE_CHALLENGE_COUNT = SimpleNamespace(detect_and_ocr=Mock())
        for text, expected in [('今日挑战次数:0/1', True), ('今日挑战次数:1/1', False),
                               ('今日挑战次数:0 / 1', True), ('', False), ('无法识别', False)]:
            with self.subTest(text=text):
                obj.O_DE_CHALLENGE_COUNT.detect_and_ocr.return_value = [SimpleNamespace(ocr_text=text)]
                self.assertEqual(obj.check_challenge_done(), expected)
        obj.O_DE_CHALLENGE_COUNT.detect_and_ocr.return_value = None
        self.assertFalse(obj.check_challenge_done())

    def harvest(self):
        cls = methods('tasks/KekkaiActivation/script_task.py', 'ScriptTask', ['harvest_card'],
                      dict(copy=copy, RuleImage=SimpleNamespace(METHOD_MULTI_SCALE_TEMPLATE_MATCH='multi')))
        obj = cls()
        cards = []
        for name in ('I_A_HARVEST_EXP', 'I_A_HARVEST_FISH4', 'I_A_HARVEST_KAIKO_4',
                     'I_A_HARVEST_KAIKO_3', 'I_A_HARVEST_KAIKO_6', 'I_A_HARVEST_FISH_6',
                     'I_A_HARVEST_MOON_3', 'I_A_HARVEST_FISH_3'):
            card = SimpleNamespace(name=name, roi_front=[0, 0, 20, 20], roi_back=[0, 0, 40, 40], method='normal')
            setattr(obj, name, card)
            cards.append(card)
        obj.screenshot = Mock()
        obj.appear_then_click = Mock(return_value=False)
        return obj, cards

    def test_harvest_retries_have_a_fixed_limit_and_keep_shared_rules(self):
        obj, cards = self.harvest()
        self.assertFalse(obj.harvest_card())
        self.assertEqual(obj.screenshot.call_count, 5)
        self.assertEqual(obj.appear_then_click.call_count, 40)
        self.assertTrue(all(card.method == 'normal' for card in cards))

    def test_harvest_stops_after_first_successful_click(self):
        obj, _ = self.harvest()
        obj.appear_then_click.side_effect = [False, True]
        self.assertTrue(obj.harvest_card())
        self.assertEqual(obj.screenshot.call_count, 1)
        self.assertEqual(obj.appear_then_click.call_count, 2)

    def guild(self, succeeds=True):
        cls = methods('tasks/RichMan/guild.py', 'Guild', ['execute_guild'],
                      dict(logger=Mock(), time=SimpleNamespace(sleep=Mock()),
                           random=SimpleNamespace(randint=lambda *_: 3),
                           page_guild_store='store', page_shirin='outside'))
        obj = cls()
        obj.goto_page = Mock()
        events = []
        obj.screenshot = Mock(side_effect=lambda: events.append('frame'))
        obj.swipe = Mock(side_effect=lambda *_args, **_kwargs: events.append('swipe'))
        obj.S_GUILD_STORE = 'swipe-rule'
        obj.appear = Mock(return_value=True)
        for marker, action in [('I_GUILD_HONOR_GIFT', '_guild_honor_gift'),
                               ('I_GUILD_BLUE', '_guild_mystery_amulet'),
                               ('I_GUILD_SCRAP', '_guild_black_daruma_scrap'),
                               ('I_GUILD_SKIN', '_guild_skin_ticket')]:
            setattr(obj, marker, marker)
            def buy(label=action):
                events.append(label)
                return succeeds
            setattr(obj, action, Mock(side_effect=buy))
        config = SimpleNamespace(enable=True, honor_gift=True, mystery_amulet=True,
                                 black_daruma_scrap=False, skin_ticket=False)
        return obj, config, events

    def test_guild_purchase_refreshes_before_next_purchase_and_does_not_swipe(self):
        obj, config, events = self.guild()
        obj.execute_guild(config)
        self.assertEqual(events, ['frame', '_guild_honor_gift', 'frame', '_guild_mystery_amulet', 'frame'])
        obj.swipe.assert_not_called()
        obj._guild_honor_gift.assert_called_once()
        obj._guild_mystery_amulet.assert_called_once()

    def test_guild_failed_purchases_keep_existing_swipe_limit(self):
        obj, config, _ = self.guild(succeeds=False)
        obj.execute_guild(config)
        self.assertEqual(obj.swipe.call_count, 4)
        obj.goto_page.assert_called_with('outside')


if __name__ == '__main__':
    unittest.main()
