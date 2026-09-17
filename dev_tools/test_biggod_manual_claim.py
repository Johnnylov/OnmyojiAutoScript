"""Replay the manual BigGod UI with virtual time; no app or device is accessed."""

import ast
from copy import copy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]


def load_nodes(relative_path, namespace, predicate):
    path = ROOT / relative_path
    tree = ast.parse(path.read_text(encoding='utf-8'))
    tree.body = [node for node in tree.body if predicate(node)]
    exec(compile(tree, str(path), 'exec'), namespace)


class TaskEnd(Exception):
    pass


class ManualClaimTests(unittest.TestCase):
    def world(self, buttons=None, login=None, gift=None):
        world = SimpleNamespace(now=1000, frame=0, taps=[], swipes=[], gift_clicks=[],
                                coord_rois=[], cleanup=Mock(), scheduled=Mock())
        timer_namespace = {'time': SimpleNamespace(time=lambda: world.now)}
        load_nodes('module/base/timer.py', timer_namespace,
                   lambda node: isinstance(node, ast.ClassDef) and node.name == 'Timer')

        # Exercise RuleImage.coord itself, with a deterministic local click provider.
        # Its deliberately off-centre result detects a regression to fixed centres.
        def local_click_point(roi):
            world.coord_rois.append(list(roi))
            return roi[0] + 7, roi[1] + 9

        image_path = ROOT / 'module/atom/image.py'
        image_tree = ast.parse(image_path.read_text(encoding='utf-8'))
        image_class = next(node for node in image_tree.body
                           if isinstance(node, ast.ClassDef) and node.name == 'RuleImage')
        coord_node = next(node for node in image_class.body
                          if isinstance(node, ast.FunctionDef) and node.name == 'coord')
        coord_namespace = {'monte_carlo_click_point': local_click_point}
        exec(compile(ast.Module(body=[coord_node], type_ignores=[]), str(image_path), 'exec'),
             coord_namespace)

        class Asset:
            coord = coord_namespace['coord']

            def __init__(self, name):
                self.name = name
                self.roi_front = [595, 539, 60, 33]

            def match_all_any(self, image):
                return list(buttons(world) if buttons else [])

        assets = SimpleNamespace(**{
            name: Asset(name) for name in (
                'I_LOGIN_AGAIN', 'I_X', 'I_LOGIN', 'I_CLAIM_S', 'I_CLAIM',
                'I_GIFT', 'I_CIRCLE_CHECK', 'I_CIRCLE', 'I_WELFARE')})
        world.assets = assets
        world.original_roi = assets.I_CLAIM.roi_front
        namespace = {'TYPE_CHECKING': False, 'copy': copy, 'Timer': timer_namespace['Timer'],
                     'TaskEnd': TaskEnd, 'PortraitUIMixin': object, 'logger': Mock(),
                     'A': assets}
        load_nodes('tasks/AutoCheckinBigGod/manual_claim.py', namespace,
                   lambda node: isinstance(node, (ast.Assign, ast.ClassDef)))

        class Harness(namespace['ManualClaimMixin']):
            device = SimpleNamespace(image=None)

            def _check_adb_connection(self):
                return True

            def _init_portrait(self):
                pass

            def _launch_app_foreground(self, package):
                return True

            def _screenshot_safe(self):
                world.now += 1
                world.frame += 1
                if world.frame > 400:
                    raise AssertionError('UI did not exit within 400 simulated seconds')

            def _appear_then_click(self, target, interval=None):
                if target is assets.I_LOGIN:
                    return bool(login and login(world))
                if target is assets.I_GIFT and gift and gift(world):
                    world.gift_clicks.append(world.now)
                    return True
                return False

            def appear(self, target):
                return target is assets.I_CLAIM_S

            def _tap(self, x, y, name=None):
                world.taps.append((world.now, x, y, name))

            def _swipe(self, *args):
                world.swipes.append((world.now, args))

            def _cleanup_portrait(self):
                world.cleanup()

            def set_next_run(self, *args, **kwargs):
                world.scheduled(*args, **kwargs)

        world.task = Harness()
        return world

    def run_world(self, world, success):
        with self.assertRaises(TaskEnd):
            world.task._run_manual_claim()
        world.cleanup.assert_called_once_with()
        world.scheduled.assert_called_once_with('AutoCheckinBigGod', success=success, finish=True)

    def test_recurring_login_cannot_bypass_total_timeout(self):
        world = self.world(login=lambda world: world.frame % 6 == 0)
        self.run_world(world, success=False)
        self.assertEqual(world.frame, 241)
        self.assertEqual(world.taps, [])

    def test_never_stable_buttons_cannot_bypass_total_timeout(self):
        world = self.world(buttons=lambda world: [
            (0.95, 590, 100 + world.frame + 40 * (world.frame % 2), 60, 33)])
        self.run_world(world, success=False)
        self.assertEqual(world.frame, 241)
        self.assertGreater(len(world.taps), 1)
        self.assertGreater(len(world.swipes), 1)

    def test_automatically_opened_empty_sheet_without_gift_finishes(self):
        world = self.world()
        self.run_world(world, success=True)
        self.assertGreaterEqual(world.frame, 30)
        self.assertLess(world.frame, 60)
        self.assertEqual(world.taps, [])
        self.assertEqual(world.gift_clicks, [])

    def test_missing_buttons_after_claim_finish_without_gift(self):
        world = self.world(buttons=lambda world: [
            (0.95, 590, 550, 60, 33)] if world.frame < 10 else [])
        self.run_world(world, success=True)
        self.assertEqual(len(world.taps), 1)
        self.assertLess(world.frame, 60)
        self.assertEqual(world.gift_clicks, [])

    def test_gift_fallback_opens_once_and_then_handles_empty_sheet(self):
        world = self.world(gift=lambda world: not world.gift_clicks)
        self.run_world(world, success=True)
        self.assertEqual(len(world.gift_clicks), 1)
        self.assertLess(world.frame, 80)

    def test_claims_in_order_with_cooldown_then_stops_at_bottom(self):
        world = self.world(buttons=lambda world: [
            (0.95, 591, 700, 60, 33), (0.96, 590, 550, 60, 33)])
        self.run_world(world, success=True)
        self.assertEqual([tap[1:] for tap in world.taps], [
            (597, 559, 'I_CLAIM'), (598, 709, 'I_CLAIM')])
        self.assertGreaterEqual(world.taps[1][0] - world.taps[0][0], 3)
        self.assertEqual(len(world.swipes), 1)
        self.assertEqual(world.swipes[0][1], (360, 1050, 360, 450, 400))
        self.assertEqual(world.coord_rois, [[590, 550, 60, 33], [591, 700, 60, 33]])
        self.assertIs(world.assets.I_CLAIM.roi_front, world.original_roi)
        self.assertEqual(world.original_roi, [595, 539, 60, 33])

    def test_new_page_buttons_are_processed_before_bottom_is_declared(self):
        world = self.world(buttons=lambda world: [
            (0.95, 590, 650 if world.swipes else 550, 60, 33)])
        self.run_world(world, success=True)
        self.assertEqual([tap[2] for tap in world.taps], [559, 659])
        self.assertEqual(len(world.swipes), 2)

    def test_brief_matching_gap_does_not_finish_before_buttons_return(self):
        world = self.world(buttons=lambda world: [] if 7 <= world.frame <= 9 else [
            (0.95, 590, 550, 60, 33), (0.95, 590, 700, 60, 33)])
        self.run_world(world, success=True)
        self.assertEqual([tap[2] for tap in world.taps], [559, 709])
        self.assertEqual(len(world.swipes), 1)


if __name__ == '__main__':
    unittest.main()
