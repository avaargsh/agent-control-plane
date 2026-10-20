# M6 — Opt-in live effect-boundary authority check

Status: experimental reference contract, NOT production issuer identity,
GitHub webhook acceptance, independent proof signer authorization or M6 GO.

## Why this exists

ACP #196–#198 authenticate a **historical** signed execution proof reference.
A DSSE signature or persisted receipt is NOT permission to perform a new
write. Existing Kubernetes execution already checks signed approval,
plan, lease, UID/generation, CAS and v3 authority reservations.
Those checks do not, by themselves, read a separately governed **current**
policy/approval/principal/operation state immediately before the provider
mutates a resource.

## Narrow integration contract

ContextBoundExecutionContext now has an optional live_effect_gate field.
Only when the caller explicitly opts in does the existing provider invoke
LiveEffectBoundaryGate.assert_current() immediately before its Kubernetes
patch_deployment call, after all existing plan and context validations.

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

On a live gate rejection **before any Kubernetes PATCH attempt**,
the coordinator catches a dedicated LiveEffectAdmissionDenied, records
an auditable ABORTED/NOT_APPLIED terminal attempt and releases the v3
AuthorityReservation. The denied attempt cannot later become COMMITTED.
An entirely new authorized attempt gets a different operation identity.

**Only** this typed, pre-patch exception can trigger the deterministic
NOT_APPLIED path. Actual provider errors, a lost ACK or ownership ambiguity
retain the original reconciliation/UNKNOWN protections.

An already-applied replay path returns a historical provider receipt and
does not issue a second PATCH. It is not a new live write or evidence of
new authorization. A later independent verifier must still check actual
observed business state before claiming success.

## Read authority must be genuinely independent

An integration must supply a trusted reader for its **own** live approval
revocation, principal enablement, policy decision/version and unique nonce
registry. Do NOT instantiate a reader from browser/chat messages, DSSE
payloads, model-generated JSON, or cached pre-approval objects. This PR
does not provide or deploy that source-of-truth adapter; the tests use
an intentionally mutable fake to verify the fail-closed call site.
Default operation is unchanged when no live_effect_gate is supplied.

## Race and production boundaries

This is a latest-moment read check, **not an atomic transaction** spanning
the authority database and Kubernetes API server. Revocation that commits
after the read but before the patch is not fully fenced by this adapter.
For strong guarantees, external authority must provide a side-effect
fencing token/revision recognized by the executing provider, plus
CAS/resourceVersion and uid/generation checking and rollback procedures.

Do not present synthetic read fixtures as production RBAC, a persistent
trusted authority source, or real Kubernetes/GitHub delivery.
No change is made to frozen ACP v0.1 schemas or the pre-existing DSSE
proof format. No new Task ledger, user-facing write API or automatic
GitHub write path is created. ACP #185 and #192 remain NO-GO.

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
