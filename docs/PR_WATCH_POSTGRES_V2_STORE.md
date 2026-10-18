# PR Watch PostgreSQL v2 — Unified Persistence Reference

**Status:** real PostgreSQL reference store, deliberately **not switched on** in
the loopback HTTP pilot and not yet used by the live Temporal Activity. This
is the next safe migration step after the v1 PostgreSQL admission prototype.
It does not make the project production HA or multi-tenant authorized.

## New transaction model

A single PostgreSQL database now contains:

| Table | Invariant |
| --- | --- |
| `pr_watch_pg_goals` | Scoped Goal, phase, signature and optimistic version |
| `pr_watch_pg_launches` | Previous v1 fencing: admission / owner / generation |
| `pr_watch_pg_controls` | CAS control intent in the same transaction as lifecycle change |
| `pr_watch_pg_events` | Immutable observed PR change, scoped cursor |
| `pr_watch_pg_attention_decisions` | Policy decision made in the observation transaction |
| `pr_watch_pg_deliveries` | Durable deduplicated notification intent, scoped per incident |
| `pr_watch_pg_delivery_receipts` | Atomic acceptance proof for Telegram message ID |

Unlike the SQLite reference's observe → persist event → later drain into
AttentionOutbox flow, `PostgresWatchStore.observe` commits the **Goal CAS
observation, immutable event, Attention decision and any delivery intent in
the same transaction**. A crash during policy evaluation or inserting a
delivery rolls back all parts. There is no SQLite/PostgreSQL dual-write.

`PostgresWatchStore.tick` reads GitHub using a supplied read-only reader
outside the database transaction, and then locks the current Goal before
persisting. Pause, cancel, another observation or version change fences stale
results before any event or delivery is inserted. A finished or paused Goal
does not call the provider.

`transition` records Pause / Resume / Cancel state and matching versioned
control intent atomically. `record_control_signal` never downgrades ACKED
after a timeout. This reuses the same user/Goal/Temporal deterministic ID
used by PostgreSQL v1 launch admission.

## Notification evidence boundary

PostgreSQL notification claims use `FOR UPDATE SKIP LOCKED` and database
clock times to permit competing Worker processes to claim different PENDING
messages while **never reclaiming a CLAIMED or UNKNOWN item automatically**.
A lease timeout becomes UNKNOWN and cannot be retried without separate
independent verification.

`complete_delivery(... DELIVERED)` requires a valid Telegram provider
receipt identity and atomically CAS-updates the lease and inserts a receipt.
An invalid/missing receipt never marks delivery as confirmed. A duplicate
remote message ID causes the whole transaction to roll back, leaving the
delivery CLAIMED and eventually UNKNOWN rather than falsely DELIVERED.

These are **store and adapter boundaries only**: the existing Telegram
dispatcher remains bound to SQLite until the full service migration.
No actual Telegram send is performed by the new PostgreSQL tests.

## Operator-controlled install

Use a disposable PostgreSQL 16 DB. Explicitly install v1 then v2:

```bash
python -m pip install -e '.[dev,pg-pilot]' 'temporalio>=1.8,<2'
export PR_WATCH_PG_DSN='postgresql://.../disposable_prwatch'
PYTHONPATH=examples python -c \
  'import os; from pr_watch_pg_store import PostgresWatchStore; PostgresWatchStore(os.environ["PR_WATCH_PG_DSN"]).install_v2()'
```

This additive migration installs the new v2 tables and `phase`/
`signature` Goal columns without dropping existing PostgreSQL v1 launch
records. DDL is **not** invoked implicitly during HTTP requests. This POC is
not a reversible, production migration system; take backups and implement
reviewed versioned schema upgrades before real deployment.

Do **not** point `examples/pr_watch_service.py` at a PostgreSQL DSN or run
SQLite API replicas beside this store. The active API, Telegram delivery
adapter, and Temporal Workflow Activity remain SQLite-only and require a
coordinated store/provider switch after all interface contracts are ported
and acceptance-tested.

## Acceptance

CI runs Python 3.11 / 3.12 with disposable PostgreSQL 16 and a real
Temporal SDK time-skipping test server. The test file truncates the
disposable pilot test tables, **never run it against user data**.

```bash
export PR_WATCH_PG_TEST_DSN='postgresql://.../DISPOSABLE_TEST_DB'
python -m pytest -q tests/test_pr_watch_pg_admission.py tests/test_pr_watch_pg_store.py -x
```

Verification includes competing control requests; pause during a stale
provider read; immutable scoped event cursors; policy rollback; failure →
green → merge; quiet-hour notification scheduling; concurrent
`SKIP LOCKED` notification claims; stale/expired owner generation;
provider receipt idempotency/unique ID collision; tenant-scoped access; and
use of the same PostgreSQL Goal identity by the Temporal launch bridge.

## Remaining production gates

1. Bind authenticated HTTP and Temporal Worker to **only this PostgreSQL
   store**, with interface adapters that preserve exactly the same semantics
   and no mixed-mode fallback.
2. Replace the SQLite notification sender with the PostgreSQL claim/receipt
   adapter; test real provider acceptance and lost DB ACK separately.
3. Introduce versioned migration/rollback tooling, PostgreSQL TLS and DB
   credentials, pool limits, backups/PITR, schema-upgrade concurrency.
4. Complete OIDC and project-scoped authorization before multi-user rollout.
5. Validate multi-process API coordination, Workflow worker failover, Temporal
   server persistence, and DB leader failover. Passing this POC's integration
   tests does not establish those properties.
