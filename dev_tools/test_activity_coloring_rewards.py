"""Replay retained real reward frames through the resource transaction runner."""

from pathlib import Path
import unittest

import cv2

from dev_tools.test_activity_coloring import World
from tasks.ActivityShikigami.coloring_view import ColoringView


FIXTURES = Path(__file__).with_name('fixtures') / 'activity_coloring'


class RetainedRewardReplayTests(unittest.TestCase):
    def test_real_reward_frames_close_then_confirm_the_remaining_stock_and_leave(self):
        for name, before, after in (('reward_coin', 3512, 2513), ('reward_daruma', 6604, 5605)):
            with self.subTest(fixture=name):
                image = cv2.cvtColor(cv2.imread(str(FIXTURES / f'{name}.png')), cv2.COLOR_BGR2RGB)
                world = World(stage='panel', currency=before, capacity=999, outcome='reward')
                # Real recognition decides whether/where to close the reward.
                # Normal page/counter responses are injected; no frame of the
                # post-dismissal game was supplied and none is fabricated here.
                world.view.find_reward = ColoringView().find_reward
                runner = world.runner()
                runner.capture = lambda: image if world.stage == 'reward' else world.stage
                runner.MAX_SUBMISSIONS = 1
                result = runner.run()
                self.assertEqual((result.submissions, world.currency), (1, after))
                self.assertTrue(runner.leave())
                self.assertEqual(world.clicks, ['coloring_max', 'coloring_submit', 'coloring_reward_close_0',
                                               'coloring_collapse', 'coloring_back'])


if __name__ == '__main__':
    unittest.main()
