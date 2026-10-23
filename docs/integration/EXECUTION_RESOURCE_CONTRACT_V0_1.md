# Cross-repository Execution Resource Contract v0.1

Status: **Draft for contract review; not yet wired end-to-end.**  
Owner of this cross-repository envelope: Agent Control Plane integration.  
Scope: Agent execution profile -> optional GPU Compute request -> observed Compute allocation. This document does **not** change the frozen ACP v0.1 authority kernel or its public compatibility snapshot.

## Design boundary

- **ACP / Agent product layer** selects a versioned `ExecutionProfile`, applies policy and budget, and decides whether a task needs GPU. Session/Run identity remains the canonical identity issued by the owning runtime; this contract carries opaque references and does not re-home that identity.
- **temp-runner / execution runtime** owns CPU Worker, temporary Sandbox, and persistent Workspace execution. A non-GPU profile does not create a GPU ComputeRequest and must not be represented as a zero-GPU Workload.
- **gpu-compute-platform** owns ComputePool/Workload desired state, server-side project/pool/cluster/provider binding, provider lifecycle, reconciliation, observations, cleanup, and provider evidence. It does not choose whether an Agent needs GPU, own Agent task semantics, or become the Session/Run identity source.
- **Kubernetes/Kueue/provider** own actual quota admission, scheduling, device binding and provider-object UID/resourceVersion. Provider adapters reconcile at least once; they do not promise exactly-once remote side effects.

This contract is an adapter envelope, **not a second desired-state database**. The adapter maps a `ComputeRequest` to the existing project-scoped Management API and its `Workload` desired resource. `ComputeAllocation` is a normalized read projection of Workload desired state, independent observations, provider references and evidence; it must not be maintained as a competing source of truth.

## 1. ExecutionProfile

A profile is immutable by revision. Editing a profile creates a new revision; an accepted request always pins `profileId + revision`.

| Field | Required | Meaning / authority |
|---|---:|---|
| `apiVersion` | yes | Literal `agent.execution-profile/v0.1`. |
| `profileId`, `revision` | yes | Stable profile name and positive immutable revision, owned by ACP's profile/config store. |
| `projectId` | yes | Scope selected and authorized server-side; never authorization evidence by itself. |
| `environment` | yes | `worker`, `sandbox`, `workspace`, or `gpu-workload`. |
| `resources.cpuMilli`, `memoryMiB`, `ephemeralStorageMiB` | yes | Execution resource ceilings/requests for the selected executor. Units are explicit; implementations must not infer units. |
| `resources.timeoutSeconds`, `maxConcurrency` | yes | Runtime limit and concurrency ceiling; enforcement owner is the selected executor/control plane. |
| `accelerator.mode` | yes | v0.1 is deliberately binary: `none` or `required`. No implicit/automatic GPU fallback. |
| `accelerator.class`, `quota` | GPU only | Portable accelerator class and positive device quota/count for a `gpu-workload`; mapping to vendor resource names belongs to the ComputePool binding/provider layer. |
| `persistence` | yes | `ephemeral`, `session`, or `durable`; this is intent, not proof that storage has been created or checkpointed. |
| `networkPolicyRef` | yes | Reference to a separately governed network policy. Default behavior is deny-by-default; a profile string does not itself authorize egress. |
| `credentialBindingRefs` | yes | Opaque references to scoped credential bindings. Never place secret values, tokens, private keys, or provider credentials in the profile/request. |
| `budgetRef` | no | Reference to an independently enforced budget policy; omission does not mean unlimited production spend. |

Only `environment=gpu-workload` with `accelerator.mode=required` may produce a ComputeRequest. The other environments use the selected Worker/Sandbox/Workspace executor. A persistent Workspace remains owned by its Workspace provider; deleting a GPU Workload must not delete an externally owned PVC or user data.

## 2. ComputeRequest

ComputeRequest is a durable logical request envelope around the existing Compute Workload write path. It is not a second Job/Workload API.

