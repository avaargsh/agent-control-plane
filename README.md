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

Private incubation repository. Initial work is documentation-first: contracts, schemas, adapters and one complete vertical slice before broad feature work.
