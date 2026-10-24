# GitHub pull-request merge authorization boundary

Status: guarded reference adapter; **not production deployment acceptance**.

## Enforcement point

`GitHubPullRequestMergeProvider.apply()` used to depend only on the sealed provider plan, a caller-supplied operation ID, the observer principal and time. The provider-side head-SHA check and commit-marker replay check were useful concurrency/ownership controls, but they were not authorization.

A new merge now fails closed unless trusted service construction configured a `LiveEffectBoundaryGate`. The merge request cannot provide or replace that gate. Before reading provider state or sending a merge request, the adapter derives `PlanAuthorizationBinding` and `PlanExecutionFence` from the exact `TransitionPlan`, `StateTransition`, `ActionIntent`, `AuthorizationBinding` and active lease; it calls `validate_plan_execution()` and requires the observer actor to match the lease-holding caller.

After the existing live PR/head preconditions pass, the actual `merge_pull_request()` invocation runs inside `LiveEffectBoundaryGate.invoke_with_current()`. The reader must independently resolve the configured operator scope and the exact work/operation identity, and return current authority matching the resource, transition/action/authorization hashes, approval, policy input/decision, principal, executor and lease. Policy must be `PERMIT`; the approval, principal and operation must be `ACTIVE`; stale, expired, missing or mismatched authority denies before the merge API call.

## Failure semantics

- Missing gate or incomplete action/authorization/lease context: typed `LiveEffectAdmissionDenied` before a provider mutation.
- Current-authority outage, revoked approval, changed action parameters, wrong scope or executor substitution: no merge API call.
- The provider's approved-head check remains in force; the new gate does not replace GitHub's head-SHA precondition.
- Once the merge call has been entered, timeout/lost ACK remains ambiguous and is handled by the existing ownership-aware reconciliation path. The gate does not relabel a post-entry failure as `NOT_APPLIED`.
- A replay that proves the exact operation/plan marker does not issue another mutation, and its receipt is not independent outcome proof.

## Trust and deployment boundary

The reader is trusted configuration created by service wiring, not request data. A production authority writer must only publish an active row after its own trusted policy and action-specific approval workflow has accepted the exact operation. A caller-supplied digest, standalone `AuthorizationBinding`, scope-level workload credential, or historical proof is not enough to create that row.

The GitHub adapter does **not** own a durable execution journal, approval issuer, authority-row writer or end-to-end webhook service. Its typed pre-effect denial does not itself create an auditable durable `ActionExecution` terminal record. The integration caller must persist operation state and preserve the post-call `UNKNOWN`/reconciliation contract. The checked-in tests use a fake authority reader; they prove the adapter's fail-closed call path, not production identity, database provisioning or a live GitHub merge.

Before production acceptance, require an operator-owned current-authority reader and row-lock configuration, real signer/policy lifecycle and revocation propagation, durable operation/journal integration, provider credentials and permissions review, and a disposable or staging GitHub acceptance run covering denial, approved merge, revocation, and lost-ACK recovery. No frozen ACP v0.1 schema or public compatibility contract is changed by this slice.
