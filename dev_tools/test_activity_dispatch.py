"""Offline dispatch state-machine tests: no device, configuration or game."""

import unittest

from tasks.ActivityShikigami.dispatch import DailyDispatcher, DispatchError
from tasks.ActivityShikigami.dispatch_view import DispatchObservation as Observation


SLOT = (100, 100, 20, 20)
PORTRAIT = (200, 500, 20, 20)
PLUS = (900, 300, 20, 20)
MINUS = (700, 300, 20, 20)
SUBMIT = (800, 400, 50, 25)
CLOSE = (500, 440, 20, 20)
DISMISS = (520, 180, 25, 22)


class World:
    def __init__(self, empty=1, locked=3, running=0, current=9, maximum=9):
        self.empty, self.locked, self.running = empty, locked, running
        self.current, self.maximum = current, maximum
        self.kind = 'map'
        self.clicks = []
        self.controls = []
        self.reads = 0
        self.available = ((1, PORTRAIT), (4, (400, 500, 20, 20)))
        self.selected = None
        self.stall = None
        self.confirm_submit = True
        self.leave_drawer = False
        self.show_success = False
        self.after_success = 'running_details'

    def screenshot(self):
        self.reads += 1
        if self.kind == 'map':
            return Observation(kind='map', empty=tuple(
                (100 + i * 50, 100, 20, 20) for i in range(self.empty)),
                locked=self.locked, running=self.running)
        if self.kind == 'unknown':
            return Observation()
        if self.kind == 'success':
            return Observation(kind='success', dismiss_roi=DISMISS)
        if self.kind == 'running_details':
            return Observation(kind='running_details', close_roi=CLOSE)
        return Observation(kind=self.kind, available=self.available, selected=self.selected,
                           current=self.current if self.kind == 'setup' else None,
                           maximum=self.maximum if self.kind == 'setup' else None,
                           plus_roi=PLUS, minus_roi=MINUS, submit_roi=SUBMIT, close_roi=CLOSE)

    def click(self, roi, name):
        self.clicks.append(roi)
        self.controls.append(name)
        if roi == self.stall:
            return
        if roi == CLOSE:
            self.kind = 'map'
        elif roi == DISMISS:
            self.kind = self.after_success
        elif self.kind == 'map' and roi[1] == 100:
            self.kind = 'portraits'
        elif roi in [value for _, value in self.available]:
            self.kind = 'setup'
            self.selected = next(index for index, value in self.available if value == roi)
        elif roi == PLUS:
            self.current += 1
        elif roi == MINUS:
            self.current -= 1
        elif roi == SUBMIT:
            if self.confirm_submit:
                self.empty -= 1
                self.running += 1
            self.kind = 'success' if self.show_success else 'portraits' if self.leave_drawer else 'map'


class IdentityView:
    def observe(self, image):
        return image


def dispatcher(world, **kwargs):
    return DailyDispatcher(world.screenshot, world.click, view=IdentityView(),
                           sleep=lambda seconds: None, choose=lambda choices: choices[0], **kwargs)


