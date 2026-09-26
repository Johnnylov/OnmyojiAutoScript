"""Worker-side lifecycle, durable checkpoints and cooperative boundary protocol."""
from datetime import datetime, timezone
import hashlib
import json
import time
import uuid

from .coordinator import LeaseLost, normalize_device_id
from .fence import DeviceProcessLock
from .task_metrics import BATTLE_TASKS, battle_unavailable_reason
from .deadline import task_deadline, is_expired


TERMINAL = {'succeeded', 'failed', 'cancelled', 'interrupted', 'crashed'}


class SafeBoundaryExit(Exception):
    def __init__(self, outcome):
        super().__init__(outcome)
        self.outcome = outcome


class ReconciliationRequired(RuntimeError):
    pass


class DeadlineExpired(RuntimeError):
    pass


class DispatchStopped(SystemExit):
    pass


def config_revision(config):
    data = config.model_dump(mode='json') if hasattr(config, 'model_dump') else config
    data = dict(data)
    data.pop('scheduler', None)
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def cooperative_task(command, config):
    if command != 'Orochi':
        return False
    return str(config.orochi.orochi_config.user_status) in ('alone', 'UserStatus.ALONE')


def device_revision(device):
    # Screenshot benchmarking changes capture method automatically; that is not
    # a target change and must not invalidate a safe continuation.
    return {'identity': normalize_device_id(device), 'handle': device.get('handle', ''),
            'package_name': device.get('package_name', 'auto')}


def task_progress_targets(command, task_config):
    """Freeze only known task semantics; arbitrary counters are not battles."""
    if command != 'Orochi':
        return {}
    data = task_config.model_dump(mode='json') if hasattr(task_config, 'model_dump') else task_config
    options = data.get('orochi_config', {})
    result = {}
    count = options.get('limit_count')
    if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
        result['target_count'] = count
    value = options.get('limit_time')
    try:
        if isinstance(value, str):
            hours, minutes, seconds = map(int, value.split(':'))
            if not (0 <= hours <= 23 and 0 <= minutes <= 59 and 0 <= seconds <= 59):
                return result
        elif value is not None:
            hours, minutes, seconds = value.hour, value.minute, value.second
        else:
            return result
        result['target_seconds'] = hours * 3600 + minutes * 60 + seconds
    except (ValueError, TypeError, AttributeError):
        pass
    return result


