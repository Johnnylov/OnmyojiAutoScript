"""A manual stop must join, close unfinished work and remain restartable."""
import asyncio
from types import SimpleNamespace
import unittest
import test_restart_control as restart_fixtures
from module.server.solana_runtime import ServiceError


class ImmediateStopTests(unittest.TestCase):
    setUp = restart_fixtures.RestartControlTests.setUp
    tearDown = restart_fixtures.RestartControlTests.tearDown
    request = restart_fixtures.RestartControlTests.request
    active_run = restart_fixtures.RestartControlTests.active_run

    def test_immediate_stop_closes_active_work_without_success_or_replay(self):
        self.active_run()

        async def stop():
            self.assertTrue(self.service.owners['old-owner']['stop_requested'])
            self.assertEqual(self.service.runs['old-run']['state'], 'running')
            self.service.process_exited('old-owner', -1)

        self.adapter.manager.script_process['profile'] = SimpleNamespace(stop=stop)
        request = self.request('immediate_stop')
        receipt = self.service.begin_control(request)
        result = asyncio.run(self.adapter.control(self.service, receipt))
        self.service.complete_control(receipt, True, result)
        self.assertEqual(result, 'stopped')
        self.assertFalse(self.service.executor_alive(self.profile_id))
        self.assertEqual(self.service.profile_state[self.profile_id]['state'], 'inactive')
        self.assertEqual(self.service.runs['old-run']['state'], 'interrupted')
        pointer = self.store.checkpoints.get('runs', 'old-task-pointer')
        self.assertTrue(pointer['terminal'])
        self.assertFalse(pointer['in_flight'])
        self.assertEqual(self.service.recovery_status(self.profile_id)['items'], [])
        finished = self.store.query(filters={'type': 'run.finished'})['items']
        self.assertEqual(len(finished), 1)
        self.assertEqual(finished[0]['payload']['game_outcome'], 'unknown')
        self.assertFalse(finished[0]['payload']['automatic_replay'])
        self.assertTrue(self.service.begin_control(request)['duplicate'])
        self.assertTrue(self.service.begin_control(self.request('start'))['accepted'])

    def test_unjoined_process_is_not_reported_stopped(self):
        self.active_run()

        async def stop():
            pass

        self.adapter.manager.script_process['profile'] = SimpleNamespace(stop=stop)
        receipt = self.service.begin_control(self.request('immediate_stop'))
        with self.assertRaises(ServiceError) as error:
            asyncio.run(self.adapter.control(self.service, receipt))
        self.assertEqual(error.exception.code, 'executor_still_running')
        self.assertEqual(self.service.runs['old-run']['state'], 'running')

    def test_stop_never_closes_another_profiles_run(self):
        self.active_run()
        other = self.service.profile_id('another-profile')
        self.service.runs['other-run'] = dict(run_id='other-run', profile_id=other, state='running')
        receipt = self.service.begin_control(self.request('immediate_stop'))
        self.service.process_exited('old-owner', -1)
        self.service.finish_immediate_stop(receipt)
        self.assertEqual(self.service.runs['other-run']['state'], 'running')
