# PR Watch — PostgreSQL Admission Vertical Slice

**Status:** isolated, runnable PostgreSQL 16 reference. This is **not**
a drop-in replacement for the SQLite `GoalStore`, **not** deployed as the
HTTP API's backing database, and **not** an assertion that the existing
AttentionOutbox/Control Journal can already run across multiple replicas.

The preceding SQLite Pilot makes Goal registration and Temporal launch
atomic within a local process and SQLite file. To avoid dangerous split-brain
migration, this stage starts a standalone PostgreSQL-backed **Goal + launch
admission transaction** and exercises it with independent PostgreSQL sessions.
It does **not** wire the PostgreSQL admission table into the SQLite API.

## Why this order

The reference Harness still persists provider observations, notification
outbox, lifecycle controls and launch journal in one SQLite file. Swapping
only the launch journal for PostgreSQL would break atomic registration and
risk diverging Goal states. This slice proves the two-table registration
boundary and one-shot start safety in PostgreSQL first. A later migration
must move the rest of the store in one coherent transaction model before
adding multi-instance API pods.

## Install and run

```bash
python -m pip install -e '.[dev,pg-pilot]'
# Choose a disposable PostgreSQL 16 database with separate credentials.
export PR_WATCH_PG_DSN='postgresql://prwatch:secret@127.0.0.1:5432/prwatch'
python examples/pr_watch_pg_admission.py init
```

The `init` command installs **v1 pilot tables only**:
`pr_watch_pg_goals` (user-scoped registration, CAS lifecycle) and
`pr_watch_pg_launches` (shared admission and state/evidence). Schema creation
is never implicit in an API read or call. The DSN must stay in environment or
a secret manager; never commit it or log it.

## Correctness contract

- `register(user_id,goal_id,repo,number)`: one PostgreSQL transaction;
  same scoped identity is idempotent, rebinding the same ID to a different
  repository/PR is rejected. Creates PENDING launch in that transaction.
- `claim(owner,timeout_seconds)`: row-locks both Goal and launch; **only**
  the transaction transitioning PENDING→IN_FLIGHT reports `claimed_now=true`
  and may issue Temporal `start_workflow`.
- All claims use PostgreSQL server clock to store `claim_deadline`; no
  caller-controlled client clock enters admission decisions.
- `finalize(claim, ACKED/UNKNOWN)`: fenced by user, goal, owner, monotonic
  generation and unexpired deadline. An expired or stolen claim cannot
  overwrite the journal.
- `recover_expired()`: only expired IN_FLIGHT → UNKNOWN, never PENDING.
  It does not steal a valid in-flight claim on another instance.
- `observe_existing()`: accepts only a positive, trusted Temporal Describe
  status and upgrades to OBSERVED. A missing/failed Describe does not grant
  permission to start again.
- Paused Goals may start idle Workflows; cancelled/finished Goals are
  SKIPPED and never automatically restarted.
- A unique `(user_id,goal_id)` primary key and deterministic Temporal ID
  `pr-watch/<sha256(user_id,goal_id)>` protect both the local admission
  and Temporal service boundary; `REJECT_DUPLICATE` remains enabled.
- `PostgresLaunchDispatcher` reuses the trusted Temporal bridge's start and
  describe actions. Its example code makes at most **one attempted start**
  per successful PG claim; ambiguous outcomes can only be reconciled by
  read-only Describe.

## Run the acceptance suite

For a **disposable database only** — the tests `TRUNCATE` the two pilot
tables in the selected database:

```bash
python -m pip install -e '.[dev,pg-pilot]' 'temporalio>=1.8,<2'
export PR_WATCH_PG_TEST_DSN='postgresql://...DISPOSABLE...'
python -m pytest -q tests/test_pr_watch_pg_admission.py -x
```

GitHub Actions runs PostgreSQL 16 as an isolated service and Temporal's
real time-skipping test server on Python 3.11 and 3.12. Tests cover a
12-connection concurrent registration/claim race, stale owner/generation,
expired and live claims, scoped lookup, CAS conflicts, terminal goals,
duplicate workflow IDs, and real Temporal Start/Describe. No real GitHub,
Telegram or production database is used.

## Open production gates

1. Port and test the remaining Goal/Observation/Event/Attention Outbox and
   control-intent schema, in a single authoritative PostgreSQL model.
2. Introduce versioned, reviewed PostgreSQL migrations with compatibility
   and rollback verification (this v1 reference uses explicit DDL only).
3. Add tenant project authorization and OIDC, connection pooling, TLS and
   credential rotation. PostgreSQL's composite keys do not replace auth.
4. Add audited multi-replica API lifecycle, paginated reconciliation, quotas,
   backups/PITR, replica failover and fault injection.
5. Validate real Temporal server persistence and failover, in addition to
   the current isolated SDK test-server executions.

**Do not** point the current SQLite `pr_watch_service.py` at this PG DSN or
assume shared admission alone makes the end-to-end Harness multi-instance-safe.
