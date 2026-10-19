# PR Watch #185 — real Nginx TLS staging ingress contract

**Scope:** opt-in, localhost-only test fixture for a signed Webhook
terminating TLS at an Nginx reverse proxy and forwarding to the existing
loopback PR Watch Webhook receiver. Not a ready-to-deploy public reverse
proxy, production release evidence or GitHub delivery acceptance.

See [issue #185](https://github.com/avaargsh/agent-control-plane/issues/185).
The template is `examples/pr_watch_nginx_staging.conf.template`.

## Threat model and trusted handoff

GitHub sends an HMAC-SHA256 signed raw JSON POST with:
`X-Hub-Signature-256`, `X-GitHub-Event`, and `X-GitHub-Delivery`.
The signature binds **exact request-body bytes**, not a parsed or
reformatted JSON object. Nginx buffers, but must not rewrite the body.
The downstream receiver independently validates HMAC, exact repository/PR
binding, UUID, event type, size and Goal state. Even a valid delivery is
**only a scheduling hint**; current GitHub PR and CI state is fetched
separately by the Worker using read-only credentials.

The reference application deliberately requires a loopback Host header
matching its **own port**. The template overwrites `Host` with its
fixed local backend value; it does not pass caller-supplied
`Forwarded`/`X-Forwarded-*` data. Nginx also denies paths other than
`/webhooks/github`, non-POST methods, supplied Origin, unexpected
client Host, and client `Transfer-Encoding`. It limits request bodies to
256 KiB, disables request access logging, has bounded upstream timeouts
and `proxy_next_upstream off` to avoid automatic retries of an
ambiguous Signal attempt. Duplicate deliveries still rely on the durable
SQLite/PG Inbox.

These constraints **reduce** staging ingress risks; they do not establish
that a public deployment is secure.

## Local disposable acceptance

The CI job `pr-watch-tls-ingress-staging` installs **real Nginx and
OpenSSL** on an isolated GitHub runner. On Python 3.11 and 3.12 it:

1. creates a one-day local self-signed certificate for localhost;
2. starts the existing real Python Webhook server on 127.0.0.1;
3. renders Nginx with test-owned absolute temp paths and localhost ports,
   verifies the config with `nginx -t`, then starts actual HTTPS;
4. posts whitespace-sensitive signed JSON through real TLS and asserts
   that PostgreSQL/SQLite-independent HMAC validation preserved the exact
   raw body digest, with one wake on replay;
5. checks invalid HMAC, wrong repository/PR, reused delivery UUID with
   changed body, denied non-webhook routes/methods, foreign Host/Origin,
   and payload larger than 256 KiB;
6. checks a signed `check_run.completed` hint creates a wake but cannot
   by itself rewrite the durable observed CI phase.

The standalone Nginx fixture above uses **SQLite** for its local Inbox.
The `pr-watch-postgres-admission` CI additionally runs this **same real
localhost TLS frontend against real PostgreSQL 16 and a real Temporal SDK
test-server**: authenticated Goal registration -> Nginx HTTPS ->
HMAC-bound Webhook -> PostgreSQL Inbox -> existing Temporal Workflow
Signal -> independent fixture-based PR observation -> persisted events ->
user-scoped API and positive History audit. Both normal ACK and deliberately
lost ACK with replay/History reconciliation run in **direct HTTP and TLS**
modes. Neither mode treats the signed event's `conclusion` as authoritative
CI evidence. The resulting audit must still report `NO_GO`.

This combined test closes the *composition* gap; it is not real GitHub
Webhook delivery, a deployed public ingress, or a production Temporal
cluster. Those release gates remain open in #185.

To repeat on a disposable Ubuntu machine with Nginx and OpenSSL installed:

```bash
python -m pip install -e '.[dev]'
python -m pytest -vv tests/test_pr_watch_tls_ingress.py
```

## Operator trial — still local only

Only in an explicitly authorized staging host:

- Install a locally trusted TLS certificate and key; do **not** commit
  credentials, private certificates, webhook secrets or DB URLs.
- Copy the template outside the repository. Replace all `@@...@@`
  placeholders with reviewed absolute paths and numerical loopback
  ports. Verify Nginx configuration with `nginx -t -c <absolute-path>`.
- Keep the listener `listen 127.0.0.1:... ssl` and backend loopback-only.
  Start Nginx only after reviewing its effective configuration. Do not
  change the listen address to a public interface as part of this task.
- Start the existing Webhook receiver on the fixed private loopback port,
  explicitly configured for a single trusted user/Goal/repo/PR and a
  sufficiently long secret. Keep `PR_WATCH_STORAGE=postgres` and install
  the independent PG extension separately if testing the PG pilot.
- Run the local Nginx TLS acceptance, then verify that the upstream
  event receipt and History audit remain independently observable and
  never contain raw provider instructions.

## Remaining issue #185 release gates

The combined local PG16/Nginx/Temporal test is **not** an operator signoff.

A real GitHub Webhook delivery from a disposable repository, proper
externally validated TLS certificate/DNS configuration, perimeter network
rules, GitHub configuration and secret rotation, tenant and rate-limit
security review, production-grade failure and backup rehearsals, and
independent operator GO/NO-GO **remain unverified**. There is no automatic
GitHub Webhook registration and the tested TLS frontend is intentionally
not publicly reachable.

Reference: [NGINX proxy module](https://nginx.org/en/docs/http/ngx_http_proxy_module.html),
[NGINX SSL module](https://nginx.org/en/docs/http/ngx_http_ssl_module.html).
