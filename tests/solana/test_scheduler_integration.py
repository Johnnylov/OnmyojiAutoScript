"""Lifecycle -> parent bridge service -> durable real store, without a game."""
from datetime import datetime
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from module.observability import EventStore
from module.scheduling.runtime import ExecutionRuntime, SafeBoundaryExit
from module.server.solana_runtime import RuntimeService


class SchedulerStorageIntegration(unittest.TestCase):
    def test_two_segments_produce_one_durable_run(self):
        with TemporaryDirectory() as directory:
            store = EventStore(directory)
            service = RuntimeService(store)
            identity = service.register_process('integration', 'fake-worker')
            bridge = SimpleNamespace(**identity)
            bridge.call = lambda operation, payload: service.dispatch('fake-worker', operation, payload)
            clock = [0.0]
            execution = ExecutionRuntime(bridge, 'integration', monotonic=lambda: clock[0])
            config = SimpleNamespace(
                script=SimpleNamespace(device=SimpleNamespace(model_dump=lambda **kw: {'serial': 'auto'})),
                orochi=SimpleNamespace(orochi_config=SimpleNamespace(user_status='alone')),
                pending_task=[SimpleNamespace(command='Orochi', next_run=datetime(2020, 1, 1))],
                waiting_task=[])
            service.coordinator.set_policy({'mode': 'eevdf'})
            try:
                self.assertEqual(execution.acquire(config)['status'], 'acquired')
                execution.begin('Orochi', {'target': 5}, True)
                identity = execution.active['run_id']
                clock[0] = 120
                with self.assertRaises(SafeBoundaryExit):
                    execution.safe_boundary(3, True, 'yielded')
                execution.finish('yielded')
                self.assertEqual(execution.acquire(config)['status'], 'acquired')
                execution.begin('Orochi', {'target': 5}, True)
                self.assertEqual(execution.active['run_id'], identity)
                self.assertEqual(execution.count, 3)
                clock[0] = 200
                execution.finish('succeeded')
                summary = store.statistics()['totals']
                self.assertEqual(summary['started'], 1)
                self.assertEqual(summary['succeeded'], 1)
                self.assertEqual(summary['device_seconds'], 200)
                self.assertEqual(summary['success_denominator'], 1)
                self.assertEqual(service.runs[identity]['state'], 'succeeded')
                checkpoint = store.checkpoints.get('runs', execution._key('Orochi'))
                self.assertTrue(checkpoint['terminal'])
                self.assertGreater(checkpoint['event_seq'], 0)
            finally:
                if execution.lease:
                    execution.release('interrupted')
                service.close()


if __name__ == '__main__':
    unittest.main()
