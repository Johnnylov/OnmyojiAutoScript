"""Offline configuration and scheduling coverage for the selected task update."""

import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from module.config.config import Function
from module.config.config_manual import ConfigManual
from module.config.config_menu import ConfigMenu
from module.config.config_model import ConfigModel
from module.config.scheduler import TaskScheduler
from tasks.Script.config_optimization import ScheduleRule


class TaskRegistrationUpdateTests(unittest.TestCase):
    def test_removed_hyakkiyakou_cannot_resume_or_reenter_schedule(self):
        for marker in ('Hyakkiyakou', 'hyakkiyakou'):
            model = ConfigModel(config_name='offline', running_task=marker,
                                hyakkiyakou={'scheduler': {'enable': True}})
            self.assertEqual(model.running_task, '')
            self.assertNotIn('hyakkiyakou', model.model_dump())
            self.assertEqual(model.gui_task('Hyakkiyakou'), '')
            self.assertEqual(model.gui_args('Hyakkiyakou'), '')
        self.assertFalse(any('Hyakkiyakou' in tasks for tasks in ConfigMenu().menu.values()))
        self.assertNotIn('Hyakkiyakou', ConfigManual.SCHEDULER_PRIORITY)
        self.assertFalse((ROOT / 'tasks/Hyakkiyakou/script_task.py').exists())

    def test_legacy_removed_task_cannot_resume_or_reenter_schedule(self):
        model = ConfigModel(
            config_name='offline', running_task='Chess',
            chess={'scheduler': {'enable': True}},
        )
        self.assertEqual(model.running_task, '')
        data = model.model_dump()
        self.assertNotIn('chess', data)
        self.assertFalse(any(Function(key, value).command == 'Chess'
                             for key, value in data.items()))
        self.assertEqual(model.gui_task('Chess'), '')

    def test_moonlight_has_independent_disabled_configuration(self):
        model = ConfigModel(config_name='offline', running_task='Orochi')
        self.assertEqual(model.running_task, 'Orochi')
        self.assertFalse(model.moonlight.scheduler.enable)
        self.assertEqual(model.moonlight.general_config.challenge_limit, 1)
        before = model.activity_shikigami.model_dump()
        model.moonlight.general_config.challenge_limit = 3
        self.assertEqual(model.activity_shikigami.model_dump(), before)
        self.assertIn('moonlight_battle_conf', model.gui_task('Moonlight'))
        menu = ConfigMenu().menu
        self.assertIn('Moonlight', menu['Activity Task'])
        self.assertNotIn('Chess', menu['Weekly Task'])

    def test_moonlight_legacy_config_exposes_and_saves_soul_switch(self):
        template = json.loads((ROOT / 'config/template.json').read_text(encoding='utf-8'))['moonlight']
        legacy = dict(template)
        expected = legacy.pop('switch_soul')
        model = ConfigModel(config_name='offline', running_task='', moonlight=legacy)
        self.assertEqual(model.moonlight.switch_soul.model_dump(), expected)
        self.assertEqual(expected, dict(enable=False, switch_group_team='-1,-1',
                                       enable_switch_by_name=False, group_name='', team_name=''))
        fields = {field['name']: field for field in model.script_task('Moonlight')['switch_soul']}
        self.assertEqual({name: field['value'] for name, field in fields.items()}, expected)
        self.assertEqual(fields['enable']['title'], '按编号切换御魂')
        other_task = model.fallen_sun.switch_soul.model_dump()
        with patch.object(ConfigModel, 'save') as save:
            for field, value in dict(enable=True, switch_group_team='3,2', enable_switch_by_name=True,
                                     group_name='活动', team_name='月华流光').items():
                self.assertTrue(model.script_set_arg('Moonlight', 'switch_soul', field, value))
            self.assertEqual(save.call_count, 5)
        restored = ConfigModel(config_name='offline', running_task='',
                               moonlight=json.loads(model.gui_task('Moonlight')))
        self.assertEqual(restored.moonlight.switch_soul, model.moonlight.switch_soul)
        self.assertEqual(model.fallen_sun.switch_soul.model_dump(), other_task)

    def test_enabled_moonlight_survives_priority_filter(self):
        model = ConfigModel(config_name='offline', running_task='')
        model.moonlight.scheduler.enable = True
        task = Function('moonlight', model.moonlight.model_dump())
        self.assertTrue(task.enable)
        self.assertEqual(task.command, 'Moonlight')
        self.assertIn('Moonlight', ConfigManual.SCHEDULER_PRIORITY)
        self.assertIn(task, TaskScheduler.schedule(ScheduleRule.FILTER, [task]))


if __name__ == '__main__':
    unittest.main()
