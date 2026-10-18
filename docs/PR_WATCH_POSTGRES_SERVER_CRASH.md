# PR Watch — physical PostgreSQL crash/WAL recovery gate

This acceptance is **deliberately destructive** and only runs on an isolated
GitHub Actions PostgreSQL 16 service. The test script refuses operation unless
`GITHUB_ACTIONS=true`, `CI=true`, an exact 64-hex-character service container
ID supplied through the GitHub `job.services.postgres.id` context,
`postgres:16` image and a disposable loopback `prwatch` DSN match. Never
run it against production databases or other Docker containers.

## What is tested

After an explicit, versioned V2 schema install by the existing PostgreSQL
acceptance tests, the crash test commits these independently:

- A Goal with Temporal start ACKED.
- An immutable Pause version and `UNKNOWN` Signal journal intent.
- An uncertain Workflow start journal (`UNKNOWN`).
- A committed Workflow launch still `IN_FLIGHT` with a short deadline.
- A persistent PR failure event, Attention decision and CLAIMED delivery.
- A separate Telegram **simulated** confirmed receipt with DELIVERED.
- An intentionally **uncommitted** Goal plus corresponding launch in one
  PostgreSQL client transaction.

A real local HTTP API stays alive and initially reports
`GET /healthz → 200`, `GET /readyz → 200`.
CI then issues `docker kill --signal=SIGKILL` against **only the validated
job-owned PostgreSQL service container**, not `docker stop` (which performs
a graceful shutdown). During the outage, liveness remains 200, while
database-dependent readiness must return a sanitized 503 without exposing
the DSN, SQL driver error or credentials.

The **same** container is restarted with `docker start`, and the script
requires the PostgreSQL postmaster start time to advance. This exercises
actual PostgreSQL WAL crash recovery on the container's preserved storage.
Once ready:

- The API returns `/readyz → 200`, and Goal queries work **without
  restarting the API**.
- The previously committed Goal/Launch/Control/Event/Receipt state remains
  unchanged across server restart.
- The open/uncommitted transaction is entirely rolled back, with neither
  the Goal nor its launch present.
- Expired `IN_FLIGHT` launches and CLAIMED deliveries become `UNKNOWN`
  and never become PENDING or eligible for blind resend.
- Already UNKNOWN records remain UNKNOWN. Scoped duplicate registration
  remains idempotent.

This stage adds `/readyz` only in the local development Pilot. `/healthz`
remains liveness; readiness is a PostgreSQL schema-version validation when
the PostgreSQL mode is selected. The endpoint has no user data. A failed
database connection is reported only as `{"status":"unavailable"}`.

## CI execution

The existing `pr-watch-postgres-admission` workflow runs all previous
PostgreSQL/Temporal integration and OS-process SIGTERM tests **before** the
physical server crash step, in each Python 3.11 and 3.12 matrix job. The job
service is fresh and disposable; the crash step runs last and never deletes
its database files. The script uses the exact service-container ID provided
by GitHub Actions, refuses any arbitrary target, and starts the same
container again even if a test assertion fails.

The recovery assertion is bounded and does not mask an unavailable database
as healthy.

## Explicit remaining production gates

- A single service container WAL restart is not a PostgreSQL primary-replica
  switchover, automatic failover, synchronous replica validation or PITR.
- No live Temporal Server crash, history replay across Temporal persistence
  failover or actual Worker process adoption is tested in this step.
- No real Telegram send or GitHub API call is made in the crash test.
- A failure during an *external side effect* is still UNKNOWN. The DB cannot
  prove that the provider did not accept an operation.
- OIDC/RBAC, TLS, secret rotation, pooling, backup/PITR, and full production
  deploy readiness remain open.
- ACP v0.1 exact-plan authority is unchanged.
