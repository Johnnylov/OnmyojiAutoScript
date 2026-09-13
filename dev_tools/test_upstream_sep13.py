"""Offline checks for the September 13 upstream adaptations."""

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from module.atom.ocr import RuleOcr
from tasks.ActivityShikigami.assets import ActivityShikigamiAssets


class ClimbCounterTests(unittest.TestCase):
    def test_climb_counters_return_numbers_and_correct_digit_shapes(self):
        for rule in (ActivityShikigamiAssets.O_REMAIN_AP,
                     ActivityShikigamiAssets.O_REMAIN_PASS):
            for raw, expected in (('120', 120), ('1O0', 100), ('0012', 12),
                                  ('0', 0), ('00', 0), ('000', 0), ('', 0)):
                with self.subTest(rule=rule.name, raw=raw):
                    result = rule.after_process(raw)
                    self.assertIsInstance(result, int)
                    self.assertEqual(result, expected)

    def test_global_zero_ocr_stays_zero(self):
        rule = RuleOcr(roi=(0, 0, 20, 20), area=(0, 0, 20, 20),
                       mode='Digit', method='Default', keyword='', name='zero')
        for raw in ('0', '00', '000', 'OO'):
            with self.subTest(raw=raw):
                self.assertEqual(rule.after_process(raw), 0)


if __name__ == '__main__':
    unittest.main()
