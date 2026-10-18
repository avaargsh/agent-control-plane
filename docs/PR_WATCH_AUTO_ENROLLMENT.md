# PR Watch — Durable Automatic Temporal Enrollment Pilot

**Scope:** experimental, single-node, loopback-only Harness service. Not ACP v0.1
execution authority, not OIDC/RBAC, not a production multi-node scheduler.

This change closes the gap between registering a Goal in the browser and
submitting its read-only monitoring Workflow to Temporal. No manual `start`
command is required **when** the trusted server and its Worker have been
configured.

## Lifecycle

```text
POST /api/goals (authenticated local user)
   |
   | SQLite BEGIN IMMEDIATE
   +--- INSERT Goal RUNNING and launch intent PENDING
   |                  [same transaction]
   +--- COMMIT
   |
   +--- CAS claim: PENDING -> IN_FLIGHT [only CAS winner sends]
   |
   +--- Temporal start_workflow(PRWatchWorkflow, deterministic WorkflowID,
   |                           REJECT_DUPLICATE)
         |              |                   |
         ACK          already exists        timeout / crash / lost ACK
         |              |                   |
        ACKED        describe workflow       UNKNOWN
                       |                       |
                     OBSERVED        read-only describe reconciliation
                                               |
                                 found -> OBSERVED / absent -> UNKNOWN

On service restart: abandoned IN_FLIGHT -> UNKNOWN, never blind resend.
Previously unsent PENDING -> one new start attempt (safe first issuance).
Terminal or cancelled Goal -> SKIPPED, no new Workflow creation.
```

The `pr_watch_launches` table is an append-independent one-row-per-user/goal
enrollment journal; its state is **separate** from the runtime Goal state
RUNNING/PAUSED/CANCELLED/FINISHED and Attention notification status.

- `PENDING`: registration durably committed, no caller has claimed start.
- `IN_FLIGHT`: a caller has exclusively claimed the *single* start attempt.
- `ACKED`: Temporal Start Workflow API acknowledged creation. Does not prove
  any Worker polled the task or GitHub was observed.
- `OBSERVED`: an authenticated server-side Temporal Describe returned an
  existing execution, with observed status (RUNNING, COMPLETED, etc.).
  An existing terminal Workflow is **not** an active watcher.
- `UNKNOWN`: start might have happened but cannot be confirmed. No automatic
  retry, even if the currently queried Workflow ID is not found.
- `SKIPPED`: Goal was already cancelled or terminal when enrollment was
  claimed. Paused Goals are allowed to start *idle* Workflows; `tick` will not
  contact GitHub until resumed.

Each Goal's Temporal WorkflowID remains
`pr-watch/<sha256((user_id, goal_id))>`, the same ID used by the existing
`wake` Signal bridge. Temporal rejects duplicate reuse, even when a
prior Workflow with that ID has completed; the prototype intentionally does
not silently create a new incarnation of a completed watcher.

## Configuration

Configure the local pilot API and the **separate Temporal Worker** against a
single-node stable SQLite database:

```bash
export PR_WATCH_DB=.artifacts/pr-watch/watch.db
export PR_WATCH_TEMPORAL_ADDRESS=127.0.0.1:7233
export PR_WATCH_TEMPORAL_NAMESPACE=default
export PR_WATCH_INTERVAL_SECONDS=300
export PR_WATCH_LOCAL_TOKENS_JSON='{"demo":"replace-with-a-random-token-longer-than-32-chars"}'

# Terminal A: requires a separately started Temporal development server
python examples/pr_watch_temporal.py worker

# Terminal B: new authenticated goals are now automatically enrolled
python examples/pr_watch_service.py --db "$PR_WATCH_DB" --port 8792
```

Open `http://127.0.0.1:8792` and register a Goal. The response
`{"goal":...,"launch":...}` reports the distinction between
registered and Temporal-accepted. In the absence of
`PR_WATCH_TEMPORAL_ADDRESS`, the service remains **explicit LOCAL_ONLY** and
does not send start requests; the PENDING journal stays ready for later safe
enrollment.

The service performs one **bounded startup sweep** (up to 100 records):
- `PENDING` records are safe to attempt once because no start claim existed.
- abandoned `IN_FLIGHT` are conservatively changed to `UNKNOWN`.
- `UNKNOWN` only attempts a read-only Temporal Describe; it is never
  automatically re-enrolled.

This crash-recovery sweep assumes **exclusive ownership of a single-node
pilot process**. Running two HTTP service instances against the same SQLite
file is unsupported. A 100-record bound is not a full fleet reconciler.
Production needs leader election/leases, a durable job dispatcher and
PostgreSQL before enabling multiple API replicas.

## HTTP API (user-scoped)

- `POST /api/goals`: register + attempt enrollment when bridge is enabled.
  HTTP 200 for ACKED / OBSERVED or explicit LOCAL_ONLY. HTTP 202 for uncertain
  or pending enrollment.
- `GET /api/goals/{id}/launch`: read durable enrollment state.
- `POST /api/goals/{id}/launch/reconcile` with `{}`: read-only Temporal
  Describe and persist positive existence evidence. **Never retries start.**
- Existing Pause/Resume/Cancel remains a version-fenced user-scoped Goal CAS
  with a separate persisted Signal intent. A Signal is a wake hint, not the
  source of truth for Goal lifecycle.

No HTTP caller can choose a Temporal address, namespace, WorkflowID,
task queue, or another user's identity.

## Failure acceptance

The test suite exercises exclusive concurrent admission, re-register without
duplicate Workflow starts, cross-user launch lookup, in-flight crash,
transport ACK loss, startup recovery, no proof of absence, cancelled/paused
goals, and an **actual Temporal SDK time-skipping server** for start/describe
and duplicate-Workflow-ID handling. It does **not** prove production Temporal
server persistence, real GitHub API observation or Telegram delivery.

```bash
python -m pip install -e '.[dev]' 'temporalio>=1.8,<2'
python -m pytest -q tests/test_pr_watch_launcher.py tests/test_pr_watch_service.py
```

## Production blockers

PostgreSQL persistence and transactional migrations; OIDC user and project
authorization; durable multi-instance start admission/leader election;
backfill/pagination for startup sweeping beyond 100 goals; Workflow versioning,
worker liveness and tenant quotas; reproducible process + Temporal server
restart tests; audited reconciliation and notification provider delivery
confirmation. Avoid interpreting an ACK as user value delivered.