| Field | Required | Authority / invariant |
|---|---:|---|
| `apiVersion` | yes | Literal `compute.request/v0.1`. |
| `requestId` | yes | Exactly 32 lowercase hexadecimal characters. Generate with a cryptographically secure 128-bit source, persist before the first side effect, and reuse it after caller/worker restart. It is also the existing Management API `Idempotency-Key` and is returned as `OperationReceipt.requestId`; there is no second independently generated key. |
| `intentDigest` | yes | Local envelope integrity digest in `sha256:<64 lowercase hex>` form, computed over the canonical ComputeRequest intent with this field omitted. Persist it with the exact request body and reject same-ID replay if the intent differs. It is **not** the server-generated `OperationReceipt.requestDigest`; retain that backend value separately as receipt evidence. |
| `projectId`, `computePoolId` | yes | Requested scope/target. Management must revalidate current Project authorization and resolve pool/cluster/provider binding server-side; caller values are not authority. |
| `profileRef.profileId + revision` | yes | Pins the exact GPU ExecutionProfile revision that justified the request. |
| `correlation.agentId/sessionId/runId` | yes | Opaque canonical identity references for trace/audit correlation. They are not caller authorization claims and do not become Compute-owned identity. |
| `workload.id + expectedGeneration` | yes | Stable Workload ID plus the Management API CAS precondition. For the current v0.1 public create path, `expectedGeneration=0`; the server assigns the resulting generation and the operation receipt returns it. Never let the adapter invent the result generation. The current public Management path rejects reuse of an ID while a tombstone exists; do not claim delete/recreate is supported until its higher-generation lifecycle path is separately wired and accepted. |
| `workload.image` | yes | Image reference requested by the Workload. Production supply-chain acceptance must resolve/pin and verify an immutable digest; a mutable tag alone is not release evidence. |
| `workload.accelerator.class + quota` | yes | Must agree with the pinned profile and selected ComputePool binding. Provider-specific resource names are resolved inside the Compute layer. |
| `workload.command` | no | Explicit command vector, if used; never accept arbitrary privilege/configuration fields outside the existing reviewed Workload contract. |

The v0.1 adapter maps this envelope to the existing authenticated Management API mutation:
`PUT /api/v1/management/clusters/{resolvedCluster}/projects/{projectId}/resources/Workload/{workload.id}`,
header `Idempotency-Key: requestId`, and JSON body `{expectedGeneration, poolId: computePoolId, spec: {image, command?, accelerator}}`. The server resolves the project/pool/cluster/provider binding and assigns the resulting generation. After HTTP 204, fetch the scoped operation receipt by the original requestId; that receipt's `resultingGeneration` and the authorized Desired read establish the committed generation. Do not bypass the authenticated Management ingress by calling Agent/internal endpoints. Do not create a second durable operation-receipt database when the Management receipt is authoritative. If a new-generation recreation is not supported by the current public endpoint, fail closed rather than forging a generation or routing around Management.

### Ambiguous submission outcome

A timeout, connection loss, 5xx after send, or process death before the acknowledgement is observed means **UNKNOWN**, not rejected and not successful. Recover using the original `requestId` (the same value as `Idempotency-Key`), query the owner-scoped operation receipt, then re-read authorized Desired state and independent execution observations. Never mint a new ID/key merely because the caller did not receive ACK. HTTP 204 / a committed receipt means the desired write was accepted; it does not mean Kueue admitted it, a Pod became ready, inference succeeded, or resources were durably released.

## 3. ComputeAllocation

ComputeAllocation is a normalized **read projection**, not an independent durable authority. The Compute service derives it from the durable Workload Desired record, the current generation/provider binding, live or persisted independent observations, and evidence references.

| Field | Required | Authority / invariant |
|---|---:|---|
| `apiVersion` | yes | Literal `compute.allocation/v0.1`. |
| `requestId` | yes | Echoes the same 32-hex operation ID that was sent as `Idempotency-Key`. |
| `projectId`, `computePoolId`, `workloadId`, `generation` | yes | Resolved Workload identities and committed desired generation; the generation is taken from the server receipt and authorized Desired record, not from the incoming ComputeRequest. |
| `clusterId`, `provider`, `providerBindingGeneration` | yes | Resolved by the server from the frozen pool/cluster binding, never trusted solely from the request body. |
| `state` | yes | `REQUESTED`, `ALLOCATING`, `ALLOCATED`, `RELEASING`, `RELEASED`, or `UNKNOWN`. This is resource lifecycle, not Agent Run outcome. |
| `observedGeneration` | yes | Latest desired generation actually processed by the observer/reconciler; it does not alone imply readiness. |
| `providerRefs` | yes | Zero or more object references. Once observed, bind kind/namespace/name to API-server-issued UID, resourceVersion and generation. Labels/name alone are not ownership proof. |
| `conditions` | yes | Independent condition snapshots such as quota reservation, admission, Pod readiness, failure and cleanup; include status/reason/observation time. Run success and resource release remain separate facts. |
| `evidenceRefs` | yes | Stable references/digests to the records supporting the projection. An evidence pointer does not imply the provider object still exists. |

