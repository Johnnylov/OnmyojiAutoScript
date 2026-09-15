"""Bounded daily dispatch flow; every resource action uses a verified setup."""

from dataclasses import dataclass
import random
from time import sleep as default_sleep

from tasks.ActivityShikigami.dispatch_view import DispatchView


class DispatchError(RuntimeError):
    """The dispatch screen could not be safely recognized or confirmed."""


@dataclass(frozen=True)
class DispatchResult:
    completed: bool
    dispatched: int
    reason: str


class DailyDispatcher:
    def __init__(self, screenshot, click_roi, view=None, sleep=default_sleep, choose=None):
        self.screenshot = screenshot
        self.click_roi = click_roi
        self.view = view or DispatchView()
        self.sleep = sleep
        self.choose = choose or random.choice
        self.latest = None
        self._restore_failed = False

    def _observe(self):
        self.latest = self.view.observe(self.screenshot())
        return self.latest

    @staticmethod
    def _state(observation):
        return (observation.kind, len(observation.empty), observation.locked,
                observation.running, observation.uncertain, observation.selected,
                observation.current, observation.maximum,
                tuple(index for index, _ in observation.available), bool(observation.close_roi),
                bool(observation.dismiss_roi), observation.return_id)

    def _wait(self, predicate, reason):
        previous, unchanged = None, 0
        for _ in range(8):
            observation = self._observe()
            if predicate(observation):
                return observation
            state = self._state(observation)
            unchanged = unchanged + 1 if state == previous else 0
            if unchanged >= 3:
                break
            previous = state
            self.sleep(.4)
        raise DispatchError(reason)

    def _click(self, roi, name):
        if roi is None:
            raise DispatchError('Missing verified dispatch control')
        if self.click_roi(roi, name) is False:
            raise DispatchError('Dispatch click was not delivered')
        self.sleep(.4)

    def _wait_after_overlay(self, previous, closed_returns):
        candidate, stable, map_stable = None, 0, 0

        def restored(observation):
            nonlocal candidate, stable, map_stable
            if observation.kind == 'map':
                candidate, stable = None, 0
                map_stable += 1
                # A single map frame can be the gap before the next return.
                return map_stable >= 2
            map_stable = 0
            if (previous.kind in ('success', 'returned', 'interrupted')
                    and observation.kind not in ('success', 'returned', 'interrupted')
                    and observation.close_roi is not None):
                return True
            if observation.kind == 'returned':
                identity = observation.return_id
                if identity in closed_returns or None in closed_returns:
                    candidate, stable = None, 0
                    return False
                if previous.kind == 'returned' and (
                        previous.return_id is None or identity is None):
                    candidate, stable = None, 0
                    return False
                key = ('returned', identity)
                stable = stable + 1 if candidate == key else 1
                candidate = key
                return stable >= 2
            if observation.kind == 'interrupted' and previous.kind != 'interrupted':
                stable = stable + 1 if candidate == 'interrupted' else 1
                candidate = 'interrupted'
                return stable >= 2
            candidate, stable = None, 0
            return False

        return self._wait(restored, 'Dispatch overlay did not close or reveal a confirmed new return')

    def restore_map(self):
        """Close verified result overlays and their drawer, then confirm the map."""
        self._restore_failed = False
        try:
            map_stable = 0

            def ready(observation):
                nonlocal map_stable
                if observation.kind == 'map':
                    map_stable += 1
                    return map_stable >= 2
                map_stable = 0
                return observation.kind in ('success', 'returned', 'interrupted') or observation.close_roi is not None

            observation = self._wait(
                ready,
                'Cannot identify the dispatch map, result overlay or its drawer')
            closed_returns, return_count = set(), 0
            success_closed = interrupted_closed = drawer_closed = False
            for _ in range(8):
                if observation.kind == 'map':
                    return True
                previous = observation
                if observation.kind == 'returned':
                    if return_count >= 4:
                        raise DispatchError('Return overlay count exceeded the four dispatch slots')
                    self._click(observation.dismiss_roi, 'dispatch_return_close')
                    return_count += 1
                    closed_returns.add(observation.return_id)
                elif observation.kind == 'success':
                    if success_closed:
                        raise DispatchError('Dispatch success overlay appeared again without progress')
                    self._click(observation.dismiss_roi, 'dispatch_success_close')
                    success_closed = True
                elif observation.kind == 'interrupted':
                    if interrupted_closed:
                        raise DispatchError('Dispatch interruption overlay appeared again without progress')
                    self._click(observation.dismiss_roi, 'dispatch_interrupt_close')
                    interrupted_closed = True
                elif observation.close_roi is not None:
                    if drawer_closed:
                        raise DispatchError('Dispatch drawer appeared again without a confirmed map')
                    self._click(observation.close_roi, 'dispatch_collapse')
                    drawer_closed = True
                else:
                    raise DispatchError('Cannot safely leave the dispatch result screen')
                observation = self._wait_after_overlay(previous, closed_returns)
            raise DispatchError('Dispatch result recovery exceeded its bound')
        except DispatchError:
            # Do not repeat a failed popup/drawer click again in run() cleanup.
            self._restore_failed = True
            raise

    def _complete_without_dispatch(self, dispatched, reason):
        self.restore_map()
        if not self.latest.all_slots_known:
            return DispatchResult(False, dispatched, '上阵面板已收起，但部分格子被遮挡，暂缓派遣')
        # A fully inspected account with no usable portrait or no remaining
        # time allowance has completed today's attempt; it must not buy more.
        return DispatchResult(True, dispatched, reason)

    def _duration(self, selected, observation):
        if observation.selected != selected:
            raise DispatchError('The selected dispatch portrait changed')
        if observation.current is None or observation.maximum is None:
            raise DispatchError('Cannot read the dispatch duration safely')
        maximum = observation.maximum
        target = min(12, maximum)
        for _ in range(24):
            if observation.current == target:
                return observation, target
            previous = observation.current
            direction = 1 if previous < target else -1
            # The device's repeated-click guard must distinguish actual,
            # OCR-verified progress from repeatedly pressing a stuck control.
            self._click(observation.plus_roi if direction > 0 else observation.minus_roi,
                        f'dispatch_hours_{"plus" if direction > 0 else "minus"}_from_{previous}')
            observation = self._wait(
                lambda o: o.kind == 'setup' and o.selected == selected
                and o.current is not None and o.current != previous,
                'Dispatch duration did not change after one adjustment')
            if observation.maximum != maximum or observation.current != previous + direction:
                raise DispatchError('Unexpected change in dispatch duration or its maximum')
        raise DispatchError('Dispatch duration adjustment exceeded its bound')

    def _run(self):
        self.restore_map()
        dispatched = 0
        for _ in range(5):
            before = self._wait(lambda o: o.kind == 'map',
                                'Cannot confirm the dispatch map before checking slots')
            if not before.all_slots_known:
                # Character effects can hide timers or their inspect icons.
                # Keep those slots unknown, but do not block the main climb
                # after positively identifying the unobstructed activity map.
                return DispatchResult(False, dispatched, '部分上阵格子被遮挡或无法识别，暂缓派遣')
            if not before.empty:
                return DispatchResult(True, dispatched, 'All unlocked slots are already running')
            if dispatched >= 4:
                raise DispatchError('Dispatch slot count did not converge after four dispatches')
            # An empty slot is recognized by the combined plus and 上阵 label;
            # neither resource plus buttons nor sealed/countdown slots qualify.
            slot = min(before.empty, key=lambda roi: (roi[1], roi[0]))
            self._click(slot, 'dispatch_slot')
            drawer = self._wait(lambda o: o.kind in ('portraits', 'setup'),
                                'Empty dispatch slot did not open a portrait drawer')
            if not drawer.available:
                return self._complete_without_dispatch(dispatched, 'No available dispatch portrait')
            selection = self.choose(drawer.available)
            if selection not in drawer.available:
                raise DispatchError('Portrait chooser returned an unavailable portrait')
            selected, portrait_roi = selection
            self._click(portrait_roi, 'dispatch_portrait')
            setup = self._wait(lambda o: o.kind == 'setup' and o.selected == selected,
                               'Selected portrait did not open its dispatch setup')
            if setup.maximum == 0 and setup.current == 0:
                return self._complete_without_dispatch(dispatched, 'No remaining dispatch duration')
            setup, target = self._duration(selected, setup)
            fresh = self._observe()
            if (fresh.kind != 'setup' or fresh.selected != selected or fresh.current != target
                    or fresh.maximum != setup.maximum or target <= 0):
                raise DispatchError('Dispatch setup changed before submission')
            self._click(fresh.submit_roi, 'dispatch_submit')
            # Never repeat a resource-spending submit click. Dismiss the success
            # overlay, collapse running details (whose button is now Recall),
            # and require one new running timer before touching another slot.
            self.restore_map()
            after = self._wait(lambda o: o.kind == 'map' and (not o.all_slots_known or (
                               o.running == before.running + 1
                               and len(o.empty) == len(before.empty) - 1
                               and o.locked == before.locked)),
                               'Dispatch was not confirmed by a new running countdown')
            if not after.all_slots_known:
                # The submit was already sent. Do not resend or record success
                # when its new countdown is obscured; climbing may continue.
                return DispatchResult(False, dispatched, '提交后已返回地图，但倒计时未确认，不重复上阵')
            dispatched += 1
            if not after.empty:
                return DispatchResult(True, dispatched, 'All unlocked slots are now running')
        raise DispatchError('Daily dispatch exceeded its bound')

    def run(self):
        try:
            return self._run()
        except DispatchError as error:
            # Safe best effort: leave a recognized setup via its collapse
            # control. Unknown screens never receive a guessed back/plus click.
            if (not self._restore_failed and self.latest is not None
                    and (self.latest.close_roi is not None or self.latest.dismiss_roi is not None)):
                try:
                    self.restore_map()
                except DispatchError:
                    pass
            raise error
