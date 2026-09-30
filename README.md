# Agent Control Plane

A thin, provider-neutral control plane for production Agent systems.

It deliberately does **not** become another Agent framework or durable runtime. Instead, it manages desired state, bindings, policy/approval, evidence, release decisions and replay while execution stays in specialized runtimes such as Temporal and Kubernetes sandboxes.

## Why this exists

Production Agent systems usually span multiple execution domains:

- a durable workflow engine owns continuation
- a sandbox owns isolated compute
- MCP/tool providers expose capabilities
- a harness owns model/tool interaction semantics
- release policy decides whether an execution is allowed to advance

Without a control plane, those identities and decisions drift apart. This project keeps one provenance chain across them.

## Golden incident

```text
GPU XID Alert
  -> Frozen approved evidence
  -> AgentRelease / Binding DAG
  -> Kubernetes Sandbox
  -> Temporal Workflow
  -> Tool / Harness / Decision bindings
  -> Eval Gate
  -> ReleaseEvidence
  -> Replay Verification
```

The important invariant is not "one framework owns everything". It is that the control plane can prove which release, workflow, sandbox, evidence snapshot and decision belong to the same run.

## Five-minute demo

Requirements: Python 3.11+.

```bash
make setup
make test
make demo
```

The deterministic demo uses in-memory execution and writes:

```text
.artifacts/demo/release-evidence-summary.json
```

It demonstrates approval freeze/resume, ordered binding execution, evaluation, sealed release evidence and replay verification.

## Live smoke boundary

The repository also includes SDK-neutral runtime clients plus CLI transports for Kubernetes and Temporal.

```text
KubernetesSandboxExecutor
  -> KubernetesSandboxClient
  -> KubectlApi
  -> kubectl
  -> Kubernetes API

TemporalWorkflowExecutor
  -> TemporalWorkflowClient
  -> TemporalCliApi
  -> temporal CLI
  -> Temporal service
```

Configure a local environment:

```bash
export KUBE_CONTEXT=kind-agent-control-plane
export KUBE_NAMESPACE=agent-runtime
export TEMPORAL_ADDRESS=127.0.0.1:7233
make preflight
make smoke
```

Normal unit CI does not imply that a live Kubernetes cluster or Temporal server was exercised.

## Architecture

```text
                         Agent Control Plane

┌─────────────────────────────────────────────────────────────┐
│ AgentRelease / Desired State                                │
│ Provider Bindings                                           │
│ Policy / Approval                                           │
│ Placement / Rollout                                         │
│ Frozen Evidence                                             │
│ Eval Gate                                                   │
│ ReleaseEvidence / Replay                                    │
└───────────────────────┬─────────────────────────────────────┘
                        │ provider-neutral contracts
          ┌─────────────┼───────────────┬────────────────┐
          ▼             ▼               ▼                ▼
      Harness        Workflow         Sandbox           Tools
       Codex         Temporal       Kubernetes           MCP
                        │               │
                  workflowId/runId   sandbox UID
                        └───────┬───────┘
                                ▼
                         ReleaseEvidence
                                ▼
                         Replay Verification
```

## Design principles

- **Thin control plane.** Desired state, policy and release control stay here; provider execution semantics do not.
- **Continuation ownership is explicit.** Temporal/Restate own durable workflow continuation.
- **Sandbox ownership is explicit.** Kubernetes/Agent Sandbox owns isolated execution lifecycle.
- **Evidence is immutable at approval boundaries.** Resume uses the frozen snapshot, not a live reread.
- **Identity is first-class.** Release, workflow, sandbox, evidence and decision identities remain correlated.
- **Replay beats hidden reasoning.** Auditing relies on structured evidence and receipts, not model chain-of-thought.
- **Provider SDKs are optional.** Narrow protocols permit CLI, SDK or service transports.

## Core primitives

- AgentBundle / AgentRelease
- Federation Bindings
- ResolvedReleasePlan
- Provider Registry
- Binding Executors
- FrozenEvidence
- Eval Gates
- ReleaseEvidence
- deterministic replay digest
- AgentAuthorityEnvelope + Fleet/Agent authority inventory
- deployment-time authority drift admission

## Current status

v0.1 release candidate.

Implemented:

- manifest/binding planning
- provider registry and executor boundary
- apply + compensation semantics
- FrozenEvidence approval/resume path
- Fleet/Agent AuthorityEnvelope inventory and fail-closed drift admission
- replay-verifiable ReleaseEvidence
- Kubernetes sandbox runtime client
- Temporal workflow runtime client
- kubectl and Temporal CLI transports
- deterministic GPU XID Golden Incident
- AgentOS v3.2 Binding / Policy Projection / Evidence Provenance / ToolContract schemas
- CapabilityIntent → Rego v1 compilation with real OPA allow/deny enforcement
- cross-repository policy-digest provenance proof through Temporal, Kubernetes sandbox replacement and Evidence replay
- opt-in live smoke preflight
- `make demo` / `make smoke` developer workflow

Not yet claimed:

- a production multi-tenant controller
- HA control-plane deployment
- live end-to-end testing against every supported provider
- a new durable Agent runtime
- ownership of Temporal/Sandbox provider semantics
- production-grade secret, budget and tenancy backends

See [DEVELOPMENT.md](DEVELOPMENT.md) and [RELEASE_READINESS.md](RELEASE_READINESS.md).

## Non-goal

This project is not trying to replace Temporal, Restate, OpenAI Agents, Codex, MCP, Kubernetes Agent Sandbox or agent gateways. Its job is to make those systems governable as one release/evidence plane.
