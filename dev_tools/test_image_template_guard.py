"""Offline template regressions using real OpenCV, without game or RPC services."""

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[1]


def methods(relative, class_name, names, namespace):
    tree = ast.parse((ROOT / relative).read_text(encoding='utf-8-sig'))
    source = next(node for node in tree.body
                  if isinstance(node, ast.ClassDef) and node.name == class_name)
    selected = [node for node in source.body
                if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in selected} == set(names)
    cls = ast.ClassDef(name='Subject', bases=[], keywords=[], body=selected, decorator_list=[])
    module = ast.Module(body=[ast.ImportFrom(module='__future__',
                        names=[ast.alias(name='annotations')], level=0), cls], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), relative, 'exec'), namespace)
    return namespace['Subject']


class ImageGuardTests(unittest.TestCase):
    def setUp(self):
        namespace = dict(cv2=cv2, np=np, logger=Mock())
        runtime = methods('module/image/runtime.py', 'ImageRuntime',
                          ['_crop', '_template_image_invalid', '_template_match_image',
                           '_get_multi_scale_range', '_multi_scale_template_match'], namespace)
        self.runtime = runtime()
        rule = methods('module/atom/image.py', 'RuleImage',
                       ['corp', '_template_image_invalid', 'template_match',
                        '_get_multi_scale_range', 'multi_scale_template_match',
                        '_update_roi_front', '_apply_match_result'], namespace)
        self.rule = rule()
        self.rule.roi_back = [0, 0, 40, 30]
        self.rule.roi_front = [0, 0, 10, 10]
        self.rule.threshold = 0.9
        self.rule.debug_mode = False
        self.rule.scale_range = (0.5, 1.0, 0.25)
        self.rng = np.random.default_rng(914)
        self.image = self.rng.integers(0, 256, (30, 40, 3), dtype=np.uint8)

    def match_runtime(self, template, multi=False):
        kwargs = dict(image=self.image, template=template,
                      roi_back=self.rule.roi_back, threshold=0.9, log_name='test')
        if multi:
            return self.runtime._multi_scale_template_match(
                **kwargs, scale_range=(0.5, 1.0, 0.25), scale_step=0.25)
        return self.runtime._template_match_image(**kwargs)

    def test_empty_templates_are_never_successful(self):
        for template in (None, np.empty((0, 10, 3), dtype=np.uint8),
                         np.empty((10, 0, 3), dtype=np.uint8)):
            with self.subTest(shape=None if template is None else template.shape):
                self.rule.image = template
                self.assertFalse(self.rule.template_match(self.image))
                self.assertFalse(self.rule.multi_scale_template_match(self.image))
                self.assertEqual(self.match_runtime(template), (False, -1.0, None))
                self.assertEqual(self.match_runtime(template, multi=True), (False, -1.0, None))

    def test_oversized_template_does_not_match_or_raise(self):
        for shape in ((31, 5, 3), (5, 41, 3), (31, 41, 3)):
            with self.subTest(shape=shape):
                template = self.rng.integers(0, 256, shape, dtype=np.uint8)
                self.rule.image = template
                self.assertFalse(self.rule.template_match(self.image))
                self.assertEqual(self.match_runtime(template), (False, -1.0, None))

    def test_empty_search_region_does_not_match_or_raise(self):
        self.rule.roi_back = [100, 100, 10, 10]
        template = self.image[:10, :10].copy()
        self.rule.image = template
        self.assertFalse(self.rule.template_match(self.image))
        self.assertFalse(self.rule.multi_scale_template_match(self.image))
        self.assertFalse(self.match_runtime(template)[0])
        self.assertFalse(self.match_runtime(template, multi=True)[0])

    def test_valid_template_keeps_screen_coordinates(self):
        self.rule.roi_back = [5, 3, 30, 25]
        template = self.image[8:18, 11:23].copy()
        self.rule.image = template
        self.assertTrue(self.rule.template_match(self.image))
        self.assertEqual(self.rule.roi_front, [11, 8, 12, 10])
        matched, _, roi = self.match_runtime(template)
        self.assertTrue(matched)
        self.assertEqual(roi, [11, 8, 12, 10])

    def test_large_template_can_match_after_downscaling(self):
        template = self.rng.integers(0, 256, (40, 50, 3), dtype=np.uint8)
        self.image[4:24, 6:31] = cv2.resize(template, (25, 20), interpolation=cv2.INTER_LINEAR)
        self.rule.image = template
        self.assertTrue(self.rule.multi_scale_template_match(self.image))
        self.assertEqual(self.rule.roi_front, [6, 4, 25, 20])
        matched, _, roi = self.match_runtime(template, multi=True)
        self.assertTrue(matched)
        self.assertEqual(roi, [6, 4, 25, 20])

    def test_all_scales_too_large_are_rejected(self):
        template = np.zeros((100, 120, 3), dtype=np.uint8)
        self.rule.image = template
        self.assertFalse(self.rule.multi_scale_template_match(self.image))
        self.assertEqual(self.match_runtime(template, multi=True), (False, -1.0, None))

    def animator(self):
        def dynamic(**kwargs):
            matched, score, roi = self.runtime._template_match_image(
                image=kwargs['image'], template=kwargs['template'],
                roi_back=kwargs['roi_back'], threshold=kwargs['threshold'], log_name='animation')
            return dict(matched=matched, score=score, roi_front=roi)
        client = SimpleNamespace(match_dynamic_template=Mock(side_effect=dynamic))
        cls = methods('module/atom/animate.py', 'RuleAnimate', ['stable', 'refresh'],
                      dict(get_image_client=lambda: client, logger=Mock()))
        obj = cls()
        obj._last_image = None
        obj.roi_front = [11, 8, 12, 10]
        obj.roi_back = [0, 0, 40, 30]
        obj.threshold = 0.9
        obj.name = 'animation'
        obj.corp = type(self.rule).corp.__get__(obj)
        obj._apply_match_result = type(self.rule)._apply_match_result.__get__(obj)
        return obj, client

    def test_stable_target_ignores_changes_outside_target(self):
        obj, client = self.animator()
        changed = self.rng.integers(0, 256, self.image.shape, dtype=np.uint8)
        changed[8:18, 11:23] = self.image[8:18, 11:23]
        self.assertFalse(obj.stable(self.image))
        client.match_dynamic_template.assert_not_called()
        self.assertEqual(obj._last_image.shape, (10, 12, 3))
        self.assertTrue(obj.stable(changed, frame_id='frame-2'))
        self.assertEqual(client.match_dynamic_template.call_args.kwargs['frame_id'], 'frame-2')
        self.assertEqual(obj.roi_front, [11, 8, 12, 10])

    def test_changed_target_recovers_without_expanding_to_search_region(self):
        obj, _ = self.animator()
        changed = self.rng.integers(0, 256, self.image.shape, dtype=np.uint8)
        self.assertFalse(obj.stable(self.image))
        self.assertFalse(obj.stable(changed))
        self.assertEqual(obj._last_image.shape, (10, 12, 3))
        self.assertTrue(obj.stable(changed.copy(), refresh_after_stable=True))
        self.assertIsNone(obj._last_image)
        self.assertFalse(obj.stable(changed))

    def test_out_of_screen_target_is_not_reported_stable(self):
        obj, _ = self.animator()
        obj.roi_front = [100, 100, 12, 10]
        self.assertFalse(obj.stable(self.image))
        self.assertFalse(obj.stable(self.image))


if __name__ == '__main__':
    unittest.main()
