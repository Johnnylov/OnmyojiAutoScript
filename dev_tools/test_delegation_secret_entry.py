"""Offline regressions for saved delegation dialogue and secret entry stalls.

No device, game, OCR model, or RPC service is started. Local log screenshots
are replayed when available; account screenshots are not copied into the repo.
"""

import ast
from collections import deque
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import cv2
import numpy as np

from module.exception import GameTooManyClickError
from test_image_template_guard import methods


ROOT = Path(__file__).resolve().parents[1]
DELEGATION_ERRORS = ('oas2_1789723554045', 'oas2_1789723677483',
                     'oas2_1789723786475', 'oas2_1789723897414')
SECRET_ERROR = 'oas1_1789699491423'


class Deferred(Exception):
    pass


def assets(task):
    tree = ast.parse((ROOT / 'tasks' / task / 'assets.py').read_text(encoding='utf-8'))
    return {node.targets[0].id: SimpleNamespace(
        **{kw.arg: ast.literal_eval(kw.value) for kw in node.value.keywords})
        for cls in tree.body if isinstance(cls, ast.ClassDef)
        for node in cls.body if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call)}


class Replay:
    def __init__(self, image):
        self.image = image

    def appear(self, rule):
        x, y, w, h = rule.roi_back
        template = cv2.imread(str(ROOT / rule.file))
        score = cv2.minMaxLoc(cv2.matchTemplate(
            self.image[y:y+h, x:x+w], template, cv2.TM_CCOEFF_NORMED))[1]
        return score > rule.threshold


def subject(task, names, namespace=None):
    return methods(f'tasks/{task}/script_task.py', 'ScriptTask', names,
                   dict(logger=Mock(), TaskDeferred=Deferred, **(namespace or {})))


def device_click_guard():
    """Exercise production click counting without constructing a device."""
    cls = methods('module/device/device.py', 'Device',
                  ['click_record_add', 'click_record_check'],
                  dict(logger=Mock(), GameTooManyClickError=GameTooManyClickError))
    guard = cls()
    guard.click_record = deque(maxlen=15)
    guard.click_record_clear = Mock(side_effect=guard.click_record.clear)
    return guard


class FrameWorld:
    def __init__(self, task, names, frames, namespace=None):
        self.now = 1.0
        self.index = -1
        self.frames = frames
        self.clicks = []
        self.guard = None
        clock = methods('module/base/timer.py', 'Timer',
                        ['__init__', 'start', 'started', 'reached', 'reset'],
                        dict(time=SimpleNamespace(time=lambda: self.now)))
        self.task = subject(task, names, dict(Timer=clock, **(namespace or {})))()
        for name in assets(task):
            setattr(self.task, name, name)
        self.task.screenshot = Mock(side_effect=self.screenshot)
        self.task.appear = Mock(side_effect=lambda rule: rule in self.frame)
        self.task.click = Mock(side_effect=self.click)
        self.task.appear_then_click = Mock(side_effect=self.appear_then_click)
        self.task.goto_page = Mock()

    @property
    def frame(self):
        return self.frames[max(0, self.index)]

    def screenshot(self):
        self.now += 1
        self.index = min(self.index + 1, len(self.frames) - 1)
        if self.task.screenshot.call_count > 100:
            raise AssertionError('Recovery did not stop')

    def click(self, rule, **kwargs):
        if self.guard is not None:
            self.guard.click_record_add(rule)
            self.guard.click_record_check()
        self.clicks.append(rule)
        return True

    def appear_then_click(self, rule, **kwargs):
        return self.click(rule) if rule in self.frame else False


