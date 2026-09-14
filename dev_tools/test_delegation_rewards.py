"""Offline Delegation reward regressions; never connects to a game or RPC server.

Optional snapshot replay: DELEGATION_REPLAY_IMAGES is a list of local PNG paths
separated by os.pathsep. Screenshots are not copied into the repository.
"""

import ast
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import numpy as np

from test_image_template_guard import methods


ROOT = Path(__file__).resolve().parents[1]


def completion_rule():
    tree = ast.parse((ROOT / 'tasks/Delegation/assets.py').read_text(encoding='utf-8'))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    assignment = next(node for node in cls.body if isinstance(node, ast.Assign)
                      and node.targets[0].id == 'O_D_DONE')
    values = {kw.arg: ast.literal_eval(kw.value) for kw in assignment.value.keywords}
    return SimpleNamespace(**values)


def result(text, x, y, width=48, height=27):
    return SimpleNamespace(ocr_text=text, box=np.array(
        [[x, y], [x + width, y], [x + width, y + height], [x, y + height]],
        dtype=float))


class World:
    def __init__(self, frames):
        self.now = 1.0
        self.index = -1
        self.frames = frames
        self.clicks = []
        self.last_click = {}
        timer = methods('module/base/timer.py', 'Timer',
                        ['__init__', 'start', 'started', 'reached', 'reset'],
                        dict(time=SimpleNamespace(time=lambda: self.now)))
        subject = methods('tasks/Delegation/script_task.py', 'ScriptTask',
                          ['click_completed_delegation', 'check_reward'],
                          dict(RuleClick=SimpleNamespace, Timer=timer))
        self.task = subject()
        self.task.O_D_DONE = completion_rule()
        self.task.O_D_DONE.detect_and_ocr = Mock(side_effect=self.detect)
        self.task.device = SimpleNamespace(image=None)
        names = ('I_REWARDS_GET', 'I_REWARDS_CHAT', 'I_CHAT_1', 'I_CHAT_2',
                 'I_REWARDS_DONE', 'I_REWARDS_FALSE', 'I_REWARDS_MIN')
        for name in names:
            setattr(self.task, name, name)
        self.task.screenshot = Mock(side_effect=self.screenshot)
        self.task.appear = Mock(side_effect=lambda name: name in self.frame)
        self.task.appear_then_click = Mock(side_effect=self.appear_then_click)
        self.task.click = Mock(side_effect=self.click)

    @property
    def frame(self):
        return self.frames[max(self.index, 0)]

    def screenshot(self):
        self.now += 0.6
        self.index = min(self.index + 1, len(self.frames) - 1)
        if self.task.screenshot.call_count > 40:
            raise AssertionError('Reward collection never settled')

    def detect(self, image):
        x, y, w, h = self.task.O_D_DONE.roi
        detected = []
        for box in self.frame.get('text', []):
            if (x <= box.box[0, 0] and box.box[2, 0] <= x + w
                    and y <= box.box[0, 1] and box.box[2, 1] <= y + h):
                detected.append(SimpleNamespace(ocr_text=box.ocr_text,
                                                box=box.box - [x, y]))
        return detected

    def click(self, action, interval):
        if self.now - self.last_click.get(action.name, -100) < interval:
            return False
        self.last_click[action.name] = self.now
        self.clicks.append((action.name, action.roi_front))
        return True

    def appear_then_click(self, name, interval):
        if name in self.frame:
            self.clicks.append((name, None))
            return True
        return False


def map_frame(*boxes):
    return {'I_REWARDS_MIN': True, 'text': list(boxes)}


