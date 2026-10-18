# Persistent Agent / Attention Plane integration reference (experimental)

**Status:** example + tests; NOT part of the frozen ACP v0.1 authority boundary.
**Owner:** an external Agent Harness / Channel Gateway, NOT the ACP reconciler.

The ACP repo owns exact-plan authorization, execution attempt identity, fencing,
and independent outcome evidence. It does **not** own chat/session orchestration,
notification transport, Temporal continuation, user memory, or VM lifecycle.
This example demonstrates a narrow policy/outbox protocol that an external
Harness could implement without changing the v0.1 execution contract.

## Logical boundaries

```text
Client / channel adapters -> authenticated EventEnvelope -> Harness goal/task
                                                          |
ACP TransitionPlan <--- explicit authorized action -------+
ACP ExecutionAttempt / IndependentExecutionProof -> verified progress event
                                                          |
                                  Attention Policy + durable Outbox [example]
                                                          |
                                   authenticated user-scoped delivery adapter
                                                          |
                            SSE / push / email / bot / mobile notification
```

- An Agent proposal is not authorization. Only ACP's exact transition admission
  and provider-side fenced execution may affect protected external resources.
- A Temporal Workflow ID is a continuation reference, not the canonical ACP
  transition identity or evidence of operation ownership.
- Workspaces persist independently of Sandbox/VM allocation. Neither a snapshot
  nor a running microVM proves external provider side effects.
- Channel adapters must authenticate source identity and map it to the canonical
  user/goal scope. NEVER merge Slack/email/chat contexts based on text similarity.
- Untrusted external content and LLM-generated classifications cannot assert
  `verified_critical`. Trusted provenance verification and user-approved
  emergency settings are enforced by the hosting service, not by this library.
- Each transport delivery is protected by an independent recipient entitlement
  check. The outbox's `user_id` is a tenant key, not an authentication credential.

## Runnable, limited reference

```bash
python -m pytest -q tests/test_attention_plane_reference.py
```

`examples/attention_plane.py` provides:

1. Pure deterministic `decide(event, preferences, now=...)` with SILENT,
   DIGEST, NOTIFY and a separate `requires_approval` signal.
2. Configurable user-local quiet hours, including overnight windows; verified
   critical alerts bypass quiet hours ONLY when explicitly permitted.
3. SQLite decision records with per-(user,event) fingerprint checks.
4. A user+goal+episode-scoped deduplicating notification outbox.
5. Atomic single-owner delivery claim, lease generation checks and CAS ACK.
6. Explicit UNKNOWN when a delivery may have happened but the response was
   lost. Expired claims are UNKNOWN, **never re-sent automatically**.
7. Operator/provider-confirmed absence -> explicit requeue with evidence ref.

**No sending mechanism is implemented.** This is a transport-agnostic contract
reference, not a mailer, web API, production AuthN service or scheduled digest
sender. DIGEST/SILENT decisions are recorded only; batching, delivery scheduling,
notification budget, quota, policy version migrations, notification revocation,
signatures, provider evidence validation and audit retention require a Harness.

Dedupe keys MUST identify a bounded episode/revision (e.g. `pr42-ci-run-7`).
Do not use `pr42` for all future CI events, or the durable unique key will
suppress valid future alerts. Do not place secrets in event summaries.

## Important limits and next build steps

- SQLite is a **single-node reference**, not multi-tenant production HA.
  A real service should use PostgreSQL transactional outbox + row security,
  scoped auth, append-only audit and delivery provider adapters.
- An outbox lease does not make a network request exactly once. When a send
  times out, retain UNKNOWN until the provider can prove delivery/absence.
  Without a provider reconciliation API, ask a user/operator instead of retrying.
- Local wall-clock quiet-hour conversion is a reference; DST gaps/folds, explicit
  vacations and holidays need pinned user-facing policies and tests.
- An arbitrary string passed as `evidence_ref` is not cryptographic proof:
  the caller must validate a provider-signed or otherwise independently
  verifiable absence statement before invoking the requeue operation.
- `requires_approval` is a presentation hint, never a grant. Human approval
  must be bound to an ACP PlanAuthorizationBinding via trusted server identity.
- Next vertical slice: GitHub PR status event -> verified ACP run/progress ->
  Temporal timer/signal -> reference Attention decision -> mock channel
  delivery with explicit UNKNOWN reconciliation. Test restart, replay, cross-user
  denial and notification precision, then decide whether a separate Harness
  repository/service is justified.

This example is intentionally optional and does not alter ACP's normative
v0.1 release or provider-authority contract.
