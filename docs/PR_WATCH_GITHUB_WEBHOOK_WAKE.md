# Signed GitHub Webhook Wake — PR Watch Pilot

**Status:** opt-in, SQLite-only, **loopback-only** reference. This is not a
public webhook service, not enterprise multi-tenancy, and not an ACP v0.1
authorization grant. It never writes to GitHub.

## What it does

An operator configures **one** existing, immutable `(user_id, goal_id)`
binding. The receiver looks up the bound Goal from the same durable SQLite
file used by PR Watch. Only a validated GitHub `pull_request` event with an
exactly matching repository and PR number may wake that existing RUNNING
Goal. The event is a hint: the Temporal Activity must still fetch current
GitHub PR/CI/review status independently, re-read durable Goal state, and
apply existing CAS and attention decisions.

1. Reject non-loopback HTTP listeners, bad Host/Origin, duplicate headers,
   unsupported encoding, oversized bodies (>256 KiB), invalid delivery UUID
   or incorrect `X-Hub-Signature-256` over the **raw request body**.
2. Accept only an allowlisted pull request lifecycle action. Never parse PR
   prose into instructions and never use payload data to choose a user/Goal.
3. Before sending, write a scoped `IN_FLIGHT` delivery claim into a durable
   SQLite table with UNIQUE `(user_id,goal_id,delivery)`. Only save the
   SHA-256 digest, deterministic Signal ID and outcome, not the webhook body.
4. Send **one** Temporal `wake` Signal against the already existing
   deterministic Workflow ID. A successful RPC records `ACKED`; a failed or
   ambiguous RPC records `UNKNOWN`. Crash after claim leaves `IN_FLIGHT`.
5. Redeliveries with the same signed body return the existing state **without
   resending**; reused delivery ID with different signed body returns 409.
   `UNKNOWN`/`IN_FLIGHT` require read-only operator reconciliation with
   Temporal History. A missing Signal in a finite History read does not
   authorize resending; regular Temporal timers remain the fallback.

This is *at-most-one local Signal attempt per delivery*, not exactly-once
GitHub event processing, not guaranteed wake delivery, and not proof that the
PR operation succeeded.

## Run locally

First, register a PR Watch Goal through the existing local API, and start its
Temporal Worker with the same persistent SQLite DB as described in
`PR_WATCH_LOCAL_API_PILOT.md`. Use a GitHub webhook secret from your local
secret manager; do not store it in the repo or provide it on the command line.

```bash
export PR_WATCH_WEBHOOK_SECRET='<secret-at-least-32-bytes>'
python examples/pr_watch_webhook.py \
  --db .artifacts/pr-watch/watch.db \
  --user demo --goal review-168 \
  --temporal-address localhost:7233 --port 8793
```

It listens only on `127.0.0.1:8793/webhooks/github`. Configure GitHub to
send **Pull requests** events with content type `application/json` and the
same secret. GitHub cannot directly reach a loopback address: expose the
single endpoint only through a separately authenticated/managed, TLS
terminating ingress or a carefully configured development tunnel. Protect
the endpoint with bounded request limits, IP/network rules and operational
monitoring. Do **not** forward arbitrary endpoints to the local API. Keep
GitHub PATs entirely in the read-only Worker; the receiver needs no PAT.

The receiver does not register new Goals or start Temporal Workflows. If
the bound Goal is PAUSED/CANCELLED/FINISHED, or the event targets another
PR, it is ignored. The pilot is **not** available in PostgreSQL mode;
do not mix its SQLite journal with PostgreSQL's authoritative PR Watch store.

## Verification

```bash
python -m pytest -q tests/test_pr_watch_webhook.py
python -m pytest -q tests/test_pr_watch_webhook_temporal_e2e.py
python -m ruff check examples/pr_watch_webhook.py tests/test_pr_watch_webhook.py tests/test_pr_watch_webhook_temporal_e2e.py
```

Offline tests use a real loopback HTTP server, durable SQLite Goal/journal,
a deterministic fake Signal transport, crashes and concurrency. The separate
Temporal integration test uses a real SDK time-skipping server, Workflow,
Signal, SQLite journal and Activity with **fixture GitHub observations**.
It does **not** establish live GitHub webhook delivery, a production Temporal
cluster, cross-process provider exactly-once or HA deployment. Operator
reconciliation and a PostgreSQL inbox/migration are separate follow-up work;
do not claim those exist in this version.
