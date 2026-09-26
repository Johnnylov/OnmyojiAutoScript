"""Real persisted deadline auto-disable with existing config CAS and audit."""
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from module.config.config import Config
from module.config.config_model import ConfigModel
from module.server.solana_adapter import ManagerAdapter
from module.server.solana_deadlines import DeadlineMonitor
from module.server.solana_runtime import RuntimeService
from module.observability import EventStore


class Adapter(ManagerAdapter):
    @property
    def manager(self):
        return SimpleNamespace(all_script_files=lambda: ['trial'], config_cache=lambda name: Config(name))


class DeadlineAutoDisableTests(unittest.TestCase):
    def setUp(self):
        self.previous = Path.cwd()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'config').mkdir()
        os.chdir(self.root)
        data = ConfigModel().model_dump()
        data['config_name'] = 'trial'
        self.cutoff = datetime.now(timezone.utc) - timedelta(minutes=1)
        data['orochi']['scheduler'].update(enable=True, real_deadline=self.cutoff.isoformat())
        data['area_boss']['scheduler'].update(enable=True, real_deadline='')
        data['private_custom_value'] = {'preserved': 37}
        self.path = self.root / 'config' / 'trial.json'
        self.path.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding='utf-8')
        self.store = EventStore(self.root / 'runtime_data')
        self.adapter = Adapter()
        self.service = RuntimeService(self.store, self.adapter)
        self.monitor = DeadlineMonitor(self.service)
        self.disabled = logging.getLogger('oas').disabled
        logging.getLogger('oas').disabled = True

    def tearDown(self):
        self.monitor.stop()
        self.monitor.join()
        self.service.close()
        os.chdir(self.previous)
        logging.getLogger('oas').disabled = self.disabled
        self.temp.cleanup()

    def raw(self):
        return json.loads(self.path.read_text(encoding='utf-8'))

    def test_stopped_profile_persists_unchecked_task_and_single_audit(self):
        self.assertFalse(self.service.owners)
        before = self.raw()
        self.assertEqual(self.monitor.scan_due(), [('trial', 'orochi')])
        expected = json.loads(json.dumps(before))
        expected['orochi']['scheduler']['enable'] = False
        self.assertEqual(self.raw(), expected, 'only the task enable field may change')
        self.assertFalse(ConfigModel('trial').orochi.scheduler.enable)
        self.assertEqual(self.raw()['orochi']['scheduler']['real_deadline'], self.cutoff.isoformat())
        events = self.store.query(filters={'type': 'config.changed'}, limit=20)['items']
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event['source']['type'], 'backend')
        self.assertEqual(event['payload']['reason'], 'deadline_expired')
        self.assertEqual(event['payload']['task_id'], 'orochi')
        self.assertEqual(event['payload']['changes'], [{'path': 'orochi.scheduler.enable', 'old': True, 'new': False}])
        stamp = self.path.stat().st_mtime_ns
        for _ in range(3):
            self.assertEqual(self.monitor.scan_due(), [])
        self.assertEqual(self.path.stat().st_mtime_ns, stamp)
        self.assertEqual(len(self.store.query(filters={'type': 'config.changed'}, limit=20)['items']), 1)

    def test_editing_deadline_or_disabling_limit_wins_over_cached_due_value(self):
        for value in ((datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), ''):
            with self.subTest(value=value):
                model = ConfigModel('trial')
                model.script_set_arg('orochi', 'scheduler', 'real_deadline', self.cutoff.isoformat())
                self.monitor.cache.clear()
                self.assertEqual(self.monitor._due('trial', datetime.now().timestamp()), ['orochi'])
                ConfigModel('trial').script_set_arg('orochi', 'scheduler', 'real_deadline', value)
                self.assertIsNone(self.monitor.disable_if_expired('trial', 'orochi'))
                self.assertTrue(self.raw()['orochi']['scheduler']['enable'])
                self.assertEqual(self.raw()['orochi']['scheduler']['real_deadline'], value)

    def test_running_task_writer_preserves_auto_uncheck_and_other_edits(self):
        worker = Config('trial')
        ConfigModel('trial').script_set_arg('area_boss', 'scheduler', 'priority', 9)
        self.monitor.scan_due()
        worker.model.running_task = 'Orochi'
        worker.task_delay('Orochi', target=datetime.now() + timedelta(days=2), server=False)
        current = self.raw()
        self.assertFalse(current['orochi']['scheduler']['enable'])
        self.assertEqual(current['area_boss']['scheduler']['priority'], 9)
        self.assertEqual(current['private_custom_value'], {'preserved': 37})

    def test_reenabling_expired_date_is_a_new_disable_not_stale_idempotency(self):
        self.monitor.scan_due()
        ConfigModel('trial').script_set_arg('orochi', 'scheduler', 'enable', True)
        self.assertEqual(self.monitor.scan_due(), [('trial', 'orochi')])
        self.assertFalse(self.raw()['orochi']['scheduler']['enable'])
        self.assertEqual(len(self.store.query(filters={'type': 'config.changed'}, limit=20)['items']), 2)

    def test_extending_deadline_then_reenabling_remains_enabled(self):
        self.monitor.scan_due()
        model = ConfigModel('trial')
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        model.script_set_arg('orochi', 'scheduler', 'real_deadline', future)
        model.script_set_arg('orochi', 'scheduler', 'enable', True)
        self.assertEqual(self.monitor.scan_due(), [])
        self.assertTrue(self.raw()['orochi']['scheduler']['enable'])

    def test_unchanged_files_are_not_reparsed_each_tick_and_io_failure_retries(self):
        self.monitor.scan_due()
        self.monitor.scan_due()  # refresh after the persisted mutation
        with patch.object(self.adapter, 'raw', wraps=self.adapter.raw) as raw:
            self.monitor.scan_due()
            self.monitor.scan_due()
            self.assertEqual(raw.call_count, 0)
        self.monitor.cache.clear()
        with patch.object(self.adapter, 'raw', side_effect=OSError('temporary')):
            self.assertEqual(self.monitor.scan_due(), [])
        self.assertIn('trial', self.monitor.retry_after)
        self.monitor.retry_after.clear()
        self.assertEqual(self.monitor.scan_due(), [])

    def test_monitor_thread_is_stopped_by_service_close(self):
        self.service.deadline_monitor = self.monitor
        self.monitor.start()
        self.service.close()
        self.assertFalse(self.monitor.thread.is_alive())

    def test_last_task_auto_disable_leaves_executor_idle_instead_of_error(self):
        data = self.raw()
        for task in data.values():
            if isinstance(task, dict) and 'scheduler' in task:
                task['scheduler']['enable'] = False
        data['orochi']['scheduler']['enable'] = True
        self.path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        self.monitor.scan_due()
        config = Config('trial')
        task = config.get_next()
        self.assertFalse(config.pending_task)
        self.assertFalse(config.waiting_task)
        self.assertGreater(task.next_run, datetime.now())
        self.assertFalse(task.enable)
        self.assertEqual(config.get_schedule_data()['waiting'], [])


if __name__ == '__main__':
    unittest.main()
