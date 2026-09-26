"""Repeatable WALL-CLOCK synthetic soak; never imports or starts a game/device.

Example smoke run (creates a unique child directory under --output-dir):
  toolkit/python.exe tests/solana/soak_runner.py --duration-seconds 60 \
      --interval-seconds 0.5 --output-dir D:/solana-validation

Long runs are deliberately opt-in. No scheduler/automation is installed. The
default storage policy is unchanged, including the 14-day retry/dedup window.
An explicit --retry-window-seconds is reported as a nondefault test policy.
--max-events ends workload early and returns a PARTIAL report, never a claimed
full-duration soak. It is a dispatch threshold: terminal/cleanup records are
still persisted after the threshold. IPC transport, game vision and UI are
outside scope. Runs too short to exercise all scenarios report PARTIAL.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import sys
import time
import traceback
from types import SimpleNamespace
import uuid

BACKEND = Path(__file__).resolve().parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from module.observability import EventStore, StoragePolicy
from module.scheduling.runtime import ExecutionRuntime, SafeBoundaryExit, DispatchStopped
from module.server.solana_runtime import RuntimeService


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def source_hashes():
    paths = [Path(__file__), *sorted((BACKEND / 'module/observability').glob('*.py'))]
    paths += [BACKEND / relative for relative in (
        'module/scheduling/core.py', 'module/scheduling/coordinator.py',
        'module/scheduling/runtime.py', 'module/scheduling/fence.py', 'module/scheduling/preflight.py',
        'module/server/solana_runtime.py', 'module/server/solana_recovery.py')]
    return {str(path.relative_to(BACKEND)).replace('\\', '/'): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths}


class ObservedStore(EventStore):
    """Observe public durable ACKs without replacing any storage behaviour."""
    def __init__(self, root, counters, policy):
        self.counters = counters
        super().__init__(root, policy=policy)

    def append(self, event):
        self.counters['submitted'][event['type']] += 1
        ack = super().append(event)
        if ack.get('duplicate'):
            self.counters['duplicate_acks'] += 1
        elif ack.get('persisted'):
            sequence = ack['seq']
            if sequence <= self.counters['last_unique_seq']:
                raise AssertionError('A new persisted event reused/decreased the sequence')
            self.counters['last_unique_seq'] = sequence
            self.counters['persisted'][event['type']] += 1
        return ack


class SyntheticAdapter:
    def __init__(self, names, device_id):
        self.profiles, self.device_id = names, device_id

    def names(self):
        return list(self.profiles)

    def device(self, name):
        return {'device_id': self.device_id, 'serial': 'auto'}


class DirectBridge:
    """Real parent dispatch; intentionally excludes transport/process testing."""
    def __init__(self, service, identity):
        self.service = service
        self.__dict__.update(identity)
        self.last_segment = None

    def call(self, operation, payload):
        result = self.service.dispatch(self.owner_id, operation, payload)
        if operation == 'event.append' and payload['type'] == 'segment.finished':
            self.last_segment = copy.deepcopy(payload)
        return result


class Soak:
    def __init__(self, args):
        self.args = args
        self.identifier = uuid.uuid4().hex
        self.path = Path(args.output_dir).resolve() / (
            'soak-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + self.identifier[:8])
        self.path.mkdir(parents=True, exist_ok=False)
        self.started_at, self.started = utc_now(), time.monotonic()
        self.source_start = source_hashes()
        self.names = ['soak-' + self.identifier[:12] + '-p' + str(n) for n in (1, 2, 3)]
        self.device_id = 'soak-device-' + self.identifier
        self.adapter = SyntheticAdapter(self.names, self.device_id)
        self.policy = StoragePolicy()
        if args.retry_window_seconds is not None:
            self.policy.retry_window_seconds = args.retry_window_seconds
        self.policy.validate()
        self.counters = {'submitted': Counter(), 'persisted': Counter(), 'duplicate_acks': 0, 'last_unique_seq': 0}
        self.expected = {'started': 0, 'succeeded': 0, 'cancelled': 0, 'device_seconds': 0.0}
        self.models = {name: {'weight': weight, 'run_id': None, 'count': 0, 'resume_after': None,
                             'actual_service': 0.0, 'ideal_service': 0.0, 'segments': 0}
                       for name, weight in zip(self.names, (1, 2, 3))}
        self.segments = self.restarts = self.pause_cycles = self.resume_cycles = 0
        self.peak_bytes = self.max_segment = 0
        self.check_counts = Counter()
        self.errors = []
        self.workers = {}
        self.store = self.service = None
        self.last_storage = {}
        self.safety_stop_probe = None
        self.next_heartbeat = self.started
        self.task_config = {'orochi_config': {'limit_count': 5, 'limit_time': '23:59:59', 'user_status': 'alone'}}

    def check(self, condition, name):
        if not condition:
            raise AssertionError(name)
        self.check_counts[name] += 1

    def open_service(self):
        self.store = ObservedStore(self.path / 'data', self.counters, self.policy)
        self.service = RuntimeService(self.store, self.adapter)
        old_ids = {name: model.get('profile_id') for name, model in self.models.items()}
        self.workers = {}
        for name, model in self.models.items():
            identity = self.service.register_process(name, 'soak-owner-' + uuid.uuid4().hex)
            if old_ids[name]:
                self.check(identity['profile_id'] == old_ids[name], 'profile_identity_survives_restart')
            model['profile_id'] = identity['profile_id']
            bridge = DirectBridge(self.service, identity)
            execution = ExecutionRuntime(bridge, name)
            config = SimpleNamespace(
                script=SimpleNamespace(device=SimpleNamespace(model_dump=lambda name=name, **_: self.adapter.device(name))),
                orochi=SimpleNamespace(orochi_config=SimpleNamespace(user_status='alone')),
                pending_task=[SimpleNamespace(command='Orochi', next_run=datetime(2000, 1, 1),
                    scheduling={'estimated_batch_seconds': self.args.interval_seconds,
                                'fair_weight': model['weight'], 'config_revision': 'synthetic-fixture-v1'})],
                waiting_task=[])
            self.workers[name] = (execution, config)
            if model['resume_after'] is not None:
                self.service.request_control(model['profile_id'], 'pause')
        if not self.restarts:
            self.service.set_scheduler_policy({'mode': 'eevdf', 'batch_seconds': self.args.interval_seconds,
                'max_wait_seconds': 900, 'urgent_budget_seconds': 0,
                'weights': {m['profile_id'] + ':Orochi': m['weight'] for m in self.models.values()}})
        self.check(self.service.coordinator.policy.mode == 'eevdf', 'policy_survives_restart')
        self.check(not self.service.dispatch_blocked, 'dispatch_is_healthy')
        self.verify_summary()

    def register_candidates(self):
        # All three synthetic executors are active contenders before selection.
        for name, (execution, _) in self.workers.items():
            model = self.models[name]
            self.service.coordinator.register_profile(self.device_id, model['profile_id'], execution.owner_id,
                [{'task': 'Orochi', 'quantum': self.args.interval_seconds, 'weight': model['weight'],
                  'cooperative': True, 'config_revision': 'synthetic-fixture-v1'}], ['Orochi'])

    def control(self, name, action):
        profile = self.models[name]['profile_id']
        receipt = self.service.begin_control({'profile_id': profile, 'action': action, 'request_id': str(uuid.uuid4())})
        self.service.request_control(profile, action)
        result = self.service.complete_control(receipt, True, 'requested')
        self.check(result.get('persisted') is True, 'control_result_is_durable')

    def select(self):
        self.register_candidates()
        for name, (execution, config) in self.workers.items():
            result = execution.acquire(config)
            if result['status'] == 'acquired':
                return name, execution
        raise AssertionError('Runnable synthetic tasks did not obtain a device lease')

    def step(self, deadline):
        for name, model in self.models.items():
            if model['resume_after'] is not None and self.segments >= model['resume_after']:
                self.control(name, 'resume')
                model['resume_after'] = None
                self.resume_cycles += 1
        eligible = [name for name, model in self.models.items() if model['resume_after'] is None]
        name, execution = self.select()
        self.check(name in eligible, 'paused_profiles_are_not_dispatched')
        model = self.models[name]
        leases = [e.lease for e, _ in self.workers.values() if e.lease]
        self.check(len(leases) == 1, 'one_device_one_holder')
        self.check(execution.validate().get('valid'), 'granted_lease_validates')
        key = model['profile_id'] + ':Orochi'
        before_service = self.service.coordinator.snapshot()[self.device_id]['entities'][key]['service']
        execution.begin('Orochi', self.task_config, cooperative=True)
        if model['run_id'] is None:
            model['run_id'], model['count'] = execution.active['run_id'], 0
            self.expected['started'] += 1
        self.check(execution.active['run_id'] == model['run_id'], 'logical_run_survives_yield_and_restart')
        self.check(execution.count == model['count'], 'checkpoint_progress_matches_external_model')
        execution.before_battle(model['count'])
        sleep_until = min(time.monotonic() + self.args.interval_seconds, deadline)
        while time.monotonic() < sleep_until:
            # A long synthetic batch still publishes its heartbeat at 30 s.
            time.sleep(max(0, min(sleep_until, self.next_heartbeat) - time.monotonic()))
            self.heartbeat()
        # The synthetic fixture now reports a settled lobby. No game class or
        # screenshot matcher is involved; this is not a vision acceptance test.
        model['count'] += 1
        execution.record_progress(model['count'])
        snapshot = execution.progress_snapshot()
        self.check(snapshot['target_count'] == 5 and snapshot['current_count'] == model['count'], 'frozen_target_and_live_progress')
        self.check(snapshot['battle_count'] is None, 'no_fabricated_battle_metric')
        pause = self.segments > 0 and self.segments % 13 == 0 and model['count'] < 5
        if model['count'] == 5:
            outcome = 'succeeded'
            self.expected['succeeded'] += 1
        else:
            outcome = 'paused' if pause else 'yielded'
            if pause:
                self.control(name, 'pause')
                model['resume_after'] = self.segments + 3
                self.pause_cycles += 1
            try:
                execution.safe_boundary(model['count'], verified=True, outcome=outcome)
            except SafeBoundaryExit as boundary:
                self.check(boundary.outcome == outcome, 'synthetic_safe_boundary_contract')
            else:
                raise AssertionError('Safe boundary did not yield control')
        execution.finish(outcome)
        self.check(execution.lease is None, 'finished_segment_releases_device')
        segment = execution.bridge.last_segment
        duration = segment['payload']['duration_seconds']
        self.check(math.isfinite(duration) and duration >= 0, 'segment_duration_is_finite')
        self.expected['device_seconds'] += duration
        after_service = self.service.coordinator.snapshot()[self.device_id]['entities'][key]['service']
        occupied = after_service - before_service
        self.check(occupied + .001 >= duration, 'accounted_lease_covers_segment')
        self.max_segment = max(self.max_segment, occupied)
        model['actual_service'] += occupied
        total_weight = sum(self.models[n]['weight'] for n in eligible)
        for candidate in eligible:
            self.models[candidate]['ideal_service'] += occupied * self.models[candidate]['weight'] / total_weight
        model['segments'] += 1
        self.segments += 1
        if outcome == 'succeeded':
            model['run_id'], model['count'] = None, 0
        if self.segments % 11 == 0:
            before = self.store.statistics()['totals']
            ack = execution.bridge.call('event.append', segment)
            self.check(ack.get('duplicate') is True, 'event_replay_gets_duplicate_ack')
            self.check(self.store.statistics()['totals'] == before, 'event_replay_does_not_double_count')
        self.verify_summary()
        if self.segments % 40 == 0 and time.monotonic() < deadline:
            self.restart_service()

    def verify_summary(self):
        totals = self.store.statistics()['totals']
        for key in ('started', 'succeeded', 'cancelled'):
            self.check(totals[key] == self.expected[key], 'summary_' + key + '_matches_logical_model')
        self.check(abs(totals['device_seconds'] - self.expected['device_seconds']) < .01,
                   'summary_device_seconds_matches_unique_segments')
        self.check(totals['failed'] == totals['interrupted'] == totals['crashed'] == 0,
                   'no_unexpected_terminal_outcome')

    def sample_storage(self):
        self.last_storage = self.store.storage_status()
        self.peak_bytes = max(self.peak_bytes, self.last_storage['peak_bytes'], self.last_storage['total_bytes'])
        hard = self.policy.normal_budget_bytes + self.policy.temporary_reserve_bytes
        self.check(self.peak_bytes <= hard, 'storage_peak_stays_within_protection_budget')
        self.check(self.last_storage['state'] == 'healthy' and self.last_storage['dispatch_allowed'], 'storage_is_healthy')

    def restart_service(self):
        self.sample_storage()
        for execution, _ in self.workers.values():
            self.service.process_exited(execution.owner_id, 0)
        self.service.close()
        self.restarts += 1
        self.open_service()

    def heartbeat(self, force=False):
        now = time.monotonic()
        if not force and now < self.next_heartbeat:
            return
        self.sample_storage()
        atomic_json(self.path / 'heartbeat.json', {
            'run_id': self.identifier, 'status': 'running', 'started_at': self.started_at,
            'observed_at': utc_now(), 'actual_elapsed_seconds': now - self.started,
            'requested_duration_seconds': self.args.duration_seconds, 'segments': self.segments,
            'service_restarts': self.restarts, 'persisted_events': sum(self.counters['persisted'].values()),
            'data_bytes_current': self.last_storage['total_bytes'], 'data_bytes_peak': self.peak_bytes,
            'errors': len(self.errors)})
        self.next_heartbeat = now + 30

    def cancel_remaining(self):
        for name, (execution, _) in self.workers.items():
            if self.models[name]['run_id'] is not None:
                try:
                    execution.stop_at_boundary()
                except DispatchStopped:
                    pass
                self.check(not execution.storage_failed, 'final_cancel_is_durable')
                self.expected['cancelled'] += 1
                self.models[name]['run_id'] = None
        self.verify_summary()

    def probe_stop_after_failure(self):
        probe = {'attempted': True, 'controls_observed': [], 'returned': 0, 'errors': []}
        for name, (execution, _) in self.workers.items():
            try:
                self.service.request_control(self.models[name]['profile_id'], 'safe_stop')
                probe['controls_observed'].append(execution.control_status().get('control'))
                try:
                    execution.stop_at_boundary()
                except DispatchStopped:
                    probe['returned'] += 1
            except Exception as exc:
                probe['errors'].append(type(exc).__name__ + ': ' + str(exc))
        self.safety_stop_probe = probe

    def run(self):
        stop_reason = 'duration_completed'
        try:
            self.open_service()
            self.heartbeat(force=True)
            deadline = self.started + self.args.duration_seconds
            while time.monotonic() < deadline:
                if self.args.max_events and sum(self.counters['persisted'].values()) >= self.args.max_events:
                    stop_reason = 'event_limit_reached'
                    break
                self.step(deadline)
                self.heartbeat()
            self.cancel_remaining()
            self.sample_storage()
            if self.segments >= 20:
                self.check(all(model['segments'] for model in self.models.values()), 'all_profiles_received_service')
            busy = sum(model['actual_service'] for model in self.models.values())
            if busy >= 10000:
                tolerance = max(busy * .05, 5 * self.max_segment)
                self.check(all(abs(m['actual_service'] - m['ideal_service']) <= tolerance for m in self.models.values()),
                           'long_sample_fairness_within_five_percentage_points_or_batch_bound')
        except BaseException as exc:
            stop_reason = 'error'
            self.errors.append({'type': type(exc).__name__, 'message': str(exc), 'traceback': traceback.format_exc()})
            if self.service is not None:
                self.probe_stop_after_failure()
        finally:
            # No automatic recovery/retry of a failed workload. Only release
            # this synthetic fixture's own process locks and close its files.
            for execution, _ in self.workers.values():
                if execution.process_lock is not None:
                    execution.process_lock.release()
                    execution.process_lock = None
            if self.service is not None:
                try:
                    for execution, _ in self.workers.values():
                        self.service.process_exited(execution.owner_id, -1 if self.errors else 0)
                    self.last_storage = self.store.storage_status()
                    self.peak_bytes = max(self.peak_bytes, self.last_storage['peak_bytes'])
                    self.service.close()
                except Exception as exc:
                    self.errors.append({'type': type(exc).__name__, 'message': 'cleanup: ' + str(exc)})
        elapsed = time.monotonic() - self.started
        source_end = source_hashes()
        changed = [name for name in self.source_start if source_end.get(name) != self.source_start[name]]
        busy = sum(m['actual_service'] for m in self.models.values())
        coverage = {
            'same_device_multi_profile_contention': all(m['segments'] for m in self.models.values()),
            'logical_run_multiple_segments': self.check_counts['synthetic_safe_boundary_contract'] > 0,
            'pause_and_resume': self.pause_cycles > 0 and self.resume_cycles > 0,
            'duplicate_event_replay': self.counters['duplicate_acks'] > 0,
            'service_restart_with_stable_identity': self.restarts > 0 and self.check_counts['profile_identity_survives_restart'] > 0,
        }
        duration_completed = stop_reason == 'duration_completed' and elapsed >= self.args.duration_seconds
        if not self.errors and stop_reason == 'duration_completed' and not all(coverage.values()):
            stop_reason = 'scenario_coverage_incomplete'
        status = 'failed' if self.errors else 'partial' if stop_reason != 'duration_completed' else 'passed'
        report = {
            'schema_version': 1, 'run_id': self.identifier, 'status': status, 'stop_reason': stop_reason,
            'started_at': self.started_at, 'finished_at': utc_now(), 'actual_elapsed_seconds': elapsed,
            'requested_duration_seconds': self.args.duration_seconds,
            'duration_completed': duration_completed,
            'interval_seconds': self.args.interval_seconds, 'max_events': self.args.max_events,
            'max_events_is_dispatch_threshold': True,
            'scope': 'synthetic single-thread multi-profile contention; real file storage and parent/worker APIs; no IPC transport, game, vision, emulator or UI',
            'time_basis': 'actual wall-clock holding time; no virtual time acceleration',
            'python': sys.version, 'platform': platform.platform(),
            'source_hashes_start': self.source_start, 'source_hashes_end': source_end,
            'source_changed_during_run': changed, 'qualifies_as_frozen_source_run': not changed,
            'storage_policy': asdict(self.policy),
            'nondefault_storage_policy': ({'retry_window_seconds': self.args.retry_window_seconds,
                'warning': 'This override does NOT validate the default 14-day retry policy'}
                if self.args.retry_window_seconds is not None else {}),
            'event_counts': dict(self.counters['persisted']), 'event_submissions': dict(self.counters['submitted']),
            'duplicate_acks': self.counters['duplicate_acks'], 'last_unique_seq': self.counters['last_unique_seq'],
            'segments': self.segments, 'service_restarts': self.restarts,
            'pause_cycles': self.pause_cycles, 'resume_cycles': self.resume_cycles,
            'scenario_coverage': coverage, 'scenario_coverage_complete': all(coverage.values()),
            'logical_model': self.expected, 'invariant_checks': dict(self.check_counts),
            'fairness': {'busy_seconds': busy, 'gate_applied': busy >= 10000,
                'note': 'Short smoke samples are descriptive, not a 1:2:3 fairness acceptance gate',
                'max_segment_seconds': self.max_segment,
                'profiles': {name: {key: m[key] for key in ('weight', 'segments', 'actual_service', 'ideal_service')}
                             for name, m in self.models.items()}},
            'data_bytes_peak': self.peak_bytes, 'storage_final': self.last_storage,
            'safety_stop_probe': self.safety_stop_probe, 'errors': self.errors,
            'limitations': ['No real game or emulator was run', 'No 24-hour result is implied by a shorter run',
                            'Controlled restart at a safe boundary is not crash/fault injection',
                            'Transport and real process races are covered by separate tests, not this runner']}
        atomic_json(self.path / 'report.json', report)
        atomic_json(self.path / 'heartbeat.json', {'run_id': self.identifier, 'status': status,
            'started_at': self.started_at, 'observed_at': utc_now(), 'actual_elapsed_seconds': elapsed,
            'segments': self.segments, 'errors': len(self.errors), 'report': str(self.path / 'report.json')})
        print(json.dumps({'status': status, 'actual_elapsed_seconds': elapsed,
                          'report': str(self.path / 'report.json'), 'errors': len(self.errors)}, ensure_ascii=False), flush=True)
        return 1 if self.errors else 2 if status == 'partial' else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--duration-seconds', type=float, required=True)
    parser.add_argument('--interval-seconds', type=float, default=.5)
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--retry-window-seconds', type=int, default=None)
    parser.add_argument('--max-events', type=int, default=None)
    args = parser.parse_args()
    for name in ('duration_seconds', 'interval_seconds'):
        value = getattr(args, name)
        if not math.isfinite(value) or value <= 0:
            parser.error(name.replace('_', '-') + ' must be finite and positive')
    if args.interval_seconds > 60:
        parser.error('interval-seconds must be <= 60 to keep heartbeats observable')
    if args.retry_window_seconds is not None and args.retry_window_seconds < 1:
        parser.error('retry-window-seconds must be positive')
    if args.max_events is not None and args.max_events < 1:
        parser.error('max-events must be positive')
    return Soak(args).run()


if __name__ == '__main__':
    raise SystemExit(main())
