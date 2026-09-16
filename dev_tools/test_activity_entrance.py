"""Offline activity-entry replay, including real right-menu strips and navigation."""

from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tasks.ActivityShikigami.assets import ActivityShikigamiAssets
from tasks.ActivityShikigami.entrance import BRIGHT_ACTIVITY_ENTRY, enter_activity
from tasks.ActivityShikigami.page import page_act
from tasks.Component.RightActivity.assets import RightActivityAssets
from tasks.GameUi.navigator import GameUi
from tasks.GameUi.page import page_main


LEGACY = ActivityShikigamiAssets.I_MAIN_GOTO_ACT
TOGGLE = RightActivityAssets.I_TOGGLE_BUTTON


def blank():
    return np.zeros((720, 1280, 3), dtype=np.uint8)


def template(marker):
    image = cv2.imread(str(ROOT / marker.file))
    assert image is not None
    return image


def paste(image, marker, position):
    x, y = position
    crop = template(marker)
    h, w = crop.shape[:2]
    image[y:y+h, x:x+w] = crop


def reference(index):
    path = ROOT / f'dev_tools/fixtures/activity_entrance/2026-09-16_right_menu_{index}.png'
    crop = cv2.imread(str(path))
    assert crop is not None and crop.shape == (453, 83, 3)
    image = blank()
    image[134:587, 1164:1247] = crop
    return image


class Frames:
    def __init__(self, image, main=True):
        self.device = SimpleNamespace(image=image)
        self.main = main
        self.clicks = []
        self.scores = {}
        self.rois = {}
        self.throttled = set()
        self.missing = set()

    def match_page_once(self, page):
        assert page is page_main
        return self.main

    def appear(self, marker):
        if marker.name in self.missing:
            return False
        x, y, w, h = marker.roi_back
        crop = self.device.image[y:y+h, x:x+w]
        target = template(marker)
        if crop.shape[0] < target.shape[0] or crop.shape[1] < target.shape[1]:
            return False
        _, score, _, point = cv2.minMaxLoc(cv2.matchTemplate(crop, target, cv2.TM_CCOEFF_NORMED))
        self.scores[marker.name] = score
        if score <= marker.threshold:
            return False
        self.rois[marker.name] = (x+point[0], y+point[1], target.shape[1], target.shape[0])
        return True

    def appear_then_click(self, marker, interval):
        if marker.name in self.throttled or not self.appear(marker):
            return False
        self.clicks.append((marker.name, self.rois[marker.name], interval))
        return True


