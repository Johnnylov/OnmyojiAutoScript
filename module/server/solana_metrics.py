"""Bounded execution-session progress, projected from real lifecycle events."""
import copy
from datetime import datetime


METRIC_FIELDS = {
    'current_count', 'target_count', 'remaining_target', 'count_unit',
    'progress_phase', 'count_supported', 'battle_count', 'battle_supported',
    'battle_scope', 'observed_at', 'target_unbounded',
    'count_unavailable_reason', 'battle_unavailable_reason', 'target_seconds', 'metric_revision',
}


def observation_is_current(candidate, previous):
    """A delayed screenshot must not overwrite a newer durable observation."""
    old_revision, new_revision = previous.get('metric_revision'), candidate.get('metric_revision')
    if isinstance(old_revision, int) and isinstance(new_revision, int):
        if new_revision != old_revision:
            return new_revision > old_revision
        # Equal revisions carry the same counters; elapsed time is monotonic
        # even when the machine's wall clock moves backwards.
        old_seconds, new_seconds = previous.get('execution_seconds'), candidate.get('execution_seconds')
        if isinstance(old_seconds, (int, float)) and isinstance(new_seconds, (int, float)):
            return new_seconds >= old_seconds
    old, new = previous.get('observed_at'), candidate.get('observed_at')
    if not old:
        return True
    if not new:
        return False
    try:
        return datetime.fromisoformat(new.replace('Z', '+00:00')) >= datetime.fromisoformat(old.replace('Z', '+00:00'))
    except (ValueError, TypeError, AttributeError):
        return False


class ExecutionMetricsMixin:
    def _load_execution_cycles(self):
        self.execution_cycles = {}
        for document in self.store.checkpoints.list('execution_cycles'):
            cycle = document.get('data', {})
            if cycle.get('profile_id') and cycle.get('cycle_id'):
                self.execution_cycles[cycle['profile_id']] = cycle

    def _start_execution_cycle(self, name, profile_id, owner_id):
        plan = self.adapter.planned_tasks(name) if self.adapter and hasattr(self.adapter, 'planned_tasks') else []
        self.emit({'type': 'execution.cycle_started', 'profile_id': profile_id,
                   'execution_cycle_id': owner_id,
                   'payload': {'task_ids': sorted(set(plan)), 'scope': 'executor_session'}})

    def _reduce_execution_cycle(self, event):
        kind = event['type']
        if kind not in {'execution.cycle_started', 'execution.cycle_finished',
                        'run.created', 'run.started', 'run.resumed', 'run.finished', 'scheduler.skipped'}:
            return
        profile_id = event.get('profile_id')
        cycle_id = event.get('execution_cycle_id')
        if not cycle_id and event.get('run_id'):
            cycle_id = self.runs.get(event['run_id'], {}).get('execution_cycle_id')
        if not profile_id or not cycle_id:
            return
        old = self.execution_cycles.get(profile_id)
        seq = event.get('seq', 0)
        if old and seq and seq <= old.get('last_event_seq', 0):
            return
        payload = event.get('payload', {})
        if kind == 'execution.cycle_started':
            cycle = {'profile_id': profile_id, 'cycle_id': cycle_id,
                     'scope': 'executor_session', 'started_at': event['occurred_at'],
                     'state': 'active', 'task_ids': sorted(set(payload.get('task_ids', []))),
                     'completed_task_ids': [], 'failed_task_ids': [], 'deferred_task_ids': []}
        else:
            if not old or old['cycle_id'] != cycle_id:
                return
            cycle = copy.deepcopy(old)
            planned = set(cycle['task_ids'])
            completed = set(cycle['completed_task_ids'])
            failed = set(cycle['failed_task_ids'])
            deferred = set(cycle['deferred_task_ids'])
            task = event.get('task_id') or event.get('task')
            if kind == 'execution.cycle_finished':
                cycle.update(state=payload.get('state', 'stopped'), finished_at=event['occurred_at'])
            elif task:
                if kind == 'scheduler.skipped':
                    # A known clock prerequisite postpones work to a future plan;
                    # it is never counted as a completed task in this session.
                    if task not in completed:
                        planned.discard(task)
                        failed.discard(task)
                        deferred.add(task)
                else:
                    planned.add(task)
                    deferred.discard(task)
                    if kind == 'run.finished':
                        if payload.get('outcome') == 'succeeded':
                            completed.add(task)
                            failed.discard(task)
                        elif (payload.get('outcome') in {'failed', 'crashed', 'interrupted', 'cancelled'}
                              and task not in completed):
                            failed.add(task)
                cycle.update(task_ids=sorted(planned), completed_task_ids=sorted(completed),
                             failed_task_ids=sorted(failed), deferred_task_ids=sorted(deferred))
        cycle.update(completed_tasks=len(cycle['completed_task_ids']), total_tasks=len(cycle['task_ids']),
                     failed_tasks=len(cycle['failed_task_ids']), deferred_tasks=len(cycle['deferred_task_ids']),
                     updated_at=event['occurred_at'], last_event_seq=seq)
        # One document per profile; retry/run counts never create an unbounded list.
        self.store.checkpoints.save('execution_cycles', profile_id, cycle, event_seq=seq)
        self.execution_cycles[profile_id] = cycle
