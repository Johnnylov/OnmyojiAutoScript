"""Offline reward detail regressions; no game, emulator, or RPC service is used."""

import ast
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import cv2
import numpy as np

from test_image_template_guard import methods


ROOT = Path(__file__).resolve().parents[1]
PREPARE, BATTLE, RESULT, REWARD = 'prepare', 'battle', 'result', 'reward'
DETAILS = ('detail_1', 'detail_2', 'detail_3')
ACTION = SimpleNamespace(CONTINUE='continue', EXIT_WIN='win', EXIT_LOSE='lose')


class World:
    def __init__(self, frames, last_page=REWARD):
        self.frames = frames
        self.index = 0
        self.safe_click = Mock(return_value='local_safe_random_area')
        subject = methods(
            'tasks/Component/GeneralBattle/general_battle.py', 'GeneralBattle',
            ['_handle_reward_detail_popup', '_handle_reward', '_handle_missing_battle_page'],
            dict(BattleAction=ACTION, page_battle_prepare=PREPARE, page_battle=BATTLE,
                 page_battle_result=RESULT, page_reward=REWARD, random_click=self.safe_click,
                 logger=Mock(), time=SimpleNamespace(time=lambda: 100)),
        )
        self.task = subject()
        for index, marker in enumerate(DETAILS, 1):
            setattr(self.task, f'I_END_FIX_{index}', marker)
        for name in ('I_UI_BACK_RED', 'I_OVER_GHOST', 'I_GB_SKIN_CONFIRM'):
            setattr(self.task, name, name)
        self.task.screenshot = Mock(side_effect=self.advance)
        self.task.appear = Mock(side_effect=lambda marker, **kwargs: marker in self.frames[self.index])
        self.task.click = Mock(return_value=True)
        self.task.appear_then_click = Mock(return_value=False)
        self.task.device = SimpleNamespace(click_record_clear=Mock())
        self.task._evaluate_exit_matcher = Mock(return_value=True)
        self.context = SimpleNamespace(last_page=last_page, reward_no_battle_ts=0.0, is_win=True)
        self.config = SimpleNamespace(continuous_battle=False)

    def advance(self):
        self.index = min(self.index + 1, len(self.frames) - 1)

    def reward(self):
        return self.task._handle_reward(self.context, self.config)

    def missing(self, exit_matcher='task_exit'):
        return self.task._handle_missing_battle_page(self.context, self.config, exit_matcher)


