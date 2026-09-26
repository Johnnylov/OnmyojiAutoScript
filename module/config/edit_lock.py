"""Short cross-process locks around config read/compare/write transactions."""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import threading
import time
import copy
import inspect
from contextlib import ExitStack
from functools import wraps


class ConfigConflict(RuntimeError):
    """Concurrent changes to the same field; never include secret values."""
    def __init__(self, paths):
        self.paths = sorted(set(paths))
        super().__init__('Configuration fields changed concurrently: ' + ', '.join(self.paths))


def config_path(name):
    if not isinstance(name, str) or not name or name in ('.', '..') or '/' in name or '\\' in name:
        raise ValueError('Invalid configuration name')
    return (Path.cwd() / 'config' / (name + '.json')).resolve()


def locked_config_files(*names, error_type=ValueError):
    """Lock a whole legacy manager transaction, including existence checks."""
    def decorate(function):
        signature = inspect.signature(function)
        @wraps(function)
        def wrapped(*args, **kwargs):
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            try:
                paths = sorted({config_path(bound.arguments[name]) for name in names}, key=lambda p: os.path.normcase(str(p)))
            except ValueError as exc:
                raise error_type(str(exc)) from exc
            with ExitStack() as stack:
                for path in paths:
                    stack.enter_context(config_edit_lock(path))
                return function(*args, **kwargs)
        return wrapped
    return decorate


_MISSING = object()


def _get(data, path):
    for key in path:
        if not isinstance(data, dict) or key not in data:
            return _MISSING
        data = data[key]
    return data


def _put(data, path, value):
    target = data
    for key in path[:-1]:
        if not isinstance(target.get(key), dict):
            target[key] = {}
        target = target[key]
    if value is _MISSING:
        target.pop(path[-1], None)
    else:
        target[path[-1]] = copy.deepcopy(value)


def changed_paths(before, after, prefix=()):
    if isinstance(before, dict) and isinstance(after, dict):
        result = []
        for key in sorted(set(before) | set(after)):
            if key not in before or key not in after:
                result.append(prefix + (key,))
            else:
                result.extend(changed_paths(before[key], after[key], prefix + (key,)))
        return result
    # Lists have no stable element identity. Treat a list as an atomic field;
    # merging indexes after a concurrent reorder can change the wrong target.
    return [prefix] if before != after else []


def merge_config_fields(local_base, disk_base, desired, current):
    """Three-way patch without advancing unseen fields' expected baseline.

    Return (merged_disk, new_expected_disk). The running model keeps its own
    view; external changes are not injected into an in-flight task.
    """
    patches = changed_paths(local_base, desired)
    conflicts = []
    for path in patches:
        expected, latest, requested = _get(disk_base, path), _get(current, path), _get(desired, path)
        if latest != expected and latest != requested:
            conflicts.append('.'.join(path))
        # A removed/replaced parent object is also a conflict, even when the
        # leaf being edited did not exist in either original document.
        for depth in range(1, len(path)):
            ancestor = path[:depth]
            base_parent, parent = _get(disk_base, ancestor), _get(current, ancestor)
            if isinstance(base_parent, dict) and not isinstance(parent, dict):
                conflicts.append('.'.join(ancestor))
    if conflicts:
        raise ConfigConflict(conflicts)
    merged, expected_disk = copy.deepcopy(current), copy.deepcopy(disk_base)
    for path in patches:
        value = _get(desired, path)
        _put(merged, path, value)
        _put(expected_disk, path, value)

    # Materialize newly introduced schema defaults only where the latest file
    # is still missing them; preserve unknown fields from custom task versions.
    def defaults(base, requested, latest, expected, prefix=()):
        if not isinstance(requested, dict) or not isinstance(latest, dict):
            return
        for key, value in requested.items():
            if key not in latest and (not isinstance(base, dict) or key not in base):
                latest[key] = copy.deepcopy(value)
                _put(expected_disk, prefix + (key,), value)
            elif key in latest and isinstance(value, dict):
                defaults(base.get(key, {}) if isinstance(base, dict) else {}, value, latest[key], expected, prefix + (key,))
    defaults(disk_base, desired, merged, expected_disk)
    return merged, expected_disk

_locks = {}
_guard = threading.Lock()
_local = threading.local()


@contextmanager
def config_edit_lock(path, timeout=10):
    path = Path(path).resolve()
    identity = os.path.normcase(str(path))
    with _guard:
        mutex = _locks.setdefault(identity, threading.RLock())
    with mutex:
        held = getattr(_local, 'held', set())
        if identity in held:
            yield
            return
        folder = path.parent / '.runtime' / 'write_locks'
        folder.mkdir(parents=True, exist_ok=True)
        filename = folder / (hashlib.sha256(identity.encode()).hexdigest() + '.lock')
        with filename.open('a+b') as handle:
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            deadline = time.monotonic() + timeout
            while True:
                try:
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Configuration is busy')
                    time.sleep(0.02)
            _local.held = held | {identity}
            try:
                yield
            finally:
                _local.held = held
                handle.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
