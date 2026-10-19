# PR Watch #185 — GitHub official Webhook delivery corroboration

**Pre-production, read-only.** This tool correlates a real delivery record
returned by **GitHub's repository webhook deliveries API** with a scoped
PostgreSQL Webhook Inbox receipt and optional Temporal Workflow History.
It does not create, update, ping or redeliver a GitHub webhook.

Existing local integration tests already exercise Nginx TLS → PG16 Inbox →
Temporal SDK → event API. The missing independent observation is **GitHub's
own record that it sent a specific event to the configured endpoint**.

## Requirements / permissions

- Isolated operator-owned staging, explicit PostgreSQL V2 + webhook extension
  installed, an existing scoped Goal and a recorded real delivery GUID.
- The repository Hook ID and its **numeric delivery ID** from GitHub
  Settings → Webhooks → Recent Deliveries. Delivery GUID and numeric
  delivery ID are **different** and both are required.
- A separate, short-lived, operator-supplied read-only token, with fine-grained
  **Webhooks: read** permission for that specific repository. The tool calls
  only `GET /repos/{owner}/{repo}` and
  `GET /repos/{owner}/{repo}/hooks/{hook_id}/deliveries/{delivery_id}`.
  The token must additionally be allowed to read repository metadata.
- Exact HTTPS destination URL (e.g. `https://staging.example.org/webhooks/github`),
  reviewed independently from the API response. No URL credentials, fragments
  or query strings. Never provide a production URL merely to satisfy tests.
- Optional Temporal namespace/address reachable by the operator.

The tool discovers the bound repo/PR solely from the **persisted Goal**
selected by `--user` and `--goal`, not the GitHub JSON body or a caller-
provided repository selector.

## Local operator command

```bash
export PR_WATCH_STORAGE=postgres
export PR_WATCH_PG_DSN='postgresql://.../isolated'
export PR_WATCH_GITHUB_HOOK_READ_TOKEN='<ephemeral Webhooks-read token>'
unset PR_WATCH_DB

PYTHONPATH=examples python examples/pr_watch_github_delivery_evidence.py \
  --user 'approved-user' --goal 'approved-goal' \
  --delivery '00000000-0000-4000-8000-000000000001' \
  --hook-id 12345 --github-delivery-id 987654 \
  --expected-endpoint 'https://staging.example.org/webhooks/github' \
  --temporal-address 'localhost:7233' > github-delivery-evidence.json
```

**All IDs and endpoint above are placeholders**. This command must never
be run against shared production credentials without explicit approval.
Do not print the token, DSN, request payload or server response payload.
Ensure the resulting JSON is reviewed before sharing outside the staging
team (hashes are pseudonymous, not anonymized).

## Evidence checks

- Exact GitHub repository **numeric ID** and full name from the independently
  fetched repository metadata, matched against delivery `repository_id` and
  payload repository identity.
- Exact Hook ID, numeric GitHub delivery ID, canonical delivery GUID, request
  headers, allowed event/action, previously bound PR number or check association,
  and independently supplied HTTPS destination.
- GitHub observed an HTTP 202 `accepted` or 200 `duplicate` with the
  expected receiver JSON acknowledgement, rather than a 2xx `ignored` hint.
- The same GUID exists in the scoped PostgreSQL Inbox. Its SHA-256 payload
  digest is well formed and its persisted Signal ID is precisely deterministic.
  Optional real Temporal History yields `PROVEN` only after an exact Signal.
- No automatic retry after `UNKNOWN`, no Signal mutation, no DDL, no GitHub
  webhook write. HTTP client targets **only api.github.com** with GET, refuses
  redirects and bounds responses to 2 MiB.

### Critical limitation: parsed JSON is *not* raw signed bytes

GitHub's delivery API provides JSON `request.payload`. That representation
cannot establish the exact original wire bytes signed by
`X-Hub-Signature-256`, and the audit **never reserializes it to claim a valid
HMAC**. The receiver already validates HMAC on raw bytes before insertion.

A positive result marks
`actual_github_delivery_origin=GITHUB_API_RECORD_CORROBORATED`, with
`raw_hmac_body_equivalence=NOT_ATTESTED_BY_API`. This is independent
**provenance corroboration**, not a cryptographic equivalence proof between
the API's reconstituted JSON and the receiver's original payload digest.
It also cannot independently prove that a particular GitHub delivery
caused a specific provider-state observation, because the Activity may
also be triggered by polling.

**The release decision is always `NO_GO`**, even when all above evidence
matches. Independent security review, public ingress design, rollback drill,
secret rotation and an operator-signed release decision are still required
under [#185](https://github.com/avaargsh/agent-control-plane/issues/185).

## Testing and no-fake-live evidence

```bash
PR_WATCH_PG_TEST_DSN='postgresql://.../disposable' \
  pytest -q tests/test_pr_watch_github_delivery_evidence.py
```

The CI tests perform **real PG16 journal writes + read-only audits** with
*injected GitHub API record fixtures*. They test mismatched repo ID, GUID,
Hook ID, PR selectors, wrong endpoint, response/duplicate semantics, missing
History proof, and raw-secret redaction. They do NOT contact GitHub's Webhook
delivery API or attest a live external event, because CI has no configured
webhook and no privileged token. A real positive report must come from the
operator-executed CLI.

Official endpoint:
https://docs.github.com/en/rest/repos/webhooks#get-a-delivery-for-a-repository-webhook