class DelegationRewardTests(unittest.TestCase):
    def test_sidebar_completion_banner_is_excluded_from_detection(self):
        world = World([map_frame(result('完成', 996, 137, 56, 32))])
        world.task.check_reward()
        self.assertEqual(world.clicks, [])
        self.assertLess(world.task.screenshot.call_count, 10)

    def test_map_marker_is_clicked_when_sidebar_has_same_completion_text(self):
        world = World([map_frame(result('完成', 996, 137, 56, 32),
                                 result('完成', 440, 631))])
        self.assertTrue(world.task.click_completed_delegation())
        self.assertEqual(world.clicks, [('d_done', (440, 631, 48, 27))])

    def test_multiple_map_markers_are_not_merged_into_an_empty_click_area(self):
        world = World([map_frame(result('完成', 210, 260), result('完成', 670, 590))])
        self.assertTrue(world.task.click_completed_delegation())
        self.assertEqual(world.clicks, [('d_done', (210, 260, 48, 27))])

    def test_partial_and_unrelated_text_is_not_clicked(self):
        world = World([map_frame(result('未完成', 210, 260), result('御魂', 440, 350),
                                 result('完', 440, 500))])
        self.assertFalse(world.task.click_completed_delegation())
        self.assertEqual(world.clicks, [])

    def test_marker_click_is_throttled_while_waiting_for_response(self):
        world = World([map_frame(result('完成', 440, 631))])
        self.assertTrue(world.task.click_completed_delegation())
        self.assertFalse(world.task.click_completed_delegation())
        self.assertEqual(len(world.clicks), 1)

    def test_completed_mission_claims_dialog_and_reward_before_finishing(self):
        world = World([map_frame(result('完成', 440, 631)),
                       {'I_REWARDS_DONE': True}, {'I_REWARDS_GET': True}, map_frame()])
        world.task.check_reward()
        self.assertEqual([name for name, _ in world.clicks],
                         ['d_done', 'I_REWARDS_DONE', 'I_REWARDS_GET'])

    def test_each_completed_marker_is_handled_after_previous_reward(self):
        world = World([map_frame(result('完成', 210, 260), result('完成', 670, 590)),
                       {'I_REWARDS_DONE': True}, {'I_REWARDS_GET': True},
                       map_frame(result('完成', 670, 590)),
                       {'I_REWARDS_DONE': True}, {'I_REWARDS_FALSE': True}, map_frame()])
        world.task.check_reward()
        self.assertEqual([area for name, area in world.clicks if name == 'd_done'],
                         [(210, 260, 48, 27), (670, 590, 48, 27)])

    def test_chat_steps_remain_ahead_of_map_marker_detection(self):
        world = World([{'I_REWARDS_CHAT': True}, {'I_CHAT_1': True}, {'I_CHAT_2': True},
                       {'I_REWARDS_DONE': True}, {'I_REWARDS_GET': True}, map_frame()])
        world.task.check_reward()
        self.assertEqual([name for name, _ in world.clicks],
                         ['I_REWARDS_CHAT', 'I_CHAT_1', 'I_CHAT_2',
                          'I_REWARDS_DONE', 'I_REWARDS_GET'])

    def test_returning_to_map_checks_marker_even_after_dialog_timeout(self):
        world = World([{}, {}, {}, {}, {}, {}, map_frame(result('完成', 440, 631)),
                       {'I_REWARDS_DONE': True}, {'I_REWARDS_GET': True}, map_frame()])
        world.task.check_reward()
        self.assertEqual([name for name, _ in world.clicks],
                         ['d_done', 'I_REWARDS_DONE', 'I_REWARDS_GET'])

    def test_asset_and_source_metadata_agree_and_cover_visible_map(self):
        source = json.loads((ROOT / 'tasks/Delegation/rewards/ocr.json').read_text(encoding='utf-8'))[0]
        rule = completion_rule()
        self.assertEqual(rule.roi, tuple(map(int, source['roiFront'].split(','))))
        self.assertEqual(rule.area, tuple(map(int, source['roiBack'].split(','))))
        x, y, w, h = rule.roi
        self.assertLessEqual(x, 440)
        self.assertGreaterEqual(y + h, 658)
        self.assertLessEqual(x + w, 968)  # Sidebar's left edge in the error frames.


@unittest.skipUnless(os.environ.get('DELEGATION_REPLAY_IMAGES'), 'No local error snapshots requested')
class DelegationSnapshotTests(unittest.TestCase):
    def test_saved_frames_detect_and_click_actual_map_completion_marker(self):
        import cv2
        from module.ocr.ppocr import TextSystem

        model = TextSystem(ort_providers=['CPUExecutionProvider'])
        # Use production cropping, OCR thresholding, and result processing with
        # a local model in place of the RPC client.
        base = methods('module/ocr/base_ocr.py', 'BaseCor',
                       ['crop', 'pre_process', 'after_process', 'detect_and_ocr'],
                       dict(time=SimpleNamespace(time=lambda: 1.0), logger=Mock(),
                            float2str=str, enlarge_canvas=self.enlarge_canvas))
        reader = base()
        reader.roi = completion_rule().roi
        reader.model = model
        reader.score = 0.6
        reader.name = 'D_DONE'
        for image_path in os.environ['DELEGATION_REPLAY_IMAGES'].split(os.pathsep):
            with self.subTest(image=Path(image_path).name):
                frame = cv2.imread(image_path)
                self.assertIsNotNone(frame)
                world = World([map_frame()])
                world.task.device.image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                world.task.O_D_DONE.detect_and_ocr = reader.detect_and_ocr
                self.assertTrue(world.task.click_completed_delegation())
                _, (x, y, w, h) = world.clicks[0]
                self.assertTrue(430 <= x < x + w <= 500)
                self.assertTrue(620 <= y < y + h <= 668)

    @staticmethod
    def enlarge_canvas(image):
        import cv2
        height, width = image.shape[:2]
        length = int(max(width, height) // 32 * 32 + 32)
        return cv2.copyMakeBorder(image, 0, length - height, 0, length - width,
                                  cv2.BORDER_CONSTANT, value=(0, 0, 0))


if __name__ == '__main__':
    unittest.main()
