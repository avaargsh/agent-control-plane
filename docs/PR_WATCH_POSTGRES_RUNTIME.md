# PR Watch: Explicit PostgreSQL Runtime Pilot

**Scope:** opt-in loopback-only pilot, using PostgreSQL V2 as the *only*
persistent state source for HTTP Goal APIs, Temporal observation Activities,
and Telegram delivery. This is not enterprise OIDC, automatic HA failover,
a production deployment, or a migration of existing SQLite user records.

The PostgreSQL V2 reference was added in PR #176 but had no service adapters.
This stage wires the adapters while preserving SQLite as the default mode.

## Select one backend per entire deployment

**PostgreSQL mode** requires the **same DSN** in API, Worker and Telegram
dispatcher and a separately running Temporal server:

```bash
python -m pip install -e '.[dev,pg-pilot]' 'temporalio>=1.8,<2'
export PR_WATCH_STORAGE=postgres
export PR_WATCH_PG_DSN='postgresql://prwatch:secret@127.0.0.1:5432/prwatch'
export PR_WATCH_TEMPORAL_ADDRESS=127.0.0.1:7233
export PR_WATCH_TEMPORAL_NAMESPACE=default
export PR_WATCH_LOCAL_TOKENS_JSON='{"demo":"replace-this-with-a-secure-random-40-character-token"}'
unset PR_WATCH_DB

# One-time reviewed pilot schema installation, never implicit on HTTP startup:
PYTHONPATH=examples python -c \
  'import os; from pr_watch_pg_store import PostgresWatchStore; PostgresWatchStore(os.environ["PR_WATCH_PG_DSN"]).install_v2()'

# Terminal A: Workflow Worker, same PR_WATCH_STORAGE and PR_WATCH_PG_DSN
python examples/pr_watch_temporal.py worker

# Terminal B: authenticated loopback API + Postgres launch fencing
python examples/pr_watch_service.py --port 8792

# Terminal C: one-shot Telegram sending, explicit operator choice
export PR_WATCH_TELEGRAM_BOT_TOKEN='...'
export PR_WATCH_TELEGRAM_RECIPIENTS_JSON='{"demo":123456789}'
python examples/attention_telegram_adapter.py --user demo --owner pg-telegram-1
```

**SQLite mode**, the default, continues with `--db` for HTTP/Telegram and
`PR_WATCH_DB` for Temporal Activity. In SQLite mode **do not set**
`PR_WATCH_PG_DSN`. In PostgreSQL mode **do not set**
`PR_WATCH_DB` or use `--db`. Conflicting settings fail closed,
not fallback to SQLite.

## Shared transactional model

- HTTP uses `PostgresWatchStore` for scoped Goals, CAS control intents,
  scoped event cursor and launch status. Workflow launch uses
  `PostgresLaunchDispatcher`: only the PG CAS winner may call Temporal.
- Temporal `read_and_persist_pr` selects PG by explicit environment. It
  uses a read-only provider outside a transaction; `tick` then atomically
  checks version and writes Goal phase, observation, Attention policy and
  delivery intent in **one PostgreSQL transaction**. Paused/finished Goals
  never access GitHub.
- Telegram sends are still **opt-in one-shot**, now through
  `PGTelegramOutbox`. It delegates to the PostgreSQL SKIP LOCKED claims
  and server-clock leases. Telegram provider receipt and lease completion
  commit atomically. Timeouts or uncertain ACKs become UNKNOWN and are not
  automatically retried. Provider ACCEPTED does not mean human read.
- Control Signal ACK and its read-only Temporal history reconciliation keep
  the existing semantics. A Signal is a wake hint, never workflow authority.
- PostgreSQL startup checks its V2 tables and Goal columns without creating
  any schema on reads. The startup sweep has a **bounded** set of launch
  candidates: truly PENDING starts can be attempted once, expired claims
  become UNKNOWN, and UNKNOWN records are only described, not resent.

## Verification

```bash
# Runs only against disposable PostgreSQL test DB; tests TRUNCATE pilot tables.
export PR_WATCH_PG_TEST_DSN='postgresql://.../DISPOSABLE_TEST_DB'
python -m pytest -q tests/test_pr_watch_pg_admission.py \
    tests/test_pr_watch_pg_store.py tests/test_pr_watch_pg_runtime.py
```

GitHub CI runs PostgreSQL 16 and the real Temporal SDK test server on
Python 3.11/3.12, alongside existing full-project tests. The integration
tests use loopback HTTP, independent scoped token bindings, Temporal
Start/Signal/Describe, PostgreSQL Activity observation, and a **mock**
Telegram provider accepting a receipt; **no live GitHub or Telegram send**.

## Deliberate remaining production gates

- SQLite -> PostgreSQL user-data migration with snapshot verification,
  rollback/compatibility tests and explicit cutover locking.
- Production schema migrations, connection pools, TLS and least-privilege
  credential management, backups/PITR.
- Multi-process restart and real Temporal server/DB failover chaos; bounded
  startup sweep is not a fleet reconciler.
- OIDC and project-scoped RBAC before any non-loopback/multi-user deployment.
- Real provider API acceptance under partial failures; missing ACK must not
  be presented as delivered.
- Resource/tenant quotas and rate limits.

The ACP v0.1 execution authority engine is untouched.
