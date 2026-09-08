"""Offline checks: no device, account, OCR process, or image RPC is used.

Run: toolkit/python.exe -m unittest discover -s dev_tools -p test_monte_carlo_click.py -v
"""
import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "click_sampler_under_test", ROOT / "module/base/random_click.py")
sampler_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sampler_module)
sample = sampler_module.monte_carlo_click_point


def coord_method(relative, class_name, method, sampler):
    """Execute real coordinate methods without importing service dependencies."""
    tree = ast.parse((ROOT / relative).read_text(encoding="utf-8-sig"))
    imports = [node for node in tree.body if isinstance(node, ast.ImportFrom)
               and node.module == "module.base.random_click"]
    assert any(alias.name == "monte_carlo_click_point"
               for node in imports for alias in node.names)
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == class_name)
    func = next(node for node in cls.body
                if isinstance(node, ast.FunctionDef) and node.name == method)
    func.decorator_list = []
    namespace = dict(monte_carlo_click_point=sampler,
                     OcrMode=SimpleNamespace(FULL="full"))
    exec(compile(ast.Module(body=[func], type_ignores=[]), relative, "exec"), namespace)
    return namespace[method]


class SamplingTests(unittest.TestCase):
    def points(self, roi, count=1000, seed=712, **kwargs):
        rng = np.random.default_rng(seed)
        return np.asarray([sample(roi, rng=rng, **kwargs) for _ in range(count)])

    def test_bounds_for_varied_shapes_and_offsets(self):
        for roi in [(0, 0, 1280, 720), (1278, 718, 2, 2),
                    (-120, -5, 73, 33), (4, 8, 1, 200), (4, 8, 200, 1),
                    (0.2, 1.7, 8.4, 6.2)]:
            with self.subTest(roi=roi):
                points = self.points(roi)
                self.assertTrue(np.all(points >= np.ceil(roi[:2])))
                self.assertTrue(np.all(points < np.asarray(roi[:2]) + roi[2:]))

    def test_points_and_zero_width_lines(self):
        self.assertEqual(sample((30, 40, 0, 0)), (30, 40))
        self.assertEqual(sample((30, 40, 1, 1)), (30, 40))
        points = self.points((30, 40, 0, 10))
        self.assertTrue(np.all(points[:, 0] == 30))
        self.assertGreater(len(np.unique(points[:, 1])), 1)
        points = self.points((30, 40, 10, 0))
        self.assertTrue(np.all(points[:, 1] == 40))
        self.assertGreater(len(np.unique(points[:, 0])), 1)

    def test_tiny_targets_keep_randomness(self):
        for size in (2, 3, 4):
            points = self.points((30, 40, size, size))
            self.assertGreater(len(np.unique(points[:, 0])), 1)
            self.assertGreater(len(np.unique(points[:, 1])), 1)

    def test_seed_reproducibility_and_native_ints(self):
        rng_a = np.random.default_rng(29)
        rng_b = np.random.default_rng(29)
        for _ in range(50):
            point = sample((10, 20, 80, 40), rng=rng_a)
            self.assertEqual(point, sample((10, 20, 80, 40), rng=rng_b))
            self.assertTrue(all(type(value) is int for value in point))
        state = np.random.get_state()
        try:
            np.random.seed(29)
            first = sample((10, 20, 80, 40))
            np.random.seed(29)
            self.assertEqual(first, sample((10, 20, 80, 40)))
        finally:
            np.random.set_state(state)

    def test_convergence_and_nonzero_spread(self):
        roi = (0, 0, 1000, 600)
        first = self.points(roi, count=3000, rounds=1)
        default = self.points(roi, count=3000)
        many = self.points(roi, count=3000, rounds=12)
        center = np.asarray((499.5, 299.5))
        self.assertTrue(np.all(np.abs(default.mean(axis=0) - center) < 5))
        self.assertTrue(np.all(default.std(axis=0) < first.std(axis=0) * 0.65))
        self.assertTrue(np.all(many.std(axis=0) > np.asarray((60, 36))))
        self.assertGreater(len(np.unique(default, axis=0)), 2800)

    def test_inset_and_minimum_window(self):
        points = self.points((10, 20, 100, 100), inset=0.25, min_spread=1)
        self.assertTrue(np.all(points >= (35, 45)))
        self.assertTrue(np.all(points < (85, 95)))
        # Full retained window must continue exploring, even after many rounds.
        points = self.points((0, 0, 100, 100), rounds=12, inset=0, min_spread=1)
        self.assertTrue(np.all(points.min(axis=0) < 5))
        self.assertTrue(np.all(points.max(axis=0) > 94))

    def test_invalid_inputs_fail(self):
        for roi in [(1, 2, -1, 3), (1, 2, 3, -1), (1, 2, 3),
                    (1, 2, np.nan, 3), (np.inf, 2, 3, 4), (0.1, 0, 0.2, 1)]:
            with self.subTest(roi=roi), self.assertRaises(ValueError):
                sample(roi)
        for kwargs in [dict(samples=0), dict(samples=3.5), dict(samples=4097),
                       dict(rounds=0), dict(rounds=17), dict(rounds=2.5),
                       dict(contraction=0), dict(contraction=1),
                       dict(min_spread=0), dict(min_spread=1.1),
                       dict(inset=-0.1), dict(inset=0.5)]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                sample((0, 0, 10, 10), **kwargs)

    def test_translation_equivariance_and_no_target_memory(self):
        a = self.points((0, 0, 120, 70), count=50)
        b = self.points((700, 300, 120, 70), count=50)
        np.testing.assert_array_equal(a + (700, 300), b)

    def test_bounded_work(self):
        class CountingRng:
            def __init__(self):
                self.base = np.random.default_rng(29)
                self.calls = 0

            def uniform(self, *args, **kwargs):
                self.calls += 1
                return self.base.uniform(*args, **kwargs)

        rng = CountingRng()
        sample((0, 0, 100, 100), rng=rng)
        self.assertEqual(rng.calls, 4)


