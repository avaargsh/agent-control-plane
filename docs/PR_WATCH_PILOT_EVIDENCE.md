# PR Watch #185 — Read-only pilot evidence audit

**Status: pre-production operator tool, not a release certificate.**

The combined GitHub PR Watch implementation is merged to main. Its automated
suite uses real PostgreSQL 16, HTTP and Temporal test-server operations, but
GitHub provider observations are fixtures. Issue
[#185](https://github.com/avaargsh/agent-control-plane/issues/185)
requires a **separate real GitHub delivery and operator GO/NO-GO**.

## Why this command exists

An `ACKED` webhook receipt proves only that an RPC returned successfully.
Even positive `wake` Signal History proves only that Temporal recorded the
Signal. Neither proves that GitHub emitted a real event, the activity processed
the *latest* GitHub state, that a particular event caused a specific state
change, or that an operator approved public ingress.

For **one configured canonical user, Goal and GitHub delivery UUID**, this
command checks the actual PostgreSQL V2 + inbox-extension schema and ledger,
reads the durable receipt, verifies the exact deterministic Signal identity,
and optionally looks up the specific Workflow History Signal **without
writing to the database or Temporal**. It returns a machine-readable report
whose release decision is deliberately always `NO_GO`.

## Operator usage

On an **isolated, operator-owned PR Watch staging environment**, after
explicit one-time V2 + inbox extension installation:

```bash
export PR_WATCH_STORAGE=postgres
export PR_WATCH_PG_DSN='postgresql://.../staging'
unset PR_WATCH_DB

PYTHONPATH=examples python examples/pr_watch_pilot_evidence.py \
  --user 'approved-user' --goal 'approved-goal' \
  --delivery '00000000-0000-4000-8000-000000000001' \
  --temporal-address 'localhost:7233' \
  > pr-watch-readiness.json
```

The UUID above is a placeholder and must be replaced with a **real**
GitHub delivery ID recorded in the authorized inbox. Do not place database
credentials, token values or webhook secrets in arguments, logs or tickets.
Only a hash of the canonical Goal scope, delivery ID and Workflow identity
is emitted (along with goal lifecycle/phase and counts). The raw webhook
payload, original owner/repo identifiers, credential and event summaries
are omitted.

The `--temporal-address` flag is optional; without it, the report says
`temporal_history=NOT_CHECKED`. A successful History scan says `PROVEN`
*only* for an exact `wake` Signal carrying the persisted deterministic
signal ID. No evidence -> `NOT_PROVEN`; Temporal errors ->
`UNAVAILABLE`. These are never safe-to-retry conclusions.

**This command does not perform a reconciliation write.** If an authorized
operator decides a positive proof warrants promoting an ambiguous receipt,
they can separately use the existing `pr_watch_pg_webhook.py reconcile`
command after normal change control. The read-only audit should remain
reproducible before and after reconciliation.

## What the evidence means

| Field | Proven | Not established |
| --- | --- | --- |
| `schema` | PG V2 + extension structural version/checksum accepted | Backups, permissions, HA |
| `inbox_state` | Current persisted scoped receipt state | Actual GitHub origin |
| `signal_identity` | Journal ID equals deterministic scoped Signal ID | Signal arrived remotely |
| `temporal_history=PROVEN` | Exact Signal exists in available Workflow History | Worker causality, external GitHub status |
| `recorded_observations` | Number of current Goal's persisted observation events | That an event was *caused* by this delivery |
| `release_decision=NO_GO` | Missing independent release attestation remains visible | Automatic authorization to deploy |

Any missing scoped Goal/delivery, invalid UUID, schema drift, or forged Signal
identity fails with a nonzero exit; it never silently creates tables, Goals,
Workflows, Signals or proof. `NO_GO` is a **fixed safety cap**, not a summary
that can be changed by editing an event body.

## Evidence to attach to issue #185

Capture the sanitized JSON plus the exact merged source SHA and CI run URLs.
A separate reviewer must compare GitHub's real delivery record with the inbox
delivery digest, inspect raw-body HMAC preservation through the TLS proxy,
review Temporal History retention and tenant authorization, observe
independent **live GitHub API** reads by the worker, and approve or reject
a documented runbook/rollback plan.

Do not set any checklist items in #185 to completed merely because this audit
reports a PostgreSQL receipt or `PROVEN` History. Use an isolated staging
environment, never a shared production DSN. No public binding, secret
provisioning or GitHub webhook registration is performed by this command.

## Test command

```bash
PR_WATCH_PG_TEST_DSN='postgresql://.../disposable' \
  pytest -q tests/test_pr_watch_pilot_evidence.py
```

The repository's PG16 workflow runs the audit tests under Python 3.11 and
3.12, including cross-scope missing evidence, malformed UUID, tampered
deterministic Signal identity, ambiguous receipts and unavailability.