class RecognitionTests(unittest.TestCase):
    def is_visible(self, task, image):
        method = 'painting_dialogue_visible' if task == 'Delegation' else 'normal_secret_visible'
        obj = subject(task, [method])()
        for name, rule in assets(task).items():
            setattr(obj, name, rule)
        obj.appear = Replay(image).appear
        return getattr(obj, method)()

    def test_both_independent_anchors_are_required(self):
        for task, names in [('Delegation', ('I_STORY_MAP', 'I_STORY_PANEL')),
                            ('Secret', ('I_NORMAL_TITLE', 'I_NORMAL_CLOSE'))]:
            rules = assets(task)
            for count in (0, 1, 2):
                image = np.zeros((720, 1280, 3), dtype=np.uint8)
                for name in names[:count]:
                    rule = rules[name]
                    template = cv2.imread(str(ROOT / rule.file))
                    x, y = rule.roi_front[:2]
                    image[y:y+template.shape[0], x:x+template.shape[1]] = template
                self.assertEqual(self.is_visible(task, image), count == 2)
            image = np.zeros((720, 1280, 3), dtype=np.uint8)
            rule = rules[names[1]]
            template = cv2.imread(str(ROOT / rule.file))
            x, y = rule.roi_front[:2]
            image[y:y+template.shape[0], x:x+template.shape[1]] = template
            self.assertFalse(self.is_visible(task, image))

    def test_saved_errors_match_only_their_own_recovery(self):
        paths = list((ROOT / 'log/error').glob('*/*.png'))
        if not paths:
            self.skipTest('No local error screenshots')
        seen = set()
        for path in paths:
            image = cv2.imread(str(path))
            if image.shape != (720, 1280, 3):
                continue
            folder = path.parent.name
            seen.add(folder)
            with self.subTest(image=folder):
                self.assertEqual(self.is_visible('Delegation', image), folder in DELEGATION_ERRORS)
                self.assertEqual(self.is_visible('Secret', image), folder == SECRET_ERROR)
        self.assertTrue(set(DELEGATION_ERRORS + (SECRET_ERROR,)).issubset(seen))

    def test_recovery_assets_keep_metadata_and_generated_rules_in_sync(self):
        for task, folder in [('Delegation', 'story'), ('Secret', 'recovery')]:
            rules = assets(task)
            for record in json.loads((ROOT/'tasks'/task/folder/'image.json').read_text(encoding='utf-8')):
                rule = rules['I_' + record['itemName'].upper()]
                self.assertEqual(rule.roi_back, tuple(map(int, record['roiBack'].split(','))))
                self.assertEqual(rule.threshold, 0.9)


class DelegationFlowTests(unittest.TestCase):
    def world(self, frames):
        world = FrameWorld('Delegation', ['check_reward', 'painting_dialogue_visible'], frames)
        world.task.click_completed_delegation = Mock(return_value=False)
        return world

    def test_dialogue_advances_then_existing_reward_flow_resumes(self):
        world = self.world([{'I_STORY_MAP', 'I_STORY_PANEL'},
                            {'I_STORY_MAP', 'I_STORY_PANEL'},
                            {'I_REWARDS_DONE'}, {'I_REWARDS_GET'}, {'I_REWARDS_MIN'}])
        world.task.check_reward()
        self.assertEqual(world.clicks, ['C_STORY_CONTINUE', 'C_STORY_CONTINUE',
                                       'I_REWARDS_DONE', 'I_REWARDS_GET'])

    def test_known_dialogue_precedes_sidebar_controls(self):
        world = self.world([{'I_STORY_MAP', 'I_STORY_PANEL', 'I_REWARDS_CHAT'},
                            {'I_REWARDS_MIN'}])
        world.task.check_reward()
        self.assertEqual(world.clicks, ['C_STORY_CONTINUE'])

    def test_unresponsive_dialogue_defers_before_actual_device_click_guard(self):
        world = self.world([{'I_STORY_MAP', 'I_STORY_PANEL'}])
        world.guard = device_click_guard()
        with self.assertRaises(Deferred):
            world.task.check_reward()
        self.assertEqual(world.clicks, ['C_STORY_CONTINUE'] * 8)
        self.assertEqual(list(world.guard.click_record), world.clicks)
        world.guard.click_record_clear.assert_not_called()

    def test_unknown_page_defers_without_clicking(self):
        world = self.world([set()])
        with self.assertRaises(Deferred):
            world.task.check_reward()
        self.assertEqual(world.clicks, [])
        self.assertLess(world.now, 50)

    def test_completed_story_is_collected_before_attempting_dispatch(self):
        world = FrameWorld('Delegation', ['delegate_one', 'painting_dialogue_visible'],
                           [{'I_STORY_MAP', 'I_STORY_PANEL'}])
        world.task.O_D_NAME = SimpleNamespace(keyword=None)
        world.task.ocr_appear = Mock(side_effect=[True, False])
        world.task.check_reward = Mock()
        self.assertFalse(world.task.delegate_one('画'))
        world.task.check_reward.assert_called_once_with()
        self.assertEqual(world.clicks, [])

    def test_regular_assignment_still_selects_the_existing_shikigami_slots(self):
        world = FrameWorld('Delegation', ['delegate_one', 'painting_dialogue_visible'],
                           [set(), {'I_D_START'}, set(), {'I_D_SELECT_1'},
                            set(), {'I_D_SELECT_2'}, set(), {'I_D_SELECT_3'},
                            set(), {'I_D_SELECT_4'}, set()])
        world.task.O_D_NAME = SimpleNamespace(keyword=None)
        world.task.ocr_appear = Mock(return_value=True)
        world.task.check_reward = Mock()
        world.task.delegate_one('画')
        self.assertEqual(world.clicks, ['C_D_1', 'C_D_2', 'C_D_3', 'C_D_4'])
        world.task.check_reward.assert_not_called()

    def test_unknown_assignment_view_defers_without_reporting_completion(self):
        world = FrameWorld('Delegation', ['delegate_one', 'painting_dialogue_visible'], [set()])
        world.task.O_D_NAME = SimpleNamespace(keyword=None)
        world.task.ocr_appear = Mock(return_value=True)
        world.task.ocr_appear_click = Mock(return_value=False)
        with self.assertRaises(Deferred):
            world.task.delegate_one('画')
        self.assertEqual(world.clicks, [])


