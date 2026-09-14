"""Offline regressions for reward overlays and the reported Demon Encounter data."""

import ast
import csv
import difflib
import json
from pathlib import Path
import re
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import cv2

from test_image_template_guard import methods

ROOT = Path(__file__).resolve().parents[1]


class RewardRecoveryTests(unittest.TestCase):
    def subject(self, visible=True, clicked=True):
        subject = methods('tasks/base_task.py', 'BaseTask',
                          ['ui_reward_appear_click'], {})()
        subject.I_UI_REWARD = 'reward_title'
        subject.C_UI_REWARD = 'safe_margin'
        subject.appear = Mock(return_value=visible)
        subject.click = Mock(return_value=clicked)
        subject.screenshot = Mock()
        return subject

    def test_visible_reward_blocks_underlying_actions_even_when_click_is_throttled(self):
        for clicked in (False, True):
            subject = self.subject(clicked=clicked)
            self.assertTrue(subject.ui_reward_appear_click())
            subject.click.assert_called_once_with('safe_margin', interval=0.8)

    def test_absent_reward_is_never_clicked(self):
        subject = self.subject(visible=False)
        self.assertFalse(subject.ui_reward_appear_click(screenshot=True))
        subject.screenshot.assert_called_once()
        subject.click.assert_not_called()

    def test_reward_target_stays_outside_panel_and_item_details(self):
        data = json.loads((ROOT / 'tasks/GlobalGame/ui/click.json').read_text(encoding='utf-8'))
        entry = next(row for row in data if row['itemName'] == 'ui_reward')
        namespace = {'RuleClick': lambda **kwargs: SimpleNamespace(**kwargs)}
        tree = ast.parse((ROOT / 'tasks/GlobalGame/assets.py').read_text(encoding='utf-8'))
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef))
        assignment = next(node for node in cls.body if isinstance(node, ast.Assign)
                          and any(isinstance(t, ast.Name) and t.id == 'C_UI_REWARD'
                                  for t in node.targets))
        exec(compile(ast.Module(body=[assignment], type_ignores=[]), 'reward_asset', 'exec'), namespace)
        for field, attribute in (('roiFront', 'roi_front'), ('roiBack', 'roi_back')):
            roi = tuple(map(int, entry[field].split(',')))
            self.assertEqual(roi, getattr(namespace['C_UI_REWARD'], attribute))
            x, y, w, h = roi
            self.assertGreaterEqual(x, 0)
            self.assertLessEqual(x + w, 100)
            self.assertGreaterEqual(y, 250)
            self.assertLessEqual(y + h, 400)

    def test_reported_reward_screenshots_are_recognized_even_with_item_tooltip(self):
        folders = ('oas1_1789329484656', 'oas1_1789207303086')
        paths = [next((ROOT / 'log/error' / folder).glob('*.png'), None) for folder in folders]
        if any(path is None for path in paths):
            self.skipTest('Local error snapshots have been cleaned')
        template = cv2.imread(str(ROOT / 'tasks/GlobalGame/ui/ui_ui_reward.png'))
        for path in paths:
            with self.subTest(path=path.name):
                screenshot = cv2.imread(str(path))
                match = cv2.matchTemplate(screenshot[142:287, 464:814], template,
                                         cv2.TM_CCOEFF_NORMED)
                self.assertGreaterEqual(cv2.minMaxLoc(match)[1], 0.6)


class DemonEncounterTests(unittest.TestCase):
    def test_challenge_count_supports_event_totals_and_rejects_missing_counts(self):
        subject = methods('tasks/DemonEncounter/script_task.py', 'ScriptTask',
                          ['check_challenge_done'], dict(re=re, logger=Mock()))()
        subject.device = SimpleNamespace(image=None)
        subject.O_DE_CHALLENGE_COUNT = SimpleNamespace(detect_and_ocr=Mock())
        for text, done in (('数：2/2', False), ('数：0/2', True), ('0/1', True),
                           ('1/1', False), ('0/0', False), ('', False), ('次数未知', False)):
            with self.subTest(text=text):
                subject.O_DE_CHALLENGE_COUNT.detect_and_ocr.return_value = [SimpleNamespace(ocr_text=text)]
                self.assertEqual(subject.check_challenge_done(), done)

    def test_logged_procurement_question_resolves_regardless_of_option_order(self):
        source = ROOT / 'tasks/DemonEncounter/data/answer.py'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        definitions = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))]
        namespace = dict(csv=csv, re=re, difflib=difflib, Path=Path,
                         __file__=str(source), logger=Mock())
        exec(compile(ast.Module(body=definitions, type_ignores=[]), str(source), 'exec'), namespace)
        answer = namespace['Answer']()
        question = '以下选项中，可以在寮内采办直接购买、发放给寮成员的是？'
        options = ['御魂&觉醒加成', '御行达摩', '勾玉']
        for _ in range(3):
            index = answer.answer_one(question, options)
            self.assertEqual(options[index - 1], '御魂&觉醒加成')
            options = options[1:] + options[:1]


if __name__ == '__main__':
    unittest.main()
