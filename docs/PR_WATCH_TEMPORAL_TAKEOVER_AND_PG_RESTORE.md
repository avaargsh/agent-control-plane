# PR Watch — Temporal Worker SIGKILL takeover and PostgreSQL logical restore

**Scope:** isolated PostgreSQL 16 and the Temporal SDK **time-skipping
test server**. This extends the prior real PostgreSQL service SIGKILL/WAL
acceptance (#180); it does not test a production Temporal server cluster.

## Worker death with durable Workflow state

The test spawns an **independent Python interpreter** that connects to
the Temporal SDK test server and polls the actual PR Watch task queue.
The fixture Activity commits an initial PR CI FAILURE observation into
PostgreSQL, and the parent waits for the Temporal Workflow History
`TIMER_STARTED` event. Then it kills Worker A with `SIGKILL` without
orderly SDK shutdown. The Temporal test server retains the original
Workflow ID and history.

A second Worker polls the same queue. An explicit persisted Temporal wake
Signal causes Workflow History replay and a **second Activity**, which
observes a fixture PR MERGED snapshot and commits it to PostgreSQL V2.
Expected acceptance:

- One Workflow started with the existing deterministic user/Goal ID.
- Worker A has durably committed the initial CI failure: exactly
  one Action Required event and one pending delivery before its death.
- Worker B completes with FINISHED/MERGED, **two total** Goal versions
  and **two total** immutable PR events, with no duplicate notification.
- Re-reading finished Goal cannot execute a provider fetch or emit a
  duplicate notification.
- The separate killed process is always reaped, even after test failure.

The test uses fixture Activities, **not the live GitHub API**. It proves
Worker process replacement and Temporal Workflow History replay **after a
committed Activity at the timer boundary**. It does **not** prove
in-flight Activity timeout redelivery after SIGKILL; the SDK time-skipping
test server cannot faithfully advance an outstanding externally held Activity
under that fault. That scenario requires a separately persisted Temporal
server and a real-time integration environment. Activities may execute more
than once and must use CAS/idempotency before persisting effects.

## Logical PostgreSQL backup and restore

The CI job **after** process takeover snapshots its disposable
`prwatch` database via the same PostgreSQL 16 service's `pg_dump`
custom archive, creates a separate `prwatch_restore_acceptance`
database, then runs `pg_restore --no-owner --no-acl`. It compares the
restored data and migration ledger to the source for:

- Goal + launch ownership and ACK state
- Versioned control intent and `UNKNOWN` evidence
- PR observation/event cursor
- Attention decisions and delivered Telegram provider receipt
- Durable sender and Workflow duplicate admission fencing

A committed write made **after the dump** is required to be absent from
the restored snapshot. The original database remains unmodified by
restoration. The test drops **only its newly created clone** afterward.

The destructive-capable Docker operations use the previous crash test's
fail-closed validator: GitHub Actions only, known service-container ID,
`postgres:16` image and exact disposable loopback DSN. No operator
server or arbitrary container may be targeted.

## Explicit limits

A logical `pg_dump/pg_restore` snapshot is **not PITR**. This stage
does not test WAL archiving, point-in-time recovery targets, backup
encryption, off-host retention, RPO/RTO SLOs or PostgreSQL HA promotion.
The Temporal test server is not a real persisted Temporal production
deployment, and its own storage failover has not been validated.
OIDC/RBAC and secrets/TLS hardening remain open before enterprise use.

No ACP v0.1 exact-plan execution authority components are changed.
