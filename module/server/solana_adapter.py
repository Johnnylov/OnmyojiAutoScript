"""Adapter for existing OAS profiles; imports game-facing modules lazily."""
from datetime import datetime, timedelta
import hashlib
import json
import re

from module.server.solana_runtime import ServiceError
from module.scheduling.deadline import deadline_timestamp, is_expired


class ManagerAdapter:
    @property
    def manager(self):
        from module.server.main_manager import mm
        return mm

    def names(self):
        return self.manager.all_script_files()

    def _path(self, name):
        from module.server.config_manager import ConfigManager
        return ConfigManager.config_path(ConfigManager.validate_config_name(name, allow_template=False))

    def raw(self, name):
        return json.loads(self._path(name).read_text(encoding='utf-8'))

    def revision(self, name):
        # Exact file content catches edits by old clients and running task writers.
        return hashlib.sha256(self._path(name).read_bytes()).hexdigest()

    def device(self, name):
        return self.raw(name).get('script', {}).get('device', {})

    def args(self, name, task):
        return {'args': self.manager.config_cache(name).model.script_task(task), 'revision': self.revision(name)}

    def planned_tasks(self, name, now=None):
        """Freeze the enabled, due tasks at executor launch; later dispatches join."""
        from module.scheduling.preflight import business_preflight
        now = now or datetime.now()
        tasks = set()
        for key, config in self.raw(name).items():
            if not isinstance(config, dict):
                continue
            schedule = config.get('scheduler', {})
            command = schedule.get('command') or ''.join(word.title() for word in key.split('_'))
            # The scheduler deliberately skips its first Restart at startup.
            if not schedule.get('enable') or command == 'Restart':
                continue
            try:
                due = datetime.fromisoformat(schedule['next_run']).replace(tzinfo=None) <= now
            except (KeyError, TypeError, ValueError):
                continue
            if (due and not is_expired(deadline_timestamp(schedule.get('real_deadline')), now.timestamp())
                    and business_preflight(command, now) is None):
                tasks.add(command)
        return sorted(tasks)

    def queues(self, service):
        ready, waiting = [], []
        now = datetime.now()
        active = {(run.get('profile_id'), run.get('task_id')) for run in service.runs.values()
                  if run.get('state') == 'running'}
        for name in self.names():
            profile_id = service.profile_id(name)
            running = any(owner['profile_id'] == profile_id and owner['alive'] for owner in service.owners.values())
            for task_id, config in self.raw(name).items():
                if not isinstance(config, dict) or 'scheduler' not in config:
                    continue
                schedule = config['scheduler']
                if not schedule.get('enable', False):
                    continue
                next_run = schedule.get('next_run')
                try:
                    due = datetime.fromisoformat(next_run).replace(tzinfo=None) <= now
                except (TypeError, ValueError):
                    due = False
                state = service.profile_state.get(profile_id, {}).get('state')
                expired = is_expired(deadline_timestamp(schedule.get('real_deadline')), now.timestamp())
                reason = ('deadline_expired' if expired else 'profile_inactive' if not running else 'paused' if state in ('paused', 'pausing')
                          else 'scheduled' if not due else 'waiting_device')
                public_task = schedule.get('command') or task_id
                if (profile_id, public_task) in active:
                    continue
                item = {'profile_id': profile_id, 'profile_name': name, 'task_id': public_task,
                        'config_key': task_id, 'task': public_task, 'next_run': next_run,
                        'reason': reason, 'estimated_seconds': None,
                        'cooperative': task_id == 'orochi' and config.get('orochi_config', {}).get('user_status') == 'alone'}
                (ready if running and due and not expired and state not in ('paused', 'pausing') else waiting).append(item)
        return {'ready': ready, 'waiting': waiting}

    async def control(self, service, receipt):
        name, action = receipt['name'], receipt['action']
        process = self.manager.script_process.get(name)
        if action == 'restart':
            # A user-requested restart starts a fresh run only after confirmed
            # old-process exit. Never release a live owner's device fence.
            if process is not None:
                await process.stop()
            service.prepare_restart(receipt)
        if action in ('start', 'restart'):
            if process is None:
                from module.server.script_process import ScriptProcess
                process = ScriptProcess(name)
                self.manager.script_process[name] = process
            await process.start()
            return 'restarted' if action == 'restart' else 'started'
        result = service.request_control(receipt['profile_id'], action)
        if result.get('status') == 'already_stopped':
            return 'already_stopped'
        if action == 'immediate_stop':
            if process is not None:
                await process.stop()
            service.finish_immediate_stop(receipt)
            return 'stopped'
        return 'waiting_safe_boundary' if action in ('pause', 'safe_stop') else 'resumed'

    @staticmethod
    def coerce(types, value):
        if types == 'integer':
            return int(value)
        if types == 'number':
            return float(value)
        if types == 'boolean':
            if isinstance(value, bool):
                return value
            if str(value).lower() not in ('true', 'false', '1', '0'):
                raise ValueError('Invalid boolean')
            return str(value).lower() in ('true', '1')
        if types in ('date_time', 'next_run'):
            return datetime.fromisoformat(str(value))
        if types == 'time':
            return datetime.strptime(str(value), '%H:%M:%S').time()
        if types == 'time_delta':
            match = re.fullmatch(r'(\d+)\s+(\d{1,2}):(\d{1,2}):(\d{1,2})', str(value))
            if not match:
                raise ValueError('Expected days HH:MM:SS')
            days, hours, minutes, seconds = map(int, match.groups())
            if hours >= 24 or minutes >= 60 or seconds >= 60:
                raise ValueError('Invalid interval clock')
            return timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
        return value

    def save_value(self, service, data, *, source=None, reason=None):
        from module.config.utils import convert_to_underscore
        from module.server.config_manager import ConfigManager
        from module.config.edit_lock import config_edit_lock, ConfigConflict
        name = service.profile_name(data['profile_id'])
        with service.lock, config_edit_lock(self._path(name)):
            request_id = data['request_id']
            source = source or {'type': 'client', 'client_id': str(data.get('client_id') or 'unspecified')[:128]}
            digest = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
            previous_request = service.store.checkpoints.get('config_requests', request_id)
            if previous_request:
                if previous_request['digest'] != digest:
                    raise ServiceError('request_conflict', '请求标识已用于另一项修改')
                if previous_request.get('terminal') and previous_request.get('result'):
                    result = previous_request['result']
                    if result.get('error_code'):
                        raise ServiceError(result['error_code'], result['message'], result['http_status'])
                    return {**result, 'duplicate': True}
                # A config file, request checkpoint and MysteryShop manual
                # marker cannot be one transaction. Never blindly repeat an
                # intent whose completion was not durably acknowledged.
                raise ServiceError('reconciliation_required', '上次修改结果尚未核验，请重新加载配置并核验后使用新请求')
            revision = self.revision(name)
            if data.get('expected_revision') != revision:
                raise ServiceError('revision_conflict', '配置已被修改，请重新加载后保存')
            task, group, argument = [convert_to_underscore(data[k]) for k in ('task', 'group', 'argument')]
            try:
                value = self.coerce(data.get('types'), data['value'])
                if isinstance(value, datetime) and value.tzinfo is not None:
                    raise ValueError('Configuration dates are local naive datetimes')
                if data.get('types') == 'integer' and isinstance(data['value'], float) and data['value'] != value:
                    raise ValueError('Fractional integer')
            except (ValueError, TypeError, OverflowError):
                raise ServiceError('invalid_field', '字段格式不正确', 422)
            before_raw = self.raw(name)
            before = ConfigManager.redact_config(before_raw)
            intent = service.emit({'type': 'control.requested', 'request_id': request_id,
                          'source': source,
                          'profile_id': service.profile_id(name), 'config_revision': revision,
                          'payload': {'action': 'config.change', 'path': f'{task}.{group}.{argument}',
                                      **({'reason': reason, 'task_id': task} if reason else {}),
                                      'name': name, 'request_kind': 'config_requests', 'request_digest': digest}})
            record = {'digest': digest, 'previous_revision': revision, 'terminal': False,
                      'verified': False, 'path': f'{task}.{group}.{argument}',
                      'profile_id': service.profile_id(name), 'name': name, 'action': 'config.change'}
            service.store.checkpoints.save('config_requests', request_id, record, event_seq=intent['seq'])

            def failed(code, message, status):
                result = {'saved': False, 'persisted': True, 'error_code': code,
                          'message': message, 'http_status': status}
                ack = service.emit({'type': 'control.completed', 'request_id': request_id,
                    'source': source,
                    'profile_id': service.profile_id(name),
                    'payload': {'action': 'config.change', 'executed': False, 'error_category': code}})
                service.store.checkpoints.save('config_requests', request_id,
                    {**record, 'terminal': True, 'verified': True, 'result': result}, event_seq=ack['seq'])
                raise ServiceError(code, message, status)

            config = self.manager.config_cache(name)
            try:
                saved = config.model.script_set_arg(task, group, argument, value, raise_conflicts=True)
            except ConfigConflict:
                failed('revision_conflict', '该字段已被其他写入器修改，请重新加载', 409)
            except OSError:
                # An OS error can occur after replacement. Preserve the intent
                # for revision-based reconciliation rather than claiming no edit.
                return {'saved': self.revision(name) != revision, 'persisted': False,
                        'revision': self.revision(name), 'status': 'needs_reconciliation',
                        'applies_to': 'next_run', 'needs_reconciliation': True}
            if not saved:
                failed('invalid_field', '字段校验失败，配置未保存', 422)
            manual_error = False
            if data.get('types') == 'next_run' and task == 'mystery_shop' and group == 'scheduler' and argument == 'next_run' and value <= datetime.now():
                try:
                    from tasks.MysteryShop.schedule import MysteryShopSchedule
                    MysteryShopSchedule(name).request_manual_run(datetime.now())
                except OSError:
                    manual_error = True
            after_raw = self.raw(name)
            after = ConfigManager.redact_config(after_raw)
            new_revision = self.revision(name)
            changes = []
            def diff(old, new, safe_old, safe_new, path=''):
                if all(isinstance(item, dict) for item in (old, new, safe_old, safe_new)):
                    for key in sorted(set(old) | set(new)):
                        diff(old.get(key), new.get(key), safe_old.get(key), safe_new.get(key), f'{path}.{key}' if path else key)
                elif old != new:
                    redacted = safe_old != old or safe_new != new
                    changes.append({'path': path, 'old': '[changed]' if redacted else safe_old,
                                    'new': '[changed]' if redacted else safe_new})
            diff(before_raw, after_raw, before, after)
            if not changes:
                changes.append({'path': f'{task}.{group}.{argument}', 'change': 'unchanged'})
            try:
                service.emit({'type': 'config.changed', 'request_id': request_id,
                              'source': source,
                              'profile_id': service.profile_id(name), 'config_revision': new_revision,
                              'payload': {'changes': changes, 'previous_revision': revision, 'applies_to': 'next_run',
                                          **({'reason': reason, 'task_id': task} if reason else {})}})
                completion = service.emit({'type': 'control.completed', 'request_id': request_id,
                    'source': source,
                    'profile_id': service.profile_id(name), 'config_revision': new_revision,
                    'payload': {'action': 'config.change', 'executed': not manual_error,
                                'config_saved': True, 'error_category': 'manual_request_failed' if manual_error else None}})
                result = {'saved': True, 'persisted': True, 'revision': new_revision, 'applies_to': 'next_run'}
                if manual_error:
                    result.update(status='partially_executed', needs_reconciliation=True,
                                  error_category='manual_request_failed')
                service.store.checkpoints.save('config_requests', request_id,
                    {**record, 'terminal': not manual_error, 'verified': not manual_error, 'result': result},
                    event_seq=completion['seq'])
            except Exception:
                return {'saved': True, 'persisted': False, 'revision': new_revision,
                        'status': 'executed_not_saved', 'applies_to': 'next_run'}
            return result
