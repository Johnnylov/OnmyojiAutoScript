"""Automatically uncheck expired tasks, including stopped profiles.

The cache only stores task deadlines. File writes reuse the existing locked,
field-level CAS and audited config mutation, never a stale whole-file snapshot.
"""
import hashlib
import threading
import time
import uuid

from module.config.edit_lock import config_edit_lock
from module.scheduling.deadline import deadline_timestamp, is_expired


class DeadlineMonitor:
    def __init__(self, service, *, clock=time.time, interval=1.0):
        self.service, self.clock, self.interval = service, clock, interval
        self.cache = {}
        self.retry_after = {}
        self.stopping = threading.Event()
        self.thread = None

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self._run, name='oas-activity-deadlines', daemon=True)
            self.thread.start()

    def stop(self):
        self.stopping.set()

    def join(self, timeout=2):
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(timeout)

    def _run(self):
        while not self.stopping.is_set():
            try:
                self.scan_due()
            except Exception:
                # Enumeration can race an import/rename or temporary IO fault.
                # Keep watching; individual profile failures have a backoff.
                from module.logger import logger
                logger.exception('活动截止时间检查暂时失败，将自动重试')
                if self.stopping.wait(10):
                    break
            if self.stopping.wait(self.interval):
                break

    @staticmethod
    def _signature(path):
        info = path.stat()
        return info.st_mtime_ns, info.st_size

    def _due(self, name, now):
        adapter = self.service.adapter
        path = adapter._path(name)
        signature = self._signature(path)
        cached = self.cache.get(name)
        if cached is None or cached['signature'] != signature:
            # Read/stamp inside the same short lock so a concurrent replacement
            # cannot pair the old contents with the new file's timestamp.
            with config_edit_lock(path):
                data = adapter.raw(name)
                signature = self._signature(path)
            deadlines = {}
            for task, config in data.items():
                if not isinstance(config, dict):
                    continue
                schedule = config.get('scheduler', {})
                if schedule.get('enable'):
                    cutoff = deadline_timestamp(schedule.get('real_deadline'))
                    if cutoff is not None:
                        deadlines[task] = cutoff
            cached = {'signature': signature, 'deadlines': deadlines}
            self.cache[name] = cached
        return [task for task, cutoff in cached['deadlines'].items() if is_expired(cutoff, now)]

    def disable_if_expired(self, name, task):
        service, adapter = self.service, self.service.adapter
        with service.lock, config_edit_lock(adapter._path(name)):
            if service.closed or self.stopping.is_set():
                return None
            # Never act on the cache's deadline: a user may have extended it,
            # unchecked the cutoff, or disabled this task while we waited.
            schedule = adapter.raw(name).get(task, {}).get('scheduler', {})
            if not schedule.get('enable') or not is_expired(
                    deadline_timestamp(schedule.get('real_deadline')), self.clock()):
                return None
            revision = adapter.revision(name)
            profile_id = service.profile_id(name)
            # A user can explicitly re-enable the same expired configuration.
            # Its bytes may match an older intent, but it is a new activation.
            generation = self._signature(adapter._path(name))
            identity = hashlib.sha256(f'{profile_id}:{task}:{revision}:{generation}'.encode()).hexdigest()
            request_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'oas:deadline:disable:' + identity))
            result = adapter.save_value(service, {
                'profile_id': profile_id, 'task': task, 'group': 'scheduler', 'argument': 'enable',
                'types': 'boolean', 'value': False, 'expected_revision': revision,
                'request_id': request_id, 'client_id': 'backend-activity-deadline',
            }, source={'type': 'backend', 'component': 'activity_deadline'}, reason='deadline_expired')
            if not result.get('saved') or not result.get('persisted'):
                raise RuntimeError('活动到期停用结果需要核验')
            return result

    def scan_due(self):
        if self.service.closed or self.stopping.is_set():
            return []
        names = self.service.adapter.names()
        self.cache = {name: value for name, value in self.cache.items() if name in names}
        self.retry_after = {name: value for name, value in self.retry_after.items() if name in names}
        changed = []
        now = self.clock()
        for name in names:
            if time.monotonic() < self.retry_after.get(name, 0):
                continue
            try:
                for task in self._due(name, now):
                    result = self.disable_if_expired(name, task)
                    if result is not None:
                        changed.append((name, task))
                self.retry_after.pop(name, None)
            except Exception:
                self.retry_after[name] = time.monotonic() + 30
                from module.logger import logger
                logger.exception(f'配置 {name} 的活动到期停用暂时失败，将自动重试')
        return changed
