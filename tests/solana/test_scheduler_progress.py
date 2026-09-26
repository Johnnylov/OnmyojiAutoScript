"""Live preview metadata is bounded, truthful and independent of durable RPC."""
import copy
from datetime import datetime
import unittest
import uuid

from module.scheduling.coordinator import Coordinator, LeaseLost
from module.scheduling.runtime import ExecutionRuntime, SafeBoundaryExit


class Checkpoints:
    def __init__(self):
        self.data = {}

    def get(self, kind, key):
        return copy.deepcopy(self.data.get((kind, key)))

    def save(self, kind, key, data, event_seq=None):
        self.data[kind, key] = copy.deepcopy(data)


class DirectBridge:
    profile_id, owner_id, device_id = 'progress-profile', 'worker', 'device'

    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.calls = []
        self.events = []
        self.sequence = 0

    def call(self, operation, payload):
        self.calls.append(operation)
        if operation == 'event.append':
            self.sequence += 1
            self.events.append(copy.deepcopy(payload))
            return {'seq': self.sequence}
        return self.coordinator.dispatch(operation, payload)


class ProgressSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.coordinator = Coordinator(Checkpoints(), clock=lambda: 10000, monotonic=lambda: self.now)
        self.bridge = DirectBridge(self.coordinator)
        self.execution = ExecutionRuntime(self.bridge, 'profile', monotonic=lambda: self.now)
        self.config = {'orochi_config': {'limit_count': 50, 'limit_time': '00:35:00'}}

    def begin(self, cooperative=True, command='Orochi'):
        result = self.coordinator.acquire({'profile_id': self.bridge.profile_id,
            'device_id': self.bridge.device_id, 'owner_id': self.bridge.owner_id,
            'candidates': [{'task': command, 'cooperative': cooperative}], 'legacy_order': [command]})
        self.execution.lease = result['lease']
        self.execution.lease_started = self.now
        self.execution.begin(command, self.config, cooperative)

    def test_no_run_is_unknown_not_zero_or_fabricated_count(self):
        snapshot = self.execution.progress_snapshot()
        self.assertIsNone(snapshot['run_id'])
        self.assertIsNone(snapshot['execution_seconds'])
        self.assertIsNone(snapshot['current_count'])
        self.assertFalse(snapshot['count_supported'])
        self.assertIsNone(snapshot['battle_count'])
        self.assertEqual(self.bridge.calls, [])

    def test_snapshot_uses_monotonic_and_frozen_targets_without_rpc(self):
        self.begin()
        self.now += 75
        self.execution.record_progress(18)
        calls = list(self.bridge.calls)
        self.config['orochi_config'].update(limit_count=999, limit_time='23:00:00')
        snapshot = self.execution.progress_snapshot()
        self.assertEqual(snapshot['execution_seconds'], 75)
        self.assertEqual(snapshot['target_seconds'], 2100)
        self.assertEqual(snapshot['current_count'], 18)
        self.assertEqual(snapshot['target_count'], 50)
        self.assertEqual(snapshot['remaining_target'], 32)
        self.assertTrue(snapshot['count_supported'])
        self.assertEqual(snapshot['battle_count'], 0)
        self.assertEqual(snapshot['run_id'], self.execution.active['run_id'])
        self.assertEqual(snapshot['segment_id'], self.execution.active['segment_id'])
        self.assertEqual(snapshot['task_id'], 'Orochi')
        self.assertIsNotNone(datetime.fromisoformat(snapshot['observed_at']).tzinfo)
        self.assertEqual(self.bridge.calls, calls)
        snapshot['current_count'] = 999
        self.assertEqual(self.execution.progress_snapshot()['current_count'], 18)

    def test_pause_freezes_time_resume_keeps_logical_run(self):
        self.begin()
        run_id = self.execution.active['run_id']
        first_segment = self.execution.active['segment_id']
        self.now += 120
        with self.assertRaises(SafeBoundaryExit):
            self.execution.safe_boundary(3, True, 'paused')
        self.execution.finish('paused')
        self.now += 3600
        paused = self.execution.progress_snapshot()
        self.assertEqual(paused['state'], 'paused')
        self.assertEqual(paused['execution_seconds'], 120)
        self.assertEqual(paused['current_count'], 3)
        self.coordinator.request_control(self.bridge.profile_id, 'resume')
        self.begin()
        self.now += 30
        resumed = self.execution.progress_snapshot()
        self.assertEqual(resumed['state'], 'running')
        self.assertEqual(resumed['run_id'], run_id)
        self.assertNotEqual(resumed['segment_id'], first_segment)
        self.assertEqual(resumed['execution_seconds'], 150)
        self.assertEqual(resumed['current_count'], 3)

    def test_uninstrumented_task_uses_one_item_not_unverified_battle_attempts(self):
        for command in ('Orochi', 'DailyTrifles'):
            with self.subTest(command=command):
                self.begin(cooperative=False, command=command)
                self.execution.active['count'] = 18
                self.execution.record_progress(20)
                snapshot = self.execution.progress_snapshot()
                self.assertTrue(snapshot['count_supported'])
                self.assertEqual(snapshot['current_count'], 0)
                self.assertEqual(snapshot['target_count'], 1)
                self.assertEqual(snapshot['count_unit'], '项')
                self.assertEqual(snapshot['remaining_target'], 1)
                self.assertEqual(snapshot['battle_count'], 0 if command == 'Orochi' else None)
                self.assertEqual(snapshot['battle_supported'], command == 'Orochi')
                if command == 'DailyTrifles':
                    self.assertEqual(snapshot['battle_unavailable_reason'], 'not_applicable')
                self.execution.finish('succeeded')
                self.assertEqual(self.execution.progress_snapshot()['current_count'], 1)

    def test_generic_failure_does_not_complete_the_item(self):
        self.begin(cooperative=False, command='DailyTrifles')
        self.execution.finish('failed', 'screenshot_failed')
        snapshot = self.execution.progress_snapshot()
        self.assertEqual((snapshot['current_count'], snapshot['target_count']), (0, 1))
        terminal = next(e for e in self.bridge.events if e['type'] == 'run.finished')
        self.assertEqual(terminal['payload']['current_count'], 0)
        self.assertFalse(terminal['payload']['battle_supported'])

    def test_task_specific_progress_does_not_grant_cooperative_pause(self):
        self.begin(cooperative=False, command='AreaBoss')
        self.assertTrue(self.execution.report_progress(2, 6, unit='个目标', phase='狭间暗域'))
        calls = list(self.bridge.calls)
        self.assertFalse(self.execution.report_progress(2, 6, unit='个目标', phase='狭间暗域'))
        self.assertFalse(self.execution.report_progress(0, 6, unit='个目标', phase='狭间暗域'))
        self.assertEqual(self.bridge.calls, calls)
        self.assertFalse(self.execution.active['cooperative'])
        self.assertEqual(self.execution.count, 0)
        snapshot = self.execution.progress_snapshot()
        self.assertEqual((snapshot['current_count'], snapshot['target_count']), (2, 6))
        self.assertEqual(snapshot['count_unit'], '个目标')
        self.assertEqual(snapshot['progress_phase'], '狭间暗域')
        self.assertEqual(self.bridge.calls, calls)
        self.execution.finish('failed')
        terminal = next(e for e in self.bridge.events if e['type'] == 'run.finished')
        self.assertEqual(terminal['payload']['current_count'], 2)
        self.assertEqual(terminal['payload']['target_count'], 6)

    def test_battles_require_observed_settlement_and_deduplicate_old_tokens(self):
        self.begin(cooperative=False, command='AreaBoss')
        abandoned = self.execution.begin_battle()
        self.assertEqual(self.execution.progress_snapshot()['battle_count'], 0)
        for unknown in (None, 'started', 'unknown', 'crashed', 'skipped', 'timeout'):
            self.assertFalse(self.execution.finish_battle(abandoned, unknown))
        won = self.execution.begin_battle()
        self.assertNotEqual(won, abandoned)
        self.assertFalse(self.execution.finish_battle(abandoned, 'settled'))
        self.assertTrue(self.execution.finish_battle(won, 'won'))
        self.assertFalse(self.execution.finish_battle(won, 'won'))
        lost = self.execution.begin_battle()
        self.assertFalse(self.execution.finish_battle(won, 'lost'))
        self.assertTrue(self.execution.finish_battle(lost, 'lost'))
        snapshot = self.execution.progress_snapshot()
        self.assertTrue(snapshot['battle_supported'])
        self.assertEqual(snapshot['battle_count'], 2)
        self.assertEqual(snapshot['current_count'], 0)
        battles = [event for event in self.bridge.events if event['type'] == 'battle.finished']
        self.assertEqual([event['payload']['result'] for event in battles], ['won', 'lost'])
        self.assertEqual([event['payload']['battle_count'] for event in battles], [1, 2])
        self.assertEqual(battles[0]['event_id'], str(uuid.uuid5(uuid.NAMESPACE_URL,
            'oas:battle:finished:' + won)))
        self.assertEqual(battles[0]['payload']['battle_id'], won)

    def test_battle_totals_and_reported_units_survive_safe_continuation(self):
        self.begin()
        run_id = self.execution.active['run_id']
        token = self.execution.begin_battle()
        self.execution.finish_battle(token, 'settled')
        self.execution.report_progress(3, 50, unit='次挑战', phase='单人御魂')
        self.now += 120
        with self.assertRaises(SafeBoundaryExit):
            self.execution.safe_boundary(3, True, 'yielded')
        self.execution.finish('yielded')
        # Exercise loading a durable checkpoint in a new worker, not just the
        # same in-memory dict. Resuming never fabricates missing settlements.
        self.execution = ExecutionRuntime(self.bridge, 'profile', monotonic=lambda: self.now)
        self.begin()
        self.assertEqual(self.execution.active['run_id'], run_id)
        self.assertFalse(self.execution.finish_battle(token, 'settled'))
        self.execution.record_progress(3)
        next_token = self.execution.begin_battle()
        self.execution.finish_battle(next_token, 'won')
        self.now += 30
        snapshot = self.execution.progress_snapshot()
        self.assertEqual(snapshot['execution_seconds'], 150)
        self.assertEqual(snapshot['battle_count'], 2)
        self.assertEqual(snapshot['current_count'], 3)
        self.assertEqual(snapshot['count_unit'], '次挑战')
        self.assertEqual(snapshot['progress_phase'], '单人御魂')
        resumed = next(e for e in self.bridge.events if e['type'] == 'run.resumed')
        self.assertEqual(resumed['payload']['current_count'], 3)
        self.assertEqual(resumed['payload']['battle_count'], 1)

    def test_lifecycle_without_preview_contains_complete_metric_payload(self):
        self.begin(cooperative=False, command='Restart')
        self.execution.finish('succeeded')
        events = [event for event in self.bridge.events
                  if event['type'] in ('run.created', 'run.started', 'run.finished')]
        self.assertEqual(len(events), 3)
        for event in events:
            payload = event['payload']
            self.assertEqual(payload['target_count'], 1)
            self.assertEqual(payload['count_unit'], '项')
            self.assertIn('observed_at', payload)
            self.assertFalse(payload['battle_supported'])
            self.assertIsNone(payload['battle_count'])
        self.assertEqual([e['payload']['current_count'] for e in events], [0, 0, 1])

    def test_unknown_target_is_not_inferred_to_be_unbounded(self):
        self.begin(cooperative=False, command='Activity')
        self.execution.report_progress(2, unit='场')
        self.assertIsNone(self.execution.progress_snapshot()['target_count'])
        self.assertFalse(self.execution.progress_snapshot()['target_unbounded'])
        self.execution.report_progress(3, unit='场', target_unbounded=True)
        self.assertTrue(self.execution.progress_snapshot()['target_unbounded'])
        self.assertIsNone(self.execution.progress_snapshot()['target_count'])
        with self.assertRaises(ValueError):
            self.execution.report_progress(3, 10, target_unbounded=True)

    def test_older_safe_checkpoint_keeps_count_without_inventing_battle_history(self):
        self.begin()
        with self.assertRaises(SafeBoundaryExit):
            self.execution.safe_boundary(3, True, 'paused')
        self.execution.finish('paused')
        key = ('runs', self.execution._key('Orochi'))
        self.coordinator.checkpoint_store.data[key].pop('metrics')
        self.coordinator.request_control(self.bridge.profile_id, 'resume')
        self.execution = ExecutionRuntime(self.bridge, 'profile', monotonic=lambda: self.now)
        self.begin()
        snapshot = self.execution.progress_snapshot()
        self.assertEqual((snapshot['current_count'], snapshot['target_count']), (3, 50))
        self.assertIsNone(snapshot['battle_count'])
        self.assertFalse(snapshot['battle_supported'])

    def test_metric_revision_is_read_only_and_survives_worker_resume(self):
        self.begin()
        initial = self.execution.progress_snapshot()['metric_revision']
        calls = list(self.bridge.calls)
        self.now += 15
        self.assertEqual(self.execution.progress_snapshot()['metric_revision'], initial)
        self.assertEqual(self.execution.progress_snapshot()['metric_revision'], initial)
        self.assertEqual(self.bridge.calls, calls)
        self.execution.report_progress(2, 50, unit='次挑战')
        reported = self.execution.progress_snapshot()['metric_revision']
        self.assertGreater(reported, initial)
        with self.assertRaises(SafeBoundaryExit):
            self.execution.safe_boundary(2, True, 'paused')
        self.execution.finish('paused')
        paused = self.execution.progress_snapshot()['metric_revision']
        self.assertGreater(paused, reported)
        key = ('runs', self.execution._key('Orochi'))
        self.assertEqual(self.coordinator.checkpoint_store.data[key]['metrics']['metric_revision'], paused)
        paused_event = next(e for e in self.bridge.events if e['type'] == 'run.paused')
        self.assertEqual(paused_event['payload']['metric_revision'], paused)
        self.coordinator.request_control(self.bridge.profile_id, 'resume')
        self.execution = ExecutionRuntime(self.bridge, 'profile', monotonic=lambda: self.now)
        self.begin()
        self.assertGreater(self.execution.progress_snapshot()['metric_revision'], paused)
        resumed = next(e for e in self.bridge.events if e['type'] == 'run.resumed')
        self.assertGreater(resumed['payload']['metric_revision'], paused)
        self.assertEqual(resumed['payload']['current_count'], 2)

    def test_release_stops_clock_without_waiting_for_a_new_run(self):
        self.begin()
        self.now += 40
        self.execution.release('interrupted')
        self.now += 600
        snapshot = self.execution.progress_snapshot()
        self.assertEqual(snapshot['execution_seconds'], 40)
        self.assertEqual(snapshot['state'], 'waiting_resource')

    def test_terminal_snapshot_does_not_keep_counting(self):
        self.begin()
        self.now += 80
        self.execution.record_progress(50)
        self.execution.finish('succeeded')
        self.now += 300
        snapshot = self.execution.progress_snapshot()
        self.assertEqual(snapshot['state'], 'succeeded')
        self.assertEqual(snapshot['execution_seconds'], 80)
        self.assertEqual(snapshot['current_count'], 50)
        self.assertEqual(snapshot['remaining_target'], 0)

    def test_lost_authority_freezes_progress_before_worker_exit(self):
        self.begin()
        self.now += 25
        self.coordinator.revoke_owner(self.bridge.owner_id, confirmed_dead=True)
        with self.assertRaises(LeaseLost):
            self.execution.validate()
        self.now += 900
        snapshot = self.execution.progress_snapshot()
        self.assertEqual(snapshot['state'], 'needs_reconciliation')
        self.assertEqual(snapshot['execution_seconds'], 25)


if __name__ == '__main__':
    unittest.main()
