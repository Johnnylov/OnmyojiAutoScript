"""Offline current-event map recognition using the production asset metadata.

Full clipboard references are resized to the required 1280 x 720 runtime.
The clipped idle-map reference is intentionally not stretched into a full
screen: its missing margins would move every fixed navigation anchor.
"""

import ast
import json
from pathlib import Path
import os
import tempfile
import unittest

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
TEMP = Path(os.environ.get('ACTIVITY_REFERENCE_DIR', tempfile.gettempdir()))
REFERENCES = {
    'running': '95beef8e-89c2-4714-b256-9be8932833dc',
    'annotated_map': '4be37c8e-260c-41e3-b9f5-d1c0befee047',
    'portraits': '86eb7849-a5ab-4404-aa7f-e1b276e75279',
    'dispatch_setup': '4909a6ae-dd6d-416e-817f-3962c2642354',
    'painting': 'a2fe886d-71a0-4747-998c-e4e6d53b1e45',
    'painting_setup': 'dc69021d-2481-42a4-bda6-bba98c26a532',
}


def asset_metadata():
    tree = ast.parse((ROOT / 'tasks/ActivityShikigami/assets.py').read_text(encoding='utf-8'))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    rule = next(node.value for node in cls.body if isinstance(node, ast.Assign)
                and node.targets[0].id == 'I_MAP_GOTO_BATTLE')
    return {kw.arg: ast.literal_eval(kw.value) for kw in rule.keywords}


class ActivityMapNavigationTests(unittest.TestCase):
    def setUp(self):
        self.asset = asset_metadata()
        self.template = cv2.imread(str(ROOT / self.asset['file']))
        self.assertIsNotNone(self.template)

    def match(self, image):
        x, y, width, height = self.asset['roi_back']
        scores = cv2.matchTemplate(image[y:y + height, x:x + width], self.template,
                                   cv2.TM_CCOEFF_NORMED)
        _, score, _, point = cv2.minMaxLoc(scores)
        return score, (x + point[0], y + point[1])

    def test_small_label_shift_still_recognizes_map_in_runtime_resolution(self):
        # Actual full reference is four pixels right of the original exact ROI.
        image = np.full((720, 1280, 3), 27, dtype=np.uint8)
        height, width = self.template.shape[:2]
        image[93:93 + height, 75:75 + width] = self.template
        score, point = self.match(image)
        self.assertGreaterEqual(score, self.asset['threshold'])
        self.assertEqual(point, (75, 93))

    def test_label_elsewhere_on_screen_cannot_identify_map(self):
        image = np.full((720, 1280, 3), 27, dtype=np.uint8)
        height, width = self.template.shape[:2]
        image[300:300 + height, 550:550 + width] = self.template
        score, _ = self.match(image)
        self.assertLess(score, self.asset['threshold'])

    def test_asset_and_source_metadata_stay_in_sync_without_expanding_click_region(self):
        entries = json.loads((ROOT / 'tasks/ActivityShikigami/as/pages.json').read_text(encoding='utf-8'))
        entry = next(row for row in entries if row['itemName'] == 'map_goto_battle')
        self.assertEqual(self.asset['roi_front'], tuple(map(int, entry['roiFront'].split(','))))
        self.assertEqual(self.asset['roi_back'], tuple(map(int, entry['roiBack'].split(','))))
        self.assertEqual(self.asset['roi_front'], (71, 93, 99, 36))
        self.assertEqual(self.asset['threshold'], entry['threshold'])

    def test_supplied_full_map_references_match_but_open_panels_do_not(self):
        paths = {key: TEMP / f'codex-clipboard-{suffix}.png'
                 for key, suffix in REFERENCES.items()}
        if not all(path.exists() for path in paths.values()):
            self.skipTest('Local clipboard references are no longer available')
        for key, path in paths.items():
            with self.subTest(reference=key):
                original = cv2.imread(str(path))
                self.assertIsNotNone(original)
                height, width = original.shape[:2]
                self.assertLess(abs(width / height - 1280 / 720), 0.01)
                runtime = cv2.resize(original, (1280, 720), interpolation=cv2.INTER_LINEAR)
                score, point = self.match(runtime)
                if key in ('running', 'annotated_map'):
                    self.assertGreaterEqual(score, self.asset['threshold'])
                    self.assertEqual(point, (75, 93))
                else:
                    self.assertLess(score, self.asset['threshold'])


if __name__ == '__main__':
    unittest.main()
