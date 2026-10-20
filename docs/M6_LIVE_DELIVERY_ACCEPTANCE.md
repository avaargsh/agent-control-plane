# M6 / #185 — Operator-owned live delivery acceptance

Status: **NOT RUN / NO-GO**. This is a checklist and evidence contract, not a claim of a completed GitHub delivery. Source baseline: `c96c88edca4a90ed95eff2253b70cefc6c208aaa`. Existing local Nginx/PG16/Temporal CI is not a substitute for real GitHub ingress.

## 0. Ownership and prerequisites

An authorized operator must select a disposable GitHub repository and PR, isolated staging host and domain, non-production PostgreSQL 16 and Temporal namespace, least-privileged read-only GitHub credential, webhook secret from a secret manager, TLS certificate, and explicit rollback owner. Never place secrets, tokens, DSNs, private certificates or raw sensitive event bodies in issue comments or committed evidence.

Default decision is **NO-GO**. No public listener, GitHub webhook registration, migration, DNS change, or live delivery may be initiated merely by running this checklist. The reference Python receiver must remain bound to loopback behind separately reviewed ingress.

## 1. Before any external ingress

- [ ] Record exact repository commit, operator approval, disposable environment identifiers and review time.
- [ ] Confirm current PG16 V2 migration ledger, optional Inbox extension checksum, existing backup and restore rehearsal; perform no request-time DDL.
- [ ] Review effective Nginx config and certificate chain, raw-byte body forwarding, fixed backend Host, restricted routes, method and Origin checks, request-size cap, rate limits and secret rotation. Do not reuse the localhost-only self-signed CI template as a public production configuration.
- [ ] Confirm authenticated Goal owner/repo/PR binding, read-only GitHub token permissions, Temporal Workflow ID, History retention and event observation API.
- [ ] Capture pre-delivery Inbox count, Goal version, Workflow History watermark and task projection. Do not infer success from a baseline snapshot digest.

## 2. Real delivery and independent observation

- [ ] Register the explicitly authorized disposable GitHub webhook using the reviewed external HTTPS endpoint and a non-production secret. Capture redacted GitHub delivery ID, event type, HTTP response class, delivery timestamp and redelivery status.
- [ ] Trigger a real pull_request event for the bound PR. Verify HMAC over exact raw bytes at the receiver; separately capture Inbox claim and one matching Temporal wake Signal in Workflow History.
- [ ] Trigger an explicitly PR-associated check_run.completed or check_suite.completed event. If GitHub omits pull_requests[] (e.g. fork checks), record polling fallback, not a fabricated association.
- [ ] Worker independently refetches the exact GitHub PR and CI using a read-only credential. Compare observed PR identity/head SHA and outcome with the stored Goal and authenticated scoped events.
- [ ] GET /api/tasks/{goal_id} must remain business_outcome_verified=false and independent_proof_ref=null even after observed MERGED. Only ACP #191 trusted-signer and exact-operation proof work may introduce independently verified completion in a separately reviewed contract.

## 3. Negative-first and crash/recovery matrix

For each case record pre-state, injected fault, Inbox/History/Goal/task projection after restart, and whether any duplicate side effect or cross-owner data exposure occurred.

| Case | Required acceptance |
| --- | --- |
| Invalid or rotated-out HMAC | DENY, no wake |
| Wrong repository/PR or unaffiliated check | DENY/ignore, no guessed association |
| Duplicate delivery UUID and identical body | Same durable receipt, no second Signal |
| Same UUID, conflicting body | Conflict, no second Signal |
| Paused/cancelled Goal | No unauthorized wake |
| Wrong authenticated owner | Scoped 404/DENY, no leakage |
| Lost Signal ACK | UNKNOWN until positive exact Temporal History proof; no resend |
| Worker SIGKILL/restart | Same Goal identity, no duplicate write |
| GitHub API outage/rate limit | UNKNOWN/WAITING, never verified success |
| Truncated/inaccessible Temporal History | UNKNOWN; absence is not proof of non-delivery |
| Approval revoked or stale | DENY at write boundary; PR Watch itself has no GitHub write permission |

## 4. Evidence bundle and GO/NO-GO

Use a non-secret directory outside the repository for raw operator artifacts. Publish only redacted summaries and content hashes:

- exact Git commit and deployed artifact digest;
- GitHub redacted delivery identifiers and event association;
- PG migration and Inbox receipt summaries;
- Temporal Workflow ID and exact matching History event identifiers;
- authenticated scoped Goal/Task/Activity snapshots;
- restart, replay, denial, backup/restore and rollback outcomes;
- reviewer identity, decision time, limitations and explicit signed GO/NO-GO.

A SHA-256 digest proves integrity of the captured artifact only, not signer identity or business outcome. Keep #185 and #192 open if any live-delivery or proof gate is unverified. #191 separately owns in-toto/DSSE signer authenticity and replay binding.

## 5. Rollback

Disable the external webhook at GitHub and revoke its secret; close external ingress; pause the scoped Goal with the existing authenticated CAS API; stop the staging worker. Preserve Inbox and Temporal History for reconciliation, and do **not** blindly replay UNKNOWN deliveries or drop the Inbox extension (it has a Goal foreign key). Restore from the operator-verified backup only with an approved downtime and data-loss decision. Record final NO-GO if any evidence cannot be independently reproduced.