State meaning:
- `REQUESTED`: durable desired intent/operation receipt exists; execution is not implied.
- `ALLOCATING`: request is accepted and quota/provider allocation is pending or reconciling.
- `ALLOCATED`: the current-generation provider allocation is independently observed and bound; this does not imply task success.
- `RELEASING`: deletion/release intent is durable and cleanup is being reconciled.
- `RELEASED`: every workload-owned provider resource required by the selected mode is independently observed absent, cleanup/finalization is complete, and the current generation/tombstone fence is committed.
- `UNKNOWN`: provider or persistence observations are insufficient to make a safe claim. Fail closed and reconcile; do not silently map UNKNOWN to success or absence.

A task may fail while its allocation is still present; represent that with a condition and continue cleanup. For DRA mode, Job deletion must be observed before deleting the workload-owned ResourceClaim, and the Workload is not RELEASED until both are gone. Shared pool resources (for example ResourceFlavor) and externally owned Workspace/PVC data are not released as Workload side effects.

## 4. Error envelope and retry semantics

The normalized error envelope is defined in the accompanying JSON Schema. Implementations may return their native HTTP status, but must preserve the stable `code`, `outcome`, and retry meaning.

| Code | Meaning | Outcome / handling |
|---|---|---|
| `INVALID_ARGUMENT` | Malformed payload or invalid field | `REJECTED`; do not retry without changing the payload and generating a new logical request. |
| `UNAUTHENTICATED` | Missing, expired, revoked, or invalid caller credential | `REJECTED`; obtain valid identity, then re-authorize. |
| `PERMISSION_DENIED` | Caller lacks current project/resource permission | `REJECTED`; do not retry until authorization changes. |
| `GENERATION_CONFLICT` | Stale generation, mutation of immutable same-generation intent, or active tombstone | `REJECTED`; recover current state and follow delete/finalize/recreate contract. |
| `IDEMPOTENCY_CONFLICT` | Original idempotency key reused with a different canonical request digest | `REJECTED`; never reinterpret as a second request. |
| `PROFILE_UNSUPPORTED` | Requested profile/resource mode is not supported by the selected executor/provider binding | `REJECTED`; do not silently substitute CPU/GPU or another provider. |
| `QUOTA_EXCEEDED` | Policy or quota refuses the request | `REJECTED` if refusal is proven; retry only after policy/resource state changes. |
| `RESOURCE_UNAVAILABLE` | Provider/scheduler is temporarily unable to make progress | `NOT_COMMITTED` only when the implementation proves no write occurred; otherwise `UNKNOWN`. Retry with the same key. |
| `DEADLINE_EXCEEDED` / `UNAVAILABLE` | Deadline, transport or dependency failure | After a side-effecting call may have been sent, `UNKNOWN`; recover using the same key and receipt. |
| `OWNERSHIP_UNPROVEN` | Same-generation name/labels or readiness do not prove the authorized operation created/owns the observed object | `UNKNOWN`; no success promotion or destructive cleanup based only on name/label. |
| `PROVIDER_STATE_CONFLICT` | Provider generation, UID, binding, or desired digest disagrees | Fail closed; preserve evidence and require reconciliation/manual review as appropriate. |
| `CLEANUP_PENDING` | Delete request was accepted but absence/finalization is not yet proved | `UNKNOWN`/in progress; do not claim release complete or discard the tombstone fence. |

Errors must include a safe correlation/request ID, must not leak raw tokens, credential values, kubeconfig, database DSNs, or unrestricted provider responses, and must distinguish a **known rejection** from an **unknown side-effect outcome**.

## 5. Authoritative data sources

