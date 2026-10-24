# M6 — Live effect-boundary authority check

Status: experimental reference contract, NOT production issuer identity,
GitHub webhook acceptance, independent proof signer authorization or M6 GO.

## Why this exists

ACP #196–#198 authenticate a **historical** signed execution proof reference.
A DSSE signature or persisted receipt is NOT permission to perform a new
write. The Kubernetes execution path already checks signed approval, plan, lease,
UID/generation, CAS and v3 authority reservations. Those checks do not, by
themselves, read a separately governed **current** policy/approval/principal/
operation state immediately before a provider mutates a resource. The GitHub
pull-request merge adapter previously relied on plan/head/replay checks alone;
it now requires the same current-authority gate before a new merge request.

## Narrow integration contract

The gate is configured by trusted service wiring, not by an incoming provider
request. The Kubernetes context-bound path invokes
`LiveEffectBoundaryGate.invoke_with_current()` immediately before
`patch_deployment()` when `live_effect_gate` is configured. The GitHub
pull-request merge adapter is stricter: it refuses a new merge unless a gate
is configured and the caller supplies a coherent `StateTransition`,
`ActionIntent`, `AuthorizationBinding`, active lease, caller, work ID and
current policy-input hash. It derives and validates `PlanAuthorizationBinding`
and `PlanExecutionFence` before observing/mutating the PR, then runs the merge
API call inside `invoke_with_current()`. A gate cannot be passed in an
individual merge request.

The gate is configured out of band with a trusted EffectScope (tenant,
project, cluster, operation nonce), a trusted timezone-aware clock, and
a CurrentEffectAuthorityReader that performs an independently sourced
read_current(tenant, project, cluster, work_id, operation_id).

The returned CurrentEffectAuthority must contain the exact authenticated
policy principal and separately named lease-holding controller, work and
operation identity, resource UID and generation, transition/action and
authorization hashes, approval hash, policy input/decision hashes, policy
version, nonce, lease identity/epoch, current decision/statuses, read time,
expiry, and positive version. Policy must be PERMIT and approval, principal
and operation must each be ACTIVE. Missing/old/future timestamp, denied
approval, wrong tenant/project, stale policy, operation mismatch,
reader exception or clock outage are all DENY.

## Terminalization and replay

For Kubernetes, a gate rejection **before any PATCH attempt** reaches the
execution coordinator as `LiveEffectAdmissionDenied`; the coordinator records
an auditable `ABORTED/NOT_APPLIED` attempt and releases the authority
reservation. The denied attempt cannot later become COMMITTED.

For GitHub, `GitHubPullRequestExecutionCoordinator` writes the existing provider-neutral `ExecutionAttempt` as `PREPARED` before calling the guarded merge provider. A typed denial before `merge_pull_request()` becomes durable `ABORTED/NOT_APPLIED`. A successful response is not enough for `COMMITTED`: the coordinator independently observes GitHub and requires exact operation/plan ownership markers. A crash after provider success but before journal commit is recovered from observation with the same operation ID and no second merge request; unresolved outcomes become terminal `UNKNOWN` and cannot be automatically retried.

Once a provider call has been entered, a timeout, lost ACK or ownership
ambiguity keeps its original reconciliation/UNKNOWN semantics. The gate never
rewrites post-entry provider exceptions into `NOT_APPLIED`.

An already-applied replay returns a historical receipt and does not issue a
second mutation. It is not new authorization or independent outcome proof; a
later independent verifier must still observe the business state.

## Read authority must be genuinely independent

An integration must supply a trusted reader for its **own** live approval
revocation, principal enablement, policy decision/version and unique nonce
registry. Do NOT instantiate a reader from browser/chat messages, DSSE
payloads, model-generated JSON, or cached pre-approval objects. This PR
does not provide or deploy that source-of-truth adapter; the tests use
an intentionally mutable fake to verify the fail-closed call site.
The Kubernetes context path remains opt-in for compatibility. The GitHub
merge provider instead fails closed: a missing gate denies a new merge before
the merge API can be called. Tests use a deliberately mutable fake reader;
they do not prove a production authority service has been deployed.

## Race and production boundaries

This is a latest-moment authority check, **not an atomic transaction** spanning
the authority database and an external provider. A `hold_current()` reader
can keep the exact authority row lock until the provider call returns; a
read-only reader preserves a check/use race. Provider-native preconditions
still matter (Kubernetes resourceVersion/UID/generation; GitHub approved head
SHA), and accepted side effects cannot be undone by revocation. Production
wiring must document lock duration, provider-side fencing/CAS, timeout and
compensation behavior.

Do not present synthetic read fixtures as production RBAC, a persistent
trusted authority source, or live Kubernetes/GitHub delivery. No frozen ACP
v0.1 schema or DSSE proof format is changed. No new Task ledger or user-facing
write API is created. The guarded GitHub provider is not production-integrated
until trusted reader configuration, durable execution/reconciliation wiring
and a live GitHub acceptance run exist. ACP #185 and #192 remain NO-GO.

## Developer tests

    python -m pip install -e '.[dev]'
    python -m pytest -q tests/test_live_effect_boundary.py \
      tests/test_execution_lifecycle.py
    python -m ruff check src/agent_control_plane/live_effect_boundary.py \
      tests/test_live_effect_boundary.py

The negative tests verify no PATCH, journal ABORTED/NOT_APPLIED and release
of authority reservation even when signed approval was valid at prepare time.
Runbook prerequisites for a real pilot: operator-owned current state reader,
negative replay and revoked-secret tests, and independent external checks.
