"""Parent-owned Solana service, independent of game imports for fault testing."""
from collections import deque, OrderedDict
from dataclasses import asdict
from datetime import datetime, timezone
import copy
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import uuid

from module.scheduling.coordinator import Coordinator, normalize_device_id
from module.server.solana_recovery import RecoveryMixin
from module.server.solana_metrics import ExecutionMetricsMixin, METRIC_FIELDS, observation_is_current

TERMINAL = {'succeeded', 'failed', 'cancelled', 'interrupted', 'crashed'}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class ServiceError(RuntimeError):
    def __init__(self, code, message, status=409):
        self.code, self.status = code, status
        super().__init__(message)


class RuntimeService(RecoveryMixin, ExecutionMetricsMixin):
    def __init__(self, store, adapter=None, replay_limit=1024):
        self.store, self.adapter = store, adapter
        self.lock = threading.RLock()
        self.stream_id, self.stream_seq = str(uuid.uuid4()), 0
        self.replay = deque(maxlen=replay_limit)
        self.run_pages = OrderedDict()
        self.previews = OrderedDict()
        self.progress = {}
        self._external_usage = None
        self._external_usage_at = 0
        self.owners, self.profile_state, self.runs = {}, {}, {}
        self.active_operations = set()
        self.latest_control_request = {}
        self.closed = False
        self.dispatch_blocked = False
        self._reducer_dirty = False
        if self.store.checkpoints.get('settings', 'reducer_cursor') is None:
            self.store.checkpoints.save('settings', 'reducer_cursor', {'cursor_seq': 0, 'retention_consumer': True})
        self.registry = self.store.checkpoints.get('settings', 'profiles') or {'names': {}}
        self.coordinator = Coordinator(checkpoint_store=store.checkpoints, event_sink=self.emit)
        policy = self.store.checkpoints.get('settings', 'scheduler_policy')
        if policy:
            self.coordinator.set_policy(policy)
        for record in self.store.checkpoints.list('runtime_runs'):
            # CheckpointStore.list can return records keyed by ID or record values.
            run = record.get('data', record) if isinstance(record, dict) else None
            if run and run.get('run_id'):
                self.runs[run['run_id']] = run
        self._load_execution_cycles()
        self._recover_reducer(mark_orphans=True)
        self.dispatch_blocked = self.dispatch_blocked or not self.store.storage_status()['dispatch_allowed']

    def _recover_reducer(self, mark_orphans=False):
        watermark = (self.store.checkpoints.get('settings', 'reducer_cursor') or {}).get('cursor_seq', 0)
        pending, cursor = [], None
        while True:
            page = self.store.query(filters={'after_seq': watermark}, cursor=cursor, limit=200)
            pending.extend(page['items'])
            cursor = page['next_cursor']
            if page.get('bounded_reason') and not cursor:
                self.dispatch_blocked = True
                self._reducer_dirty = True
                # Descending pages are a suffix, not a consumed prefix. Never
                # commit their high watermark if an older shard was not read.
                return False
            if not cursor:
                break
        for event in sorted(pending, key=lambda item: item['seq']):
            self._reduce(event)
            watermark = event['seq']
        if watermark:
            self.store.checkpoints.save('settings', 'reducer_cursor',
                {'cursor_seq': watermark, 'retention_consumer': True}, event_seq=watermark)
        self._reducer_dirty = False
        for run_id, run in list(self.runs.items()) if mark_orphans else []:
            if run['state'] not in TERMINAL | {'paused', 'yielded'}:
                run = dict(run, state='needs_reconciliation', recovery_reason='service_restarted')
                self.runs[run_id] = run
                self.store.checkpoints.save('runtime_runs', run_id, run)
        return True

    def profile_id(self, name):
        with self.lock:
            if name not in self.registry['names']:
                self.registry['names'][name] = str(uuid.uuid4())
                self.store.checkpoints.save('settings', 'profiles', self.registry)
            return self.registry['names'][name]

    def profile_name(self, identity):
        with self.lock:
            names = self.adapter.names() if self.adapter else list(self.registry['names'])
            if identity in names:
                self.profile_id(identity)
                return identity
            for name in names:
                if self.profile_id(name) == identity:
                    return name
        raise ServiceError('profile_not_found', '配置不存在', 404)

    def rename_profile(self, old_name, new_name):
        with self.lock:
            identity = self.profile_id(old_name)
            self.registry['names'][new_name] = identity
            self.registry['names'].pop(old_name, None)
            self.store.checkpoints.save('settings', 'profiles', self.registry)
            self.publish('profiles.changed', {'profile_id': identity, 'name': new_name})

    def register_process(self, name, owner_id):
        with self.lock:
            if self.dispatch_blocked:
                raise ServiceError('reconciliation_required', '存储恢复后须先核验状态', 503)
            profile_id = self.profile_id(name)
            for owner in self.owners.values():
                if owner['profile_id'] == profile_id and owner['alive']:
                    raise ServiceError('already_running', '该配置已启动')
            device = self.adapter.device(name) if self.adapter else {'serial': 'auto'}
            device_id = normalize_device_id(device)
            self._start_execution_cycle(name, profile_id, owner_id)
            self.coordinator.request_control(profile_id, 'resume')
            self.owners[owner_id] = {'profile_id': profile_id, 'name': name,
                                     'device_id': device_id, 'alive': True, 'cycle_id': owner_id}
            self._set_state(profile_id, 'starting')
            return {'profile_id': profile_id, 'owner_id': owner_id, 'device_id': device_id}

    def _set_state(self, profile_id, state):
        previous = self.profile_state.get(profile_id, {})
        alive = self.executor_alive(profile_id)
        if previous.get('state') == state and previous.get('executor_alive') == alive:
            return
        current = {'state': state, 'executor_alive': alive,
                   'state_version': previous.get('state_version', 0) + 1}
        self.profile_state[profile_id] = current
        self.publish('profile.state', {'profile_id': profile_id,
                                      'executor_alive': self.executor_alive(profile_id), **current})

    def executor_alive(self, profile_id):
        """Ownership follows confirmed process exit, never retained run history."""
        return any(owner['profile_id'] == profile_id and owner['alive']
                   for owner in self.owners.values())

    def publish(self, event_type, payload, event_id=None):
        with self.lock:
            self.stream_seq += 1
            event = {'schema_version': 1, 'type': event_type, 'payload': copy.deepcopy(payload),
                     'event_id': event_id or str(uuid.uuid4()), 'occurred_at': utc_now(),
                     'stream_id': self.stream_id, 'stream_seq': self.stream_seq}
            self.replay.append(event)
            return event

    def stream_after(self, stream_id, after):
        with self.lock:
            oldest = self.replay[0]['stream_seq'] if self.replay else self.stream_seq + 1
            if stream_id != self.stream_id or after < oldest - 1 or after > self.stream_seq:
                return {'resync_required': True, 'stream_id': self.stream_id, 'stream_seq': self.stream_seq}
            return {'items': copy.deepcopy([e for e in self.replay if e['stream_seq'] > after])}

    def emit(self, event):
        with self.lock:
            event = dict(event)
            event.setdefault('event_id', str(uuid.uuid4()))
            event.setdefault('schema_version', 1)
            event.setdefault('occurred_at', utc_now())
            event.setdefault('source', {'type': 'backend'})
            event.setdefault('payload', {})
            try:
                if self._reducer_dirty:
                    if not self._recover_reducer():
                        raise ServiceError('recovery_scan_limit', '恢复扫描未完成，不能跳过早期记录', 503)
                ack = self.store.append(event)
                event['seq'] = ack.get('seq')
                self._reduce(event)
                if event.get('seq'):
                    current = self.store.checkpoints.get('settings', 'reducer_cursor') or {}
                    if event['seq'] > current.get('cursor_seq', 0):
                        self.store.checkpoints.save('settings', 'reducer_cursor',
                            {'cursor_seq': event['seq'], 'retention_consumer': True}, event_seq=event['seq'])
                if not ack.get('duplicate'):
                    from module.observability.common import redact
                    self.publish(event['type'], redact(event), event['event_id'])
            except Exception:
                self._reducer_dirty = True
                self.dispatch_blocked = True
                self.publish('storage.degraded', {'state': 'degraded', 'dispatch_blocked': True})
                raise
            return ack

    def _reduce(self, event):
        # Its own checkpoint watermark makes this replayable independently of
        # runtime_runs (including a crash between either checkpoint write).
        self._reduce_execution_cycle(event)
        if event['type'] == 'control.completed' and event.get('request_id'):
            document = self.store.checkpoints.get_document('requests', event['request_id'])
            if document and (not event.get('seq') or event['seq'] > document['event_seq']):
                saved = document['data']
                payload = event.get('payload', {})
                unknown = payload.get('status') == 'unknown_state_accepted'
                saved['result'].update(executed=None if unknown else bool(payload.get('executed')),
                    status=payload.get('status', 'completed'), persisted=True)
                if unknown:
                    saved['result'].update(historical_outcome='unknown', current_state_verified=True,
                                           reexecuted=False)
                saved.update(terminal=True, verified=True)
                self.store.checkpoints.save('requests', event['request_id'], saved, event_seq=event.get('seq'))
        run_id = event.get('run_id')
        payload = event.get('payload', {})
        if not run_id:
            return
        existing = self.runs.get(run_id)
        if existing and event.get('seq') and event['seq'] <= existing.get('last_event_seq', 0):
            return
        run = copy.deepcopy(existing or {'run_id': run_id, 'state': 'queued'})
        for key in ('profile_id', 'task_id', 'device_id', 'segment_id', 'config_revision', 'execution_cycle_id'):
            if key in event:
                run[key] = event[key]
        states = {'run.created': 'queued', 'run.started': 'running', 'run.resumed': 'running',
                  'segment.started': 'running', 'run.yielded': 'yielded', 'run.paused': 'paused',
                  'recovery.requested': 'recovery_requested', 'recovery.resolved': 'yielded'}
        event_type = event['type']
        if run['state'] in TERMINAL and event_type != 'run.finished':
            return
        run['updated_at'] = event['occurred_at']
        if event_type == 'run.started':
            run.setdefault('started_at', event['occurred_at'])
        if event_type == 'run.finished':
            run.update(state=payload.get('outcome', 'interrupted'), outcome=payload.get('outcome'),
                       finished_at=event['occurred_at'], terminal=True, verified=True)
        elif event_type in states:
            run['state'] = states[event_type]
            if event_type == 'recovery.requested' and payload.get('state') == 'needs_reconciliation':
                run['state'] = 'needs_reconciliation'
        if event_type == 'segment.finished':
            accounted = run.get('accounted_execution_seconds', run.get('execution_seconds', 0))
            run['accounted_execution_seconds'] = accounted + payload.get('duration_seconds', 0)
            run['execution_seconds'] = run['accounted_execution_seconds']
        for key in ('error_category', 'reason'):
            if key in payload:
                run[key] = payload[key]
        # Durable event order is authoritative, including after a UTC clock
        # adjustment. Only asynchronous preview frames need freshness checks.
        if METRIC_FIELDS.intersection(payload):
            run.update({key: payload[key] for key in METRIC_FIELDS if key in payload})
            if payload.get('execution_seconds') is not None:
                run.setdefault('accounted_execution_seconds', run.get('execution_seconds', 0))
                run['execution_seconds'] = payload['execution_seconds']
            if 'battle_count' in payload:
                run['battle_scope'] = 'current_run'
            self.progress.pop(run_id, None)
        # Terminal history is in the event store; keep recovery state bounded.
        run['last_event_seq'] = event.get('seq') or run.get('last_event_seq', 0)
        self.store.checkpoints.save('runtime_runs', run_id, run, event_seq=event.get('seq'))
        self.runs[run_id] = run
        profile_id = run.get('profile_id')
        if profile_id:
            self._set_state(profile_id, 'waiting' if run['state'] in TERMINAL else run['state'])

    def dispatch(self, owner_id, operation, payload):
        with self.lock:
            owner = self.owners.get(owner_id)
            if not owner or not owner['alive']:
                raise ServiceError('owner_revoked', '执行器已失去控制权')
            if operation == 'event.append':
                payload = {**payload, 'profile_id': owner['profile_id'], 'device_id': owner['device_id'],
                           'execution_cycle_id': owner.get('cycle_id')}
                return self.emit(payload)
            if not operation.startswith('scheduling.'):
                raise ServiceError('unknown_operation', '未知运行操作', 400)
            if operation in ('scheduling.acquire', 'scheduling.maintenance') and self.dispatch_blocked:
                # Protective controls must reach the worker even while new
                # dispatch is fenced. Otherwise a due task can poll forever.
                return {'status': 'waiting', 'reason': 'storage_reconciliation_required',
                        'control': self.coordinator.controls.get(owner['profile_id'], '')}
            payload = dict(payload)
            if operation in ('scheduling.acquire', 'scheduling.control', 'scheduling.maintenance'):
                payload.update(profile_id=owner['profile_id'], owner_id=owner_id, device_id=owner['device_id'])
            if 'lease' in payload and payload['lease'].get('owner_id') != owner_id:
                raise ServiceError('owner_mismatch', '控制权不属于当前执行器')
            result = self.coordinator.dispatch(operation, payload)
            if operation == 'scheduling.validate':
                result['storage_degraded'] = self.dispatch_blocked
            if operation == 'scheduling.acquire' and result.get('control') in ('pause', 'paused'):
                if self.profile_state.get(owner['profile_id'], {}).get('state') != 'paused':
                    self._set_state(owner['profile_id'], 'paused')
            elif operation == 'scheduling.acquire' and result.get('status') in ('waiting', 'waiting_resource'):
                state = result['status']
                if self.profile_state.get(owner['profile_id'], {}).get('state') != state:
                    self._set_state(owner['profile_id'], state)
            if operation in ('scheduling.acquire', 'scheduling.release', 'scheduling.control'):
                self.publish('scheduler.changed', {'profile_id': owner['profile_id']})
            return result

    def process_exited(self, owner_id, exit_code):
        with self.lock:
            owner = self.owners.get(owner_id)
            if not owner or owner.get('cleanup_complete'):
                return
            owner['alive'] = False
            owner['confirmed_dead'] = True
            owner['exit_code'] = exit_code
            # Only called after Process.join(), never on heartbeat expiry.
            try:
                self.coordinator.revoke_owner(owner_id, confirmed_dead=True)
                for run in list(self.runs.values()):
                    if run.get('profile_id') != owner['profile_id'] or run['state'] in TERMINAL:
                        continue
                    if run['state'] not in ('paused', 'yielded'):
                        run['state'] = 'needs_reconciliation'
                        run['recovery_reason'] = 'executor_exit'
                        self.store.checkpoints.save('runtime_runs', run['run_id'], run)
                self.emit({'type': 'execution.cycle_finished', 'profile_id': owner['profile_id'],
                           'execution_cycle_id': owner.get('cycle_id'),
                           'payload': {'state': 'stopped' if exit_code == 0 else 'crashed'}})
                owner['cleanup_complete'] = True
            except Exception:
                self.dispatch_blocked = True
            self._set_state(owner['profile_id'], 'inactive' if exit_code == 0 else 'warning')

    def record_preview(self, owner_id, frame):
        with self.lock:
            owner = self.owners.get(owner_id)
            if self.closed or not owner or not owner['alive']:
                return
            if frame.get('mime_type') != 'image/jpeg' or len(frame.get('image_base64', '')) > 128 * 1024:
                return
            self.previews[owner['profile_id']] = {**frame, 'profile_id': owner['profile_id'],
                'device_id': owner['device_id'], 'received_monotonic': time.monotonic()}
            self.previews.move_to_end(owner['profile_id'])
            while len(self.previews) > 16:
                self.previews.popitem(last=False)
            progress = frame.get('progress')
            if isinstance(progress, dict):
                run = self.runs.get(progress.get('run_id'))
                if (run and run['state'] == 'running' and run.get('profile_id') == owner['profile_id']
                        and run.get('segment_id') == progress.get('segment_id')
                        and observation_is_current(progress, run)
                        and observation_is_current(progress, self.progress.get(run['run_id'], {}))):
                    self.progress[run['run_id']] = {key: value for key, value in progress.items() if key in {
                        'run_id', 'segment_id', 'task_id', 'execution_seconds', 'target_seconds',
                        *METRIC_FIELDS}}
                    self.publish('run.progress', self.progress[run['run_id']])

    def preview(self, profile_id):
        with self.lock:
            profile_id = self.profile_id(self.profile_name(profile_id))
            frame = self.previews.get(profile_id)
            if not frame:
                return {'available': False, 'profile_id': profile_id, 'reason': 'no_captured_frame'}
            age = max(0, time.monotonic() - frame['received_monotonic'])
            return {**{key: value for key, value in frame.items() if key != 'received_monotonic'},
                    'stale': age > 10, 'age_seconds': age}

    def overview(self):
        with self.lock:
            names = self.adapter.names() if self.adapter else list(self.registry['names'])
            profiles = [{'id': self.profile_id(name), 'name': name,
                         'executor_alive': self.executor_alive(self.profile_id(name)),
                         **self.profile_state.get(self.profile_id(name), {'state': 'inactive', 'state_version': 0})}
                        for name in names]
            for profile in profiles:
                if self.adapter:
                    device = self.adapter.device(profile['name'])
                    profile['device'] = {'id': normalize_device_id(device),
                        'name': device.get('emulatorinfo_name') or device.get('emulatorinfo_type') or '设备',
                        'serial': device.get('serial') or 'auto', 'status': 'unknown'}
                    frame = self.previews.get(profile['id'])
                    if frame:
                        profile['device'].update(status='observed' if time.monotonic() - frame['received_monotonic'] <= 10 else 'stale',
                                                 observed_at=frame['occurred_at'])
            devices = [{'id': key, **value} for key, value in self.coordinator.snapshot().items()]
            from module.observability.common import report_zone
            zone = report_zone(getattr(self.store.policy, 'timezone', 'Asia/Shanghai'))
            date = datetime.now(zone).date().isoformat()
            today = self.store.statistics(start_date=date, end_date=date)
            return copy.deepcopy({'profiles': profiles, 'devices': devices,
                    'current_runs': [{**r, **(self.progress.get(r['run_id'], {}) if r['state'] == 'running' else {})}
                                     for r in self.runs.values() if r['state'] not in TERMINAL],
                    'execution_cycles': [cycle for identity, cycle in self.execution_cycles.items()
                                         if identity in {p['id'] for p in profiles}],
                    'today': today.get('totals', {}), 'updated_at': utc_now(),
                    'dispatch_blocked': self.dispatch_blocked,
                    'stream_id': self.stream_id, 'stream_seq': self.stream_seq})

    def scheduler_snapshot(self, profile_id=None):
        with self.lock:
            devices = self.coordinator.snapshot()
            queues = self.adapter.queues(self) if self.adapter else {'ready': [], 'waiting': []}
            if profile_id:
                queues = {key: [item for item in value if item.get('profile_id') == profile_id]
                          for key, value in queues.items()}
            return {'policy': asdict(self.coordinator.policy), **queues,
                    'running': copy.deepcopy([r for r in self.runs.values() if r['state'] == 'running'
                                              and (not profile_id or r.get('profile_id') == profile_id)]),
                    'decisions': [d['decision'] for d in devices.values() if d.get('decision')],
                    'devices': devices}

    def query_runs(self, profile_id=None, task_id=None, start_date=None, end_date=None, cursor=None, limit=50):
        """Logical runs with bounded, immutable pagination snapshots.

        Snapshots expire after 2 minutes or history cleanup; a client is told to
        refresh instead of receiving silently missing/duplicated rows.
        """
        from module.observability.common import HistoryExpired, timestamp, report_zone
        if not 1 <= limit <= 200:
            raise ValueError('Invalid page size')
        filters = (profile_id, task_id, start_date, end_date)
        with self.lock:
            now = time.monotonic()
            generation = self.store.manifest['generation']
            for key, snapshot in list(self.run_pages.items()):
                if now - snapshot['at'] > 120 or snapshot['generation'] != generation:
                    del self.run_pages[key]
            if cursor:
                try:
                    token, offset = cursor.split(':')
                    offset = int(offset)
                    snapshot = self.run_pages[token]
                except (KeyError, ValueError):
                    raise HistoryExpired('运行列表已过期，请刷新')
                if snapshot['filters'] != filters or offset < 0:
                    raise ValueError('Cursor filters do not match')
            else:
                zone = report_zone(self.store.policy.timezone)
                available = self.store.storage_status().get('detail_from')
                cutoff = timestamp(available) if available else None
                rows, scanned_bytes, bounded_reason = [], 0, None
                for run in reversed(list(self.runs.values())):
                    if time.monotonic() - now > self.store.policy.query_timeout_seconds:
                        bounded_reason = 'timeout'
                        break
                    scanned_bytes += len(json.dumps(run, ensure_ascii=False).encode('utf-8'))
                    if scanned_bytes > self.store.policy.query_max_bytes:
                        bounded_reason = 'scan_budget'
                        break
                    if profile_id and run.get('profile_id') != profile_id or task_id and run.get('task_id') != task_id:
                        continue
                    date_value = run.get('started_at') or run.get('updated_at')
                    if not date_value:
                        continue
                    moment = timestamp(date_value)
                    day = moment.astimezone(zone).date().isoformat()
                    if start_date and day < start_date or end_date and day > end_date:
                        continue
                    if run.get('terminal') and (not cutoff or moment < cutoff):
                        continue
                    rows.append({**run, 'details_available': bool(cutoff and moment >= cutoff)})
                rows.sort(key=lambda run: (run.get('started_at', run.get('updated_at', '')), run['run_id']), reverse=True)
                token, offset = str(uuid.uuid4()), 0
                snapshot = {'items': rows, 'filters': filters, 'at': now, 'generation': generation,
                            'bounded_reason': bounded_reason}
                self.run_pages[token] = snapshot
                while len(self.run_pages) > 8:
                    self.run_pages.popitem(last=False)
            items = copy.deepcopy(snapshot['items'][offset:offset + limit])
            next_cursor = f'{token}:{offset + limit}' if len(snapshot['items']) > offset + limit else None
            status = self.store.storage_status()
            if self.store.manifest['generation'] != generation:
                raise HistoryExpired('查询期间历史已清理，请刷新')
            return {'items': items, 'next_cursor': next_cursor, 'incomplete': bool(status.get('gaps')),
                    'gaps': status.get('gaps', []), 'earliest_available_at': status.get('detail_from'),
                    'bounded_reason': snapshot.get('bounded_reason'), 'recording_mode': self.store.policy.mode,
                    'history_pruned': bool(status.get('removed_ranges')), 'removed_ranges': status.get('removed_ranges', [])}

    def begin_control(self, data):
        with self.lock:
            request_id = data['request_id']
            try:
                uuid.UUID(request_id)
            except (ValueError, TypeError):
                raise ServiceError('invalid_request_id', 'request_id 必须是 UUID', 400)
            digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
            old = self.store.checkpoints.get('requests', request_id)
            if old:
                if old['digest'] != digest:
                    raise ServiceError('request_id_conflict', '同一 request_id 不能用于不同操作')
                return {**old['result'], 'duplicate': True}
            name = self.profile_name(data['profile_id'])
            profile_id = self.profile_id(name)
            action = data['action']
            state = self.profile_state.get(profile_id, {'state_version': 0, 'state': 'inactive'})
            if data.get('expected_state_version') is not None and data['expected_state_version'] != state['state_version']:
                raise ServiceError('state_conflict', '状态已变化，请刷新后重试')
            alive = self.executor_alive(profile_id)
            if action in ('start', 'restart'):
                if self.dispatch_blocked:
                    raise ServiceError('reconciliation_required', '存储恢复后须先核验状态', 503)
                for active_id in self.active_operations:
                    active = self.store.checkpoints.get('requests', active_id) or {}
                    if active.get('result', {}).get('profile_id') == profile_id:
                        raise ServiceError('control_in_progress', '该配置的控制操作尚未完成，请稍后再试')
            if action == 'start' and alive:
                raise ServiceError('already_running', '该配置已经运行，无需再次启动')
            if action in ('pause', 'resume') and not alive:
                raise ServiceError('executor_not_running', '执行器已退出，请重新运行')
            result = {'accepted': True, 'executed': False, 'persisted': False, 'status': 'accepted',
                      'request_id': request_id, 'profile_id': profile_id, 'name': name, 'action': action,
                      'source': data.get('source', {'type': 'local_api'})}
            try:
                self.emit({'type': 'control.requested', 'request_id': request_id, 'profile_id': profile_id,
                           'source': result['source'],
                           'payload': {'action': action, 'name': name, 'request_kind': 'requests',
                                       'request_digest': digest, 'expected_state_version': data.get('expected_state_version')}})
                result['persisted'] = True
                self.store.checkpoints.save('requests', request_id, {'digest': digest, 'result': result})
            except Exception:
                if action not in ('safe_stop', 'immediate_stop'):
                    raise ServiceError('storage_unavailable', '操作未执行：无法保存必要状态', 503)
            self.latest_control_request[profile_id] = request_id
            if alive or action not in ('safe_stop', 'immediate_stop'):
                self._set_state(profile_id, 'starting' if action in ('start', 'restart') else 'control_pending')
            return result

    def complete_control(self, receipt, executed, status, error=None):
        with self.lock:
            result = {**receipt, 'executed': executed, 'status': status}
            try:
                ack = self.emit({'type': 'control.completed', 'request_id': receipt['request_id'],
                           'source': receipt.get('source', {'type': 'local_api'}),
                           'profile_id': receipt['profile_id'], 'payload': {
                               'action': receipt['action'], 'executed': executed, 'status': status,
                               'error_category': error}})
                saved = self.store.checkpoints.get('requests', receipt['request_id'])
                if saved:
                    result['persisted'] = True
                    saved['result'] = result
                    saved.update(terminal=True, verified=True)
                    self.store.checkpoints.save('requests', receipt['request_id'], saved, event_seq=ack.get('seq'))
            except Exception:
                result.update(persisted=False, status='executed_not_saved' if executed else 'not_saved')
            if not executed and self.latest_control_request.get(receipt['profile_id']) == receipt['request_id']:
                self._set_state(receipt['profile_id'], 'warning')
            self.publish('control.completed', result)
            return result

    def request_control(self, profile_id, action):
        with self.lock:
            # The process can exit after begin_control but before this dispatch.
            # A stopped executor cannot consume a pause/resume or finish stopping.
            if not self.executor_alive(profile_id):
                if action in ('safe_stop', 'immediate_stop'):
                    return {'status': 'already_stopped', 'profile_id': profile_id, 'action': action}
                if action in ('pause', 'resume'):
                    raise ServiceError('executor_not_running', '执行器已退出，请重新运行')
            if action == 'immediate_stop':
                for owner in self.owners.values():
                    if owner['profile_id'] == profile_id and owner['alive']:
                        owner['stop_requested'] = True
            result = self.coordinator.request_control(profile_id, action)
            self._set_state(profile_id, {'pause': 'pausing', 'safe_stop': 'stopping',
                                        'resume': 'waiting'}.get(action, 'stopping'))
            return result

    def set_scheduler_policy(self, policy):
        with self.lock:
            from module.scheduling.core import Policy
            validated = asdict(Policy(**{**asdict(self.coordinator.policy), **policy}))
            intent = self.emit({'type': 'control.requested', 'payload': {'action': 'strategy.change', 'policy': validated}})
            self.store.checkpoints.save('settings', 'scheduler_policy', validated, event_seq=intent.get('seq'))
            self.coordinator.set_policy(validated)
            self.emit({'type': 'strategy.changed', 'payload': {'policy': validated, 'applies_at': 'safe_boundary'}})
            return {'policy': validated, 'applies_at': 'safe_boundary'}

    def close(self):
        with self.lock:
            self.closed = True
            self.store.close()

    def storage_status(self):
        result = self.store.storage_status()
        if self._external_usage is None or time.monotonic() - self._external_usage_at > 30:
            from module.server.solana_disk_usage import sample_existing_files
            self._external_usage = sample_existing_files(Path.cwd())
            self._external_usage_at = time.monotonic()
        return {**result, 'external_usage': self._external_usage}


_runtime = None
_runtime_lock = threading.Lock()


def get_runtime():
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            from module.observability import EventStore
            from module.server.solana_adapter import ManagerAdapter
            root = Path(os.environ.get('OAS_RUNTIME_DATA', str(Path.cwd() / 'runtime_data')))
            store = EventStore(root)
            try:
                service = RuntimeService(store, ManagerAdapter())
                # Restore rename identity before any profile enumeration can
                # assign a second ID to the moved configuration.
                from module.server.solana_legacy_audit import reconcile_legacy_operations
                reconcile_legacy_operations(service)
                service.pending_operations()
                _runtime = service
            except Exception:
                store.close()
                raise
        return _runtime


def close_runtime():
    global _runtime
    with _runtime_lock:
        if _runtime:
            _runtime.close()
            _runtime = None
