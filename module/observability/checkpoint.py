"""Atomic recovery checkpoints, independent of ordinary history retention."""
from __future__ import annotations

import hashlib
import re
import threading
from pathlib import Path

from .common import atomic_json, read_json, redact, iso, SCHEMA_VERSION, DirectoryLock, fsync_directory


class CheckpointStore:
    def __init__(self, root, *, committed_seq=None, observer=None, lock=None, assert_open=None, is_persisted=None):
        self.root = Path(root)
        self.directory = self.root / "checkpoints"
        self._committed_seq = committed_seq
        self._is_persisted = is_persisted
        self._observer = observer
        self._lock = lock or threading.RLock()
        self._writer = DirectoryLock(self.root / "meta" / "writer.lock") if lock is None else None
        self._assert_open = assert_open or (lambda: None)

    def _path(self, kind, key):
        if not isinstance(kind, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", kind):
            raise ValueError("Invalid checkpoint kind")
        key = str(key)
        if not key:
            raise ValueError("Checkpoint key is required")
        return self.directory / kind / (hashlib.sha256(key.encode()).hexdigest() + ".json")

    def save(self, kind, key, data, event_seq=None):
        with self._lock:
            self._assert_open()
            previous = self.get_document(kind, key)
            seq = int(event_seq if event_seq is not None else data.get("event_seq") if data.get("event_seq") is not None else previous["event_seq"] if previous else 0)
            if seq < 0 or (self._committed_seq and seq > self._committed_seq()):
                raise ValueError("Checkpoint cannot reference an unacknowledged event")
            if seq and self._is_persisted and (not previous or seq != previous["event_seq"]) and not self._is_persisted(seq):
                raise ValueError("Checkpoint event sequence is a reserved gap or outside retained verification window")
            if previous and seq < previous["event_seq"]:
                raise ValueError("Checkpoint event_seq cannot regress")
            document = {"schema_version": SCHEMA_VERSION, "kind": kind, "key": str(key),
                        "event_seq": seq, "updated_at": iso(), "data": redact(data)}
            atomic_json(self._path(kind, key), document, self._observer)
            return document

    def get(self, kind, key):
        document = self.get_document(kind, key)
        return document["data"] if document else None

    def get_document(self, kind, key):
        with self._lock:
            return read_json(self._path(kind, key))

    def list(self, kind=None):
        with self._lock:
            kinds = [kind] if kind else [p.name for p in self.directory.iterdir() if p.is_dir()] if self.directory.exists() else []
            result = []
            for category in kinds:
                # Validate even when a directory does not exist.
                self._path(category, "validation")
                for path in sorted((self.directory / category).glob("*.json")):
                    result.append(read_json(path))
            return result

    def delete(self, kind, key, *, verified=False):
        """Only explicitly verified, terminal checkpoints may be reclaimed."""
        with self._lock:
            self._assert_open()
            current = self.get_document(kind, key)
            if current is None:
                return False
            if not verified or not current["data"].get("terminal"):
                raise ValueError("Checkpoint deletion requires verified terminal state")
            self._path(kind, key).unlink()
            fsync_directory(self._path(kind, key).parent)
            return True

    def reclaim(self, before):
        """Keep unverified and nonterminal recovery state regardless of budget."""
        removed = 0
        for document in self.list():
            if document["kind"] in {"settings", "profiles", "devices", "scheduler"}:
                continue
            data = document["data"]
            if (data.get("terminal") and data.get("verified") and document["updated_at"] < before
                    and (not data.get("retained_until") or data["retained_until"] < before)):
                removed += bool(self.delete(document["kind"], document["key"], verified=True))
        return removed

    def close(self):
        if self._writer:
            self._writer.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
