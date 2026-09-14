"""Offline shared reward-helper compatibility tests for Collective Missions."""

from types import SimpleNamespace
import unittest

from test_image_template_guard import methods


class World:
    def __init__(self, rewards=2, clicks_to_close=1, stuck=False):
        self.now = 1.0
        self.available = rewards
        self.clicks_to_close = clicks_to_close
        self.stuck = stuck
        self.visible = False
        self.panel_clicks = 0
        self.claimed = 0
        self.dismissed = 0
        self.click_times = {}
        self.claim_while_reward_visible = False
        timer = methods('module/base/timer.py', 'Timer',
                        ['__init__', 'start', 'started', 'reached', 'reset'],
                        dict(time=SimpleNamespace(time=lambda: self.now)))
        subject = methods('tasks/CollectiveMissions/script_task.py', 'ScriptTask',
                          ['get_reward_and_close'], dict(Timer=timer))
        reward = methods('tasks/base_task.py', 'BaseTask', ['ui_reward_appear_click'], {})
        self.task = subject()
        self.task.ui_reward_appear_click = reward.ui_reward_appear_click.__get__(self.task)
        self.task.I_UI_REWARD = 'reward'
        self.task.C_UI_REWARD = 'safe_margin'
        self.task.screenshot = self.screenshot
        self.task.appear = lambda marker, **kwargs: marker == 'reward' and self.visible
        self.task.click = self.click
        self.task.appear_then_click = self.claim

    def screenshot(self):
        self.now += 0.1
        if self.now > 6:
            raise AssertionError('Reward collection exceeded its bounded timeout')

    def ready(self, name, interval):
        if self.now - self.click_times.get(name, -100) < interval:
            return False
        self.click_times[name] = self.now
        return True

    def click(self, marker, interval):
        if not self.ready(marker, interval):
            return False
        self.panel_clicks += 1
        if not self.stuck and self.panel_clicks >= self.clicks_to_close:
            self.visible = False
            self.dismissed += 1
        return True

    def claim(self, marker, interval):
        if self.visible:
            self.claim_while_reward_visible = True
        if self.available <= 0 or not self.ready(marker, interval):
            return False
        self.available -= 1
        self.claimed += 1
        self.panel_clicks = 0
        self.visible = True
        return True

    def run(self):
        self.task.get_reward_and_close('claim_reward')


class CollectiveRewardDismissalTests(unittest.TestCase):
    def test_throttled_frames_do_not_count_one_tooltip_reward_twice(self):
        world = World(rewards=2, clicks_to_close=2)
        world.run()
        self.assertEqual(world.claimed, 2)
        self.assertEqual(world.dismissed, 2)
        self.assertFalse(world.visible)
        self.assertFalse(world.claim_while_reward_visible)

    def test_stop_after_two_completed_dismissals_without_claiming_a_third(self):
        world = World(rewards=3)
        world.run()
        self.assertEqual(world.claimed, 2)
        self.assertEqual(world.dismissed, 2)
        self.assertEqual(world.available, 1)
        self.assertFalse(world.visible)

    def test_reward_with_existing_click_throttle_is_still_waited_for(self):
        world = World(rewards=1)
        world.visible = True
        world.click_times['safe_margin'] = world.now
        world.run()
        self.assertEqual(world.dismissed, 2)
        self.assertEqual(world.claimed, 1)
        self.assertFalse(world.visible)
        self.assertFalse(world.claim_while_reward_visible)

    def test_single_reward_does_not_require_another_claim_to_finish(self):
        world = World(rewards=1)
        world.run()
        self.assertEqual(world.dismissed, 1)
        self.assertEqual(world.claimed, 1)
        self.assertFalse(world.visible)
        self.assertLessEqual(world.now, 4.2)

    def test_absent_rewards_leave_within_the_existing_timeout(self):
        world = World(rewards=0)
        world.run()
        self.assertEqual(world.claimed, 0)
        self.assertEqual(world.dismissed, 0)
        self.assertLessEqual(world.now, 4.2)

    def test_unresponsive_reward_never_causes_another_claim_and_stays_bounded(self):
        world = World(rewards=2, stuck=True)
        world.run()
        self.assertEqual(world.claimed, 1)
        self.assertEqual(world.dismissed, 0)
        self.assertFalse(world.claim_while_reward_visible)
        self.assertLessEqual(world.now, 4.2)


if __name__ == '__main__':
    unittest.main()
