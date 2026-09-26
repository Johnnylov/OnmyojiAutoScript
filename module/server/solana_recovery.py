"""Explicit resolution of uncertain work; never replays a game side effect."""
from contextlib import contextmanager
import uuid

from module.scheduling.fence import DeviceProcessLock

TERMINAL = {'succeeded', 'failed', 'cancelled', 'interrupted', 'crashed'}


class RecoveryMixin:
    def finish_immediate_stop(self, receipt):
        """A manual stop closes this executor's unfinished work, never a win.

        Called only after join: no timer, heartbeat or button state is proof
        that the old worker has stopped sending device actions.
        """
        from module.server.solana_runtime import ServiceError
        with self.lock:
            profile_id = receipt['profile_id']
            if self.executor_alive(profile_id):
                raise ServiceError('executor_still_running', '执行器尚未退出，请再次停止')
            if self.latest_control_request.get(profile_id) != receipt['request_id']:
                raise ServiceError('control_superseded', '已有更新的控制操作，请查看当前状态')
            for run in list(self.runs.values()):
                if run.get('profile_id') != profile_id or run['state'] in TERMINAL:
                    continue
                self.emit({'type': 'run.finished',
                    'event_id': str(uuid.uuid5(uuid.UUID(receipt['request_id']), 'stop:' + run['run_id'])),
                    'run_id': run['run_id'], 'profile_id': profile_id, 'task_id': run.get('task_id'),
                    'device_id': run.get('device_id'), 'request_id': receipt['request_id'],
                    'payload': {'outcome': 'interrupted', 'reason': 'user_immediate_stop',
                                'execution_seconds': None, 'duration_incomplete': True,
                                'game_outcome': 'unknown', 'automatic_replay': False}})
                current = self.runs[run['run_id']]
                for document in self.store.checkpoints.list('runs'):
                    checkpoint = document['data']
                    if checkpoint.get('run_id') != run['run_id']:
                        continue
                    checkpoint.update(state='interrupted', terminal=True, verified=True,
                                      in_flight=False, resolution='closed_by_manual_stop')
                    self.store.checkpoints.save('runs', document['key'], checkpoint,
                                                event_seq=current['last_event_seq'])
            self._set_state(profile_id, 'inactive')

    def prepare_restart(self, receipt):
        """Close old work as interrupted for an explicit fresh-run request.

        This is not recovery of unverified game progress: the user requested a
        new executor. Never mark old work successful or reuse an in-flight step.
        """
        from module.server.solana_runtime import ServiceError
        with self.lock:
            profile_id = receipt['profile_id']
            if self.latest_control_request.get(profile_id) != receipt['request_id']:
                raise ServiceError('control_superseded', '已有更新的控制操作，本次重启已取消')
            if self.executor_alive(profile_id):
                raise ServiceError('executor_still_running', '原执行器尚未退出，未启动新进程')
            if self.dispatch_blocked:
                raise ServiceError('reconciliation_required', '存储状态需要核验，未启动新进程', 503)
            pending = [run for run in self.runs.values()
                       if run.get('profile_id') == profile_id and run['state'] not in TERMINAL]
            with self._recovery_device_locks([run.get('device_id') for run in pending]):
                for run in pending:
                    self.emit({'type': 'run.finished',
                        'event_id': str(uuid.uuid5(uuid.UUID(receipt['request_id']), 'restart:' + run['run_id'])),
                        'run_id': run['run_id'], 'profile_id': profile_id, 'task_id': run.get('task_id'),
                        'device_id': run.get('device_id'), 'request_id': receipt['request_id'],
                        'payload': {'outcome': 'interrupted', 'reason': 'user_restart',
                                    'execution_seconds': None, 'duration_incomplete': True,
                                    'game_outcome': 'unknown', 'automatic_replay': False}})
                    current = self.runs[run['run_id']]
                    for document in self.store.checkpoints.list('runs'):
                        checkpoint = document['data']
                        if checkpoint.get('run_id') != run['run_id']:
                            continue
                        checkpoint.update(state='interrupted', terminal=True, verified=True,
                                          in_flight=False, resolution='closed_for_fresh_restart')
                        self.store.checkpoints.save('runs', document['key'], checkpoint,
                                                    event_seq=current['last_event_seq'])
                self._set_state(profile_id, 'starting')

    def pending_operations(self):
        from module.server.solana_legacy_audit import list_pending_operations
        with self.lock:
            items = list_pending_operations(self)
            return {'items': items, 'pending_count': len(items),
                    'dispatch_blocked': self.dispatch_blocked}

    def resolve_operation(self, data):
        from module.server.solana_legacy_audit import resolve_pending_operation
        from module.server.solana_runtime import ServiceError
        with self.lock:
            if any(owner['alive'] for owner in self.owners.values()):
                raise ServiceError('executors_running', '请先停止所有配置，再核验未完成操作')
            return resolve_pending_operation(self, **data)

    @contextmanager
    def _recovery_device_locks(self, device_ids):
        from module.server.solana_runtime import ServiceError
        locks = []
        try:
            for device_id in sorted(set(value for value in device_ids if value)):
                guard = DeviceProcessLock(device_id)
                if not guard.acquire():
                    raise ServiceError('device_still_owned', '旧执行器仍持有设备，请先停止并等待退出')
                locks.append(guard)
            yield
        finally:
            for guard in reversed(locks):
                guard.release()

    def recovery_status(self, profile_id=None):
        with self.lock:
            items = []
            for run in self.runs.values():
                if run['state'] != 'needs_reconciliation' or profile_id and run.get('profile_id') != profile_id:
                    continue
                alive = any(owner['alive'] and owner['profile_id'] == run.get('profile_id')
                            for owner in self.owners.values())
                items.append({**run, 'executor_alive': alive, 'manual_verification_required': True,
                    'resolution': 'close_interrupted',
                    'explanation': '上次操作结果无法确认。检查游戏实际进度后可结束这次运行；不会标为成功或自动补跑。'})
            return {'items': items, 'dispatch_blocked': self.dispatch_blocked,
                    'storage': self.store.storage_status(),
                    'can_check_storage': not any(owner['alive'] for owner in self.owners.values())}

    def resolve_run(self, data):
        from module.server.solana_runtime import ServiceError
        if data.get('resolution') != 'close_interrupted' or data.get('game_state_reviewed') is not True:
            raise ServiceError('review_required', '请先检查游戏进度，再明确结束未确认运行', 422)
        with self.lock:
            name = self.profile_name(data['profile_id'])
            profile_id = self.profile_id(name)
            run = self.runs.get(data['run_id'])
            if not run or run.get('profile_id') != profile_id:
                raise ServiceError('run_not_found', '运行记录不存在', 404)
            if any(owner['alive'] and owner['profile_id'] == profile_id for owner in self.owners.values()):
                raise ServiceError('executor_still_running', '先停止该配置并等待执行器退出')
            if run['state'] not in TERMINAL | {'needs_reconciliation'}:
                raise ServiceError('run_not_uncertain', '该运行处于可继续状态，请使用正常暂停或停止操作')
            with self._recovery_device_locks([run.get('device_id')]):
                receipt = self.begin_control({**data, 'profile_id': profile_id, 'action': 'resolve_run'})
                if receipt.get('duplicate') and receipt.get('executed'):
                    return receipt
                if run['state'] not in TERMINAL:
                    self.emit({'type': 'run.finished', 'event_id': str(uuid.uuid5(uuid.UUID(data['request_id']), 'finish')),
                        'run_id': run['run_id'], 'profile_id': profile_id, 'task_id': run.get('task_id'),
                        'device_id': run.get('device_id'), 'request_id': data['request_id'],
                        'payload': {'outcome': 'interrupted', 'reason': 'user_closed_uncertain_run',
                            'execution_seconds': None, 'duration_incomplete': True,
                            'game_outcome': 'unknown', 'automatic_replay': False}})
                # Synchronize the worker's per-task continuation pointer only
                # after the logical terminal outcome is durably recorded.
                current = self.runs[run['run_id']]
                for document in self.store.checkpoints.list('runs'):
                    checkpoint = document['data']
                    if checkpoint.get('run_id') != run['run_id']:
                        continue
                    checkpoint.update(state=current['state'], terminal=True, verified=True,
                                      in_flight=False, resolution='closed_without_replay')
                    self.store.checkpoints.save('runs', document['key'], checkpoint,
                                                event_seq=current['last_event_seq'])
                self.emit({'type': 'recovery.resolved', 'profile_id': profile_id, 'run_id': run['run_id'],
                           'request_id': data['request_id'], 'payload': {'resolution': 'close_interrupted',
                            'automatic_replay': False, 'game_outcome': 'unknown'}})
                result = self.complete_control(receipt, True, 'resolved')
                self._set_state(profile_id, 'inactive')
                return result

    def reconcile_storage(self):
        """Check ownership and durable state before removing the dispatch fence."""
        from module.server.solana_runtime import ServiceError
        with self.lock:
            if any(owner['alive'] for owner in self.owners.values()):
                raise ServiceError('executors_running', '请先安全停止所有配置，再检查记录恢复')
            for owner_id, owner in list(self.owners.items()):
                if owner.get('confirmed_dead') and not owner.get('cleanup_complete'):
                    self.process_exited(owner_id, owner['exit_code'])
            devices = self.coordinator.snapshot()
            if any(value.get('lease') for value in devices.values()):
                raise ServiceError('device_still_owned', '设备控制权尚未释放')
            device_ids = list(devices) + [run.get('device_id') for run in self.runs.values()]
            with self._recovery_device_locks(device_ids):
                if not self._recover_reducer(mark_orphans=True):
                    raise ServiceError('recovery_scan_limit', '恢复扫描未完成，记录仍受保护，不能恢复派发', 503)
                from module.server.solana_legacy_audit import reconcile_legacy_operations
                reconcile_legacy_operations(self)
                if self.pending_operations()['pending_count']:
                    self.dispatch_blocked = True
                    raise ServiceError('unresolved_operations', '仍有未核验操作，请先检查并确认当前配置')
                if self.store.storage_status().get('pending_requests'):
                    self.dispatch_blocked = True
                    raise ServiceError('unresolved_operation_journal', '仍有未闭合的操作日志，请保留记录并检查恢复列表')
                if any(run['state'] not in TERMINAL | {'paused', 'yielded'} for run in self.runs.values()):
                    raise ServiceError('unresolved_runs', '仍有未确认运行，请先核验并结束这些运行')
                for document in self.store.checkpoints.list('runs'):
                    run = document['data']
                    if run.get('state') in TERMINAL:
                        continue
                    if run.get('in_flight') or run.get('state') not in ('paused', 'yielded') or run.get('safe_boundary') != 'page_main':
                        raise ServiceError('checkpoint_uncertain', '执行检查点仍未核验，不能恢复派发')
                # Persist the evidence first. acknowledge_recovery writes its
                # own durable latch; only then may new workers be started.
                self.emit({'type': 'recovery.resolved', 'payload': {
                    'scope': 'storage', 'checks': ['no_live_executor', 'device_os_locks_free',
                                                  'runtime_replayed', 'operations_reconciled', 'checkpoints_consistent'],
                    'automatic_restart': False}})
                self.store.acknowledge_recovery()
                self.dispatch_blocked = False
                self.publish('storage.recovered', {'dispatch_blocked': False, 'automatic_restart': False})
                return {'verified': True, 'dispatch_blocked': False, 'automatic_restart': False,
                        'storage': self.store.storage_status()}
