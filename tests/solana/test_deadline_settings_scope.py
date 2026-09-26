import unittest

from module.config.config_menu import ConfigMenu
from module.config.config_model import ConfigModel


class DeadlineSettingsScopeTests(unittest.TestCase):
    def test_only_limited_activity_pages_expose_the_cutoff_switch(self):
        model = ConfigModel.model_construct()
        menu = ConfigMenu().gui_menu_list
        activities = set(menu['Activity Task'])
        shown = set()
        for tasks in menu.values():
            for task in tasks:
                with self.subTest(task=task):
                    scheduler = model.script_task(task).get('scheduler', [])
                    visible = any(field['name'] == 'real_deadline' for field in scheduler)
                    self.assertEqual(visible, task in activities)
                    if visible:
                        shown.add(task)
        self.assertEqual(shown, activities)
        self.assertEqual(len(shown), 10)

    def test_task_spelling_and_stored_schema_stay_compatible(self):
        model = ConfigModel.model_construct()
        self.assertNotIn('real_deadline', {f['name'] for f in model.script_task('orochi')['scheduler']})
        self.assertIn('real_deadline', {f['name'] for f in model.script_task('activity_shikigami')['scheduler']})
        # The display filter neither writes nor removes existing persisted data.
        self.assertIn('real_deadline', model.orochi.scheduler.model_dump())


if __name__ == '__main__':
    unittest.main()
