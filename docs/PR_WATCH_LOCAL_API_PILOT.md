# PR Watch Harness — Authenticated Local API Pilot

**Status:** local loopback-only development pilot; **NOT** internet-facing,
enterprise OIDC/RBAC, production multi-tenant, or guaranteed live notification.

This layer extends the read-only PR Watch reference. It does not modify the
frozen ACP v0.1 authority chain, nor authorize GitHub writes or VM operations.

## Run it

```bash
python -m pip install -e '.[dev]'
mkdir -p .artifacts/pr-watch
export PR_WATCH_LOCAL_TOKENS_JSON="$(python -c 'import json,secrets; print(json.dumps({"demo":secrets.token_hex(24)}))')"
python -c 'import os,json; print("Local demo token:", json.loads(os.environ["PR_WATCH_LOCAL_TOKENS_JSON"])["demo"])'
# Keep this token in a local development terminal only; never commit it.
python examples/pr_watch_service.py --db .artifacts/pr-watch/watch.db --port 8792
```

Open `http://127.0.0.1:8792`. Obtain the local token from your development
secret setup and paste it into the input; it is held only in tab memory.
For easier manual setup, use your local secret manager to generate the JSON
string mapping user ID to a >=32-character random token. Do not share tokens.

**Important:** the HTTP process never fetches GitHub PR status itself.
When `PR_WATCH_TEMPORAL_ADDRESS` is configured, registering a Goal now
**automatically submits its persisted launch intent to Temporal**, while a
separate read-only Worker polls GitHub using the same stable database. When
Temporal is not configured, the API remains explicitly LOCAL_ONLY and
does not submit a Workflow. See `docs/PR_WATCH_AUTO_ENROLLMENT.md`.
The browser never triggers GitHub provider writes.

## API contract

Authenticated requests use `Authorization: Bearer <local-token>`. User scope
is **derived exclusively from server-side token configuration**, never request
body, URL, or browser-provided headers.

| Method | Route | Result |
|---|---|---|
| GET | `/api/goals` | Current user's goals (bounded 100) |
| GET | `/api/goals/{id}` | Scoped goal or 404 |
| POST | `/api/goals` | Register goal and, if configured, enroll Temporal Workflow |
| GET | `/api/goals/{id}/launch` | Scoped launch journal status |
| POST | `/api/goals/{id}/launch/reconcile` | Read-only Temporal existence check; never resends ambiguous start |
| POST | `/api/goals/{id}/pause` | CAS RUNNING -> PAUSED |
| POST | `/api/goals/{id}/resume` | CAS PAUSED -> RUNNING |
| POST | `/api/goals/{id}/cancel` | CAS RUNNING/PAUSED -> CANCELLED |
| POST | `/api/goals/{id}/controls/prepare` | Reserve a validated client request, without changing the Goal |
| GET | `/api/control-requests?limit=N` | User-scoped prepared/committed request journal (max 100) |
| GET | `/api/control-requests/{request_id}` | Exact user-scoped request status (PREPARED/COMMITTED) |
| GET | `/api/control-receipts?limit=N` | Bounded owner-scoped CAS-committed controls with `PENDING/UNKNOWN` Temporal signals (max 100) |
| GET | `/api/goals/{id}/controls/{version}` | Exact user-scoped durable control receipt or 404 |
| GET | `/api/goals/{id}/controls` | Latest 50 committed control records including stable receipt IDs |
| GET | `/api/events?after=N` | Durable scoped event cursor (bounded 100) |
| GET | `/api/events/stream?after=N` | One-shot SSE snapshot with IDs and retry hint |
| GET | `/healthz` | Unauthenticated local liveness only |

Lifecycle writes require `{"expected_version": 0}` or the latest observed
version; stale updates and invalid transitions return 409. Another user's
task resolves to 404. A task paused while a GitHub fetch was in flight
rejects the stale provider observation at the durable state update boundary.

`emitted=1` in the event list means transferred to AttentionOutbox, not
delivered to a recipient. The SSE endpoint is a short replayable snapshot,
**not** a long-lived push stream; the browser intentionally polls locally.
A production SSE consumer should reconnect using its last seen cursor.

## Local security model

- Explicit 127.0.0.1/localhost loopback binding; rejects 0.0.0.0.
- Single Authorization header; constant-time comparison of SHA-256 token
  digests with unique server-side identity bindings.
- Host allowlist reduces DNS rebinding risk; same-origin browser POST allowed,
  foreign Origin denied, no CORS access grants.
- Strict bounded JSON body; no tokens in URL, logs or browser storage.
- Small event and goal read limits; browser renders untrusted strings only as
  text nodes (no dynamic innerHTML).
- Event cursor is per-user filtered. It is **not** authorization or a shared
  event stream cursor across tenants.