class DispatchFlowTests(unittest.TestCase):
    def test_locked_slots_are_skipped_and_available_maximum_is_used(self):
        world = World()
        result = dispatcher(world).run()
        self.assertTrue(result.completed)
        self.assertEqual(result.dispatched, 1)
        self.assertEqual(world.clicks, [SLOT, PORTRAIT, SUBMIT])
        self.assertEqual(world.controls, ['dispatch_slot', 'dispatch_portrait', 'dispatch_submit'])
        self.assertEqual(world.kind, 'map')

    def test_four_running_slots_need_no_click(self):
        world = World(empty=0, locked=0, running=4)
        result = dispatcher(world).run()
        self.assertTrue(result.completed)
        self.assertEqual(result.dispatched, 0)
        self.assertEqual(world.clicks, [])

    def test_existing_running_slots_are_preserved(self):
        world = World(empty=2, locked=0, running=2, current=12, maximum=12)
        result = dispatcher(world).run()
        self.assertEqual(result.dispatched, 2)
        self.assertEqual(world.running, 4)
        self.assertEqual(world.clicks.count(SUBMIT), 2)

    def test_each_dispatch_is_verified_before_advancing(self):
        world = World(empty=4, locked=0, running=0, current=12, maximum=12)
        world.leave_drawer = True
        result = dispatcher(world).run()
        self.assertEqual(result.dispatched, 4)
        self.assertEqual(world.clicks, [SLOT, PORTRAIT, SUBMIT, CLOSE] * 4)

    def test_success_overlay_is_dismissed_before_collapsing_running_details(self):
        world = World(empty=4, locked=0, current=12, maximum=12)
        world.show_success = True
        result = dispatcher(world).run()
        self.assertEqual(result.dispatched, 4)
        self.assertEqual(world.clicks, [SLOT, PORTRAIT, SUBMIT, DISMISS, CLOSE] * 4)
        self.assertEqual(world.controls.count('dispatch_success_close'), 4)
        self.assertEqual(world.kind, 'map')

    def test_interrupted_success_is_recovered_without_resubmitting_its_character(self):
        world = World(empty=3, locked=0, running=1)
        world.show_success = True
        world.kind = 'success'
        result = dispatcher(world).run()
        self.assertEqual(result.dispatched, 3)
        self.assertEqual(world.clicks[:2], [DISMISS, CLOSE])
        self.assertEqual(world.clicks.count(SUBMIT), 3)
        self.assertEqual(world.running, 4)

    def test_success_without_new_countdown_does_not_count_as_completed(self):
        world = World()
        world.show_success = True
        world.confirm_submit = False
        with self.assertRaises(DispatchError):
            dispatcher(world).run()
        self.assertEqual(world.clicks, [SLOT, PORTRAIT, SUBMIT, DISMISS, CLOSE])

    def test_stuck_success_overlay_gets_one_dismissal_and_no_underlying_clicks(self):
        world = World()
        world.show_success = True
        world.stall = DISMISS
        with self.assertRaises(DispatchError):
            dispatcher(world).run()
        self.assertEqual(world.clicks, [SLOT, PORTRAIT, SUBMIT, DISMISS])

    def test_unknown_success_animation_frames_do_not_allow_repeat_dismissal(self):
        class FlickeringWorld(World):
            def screenshot(self):
                if self.clicks:
                    self.kind = 'unknown' if self.reads % 2 else 'success'
                return super().screenshot()

        world = FlickeringWorld()
        world.kind = 'success'
        with self.assertRaises(DispatchError):
            dispatcher(world).run()
        self.assertEqual(world.clicks, [DISMISS])
        self.assertLessEqual(world.reads, 9)

    def test_stuck_running_details_never_receive_a_recall_or_repeat_collapse(self):
        world = World(empty=0, locked=0, running=4)
        world.kind = 'running_details'
        world.stall = CLOSE
        with self.assertRaises(DispatchError):
            dispatcher(world).run()
        self.assertEqual(world.clicks, [CLOSE])

    def test_success_can_return_directly_to_the_map(self):
        world = World()
        world.show_success = True
        world.after_success = 'map'
        result = dispatcher(world).run()
        self.assertTrue(result.completed)
        self.assertEqual(world.clicks, [SLOT, PORTRAIT, SUBMIT, DISMISS])

    def test_target_twelve_adjusts_and_reads_every_hour(self):
        for current, control, count in ((8, PLUS, 4), (15, MINUS, 3)):
            with self.subTest(current=current):
                world = World(current=current, maximum=18)
                result = dispatcher(world).run()
                self.assertTrue(result.completed)
                self.assertEqual(world.current, 12)
                self.assertEqual(world.clicks, [SLOT, PORTRAIT] + [control] * count + [SUBMIT])

    def test_shorter_available_maximum_is_reached_without_purchases(self):
        world = World(current=5, maximum=9)
        dispatcher(world).run()
        self.assertEqual(world.clicks, [SLOT, PORTRAIT] + [PLUS] * 4 + [SUBMIT])

    def test_long_adjustment_names_follow_verified_numeric_progress(self):
        world = World(current=0, maximum=12)
        dispatcher(world).run()
        self.assertEqual(world.current, 12)
        self.assertEqual(world.controls[2:-1],
                         ['dispatch_hours_plus_from_' + str(i) for i in range(12)])

    def test_zero_duration_completes_attempt_and_closes_without_submission(self):
        world = World(current=0, maximum=0)
        result = dispatcher(world).run()
        self.assertTrue(result.completed)
        self.assertEqual(result.dispatched, 0)
        self.assertEqual(world.clicks, [SLOT, PORTRAIT, CLOSE])
        self.assertEqual(world.kind, 'map')

    def test_no_available_portraits_completes_attempt_without_guessing(self):
        world = World()
        world.available = ()
        result = dispatcher(world).run()
        self.assertTrue(result.completed)
        self.assertEqual(world.clicks, [SLOT, CLOSE])

    def test_interrupted_setup_is_closed_before_any_new_dispatch(self):
        world = World(empty=0, locked=0, running=4)
        world.kind = 'setup'
        world.selected = 4
        result = dispatcher(world).run()
        self.assertTrue(result.completed)
        self.assertEqual(world.clicks, [CLOSE])

    def test_restore_map_never_submits_an_interrupted_setup(self):
        world = World()
        world.kind = 'setup'
        world.selected = 1
        self.assertTrue(dispatcher(world).restore_map())
        self.assertEqual(world.clicks, [CLOSE])

    def test_unknown_screens_stop_without_clicks(self):
        world = World()
        world.kind = 'unknown'
        with self.assertRaises(DispatchError):
            dispatcher(world).run()
        self.assertEqual(world.clicks, [])
        self.assertLessEqual(world.reads, 8)

    def test_incomplete_map_is_not_treated_as_completed(self):
        world = World(empty=1, locked=1, running=0)
        result = dispatcher(world).run()
        self.assertFalse(result.completed)
        self.assertEqual(result.dispatched, 0)
        self.assertEqual(world.clicks, [])
        self.assertLessEqual(world.reads, 3)

    def test_obscured_running_slot_skips_dispatch_without_waiting_for_ocr(self):
        world = World(empty=0, locked=3, running=0)
        result = dispatcher(world).run()
        self.assertFalse(result.completed)
        self.assertEqual(result.dispatched, 0)
        self.assertEqual(world.clicks, [])
        self.assertEqual(world.kind, 'map')
        self.assertLessEqual(world.reads, 3)

    def test_obscured_post_submit_countdown_is_not_resubmitted_or_recorded(self):
        class ObscuredWorld(World):
            def click(self, roi, name):
                super().click(roi, name)
                if roi == SUBMIT:
                    self.running = 0

        world = ObscuredWorld()
        world.show_success = True
        result = dispatcher(world).run()
        self.assertFalse(result.completed)
        self.assertEqual(result.dispatched, 0)
        self.assertEqual(world.clicks, [SLOT, PORTRAIT, SUBMIT, DISMISS, CLOSE])
        self.assertEqual(world.kind, 'map')

    def test_obscured_map_after_no_resources_is_partial_not_complete(self):
        class ObscuredWorld(World):
            def click(self, roi, name):
                super().click(roi, name)
                if roi == CLOSE:
                    self.locked = 2

        world = ObscuredWorld(current=0, maximum=0)
        result = dispatcher(world).run()
        self.assertFalse(result.completed)
        self.assertEqual(world.clicks, [SLOT, PORTRAIT, CLOSE])

    def test_unchanged_duration_stops_after_one_adjustment(self):
        world = World(current=8, maximum=12)
        world.stall = PLUS
        with self.assertRaises(DispatchError):
            dispatcher(world).run()
        self.assertEqual(world.clicks, [SLOT, PORTRAIT, PLUS, CLOSE])
        self.assertEqual(world.kind, 'map')

    def test_unconfirmed_submit_is_never_repeated(self):
        world = World()
        world.confirm_submit = False
        with self.assertRaises(DispatchError):
            dispatcher(world).run()
        self.assertEqual(world.clicks, [SLOT, PORTRAIT, SUBMIT])
        self.assertEqual(world.kind, 'map')

    def test_unreadable_duration_never_guesses_a_resource_action(self):
        world = World(current=None, maximum=None)
        with self.assertRaises(DispatchError):
            dispatcher(world).run()
        self.assertEqual(world.clicks, [SLOT, PORTRAIT, CLOSE])

    def test_setup_changes_before_submission_are_rejected(self):
        world = World()
        flow = dispatcher(world)
        original = flow._duration

        def duration_then_change(selected, observation):
            result = original(selected, observation)
            world.current = 8
            return result

        flow._duration = duration_then_change
        with self.assertRaises(DispatchError):
            flow.run()
        self.assertNotIn(SUBMIT, world.clicks)
        self.assertEqual(world.clicks[-1], CLOSE)


if __name__ == '__main__':
    unittest.main()
