# PR Watch — Explicit PostgreSQL Webhook Inbox Extension

**Status:** experimental opt-in PG16 pilot, stacked on
[PR #183](https://github.com/avaargsh/agent-control-plane/pull/183).
Not a production ingress, HA claim, or replacement for ACP authorization.

## Design and migration boundary

Existing PostgreSQL V1/V2 SQL and SHA-256 migration checksums remain frozen.
This optional feature has its own versioned schema ledger:
`pr_watch_pg_webhook_schema` (version 1, pinned DDL checksum),
and `pr_watch_pg_webhook_wakes` (durable per-Goal delivery journal).

No application startup or webhook request ever creates a PostgreSQL table.
After an operator has quiesced writers, reviewed backup/restore, and provisioned
an **isolated PostgreSQL 16 database**, install once:

```bash
export PR_WATCH_PG_DSN='postgresql://...'
PYTHONPATH=examples python examples/pr_watch_pg_webhook.py install
PYTHONPATH=examples python examples/pr_watch_pg_webhook.py validate
```

The command first requires the existing V2 migration ledger and shape,
then acquires an advisory transaction lock, installs the new inbox and its
checksum ledger atomically, or checks their exact shape/checksum if already
installed. Missing, partial, or drifted extension schemas are fail-closed.

This extension references the existing Goal table and adds a foreign key:
a rollback of V2 **must not proceed while the extension exists**. No
automatic destructive removal or downgrade is provided. Plan an explicit
offline backup, retention and restore procedure before any release.

## Local run

Register a real Goal using the **existing authenticated PostgreSQL PR Watch API**
and run the existing read-only Temporal Worker in PG mode. Then:

```bash
export PR_WATCH_STORAGE=postgres
export PR_WATCH_PG_DSN='postgresql://...'
export PR_WATCH_WEBHOOK_SECRET='<at-least-32-byte-secret-from-secret-manager>'
unset PR_WATCH_DB
python examples/pr_watch_webhook.py --user demo --goal review-168 \
  --temporal-address localhost:7233 --port 8793
```

Only `127.0.0.1:8793/webhooks/github` is exposed. Never bind this
reference server to public interfaces directly. A separately audited TLS,
signature-preserving ingress is required for live GitHub delivery. Do not
reuse this service's payload as a GitHub write, policy approval or task result.

HMAC-SHA256 validation and Goal scope matching are shared between SQLite
and PostgreSQL via `pr_watch_webhook_envelope.py`. PostgreSQL obtains a
row lock on the persisted Goal, checks the fixed owner/repo/PR binding and
durably claims `IN_FLIGHT` before exactly one attempted Temporal wake Signal.
The primary key `(user_id,goal_id,delivery)` serializes duplicate deliveries,
including simultaneous HTTP workers. Replaying an already claimed delivery
never resends the Signal; even after the Goal becomes terminal, the durable
receipt remains visible.

### CI completion wake

Subscribe to GitHub **Pull requests**, **Check runs** and **Check suites** events
on the webhook. The parser accepts PR lifecycle events and only
`check_run.completed` / `check_suite.completed` with an explicit
`pull_requests[].number` matching the already-bound Goal's PR. It never
trusts check names, branches, SHAs or conclusions as proof of CI success:
the Temporal Activity re-fetches current GitHub status using its own read-only
credential. Other repo/PR associations and check actions do not wake tasks.

GitHub may leave `pull_requests` empty for checks originating from forks.
Such events are intentionally ignored; the existing Temporal periodic polling
remains the fallback. No branch/commit heuristic is used to guess an
association. This is a delivery latency improvement, not guaranteed CI
event delivery or merge eligibility proof.

## Evidence and acknowledgement uncertainty

Both inbox implementations provide a trusted, **non-HTTP** `reconcile`
operation accepting a read-only `prove(signal_id)` callable. The production
pilot transport `TemporalWebhookWake.prove` reads the specific Workflow's
History and looks for the exact `wake` Signal payload. Only a **positive**
matching History event upgrades `UNKNOWN` or `IN_FLIGHT` to `ACKED`.
Missing/inaccessible/truncated History is uncertainty, **not** proof that
the Signal never arrived. There is no retry or automatically created Workflow.

After an operator verifies the bound Goal and the exact delivery UUID, perform
an **out-of-band, read-only** History reconciliation using the same configured
PostgreSQL DSN and webhook secret. The command updates only the existing
delivery's state when a matching History event is positively proven:

```bash
PYTHONPATH=examples python examples/pr_watch_pg_webhook.py reconcile \\
  --user demo --goal review-168 \\
  --delivery '00000000-0000-4000-8000-000000000001' \\
  --temporal-address localhost:7233
```

This UUID is an illustrative placeholder, not a real delivery; copy the
recorded delivery ID in actual operations. Neither missing Workflow History,
a stale or truncated History read nor a failed Temporal connection may cause
a retry. The CLI does not start a Workflow or emit another Signal.
Schema validation now checks precise column types, NOT NULL, the ordered
scoped primary key, Goal FK and state constraint shape; merely retaining the
same number of constraints is insufficient.

Verification:

```bash
PR_WATCH_PG_TEST_DSN='postgresql://.../disposable' \
  pytest -q tests/test_pr_watch_pg_webhook.py
pytest -q tests/test_pr_watch_webhook.py
```

GitHub Actions runs these against disposable PG16 on Python 3.11 and 3.12.
Unit tests use a fake Signal provider for ACK/crash and History predicates.
The stacked PR inherits #183's actual Temporal test-server integration;
that remains a fixture-only GitHub observation. Do not claim live GitHub
delivery, real production Temporal History retention, secret rotation, RLS,
HA, or cross-process exactly-once external RPC as proven.
