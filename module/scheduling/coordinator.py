"""Single-parent device arbiter. A timeout never transfers a live lease."""
from dataclasses import asdict
import copy
import hashlib
import re
import threading
import time
import uuid

from .core import Candidate, FairScheduler, Policy


class LeaseLost(RuntimeError):
    pass


def normalize_device_id(device=None, aliases=None):
    """Known local ADB aliases coalesce; unresolved auto shares one fail-safe lane.

    A stable explicitly assigned device_id/alias is required when endpoints change.
    We do not claim arbitrary IPs identify a physical emulator without a mapping.
    """
    aliases = aliases or {}
    device = device or {}
    if isinstance(device, str):
        device = {'serial': device}
    explicit = str(device.get('device_id', '')).strip()
    if explicit:
        return explicit
    serial = str(device.get('serial', 'auto')).strip().lower()
    if serial in aliases:
        return aliases[serial]
    path = str(device.get('emulatorinfo_path', '')).replace('\\', '/').strip().lower()
    name = str(device.get('emulatorinfo_name', '')).strip().lower()
    local = re.fullmatch(r'(?:localhost|127\.0\.0\.1|\[::1\]):(\d+)', serial)
    emulator = re.fullmatch(r'emulator-(\d+)', serial)
    if local:
        return 'adb:local:' + local.group(1)
    if emulator:
        return 'adb:local:' + str(int(emulator.group(1)) + 1)
    if serial in ('', 'auto'):
        if path and name:
            return 'emulator:' + hashlib.sha256((path + '|' + name).encode()).hexdigest()[:24]
        return 'unresolved:auto'
    return 'adb:' + serial


