"""Offline configuration and scheduling coverage for the selected task update."""

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from module.config.config import Function
from module.config.config_manual import ConfigManual
from module.config.config_menu import ConfigMenu
from module.config.config_model import ConfigModel
from module.config.scheduler import TaskScheduler
from tasks.Script.config_optimization import ScheduleRule


class TaskRegistrationUpdateTests(unittest.TestCase):
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