class EntrypointTests(unittest.TestCase):
    def test_click_image_and_gif_use_current_roi(self):
        for relative, cls, methods in [
            ("module/atom/click.py", "RuleClick", ("coord", "coord_more")),
            ("module/atom/image.py", "RuleImage", ("coord", "coord_more")),
            ("module/atom/gif.py", "RuleGif", ("coord",)),
        ]:
            for method in methods:
                with self.subTest(cls=cls, method=method):
                    fake = Mock(return_value=(11, 22))
                    func = coord_method(relative, cls, method, fake)
                    target = SimpleNamespace(roi_front=(1, 2, 30, 40),
                                             roi_back=(100, 200, 300, 400))
                    attr = "roi_front" if method == "coord" else "roi_back"
                    self.assertEqual(func(target), (11, 22))
                    fake.assert_called_once_with(getattr(target, attr))
                    setattr(target, attr, (500, 500, 20, 30))
                    func(target)
                    fake.assert_called_with((500, 500, 20, 30))

    def test_ocr_uses_match_area_only_for_full_mode(self):
        for mode in ("full", "single", "digit"):
            fake = Mock(return_value=(11, 22))
            func = coord_method("module/atom/ocr.py", "RuleOcr", "coord", fake)
            target = SimpleNamespace(mode=mode, area=(2, 3, 4, 5), roi=(6, 7, 8, 9))
            self.assertEqual(func(target), (11, 22))
            fake.assert_called_once_with(target.area if mode == "full" else target.roi)

    def test_invite_keeps_degenerate_area_compatibility(self):
        fake = Mock(return_value=(11, 22))
        func = coord_method("tasks/Component/GeneralInvite/general_invite.py",
                            "GeneralInvite", "_random_point_in_area", fake)
        self.assertEqual(func((10, 20, 0, -1)), (11, 22))
        fake.assert_called_once_with((10, 20, 1, 1))
        func((10, 20, 40, 50))
        fake.assert_called_with((10, 20, 40, 50))


if __name__ == "__main__":
    unittest.main()
