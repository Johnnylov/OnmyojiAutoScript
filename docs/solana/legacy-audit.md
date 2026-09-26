# Legacy client audit and rename recovery

Existing HTTP Boolean/list/object responses and text WebSocket commands remain
compatible. Mutating legacy handlers now use the same durable event store and
control core as v2. This is explicit handler integration: `ApiLoggingRoute`
provides request context and transport metadata, not a second v2 audit producer.

Covered actions are profile copy/import/rename/delete, task import/copy,
configuration-group copy, parameter writes, next-run synchronization, and HTTP
or WebSocket start/stop. Uploads are read and validated before entering the
synchronous configuration transaction; no `await` is held across file locks.

## Request identities and results

Clients may send `X-Request-ID` (UUID) and `X-Client-ID`. A completed repeated
request with matching operation/input returns its saved legacy response without
re-executing. Conflicting reuse returns 409. Clients without a request ID receive
a newly generated ID; this does not promise idempotence across distinct requests.
WebSocket clients can continue sending `start`/`stop`, or send an object such as
`{"type":"start","request_id":"...UUID..."}` for identified retries.

Audit source records client instance, HTTP/WS transport and request identity. It
does not claim an authenticated human identity. Uploaded filenames, raw query
values, raw request bodies and raw response/configuration bodies are excluded
from transport logs. Configuration audit contains bounded, redacted differences;
hidden values are represented by `[changed]`.

Ordinary configuration mutations wait for durable `control.requested` and a
pending request checkpoint before writing. A missing intent ACK prevents the
write. A completed mutation then records `config.changed`, `control.completed`
and its verified request result. An audit failure after the mutation leaves the
request pending, marks dispatch blocked and broadcasts `storage.degraded`.
The old response body remains compatible; HTTP adds
`X-OAS-Audit-State: executed_not_saved` and `X-Request-ID`. An ambiguous retry
requires reconciliation and does not automatically repeat side effects.

Stopping is a fault-protective action. It remains executable if durable history
or identity persistence is unavailable. The shared control core records a result
when possible; otherwise the new UI receives the degraded state and the HTTP
header clearly distinguishes execution from persistence. Invalid conflicting
request identities are not treated as storage failures.

## File rename and stable profile identity

Before moving a profile file, the journal retains the old/new names, original
file hash and original stable profile ID. File rename and identity-registry
replacement remain separate durable operations, so an interrupted operation
is not reported as an atomic cross-file transaction.

Startup must call this hook after constructing the service and **before** any
profile enumeration that can allocate IDs, API handling or process auto-start:

```python
from module.server.solana_legacy_audit import reconcile_legacy_operations
result = reconcile_legacy_operations(service)
```

The result contains `reconciled`, `unresolved`, `reconciled_count` and
`pending_count`. If only the destination exists and its hash matches the saved
source hash, the hook commits the original ID under the destination name. If
only the unchanged source exists, it confirms the rename was not applied.
Both files, unexpected content, or persistence failure remain unresolved and
block dispatch. The hook does not invent a new identity or repeat a rename.
Other incomplete mutation intents also remain explicit unresolved requests;
they require review rather than blind replay of configuration/manual-run effects.

## Reviewing pending operations

`list_pending_operations(service)` inventories `legacy_requests`, v2
`config_requests`, and shared control `requests`. Entries contain kind/key,
recorded profile ID, configuration names, action/field path, current SHA-256
revisions, explanation and `resolvable`; they contain no configuration values.
Intents that survived before their request checkpoint was written are rebuilt
from explicit durable name/profile/action/digest metadata. Historical records
without reliable names remain unresolvable; identity is not guessed from the
current registry. Incomplete bounded reads remain visible and block dispatch.

`resolve_pending_operation(service, kind, key, expected_revisions,
reviewed=True, request_id=uuid)` compares every involved file under the same
cross-process locks used by mutations. A changed revision returns 409. Acceptance
does not edit files, launch a process or repeat manual-run side effects. It
closes the original receipt with `unknown_state_accepted`, `executed: null`,
`historical_outcome: unknown`, and `current_state_verified: true`; terminal and
verified describe the reviewed current state, not historical execution success.
The original request then returns an explicit unknown result on retry.

Review intent, original closure and review completion use durable checkpoints.
An interrupted review remains in the inventory even if the original control
receipt was already terminalized. Its `resolution_request_id` must be reused to
finish that review. A completed retry is idempotent. Control receipts require
all executors to be stopped first. Conflicting rename files cannot be accepted
through this method: fix the files and retry the hash-based reconciliation hook.

Root recovery routes expose this inventory and acceptance action; storage
reconciliation must first rerun the rename hook, require an empty operation
inventory, and reject any remaining WAL pending-request count. Acceptance never
automatically restarts an executor or removes the separate storage dispatch fence.

## Verification

`tests/solana/test_legacy_audit.py` executes the real FastAPI handlers, real
configuration models, manager, event store and identity registry in temporary
directories. Only game-process actions are faked. The suite covers unchanged
legacy response shapes, retries, redaction, HTTP/WS controls, ordinary writes
blocked by storage failure, stops continuing despite that failure, and rename
recovery including a real child process killed with `os._exit` after the file
move and before the identity commit.

The bundled toolkit lacks `httpx`; use the isolated test dependencies without
changing the running installation:

```powershell
toolkit/python.exe -m pip install --target ../test-deps -r tests/solana/requirements-http.txt
$env:PYTHONPATH = (Resolve-Path ../test-deps).Path
toolkit/python.exe -m unittest discover -s tests/solana -p test_legacy_audit.py -v
```

Without the test dependency target these HTTP tests explicitly skip; a skip is
not an acceptance result. The old MysteryShop AST fixture remains focused on its
admission semantics; the real HTTP tests own audit-boundary verification.
