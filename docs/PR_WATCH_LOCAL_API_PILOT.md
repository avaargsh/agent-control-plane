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
# Copy your generated token into your secure local development environment
# rather than committing it; the command above does not print the token alone.
python examples/pr_watch_service.py --db .artifacts/pr-watch/watch.db --port 8792
```

Open `http://127.0.0.1:8792`. Obtain the local token from your development
secret setup and paste it into the input; it is held only in tab memory.
For easier manual setup, use your local secret manager to generate the JSON
string mapping user ID to a >=32-character random token. Do not share tokens.

**Important:** the pilot API itself does **not** schedule GitHub polling.
The separately configured read-only worker / Temporal service reads the same
stable database and advances goals. The browser only reads, registers,
pauses/resumes/cancels; it does not trigger GitHub provider effects.

## API contract

Authenticated requests use `Authorization: Bearer <local-token>`. User scope
is **derived exclusively from server-side token configuration**, never request
body, URL, or browser-provided headers.

| Method | Route | Result |
|---|---|---|
| GET | `/api/goals` | Current user's goals (bounded 100) |
| GET | `/api/goals/{id}` | Scoped goal or 404 |
| POST | `/api/goals` | Register goal by `goal_id`, `repo`, `number` |
| POST | `/api/goals/{id}/pause` | CAS RUNNING -> PAUSED |
| POST | `/api/goals/{id}/resume` | CAS PAUSED -> RUNNING |
| POST | `/api/goals/{id}/cancel` | CAS RUNNING/PAUSED -> CANCELLED |
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
