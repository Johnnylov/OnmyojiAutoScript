"""True Orochi tab adaptation with the unchanged shared Orochi name selector."""
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tasks.Component.GeneralInvite import general_invite as shared
from tasks.TrueOrochi import script_task as runtime
from tasks.TrueOrochi.config import TrueOrochi
from tasks.TrueOrochi.script_task import ScriptTask, TrueOrochiError


def result(text, x, y, w=65, h=35):
    return SimpleNamespace(ocr_text=text, box=np.array([
        [x, y], [x+w, y], [x+w, y+h], [x, y+h],
    ], dtype=float))


class TrueOrochiInviteTests(unittest.TestCase):
    def world(self, target_tab=0, select_works=True, confirm_closes=True):
        task = ScriptTask.__new__(ScriptTask)
        state = SimpleNamespace(now=0, tab=0, opened=True, selected=False)
        actions = []
        class Timer:
            def __init__(self, limit):
                self.limit = limit
            def start(self):
                self.start_time = state.now
                return self
            def reached(self):
                return state.now-self.start_time >= self.limit
        def sleep(seconds):
            state.now += seconds
        for module in (runtime, shared):
            for name, value in (('Timer', Timer), ('sleep', sleep)):
                mocked = patch.object(module, name, value)
                mocked.start()
                self.addCleanup(mocked.stop)
        task.config = SimpleNamespace(true_orochi=TrueOrochi())
        task.config.true_orochi.invite_config.friend_list = '目标好友'
        task.device = SimpleNamespace(image=np.zeros((720, 1280, 3), dtype=np.uint8), image_frame_id=0)
        task._true_view = SimpleNamespace(room=lambda: not state.opened)
        def screenshot():
            state.now += .25
            task.device.image_frame_id += 1
        task.screenshot = Mock(side_effect=screenshot)
        task.O_TRUE_FRIEND_TABS = SimpleNamespace(
            roi=(350, 85, 580, 65), detect_and_ocr=Mock(return_value=[
                result('察友', 22, 8), result('好友', 139, 12),
                result('跨区', 254, 12), result('大蛇印记拥有者', 362, 13, w=206),
            ]))
        task.I_SELECTED = SimpleNamespace(match_all_any=Mock(
            side_effect=lambda *a, **kw: [object()] if state.selected else []))
        def names(_):
            found = [result('目标好友的分身', 8, 65, w=150, h=23)]
            if state.tab == target_tab:
                found.append(result('目 标 好 友', 8, 14, w=135, h=23))
            return found
        task.O_FRIEND_NAME_1 = SimpleNamespace(roi=(434, 185, 189, 345), name='friend_name_1',
                                             detect_and_ocr=Mock(side_effect=names))
        task.O_FRIEND_NAME_2 = SimpleNamespace(roi=(729, 184, 196, 346), name='friend_name_2',
                                             detect_and_ocr=Mock(return_value=[]))
        def click(x, y, control_name):
            self.assertTrue(442 <= x <= 577 and 199 <= y <= 222, (x, y))
            actions.append('select_exact_name')
            state.selected = select_works
        task.device.click = Mock(side_effect=click)
        def tap(region, name):
            actions.append(('tab', region))
            state.tab = next(i for i, tab in enumerate(task._true_friend_tabs) if tab[1] == region)
            return True
        task._tap = Mock(side_effect=tap)
        task.appear = Mock(side_effect=lambda rule: state.opened and rule == task.I_INVITE_ENSURE)
        def appear_then_click(rule, **_):
            if state.opened and rule == task.I_INVITE_ENSURE:
                self.assertTrue(state.selected)
                actions.append('invite')
                if confirm_closes:
                    state.opened = False
                return True
            return False
        task.appear_then_click = Mock(side_effect=appear_then_click)
        task.ui_click = Mock(side_effect=AssertionError('Must not wait for ordinary soul tab flags'))
        task.ui_click_until_disappear = Mock(side_effect=AssertionError('Invite confirmation must be bounded'))
        return task, state, actions

    def test_visible_friend_uses_shared_exact_name_and_selection_checks(self):
        task, state, actions = self.world()
        self.assertIs(ScriptTask._detect_select, shared.GeneralInvite._detect_select)
        self.assertIs(ScriptTask._find_exact_friend_area, shared.GeneralInvite._find_exact_friend_area)
        task._invite_true_friend()
        self.assertEqual(actions, ['select_exact_name', 'invite'])
        self.assertFalse(state.opened)
        task.ui_click.assert_not_called()

    def test_missing_friend_in_initial_tab_switches_to_actual_friend_label(self):
        task, state, actions = self.world(target_tab=1)
        task._invite_true_friend()
        self.assertIn(('tab', (489, 97, 65, 35)), actions)
        self.assertEqual(actions[-2:], ['select_exact_name', 'invite'])

    def test_unreadable_first_tab_does_not_shift_other_click_targets(self):
        task, _, _ = self.world()
        task.O_TRUE_FRIEND_TABS.detect_and_ocr.return_value = [
            result('?', 22, 8), result('好友', 139, 12), result('路区', 254, 12),
        ]
        self.assertEqual(task._read_friend_classes(), ['好友', '跨区'])
        task._switch_friend_class(0)
        self.assertEqual(task._tap.call_args.args[0], (489, 97, 65, 35))

    def test_visible_friend_is_read_even_if_all_tab_labels_are_unreadable(self):
        task, _, actions = self.world()
        task.O_TRUE_FRIEND_TABS.detect_and_ocr.return_value = []
        task._invite_true_friend()
        self.assertEqual(actions, ['select_exact_name', 'invite'])

    def test_similar_name_is_not_invited_and_missing_target_does_not_hang(self):
        task, state, actions = self.world(target_tab=-1)
        with self.assertRaises(TrueOrochiError):
            task._invite_true_friend()
        task.device.click.assert_not_called()
        self.assertNotIn('invite', actions)
        self.assertLess(state.now, 15)

    def test_name_match_without_selected_checkmark_does_not_invite(self):
        task, _, actions = self.world(select_works=False)
        with self.assertRaises(TrueOrochiError):
            task._invite_true_friend()
        self.assertIn('select_exact_name', actions)
        self.assertNotIn('invite', actions)

    def test_invite_button_that_never_closes_has_a_short_timeout(self):
        task, state, _ = self.world(confirm_closes=False)
        with self.assertRaisesRegex(TrueOrochiError, '邀请面板未关闭'):
            task._invite_true_friend()
        self.assertLess(state.now, 15)

    def test_local_ocr_reads_tabs_from_the_actual_stuck_screen(self):
        from module.ocr.ppocr import TextSystem
        header = np.array(Image.open(Path(__file__).with_name('fixtures') /
                                    'true_orochi/invite_tabs.png').convert('RGB'))
        task, _, _ = self.world()
        task.O_TRUE_FRIEND_TABS.detect_and_ocr.return_value = TextSystem().detect_and_ocr(header)
        self.assertEqual(task._read_friend_classes(), ['寮友', '好友', '跨区', '大蛇印记拥有者'])
        task._switch_friend_class(1)
        x, y, w, h = task._tap.call_args.args[0]
        self.assertTrue(468 <= x < x+w < 580)
        self.assertTrue(85 <= y < y+h < 150)


if __name__ == '__main__':
    unittest.main()
