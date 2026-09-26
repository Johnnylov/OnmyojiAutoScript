"""Exercise the real config edit method with isolated models and persistence spies."""
import ast
from datetime import datetime, time, timedelta
from enum import Enum
from pathlib import Path
import re
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock

from pydantic import BaseModel, Field, ValidationError, model_validator

from module.config.utils import convert_to_underscore
from tasks.Component.config_base import ConfigBase
from tasks.Component.config_scheduler import Scheduler
from tasks.Restart.config import TasksReset


ROOT = Path(__file__).resolve().parents[1]
tree = ast.parse((ROOT / 'module/config/config_model.py').read_text(encoding='utf-8'))
model = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'ConfigModel')
method = next(node for node in model.body if isinstance(node, ast.FunctionDef) and node.name == 'script_set_arg')
namespace = dict(BaseModel=BaseModel, ValidationError=ValidationError, datetime=datetime, timedelta=timedelta,
                 re=re, convert_to_underscore=convert_to_underscore, logger=Mock())
exec(compile(ast.Module(body=[method], type_ignores=[]), str(ROOT / 'module/config/config_model.py'), 'exec'), namespace)


class Choice(str, Enum):
    FIRST = 'first'
    SECOND = 'second'


class Entry(BaseModel):
    name: str
    count: int = Field(ge=1)


class Group(ConfigBase):
    choices: list[Choice] = Field(default_factory=lambda: [Choice.FIRST])
    entries: list[Entry] = Field(default_factory=lambda: [Entry(name='old', count=2)])
    text: str = 'original'
    optional: str | None = None


class Task(BaseModel):
    group: Group = Field(default_factory=Group)
    scheduler: Scheduler = Field(default_factory=Scheduler)
    groups: list[Group] = Field(default_factory=lambda: [Group(), Group()])
    tasks_config_reset: TasksReset = Field(default_factory=TasksReset)


