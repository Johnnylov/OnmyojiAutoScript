"""Executor controls remain truthful after a crash; no game processes are used."""
import asyncio
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
import uuid

from module.observability import EventStore
from module.server.solana_adapter import ManagerAdapter
from module.server.solana_runtime import RuntimeService, ServiceError


class Adapter(ManagerAdapter):
    def __init__(self):
        self._manager = SimpleNamespace(script_process={})

    @property
    def manager(self):
        return self._manager

    def names(self):
        return ['profile']

    def device(self, name):
        return {'device_id': 'isolated-lifecycle-test'}

    def raw(self, name):
        return {}


class ControlLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.store = EventStore(self.temp.name)
        self.adapter = Adapter()
        self.service = RuntimeService(self.store, self.adapter)
        self.profile_id = self.service.profile_id('profile')

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def profile(self):
        return self.service.overview()['profiles'][0]

    def request(self, action):
        return self.service.begin_control({
            'profile_id': self.profile_id, 'action': action,
            'request_id': str(uuid.uuid4()),
            'expected_state_version': self.profile()['state_version'],
        })

    def crash(self, with_run=False):
        self.service.register_process('profile', 'old-owner')
        if with_run:
            self.service.emit({'type': 'run.started', 'run_id': 'uncertain-run',
                               'profile_id': self.profile_id, 'task_id': 'Orochi'})
        self.service.process_exited('old-owner', -1)

    def test_overview_and_events_separate_dead_executor_from_retained_run(self):
        self.assertFalse(self.profile()['executor_alive'])
        self.service.register_process('profile', 'old-owner')
        self.service._set_state(self.profile_id, 'warning')
        self.assertTrue(self.profile()['executor_alive'], 'warning alone is not evidence of exit')
        self.service.emit({'type': 'run.started', 'run_id': 'uncertain-run',
                           'profile_id': self.profile_id, 'task_id': 'Orochi'})
        self.service.process_exited('old-owner', -1)
        self.assertFalse(self.profile()['executor_alive'])
        self.assertEqual(self.profile()['state'], 'warning')
        self.assertEqual(self.service.runs['uncertain-run']['state'], 'needs_reconciliation')
        event = self.service.replay[-1]
        self.assertEqual(event['type'], 'profile.state')
        self.assertFalse(event['payload']['executor_alive'])

    def test_pause_and_resume_on_dead_executor_are_rejected_without_phantom_state(self):
        self.crash(with_run=True)
        previous = self.profile()
        for action in ('pause', 'resume'):
            with self.subTest(action=action), self.assertRaises(ServiceError) as error:
                self.request(action)
            self.assertEqual(error.exception.code, 'executor_not_running')
            self.assertEqual(self.profile()['state'], previous['state'])
            self.assertEqual(self.profile()['state_version'], previous['state_version'])
        self.assertEqual(self.service.runs['uncertain-run']['state'], 'needs_reconciliation')

    def test_stopping_dead_executor_is_idempotent_and_does_not_create_a_process(self):
        self.crash(with_run=True)
        previous = self.profile()
        for action in ('safe_stop', 'immediate_stop'):
            with self.subTest(action=action):
                receipt = self.request(action)
                status = asyncio.run(self.adapter.control(self.service, receipt))
                result = self.service.complete_control(receipt, True, status)
                self.assertEqual(result['status'], 'already_stopped')
                self.assertTrue(result['persisted'])
                self.assertEqual(self.profile()['state'], previous['state'])
                self.assertEqual(self.profile()['state_version'], previous['state_version'])
                self.assertEqual(self.adapter.manager.script_process, {})
        self.assertEqual(self.service.runs['uncertain-run']['state'], 'needs_reconciliation')

    def test_process_exit_between_intent_and_dispatch_never_restores_active_state(self):
        for index, action in enumerate(('pause', 'resume', 'safe_stop', 'immediate_stop')):
            with self.subTest(action=action):
                owner = f'owner-{index}'
                self.service.register_process('profile', owner)
                receipt = self.request(action)
                self.service.process_exited(owner, -1)
                if action in ('pause', 'resume'):
                    with self.assertRaises(ServiceError) as error:
                        asyncio.run(self.adapter.control(self.service, receipt))
                    self.assertEqual(error.exception.code, 'executor_not_running')
                    self.service.complete_control(receipt, False, 'failed', error.exception.code)
                else:
                    status = asyncio.run(self.adapter.control(self.service, receipt))
                    self.service.complete_control(receipt, True, status)
                    self.assertEqual(status, 'already_stopped')
                self.assertFalse(self.profile()['executor_alive'])
                self.assertEqual(self.profile()['state'], 'warning')

    def test_clean_error_can_restart_with_a_fresh_state_version(self):
        self.crash()
        starts = []

        async def start():
            starts.append(self.service.register_process('profile', 'new-owner'))

        self.adapter.manager.script_process['profile'] = SimpleNamespace(start=start)
        receipt = self.request('start')
        status = asyncio.run(self.adapter.control(self.service, receipt))
        result = self.service.complete_control(receipt, True, status)
        self.assertEqual(result['status'], 'started')
        self.assertEqual(len(starts), 1)
        self.assertTrue(self.profile()['executor_alive'])


if __name__ == '__main__':
    unittest.main()
