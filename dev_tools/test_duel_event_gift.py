"""Duel event gift and soul-setup replay; no emulator, RPC or user config."""

from datetime import datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import cv2
import numpy as np

from module.exception import GameStuckError, TaskEnd
from tasks.Duel.assets import DuelAssets
from test_image_template_guard import methods


ROOT = Path(__file__).resolve().parents[1]
GIFT_MARKERS = ('I_D_EVENT_GIFT_PROTECT', 'I_D_EVENT_GIFT_COUPON', 'I_D_EVENT_GIFT_ACCEPT')


def gift_frame(name='event_gift_1431.png'):
    crop = cv2.imread(str(ROOT / 'dev_tools/fixtures/duel' / name))
    assert crop is not None and crop.shape == (366, 426, 3), name
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    image[185:551, 622:1048] = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
    return image


class World:
    def __init__(self, frames):
        self.frames, self.index, self.now = frames, -1, 1000
        self.current = frames[0]
        self.clicks = []
        self.block_accept_once = False
        timer = methods('module/base/timer.py', 'Timer',
                        ['__init__', 'start', 'started', 'current', 'reached', 'reset'],
                        dict(time=SimpleNamespace(time=lambda: self.now)))
        cls = methods('tasks/Duel/script_task.py', 'ScriptTask',
                      ['_claim_duel_event_gift', 'duel_popup_handle', 'switch_all_soul',
                       'prepare_duel', 'duel_main', 'run'],
                      dict(Timer=timer, GameStuckError=GameStuckError, TaskEnd=TaskEnd,
                           logger=Mock(), page_main='main', page_duel='duel', page_onmyodo='onmyodo',
                           random_click=Mock(return_value='safe_continue'),
                           datetime=SimpleNamespace(now=lambda: datetime(2026, 9, 16, 14, 30)),
                           time=time, timedelta=timedelta))
        self.task = cls()
        for name in GIFT_MARKERS:
            setattr(self.task, name, getattr(DuelAssets, name))
        for name in ('I_D_TEAM', 'I_UI_CONFIRM', 'I_D_TEAM_SWTICH', 'I_UI_BACK_YELLOW',
                     'I_D_TRY', 'I_D_ANNOUNCE', 'I_D_HELP', 'I_CHECK_DUEL',
                     'I_D_CELEB_STAR', 'I_D_CELEB_HONOR'):
            setattr(self.task, name, name)
        self.task.device = SimpleNamespace(image=frames[0] if isinstance(frames[0], np.ndarray)
                                           else np.zeros((720, 1280, 3), dtype=np.uint8))
        self.task.screenshot = Mock(side_effect=self.screenshot)
        self.task.appear = Mock(side_effect=self.appear)
        self.task.appear_then_click = Mock(side_effect=self.appear_then_click)
        self.task.click = Mock(side_effect=self.click)
        self.task.is_battle_end = Mock(return_value=False)
        self.task.ui_click = Mock(side_effect=AssertionError('Unbounded navigation must not run'))
        self.task.ui_click_until_appear_or_timeout = Mock(return_value=True)
        self.task.goto_page = Mock()
        self.task.switch_soul = Mock()
        self.task.switch_onmyoji = Mock()
        self.task.conf = SimpleNamespace(
            duel_config=SimpleNamespace(switch_all_soul=True, switch_enabled=False, limit_time=time(3)),
            duel_celeb_config=SimpleNamespace(initial_score=3800))
        self.task.config = SimpleNamespace(duel=self.task.conf)
        self.task.check_and_get_reward = Mock()
        self.task.can_start_duel = Mock(return_value=False)
        self.task.start_duel = Mock()
        self.task.set_next_run = Mock()

    def screenshot(self):
        self.index += 1
        self.now += 1
        self.current = self.frames[min(self.index, len(self.frames)-1)]
        self.task.device.image = (self.current if isinstance(self.current, np.ndarray)
                                  else np.zeros((720, 1280, 3), dtype=np.uint8))
        return self.task.device.image

    def appear(self, marker, **kwargs):
        if any(marker is getattr(DuelAssets, name) for name in GIFT_MARKERS):
            return marker.template_match(self.task.device.image)
        return isinstance(self.current, set) and marker in self.current

    def appear_then_click(self, marker, **kwargs):
        return self.click(marker, **kwargs) if self.appear(marker) else False

    def click(self, marker, **kwargs):
        if marker is self.task.I_D_EVENT_GIFT_ACCEPT and self.block_accept_once:
            self.block_accept_once = False
            return False
        self.clicks.append(marker.name if not isinstance(marker, str) else marker)
        return True


