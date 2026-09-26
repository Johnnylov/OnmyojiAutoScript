# Solana local history

`EventStore(root)` owns an OS lock for the entire writable runtime directory.
The API process must share this instance across threads and serve worker
submissions through bounded IPC. A second process must not create a writer for
the same directory. There are no imports from the application or third-party
packages; Python 3.10 is supported.

## Public interface

```python
store = EventStore(runtime_path)
receipt = store.append({
    "event_id": stable_producer_uuid,  # reuse on retry
    "type": "run.finished",
    "run_id": run_id,
    "profile_id": profile_id,
    "task_id": task_id,
    "occurred_at": "2026-09-24T04:00:00Z",
    "payload": {"outcome": "succeeded", "execution_seconds": 12.3},
})
store.query({"profile_id": profile_id, "types": ["run.finished"]}, limit=50)
store.statistics(start_date="2026-09-24", end_date="2026-09-24")
store.storage_status()
store.set_policy({"mode": "summary_only", "event_retention_days": 14})
store.cleanup(force=False)
store.close()
```

An ACK with `persisted=True` follows event `fsync`, durable indexing and summary
commit. `duration_ms` and `delayed` expose ACK latency over 250 ms. This is a
synchronous API; dispatch it outside an async server's event loop. On storage
failure the commit outcome can be unknown. Retry the **same** event identity;
the store scans durable state before retrying. Recovery does not itself grant
permission to dispatch tasks: the application must reconcile device/run state
and call `acknowledge_recovery()`.

`query` returns descending sequence order, `next_cursor`, a snapshot sequence,
gaps and an explicit `bounded_reason` if its byte or time budget is hit. Filters:
`profile_id`, `device_id`, `task_id`, `run_id`, `request_id`, `type`, `types`,
`type_prefix`, `outcome`, `after_seq`, `start_at`, `end_at`, `start_date`,
`end_date`. Date-only filters use the report timezone. A query cursor is invalid
after detail deletion (`HistoryExpired`); newly appended events cannot enter
an already established snapshot. `removed_ranges` distinguishes cleaned history
from an empty database. Queries with no progress and a scan limit return no
cursor, rather than a cursor that loops forever.

## Checkpoints and consumer watermarks

`store.checkpoints.save(kind, key, data, event_seq=None)` atomically persists an
envelope. `get(kind,key)` returns business data; `get_document` returns the
envelope and `list(kind=None)` returns envelopes. Omitted event sequence inherits
the previous sequence. New explicit references must identify a persisted event,
not merely a reserved sequence hole. Valid kinds match `[a-z][a-z0-9_]{0,63}`;
keys are hashed for filenames. Checkpoint writes share the writer lock and budget
observer. A standalone `CheckpointStore` acquires the same OS lock itself.

Every additional durable consumer (such as the runtime state reducer) must
register a checkpoint before consuming events:

```python
store.checkpoints.save("settings", "reducer_cursor", {
    "retention_consumer": True, "cursor_seq": last_fully_committed_seq,
}, event_seq=last_fully_committed_seq)
```

Advance only after all affected runtime checkpoints are durable. Cleanup cannot
delete an event beyond any registered consumer's cursor. On replay, process
queried events in **ascending** sequence order and commit checkpoints
idempotently. The store does not own application reducer semantics.

Only `terminal=True`, `verified=True` checkpoints older than the retry window
are automatically reclaimed. Optional `retained_until` extends this period.
Identity/settings/device/scheduler kinds remain protected. Active runs, pending
requests, unverified recovery checkpoints and unaggregated shards also protect
their required events. Explicit terminal checkpoint deletion requires
`delete(..., verified=True)`.

## Persistence and aggregation

- Sequence ranges are persisted **before** allocation. Restarts skip unused
  reservations. Cleanup never lowers this independent high-water mark.
- Recent dedup indexes survive detail deletion; automatic replay older than the
  retained dedup boundary is rejected. Unfinished runs and pending requests keep
  their entries until resolved. This is bounded dedup, not an everlasting ID log.
- Run starts, logical terminals and segment boundaries also have business
  identity dedup. A conflicting second terminal raises `TerminalConflict`.
- Each daily aggregate atomically contains both values and its consumed sequence.
  The global summary cursor moves only after every affected day commits. Replay
  after partial cross-day commits therefore cannot double-count one day.
- Segment duration is supplied by a monotonic clock. Valid wall-clock intervals
  split it across local reporting days; clock discontinuities preserve the total
  as an anomalous interval and flag incompleteness.
- Configuration secrets are redacted **before** WAL or checkpoint persistence.
  Sensitive structured diff paths redact before/after values as `[changed]`.
  Do not submit unstructured raw logs or credentials as arbitrary event text.
- An incomplete final record is truncated with a visible repair report. Damaged
  complete/interior records remain in a closed shard, are reported as gaps and
  block silent deletion. Schema mismatches stop startup/writing.
- Atomic JSON uses a same-directory temporary file, file `fsync`, then replace.
  POSIX also fsyncs the containing directory; standard-library Windows APIs do
  not provide equivalent directory flushing. OS locks are released on death.

## Modes and capacity

`standard` retains bounded details and daily aggregates. `summary_only` retains
short-lived WAL until all consumers and recovery dependencies permit deletion.
`off` omits optional history and reduces required control/recovery payloads; it
does not disable checkpoints. Each event fixes its aggregation requirement at
write time. Changing modes cannot waive prior summary work.

Retention maintenance runs on explicit cleanup, capacity pressure and the first
append after a 60-second maintenance interval. Idle applications may call
`cleanup` from their maintenance service. Compression only targets closed shards,
checks temporary headroom and disk free space, verifies the new compressed file
and flushes it before deleting the original. Checkpoints and metadata count
toward capacity. Directory byte counts cover managed files, not filesystem
allocation overhead or legacy screenshots/text logs.

The store reports `restricted` when optional recording is paused, and
`unavailable` when critical persistence fails. The latter blocks dispatch until
application reconciliation. Fault-protective stop must still be performed by
the application even if its event cannot be saved. Reporting timezone changes
with existing aggregates require an explicit rebuild; historical daily counters
are not silently reinterpreted.

## Verification

Run `python -m unittest discover -s tests/solana -p 'test_storage*.py' -v`.
Tests cover same-process and restart retries, real subprocess deaths at five
commit boundaries, cross-day replay, corruption, sequence gaps, recovery
retention, privacy, queries, modes, compression, low disk and budget failures.

`tests/solana/storage_benchmark.py` creates and removes a synthetic near-budget
fixture, reports its exact size and cache conditions, and measures bounded query
and fsync ACK latency. Its fixture-generation path is intentionally direct and
is **not** a 24-hour workload test or a power-loss guarantee.
