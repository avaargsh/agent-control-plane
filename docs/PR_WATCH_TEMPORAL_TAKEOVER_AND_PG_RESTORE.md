# PR Watch — Temporal Worker SIGKILL takeover and PostgreSQL logical restore

**Scope:** isolated PostgreSQL 16 and the Temporal SDK **time-skipping
test server**. This extends the prior real PostgreSQL service SIGKILL/WAL
acceptance (#180); it does not test a production Temporal server cluster.

## Worker death with durable Workflow state

The test spawns an **independent Python interpreter** that connects to
the Temporal SDK test server and polls the actual PR Watch task queue.
The test-owned activity records a local marker and remains blocked; the
parent kills that Worker process using `SIGKILL` without orderly SDK
shutdown. The Temporal server remains alive with the same Workflow ID
and durable history.

A second Worker polls the original queue. On Temporal Activity
redelivery after the original 45-second Activity execution timeout,
Worker B observes a mocked PR MERGED snapshot and commits the result
through the real PostgreSQL V2 store. Expected acceptance:

- One Workflow started with the existing deterministic user/Goal ID.
- No Goal version increment, event or outbox write from Worker A's
  **uncommitted** Activity.
- Worker B completes with FINISHED/MERGED, **one** Goal CAS version
  increment and **one** immutable PR event.
- Re-reading finished Goal cannot execute a provider fetch or emit a
  duplicate notification.
- The separate killed process is always reaped, even after test failure.

The test uses a fixture Activity, **not the live GitHub API**. It proves
Worker process replacement and Temporal Activity retry/History replay
with PostgreSQL persistence, not arbitrary exactly-once execution of
remote side effects. Activities can execute more than once and must use
CAS/idempotency before persisting effects.

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
