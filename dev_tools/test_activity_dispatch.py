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

    def screenshot(self):
        self.reads += 1
        if self.kind == 'map':
            return Observation(kind='map', empty=tuple(
                (100 + i * 50, 100, 20, 20) for i in range(self.empty)),
                locked=self.locked, running=self.running)
        if self.kind == 'unknown':
            return Observation()
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
            self.kind = 'portraits' if self.leave_drawer else 'map'


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
        with self.assertRaises(DispatchError):
            dispatcher(world).run()
        self.assertEqual(world.clicks, [])

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
