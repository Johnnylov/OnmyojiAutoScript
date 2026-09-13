"""Offline checks for confirmation dialogs in the active exploration workflow."""

from enum import Enum
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from test_image_template_guard import methods


class Rotation(Enum):
    yes = 'yes'
    no = 'no'


class ExplorationConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.pages = SimpleNamespace(page_exp_main='main')
        cls = methods('tasks/Exploration/script_task.py', 'ScriptTask',
                      ['_handle_confirmation_popup', 'run_on_exp_main',
                       'run_on_exp_settings'],
                      dict(pages=self.pages, AutoRotate=Rotation,
                           UserStatus=SimpleNamespace(ALONE='alone', MEMBER='member'),
                           logger=Mock()))
        self.task = task = cls()
        task.I_UI_CONFIRM = 'large-confirm'
        task.I_UI_CONFIRM_SAMLL = 'small-confirm'
        task.I_E_AUTO_ROTATE_OFF = 'rotate-off'
        task.appear = Mock(return_value=False)
        task.click = Mock(return_value=True)
        task.appear_then_click = Mock()
        task.collect_reward = Mock(return_value=True)
        task.fill_shikigami = Mock()
        task.goto_page = Mock()
        task.pre_page = None
        task._config = SimpleNamespace(exploration_config=SimpleNamespace(auto_rotate=Rotation.yes))

    def test_both_confirmation_sizes_block_underlying_main_and_settings_actions(self):
        for handler in ('run_on_exp_main', 'run_on_exp_settings'):
            for confirm in ('large-confirm', 'small-confirm'):
                with self.subTest(handler=handler, confirm=confirm):
                    self.task.appear.side_effect = lambda rule: rule == confirm
                    self.task.click.reset_mock()
                    getattr(self.task, handler)()
                    self.task.click.assert_called_once_with(confirm, interval=1)
                    self.task.collect_reward.assert_not_called()
                    self.task.fill_shikigami.assert_not_called()
                    self.task.goto_page.assert_not_called()
                    self.task.appear_then_click.assert_not_called()

    def test_throttled_confirmation_click_still_blocks_underlying_actions(self):
        self.task.appear.side_effect = lambda rule: rule == 'small-confirm'
        self.task.click.return_value = False
        self.task.run_on_exp_main()
        self.task.run_on_exp_settings()
        self.task.collect_reward.assert_not_called()
        self.task.fill_shikigami.assert_not_called()
        self.task.goto_page.assert_not_called()
        self.task.appear_then_click.assert_not_called()

    def test_main_resumes_normal_reward_handling_without_popup(self):
        self.task.run_on_exp_main()
        self.task.collect_reward.assert_called_once_with()
        self.task.click.assert_not_called()

    def test_settings_resumes_filling_and_enabling_rotation_without_popup(self):
        self.task.run_on_exp_settings()
        self.task.fill_shikigami.assert_called_once_with()
        self.task.appear_then_click.assert_called_once_with('rotate-off', interval=0.8)
        self.task.click.assert_not_called()

    def test_disabled_rotation_still_returns_to_main(self):
        self.task._config.exploration_config.auto_rotate = Rotation.no
        self.task.run_on_exp_settings()
        self.task.goto_page.assert_called_once_with('main')
        self.task.fill_shikigami.assert_not_called()
        self.task.appear_then_click.assert_not_called()


if __name__ == '__main__':
    unittest.main()