| Fact | Authority |
|---|---|
| Profile contents and immutable revision | ACP profile/config authority |
| Agent / Session / Run identity | Owning Agent runtime; this contract carries references only |
| Current user/project authorization and project-to-pool binding | Authenticated Management API + server-side grants/bindings |
| Logical request ID / Management `Idempotency-Key` | One durable ACP request ID (32-hex); same value survives retry/restart and is echoed by the Management operation receipt |
| Whether a Desired write committed | Owner-scoped durable Management operation receipt (`requestId`, server `requestDigest`, `resultingGeneration`), reconciled with authorized Desired state. The receipt's `requestDigest` is not the adapter-local `intentDigest`. |
| Workload desired spec, generation, deletion intent, finalizer and tombstone | Compute platform persistent store |
| Current cluster/provider for a pool | Server-resolved, frozen pool/cluster/provider binding |
| Quota/admission/device allocation/Pod state | Kueue/Kubernetes/provider, consumed as fresh independent observations |
| Provider UID/resourceVersion/image ID and actual disappearance | Provider API observation; desired labels or mutation ACK alone are insufficient |
| Allocation view and release claim | Derived from current-generation Desired state + independent observations + finalization evidence |
| Secret values | Credential broker/provider secret store only; never profile/request/receipt/audit body |

## 6. Acceptance gates and current truth

This contract is a **design/contract PR**, not evidence that the vertical slice is already complete.

1. **Contract:** schema and document review; map every field to an owner and prove request/profile version compatibility.
2. **Compute Golden Path:** real authenticated Management API + PostgreSQL + kind/Kueue: submit, read receipt, observe quota/admission/Pod, fail a Workload, delete, observe absence, finalize/tombstone, recreate at a higher generation.
3. **Fault injection on the same candidate SHA:** kill the caller after durable request persistence and after the server may have committed but before ACK; kill/restart the Agent/controller between provider side effect, observation, delete and finalize; replay the same request/key. Assert stable logical IDs, no second logical resource, UNKNOWN on ambiguity, and eventual cleanup without weakening ownership checks.
4. **Provider mismatch/ownership:** same-name foreign Job/Pod, matching labels but different UID/OwnerReference, changed generation/binding, replaced Job UID, failed observer and deletion/recreation must never be promoted to verified success or silently deleted.
5. **Non-GPU path:** prove `worker` / `sandbox` profiles execute without contacting the Compute API; then prove GPU path uses the exact same Session/Run references and lifecycle contract without GPU-specific identity.
6. **Security:** tenant/project cross-read/write denial, Viewer mutation denial, forged scope rejection, expired/revoked identity, credential audience/scope/replay/expiry, audit redaction, quota enforcement and release after cancellation.
7. **Supply chain:** pinned base/action/dependency versions, dependency/security scan, SBOM, image digest, build provenance and signature verification. Record each gate independently; skips are NOT RUN, not PASS.

Existing Compute lifecycle/replay tests are valuable unit/contract evidence, but they do not by themselves satisfy authenticated browser/API acceptance, every listed process-boundary fault, provider ownership proof, credential revocation, or supply-chain provenance. Keep the production gate closed until the exact-SHA evidence is independently reviewed.

## Related existing contracts

- Compute recovery and at-least-once provider semantics: [gpu-compute-platform Provider Recovery Contract](https://github.com/avaargsh/gpu-compute-platform/blob/master/docs/PROVIDER_RECOVERY_CONTRACT.md)
- Compute lifecycle, DRA cleanup and generation fencing: [Workload Lifecycle](https://github.com/avaargsh/gpu-compute-platform/blob/master/docs/WORKLOAD_LIFECYCLE.md)
- Compute v0.1 named regression gates: [Release Acceptance v0.1](https://github.com/avaargsh/gpu-compute-platform/blob/master/docs/RELEASE_ACCEPTANCE_V0_1.md)
- ACP frozen core (unchanged): [V0.1 Freeze](../V0.1_FREEZE.md)
- End-to-end product acceptance currently tracked in [gpu-compute-platform #116](https://github.com/avaargsh/gpu-compute-platform/issues/116); ownership proof gap in [gpu-compute-platform #141](https://github.com/avaargsh/gpu-compute-platform/issues/141).
