"""Scheduler lifecycle regressions for interrupted and unfinished True Orochi."""
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from dev_tools.test_activity_preparation_retry import source_methods
from module.exception import TaskDeferred, TaskEnd


class FixedDatetime(datetime):
    @classmethod
    def now(cls):
        return cls(2026, 9, 17, 19, 48, 18)


class TrueOrochiRetryTests(unittest.TestCase):
    def scheduler(self, exception):
        task = Mock()
        task.run.side_effect = exception
        namespace = dict(
            datetime=FixedDatetime, timedelta=timedelta, Path=Path,
            logger=Mock(), TaskEnd=TaskEnd,
            load_module=Mock(return_value=SimpleNamespace(ScriptTask=Mock(return_value=task))),
        )
        subject = source_methods('script.py', 'Script', [
            'run', '_handle_task_exception', '_reset_task_runtime_outcome',
            '_set_task_runtime_outcome', '_capture_task_runtime_outcome',
        ], namespace)
        scheduler = subject()
        scheduler.config = SimpleNamespace(task_delay=Mock(), task_runtime_outcome=None)
        scheduler.device = Mock()
        scheduler.team_sync = Mock()
        return scheduler

    def test_interruption_retries_in_two_minutes_without_completing_session(self):
        scheduler = self.scheduler(TaskDeferred('旧真蛇会话已中断'))
        self.assertTrue(scheduler.run('TrueOrochi'))
        expected = FixedDatetime.now() + timedelta(minutes=2)
        scheduler.config.task_delay.assert_called_once_with(
            task='TrueOrochi', target=expected, server=False)
        scheduler.team_sync.finish.assert_called_once_with('TrueOrochi', False)
        self.assertEqual(scheduler.last_task_runtime_outcome,
                         dict(task='TrueOrochi', status='retry_scheduled', wait_until=expected))

    def test_no_entries_delay_is_preserved_without_completing_session(self):
        scheduler = self.scheduler(TaskDeferred('没有真蛇入口', retry_after=86400))
        scheduler.run('TrueOrochi')
        scheduler.config.task_delay.assert_called_once_with(
            task='TrueOrochi', target=FixedDatetime.now() + timedelta(days=1), server=False)
        scheduler.team_sync.finish.assert_called_once_with('TrueOrochi', False)

    def test_deferred_task_cannot_busy_loop_with_zero_failure_interval(self):
        scheduler = self.scheduler(TaskDeferred('unfinished', retry_after=0))
        scheduler.run('TrueOrochi')
        scheduler.config.task_delay.assert_called_once_with(
            task='TrueOrochi', target=FixedDatetime.now() + timedelta(minutes=1), server=False)

    def test_verified_completion_still_finishes_session(self):
        scheduler = self.scheduler(TaskEnd('TrueOrochi'))
        scheduler.run('TrueOrochi')
        scheduler.team_sync.finish.assert_called_once_with('TrueOrochi', True)
        scheduler.config.task_delay.assert_not_called()


if __name__ == '__main__':
    unittest.main()