class SecretFlowTests(unittest.TestCase):
    wrong = {'I_NORMAL_TITLE', 'I_NORMAL_CLOSE'}
    ready = {'I_SE_FIRE', 'I_SE_PLACEMENT'}

    def world(self, frames):
        return FrameWorld('Secret', ['normal_secret_visible', 'enter_weekly_secret'],
                          frames, dict(page_main='main', page_secret_zones='secret'))

    def test_regular_weekly_entry_needs_no_recovery(self):
        world = self.world([{'I_SE_ENTER'}, self.ready])
        world.task.enter_weekly_secret()
        self.assertEqual(world.clicks, ['I_SE_ENTER'])
        world.task.goto_page.assert_not_called()

    def test_known_wrong_dungeon_closes_and_reopens_before_continuing(self):
        world = self.world([self.wrong, self.wrong, set(), self.ready])
        world.task.enter_weekly_secret()
        self.assertEqual(world.clicks, ['C_NORMAL_CLOSE'])
        self.assertEqual([c.args[0] for c in world.task.goto_page.call_args_list], ['main', 'secret'])

    def test_repeated_wrong_dungeon_is_not_marked_done(self):
        world = self.world([self.wrong, set(), self.wrong, set(), self.wrong])
        with self.assertRaises(Deferred):
            world.task.enter_weekly_secret()
        self.assertEqual(world.task.goto_page.call_count, 4)

    def test_missing_weekly_marker_and_unknown_screen_are_not_success(self):
        for frame in (set(), {'I_SE_FIRE'}, {'I_SE_PLACEMENT'}):
            world = self.world([frame])
            with self.assertRaises(Deferred):
                world.task.enter_weekly_secret()
            self.assertEqual(world.clicks, [])
            self.assertLess(world.now, 15)

    def test_close_button_that_does_not_respond_is_bounded(self):
        world = self.world([self.wrong])
        world.guard = device_click_guard()
        with self.assertRaises(Deferred):
            world.task.enter_weekly_secret()
        self.assertEqual(len(world.clicks), 3)
        self.assertTrue(all(click == 'C_NORMAL_CLOSE' for click in world.clicks))
        world.task.goto_page.assert_not_called()
        world.guard.click_record_clear.assert_not_called()

    def test_unresponsive_entry_defers_before_actual_device_click_guard(self):
        world = self.world([{'I_SE_ENTER'}])
        world.guard = device_click_guard()
        with self.assertRaises(Deferred):
            world.task.enter_weekly_secret()
        self.assertEqual(world.clicks, ['I_SE_ENTER'] * 3)
        self.assertEqual(list(world.guard.click_record), world.clicks)
        world.task.goto_page.assert_not_called()
        world.guard.click_record_clear.assert_not_called()

    def test_entry_failure_never_sets_weekly_completion_or_starts_battle(self):
        task = subject('Secret', ['run'], dict(page_secret_zones='secret'))()
        task.config = SimpleNamespace(secret=SimpleNamespace(secret_config=SimpleNamespace(),
            switch_soul=SimpleNamespace(enable=False, enable_switch_by_name=False)))
        task.before_run = Mock()
        task.check_time = Mock()
        task.goto_page = Mock()
        task.enter_weekly_secret = Mock(side_effect=Deferred('entry failed'))
        task.set_next_run = Mock()
        task.run_general_battle = Mock()
        with self.assertRaises(Deferred):
            task.run()
        task.set_next_run.assert_not_called()
        task.run_general_battle.assert_not_called()


if __name__ == '__main__':
    unittest.main()
