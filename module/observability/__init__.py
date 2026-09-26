"""Durable, bounded local history for Solana.

One EventStore owns a data directory. Other processes submit through the
application's IPC service; opening another writer deliberately fails.
"""
from .storage import EventStore, StoragePolicy
from .checkpoint import CheckpointStore
from .summary import SummaryStore
from .common import (
    StorageError, StorageUnavailable, StorageRestricted, WriterLocked,
    HistoryExpired, ReplayExpired, TerminalConflict, SchemaError,
)

__all__ = ["EventStore", "StoragePolicy", "CheckpointStore", "SummaryStore",
           "StorageError", "StorageUnavailable", "StorageRestricted",
           "WriterLocked", "HistoryExpired", "ReplayExpired",
           "TerminalConflict", "SchemaError"]
