"""Replay the completed 百鬼夜行图 story without a game or RPC service."""

from pathlib import Path
import unittest

import cv2
import numpy as np

from dev_tools.test_activity_coloring import World
from dev_tools.test_activity_coloring_view import rgb, transformed
from tasks.ActivityShikigami.coloring import ColoringError
from tasks.ActivityShikigami.coloring_view import ColoringView, ANCHORS, _template


FIXTURE = Path(__file__).with_name('fixtures') / 'activity_coloring/completion_story.png'
ERRORS = Path(__file__).resolve().parents[1] / 'log/error'
REPORTS = ('oas1_1789852027223', 'oas1_1789950485281', 'oas2_1789969348624')


def completion_confirm(image):
    image = (image * .5).astype(np.uint8)
    patch = cv2.cvtColor(_template('intro_confirm'), cv2.COLOR_GRAY2RGB)
    x, y = ANCHORS['intro_confirm']
    image[y:y+patch.shape[0], x:x+patch.shape[1]] = patch
    return image


class CompletionStoryTests(unittest.TestCase):
    def test_retained_capture_exposes_skip_at_supported_sizes_and_offsets(self):
        source = rgb(FIXTURE)
        for size in ((1280, 720), (956, 538), (840, 473)):
            with self.subTest(size=size):
                image = cv2.resize(source, size)
                view = ColoringView()
                intro = view.find_intro(image)
                self.assertIsNotNone(intro)
                self.assertEqual(intro.kind, 'skip')
                x, y, w, h = intro.action_roi
                self.assertGreaterEqual(x, size[0] * .90)
                self.assertLessEqual(x+w, size[0] * .95)
                self.assertGreaterEqual(y, size[1] * .035)
                self.assertLessEqual(y+h, size[1] * .09)
                self.assertIsNone(view.find_page(image))
                self.assertIsNone(view.find_map_entry(image))
        moved = ColoringView().find_intro(transformed(source, .9))
        self.assertIsNotNone(moved)
        self.assertEqual(moved.kind, 'skip')

    def test_three_original_reports_replay_when_local_error_archive_is_available(self):
        paths = [next((ERRORS / name).glob('*.png'), None) for name in REPORTS]
        if any(path is None for path in paths):
            self.skipTest('Local error archive absent; retained capture tested separately')
        for path in paths:
            with self.subTest(report=path.parent.name):
                intro = ColoringView().find_intro(rgb(path))
                self.assertIsNotNone(intro)
                self.assertEqual(intro.kind, 'skip')

    def test_completion_sentence_speaker_and_correct_skip_are_all_required(self):
        source = rgb(FIXTURE)
        for x, y, w, h in ((195, 616, 490, 65), (570, 548, 140, 60), (1125, 20, 115, 60)):
            with self.subTest(missing=(x, y)):
                image = source.copy()
                image[y:y+h, x:x+w] = 0
                self.assertIsNone(ColoringView().find_intro(image))
        for factor in (.5, .7):
            self.assertIsNone(ColoringView().find_intro((source * factor).astype(np.uint8)))
        misplaced = source.copy()
        misplaced[20:80, 900:1015] = misplaced[20:80, 1125:1240]
        misplaced[20:80, 1125:1240] = 0
        self.assertIsNone(ColoringView().find_intro(misplaced))

    def test_confirm_requires_completion_context_and_a_lit_button(self):
        # No real post-skip confirmation was captured. Exercise the existing
        # activity confirmation layout synthetically, retaining real context.
        image = completion_confirm(rgb(FIXTURE))
        intro = ColoringView().find_intro(image)
        self.assertIsNotNone(intro)
        self.assertEqual(intro.kind, 'confirm')
        self.assertIsNone(ColoringView().find_intro((image * .5).astype(np.uint8)))
        for x, y, w, h in ((195, 616, 490, 65), (570, 548, 140, 60)):
            incomplete = image.copy()
            incomplete[y:y+h, x:x+w] = 0
            self.assertIsNone(ColoringView().find_intro(incomplete))

    def test_actual_recognition_skips_story_then_checks_progress_without_spending(self):
        for confirm in (False, True):
            with self.subTest(confirm=confirm):
                world = World(stage='intro', currency=800, progress=100.,
                              intro_outcome='intro_confirm' if confirm else 'overview')
                original = rgb(FIXTURE)
                frames = {'intro': original, 'intro_confirm': completion_confirm(original)}
                world.view.find_intro = ColoringView().find_intro
                runner = world.runner()
                runner.capture = lambda: frames.get(world.stage, world.stage)
                result = runner.run()
                self.assertEqual((result.status, result.submissions, result.global_progress),
                                 ('complete', 0, 100.))
                self.assertEqual(world.currency, 800)
                self.assertTrue(runner.leave())
                expected = ['coloring_intro_skip']
                if confirm:
                    expected.append('coloring_intro_confirm')
                self.assertEqual(world.clicks, expected + ['coloring_back'])

    def test_restart_can_leave_story_without_spending_and_stuck_skip_is_not_repeated(self):
        image = rgb(FIXTURE)
        for outcome in ('overview', 'intro', 'unknown'):
            with self.subTest(outcome=outcome):
                world = World(stage='intro', currency=800, intro_outcome=outcome)
                world.view.find_intro = ColoringView().find_intro
                runner = world.runner()
                runner.capture = lambda: image if world.stage == 'intro' else world.stage
                if outcome == 'overview':
                    self.assertTrue(runner.leave())
                    self.assertEqual(world.clicks, ['coloring_intro_skip', 'coloring_back'])
                else:
                    with self.assertRaises(ColoringError):
                        runner.run()
                    self.assertEqual(world.clicks, ['coloring_intro_skip'])
                self.assertEqual(world.currency, 800)


if __name__ == '__main__':
    unittest.main()
