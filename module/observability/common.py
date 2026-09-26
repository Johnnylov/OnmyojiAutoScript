"""Standard-library persistence primitives. No application imports or startup IO."""
from __future__ import annotations

import copy
import json
import os
import re
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SCHEMA_VERSION = 1
TERMINAL = {"succeeded", "failed", "cancelled", "interrupted", "crashed"}


class StorageError(RuntimeError):
    code = "storage_error"


class StorageUnavailable(StorageError):
    code = "storage_unavailable"


class StorageRestricted(StorageError):
    code = "storage_restricted"


class WriterLocked(StorageError):
    code = "writer_locked"


class HistoryExpired(StorageError):
    code = "history_expired"


class ReplayExpired(StorageError):
    code = "replay_expired"


class TerminalConflict(StorageError):
    code = "terminal_conflict"


class SchemaError(StorageError):
    code = "schema_incompatible"


def utcnow():
    return datetime.now(timezone.utc)


def timestamp(value=None):
    value = value or utcnow()
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("Timestamps must include a UTC offset")
    return value.astimezone(timezone.utc)


def iso(value=None):
    return timestamp(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def report_zone(name):
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        if name == "Asia/Shanghai":
            return timezone(timedelta(hours=8), name)
        if name in {"UTC", "Etc/UTC"}:
            return timezone.utc
        raise ValueError(f"Timezone database unavailable for {name}")


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def fsync_directory(directory):
    # Windows does not expose directory fsync through os.open. os.replace is
    # still atomic there; file contents have been flushed before replacement.
    if os.name == "nt":
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_json(path, value, observer=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = encode(value) + b"\n"
    if observer:
        observer("before", len(data))
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if observer:
            observer("temporary", len(data))
        os.replace(temporary, path)
        fsync_directory(path.parent)
    except OSError:
        if observer:
            observer("failed", 0)
        raise
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path, default=None):
    try:
        with Path(path).open("r", encoding="utf-8") as stream:
            value = json.load(stream)
    except FileNotFoundError:
        return copy.deepcopy(default)
    if value.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
        raise SchemaError(f"Unsupported schema in {Path(path).name}")
    return value


class DirectoryLock:
    """An OS-released lock, including process death (not a stale PID file)."""
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = path.open("a+b")
        self.stream.seek(0, os.SEEK_END)
        if self.stream.tell() == 0:
            self.stream.write(b"\0")
            self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            self.stream.close()
            raise WriterLocked("Another writer owns this data directory") from exc

    def close(self):
        if self.stream.closed:
            return
        try:
            self.stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        finally:
            self.stream.close()


_SECRET = re.compile(r"(?:^|[.\[\]/_-])(?:password|passwd|secret|credential|credentials|access_token|refresh_token|api_key|apikey|notification_key|notify_config)(?:$|[.\[\]/_-])", re.I)
_SECRET_KEYS = {"password", "passwd", "secret", "token", "access_token", "refresh_token",
                "api_key", "apikey", "notification_key", "notify_config", "credentials"}
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*")
_QUERY_SECRET = re.compile(r"(?i)((?:password|token|secret|api_key)=)[^&\s]+")


def redact(value, _path=()):
    """Redact before writing WAL, dedup metadata, summaries or checkpoints.

    Structured diffs with a sensitive path redact old/new values as well as
    dictionaries whose own key names are sensitive. Arbitrary raw logs should
    never be placed in event payloads.
    """
    if isinstance(value, dict):
        sensitive_diff = any(_SECRET.search(str(value.get(k, ""))) or str(value.get(k, "")).split(".")[-1].lower() in _SECRET_KEYS
                             for k in ("path", "field", "field_path", "argument"))
        result = {}
        for key, item in value.items():
            key = str(key)
            # Secret is also the actual task ID for 秘闻副本. A numeric
            # scheduler weight is executable configuration, not a credential.
            task_weight = (_path[-1:] == ('weights',) and key.lower() == 'secret'
                           and isinstance(item, (int, float)) and not isinstance(item, bool)
                           and 0 < item < float('inf'))
            if task_weight:
                result[key] = item
            elif key.lower() in _SECRET_KEYS or _SECRET.search(key) or (sensitive_diff and key in {"old", "new", "before", "after", "value", "old_value", "new_value"}):
                result[key] = "[changed]"
            else:
                result[key] = redact(item, _path + (key,))
        return result
    if isinstance(value, (tuple, list)):
        return [redact(item, _path) for item in value]
    if isinstance(value, str):
        return _QUERY_SECRET.sub(r"\1[redacted]", _BEARER.sub("Bearer [redacted]", value))
    return value