class ExecutionRuntime:
    def __init__(self, bridge, profile_name, monotonic=time.monotonic, clock=time.time):
        self.bridge = bridge
        self.profile_id = getattr(bridge, 'profile_id', None) or profile_name
        self.owner_id = getattr(bridge, 'owner_id', None) or str(uuid.uuid4())
        self.monotonic = monotonic
        self.clock = clock
        self.lease = None
        self.lease_started = None
        self.active = None
        self.segment_started = None
        self.segment_wall_started = None
        self.last_seq = None
        self.mode = 'legacy'
        self.batch_seconds = 120.0
        self.business_success = None
        self.storage_failed = False
        self.maintenance_mode = False
        self.device_id = getattr(bridge, 'device_id', None)
        self.recovery_tasks = set()
        self.process_lock = None
        self.configured_device = None
        self.pending_queue_wait = 0.0
        # Only the executor publishes this immutable projection. The preview
        # thread reads one reference; it never walks a mutating checkpoint dict.
        self._progress = None

    @staticmethod
    def _lease_identity(lease):
        return tuple(lease.get(key) for key in ('device_id', 'owner_id', 'epoch', 'generation')) if lease else None

    def _refresh_progress(self, *, state=None, freeze=False):
        if self.active is None:
            if self._progress is not None:
                snapshot = self.progress_snapshot()
                snapshot.update(_segment_origin=None, _lease_identity=None)
                if state is not None:
                    snapshot['state'] = state
                self._progress = snapshot
            return
        active = self.active
        actual_state = state or active.get('state', 'queued')
        held = self.lease is not None and active.get('state') == 'running' and self.segment_started is not None
        base_seconds = float(active.get('device_seconds', 0))
        if held and freeze:
            base_seconds += max(0, self.monotonic() - self.segment_started)
        metrics = self._metrics()
        # Ordering belongs to the logical run, independent of UTC clock
        # corrections. Only the executor advances it; preview reads do not.
        metrics['metric_revision'] = metrics.get('metric_revision', 0) + 1
        count, target = metrics['current_count'], metrics['target_count']
        self._progress = {
            'run_id': active.get('run_id'), 'segment_id': active.get('segment_id'),
            'profile_id': self.profile_id, 'task_id': active.get('task'),
            'state': actual_state, 'execution_seconds': base_seconds,
            'metric_revision': metrics['metric_revision'],
            'target_seconds': active.get('target_seconds'),
            'current_count': count, 'target_count': target,
            'target_unbounded': metrics.get('target_unbounded', False),
            'remaining_target': max(0, target - count) if count is not None and target is not None else None,
            'count_supported': True, 'count_unit': metrics['count_unit'],
            'count_unavailable_reason': None,
            'progress_phase': metrics.get('progress_phase'),
            'battle_count': metrics['battle_count'] if metrics['battle_supported'] else None,
            'battle_supported': metrics['battle_supported'],
            'battle_unavailable_reason': (None if metrics['battle_supported']
                else battle_unavailable_reason(active.get('task'))),
            '_segment_origin': self.segment_started if held and not freeze else None,
            '_lease_identity': self._lease_identity(self.lease) if held and not freeze else None,
        }

    def progress_snapshot(self):
        """A bounded, read-only live projection for the separate preview queue.

        No bridge calls, game reads, config reloads or history writes. Elapsed
        execution grows only during the published running segment's own lease.
        Task progress and deduplicated observed battle results are independent
        counters. Generic tasks expose one item until a task-specific reporter
        provides finer progress. Reading the projection never publishes events.
        """
        published = self._progress
        if published is None:
            result = {'run_id': None, 'segment_id': None, 'profile_id': self.profile_id,
                'task_id': None, 'state': 'inactive', 'execution_seconds': None,
                'metric_revision': None,
                'target_seconds': None, 'current_count': None, 'target_count': None,
                'target_unbounded': False, 'count_unavailable_reason': 'no_active_run',
                'remaining_target': None, 'count_supported': False, 'count_unit': None,
                'progress_phase': None, 'battle_count': None, 'battle_supported': False,
                'battle_unavailable_reason': 'no_active_run'}
        else:
            result = {key: value for key, value in published.items() if not key.startswith('_')}
            origin = published.get('_segment_origin')
            if origin is not None and published.get('_lease_identity') == self._lease_identity(self.lease):
                result['execution_seconds'] += max(0, self.monotonic() - origin)
        result['observed_at'] = datetime.now(timezone.utc).isoformat()
        return result

    def _metrics(self):
        """Upgrade safe checkpoints without changing cooperative resume state."""
        metrics = self.active.get('metrics')
        if metrics is None:
            solo = self.active.get('task') == 'Orochi' and self.active.get('cooperative')
            metrics = {'current_count': int(self.active.get('count', 0)) if solo else 0,
                'target_count': self.active.get('target_count') if solo else 1,
                'count_unit': '次挑战' if solo else '项', 'progress_phase': None,
                'target_unbounded': False,
                'metric_revision': 0,
                'explicit': bool(solo), 'battle_count': 0,
                # A pre-telemetry resumed checkpoint has no battle history.
                # Only a brand-new run can truthfully initialize this to zero.
                'battle_supported': (self.active.get('task') in BATTLE_TASKS
                                     and self.active.get('state') == 'queued'),
                'battle_sequence': 0, 'pending_battle': None}
            self.active['metrics'] = metrics
        return metrics

    @staticmethod
    def _valid_count(value):
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0

    def report_progress(self, current, target=None, unit='次', phase=None, *, target_unbounded=None):
        """Publish a verified task counter independently of safe continuation.

        Tasks call this at observable progress changes, never once per frame.
        Duplicate observations cause no durable writes. Progress is monotonic
        within one phase so resumed/retried task setup cannot reset it to zero.
        """
        if not self.active:
            return False
        if not self._valid_count(current) or target is not None and not self._valid_count(target):
            raise ValueError('Progress counts must be non-negative integers')
        if not isinstance(unit, str) or not unit or len(unit) > 32:
            raise ValueError('Progress unit must be a short non-empty string')
        if phase is not None and (not isinstance(phase, str) or len(phase) > 128):
            raise ValueError('Progress phase must be a short string')
        if target_unbounded is not None and not isinstance(target_unbounded, bool):
            raise ValueError('Unbounded target flag must be a boolean')
        if target_unbounded and target is not None:
            raise ValueError('An unbounded target cannot also specify a count')
        metrics = self._metrics()
        # A repeated phase initializer during continuation must not discard
        # already verified work. A different named phase may use a new counter.
        if metrics['explicit'] and phase == metrics.get('progress_phase') and unit == metrics['count_unit']:
            current = max(current, metrics['current_count'])
            if target is None and target_unbounded is None:
                target = metrics['target_count']
        unbounded = (metrics.get('target_unbounded', False) if target_unbounded is None and target is None
                     else bool(target_unbounded))
        values = {'current_count': current, 'target_count': target,
                  'target_unbounded': unbounded,
                  'count_unit': unit, 'progress_phase': phase, 'explicit': True}
        if all(metrics.get(key) == value for key, value in values.items()):
            return False
        metrics.update(values)
        self._refresh_progress()
        self.event('run.progress_observed', self.progress_snapshot())
        self.save()
        return True

    def begin_battle(self):
        """Mark a concrete attempt; starting/abandoning one never counts it."""
        if not self.active:
            return None
        metrics = self._metrics()
        metrics['battle_sequence'] += 1
        token = str(uuid.uuid5(uuid.NAMESPACE_URL,
            'oas:battle:' + self.active['run_id'] + ':' + str(metrics['battle_sequence'])))
        first_observation = not metrics['battle_supported']
        metrics.update(pending_battle=token, battle_supported=True)
        self._refresh_progress()
        if first_observation:
            self.event('run.progress_observed', self.progress_snapshot())
        self.save()
        return token

    def finish_battle(self, token, result):
        """Count an observed settlement once, with a retry-stable event ID.

        An outstanding token replaces no result: an exception, a skipped fight,
        or an unrecognized screen does not become a completed battle. Keeping
        only the pending token bounds checkpoint size and rejects old tokens.
        """
        if not self.active or not token or result not in ('won', 'lost', 'settled'):
            return False
        metrics = self._metrics()
        if token != metrics.get('pending_battle'):
            return False
        metrics['battle_count'] += 1
        metrics['pending_battle'] = None
        self._refresh_progress()
        payload = dict(self.progress_snapshot(), battle_id=token, result=result)
        self.event('battle.finished', payload,
                   event_id=str(uuid.uuid5(uuid.NAMESPACE_URL, 'oas:battle:finished:' + token)))
        self.save()
        return True

    def record_progress(self, count):
        """Called only after Orochi recognizes its post-settlement lobby."""
        if not self.active or self.active.get('task') != 'Orochi' or not self.active.get('cooperative'):
            return
        self.active['count'] = max(0, int(count))
        target = self.active.get('target_count')
        if target is not None:
            self.active['remaining_target'] = max(0, target - self.active['count'])
        metrics = self._metrics()
        self.report_progress(self.active['count'], target,
            unit=metrics['count_unit'], phase=metrics.get('progress_phase'))

    def event(self, event_type, payload=None, task_id=None, *, event_id=None):
        event = {'schema_version': 1, 'event_id': event_id or str(uuid.uuid4()), 'type': event_type,
                 'occurred_at': datetime.now(timezone.utc).isoformat(), 'source': {'type': 'executor'},
                 'profile_id': self.profile_id, 'device_id': self.lease['device_id'] if self.lease else None,
                 'run_id': self.active.get('run_id') if self.active else None,
                 'segment_id': self.active.get('segment_id') if self.active else None,
                 'task': self.active.get('task') if self.active else task_id,
                 'task_id': self.active.get('task') if self.active else task_id,
                 'config_revision': self.active.get('config_revision') if self.active else None,
                 'payload': payload or {}}
        try:
            ack = self.bridge.call('event.append', event)
            self.last_seq = ack.get('seq', ack.get('event', {}).get('seq'))
            return ack
        except Exception:
            self.storage_failed = True
            raise

    def skip_before_begin(self, command, decision):
        """Account the lease and audit a proven prerequisite, without a run."""
        from .preflight import BusinessSkip
        if not isinstance(decision, BusinessSkip) or self.active is not None:
            raise ValueError('A preflight skip requires an explicit decision before run.begin')
        try:
            self.event('scheduler.skipped', {
                'reason': decision.reason, 'phase': 'business_preflight',
                'next_run': decision.next_run.isoformat(),
                'device_seconds': max(0, self.monotonic() - self.lease_started) if self.lease else 0,
            }, task_id=command)
        except Exception as exc:
            raise ReconciliationRequired('Business skip audit could not be recorded') from exc
        finally:
            self.release('skipped')

    def _key(self, command):
        return hashlib.sha256((self.profile_id + ':' + command).encode()).hexdigest()

    def save(self):
        if not self.active:
            return
        self.active['event_seq'] = self.last_seq
        try:
            self.bridge.call('scheduling.checkpoint', {'action': 'save', 'kind': 'runs',
                'key': self._key(self.active['task']), 'data': self.active, 'event_seq': self.last_seq})
        except Exception:
            self.storage_failed = True
            raise

    def control_status(self):
        return self.bridge.call('scheduling.control', {'action': 'status', 'profile_id': self.profile_id,
            'device_id': self.device_id or 'unresolved:auto'})

    def stop_at_boundary(self):
        """Cancel known suspended runs; uncertain crashed work remains uncertain.

        Fault-protection stopping must proceed even when recording has failed.
        The parent surfaces the persisted/unsaved distinction.
        """
        try:
            if self.active and self.active.get('state') == 'running':
                self.finish('cancelled', 'safe_stop')
            records = self.bridge.call('scheduling.checkpoint', {'action': 'list', 'kind': 'runs',
                'profile_id': self.profile_id}).get('items', [])
            for record in records:
                if record.get('state') not in ('yielded', 'paused', 'recovery_requested'):
                    continue
                self.active = record
                self.active.update(state='cancelled', terminal=True)
                self._refresh_progress()
                self.event('run.finished', dict(self.progress_snapshot(),
                    outcome='cancelled', reason='safe_stop',
                    execution_seconds=record.get('device_seconds', 0)))
                self.save()
                self.active = None
        except Exception:
            self.storage_failed = True
        raise DispatchStopped(0)

    def _lock_process(self):
        lock = DeviceProcessLock(self.lease['device_id'])
        if not lock.acquire():
            self.release()
            return False
        self.process_lock = lock
        return True

    def acquire(self, config, release_floor=0, preferred_task=None):
        if self.storage_failed:
            raise ReconciliationRequired('Storage failed; reconcile before dispatching another task')
        raw_device = config.script.device.model_dump(mode='json')
        fingerprint = device_revision(raw_device)
        if self.configured_device is not None and self.configured_device != fingerprint:
            raise ReconciliationRequired('Device target changed; stop and restart this profile')
        self.configured_device = fingerprint
        device_id = getattr(self.bridge, 'device_id', None) or normalize_device_id(raw_device)
        self.device_id = device_id
        candidates = []
        for task in config.pending_task + config.waiting_task + (getattr(config, 'expired_task', None) or []):
            command = task.command
            metadata = getattr(task, 'scheduling', {})
            candidates.append({'task': command, 'release_at': max(release_floor, task.next_run.timestamp()),
                'enabled': getattr(task, 'enable', True),
                'quantum': metadata.get('estimated_batch_seconds', self.batch_seconds),
                'weight': metadata.get('fair_weight', 1), 'deadline': metadata.get('deadline'),
                'config_revision': metadata.get('config_revision', ''),
                'blocked_reason': ('device_recovery_pending' if self.recovery_tasks and
                    command != 'Restart' and command not in self.recovery_tasks else
                    'team_rendezvous_pending' if preferred_task and command != preferred_task and
                    command != 'Restart' and command not in self.recovery_tasks else None),
                'cooperative': cooperative_task(command, config),
                'recovery': command in self.recovery_tasks or command == 'Restart' and bool(self.recovery_tasks)})
        result = self.bridge.call('scheduling.acquire', {'profile_id': self.profile_id,
            'owner_id': self.owner_id, 'device_id': device_id, 'candidates': candidates,
            'legacy_order': [t.command for t in config.pending_task]})
        if result['status'] == 'acquired':
            self.lease = result['lease']
            self.lease_started = self.monotonic()
            if not self._lock_process():
                return {'status': 'waiting_resource', 'reason': 'previous_process_still_owns_device'}
            self.mode, self.batch_seconds = result.get('mode', 'legacy'), result.get('batch_seconds', 120)
            self.pending_queue_wait = result.get('queue_wait_seconds', 0)
            self.event('scheduler.selected', result.get('decision', {}))
            if result.get('decision', {}).get('reason') in ('recovery', 'real_deadline', 'waiting_limit'):
                self.event('scheduler.override', result['decision'])
        return result

    def validate(self):
        if not self.lease and self.maintenance_mode:
            result = self.bridge.call('scheduling.maintenance', {'profile_id': self.profile_id,
                'owner_id': self.owner_id, 'device_id': self.device_id})
            if result['status'] == 'acquired':
                self.lease = result['lease']
                self.lease_started = self.monotonic()
                if not self._lock_process():
                    raise LeaseLost('Another process still owns this device')
        if not self.lease:
            raise LeaseLost('No device execution lease')
        try:
            return self.bridge.call('scheduling.validate', {'lease': self.lease})
        except Exception as exc:
            if self.active is not None and self.active.get('state') == 'running':
                self._refresh_progress(state='needs_reconciliation', freeze=True)
            raise LeaseLost('Unable to verify device execution authority') from exc

    def release(self, outcome='yielded'):
        if not self.lease:
            return
        if self.active is not None and self.active.get('state') == 'running':
            self._refresh_progress(state='waiting_resource', freeze=True)
        # Release the OS lock only after the parent durably accounts this segment.
        # Failure keeps the lock until worker exit; it cannot expose two workers.
        self.bridge.call('scheduling.release', {'lease': self.lease,
            'actual_seconds': max(0, self.monotonic() - self.lease_started), 'outcome': outcome})
        if self.process_lock:
            self.process_lock.release()
            self.process_lock = None
        self.lease = None
        self.lease_started = None

    def begin(self, command, task_config, cooperative=False, device_config=None):
        if self.active:
            raise RuntimeError('Logical run is already executing')
        deadline = task_deadline(task_config)
        if is_expired(deadline, self.clock()):
            # A deadline may pass while the worker waits for its lease/team.
            # No new run or game action has started at this point.
            self.event('scheduler.skipped', {'reason': 'deadline_expired'}, task_id=command)
            self.release('deadline_expired')
            raise DeadlineExpired('活动截止时间已到，跳过本次执行')
        revision = config_revision(task_config)
        if device_config is not None:
            revision = config_revision({'task_revision': revision, 'device': device_revision(device_config)})
        old = self.bridge.call('scheduling.checkpoint', {'action': 'get', 'kind': 'runs',
            'key': self._key(command)}).get('data')
        if old and old.get('state') not in TERMINAL:
            allowed = old.get('state') in ('yielded', 'paused', 'recovery_requested')
            if not allowed or old.get('in_flight') or old.get('config_revision') != revision:
                self.active = old
                self.active['state'] = 'needs_reconciliation'
                self._refresh_progress(freeze=True)
                self.event('recovery.requested', dict(self.progress_snapshot(),
                    reason='unverified_checkpoint', state='needs_reconciliation'))
                self.save()
                self.active = None
                raise ReconciliationRequired('Previous run or changed configuration needs reconciliation')
            self.active = old
            self.active['state'] = 'running'
            self._refresh_progress()
            self.event('run.resumed', dict(self.progress_snapshot(), reason=old.get('suspend_reason', 'resume')))
        else:
            self.active = {'schema_version': 1, 'profile_id': self.profile_id, 'task': command,
                'device_id': self.lease['device_id'], 'run_id': str(uuid.uuid4()),
                'config_revision': revision, 'state': 'queued', 'count': 0,
                'device_seconds': 0.0, 'cooperative': cooperative, 'in_flight': False,
                'safe_boundary': None, 'created_at': datetime.now(timezone.utc).isoformat()}
            self.active.update(task_progress_targets(command, task_config))
            self._refresh_progress()
            self.event('run.created', dict(self.progress_snapshot(), task=command))
            self.save()
            self.active['state'] = 'running'
            self._refresh_progress()
            self.event('run.started', dict(self.progress_snapshot(), task=command, cooperative=cooperative))
        self.business_success = None
        self.active['real_deadline'] = deadline
        self.active['queue_wait_seconds'] = self.active.get('queue_wait_seconds', 0) + self.pending_queue_wait
        self.pending_queue_wait = 0.0
        self.active['segment_id'] = str(uuid.uuid4())
        self.segment_started = self.monotonic()
        self.segment_wall_started = datetime.now(timezone.utc).isoformat()
        self._refresh_progress()
        self.event('segment.started', dict(self.progress_snapshot(), task=command, started_at=self.segment_wall_started))
        self.active['in_flight'] = True
        self.save()  # crash after dispatch never looks like an untouched safe checkpoint
        self._refresh_progress()

    @property
    def count(self):
        return int(self.active.get('count', 0)) if self.active else 0

    @property
    def active_seconds(self):
        return (float(self.active.get('device_seconds', 0)) if self.active else 0) + (
            self.monotonic() - self.segment_started if self.segment_started is not None else 0)

    def before_battle(self, count):
        if self.active:
            self.active.update(count=int(count), in_flight=True, safe_boundary=None)
            self.save()
            self._refresh_progress()

    def boundary_request(self):
        status = self.validate()
        control = status.get('control')
        if control in ('safe_stop', 'stop'):
            return 'cancelled'
        if control in ('pause', 'paused'):
            return 'paused'
        if self.storage_failed or status.get('storage_degraded'):
            return 'interrupted'
        if self.deadline_expired():
            return 'deadline_expired'
        if self.mode == 'eevdf' and self.monotonic() - self.segment_started >= self.batch_seconds:
            return 'yielded'
        return None

    def deadline_expired(self):
        return bool(self.active and is_expired(self.active.get('real_deadline'), self.clock()))

    def safe_boundary(self, count, verified, outcome):
        if not verified:
            raise ReconciliationRequired('Safe boundary was not visually verified')
        if not self.active or not self.active.get('cooperative'):
            raise RuntimeError('Task does not implement safe continuation')
        self.active.update(count=int(count), in_flight=False, safe_boundary='page_main')
        try:
            self.record_progress(count)
            self.save()
        except Exception:
            if outcome not in ('cancelled', 'interrupted'):
                raise
            # A safe stop already reached verified main. Failing to write must
            # not cause more gameplay or prevent the process from stopping.
            outcome = 'interrupted'
        raise SafeBoundaryExit(outcome)

    def finish(self, outcome, reason=None):
        if not self.active:
            return
        seconds = max(0, self.monotonic() - self.segment_started)
        self.active['device_seconds'] = float(self.active.get('device_seconds', 0)) + seconds
        self.active['state'] = outcome
        self.active['terminal'] = outcome in TERMINAL
        self.active['suspend_reason'] = reason
        metrics = self._metrics()
        if outcome == 'succeeded' and not metrics['explicit']:
            metrics['current_count'] = 1
        self._refresh_progress()
        metric_payload = self.progress_snapshot()
        self.event('segment.finished', {**metric_payload, 'task': self.active['task'], 'duration_seconds': seconds,
            'device_seconds': seconds, 'started_at': self.segment_wall_started,
            'ended_at': datetime.now(timezone.utc).isoformat(),
            'finished_at': datetime.now(timezone.utc).isoformat(), 'outcome': outcome})
        if outcome in TERMINAL:
            self.event('run.finished', {**metric_payload, 'task': self.active['task'], 'outcome': outcome,
                'reason': reason, 'duration_seconds': self.active['device_seconds'],
                'queue_wait_seconds': self.active.get('queue_wait_seconds', 0),
                'error_category': (reason or 'business_failure') if outcome == 'failed' else None,
                'device_seconds': self.active['device_seconds'], 'execution_seconds': self.active['device_seconds']})
        else:
            self.event({'yielded': 'run.yielded', 'paused': 'run.paused',
                        'recovery_requested': 'recovery.requested'}[outcome],
                       {**metric_payload, 'task': self.active['task'], 'reason': reason})
        self.active['in_flight'] = outcome not in ('yielded', 'paused') and outcome not in TERMINAL
        # Recovery may repeat the task only after an explicit known failure, not an unknown crash.
        if outcome == 'recovery_requested':
            self.active['in_flight'] = False
            self.recovery_tasks.add(self.active['task'])
        elif outcome in TERMINAL:
            self.recovery_tasks.discard(self.active['task'])
            if self.active['task'] == 'Restart' and outcome == 'succeeded':
                self.recovery_tasks.clear()
        self.save()
        self.active = None
        self.segment_started = None
        # Expiry cancels this activity only, unlike the user's profile stop.
        self.release('deadline_expired' if reason == 'deadline_expired' else outcome)


def classify_outcome(success, legacy_outcome=None, business_success=None):
    """Legacy True means scheduler may continue; it does not imply game success."""
    status = (legacy_outcome or {}).get('status')
    if status in ('yielded', 'paused', 'cancelled', 'interrupted', 'failed'):
        return status
    if status in ('retry_scheduled', 'recovery_requested', 'server_update_delayed', 'team_wait_failed'):
        return 'recovery_requested'
    if status in ('team_preempted', 'team_partner_finished'):
        return 'interrupted'
    if status in ('business_skipped', 'deadline_expired'):
        # Explicit prerequisite discovered after a run was already started
        # (e.g. crossing an activity-window boundary during preparation).
        return 'cancelled'
    if status == 'skipped' or business_success is False:
        return 'failed'
    return 'succeeded' if success else 'failed'