class DuelGiftVisionTests(unittest.TestCase):
    def test_all_three_logged_gifts_match_and_accept_exactly_once(self):
        for name in ('event_gift_1431.png', 'event_gift_1432.png', 'event_gift_1434.png'):
            with self.subTest(name=name):
                image = gift_frame(name)
                world = World([image, image, set(), set()])
                world.screenshot()
                self.assertTrue(world.task.duel_popup_handle())
                self.assertEqual(world.clicks, ['DUEL_D_EVENT_GIFT_ACCEPT'])
                self.assertEqual(world.index, 3)

    def test_a_button_or_either_gift_title_alone_cannot_authorize_click(self):
        source = gift_frame()
        for remove in GIFT_MARKERS:
            with self.subTest(missing=remove):
                image = source.copy()
                x, y, w, h = getattr(DuelAssets, remove).roi_back
                image[y:y+h, x:x+w] = 0
                world = World([image])
                self.assertFalse(world.task.duel_popup_handle())
                self.assertEqual(world.clicks, [])
                world.task.screenshot.assert_not_called()

    def test_stuck_gift_has_one_click_and_bounded_retryable_error(self):
        world = World([gift_frame()])
        with self.assertRaisesRegex(GameStuckError, 'gift did not close'):
            world.task.duel_popup_handle()
        self.assertEqual(world.clicks, ['DUEL_D_EVENT_GIFT_ACCEPT'])
        self.assertLessEqual(world.task.screenshot.call_count, 11)
        world.task.set_next_run.assert_not_called()

    def test_animation_must_clear_twice_and_never_gets_another_acceptance(self):
        image = gift_frame()
        world = World([image, set(), image, set(), set()])
        world.screenshot()
        self.assertTrue(world.task.duel_popup_handle())
        self.assertEqual(world.clicks, ['DUEL_D_EVENT_GIFT_ACCEPT'])
        self.assertEqual(world.index, 4)

    def test_throttled_click_does_not_count_as_delivered(self):
        image = gift_frame()
        world = World([image, image, set(), set()])
        world.block_accept_once = True
        world.screenshot()
        self.assertTrue(world.task.duel_popup_handle())
        self.assertEqual(world.clicks, ['DUEL_D_EVENT_GIFT_ACCEPT'])
        self.assertEqual(world.task.click.call_count, 2)

    def test_existing_trial_and_announcement_handlers_still_work(self):
        for marker in ('I_D_TRY', 'I_D_ANNOUNCE'):
            with self.subTest(marker=marker):
                world = World([{marker}])
                self.assertTrue(world.task.duel_popup_handle())
                self.assertEqual(world.clicks, ['I_D_TRY' if marker == 'I_D_TRY' else 'safe_continue'])


class DuelGiftSetupTests(unittest.TestCase):
    def setup_frames(self, gift=True):
        prefix = [gift_frame(), gift_frame(), set(), set()] if gift else []
        return prefix + [{'I_D_TEAM'}, {'I_D_TEAM_SWTICH'}, {'I_UI_CONFIRM'},
                         {'I_D_TEAM_SWTICH'}, {'I_UI_CONFIRM'}, {'I_D_TEAM_SWTICH'},
                         {'I_D_HELP'}, {'I_D_HELP'}]

    def test_real_run_claims_gift_finishes_soul_setup_then_uses_normal_completion(self):
        world = World(self.setup_frames())
        with self.assertRaises(TaskEnd):
            world.task.run()
        self.assertEqual(world.clicks, ['DUEL_D_EVENT_GIFT_ACCEPT', 'I_D_TEAM',
                                      'I_D_TEAM_SWTICH', 'I_UI_CONFIRM',
                                      'I_D_TEAM_SWTICH', 'I_UI_CONFIRM', 'I_D_TEAM_SWTICH'])
        world.task.ui_click_until_appear_or_timeout.assert_called_once_with(
            'I_UI_BACK_YELLOW', 'I_D_TEAM', interval=1, timeout=10)
        world.task.can_start_duel.assert_called_once()
        world.task.set_next_run.assert_called_once_with(task='Duel', success=True, finish=True)
        self.assertEqual([call.args[0] for call in world.task.goto_page.call_args_list],
                         ['main', 'duel', 'main'])

    def test_normal_setup_without_gift_retains_three_switches(self):
        world = World(self.setup_frames(gift=False))
        world.task.prepare_duel()
        self.assertEqual(world.clicks.count('I_D_TEAM_SWTICH'), 3)
        self.assertNotIn('DUEL_D_EVENT_GIFT_ACCEPT', world.clicks)
        self.assertEqual(world.task.current_score, 3800)

    def test_unknown_setup_is_bounded_without_guessing_or_successful_scheduling(self):
        world = World([set()])
        with self.assertRaisesRegex(GameStuckError, 'expected team controls'):
            world.task.run()
        self.assertEqual(world.clicks, [])
        self.assertLessEqual(world.task.screenshot.call_count, 46)
        world.task.set_next_run.assert_not_called()

    def test_unconfirmed_setup_return_cannot_start_battle_or_finish_task(self):
        world = World(self.setup_frames(gift=False))
        world.task.ui_click_until_appear_or_timeout.return_value = False
        with self.assertRaisesRegex(GameStuckError, 'return to the duel lobby'):
            world.task.run()
        world.task.can_start_duel.assert_not_called()
        world.task.start_duel.assert_not_called()
        world.task.set_next_run.assert_not_called()

    def test_disabled_soul_switch_does_not_enter_setup(self):
        world = World([gift_frame()])
        world.task.conf.duel_config.switch_all_soul = False
        world.task.switch_all_soul()
        world.task.screenshot.assert_not_called()
        self.assertEqual(world.clicks, [])

    def test_main_loop_also_claims_gift_when_soul_switch_is_disabled(self):
        image = gift_frame()
        world = World([image, image, set(), set(), {'I_D_HELP'}])
        world.task.conf.duel_config.switch_all_soul = False
        with self.assertRaises(TaskEnd):
            world.task.run()
        self.assertEqual(world.clicks, ['DUEL_D_EVENT_GIFT_ACCEPT'])
        world.task.ui_click_until_appear_or_timeout.assert_not_called()
        world.task.can_start_duel.assert_called_once()
        world.task.set_next_run.assert_called_once_with(task='Duel', success=True, finish=True)


if __name__ == '__main__':
    unittest.main()
