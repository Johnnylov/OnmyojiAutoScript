# Configuration concurrency and audit validation

The runtime and both UI generations share the existing JSON configurations.
There is no extra configuration database. `ConfigModel` retains its loaded
business values for the current run; later UI edits become visible to a newly
loaded model, while lifecycle writes preserve those edits on disk.

## Write protocol

Every `ConfigModel.save()`, top-level assignment and instance
`write_json(name, data)` goes through the same path. This includes legacy
`Config.save()`, task callbacks, `running_task`, task-copy methods and reset-time
operations. Repository search found no class-level `ConfigModel.write_json`
callers. The development template generator's detached-model template replacement
remains supported; detached models cannot overwrite an existing ordinary profile.

At load time the model captures both its normalized local view and the raw file
view, under the shared process/thread file lock. During save it compares the
local view to the submitted values to find intended changes, then reads the latest
file under that lock:

- Changes to different dictionary fields merge into the latest file.
- If another writer changed the same field since this model observed it,
  `ConfigConflict.paths` identifies the field and the whole transaction is
  rejected. Exception text does not include field values.
- An identical requested value is idempotent. Lists are atomic comparison units;
  indexes are not merged across possible reordering.
- Unknown custom fields and remote changes remain on disk. New schema defaults
  are filled only when they are still missing in the latest file.
- Saving an unrelated field does not advance the model's expected value for a
  field changed externally. This prevents a later save from implicitly accepting
  a value the running task never observed.
- Deleting/renaming a profile cannot cause a previously loaded writer to recreate
  it, including a profile whose old JSON object was empty.

Atomic file replacement is still provided by the project's existing
`write_file`/`atomic_write` implementation. Manager import, copy, rename and
delete now hold the same cross-process transaction locks over existence checks,
reads and writes. Two-file operations lock names in a stable order. This does
not make filesystem rename and the separate stable profile-identity registry one
cross-file transaction; the service must audit and reconcile that boundary.

`Config.task_delay` uses a separate freshly loaded scheduling model and writes
its next-run value through the merge protocol. It synchronizes only that saved
next-run value into the current model. It no longer replaces the entire running
model with newly edited target/team/device parameters.

The explicit Restart reset operation retains its existing effect on all nested
`next_run` fields. Its validated enable switch and schedule changes are now
persisted in one atomic write, and the operation reloads its resulting model
under the same transaction lock.

## API and audit boundary

The v2 adapter checks the exact file-content revision while holding the shared
lock. A stale revision returns `revision_conflict`. A stale cached model that
passes the outer revision check can still fail the field-level comparison; that
also returns a conflict rather than overwriting disk state. Legacy
`script_set_arg` callers keep their Boolean contract (`False` for rejected
validation/conflicts); v2 requests use `raise_conflicts=True` to distinguish them.

Edits validate a copied real Pydantic group before changing live fields. This is
important because the legacy `ConfigBase` constructor replaces some out-of-range
values with defaults. Invalid enum/range/interval values are rejected instead;
integer requests do not silently truncate a fractional number.

The adapter durably records `control.requested`, then saves a pending request
checkpoint before changing configuration. It then records `config.changed` and
`control.completed`, and finally saves the verified response checkpoint.
Request IDs are reused for retries; a verified response is replayed without
re-executing the edit or its side effects. Reuse for different input is rejected.
An unresolved prior intent requires reconciliation instead of blind replay.

Ordinary field differences carry old/new values. Differences protected by the
existing configuration redaction rules carry `[changed]` for both values. Audit
records identify the client source and request ID, without inventing a human
operator identity. Raw credentials are not put in the request checkpoint, event
store or WebSocket broadcast. An unchanged edit is explicitly marked unchanged.

If recording the intent fails, no configuration write occurs. If configuration
has been saved but its result cannot be recorded, the response explicitly says
`saved: true`, `persisted: false`, `status: executed_not_saved`. A filesystem error
with uncertain replacement outcome returns `needs_reconciliation`. These are
separate states from a rejected field.

MysteryShop's independent manual-run marker remains separate from ordinary
`next_run` editing: only the explicit `types=next_run` action with a due value
requests a manual run. `date_time` editing does not grant one. If that extra
marker fails after the configuration is saved, the response identifies a partial
execution and leaves the request unverified; it does not claim atomicity across
the two files.

## Verification

The tests use the actual `ConfigModel`, `Config`, `ConfigManager`,
`ManagerAdapter`, `RuntimeService` and file event store, with configurations and
runtime records exclusively in temporary directories. No game or emulator is
started. A subprocess barrier test exercises a genuinely separate stale writer.

Run with the packaged Python 3.10 interpreter:

```text
python -m unittest discover -s tests/solana -p test_config*.py -v
python -m unittest discover -s dev_tools -p test_config_save_validation.py -v
python -m unittest discover -s dev_tools -p test_mystery_shop_manual_run.py -v
```

While exercising real imports on Python 3.10, an existing GenericAlias issue was
found: `_is_model_type(list[...])` reached Pydantic's ABC `issubclass` hook and
raised `TypeError`, preventing a valid full-profile import. The helper now routes
typing containers to the existing container validator, and guards that subclass
check. Tests exercise valid full-profile/single-task import and preserve the
existing `ConfigNameError` contract for invalid names.