class RewardDetailTests(unittest.TestCase):
    def test_each_popup_is_confirmed_with_a_fresh_frame_before_safe_click(self):
        for marker in DETAILS:
            with self.subTest(marker=marker):
                world = World([{marker}, {marker}])
                events = []
                def refresh():
                    events.append('fresh_frame')
                    world.advance()
                world.task.screenshot.side_effect = refresh
                world.task.click.side_effect = lambda *args, **kwargs: events.append('click')
                self.assertEqual(world.reward(), ACTION.CONTINUE)
                self.assertEqual(events, ['fresh_frame', 'click'])
                world.task.click.assert_called_once_with('local_safe_random_area', interval=2.5)
                world.safe_click.assert_called_once_with()
                world.task.appear_then_click.assert_not_called()
                self.assertIsNone(world.context.reward_no_battle_ts)

    def test_popup_disappearing_on_refresh_ends_reward_frame_without_any_click(self):
        world = World([{DETAILS[0]}, set()])
        self.assertEqual(world.reward(), ACTION.CONTINUE)
        world.task.screenshot.assert_called_once_with()
        world.task.click.assert_not_called()
        world.task.appear_then_click.assert_not_called()
        world.safe_click.assert_not_called()

    def test_new_popup_variant_on_refreshed_frame_is_still_closed(self):
        world = World([{DETAILS[0]}, {DETAILS[2]}])
        self.assertEqual(world.reward(), ACTION.CONTINUE)
        world.task.click.assert_called_once_with('local_safe_random_area', interval=2.5)

    def test_popup_precedes_exit_matcher_and_expired_reward_timeout(self):
        for last_page in (RESULT, REWARD):
            with self.subTest(last_page=last_page):
                world = World([{DETAILS[1]}, {DETAILS[1]}], last_page=last_page)
                self.assertEqual(world.missing(), ACTION.CONTINUE)
                world.task._evaluate_exit_matcher.assert_not_called()
                world.task.click.assert_called_once()
                self.assertIsNone(world.context.reward_no_battle_ts)

    def test_popup_disappearing_on_refresh_defers_exit_until_next_iteration(self):
        world = World([{DETAILS[2]}, set()])
        self.assertEqual(world.missing(), ACTION.CONTINUE)
        world.task._evaluate_exit_matcher.assert_not_called()
        world.task.click.assert_not_called()
        self.assertIsNone(world.context.reward_no_battle_ts)
        self.assertEqual(world.missing(), ACTION.EXIT_WIN)
        world.task._evaluate_exit_matcher.assert_called_once_with('task_exit')

    def test_throttled_popup_click_does_not_finish_reward_collection(self):
        world = World([{DETAILS[0]}, {DETAILS[0]}])
        world.task.click.return_value = False
        self.assertEqual(world.missing(), ACTION.CONTINUE)
        world.task._evaluate_exit_matcher.assert_not_called()
        self.assertIsNone(world.context.reward_no_battle_ts)

    def test_detail_does_not_turn_a_lost_result_into_a_win(self):
        world = World([{DETAILS[0]}, {DETAILS[0]}], last_page=RESULT)
        world.context.is_win = False
        self.assertEqual(world.missing(), ACTION.CONTINUE)
        self.assertFalse(world.context.is_win)

    def test_missing_page_outside_settlement_does_not_inspect_or_click_details(self):
        for last_page in (None, PREPARE, BATTLE):
            with self.subTest(last_page=last_page):
                world = World([{DETAILS[0]}], last_page=last_page)
                world.context.reward_no_battle_ts = None
                self.assertEqual(world.missing(exit_matcher=None), ACTION.CONTINUE)
                world.task.appear.assert_not_called()
                world.task.screenshot.assert_not_called()
                world.task.click.assert_not_called()

    def test_reward_without_details_retains_normal_local_click(self):
        world = World([set()])
        self.assertEqual(world.reward(), ACTION.CONTINUE)
        world.task.screenshot.assert_not_called()
        world.task.click.assert_called_once_with('local_safe_random_area', interval=0.8)

    def test_missing_page_without_details_retains_exit_matcher(self):
        world = World([set()])
        self.assertEqual(world.missing(), ACTION.EXIT_WIN)
        world.task._evaluate_exit_matcher.assert_called_once_with('task_exit')
        world.task.screenshot.assert_not_called()
        world.task.click.assert_not_called()

    def test_missing_page_without_details_retains_timeout_fallback(self):
        world = World([set()])
        self.assertEqual(world.missing(exit_matcher=None), ACTION.EXIT_WIN)
        world.task.screenshot.assert_not_called()
        world.task.click.assert_not_called()

    def test_popup_dismissal_restarts_fallback_timeout(self):
        world = World([{DETAILS[0]}, set()])
        self.assertEqual(world.missing(exit_matcher=None), ACTION.CONTINUE)
        self.assertIsNone(world.context.reward_no_battle_ts)
        self.assertEqual(world.missing(exit_matcher=None), ACTION.CONTINUE)
        self.assertEqual(world.context.reward_no_battle_ts, 100)


class RewardDetailAssetTests(unittest.TestCase):
    def test_all_recovery_templates_decode_and_match_their_asset_metadata(self):
        directory = ROOT / 'tasks/Component/GeneralBattle/gw'
        metadata = json.loads((directory / 'image.json').read_text(encoding='utf-8'))
        self.assertEqual({entry['itemName'] for entry in metadata}, {'end_fix_1', 'end_fix_2', 'end_fix_3'})
        tree = ast.parse((ROOT / 'tasks/Component/GeneralBattle/assets.py').read_text(encoding='utf-8-sig'))
        assets = next(node for node in tree.body if isinstance(node, ast.ClassDef))
        rules = {node.targets[0].id: {kw.arg: ast.literal_eval(kw.value) for kw in node.value.keywords}
                 for node in assets.body if isinstance(node, ast.Assign)
                 and isinstance(node.targets[0], ast.Name) and node.targets[0].id.startswith('I_END_FIX_')}
        for entry in metadata:
            with self.subTest(template=entry['imageName']):
                rule = rules['I_' + entry['itemName'].upper()]
                path = ROOT / rule['file']
                self.assertEqual(path.resolve(), (directory / entry['imageName']).resolve())
                image = cv2.imdecode(np.frombuffer(path.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
                self.assertIsNotNone(image)
                self.assertEqual(image.shape[:2], (rule['roi_front'][3], rule['roi_front'][2]))
                self.assertEqual(rule['roi_front'], tuple(map(int, entry['roiFront'].split(','))))
                self.assertEqual(rule['roi_back'], tuple(map(int, entry['roiBack'].split(','))))
                self.assertEqual(rule['threshold'], entry['threshold'])
                self.assertEqual(rule['method'], entry['method'])


if __name__ == '__main__':
    unittest.main()