class ConfigSaveValidationTests(unittest.TestCase):
    def setUp(self):
        self.config = SimpleNamespace(config_name='isolated-test', sample=Task(), restart=Task(),
                                      save=Mock(), reset_datetime_for_all_enabled_tasks=Mock())
        self.set_arg = MethodType(namespace['script_set_arg'], self.config)

    def test_scalar_cannot_replace_multi_select_or_change_memory(self):
        group = self.config.sample.group
        before = group.model_dump()
        self.assertFalse(self.set_arg('Sample', 'group', 'choices', 'second'))
        self.assertIs(self.config.sample.group, group)
        self.assertEqual(group.model_dump(), before)
        self.config.save.assert_not_called()

    def test_invalid_enum_member_is_rejected(self):
        self.assertFalse(self.set_arg('Sample', 'group', 'choices', ['unknown']))
        self.assertEqual(self.config.sample.group.choices, [Choice.FIRST])
        self.config.save.assert_not_called()

    def test_valid_multi_select_is_converted_and_saved_once(self):
        self.assertTrue(self.set_arg('Sample', 'group', 'choices', ['second', 'first']))
        self.assertEqual(self.config.sample.group.choices, [Choice.SECOND, Choice.FIRST])
        self.assertIsInstance(self.config.sample.group.choices[0], Choice)
        self.config.save.assert_called_once_with()

    def test_real_config_base_range_error_does_not_silently_use_default(self):
        scheduler = self.config.sample.scheduler
        scheduler.delay_date = 7
        for value in (0, 32, 'not-a-number'):
            with self.subTest(value=value):
                self.assertFalse(self.set_arg('Sample', 'scheduler', 'delay_date', value))
                self.assertEqual(scheduler.delay_date, 7)
        self.config.save.assert_not_called()

    def test_real_scheduler_date_time_duration_boolean_and_integer_conversion(self):
        cases = (
            ('next_run', '2026-09-24 15:20:30', datetime(2026, 9, 24, 15, 20, 30)),
            ('server_update', '09:30:15', time(9, 30, 15)),
            ('success_interval', '00 01:02:03', timedelta(hours=1, minutes=2, seconds=3)),
            ('failure_interval', '123 01:02:03', timedelta(days=123, hours=1, minutes=2, seconds=3)),
            ('enable', 'true', True),
            ('enable', 'false', False),
            ('priority', '8', 8),
            ('next_run', datetime(2026, 9, 25), datetime(2026, 9, 25)),
        )
        for argument, value, expected in cases:
            with self.subTest(argument=argument, value=value):
                self.assertTrue(self.set_arg('Sample', 'scheduler', argument, value))
                self.assertEqual(getattr(self.config.sample.scheduler, argument), expected)
                self.assertIs(type(getattr(self.config.sample.scheduler, argument)), type(expected))
        self.assertEqual(self.config.save.call_count, len(cases))

    def test_invalid_datetime_and_boolean_do_not_save(self):
        before = self.config.sample.scheduler.model_dump()
        for argument, value in (('next_run', 'not-a-date'), ('server_update', '26:30:00'),
                                ('enable', 'maybe')):
            self.assertFalse(self.set_arg('Sample', 'scheduler', argument, value))
        self.assertEqual(self.config.sample.scheduler.model_dump(), before)
        self.config.save.assert_not_called()

    def test_invalid_interval_never_falls_back_to_one_day(self):
        scheduler = self.config.sample.scheduler
        scheduler.success_interval = timedelta(hours=2)
        for value in ('broken', '00 01:02:03 trailing', '00 24:00:00', '00 01:60:00',
                      '00 01:00:60', '1000000000000 00:00:00'):
            with self.subTest(value=value):
                self.assertFalse(self.set_arg('Sample', 'scheduler', 'success_interval', value))
                self.assertEqual(scheduler.success_interval, timedelta(hours=2))
        self.config.save.assert_not_called()

    def test_text_that_looks_like_a_time_or_boolean_remains_text(self):
        for value in ('true', 'false', '09:30:15', '00 01:02:03', '2026-09-24 15:20:30'):
            self.assertTrue(self.set_arg('Sample', 'group', 'text', value))
            self.assertEqual(self.config.sample.group.text, value)
        self.assertTrue(self.set_arg('Sample', 'group', 'optional', 'set nullable field'))
        self.assertEqual(self.config.sample.group.optional, 'set nullable field')

    def test_nested_list_values_validate_before_replacing_original_list(self):
        group = self.config.sample.group
        old_entries = group.entries
        self.assertFalse(self.set_arg('Sample', 'group', 'entries', [{'name': 'bad', 'count': 0}]))
        self.assertIs(group.entries, old_entries)
        self.config.save.assert_not_called()
        self.assertTrue(self.set_arg('Sample', 'group', 'entries', [{'name': 'new', 'count': '3'}]))
        self.assertIsInstance(group.entries[0], Entry)
        self.assertEqual(group.entries[0].count, 3)
        self.assertEqual(old_entries[0].name, 'old')

    def test_numbered_group_only_updates_requested_item(self):
        self.assertTrue(self.set_arg('Sample', 'groups_2', 'text', 'second group'))
        self.assertEqual(self.config.sample.groups[0].text, 'original')
        self.assertEqual(self.config.sample.groups[1].text, 'second group')
        self.assertTrue(self.set_arg('Sample', 'groups1', 'text', 'first group'))
        self.assertEqual(self.config.sample.groups[0].text, 'first group')
        self.config.save.reset_mock()
        for task, group, argument in (('missing', 'group', 'text'), ('Sample', 'missing', 'text'),
                                      ('Sample', 'groups_0', 'text'), ('Sample', 'groups_3', 'text'),
                                      ('Sample', 'groups', 'text'), ('Sample', 'group', 'missing')):
            self.assertFalse(self.set_arg(task, group, argument, 'invalid edit'))
        self.config.save.assert_not_called()

    def test_model_validator_cannot_mutate_live_nested_values_on_failure(self):
        class GuardedGroup(BaseModel):
            entries: list[int] = Field(default_factory=lambda: [1])
            enabled: bool = False

            @model_validator(mode='after')
            def reject_enabled(self):
                if self.enabled:
                    self.entries.append(99)
                    raise ValueError('cannot enable')
                return self

        class GuardedTask(BaseModel):
            group: GuardedGroup = Field(default_factory=GuardedGroup)

        self.config.sample = GuardedTask()
        self.assertFalse(self.set_arg('Sample', 'group', 'enabled', True))
        self.assertEqual(self.config.sample.group.entries, [1])
        self.assertFalse(self.config.sample.group.enabled)
        self.config.save.assert_not_called()

    def test_reset_validation_precedes_one_atomic_persistent_transaction(self):
        # Test the resulting real file, rather than require the former two-write
        # ordering (reset schedules first, then save the enable switch).
        import json
        import os
        import tempfile
        from unittest.mock import patch
        from module.config.config_model import ConfigModel
        from module.config import config_model

        previous_cwd = Path.cwd()
        target = datetime(2026, 9, 24, 16, 0)
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                real = ConfigModel('isolated-validation')
                real.restart.tasks_config_reset.reset_task_datetime = target
                real.save()
                path = Path(directory) / 'config' / 'isolated-validation.json'
                before = path.read_bytes()
                with patch.object(ConfigModel, 'reset_datetime_for_all_enabled_tasks') as reset:
                    self.assertFalse(real.script_set_arg('Restart', 'tasks_config_reset',
                                                        'reset_task_datetime_enable', 'invalid'))
                    reset.assert_not_called()
                self.assertEqual(path.read_bytes(), before)
                with patch.object(config_model, 'write_file', wraps=config_model.write_file) as write:
                    self.assertTrue(real.script_set_arg('Restart', 'tasks_config_reset',
                                                       'reset_task_datetime_enable', 'true'))
                    self.assertEqual(write.call_count, 1)
                stored = json.loads(path.read_text(encoding='utf-8'))
                self.assertTrue(stored['restart']['tasks_config_reset']['reset_task_datetime_enable'])
                self.assertEqual(stored['orochi']['scheduler']['next_run'], '2026-09-24 16:00:00')
                self.assertEqual(stored['mystery_shop']['scheduler']['next_run'], '2026-09-24 16:00:00')
            finally:
                os.chdir(previous_cwd)

    def test_reset_default_datetime_is_normalized_before_reset(self):
        self.assertTrue(self.set_arg('Restart', 'tasks_config_reset', 'reset_task_datetime_enable', True))
        self.config.reset_datetime_for_all_enabled_tasks.assert_called_once_with(datetime(2023, 1, 1))


if __name__ == '__main__':
    unittest.main()
