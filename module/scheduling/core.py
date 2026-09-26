"""Deterministic EEVDF-inspired scheduler for indivisible gameplay batches.

Wall-clock release/deadline fields and virtual deadlines have different units.
Callers supply clocks; this module never sleeps or operates a device.
"""
from dataclasses import asdict, dataclass, field
import math
from .deadline import is_expired


@dataclass
class Policy:
    mode: str = 'legacy'
    batch_seconds: float = 120.0
    max_wait_seconds: float = 900.0
    lag_limit_seconds: float = 120.0
    urgent_budget_seconds: float = 120.0
    urgent_window_seconds: float = 600.0
    recovery_limit: int = 3
    recovery_backoff_seconds: float = 30.0
    weights: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.mode == 'shadow':
            self.mode = 'eevdf_shadow'
        if self.mode not in ('legacy', 'eevdf_shadow', 'eevdf'):
            raise ValueError('Unknown scheduling mode')
        for name in ('batch_seconds', 'max_wait_seconds', 'lag_limit_seconds',
                     'urgent_window_seconds', 'recovery_backoff_seconds'):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f'{name} must be finite and positive')
        if not math.isfinite(self.urgent_budget_seconds) or self.urgent_budget_seconds < 0:
            raise ValueError('Invalid urgent budget')
        if not 1 <= self.recovery_limit <= 20:
            raise ValueError('Invalid recovery limit')
        for weight in self.weights.values():
            if not math.isfinite(float(weight)) or not 0.01 <= float(weight) <= 100:
                raise ValueError('Weights must be in [0.01, 100]')


@dataclass
class Candidate:
    key: str
    profile_id: str
    task: str
    weight: float = 1.0
    quantum: float = 120.0
    release_at: float = 0.0
    deadline: float | None = None
    enabled: bool = True
    blocked_reason: str | None = None
    recovery: bool = False
    cooperative: bool = False
    config_revision: str = ''
    ready_since: float | None = None

    def runnable(self, now):
        return (self.enabled and not self.blocked_reason and self.release_at <= now
                and not is_expired(self.deadline, now))


