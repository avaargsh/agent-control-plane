# AI Infrastructure Stack Boundary

This document fixes the relationship between the control-plane repositories so they do not evolve into overlapping products.

## Layering

```text
AI Factory Engineering
  Facility / GPU / NVLink / RDMA / NCCL / Runtime SLO
  AcceptanceArtifact / attestation
                |
                v
AI Compute Control Plane
  Project / ComputePool / Workload
  PostgreSQL desired state
  Cluster Agent -> Kueue / Kubernetes / accelerator provider
  Observation / lifecycle evidence
                |
                v
Agent Control Plane
  AgentRelease / bindings / policy / approval
  eval gate / ReleaseEvidence / replay
        |                         |
        v                         v
Cloud Agent Runtime        Agent Decision Lab
Run <-> Workflow <->       bounded decision
Sandbox lifecycle          calibration / fallback
binding                    decision evidence
```

The repositories share control-plane principles, not ownership.

## Product ownership

### agent-control-plane — primary Agent Infra product

Owns:

- AgentBundle / AgentRelease desired state
- provider-neutral binding plans
- policy and approval boundaries
- authority inventory and admission
- frozen evidence at approval boundaries
- eval/release gates
- ReleaseEvidence and deterministic replay
- canonical provenance linking release, run, workflow, sandbox, policy and decision identities

Does not own:

- durable workflow continuation
- sandbox implementation semantics
- model/harness internals
- MCP transport semantics
- GPU scheduling
- infrastructure commissioning

### cloud-agent-runtime — lifecycle binding reference

Owns the reference contract for:

```text
Canonical Session / Run
        |
        +-> Temporal workflow identity
        +-> Sandbox binding / replacement
        +-> artifact and evidence refs
        +-> pause / resume / completion
```

It proves that a Run can outlive any particular workflow worker or sandbox instance. Temporal remains the durable orchestration provider; Kubernetes/Agent Sandbox remains the isolated execution provider.

This repository is not a second Agent control plane.

### agent-decision-lab — experimental decision subsystem

Owns bounded-decision research and the Decision Gateway contract:

```text
candidate set
   -> decision model/head
   -> calibration
   -> confidence gate
   -> System-2 fallback when needed
   -> structured decision evidence
```

A decision recommendation never grants execution authority. Deterministic policy/approval remains in the control plane/runtime boundary.

This repository is not a general Agent framework or model platform.

### gpu-compute-platform — lower compute control plane

Owns portable accelerator workload intent and lifecycle:

```text
Workload
  -> ComputePool / accelerator binding
  -> Cluster Agent
  -> Kueue / Kubernetes / provider
  -> observation / evidence
  -> finalization / tombstone
```

Agent workloads may consume it as a compute provider. Agent Control Plane must not reproduce scheduler, Kueue, DRA, MIG or provider allocation semantics.

### ai-factory-engineering — infrastructure acceptance plane

Owns cross-layer commissioning evidence:

```text
Compute -> Fabric -> Runtime
   -> EvidenceBundle
   -> Gate DAG
   -> AcceptanceDecision
   -> AcceptanceArtifact
   -> attestation / replay
```

It answers whether declared infrastructure/workload SLOs were actually proven. It does not become the workload scheduler or Agent control plane.

## Shared architectural invariant

Across all layers the reusable system pattern is:

```text
Intent / Desired State
        |
        v
Control Plane
        |
        v
Provider Binding
        |
        v
Execution Provider
        |
        v
Observed State
        |
        v
Evidence
        |
        v
Gate / Decision
        |
        v
Release / Acceptance
        |
        v
Replay
```

The commonality is the contract shape. State ownership remains local to each layer.

## Cross-project Golden Slice

The first integration slice should stay operational and falsifiable:

```text
GPU XID alert
   -> AgentRelease
   -> frozen alert/evidence snapshot
   -> Cloud Agent Run
   -> Temporal workflow
   -> Kubernetes sandbox
   -> Decision Gateway selects diagnostic tool
   -> deterministic policy authorizes read action
   -> MCP diagnostic execution
   -> optional GPU workload through AI Compute Control Plane
   -> observation/evidence
   -> AI Factory acceptance check for affected compute/fabric/runtime path
   -> Eval Gate
   -> ReleaseEvidence
   -> replay verification
```

### Identity contract

A single traceable chain must preserve or reference:

- AgentRelease ID
- canonical Session/Run ID
- Temporal Workflow ID / Run ID
- Sandbox provider identity and replacement history
- Decision ID plus model/calibration/dataset digest where applicable
- Policy digest and approval receipt
- MCP/tool execution receipt
- Compute workload ID/generation when compute is used
- infrastructure AcceptanceArtifact digest when acceptance is used
- final ReleaseEvidence digest

## Integration rules

1. **No duplicate source of truth.** Each layer owns its own durable state; upstream layers keep references and evidence.
2. **No hidden execution ownership.** Continuation, sandbox lifecycle, scheduling, tool execution and acceptance each name their provider.
3. **Evidence crosses boundaries; mutable state does not.**
4. **Provider replacement must not change canonical business identity.**
5. **Low-confidence learned decisions fall back; deterministic authorization never does.**
6. **Synthetic demos and live proofs are labeled separately.**
7. **v0.1 integration prefers one end-to-end Golden Slice over adding more providers or framework adapters.**

## v0.1 release boundary

For `agent-control-plane`, the v0.1.0 release-critical provider proof is now
the clean-clone Kubernetes Deployment `20 -> 30` Acceptance Artifact path.
Temporal, sandbox-runtime, Decision Lab, GPU Compute, and AI Factory integrations
remain adjacent evidence/integration references and are not prerequisites for
claiming the Kubernetes state-transition proof.

The cross-project Golden Slice remains useful for portfolio-level integration,
but it must not weaken or replace the repository-local release rule:

```text
exact authorized TransitionPlan
  -> fenced durable execution
  -> provider ownership proof
  -> fresh independent verification
  -> IndependentExecutionProof
```

Future cross-repository work should consume the released contracts rather than
reopening v0.1 execution ownership or introducing a second runtime/control plane.
