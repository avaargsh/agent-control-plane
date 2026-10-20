# PR Watch: Local API -> Temporal Control Journal (experimental)

**Status:** local loopback-only Harness prototype. This does NOT make ACP's
execution attestation/authority core a Temporal runtime and does NOT establish
enterprise OIDC, cluster failover, or production user authorization.

## Goal

Make Pause / Resume / Cancel user controls durable **before** attempting
a Temporal wake Signal. A lost Signal ACK must not roll back the requested
goal state, turn a canceled task back into RUNNING, or trigger blind retries.

```text
Browser -> authenticated local API (server-bound user)
      -> GoalStore SQLite BEGIN IMMEDIATE
      -> CAS version / lifecycle transition
      -> INSERT pr_watch_control_intents (same transaction)
      -> COMMIT
      -> Temporal get_workflow_handle(deterministic_id).signal("wake", control_id)
             |                     |                   |
           ACKED               NO_WORKFLOW            UNKNOWN
         persisted          local goal stands      no blind resend
             \____________________|___________________/
                                  |
                trusted user can request HISTORY RECONCILE
                                  |
                   Signal event with matching control_id?
                         yes => ACKED / no => unchanged
```

The Workflow's Signal only increments a wake counter and causes its
read-only Activity to re-read the durable GoalStore state. It does not
authorize GitHub write operations, create missing workflows, create sandboxes
or modify the ACP exact-plan authority system.

## Configuration

The API still derives the user exclusively from a server-side local token map.
Temporal connection configuration is trusted **server environment**, never
HTTP user input.

```bash
# After starting a trusted local Temporal dev server and its worker
# (see docs/PR_WATCH_TEMPORAL_E2E.md):
export PR_WATCH_TEMPORAL_ADDRESS='127.0.0.1:7233'
export PR_WATCH_TEMPORAL_NAMESPACE='default'
export PR_WATCH_LOCAL_TOKENS_JSON='{"demo":"replace-with-40-character-local-secret-token"}'
python examples/pr_watch_service.py --db .artifacts/pr-watch/watch.db --port 8792
```

Without `PR_WATCH_TEMPORAL_ADDRESS`, the API continues in explicit
`LOCAL_ONLY` mode. It does not silently claim to have notified Temporal.

When `PR_WATCH_TEMPORAL_ADDRESS` is configured, registering a Goal now
**automatically submits a durably recorded startup request** to Temporal,
under the deterministic `pr-watch/<sha256(user_id,goal_id)>` Workflow ID.
The Worker must still run separately and share the same stable SQLite DB
(single-node pilot only). See `docs/PR_WATCH_AUTO_ENROLLMENT.md` for
ACK/UNKNOWN and crash recovery semantics. If no active Workflow is attached,
a control Signal can still return `NO_WORKFLOW`; the durable Goal CAS remains
authoritative.

## HTTP contract

Existing POST action path:
`/api/goals/{goal_id}/pause|resume|cancel`
with JSON `{"expected_version": N}`. Calls are authenticated and keep the
existing scoped CAS and lifecycle restrictions.

New response:
```json
{
  "goal": {"user_id":"demo","goal_id":"pr-169","state":"PAUSED","version":1},
  "control": {
    "user_id":"demo","goal_id":"pr-169","version":1,
    "action":"pause","signal_state":"ACKED","evidence":"SIGNAL_ACK"
  }
}
```
(The illustration omits other Goal fields.) HTTP 200 only means the Signal
RPC was acknowledged. It does **not** mean the Worker executed the action or
the provider operation completed. HTTP 202 is used for `PENDING`,
`UNKNOWN`, `NO_WORKFLOW`, or `LOCAL_ONLY`, all with committed CAS state.

Read history (authenticated and per-user):
- `GET /api/goals/{goal_id}/controls`: latest 50 control intents.
- `POST /api/goals/{goal_id}/controls/{version}/reconcile` with `{}`:
  fetch Temporal Workflow history **read-only**. Matching Signal event
  with the exact deterministic control identity proves server acceptance.
  If no matching event is available, status stays unchanged. **Never
  interpret history absence as proof a Signal was not received.**

Control intents are stored in the same SQLite transaction as the Goal
version transition; replaying the original expected_version receives 409,
not a duplicate control or a new Signal. Once an ACK is observed, later
transport failures cannot demote it to UNKNOWN.