class Coordinator:
    def __init__(self, checkpoint_store=None, event_sink=None, load=None, save=None,
                 clock=time.time, monotonic=time.monotonic):
        self.clock, self.monotonic = clock, monotonic
        self.checkpoint_store, self.event_sink = checkpoint_store, event_sink
        if checkpoint_store is not None:
            load = lambda identity: checkpoint_store.get('devices', self._storage_key(identity))
            save = lambda identity, data: checkpoint_store.save('devices', self._storage_key(identity), data)
        self.load, self.save = load, save
        self.devices = {}
        self.epoch = str(uuid.uuid4())
        self.lock = threading.RLock()
        self.policy = Policy()
        self.controls = {}

    @staticmethod
    def _storage_key(identity):
        return hashlib.sha256(identity.encode()).hexdigest()

    def set_policy(self, data):
        with self.lock:
            self.policy = Policy(**{**asdict(self.policy), **data})
            # Applied at the next lease acquisition, never inside a gameplay batch.
            return asdict(self.policy)

    def request_control(self, profile_id, action):
        with self.lock:
            action = 'stop' if action == 'immediate_stop' else action
            if action not in ('pause', 'resume', 'safe_stop', 'stop'):
                raise ValueError('Unsupported scheduler control')
            self.controls[profile_id] = '' if action == 'resume' else action
            return {'status': 'requested', 'profile_id': profile_id, 'action': action}

    def register_profile(self, device_id, profile_id, owner_id, candidates, legacy_order=None):
        with self.lock:
            d = self._device(device_id)
            parsed = [Candidate(**{**raw, 'profile_id': profile_id, 'key': profile_id + ':' + raw['task']})
                      for raw in candidates]
            previous = {c.key: c for c in d['profiles'].get(profile_id, {}).get('candidates', [])}
            for candidate in parsed:
                if candidate.runnable(self.clock()) and not self.controls.get(profile_id):
                    old = previous.get(candidate.key)
                    candidate.ready_since = old.ready_since if old and old.ready_since is not None else self.monotonic()
                else:
                    candidate.ready_since = None
            if profile_id not in d['profiles']:
                d['sequence'] += 1
            d['profiles'][profile_id] = {'candidates': parsed, 'owner_id': owner_id,
                'order': legacy_order or [c.task for c in parsed],
                'sequence': d['profiles'].get(profile_id, {}).get('sequence', d['sequence'])}

    def _device(self, device_id):
        if device_id not in self.devices:
            saved = self.load(device_id) if self.load else None
            self.devices[device_id] = {'engine': FairScheduler(saved.get('scheduler') if saved else None),
                'generation': (saved or {}).get('generation', 0), 'lease': None,
                'profiles': {}, 'decision': None, 'policy': Policy(), 'pause': self.controls, 'sequence': 0}
        return self.devices[device_id]

    def _save(self, device_id, device):
        if self.save:
            self.save(device_id, {'schema_version': 1, 'generation': device['generation'],
                                 'scheduler': copy.deepcopy(device['engine'].snapshot())})

    def acquire(self, payload):
        with self.lock:
            device_id = payload['device_id']
            d = self._device(device_id)
            profile = payload['profile_id']
            owner = payload['owner_id']
            if not d['lease']:
                d['policy'] = copy.deepcopy(self.policy)
            policy = d['policy']
            self.register_profile(device_id, profile, owner, payload.get('candidates', []), payload.get('legacy_order'))
            if d['lease']:
                return {'status': 'waiting_resource', 'holder': d['lease']['profile_id'],
                        'reason': 'device_owned', 'control': d['pause'].get(profile)}
            all_candidates = []
            for identity, record in d['profiles'].items():
                for c in record['candidates']:
                    c = copy.copy(c)
                    historical = d['engine'].entities.get(c.key, {})
                    if historical.get('config_revision') == c.config_revision and historical.get('estimate_seconds'):
                        c.quantum = max(1, min(86400, historical['estimate_seconds']))
                    control = d['pause'].get(identity, '')
                    if control in ('pause', 'paused', 'safe_stop', 'stop'):
                        c.blocked_reason = 'paused' if control in ('pause', 'paused') else 'stopped'
                    all_candidates.append(c)
            wall_now, mono_now = self.clock(), self.monotonic()
            fair = d['engine'].choose(all_candidates, wall_now, mono_now, policy)
            ready = {c.key: c for c in all_candidates if c.runnable(wall_now)}
            legacy = []
            for identity, record in sorted(d['profiles'].items(), key=lambda item: item[1]['sequence']):
                for task in record['order']:
                    if identity + ':' + task in ready:
                        legacy.append(identity + ':' + task)
            selected = fair.get('key') if fair and policy.mode == 'eevdf' else (legacy[0] if legacy else None)
            decision = dict(fair or {})
            decision.update(mode=policy.mode, fair_key=fair.get('key') if fair else None,
                            legacy_key=legacy[0] if legacy else None, key=selected)
            if policy.mode != 'eevdf':
                decision['reason'] = 'legacy_order'
            d['decision'] = decision
            if selected is None:
                return {'status': 'waiting', 'decision': decision, 'control': d['pause'].get(profile)}
            candidate = ready[selected]
            if candidate.profile_id != profile:
                return {'status': 'waiting_resource', 'holder': candidate.profile_id,
                        'reason': 'another_profile_selected', 'decision': decision, 'control': d['pause'].get(profile)}
            d['generation'] += 1
            self._save(device_id, d)  # durable generation before issuing control authority
            lease = {'device_id': device_id, 'profile_id': profile, 'owner_id': owner,
                     'generation': d['generation'], 'epoch': self.epoch, 'key': selected,
                     'task': candidate.task}
            d['lease'] = lease
            return {'status': 'acquired', 'lease': copy.deepcopy(lease), 'decision': decision,
                    'batch_seconds': policy.batch_seconds, 'mode': policy.mode,
                    'queue_wait_seconds': max(0, mono_now - d['engine'].entities[selected]['ready_since']),
                    'cooperative': candidate.cooperative}

    def validate(self, lease):
        with self.lock:
            d = self._device(lease['device_id'])
            if not d['lease'] or d['lease'] != lease or lease['epoch'] != self.epoch:
                raise LeaseLost('Device ownership is no longer valid')
            return {'valid': True, 'control': d['pause'].get(lease['profile_id'])}

    def maintenance(self, payload):
        """Brief idle/preheat actions are allowed only while no due task needs this device."""
        with self.lock:
            device_id, profile = payload['device_id'], payload['profile_id']
            d = self._device(device_id)
            if d['lease'] or any(c.runnable(self.clock()) for record in d['profiles'].values()
                                 for c in record['candidates']):
                return {'status': 'waiting_resource'}
            if d['pause'].get(profile) in ('pause', 'paused', 'stop', 'safe_stop'):
                return {'status': 'waiting', 'control': d['pause'][profile]}
            d['generation'] += 1
            self._save(device_id, d)
            lease = {'device_id': device_id, 'profile_id': profile, 'owner_id': payload['owner_id'],
                     'generation': d['generation'], 'epoch': self.epoch, 'key': None, 'task': 'Maintenance'}
            d['lease'] = lease
            return {'status': 'acquired', 'lease': copy.deepcopy(lease)}

    def release(self, payload):
        with self.lock:
            lease = payload['lease']
            self.validate(lease)
            d = self._device(lease['device_id'])
            reason = (d['decision'] or {}).get('reason', 'fair_share')
            if lease['key']:
                d['engine'].account(lease['key'], float(payload.get('actual_seconds', 0)),
                    self.clock(), self.monotonic(), reason,
                    payload.get('outcome') in ('failed', 'recovery_requested'), d['policy'])
            if payload.get('outcome') in ('cancelled', 'paused'):
                d['pause'][lease['profile_id']] = 'paused'
            self._save(lease['device_id'], d)
            d['lease'] = None
            # Rotation maintains legacy per-profile order while allowing waiting profiles.
            d['sequence'] += 1
            if lease['profile_id'] in d['profiles']:
                d['profiles'][lease['profile_id']]['sequence'] = d['sequence']
                for candidate in d['profiles'][lease['profile_id']]['candidates']:
                    if candidate.key == lease['key']:
                        candidate.ready_since = self.monotonic()
            if payload.get('outcome') in ('succeeded', 'failed', 'cancelled', 'interrupted', 'crashed'):
                record = d['profiles'][lease['profile_id']]
                record['candidates'] = [c for c in record['candidates'] if c.task != lease['task']]
            return {'released': True}

    def control(self, payload):
        with self.lock:
            action = payload['action']
            if action == 'status':
                return {'control': self.controls.get(payload['profile_id'])}
            d = self._device(payload['device_id'])
            if action not in ('pause', 'resume', 'safe_stop', 'stop'):
                raise ValueError('Unsupported scheduler control')
            d['pause'][payload['profile_id']] = '' if action == 'resume' else action
            return {'status': 'requested', 'waiting_safe_boundary': bool(d['lease'])}

    def revoke_owner(self, owner_id, confirmed_dead=False):
        """Parent must join/verify process death before it can transfer ownership."""
        if not confirmed_dead:
            raise LeaseLost('A timeout alone cannot revoke a device lease')
        with self.lock:
            for device_id, d in self.devices.items():
                if d['lease'] and d['lease']['owner_id'] == owner_id:
                    d['generation'] += 1
                    self._save(device_id, d)
                    d['lease'] = None
                d['profiles'] = {key: record for key, record in d['profiles'].items()
                                 if record['owner_id'] != owner_id}

    def snapshot(self):
        with self.lock:
            return {device_id: {'lease': copy.deepcopy(d['lease']), 'decision': copy.deepcopy(d['decision']),
                               'policy': asdict(d['policy']), 'controls': dict(d['pause']),
                               'entities': copy.deepcopy(d['engine'].entities)}
                    for device_id, d in self.devices.items()}

    def dispatch(self, operation, payload):
        name = operation.removeprefix('scheduling.')
        if name == 'checkpoint':
            if self.checkpoint_store is None:
                raise RuntimeError('Checkpoint storage is unavailable')
            action, kind = payload['action'], payload['kind']
            if action == 'list':
                items = self.checkpoint_store.list(kind)
                records = [item.get('data', item) for item in items]
                return {'items': [item for item in records if item.get('profile_id') == payload['profile_id']]}
            key = payload['key']
            if action == 'get':
                return {'data': self.checkpoint_store.get(kind, key)}
            if action == 'save':
                result = self.checkpoint_store.save(kind, key, payload['data'], event_seq=payload.get('event_seq'))
                return {'saved': True, 'data': result}
            raise ValueError('Invalid checkpoint action')
        if name == 'validate':
            return self.validate(payload['lease'])
        if name in ('acquire', 'release', 'control', 'maintenance'):
            return getattr(self, name)(payload)
        raise ValueError('Unknown coordinator operation')
