# M6 #185 — disposable VPS staging execution plan

**Status: proposed operator runbook; NOT DEPLOYED; NO-GO.**
This is a deployment decision and checklist, not evidence of an external
GitHub delivery or permission to change infrastructure.

## Decision

Use **one disposable Ubuntu VPS** with an operator-controlled staging
hostname, isolated non-production PostgreSQL 16 and Temporal namespace,
a disposable GitHub repository and PR, and separately reviewed TLS
termination. Do **not** use the GPU hosts or any shared production
control plane. The repository's Nginx template and Python receiver are
localhost-only *test fixtures*, **not** ready-to-publish configurations.

## Minimum external prerequisites (operator must supply privately)

- Written approval, environment owner and teardown deadline.
- Temporary VPS/cloud account and a dedicated DNS name with a valid
  publicly trusted TLS certificate. No credentials in GitHub comments.
- Disposable GitHub repository with permission to manage a single
  repository webhook and generate a PR plus associated check event.
- GitHub read-only metadata/check permission for the worker and
  Webhooks:read for the independent delivery auditor; webhook
  configuration privileges belong only to the designated operator.
- Separate, rotatable random webhook secret from a secret manager;
  backup/restore and rollback owner.

## Network and privilege boundaries

- Public ingress: TCP 443 only, after an independent security review.
  SSH is restricted to the operator's controlled access path.
- PostgreSQL 5432, Temporal 7233, the Python HTTP receiver and
  metrics/admin interfaces remain on private interfaces or loopback.
- The public reverse proxy exposes **only** POST /webhooks/github,
  with exact expected Host, body-size and request-time bounds, HMAC
  verified over unchanged raw bytes by the backend, and no implicit
  retries on ambiguous acknowledgements.
- No unauthenticated task API or generic proxy route. A valid signed
  GitHub event is only a scheduling hint, never authoritative proof
  that CI passed or a PR merged.
- Secrets are provisioned out of band. Use distinct service principals
  for schema installation, runtime Inbox operations and read-only
  GitHub evidence inspection.

## Staged execution with stop points

1. **Authorize**: name owner, repository/PR, DNS, rollback contact and
   explicit approval. Record pinned ACP main SHA and image digest.
   Stop if any is absent.
2. **Provision privately**: disposable VPS, locked-down firewall,
   separate PG16 and Temporal, backups and restore rehearsal. Apply
   the versioned Inbox migration once with an operator credential,
   verify checksum and least-privilege runtime. No request-time DDL.
3. **Review ingress**: independently approve production-quality TLS,
   DNS, host allowlist, raw-byte forwarding, rate limiting, logs
   redaction and certificate/secret rotation. Do not merely change
   `listen 127.0.0.1` in the local fixture to `0.0.0.0`.
4. **Activate test only**: deploy the receiver bound to loopback,
   register the single disposable Hook to the approved HTTPS endpoint,
   with JSON body, strict TLS and only pull_request + associated
   check_run/check_suite subscriptions. Capture redacted Hook ID.
5. **Collect two real deliveries**: one PR event and one check event
   explicitly bound to the same PR. Preserve redacted GitHub delivery
   IDs, exact receiver HMAC verification and PG Inbox receipts. Run
   `examples/pr_watch_github_delivery_evidence.py` separately for
   each event and the offline
   `examples/m6_paired_delivery_evidence.py` on both reports.
   Exact Temporal History wake cardinality must be one for each.
6. **Independent business observation**: use read-only GitHub APIs to
   refetch PR/head SHA/check outcome, then verify authenticated
   user-scoped Goal/Task/Activity projection. Event body and an ACK
   are insufficient.
7. **Faults and recovery**: invalid HMAC, duplicate and conflicting
   GUID, wrong PR/tenant, absent fork check association, lost ACK,
   worker restart, GitHub outage, restore and rollback. Fail closed;
   never blindly replay UNKNOWN.
8. **Independent release review**: record exact source SHA, artifact
   hashes, original HMAC audit provenance, reviewer identity and
   signed GO/NO-GO. An offline correlation result is not GO.
9. **Teardown**: disable Hook, revoke and rotate credentials, close
   public ingress, preserve approved redacted evidence, delete VPS
   and DNS only after retention/rollback signoff.

## Execution record

| Field | Status |
| --- | --- |
| Operator approval and VPS owner | NOT PROVIDED |
| Staging DNS and TLS review | NOT PROVIDED |
| Disposable GitHub repo/PR and Hook | NOT CONFIGURED |
| PG16 + Temporal isolated deployment | NOT DEPLOYED |
| Real PR + check GitHub deliveries | NOT OBSERVED |
| Independent HMAC/Inbox/History/Task evidence | NOT COLLECTED |
| Signed operator release decision | NO-GO |

The initial acceptance source is
[docs/M6_LIVE_DELIVERY_ACCEPTANCE.md](M6_LIVE_DELIVERY_ACCEPTANCE.md).
Keep #185 and #192 OPEN until independently reproducible external
evidence is captured. Do not confuse green CI with operator acceptance.
