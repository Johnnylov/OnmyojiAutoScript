"""Fresh restart must join the old worker, preserve truth, and remain retryable."""
import asyncio
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
import uuid

from module.observability import EventStore
from module.observability.common import redact
from module.server.solana_runtime import RuntimeService, ServiceError
from test_control_lifecycle import Adapter


class RestartControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.store = EventStore(self.temp.name)
        self.adapter = Adapter()
        self.service = RuntimeService(self.store, self.adapter)
        self.profile_id = self.service.profile_id('profile')

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def request(self, action, **extra):
        return {'profile_id': self.profile_id, 'action': action,
                'request_id': str(uuid.uuid4()),
                'expected_state_version': self.service.profile_state.get(self.profile_id, {}).get('state_version', 0),
                **extra}

    def active_run(self):
        self.service.register_process('profile', 'old-owner')
        self.service.emit({'type': 'run.started', 'run_id': 'old-run',
                           'profile_id': self.profile_id, 'task_id': 'Orochi'})
        self.store.checkpoints.save('runs', 'old-task-pointer', {
            'profile_id': self.profile_id, 'run_id': 'old-run', 'state': 'running', 'in_flight': True})

    def test_restart_joins_old_worker_and_starts_fresh_without_claiming_success(self):
        self.active_run()
        order = []

        async def stop():
            order.append('stop')
            self.service.process_exited('old-owner', -1)

        async def start():
            self.assertFalse(self.service.executor_alive(self.profile_id))
            self.assertEqual(self.service.runs['old-run']['state'], 'interrupted')
            pointer = self.store.checkpoints.get('runs', 'old-task-pointer')
            self.assertTrue(pointer['terminal'])
            self.assertFalse(pointer['in_flight'])
            order.append('start')
            self.service.register_process('profile', 'new-owner')

        self.adapter.manager.script_process['profile'] = SimpleNamespace(stop=stop, start=start)
        request = self.request('restart')
        receipt = self.service.begin_control(request)
        result = asyncio.run(self.adapter.control(self.service, receipt))
        self.service.complete_control(receipt, True, result)
        self.assertEqual(order, ['stop', 'start'])
        self.assertEqual(result, 'restarted')
        self.assertTrue(self.service.begin_control(request)['duplicate'])
        finished = self.store.query(filters={'type': 'run.finished'})['items']
        self.assertEqual(len(finished), 1)
        self.assertEqual(finished[0]['payload']['game_outcome'], 'unknown')
        self.assertFalse(finished[0]['payload']['automatic_replay'])

    def test_restart_refuses_to_start_if_old_worker_remains_alive(self):
        self.active_run()
        starts = []

        async def stop():
            pass

        async def start():
            starts.append('incorrect')

        self.adapter.manager.script_process['profile'] = SimpleNamespace(stop=stop, start=start)
        receipt = self.service.begin_control(self.request('restart'))
        with self.assertRaises(ServiceError) as error:
            asyncio.run(self.adapter.control(self.service, receipt))
        self.assertEqual(error.exception.code, 'executor_still_running')
        self.assertEqual(starts, [])
        self.assertEqual(self.service.runs['old-run']['state'], 'running')

    def test_newer_stop_cancels_pending_restart(self):
        self.active_run()
        receipt = self.service.begin_control(self.request('restart'))
        self.service.begin_control(self.request('safe_stop'))
        self.service.process_exited('old-owner', 0)
        with self.assertRaises(ServiceError) as error:
            self.service.prepare_restart(receipt)
        self.assertEqual(error.exception.code, 'control_superseded')

    def test_concurrent_restart_is_rejected_before_a_second_intent(self):
        receipt = self.service.begin_control(self.request('restart'))
        self.service.active_operations.add(receipt['request_id'])
        with self.assertRaises(ServiceError) as error:
            self.service.begin_control(self.request('restart'))
        self.assertEqual(error.exception.code, 'control_in_progress')

    def test_observation_does_not_change_control_version_but_process_death_does(self):
        self.service.register_process('profile', 'old-owner')
        self.service._set_state(self.profile_id, 'warning')
        before = self.service.profile_state[self.profile_id]['state_version']
        for _ in range(25):
            self.service._set_state(self.profile_id, 'warning')
        self.assertEqual(self.service.profile_state[self.profile_id]['state_version'], before)
        self.service.process_exited('old-owner', -1)
        self.assertGreater(self.service.profile_state[self.profile_id]['state_version'], before)

    def test_secret_task_weight_survives_save_and_service_restart(self):
        self.service.set_scheduler_policy({'weights': {'secret': 2.0, 'Orochi': 3.0}})
        self.assertEqual(self.store.checkpoints.get('settings', 'scheduler_policy')['weights']['secret'], 2.0)
        self.service.close()
        self.store = EventStore(self.temp.name)
        self.service = RuntimeService(self.store, self.adapter)
        self.assertEqual(self.service.coordinator.policy.weights['secret'], 2.0)
        self.assertEqual(redact({'secret': 'private', 'weights': {'secret': 'private', 'token': 123}}),
                         {'secret': '[changed]', 'weights': {'secret': '[changed]', 'token': '[changed]'}})


if __name__ == '__main__':
    unittest.main()
