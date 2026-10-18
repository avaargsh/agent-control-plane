# PR Watch — PostgreSQL versioned migration acceptance

Status: **operator-only Pilot tooling**, not a production schema rollout or
SQLite data importer. After PR Watch's PostgreSQL V2 API/Temporal/Telegram
cutover and multi-process crash tests, schema upgrades need durable provenance.

## Upgrade / inspect

Choose a **reviewed, backed-up, quiesced** PostgreSQL database. Apply only
outside HTTP / Temporal / Telegram request paths. Never point an integration
test DSN at a database with real user data.

```bash
python -m pip install -e '.[pg-pilot]'
export PR_WATCH_PG_DSN='postgresql://.../prwatch'
python examples/pr_watch_pg_migrations.py status
python examples/pr_watch_pg_migrations.py upgrade --to 2
python examples/pr_watch_pg_migrations.py status
```

`status` is strictly read-only; it does not create a ledger, migrate a
database, or automatically trust an unversioned schema. `upgrade --to 2`
atomically applies V1 then V2, creates a version/checksum ledger and checks
minimum required columns, keys and indexes. Subsequent calls do not modify
existing versions. A previously applied SQL checksum mismatch, missing table,
index or key causes a **fail-closed** error on startup.

Two participating installers are serialized by a PostgreSQL transaction-level
advisory lock. An 8s lock timeout is deliberate: a competing installation
should fail visibly rather than silently queue indefinitely. The advisory
lock does **not** stop the live application; quiesce writers and back up first.

## Adopt a legacy *complete* schema

Earlier PR #175/#176 versions installed `SCHEMA_V1` and `SCHEMA_V2`
without a durable migration ledger. Their existing rows **must not be
deleted** to introduce version tracking.

```bash
python examples/pr_watch_pg_migrations.py upgrade --to 2 --adopt-legacy
```

Adoption requires the **full expected V1 or V2 tables, columns, primary,
unique and foreign keys and indexes**. It stamps the known historical SQL
checksums transactionally, then upgrades if appropriate; Goal state and
UNKNOWN/ACKED launch evidence are preserved. An incomplete or ambiguous
unversioned schema is rejected for manual review—no `IF NOT EXISTS`
masking of missing columns. An unversioned database cannot start the newly
checked PostgreSQL V2 runtime until explicitly adopted. No data-only
SQLite-to-PostgreSQL import is included.

## Rollback policy

```bash
# ONLY a completely empty V2 pilot schema, after application shutdown:
python examples/pr_watch_pg_migrations.py rollback-empty-v2 --confirm-empty
```

This *physically* drops V2 tables and V2 Goal columns; it is allowed only
when **all** Pilot V1+V2 tables have zero rows. The process holds
`ACCESS EXCLUSIVE` table locks during the emptiness check and DDL in a
single PostgreSQL transaction so a writer cannot insert between checking
and dropping. It removes version 2 from the audit ledger but preserves V1.

**Any user data blocks destructive downgrade.** For populated databases,
back up and roll forward with a reviewed migration; do not delete events,
receipts or control journals to satisfy the empty guard. Schema changes
remain transactional: an injected SQL failure rolls back all new tables,
columns and ledger entries in the acceptance suite.

A runtime/application rollback is a separate compatibility decision: the
previous SQLite runtime cannot read PostgreSQL records and must not be
presented as a PG data rollback. Quiesce and verify any new binary/schema
compatibility independently.

## CI and evidence

```bash
# Disposable PostgreSQL 16 ONLY; tests create/drop own random schemas.
export PR_WATCH_PG_TEST_DSN='postgresql://.../DISPOSABLE_DATABASE'
python -m pytest -q tests/test_pr_watch_pg_migrations.py
```

On Python 3.11/3.12, CI verifies empty creation, both unversioned legacy
adoptions with data preservation, rejection of partial schema, competing
installers, SQL checksum/physical shape drift, injected mid-migration
failure with full DB rollback, empty-schema downgrade and re-upgrade, and
refusal to drop populated tables. The original PostgreSQL/Temporal/Telegram
integration and OS-process crash/fencing suites remain mandatory.

## Boundaries

- This migration layer does not yet have Alembic-managed external SQL
  manifests, independent production DDL code review, or PITR restore drills.
- Minimum keys/indexes checks do not prove an exhaustive equality of every
  SQL constraint expression or a full prior-version binary compatibility.
- No PostgreSQL server restart, HA replication, failover or OIDC/RBAC
  acceptance is claimed by these tests.
- ACP v0.1 exact-plan execution authorization remains untouched.