class ActivityEntranceTests(unittest.TestCase):
    def test_all_logged_final_frames_have_visible_legacy_and_bright_entry(self):
        # These are post-toggle final frames, not evidence that the old marker
        # was invisible throughout the preceding six-second action attempts.
        for index in (1, 2, 3):
            with self.subTest(frame=index):
                task = Frames(reference(index))
                self.assertTrue(task.appear(LEGACY))
                self.assertTrue(task.appear(BRIGHT_ACTIVITY_ENTRY))
                self.assertGreater(task.scores[BRIGHT_ACTIVITY_ENTRY.name], 0.93)
                self.assertTrue(enter_activity(task))
                self.assertEqual([name for name, _, _ in task.clicks], [LEGACY.name])

    def test_bright_variant_uses_its_detected_position_when_legacy_misses(self):
        task = Frames(reference(3))
        # Isolate the fallback branch; only the new marker's recognition uses
        # the real frame here. The source frames also match the old marker.
        task.missing.add(LEGACY.name)
        self.assertTrue(enter_activity(task))
        self.assertEqual(task.clicks, [(BRIGHT_ACTIVITY_ENTRY.name, (1179, 207, 57, 68), 0.8)])

    def test_legacy_template_remains_usable_without_new_variant(self):
        image = blank()
        paste(image, LEGACY, (1190, 448))
        task = Frames(image)
        self.assertFalse(task.appear(BRIGHT_ACTIVITY_ENTRY))
        self.assertTrue(enter_activity(task))
        self.assertEqual(task.clicks[0][1], (1190, 448, 46, 32))
        self.assertEqual(LEGACY.threshold, 0.7)

    def test_bright_variant_follows_other_right_menu_positions(self):
        image = blank()
        paste(image, BRIGHT_ACTIVITY_ENTRY, (1172, 445))
        task = Frames(image)
        task.missing.add(LEGACY.name)
        self.assertTrue(enter_activity(task))
        self.assertEqual(task.clicks[0][1], (1172, 445, 57, 68))

    def test_visible_entry_on_cooldown_does_not_toggle_the_menu(self):
        for marker in (LEGACY, BRIGHT_ACTIVITY_ENTRY):
            with self.subTest(marker=marker.name):
                task = Frames(reference(2))
                if marker is BRIGHT_ACTIVITY_ENTRY:
                    task.missing.add(LEGACY.name)
                task.throttled.add(marker.name)
                self.assertFalse(enter_activity(task))
                self.assertEqual(task.clicks, [])

    def test_no_activity_entry_only_advances_menu_and_does_not_claim_arrival(self):
        image = reference(1)
        image[190:285, 1164:1247] = 0
        task = Frames(image)
        self.assertFalse(enter_activity(task))
        self.assertEqual([name for name, _, _ in task.clicks], [TOGGLE.name])
        self.assertEqual(task.clicks[0][2], 2)

    def test_center_icon_and_other_event_icons_cannot_replace_right_entry(self):
        image = reference(2)
        image[190:285, 1164:1247] = 0
        paste(image, BRIGHT_ACTIVITY_ENTRY, (450, 225))
        paste(image, LEGACY, (480, 180))
        task = Frames(image)
        self.assertFalse(task.appear(LEGACY))
        self.assertFalse(task.appear(BRIGHT_ACTIVITY_ENTRY))
        self.assertFalse(enter_activity(task))
        self.assertEqual([name for name, _, _ in task.clicks], [TOGGLE.name])

    def test_unknown_or_departed_courtyard_never_clicks_entry_or_toggle(self):
        for image in (reference(1), blank()):
            task = Frames(image, main=False)
            task.appear = Mock(side_effect=AssertionError('unknown pages must not inspect menu controls'))
            self.assertFalse(enter_activity(task))
            self.assertEqual(task.clicks, [])

    def test_blank_courtyard_has_no_blind_click(self):
        task = Frames(blank())
        self.assertFalse(enter_activity(task))
        self.assertEqual(task.clicks, [])

    def test_graph_has_no_global_activity_toggle_failure_hook(self):
        edge = next(edge for edge in page_main.transitions if edge.key == 'page_main->page_act')
        self.assertIs(edge.action, enter_activity)
        self.assertNotIn(TOGGLE, page_act.on_enter_failure)
        self.assertNotIn(TOGGLE, edge.on_enter_failure)

    def test_transition_finds_revealed_entry_on_next_frame_before_failure_hooks(self):
        before = reference(1)
        before[190:285, 1164:1247] = 0
        after = reference(2)
        task = Frames(before)
        task.now = 0
        task.pending_frame = before
        task.frames = []
        old_click = task.appear_then_click
        def click(marker, interval):
            delivered = old_click(marker, interval)
            if delivered and marker is TOGGLE:
                task.pending_frame = after
            return delivered
        task.appear_then_click = click
        def maybe_screenshot(skip):
            if not skip:
                task.now += 0.3
                task.device.image = task.pending_frame
                task.frames.append(task.device.image)
        task.maybe_screenshot = maybe_screenshot
        task._invoke_callable = lambda action: action(task)
        task._execute_action = GameUi._execute_action.__get__(task)
        task._wait_for_destination = Mock(return_value=True)
        task._run_hooks = Mock()
        task._mark_page_entered = Mock()
        task.navigator = SimpleNamespace(add_penalty=Mock())
        class Timer:
            def __init__(self, limit):
                self.limit = limit
            def start(self):
                self.start_time = task.now
                return self
            def reached(self):
                return task.now - self.start_time >= self.limit
        edge = next(edge for edge in page_main.transitions if edge.key == 'page_main->page_act')
        with patch('tasks.GameUi.navigator.Timer', Timer):
            self.assertTrue(GameUi._execute_transition(task, edge))
        self.assertEqual(len(task.frames), 2)
        self.assertEqual([name for name, _, _ in task.clicks], [TOGGLE.name, LEGACY.name])
        task.navigator.add_penalty.assert_not_called()
        task._wait_for_destination.assert_called_once_with(page_act)
        task._mark_page_entered.assert_called_once_with(page_act)
        self.assertFalse(any(args.args[0] is page_act.on_enter_failure
                             for args in task._run_hooks.call_args_list))

    def test_missing_entry_keeps_original_transition_deadline_and_toggle_cooldown(self):
        image = reference(1)
        image[190:285, 1164:1247] = 0
        task = Frames(image)
        task.now = 0.0
        last_click = {}
        original_click = task.appear_then_click
        def click(marker, interval):
            if task.now - last_click.get(marker.name, -100) < interval:
                return False
            delivered = original_click(marker, interval)
            if delivered:
                last_click[marker.name] = task.now
            return delivered
        task.appear_then_click = click
        def execute(action, **kwargs):
            task.now += 0.3
            return action(task)
        task._execute_action = execute
        task._run_hooks = Mock()
        task._wait_for_destination = Mock()
        task._navigation_detect_categories = Mock(return_value={'global', 'activity_shikigami'})
        task._detect_current_page_with_fallback = Mock(return_value=page_main)
        task.navigator = SimpleNamespace(add_penalty=Mock(return_value=1.0))
        class Timer:
            def __init__(self, limit):
                self.limit = limit
            def start(self):
                self.start_time = task.now
                return self
            def reached(self):
                return task.now - self.start_time >= self.limit
        edge = next(edge for edge in page_main.transitions if edge.key == 'page_main->page_act')
        with patch('tasks.GameUi.navigator.Timer', Timer):
            self.assertFalse(GameUi._execute_transition(task, edge))
        self.assertLessEqual(task.now, 6.3)
        self.assertEqual([name for name, _, _ in task.clicks], [TOGGLE.name] * 3)
        task.navigator.add_penalty.assert_called_once_with(edge)
        task._wait_for_destination.assert_not_called()


if __name__ == '__main__':
    unittest.main()
