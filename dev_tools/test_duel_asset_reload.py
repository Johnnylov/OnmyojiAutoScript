"""Replay cached pre-gift assets through the actual task-module loader.

entry_animation_2026-09-17.png retains only (720, 190, 330, 370) from the
14:00:36 error oas1_1789624836527. It excludes player names, scores and chat;
the actual failure happened on this animation with no gift dialog visible.
"""

import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
MARKERS = ('I_D_EVENT_GIFT_PROTECT', 'I_D_EVENT_GIFT_COUPON', 'I_D_EVENT_GIFT_ACCEPT')


def declarations(path, class_name):
    tree = ast.parse((ROOT / path).read_text(encoding='utf-8-sig'))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    return {node.targets[0].id: {kw.arg: ast.literal_eval(kw.value) for kw in node.value.keywords}
            for node in cls.body if isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Name) and node.targets[0].id in MARKERS}


def replay_in_child():
    """Isolate sys.modules replacement from the rest of the test suite."""
    sys.path.insert(0, str(ROOT))
    from types import ModuleType, SimpleNamespace
    from unittest.mock import Mock, patch
    import cv2
    import numpy as np
    from module.base.utils import load_module
    from module.config.config import Config
    from module.device.device import Device
    import tasks.Duel.assets as current_assets

    old_members = {name: value for name, value in vars(current_assets.DuelAssets).items()
                   if not name.startswith('__') and name not in MARKERS}
    old_assets = type('DuelAssets', (), old_members)
    cached_module = ModuleType('tasks.Duel.assets')
    cached_module.__dict__.update(vars(current_assets))
    cached_module.DuelAssets = old_assets
    original_members = dict(vars(old_assets))

    def fixture(name, roi):
        crop = cv2.imread(str(ROOT / 'dev_tools/fixtures/duel' / name))
        x, y, w, h = roi
        assert crop is not None and crop.shape == (h, w, 3)
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        image[y:y+h, x:x+w] = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        return image

    def task_for(cls, frames):
        task = object.__new__(cls)  # Never construct Config, Device or BaseTask.
        task.device = SimpleNamespace(image=frames[0])
        task.appear = lambda marker: marker.template_match(task.device.image)
        task.appear_then_click = Mock(return_value=False)
        task.is_battle_end = Mock(return_value=False)
        task.click = Mock(return_value=True)
        remaining = iter(frames[1:])
        def screenshot():
            task.device.image = next(remaining)
            return task.device.image
        task.screenshot = Mock(side_effect=screenshot)
        return task

    with patch.dict(sys.modules, {'tasks.Duel.assets': cached_module}), \
            patch.object(Config, '__init__', side_effect=AssertionError('No config construction')), \
            patch.object(Device, '__init__', side_effect=AssertionError('No device construction')):
        first = load_module('script_task', str(ROOT / 'tasks/Duel/script_task.py'))
        fresh = load_module('script_task', str(ROOT / 'tasks/Duel/script_task.py'))
        assert first.ScriptTask is not fresh.ScriptTask
        for cls in (first.ScriptTask, fresh.ScriptTask):
            assert old_assets in cls.__mro__
            assert all(name in vars(cls) for name in MARKERS)
            animation = fixture('entry_animation_2026-09-17.png', (720, 190, 330, 370))
            task = task_for(cls, [animation])
            assert task.duel_popup_handle() is False
            task.click.assert_not_called()
            task.screenshot.assert_not_called()

        gift = fixture('event_gift_1432.png', (622, 185, 426, 366))
        clear = np.zeros_like(gift)
        task = task_for(fresh.ScriptTask, [gift, gift, clear, clear])
        assert task.duel_popup_handle() is True
        task.click.assert_called_once_with(task.I_D_EVENT_GIFT_ACCEPT, interval=1.2)
        assert task.screenshot.call_count == 3
        assert sys.modules['tasks.Duel.assets'] is cached_module
        assert vars(old_assets) == original_members
        assert all(not hasattr(old_assets, name) for name in MARKERS)
    print('DUEL_HOT_RELOAD_OK')


class DuelAssetReloadTests(unittest.TestCase):
    def test_real_loader_keeps_old_base_while_new_task_handles_animation_and_gift(self):
        env = dict(os.environ, PYTHONUTF8='1')
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--reload-child'],
                                cwd=ROOT, env=env, capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('DUEL_HOT_RELOAD_OK', result.stdout)

    def test_reload_safe_rules_match_generated_assets_and_source_metadata(self):
        task = declarations('tasks/Duel/script_task.py', 'ScriptTask')
        assets = declarations('tasks/Duel/assets.py', 'DuelAssets')
        self.assertEqual(set(task), set(MARKERS))
        self.assertEqual(task, assets)
        entries = json.loads((ROOT / 'tasks/Duel/duel/image.json').read_text(encoding='utf-8'))
        for entry in entries:
            name = 'I_' + entry['itemName'].upper()
            if name not in MARKERS:
                continue
            self.assertEqual(task[name], dict(
                roi_front=tuple(map(int, entry['roiFront'].split(','))),
                roi_back=tuple(map(int, entry['roiBack'].split(','))),
                threshold=entry['threshold'], method=entry['method'],
                file='./tasks/Duel/duel/' + entry['imageName']))
        self.assertEqual({entry['itemName'].upper() for entry in entries
                          if 'I_' + entry['itemName'].upper() in MARKERS},
                         {name[2:] for name in MARKERS})


if __name__ == '__main__':
    if '--reload-child' in sys.argv:
        replay_in_child()
    else:
        unittest.main()