## Fault boundaries

- API client gets disconnected after CAS: caller can query current Goal and
  controls; repeating the stale CAS must fail with 409.
- TCP/RPC error while sending: `UNKNOWN`. No automatic resend.
- Response lost after Temporal persisted Signal: explicit read-only History
  reconciliation can upgrade UNKNOWN to ACKED.
- Failure after RPC success but before local signal_state journal update:
  `PENDING`; original CAS remains durable. Reconciliation can prove ACK.
- Signal arrives during Activity: the Workflow's wake generation detects it
  and rechecks durable state. The Activity's CAS checks still fence in-flight
  observations after pause or cancel.
- Signal not found (no running Workflow): `NO_WORKFLOW`, no implicit start.
- Continue-as-new or missing history can prevent reconciliation proof;
  this remains UNKNOWN, not permission to infer an absent signal.
- This single-node SQLite pilot does not provide an out-of-process control
  dispatcher or fault-tolerant Temporal server. Poll timers remain the
  fallback if a Signal isn't observed.

## Verification

```bash
python -m pip install -e '.[dev]' 'temporalio>=1.8,<2'
pytest -q tests/test_pr_watch_service.py tests/test_pr_watch_control_bridge.py
pytest -q tests/test_pr_watch_temporal_e2e.py
ruff check examples/pr_watch_harness.py examples/pr_watch_service.py \
  examples/pr_watch_control_bridge.py examples/pr_watch_temporal.py
```

GitHub CI runs Python 3.11 and 3.12, real Temporal time-skipping server
tests for signal history, lost ACK and nonexistent workflow, plus local
HTTP auth/tenant/version tests. These are test-server results, not a
production Temporal deployment nor live GitHub provider acceptance.


## Crash-safe first Signal attempt for new keyed CAS (opt-in pilot)

A control CAS and its Temporal RPC can never be one atomic PostgreSQL/
Temporal transaction. Under the previous bridge, two concurrent API servers
could both observe `PENDING` and attempt to Signal, and a crash after
an accepted Signal but before persisting ACK could leave `PENDING`.

The upgraded bridge uses the shared durable control journal as a one-way
send claim:

1. A **keyed** `transition_with_request` transaction writes the Goal CAS,
   Control Journal and COMMITTED request with
   `signal_state=PENDING, evidence=UNATTEMPTED_V3`. This marker is *only*
   written for the new keyed path; older/legacy `PENDING` records lack
   sufficient evidence to infer they have never been sent.
2. Before the network call, every upgraded API sender atomically claims
   `PENDING -> UNKNOWN, evidence=ACK_UNCERTAIN` with SQLite
   `BEGIN IMMEDIATE` or PostgreSQL conditional `UPDATE .. RETURNING`.
   Only the transaction winner makes the Temporal RPC; other senders
   return the existing record without sending.
3. RPC acceptance records ACKED, explicit not-found records NO_WORKFLOW,
   and ambiguous failures remain UNKNOWN. If the process crashes **after
   the claim**, UNKNOWN is persistent even if it never actually issued
   the RPC. Absence from Temporal History **never** authorizes a second
   network Signal.
4. If the process crashes **after keyed Goal CAS but before the claim**,
   a trusted local-token user may inspect the owner's Control Journal and
   explicitly click **Dispatch unattempted signal** (or call authenticated
   `POST /api/goals/{id}/controls/{version}/dispatch-unattempted`
   with `{}`). The API accepts only the untouched `UNATTEMPTED_V3`
   marker and a configured trusted Temporal bridge. The same atomic
   claim fences two concurrent recoveries.

No auto-scan/retry is run on startup. Old PENDING, UNKNOWN and other
states can still request existing **read-only History reconciliation**,
but can never use this new explicit dispatch endpoint. There is no
automatic resend after ACK loss. The existing Workflow wake remains a
hint to re-read durable Goal state, **not** execution proof.

**Deployment constraint:** drain older pilot API instances before enabling
this recovery path. A mixed deployment with an unupgraded sender that can
send without a journal claim does not satisfy the at-most-one-attempt
contract. This is loopback pilot behavior, not a general distributed
Exactly-Once guarantee or production authorization. M6 external webhook
acceptance remains NO-GO.
