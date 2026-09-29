# Agent Control Plane

A thin, provider-neutral control plane for production Agent systems.

The project deliberately avoids becoming yet another Agent framework. It manages **desired state, release, binding, policy, governance, evidence and evaluation gates**, while execution semantics stay inside specialized runtimes.

## Boundary

```text
                    Agent Control Plane
┌────────────────────────────────────────────────────────┐
│ AgentBundle / AgentRelease                             │
│ Provider Bindings                                      │
│ Policy / Approval / Budget                             │
│ Placement / Rollout / Release Gate                     │
│ Canonical Session Identity + State Refs                │
│ Context Projection                                     │
│ Evidence / Eval / Governance                           │
└───────────────────────────┬────────────────────────────┘
                            |
              provider adapters / contracts
                            |
      ┌─────────────┬───────┼─────────┬──────────────┐
      v             v       v         v              v
 OpenAI Agents   Codex   Temporal   Restate   Agent Sandbox
```

## Core primitives

- **AgentBundle Manifest** — portable intent + provider extensions
- **AgentRelease** — immutable/reproducible release unit
- **Federation Bindings** — Harness / Workflow / Context / Sandbox
- **Canonical Session Identity + State Refs**
- **Context Projection Policy**
- **Evidence + Release Control**

## Design rules

- CRDs / declarative objects keep desired state, not high-frequency runtime state.
- Runtime state belongs in PostgreSQL / object storage / telemetry backends.
- The control plane must not redefine provider execution semantics.
- Evidence and evaluation gates are first-class release inputs.
- Agent-to-Agent communication is a separate Agent Fabric concern, not Service Mesh semantics.

## Golden slice

```text
Alertmanager
  -> Agent Gateway
  -> AgentRelease
  -> Harness Adapter
  -> Temporal Workflow
  -> Context Policy
  -> Memory Binding
  -> MCP Tools
  -> Sandbox
  -> Evidence
  -> Eval Gate
```

## Status

Private incubation repository. The v0.2 local controller is implemented and covered by unit/contract CI: manifest validation, binding resolution, release planning/state transitions, provider adapter registry, apply/compensation semantics, release evidence and deterministic EvalGate behavior.

The repository remains private while the v0.3/v0.4 production slice is hardened. Public release is intentionally deferred until the project has a repeatable end-to-end acceptance path rather than a collection of partial demos.