- No automatic GitHub polling, provider writes, Sandbox allocation, or real
  email/SMS/mobile push in the HTTP process.
- A deployed service must replace local bearer tokens with an OIDC/issuer
  and audience verified session, canonical project grants, PostgreSQL RLS,
  TLS, rate limits, durable user audit, controlled token rotation, and
  tested revoke policy.

## Verification

```bash
pytest -q tests/test_pr_watch_service.py tests/test_pr_watch_harness.py
python -m ruff check examples/pr_watch_service.py
```

Integration tests use a real in-process loopback HTTP server, separate users,
cross-user 404, CAS conflicts, pause-during-observe, bad Origin/Host/path,
event replay, and public/static UI bootstrap without leaked credentials.

This pilot makes no claims about a real Temporal cluster, remote browser
session, external delivery confirmation, or operation ownership proof.

## Recover control history after reloading the pilot

After refreshing the page, reconnect with a local bearer token. The panel
reads `/api/control-receipts?limit=100` and displays committed controls
whose **Temporal signal** is still `PENDING` or `UNKNOWN`. The user can
open the owner-scoped Task control journal to inspect and (when appropriate)
explicitly reconcile the signal through the existing read-only workflow
history check. The bearer credential is never saved to browser storage.

A `receipt_id` is a deterministic fingerprint of the already-committed
`(user_id, goal_id, version, action)` record, not a user-generated
idempotency key, a secret credential, proof that a specific HTTP attempt
committed the record, a Temporal ACK, or verification of business success.
`cas_committed: true` only means that **this row exists in the durable
Goal/control transaction**. It never upgrades an observed terminal state to
verified execution. The bounded list is a snapshot, **not pagination or a
complete historical export**; precise records remain queryable by version.

**Important recovery limit:** if a client attempted an operation but no
request reached or committed on the server, no durable receipt exists. After
page reload the pilot cannot recover that absent client-side attempt. Even
with an existing matching CAS row, the server cannot attribute it to a
particular lost HTTP response. Do not retry a potentially committed control
blindly; examine the scoped Goal/version and control journal first.

Neither API endpoint introduces new DDL; SQLite and PostgreSQL use the
pre-existing CAS control tables, preserving the PostgreSQL v2 migration
checksum and authority boundaries. Public M6 GitHub webhook acceptance is
still a separate NO-GO gate.

## Explicit two-phase client request contract (experimental)

A new opt-in request key supports safely resuming control operations across a
browser refresh, without putting bearer tokens, operation IDs, or secrets in
browser storage. The client must authenticate again after refreshing.

1. Generate a cryptographically random 16-byte ID encoded as 32 lowercase
   hex digits (e.g. `crypto.randomUUID().replaceAll("-", "")`).
2. `POST /api/goals/{id}/controls/prepare` with JSON
   `{"request_id":"<32 hex>","action":"pause","expected_version":0}`.
   The server atomically checks the scoped Goal/version and persists a
   `PREPARED` intent, without applying the action or waking Temporal.
3. Only after the server acknowledges PREPARED, send the existing
   `POST /api/goals/{id}/pause` with
   `{"expected_version":0,"request_id":"<same 32 hex>"}`.
   Inside one database transaction, the server commits Goal CAS, the
   durable control journal entry, and marks the client request `COMMITTED`.
4. A repeat of **that exact request ID with the same owner, action, Goal
   and expected version** returns `replayed:true` and the existing control
   record. It never resends the Temporal wake. A different request ID or
   mismatched payload cannot impersonate that receipt.
5. After reload, authenticated `GET /api/control-requests` recovers
   PREPARED (not yet committed) and COMMITTED requests. A PREPARED record
   may need **explicit operator approval** to execute; its presence alone
   never proves that a previous HTTP POST reached the service. Distinct
   PREPARED requests can race; CAS still permits only one commit.

The legacy POST with only `expected_version` continues to use strict CAS
without an idempotent request key. No automatic retry of a missing ACK is
introduced. The browser interface has not yet been migrated to invoke the
two-phase path; this is an opt-in authenticated HTTP contract first.

### PostgreSQL installation gate

The existing schema v1/v2 migration scripts and checksums are unchanged.
Prepared controls require **explicit operator installation of v3** using
`python examples/pr_watch_pg_migrations.py upgrade --to 3` in a quiesced,
backed-up pilot database. On a v2 database the new prepared-request API
returns HTTP 501 rather than attempting request-time DDL; legacy control
APIs continue to operate. SQLite creates the additional request table with
the existing local pilot bootstrap. Runtime schema validation accepts v2 or
v3; the existing `rollback-empty-v2` command is not a V3 downgrade.

This remains loopback-only development functionality. Not production
authorization, proof of a Temporal ACK, ownership attestation, or
independently verified business success. Real external webhook M6 remains
NO-GO.
