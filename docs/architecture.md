# Architecture

## Thesis

The control plane owns **desired state and governance**, not provider execution semantics.

```text
                        API / GitOps
                            |
                            v
┌──────────────── Agent Control Plane ────────────────┐
│ Bundle Registry                                     │
│ Release Controller                                  │
│ Binding Resolver                                    │
│ Policy / Approval / Budget                          │
│ Placement / Rollout                                 │
│ Context Projection                                  │
│ Evidence / Evaluation Gate                          │
└───────────────────────┬─────────────────────────────┘
                        |
              provider-neutral contracts
                        |
       ┌────────────────┼─────────────────┐
       v                v                 v
    Harness          Workflow          Sandbox
  OpenAI/Codex       Temporal         K8s/E2B
       |
       +---------------- Model / MCP / A2A
```

## Desired state vs runtime state

### Desired state
Suitable for declarative APIs and, where useful, Kubernetes CRDs:

- AgentBundle
- AgentRelease
- provider bindings
- rollout strategy
- policy references
- placement intent
- evaluation gates

### Runtime state
Must not be forced into CRDs:

- high-frequency run events
- messages / trajectories
- sandbox process state
- traces / spans
- artifacts
- large evidence payloads
- detailed evaluation records

These belong in transactional/state stores, object storage and observability systems.

## Core identity model

A stable control plane needs identities that survive provider changes:

```text
Agent
  └── Release
       └── Session
            └── Run
                 ├── StateRef
                 ├── ArtifactRef
                 └── EvidenceRef
```

Provider-specific thread/run IDs are attached as external references, not used as the canonical identity.

## Federation bindings

A release can bind independent providers for:

- harness
- workflow
- context/memory
- sandbox
- tools
- traffic / gateway
- model

The control plane resolves these bindings; providers still own their internal semantics.

## Release gate

A release becomes promotable only when deterministic policy and configured evaluation requirements pass.

```text
Bundle
 -> Release
 -> Resolve Bindings
 -> Policy Check
 -> Deploy / Canary
 -> Evidence
 -> Evaluation
 -> Promote / Rollback
```
