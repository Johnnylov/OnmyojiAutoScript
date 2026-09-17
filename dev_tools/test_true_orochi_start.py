"""Replay the supplied occupied room and configuration page across two tasks."""
from concurrent.futures import ThreadPoolExecutor
from datetime import time
from pathlib import Path
from threading import Lock
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from module.atom.ocr import RuleOcr
from tasks.TrueOrochi.config import TrueOrochi
from tasks.TrueOrochi.script_task import ScriptTask
from tasks.TrueOrochi.team import LocalTeam
from tasks.TrueOrochi.view import TrueOrochiView


FIXTURES = Path(__file__).with_name('fixtures') / 'true_orochi'


class TrueOrochiStartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from module.ocr.ppocr import TextSystem
        cls.ocr = TextSystem()
        cls.images = {name: np.array(Image.open(FIXTURES / f'{name}.png').convert('RGB'))
                      for name in ('room_joined', 'prepare')}

    def offline_ocr(self, rule, image):
        x, y, w, h = rule.roi
        return self.ocr.detect_and_ocr(image[y:y+h, x:x+w])

    def test_room_name_uses_full_name_not_leader_or_partial_match(self):
        task = ScriptTask.__new__(ScriptTask)
        task.config = SimpleNamespace(true_orochi=TrueOrochi())
        task.device = SimpleNamespace(image=self.images['room_joined'])
        panel = TrueOrochiView(task.device.image).room()
        with patch.object(RuleOcr, 'detect_and_ocr', lambda rule, image: self.offline_ocr(rule, image)):
            for name, expected in [('到相思没处辞', True), ('到相思', False),
                                   ('到相思没处辞的分身', False), ('黑之951', False)]:
                with self.subTest(name=name):
                    task.config.true_orochi.invite_config.friend_list = name
                    self.assertEqual(task._room_friend_present(panel), expected)

    def test_two_profiles_challenge_and_ready_when_member_misses_room_footer(self):
        for size in (None, (1280, 720)):
            with self.subTest(size=size), tempfile.TemporaryDirectory() as directory:
                self.replay(directory, size)

    def replay(self, directory, size):
        images = {name: cv2.resize(image, size) if size else image
                  for name, image in self.images.items()}
        # Simulate the missing room acknowledgement from the actual log. A
        # hidden target footer makes the member unable to recognize this room,
        # while the leader still sees the supplied occupied-room screenshot.
        member_room = images['room_joined'].copy()
        h, w = member_room.shape[:2]
        member_room[round(h*.86):] = 45
        prepared = images['prepare'].copy()
        prepared[round(h*.72):, round(w*.84):] = 45
        state = SimpleNamespace(fired=False, prepared=set(), settled=set())
        lock = Lock()
        actions = []
        tasks = []

        for name, peer in (('a', 'b'), ('b', 'a')):
            task = ScriptTask.__new__(ScriptTask)
            task.config = SimpleNamespace(config_name=name, true_orochi=TrueOrochi())
            task.config.true_orochi.invite_config.friend_list = '到相思没处辞' if name == 'a' else '黑之951'
            task.config.true_orochi.invite_config.wait_time = time(second=15)
            task._team_sync = LocalTeam(name, peer, 'a', 'true_orochi_leader_twice', directory)
            task._true_battling = False
            task.device = SimpleNamespace(image=images['room_joined'], detect_record=set(),
                                          stuck_record_add=Mock(), stuck_record_clear=Mock(),
                                          click_record_clear=Mock())

            def screenshot(task=task, name=name):
                task._team_sync.heartbeat()
                with lock:
                    if not state.fired:
                        image = images['room_joined'] if name == 'a' else member_room
                    elif name in state.settled:
                        image = images['room_joined']
                    elif len(state.prepared) == 2:
                        image = np.zeros_like(images['prepare'])
                    elif name in state.prepared:
                        image = prepared
                    else:
                        image = images['prepare']
                    task._reward_visible = len(state.prepared) == 2 and name not in state.settled
                task.device.image = image
                task._true_view = TrueOrochiView(image)

            def appear(rule, task=task, **_):
                # Only settlement is simulated. Room, identity, and Ready must
                # be recognized from pixels without legacy marker shortcuts.
                return rule == task.I_GREED_GHOST and task._reward_visible

            def click_marker(rule, task=task, name=name, **_):
                if rule == task.I_GREED_GHOST and task._reward_visible:
                    with lock:
                        state.settled.add(name)
                    return True
                return False

            def tap(region, control, task=task, name=name):
                x, y, width, height = region
                ih, iw = task.device.image.shape[:2]
                self.assertTrue(0 <= x < x+width <= iw and 0 <= y < y+height <= ih)
                self.assertGreater(x/iw, .85)
                self.assertGreater(y/ih, .75)
                with lock:
                    actions.append((name, control))
                    if control == 'TRUE_OROCHI_ROOM_FIRE':
                        self.assertEqual(name, 'a')
                        self.assertFalse(state.fired)
                        state.fired = True
                    elif control == 'TRUE_OROCHI_PREPARE':
                        self.assertTrue(state.fired)
                        state.prepared.add(name)
                    else:
                        self.fail(f'Unexpected control: {control}')
                return True

            task.screenshot = Mock(side_effect=screenshot)
            task.appear = Mock(side_effect=appear)
            task.appear_then_click = Mock(side_effect=click_marker)
            task.is_in_real_battle = Mock(return_value=False)
            task._at_orochi_or_home = Mock(return_value=False)
            task._tap = Mock(side_effect=tap)
            task._invite_true_friend = Mock(side_effect=AssertionError('Partner is already in the room'))
            tasks.append(task)

        try:
            for task in tasks:
                task._team_sync.ready(0, 2, 2)

            def run(task):
                if task.config.config_name == 'a':
                    task._start_true_room(0)
                else:
                    task._wait_true_invitation(0)
                return task.run_true_orochi_battle()

            with patch.object(RuleOcr, 'detect_and_ocr', lambda rule, image: self.offline_ocr(rule, image)):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = [pool.submit(run, task) for task in tasks]
                    self.assertEqual([future.result(timeout=20) for future in results], [True, True])
            self.assertEqual(actions[0], ('a', 'TRUE_OROCHI_ROOM_FIRE'))
            self.assertCountEqual(actions[1:], [('a', 'TRUE_OROCHI_PREPARE'), ('b', 'TRUE_OROCHI_PREPARE')])
            self.assertEqual(tasks[0]._team_sync.round_state(0)['joined'], ['b'])
        finally:
            for task in tasks:
                task._team_sync.close()


if __name__ == '__main__':
    unittest.main()
