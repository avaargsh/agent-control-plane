# Action Authorization Contract (post-v0.1 P0)

Status: **P0 contract mapping plus a narrow fail-closed scope-classification fix**.
This document does not widen the frozen v0.1 public API, create a new runtime,
or claim a universal MCP/GUI authorization gateway exists.

## Design rule

Authorization is checked at the boundary that can still prevent the provider
side effect. A signed approval, old credential, historical proof, prompt claim,
or caller-supplied identity is not by itself current permission to execute.

The existing ACP execution kernel already binds an exact state transition,
action, policy decision, approval, lease/fence and durable execution attempt.
The Workload Credential Authority separately binds a short-lived credential to
canonical tenant/Agent/Session/Run/sandbox state, a credential binding, audience,
scopes, expiry and revocation state. Reuse these authority sources instead of
creating duplicate identity, approval, or operation ledgers.

## Mapping the requested concepts onto existing ACP types

| Concept | Existing authority-bearing representation | Boundary / limitation |
|---|---|---|
| `AgentIdentity` | `Principal` plus the independently resolved `CanonicalRunBinding`; signed workload assertions are checked against `RunAuthorityReader` state | A request or assertion claim alone is not canonical identity. Principal, tenant, Session and Run must match authority-owned state. |
| `ActionRequest` | `ActionIntent` bound to `StateTransition` and `ResourceIdentity`; the canonical `action_hash` covers provider, operation and parameters | The durable execution layer supplies the stable `operation_id`, attempt identity, idempotency and provider outcome. Do not trust a caller to select a broader target after sealing. |
| `AuthorizationGrant` | For state transitions: `AuthorizationBinding` plus verified `TransitionApproval`, current policy decision and execution fence. For workloads: issued credential plus authority-owned CredentialBinding/ApprovalGrant records | This is intentionally not one interchangeable grant type. A workload bearer credential is not a substitute for an exact transition authorization, and a historical signature is not current authorization. |
| `ActionExecution` | Durable `ExecutionAttempt` / execution journal, authority reservation, provider result and reconciliation / verification evidence | `PREPARED`, terminal outcomes and `UNKNOWN` retain their existing semantics. Never infer NOT_APPLIED from timeout or lost acknowledgement after the side-effect boundary. |
| `AuditEvent` | Existing execution journal and evidence/proof references; credential lifecycle uses revocation records and transactional outbox events | There is no new generic AuditEvent public type in this slice. Correlation uses stable operation, attempt, approval, credential and event IDs; secrets must not be logged. |

## Current enforcement seams

- `WorkloadCredentialAuthority` validates issuer/audience/expiry, canonical
  Run binding, active CredentialBinding, allowed scopes and exact approval
  references before issuance. Durable issuance rechecks binding and approval
  versions under database locks.
- `LiveEffectBoundaryGate` is an **opt-in, provider-specific** last-moment
  check for the Kubernetes Deployment PATCH path. It binds operator-pinned
  tenant/project/cluster scope, exact action/authorization/approval/policy,
  lease, resource UID/generation and operation state. The PostgreSQL reader can
  hold the exact current-authority row lock across the PATCH invocation.
- Neither mechanism currently constitutes a universal action gateway for every
  application, MCP server, or GUI adapter. A new executor must not claim this
  guarantee until it has its own trusted current-authority enforcement and
  parity tests.

## Credential scope risk classification

A scope uses its final colon-delimited component as the action verb. Only these
explicitly known read-only verbs are exempt from the exact-approval requirement:

- `read`
- `list`
- `get`
- `describe`
- `inspect`

Every other action verb, including an unrecognized or newly introduced verb,
requires an exact approval reference. This is deliberately fail-closed: a
denylist containing only `write`, `delete`, `admin`, and `mutate` would
silently classify unknown operations such as `transfer`, `create`, or
`execute` as non-sensitive.

This is a narrow credential-scope policy, not a general-purpose semantic
classifier. Operator-reviewed CredentialBinding scope allowlists remain
required. The terminal-action convention must be documented by scope issuers;
a scope whose name does not reliably encode its operation must be treated as
approval-required or redesigned before use.

## P0 authorization invariants

1. **No self-asserted identity:** caller-supplied Agent/tenant/resource fields
   never override the canonical runtime binding or operator-pinned scope.
2. **Exact action binding:** provider, operation, target and canonical parameter
   digest are bound together. Any target or parameter mutation invalidates the
   binding/approval chain.
3. **Least privilege:** read-only scopes cannot be used as write capabilities;
   an unknown scope verb is approval-required, not implicitly read-only.
4. **Current authorization:** expired/revoked credentials, stale approval or
   binding versions, wrong audience, stale policy and authority read failures
   deny before a new protected side effect.
5. **No executor privilege escalation:** API/MCP/GUI transport choice does not
   create authority. Each executor must consume the same logical action and
   target binding and independently enforce its trusted boundary.
6. **Ambiguity is preserved:** before entering a provider call, a typed
   admission denial may be recorded as NOT_APPLIED. After provider-call entry,
   any ambiguous exception remains subject to reconciliation and may be UNKNOWN.
7. **No false exactly-once claim:** use durable idempotency and stable operation
   identity; retries reconcile existing operations rather than blindly
   repeating external effects.
8. **Redacted auditability:** correlate authority decision, action, attempt,
   external outcome and proof references without storing bearer tokens,
   private keys or raw sensitive parameters in logs/history.
9. **Revocation has a boundary:** revoking a grant blocks future admission; it
   does not magically cancel a side effect already accepted by a provider.
   In-flight cancellation and compensation are separate, auditable operations.

## Negative-first acceptance matrix

| Scenario | Required behavior |
|---|---|
| Caller forges Agent or tenant identity | Deny based on canonical authority; do not call provider |
| Read-only scope is used for a write | Deny before effect |
| Unknown credential scope action (e.g. `payments:transfer`) | Exact approval required |
| Wrong audience, expiry, revocation or stale policy/binding | Deny before new effect |
| Parameter or target changes after confirmation | Digest/binding verification fails |
| Executor changes from native API to MCP/GUI | No authority increase; recheck same action binding |
| Duplicate request with same identity and digest | Return/reconcile the same durable operation |
| Same idempotency identity with changed digest | Hard conflict; do not issue second effect |
| Authority service unavailable before effect | Fail closed for protected action |
| ACK lost or exception after provider-call entry | Preserve UNKNOWN/reconciliation semantics; never claim NOT_APPLIED without evidence |

Unit/synthetic tests, PostgreSQL integration, isolated staging, live provider
acceptance, and production readiness are distinct evidence grades.

## Explicit non-goals

- No change to frozen v0.1 schemas or public compatibility surfaces.
- No new Agent runtime, generic policy language, or duplicate Task/Run ledger.
- No universal MCP/GUI gateway claim from the Kubernetes-specific effect gate.
- No production-readiness claim based on unit tests or loopback fixtures.
- No claim that cryptographic digest integrity alone proves signer authority or
  independently observed business outcome.