class FairScheduler:
    VERSION = 'solana-eevdf-2'

    def __init__(self, state=None):
        self.entities = {}
        self.anchor = 0.0
        self.overrides = []
        self.recoveries = {}
        if state:
            if state.get('schema_version') != 1:
                raise ValueError('Unsupported scheduler state schema')
            self.entities = state.get('entities', {})
            self.anchor = float(state.get('anchor', 0))
            self.overrides = state.get('overrides', [])
            self.recoveries = state.get('recoveries', {})
            # Ready time is process monotonic time, never carry across a reboot.
            for entity in self.entities.values():
                entity['active'] = False
                entity['ready_since'] = None

    def snapshot(self):
        return {'schema_version': 1, 'entities': self.entities, 'anchor': self.anchor,
                'overrides': self.overrides, 'recoveries': self.recoveries}

    def virtual_time(self):
        active = [e for e in self.entities.values() if e['active']]
        if active:
            return sum(e['weight'] * e['v'] for e in active) / sum(e['weight'] for e in active)
        return self.anchor

    @staticmethod
    def _weight(candidate, policy):
        # The settings API uses snake_case task IDs; workers use PascalCase.
        # Keep profile scope intact while matching both task spellings.
        def canonical(value):
            scope, separator, task = str(value).rpartition(':')
            return (scope if separator else '', task.replace('_', '').replace('-', '').casefold())

        for target in (candidate.key, candidate.task):
            if target in policy.weights:
                return float(policy.weights[target])
            wanted = canonical(target)
            for key, weight in policy.weights.items():
                if canonical(key) == wanted:
                    return float(weight)
        return float(candidate.weight)

    def sync(self, candidates, wall_now, mono_now, policy):
        reference = self.virtual_time()
        ready = {c.key: c for c in candidates if c.runnable(wall_now)}
        for key, entity in self.entities.items():
            if entity['active'] and key not in ready:
                entity['lag'] = max(-policy.lag_limit_seconds,
                                    min(policy.lag_limit_seconds, entity['weight'] * (reference - entity['v'])))
                entity['active'] = False
                entity['ready_since'] = None
        self.anchor = reference
        for key, candidate in ready.items():
            weight = self._weight(candidate, policy)
            if not math.isfinite(weight) or not 0.01 <= weight <= 100:
                raise ValueError('Invalid candidate weight')
            if not math.isfinite(candidate.quantum) or candidate.quantum <= 0:
                raise ValueError('Invalid batch estimate')
            entity = self.entities.get(key)
            if entity is None:
                entity = {'v': reference, 'weight': weight, 'active': True,
                          'lag': 0.0, 'ready_since': candidate.ready_since if candidate.ready_since is not None else mono_now,
                          'service': 0.0}
                self.entities[key] = entity
            else:
                if not entity['active']:
                    lag = max(-policy.lag_limit_seconds, min(policy.lag_limit_seconds, entity['lag']))
                    entity['v'] = reference - lag / weight
                    entity['ready_since'] = candidate.ready_since if candidate.ready_since is not None else mono_now
                elif entity['weight'] != weight:
                    # Preserve service debt in physical seconds when changing weights.
                    lag = entity['weight'] * (reference - entity['v'])
                    entity['v'] = reference - lag / weight
                entity.update(active=True, weight=weight)
                if candidate.ready_since is not None:
                    entity['ready_since'] = max(entity['ready_since'], candidate.ready_since)
            if entity.get('config_revision') != candidate.config_revision:
                entity['estimate_seconds'] = None
            entity['config_revision'] = candidate.config_revision
        return ready

    def choose(self, candidates, wall_now, mono_now, policy):
        ready = self.sync(candidates, wall_now, mono_now, policy)
        if not ready:
            return None
        reference = self.virtual_time()
        candidates_by_key = ready
        eligible = [key for key in ready if self.entities[key]['v'] <= reference + 1e-9]
        if not eligible:
            eligible = [min(ready, key=lambda key: self.entities[key]['v'])]

        def deadline(key):
            e = self.entities[key]
            return e['v'] + candidates_by_key[key].quantum / e['weight']

        selected = min(eligible, key=lambda key: (deadline(key), self.entities[key]['ready_since'], key))
        reason = 'fair_share'
        self.overrides = [item for item in self.overrides if item['at'] > wall_now - policy.urgent_window_seconds]
        budget_left = policy.urgent_budget_seconds - sum(item['seconds'] for item in self.overrides)
        urgent = []
        blocked = {}
        for key, candidate in ready.items():
            rec = self.recoveries.get(key, {'count': 0, 'next_at': 0})
            if candidate.recovery:
                if policy.urgent_budget_seconds == 0:
                    blocked[key] = 'recovery_budget_disabled'
                    continue
                if rec['count'] >= policy.recovery_limit:
                    blocked[key] = 'recovery_budget_exhausted'
                    continue
                if rec['next_at'] > wall_now:
                    blocked[key] = 'recovery_backoff'
                    continue
            deadline_risk = candidate.deadline is not None and wall_now + candidate.quantum >= candidate.deadline
            if candidate.recovery or deadline_risk:
                # An indivisible recovery may exceed the entire window budget.
                # Admit it once in an unused window and charge its actual time;
                # otherwise its estimate can block this device permanently.
                oversized_recovery = (candidate.recovery and not self.overrides
                    and policy.urgent_budget_seconds > 0
                    and candidate.quantum > policy.urgent_budget_seconds)
                if candidate.quantum <= budget_left or oversized_recovery:
                    urgent.append((0 if candidate.recovery else 1,
                                   candidate.deadline if candidate.deadline is not None else float('inf'), key))
                else:
                    blocked[key] = 'urgent_budget_exhausted'
        # Exhausted recovery must not fall through to ordinary fair selection.
        forbidden = {key for key, why in blocked.items()
                     if ready[key].recovery and why in ('recovery_budget_exhausted', 'recovery_budget_disabled',
                                                       'recovery_backoff', 'urgent_budget_exhausted')}
        allowed = [key for key in ready if key not in forbidden]
        if not allowed:
            return {'key': None, 'reason': 'recovery_blocked', 'blocked': blocked, 'version': self.VERSION}
        if selected in forbidden:
            selected = min(allowed, key=lambda key: (deadline(key), key))
        starved = [key for key in allowed
                   if mono_now - self.entities[key]['ready_since'] >= policy.max_wait_seconds]
        if starved:
            selected = min(starved, key=lambda key: (self.entities[key]['ready_since'], key))
            reason = 'waiting_limit'
        elif urgent:
            selected = min(urgent)[2]
            reason = 'recovery' if ready[selected].recovery else 'real_deadline'
        if ready[selected].recovery:
            # Waiting-limit protection must not bypass retry count/backoff or
            # leave recovery service uncharged to the urgent window.
            reason = 'recovery'
        e = self.entities[selected]
        return {'key': selected, 'reason': reason, 'version': self.VERSION,
                'virtual_deadline': deadline(selected), 'lag': e['weight'] * (reference - e['v']),
                'estimated_seconds': ready[selected].quantum,
                'eligible_count': len(eligible), 'blocked': blocked,
                'overdue': ready[selected].deadline is not None and wall_now > ready[selected].deadline}

    def account(self, key, seconds, wall_now, mono_now, reason='fair_share', failed=False, policy=None):
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError('Device time must be finite and nonnegative')
        policy = policy or Policy()
        entity = self.entities[key]
        entity['v'] += seconds / entity['weight']
        entity['service'] += seconds
        if seconds > 0:
            previous = entity.get('estimate_seconds')
            entity['estimate_seconds'] = seconds if previous is None else .75 * previous + .25 * seconds
        entity['ready_since'] = mono_now
        self.anchor = self.virtual_time()
        # Persist bounded debt also for active entities, so restart cannot reset it.
        for item in self.entities.values():
            if item['active']:
                item['lag'] = max(-policy.lag_limit_seconds,
                                  min(policy.lag_limit_seconds, item['weight'] * (self.anchor - item['v'])))
        if reason in ('recovery', 'real_deadline'):
            self.overrides.append({'at': wall_now, 'seconds': seconds})
        if reason == 'recovery':
            rec = self.recoveries.setdefault(key, {'count': 0, 'next_at': 0})
            rec['count'] = rec['count'] + 1 if failed else 0
            rec['next_at'] = wall_now + min(1800, policy.recovery_backoff_seconds * 2 ** max(0, rec['count'] - 1)) if failed else 0
