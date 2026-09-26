"""Structured audit boundary for compatible legacy OAS HTTP/WS commands.

No event loop await occurs inside a configuration file transaction. Uploads
must be parsed before calling run_config_mutation. The callback is synchronous.
"""
from contextlib import contextmanager, ExitStack
from contextvars import ContextVar
import copy
import hashlib
import json
import os
from pathlib import Path
import uuid

from fastapi import HTTPException

from module.config.edit_lock import config_edit_lock, config_path


_context = ContextVar('oas_legacy_audit_context', default=None)


def _uuid(value=None):
    try:
        return str(uuid.UUID(value)) if value else str(uuid.uuid4())
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(400, detail={'code': 'invalid_request_id', 'message': 'X-Request-ID must be a UUID'}) from exc


@contextmanager
def legacy_request_context(request_id=None, client_id=None, transport='http', request=None):
    token = _context.set({'request_id': request_id or str(uuid.uuid4()),
                          'source': {'type': 'client', 'client_id': str(client_id or 'legacy-unspecified')[:128],
                                     'transport': transport}, 'request': request})
    try:
        yield
    finally:
        _context.reset(token)


def _context_value():
    value = dict(_context.get() or {'request_id': str(uuid.uuid4()),
                             'source': {'type': 'client', 'client_id': 'legacy-unspecified', 'transport': 'internal'}})
    value['request_id'] = _uuid(value['request_id'])
    if value.get('request'):
        value['request'].state.legacy_request_id = value['request_id']
    return value


def _service(service=None):
    if service is not None:
        return service
    from module.server.solana_runtime import get_runtime
    return get_runtime()


def _blocked(service, state='not_saved'):
    service.dispatch_blocked = True
    service.publish('storage.degraded', {'state': state, 'dispatch_blocked': True,
                                        'reason': 'legacy_audit_persistence_failed'})
    context = _context.get()
    if context and context.get('request'):
        context['request'].state.legacy_audit_state = state


def _snapshot(name):
    path = config_path(name)
    if not path.exists():
        return None, None
    raw = path.read_bytes()
    return json.loads(raw.decode('utf-8')), hashlib.sha256(raw).hexdigest()


def _changes(before, after):
    from module.server.config_manager import ConfigManager
    safe_before = ConfigManager.redact_config(before or {})
    safe_after = ConfigManager.redact_config(after or {})
    changes, count = [], [0]
    def visit(old, new, safe_old, safe_new, path=''):
        if all(isinstance(item, dict) for item in (old, new, safe_old, safe_new)):
            for key in sorted(set(old) | set(new)):
                visit(old.get(key), new.get(key), safe_old.get(key), safe_new.get(key), f'{path}.{key}' if path else key)
        elif old != new:
            count[0] += 1
            if len(changes) < 40:
                hidden = old != safe_old or new != safe_new
                def bounded(value):
                    return value if len(json.dumps(value, default=str)) < 256 else '[truncated]'
                changes.append({'path': path, 'old': '[changed]' if hidden else bounded(safe_old),
                                'new': '[changed]' if hidden else bounded(safe_new)})
    visit(before or {}, after or {}, safe_before, safe_after)
    return changes, count[0]


def _repair_identity(service, record, moved):
    old, new, identity = record['rename']['old_name'], record['rename']['new_name'], record['profile_id']
    registry = copy.deepcopy(service.registry)
    if moved:
        registry['names'][new] = identity
        registry['names'].pop(old, None)
    else:
        registry['names'][old] = identity
    service.store.checkpoints.save('settings', 'profiles', registry)
    service.registry = registry
    service.publish('profiles.changed', {'profile_id': identity, 'name': new if moved else old})


def run_config_mutation(action, names, callback, *, arguments=None, rename=None, service=None, external_side_effect=False):
    """Durable intent -> locked mutation -> redacted result; legacy return kept."""
    service = _service(service)
    context = _context_value()
    request_id, source = context['request_id'], context['source']
    names = list(dict.fromkeys(names))
    from module.server.config_manager import ConfigManager, ConfigNameError
    try:
        for name in names:
            ConfigManager.validate_config_name(name)
    except ConfigNameError as exc:
        raise HTTPException(400, detail={'code': 'invalid_config_name'}) from exc
    digest = hashlib.sha256(json.dumps({'action': action, 'names': names, 'arguments': arguments},
                                        sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()
    with service.lock, ExitStack() as locks:
        for path in sorted({config_path(name) for name in names}, key=lambda path: os.path.normcase(str(path))):
            locks.enter_context(config_edit_lock(path))
        previous = service.store.checkpoints.get('legacy_requests', request_id)
        if previous:
            if previous['digest'] != digest:
                raise HTTPException(409, detail={'code': 'request_id_conflict'})
            if previous.get('terminal'):
                if previous.get('error'):
                    raise HTTPException(previous['error']['status'], detail=previous['error']['detail'])
                return copy.deepcopy(previous.get('response'))
            raise HTTPException(409, detail={'code': 'reconciliation_required', 'message': 'Previous operation must be reconciled'})
        before = {name: _snapshot(name) for name in names}
        identity_name = rename['old_name'] if rename else names[0] if names else None
        try:
            identity = service.profile_id(identity_name) if identity_name else None
        except Exception as exc:
            _blocked(service)
            raise HTTPException(503, detail={'code': 'storage_unavailable', 'message': 'Operation not executed'}) from exc
        record = {'action': action, 'digest': digest, 'names': names, 'profile_id': identity,
                  'before_revisions': {name: snapshot[1] for name, snapshot in before.items()},
                  'source': source, 'terminal': False, 'verified': False}
        if rename:
            record['rename'] = dict(rename)
        try:
            intent = service.emit({'type': 'control.requested', 'request_id': request_id,
                'profile_id': identity, 'source': source, 'payload': {
                    'action': action, 'targets': names, 'rename': rename,
                    'before_revisions': record['before_revisions'],
                    'request_kind': 'legacy_requests', 'request_digest': digest}})
            service.store.checkpoints.save('legacy_requests', request_id, record, event_seq=intent['seq'])
        except Exception as exc:
            _blocked(service)
            raise HTTPException(503, detail={'code': 'storage_unavailable', 'message': 'Operation not executed'}) from exc
        try:
            response = callback()
            if rename and response is not False:
                _repair_identity(service, record, moved=True)
        except Exception as exc:
            # Preserve an unresolved intent if the file may have changed or a
            # second-file identity commit failed. Startup reconciliation handles
            # rename based on hashes, never by guessing the newer filename.
            changed = any(_snapshot(name)[1] != before[name][1] for name in names)
            if changed or external_side_effect:
                _blocked(service, 'executed_not_saved')
                raise HTTPException(503, detail={'code': 'reconciliation_required'}) from exc
            try:
                status = getattr(exc, 'status_code', 400 if isinstance(exc, ValueError) else 500)
                detail = {'code': type(exc).__name__, 'message': 'Legacy operation rejected'}
                completed = service.emit({'type': 'control.completed', 'request_id': request_id,
                    'profile_id': identity, 'source': source,
                    'payload': {'action': action, 'executed': False, 'error_category': type(exc).__name__}})
                service.store.checkpoints.save('legacy_requests', request_id,
                    {**record, 'terminal': True, 'verified': True, 'error': {'status': status, 'detail': detail}},
                    event_seq=completed['seq'])
            except Exception:
                _blocked(service)
            raise
        after = {name: _snapshot(name) for name in names}
        details, total = [], 0
        for name in names:
            changes, count = _changes(before[name][0], after[name][0])
            if count:
                details.append({'profile_name': name, 'changes': changes})
                total += count
        try:
            service.emit({'type': 'config.changed', 'request_id': request_id,
                'profile_id': identity, 'source': source, 'payload': {
                    'action': action, 'profiles': details, 'changed_fields': total,
                    'truncated': any(len(item['changes']) >= 40 for item in details),
                    'after_revisions': {name: snapshot[1] for name, snapshot in after.items()}}})
            completed = service.emit({'type': 'control.completed', 'request_id': request_id,
                'profile_id': identity, 'source': source,
                'payload': {'action': action, 'executed': response is not False, 'status': 'completed' if response is not False else 'rejected'}})
            service.store.checkpoints.save('legacy_requests', request_id,
                {**record, 'terminal': True, 'verified': True, 'response': response}, event_seq=completed['seq'])
        except Exception:
            _blocked(service, 'executed_not_saved')
        return response


async def run_legacy_control(name, action, callback, *, service=None, child_operation=None):
    """Control audit using the shared v2 core, without changing old JSON bodies."""
    service = _service(service)
    context = _context_value()
    try:
        identity = service.profile_id(name)
    except Exception as exc:
        _blocked(service)
        if action in ('safe_stop', 'immediate_stop'):
            await callback()
            _blocked(service, 'executed_not_saved')
            return {'executed': True, 'persisted': False, 'status': 'executed_not_saved'}
        raise HTTPException(503, detail={'code': 'storage_unavailable'}) from exc
    request_id = context['request_id']
    if child_operation:
        request_id = str(uuid.uuid5(uuid.UUID(request_id), child_operation))
    data = {'profile_id': identity, 'action': action, 'request_id': request_id, 'source': context['source']}
    try:
        with service.lock:
            receipt = service.begin_control(data)
            if not receipt.get('duplicate'):
                service.active_operations.add(request_id)
    except Exception as exc:
        from module.observability.common import StorageError
        storage_failure = isinstance(exc, (OSError, StorageError)) or getattr(exc, 'code', '') == 'storage_unavailable'
        if action not in ('safe_stop', 'immediate_stop') or not storage_failure:
            raise HTTPException(getattr(exc, 'status', 503), detail={'code': getattr(exc, 'code', 'storage_unavailable')}) from exc
        _blocked(service)
        receipt = {**data, 'name': name, 'accepted': True, 'persisted': False}
        with service.lock:
            service.active_operations.add(request_id)
    if receipt.get('duplicate'):
        if receipt.get('executed'):
            return receipt
        raise HTTPException(409, detail={'code': 'reconciliation_required'})
    try:
        try:
            await callback()
        except Exception as exc:
            service.complete_control(receipt, False, 'failed', type(exc).__name__)
            raise
        result = service.complete_control(receipt, True, 'started' if action == 'start' else 'stopped')
        if not result.get('persisted'):
            _blocked(service, 'executed_not_saved')
        return result
    finally:
        with service.lock:
            service.active_operations.discard(request_id)


def reconcile_legacy_operations(service):
    """Call at startup BEFORE profile enumeration/process auto-start.

    Repairs only a provably completed/not-applied rename. Other ambiguous
    mutation intents remain visible and block dispatch until explicit review.
    """
    completed, unresolved = [], []
    with service.lock:
        _restore_orphan_intents(service)
        for envelope in service.store.checkpoints.list('legacy_requests'):
            record = envelope['data']
            if record.get('terminal'):
                continue
            request_id = envelope['key']
            if not record.get('rename'):
                unresolved.append(request_id)
                continue
            try:
                old, new = record['rename']['old_name'], record['rename']['new_name']
                with ExitStack() as locks:
                    for path in sorted({config_path(old), config_path(new)}, key=lambda path: os.path.normcase(str(path))):
                        locks.enter_context(config_edit_lock(path))
                    old_hash, new_hash = _snapshot(old)[1], _snapshot(new)[1]
                    expected = record['before_revisions'][old]
                    moved = old_hash is None and new_hash == expected and expected is not None
                    untouched = old_hash == expected and new_hash is None and expected is not None
                    if not moved and not untouched:
                        unresolved.append(request_id)
                        continue
                    _repair_identity(service, record, moved)
                    ack = service.emit({'type': 'control.completed', 'request_id': request_id,
                        'profile_id': record['profile_id'], 'source': {'type': 'recovery'},
                        'payload': {'action': record['action'], 'executed': moved,
                                    'status': 'identity_reconciled' if moved else 'not_applied'}})
                    service.store.checkpoints.save('legacy_requests', request_id,
                        {**record, 'terminal': True, 'verified': True, 'response': moved}, event_seq=ack['seq'])
            except Exception:
                unresolved.append(request_id)
                continue
            completed.append(request_id)
        unresolved = list(dict.fromkeys(unresolved + [item['key'] for item in list_pending_operations(service)]))
        if unresolved:
            service.dispatch_blocked = True
            service.publish('recovery.required', {'reason': 'legacy_mutation_unresolved', 'request_ids': unresolved})
    return {'reconciled': completed, 'unresolved': unresolved,
            'reconciled_count': len(completed), 'pending_count': len(unresolved)}


def _operation_names(record):
    from module.server.config_manager import ConfigManager
    names = record.get('names') or ([record['name']] if record.get('name') else [])
    if not names or not record.get('profile_id'):
        return []
    for name in names:
        ConfigManager.validate_config_name(name)
    return list(dict.fromkeys(names))


def _operation_record(kind, record):
    if kind == 'requests':
        # Controls already retain their identity in the core's receipt.
        return {**record.get('result', {}), **record}
    return record


def _is_pending(record):
    return not record.get('terminal') or record.get('resolution', {}).get('phase') == 'pending'


def _restore_orphan_intents(service):
    """Repair the WAL-fsync -> checkpoint cut, using only recorded identity.

    Missing/old metadata is represented by an unresolvable core receipt. A
    bounded or unavailable scan remains visible and must never unlock dispatch.
    Returns transient documents if checkpoint persistence itself is unavailable.
    """
    categories = ('legacy_requests', 'config_requests', 'requests')
    known = {document['key'] for kind in categories for document in service.store.checkpoints.list(kind)}
    missing = {key: seq for key, seq in service.store.dedup['pending_requests'].items()
               if key not in known and key not in service.active_operations}
    if not missing:
        return []
    found, cursor = {}, None
    # Stop after a finite scan even when history is damaged; the unmatched keys
    # become visible unknown receipts instead of disappearing from recovery.
    try:
        for _ in range(128):
            page = service.store.query({'type': 'control.requested', 'after_seq': min(missing.values()) - 1},
                                       cursor=cursor, limit=200)
            for event in page['items']:
                if event.get('request_id') in missing:
                    found[event['request_id']] = event
            cursor = page['next_cursor']
            if len(found) == len(missing) or not cursor:
                break
    except (OSError, ValueError):
        pass
    transient = []
    for key, seq in missing.items():
        event = found.get(key, {})
        payload = event.get('payload', {})
        kind = payload.get('request_kind')
        if kind not in categories:
            kind = 'requests'
        record = {'digest': payload.get('request_digest'), 'profile_id': event.get('profile_id'),
                  'name': payload.get('name'), 'action': payload.get('action'),
                  'source': event.get('source'), 'terminal': False, 'verified': False,
                  'reconstructed_from_intent': True}
        if kind == 'legacy_requests':
            record.update(names=payload.get('targets', []), rename=payload.get('rename'),
                          before_revisions=payload.get('before_revisions', {}))
        elif kind == 'config_requests':
            record.update(path=payload.get('path'), previous_revision=event.get('config_revision'))
        else:
            record['result'] = {'request_id': key, 'profile_id': record['profile_id'],
                'name': record['name'], 'action': record['action'], 'source': record['source'],
                'accepted': True, 'executed': None, 'persisted': True, 'status': 'needs_reconciliation'}
        document = {'kind': kind, 'key': key, 'event_seq': seq, 'data': record}
        try:
            if not event:
                # Persisting an unidentifiable placeholder could mask a later
                # successful history read; retain it only in this inventory.
                raise ValueError('Intent unavailable in bounded scan')
            service.store.checkpoints.save(kind, key, record, event_seq=seq)
        except Exception:
            transient.append(document)
    service.dispatch_blocked = True
    return transient


def list_pending_operations(service):
    """Sanitized review inventory; no config values or uploaded filenames."""
    result = []
    with service.lock:
        transient = _restore_orphan_intents(service)
        for kind in ('legacy_requests', 'config_requests', 'requests'):
            documents = service.store.checkpoints.list(kind) + [item for item in transient if item['kind'] == kind]
            for envelope in documents:
                if envelope['key'] in service.active_operations:
                    continue
                record = _operation_record(kind, envelope['data'])
                if not _is_pending(record):
                    continue
                item = {'kind': kind, 'key': envelope['key'], 'profile_id': record.get('profile_id'),
                        'name': record.get('name'), 'action': record.get('action', 'config.change'),
                        'path': record.get('path'), 'resolvable': False, 'expected_revisions': {}}
                if record.get('resolution', {}).get('phase') == 'pending':
                    item['resolution_request_id'] = record['resolution']['request_id']
                try:
                    names = _operation_names(record)
                    item.update(names=names, name=record.get('name') or (names[0] if names else None))
                    if not names:
                        item['explanation'] = 'Older record has no reliable profile identity; manual inspection is required.'
                    else:
                        with ExitStack() as locks:
                            for path in sorted({config_path(name) for name in names}, key=lambda p: os.path.normcase(str(p))):
                                locks.enter_context(config_edit_lock(path))
                            item['expected_revisions'] = {name: _snapshot(name)[1] for name in names}
                        item['intent_revisions'] = record.get('before_revisions', {names[0]: record.get('previous_revision')})
                        if record.get('rename'):
                            item['explanation'] = 'Rename identity must be proven from the original source hash; correct conflicting files, then retry reconciliation.'
                            item['retry_reconciliation'] = True
                        else:
                            item['resolvable'] = True
                            item['explanation'] = 'Review the current configuration. Accepting keeps it unchanged and records the original outcome as unknown; no side effects are repeated.'
                            if kind == 'requests':
                                alive = any(owner.get('alive') for owner in service.owners.values())
                                item['executor_alive'] = alive
                                item['resolvable'] = not alive
                                item['explanation'] = ('Stop all executors before reviewing this control receipt.' if alive else
                                    'No live executor is registered. Review current state; acceptance records the historical control outcome as unknown and does not start or stop a process.')
                except (OSError, ValueError, KeyError, TypeError):
                    item['explanation'] = 'Configuration identity or file cannot be validated; resolve the file problem first.'
                result.append(item)
    return result


def resolve_pending_operation(service, kind, key, expected_revisions, *, reviewed=False, request_id):
    """Accept verified current files, never infer or re-execute an old effect."""
    if reviewed is not True:
        raise HTTPException(400, detail={'code': 'review_required'})
    if kind not in ('legacy_requests', 'config_requests', 'requests'):
        raise HTTPException(400, detail={'code': 'invalid_operation_kind'})
    request_id = _uuid(request_id)
    digest = hashlib.sha256(json.dumps({'kind': kind, 'key': key, 'revisions': expected_revisions},
                                        sort_keys=True).encode()).hexdigest()
    with service.lock, ExitStack() as locks:
        if key in service.active_operations:
            raise HTTPException(409, detail={'code': 'operation_in_progress'})
        _restore_orphan_intents(service)
        record = service.store.checkpoints.get(kind, key)
        if not record:
            raise HTTPException(404, detail={'code': 'operation_not_found'})
        prior_resolution = record.get('resolution')
        if prior_resolution and prior_resolution['request_id'] == request_id:
            if prior_resolution['digest'] != digest:
                raise HTTPException(409, detail={'code': 'request_id_conflict'})
            if prior_resolution.get('phase') == 'completed':
                return {**prior_resolution['result'], 'duplicate': True}
        elif prior_resolution and prior_resolution.get('phase') == 'pending':
            raise HTTPException(409, detail={'code': 'resolution_in_progress',
                'request_id': prior_resolution['request_id']})
        elif record.get('terminal'):
            return {'resolved': True, 'already_resolved': True, 'kind': kind, 'key': key}
        for category in ('legacy_requests', 'config_requests', 'requests'):
            for envelope in service.store.checkpoints.list(category):
                resolution = envelope['data'].get('resolution', {})
                if resolution.get('request_id') == request_id and resolution.get('digest') != digest:
                    raise HTTPException(409, detail={'code': 'request_id_conflict'})
        operation = _operation_record(kind, record)
        if operation.get('rename'):
            raise HTTPException(409, detail={'code': 'rename_requires_reconciliation'})
        try:
            names = _operation_names(operation)
        except (ValueError, TypeError):
            names = []
        if not names:
            raise HTTPException(409, detail={'code': 'operation_identity_unknown'})
        if kind == 'requests' and any(owner.get('alive') for owner in service.owners.values()):
            raise HTTPException(409, detail={'code': 'executors_running'})
        for path in sorted({config_path(name) for name in names}, key=lambda p: os.path.normcase(str(p))):
            locks.enter_context(config_edit_lock(path))
        actual = {name: _snapshot(name)[1] for name in names}
        if expected_revisions != actual:
            raise HTTPException(409, detail={'code': 'revision_conflict', 'message': 'Reload and review the current files again'})
        source = {'type': 'recovery_review'}
        common = {'request_id': request_id, 'profile_id': operation['profile_id'], 'source': source}
        response = {'resolved': True, 'kind': kind, 'key': key, 'status': 'unknown_state_accepted',
                    'historical_outcome': 'unknown', 'current_state_verified': True, 'reexecuted': False}
        # A retry of the ORIGINAL request must not masquerade as a success or
        # rerun a purchase/manual-run effect after its history was lost.
        error = {'status': 409, 'detail': {'code': 'operation_outcome_unknown',
                                          'message': 'Current configuration was reviewed; original outcome remains unknown'}}
        resolution = {'request_id': request_id, 'digest': digest, 'reviewed_revisions': actual,
                      'result': response, 'phase': 'pending'}
        def checkpoint(data, seq):
            latest = service.store.checkpoints.get_document(kind, key)
            service.store.checkpoints.save(kind, key, data, event_seq=max(seq, latest['event_seq']))
        try:
            intent = service.emit({**common, 'event_id': str(uuid.uuid5(uuid.UUID(request_id), 'review-intent')),
                          'type': 'recovery.requested', 'payload': {
                              'action': 'accept_current_config', 'operation_kind': kind, 'operation_key': key,
                              'reviewed_revisions': actual}})
            # Retain an explicit pending review across every persistence cut.
            # The core reducer may terminalize a control at the next emit; the
            # pending phase prevents that intermediate state hiding from the UI.
            checkpoint({**record, 'resolution': resolution}, intent['seq'])
            closure = service.emit({'type': 'control.completed', 'request_id': key,
                'profile_id': operation['profile_id'], 'source': source, 'payload': {
                    'action': operation.get('action', 'config.change'), 'executed': None,
                    'status': 'unknown_state_accepted', 'historical_outcome': 'unknown',
                    'current_state_verified': True, 'resolution_request_id': request_id}})
            updated = {**record, 'terminal': True, 'verified': True, 'error': error,
                'result': {**record.get('result', {}), 'error_code': 'operation_outcome_unknown',
                    'http_status': 409, 'accepted': False, 'executed': None, 'persisted': True,
                    'historical_outcome': 'unknown', 'current_state_verified': True,
                    'message': error['detail']['message'], 'status': 'unknown_state_accepted'},
                'resolution': resolution}
            checkpoint(updated, closure['seq'])
            result = service.emit({**common, 'event_id': str(uuid.uuid5(uuid.UUID(request_id), 'review-result')),
                                  'type': 'recovery.resolved', 'payload': response})
            updated['resolution'] = {**resolution, 'phase': 'completed'}
            checkpoint(updated, result['seq'])
        except Exception as exc:
            _blocked(service)
            raise HTTPException(503, detail={'code': 'reconciliation_persistence_failed'}) from exc
        return response
