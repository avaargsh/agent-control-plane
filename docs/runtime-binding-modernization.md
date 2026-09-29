# Runtime Binding Modernization

## Boundary

The control plane owns **what should run, where it may run, what it may access, and whether a release is safe to promote**. Existing runtimes own **how execution happens**.

A binding is therefore desired-state intent plus provider configuration. It is not a portable reimplementation of provider semantics.

## Ownership matrix

| Binding | Control plane owns | Provider owns |
| --- | --- | --- |
| Harness | release identity, capabilities, policy refs | model loop, tool dispatch, handoff mechanics |
| Workflow | workflow selection, timeout/retry intent, refs | durable history, timers, waits, replay |
| Sandbox | isolation/placement intent, template refs | process/filesystem lifecycle, snapshots |
| Tool | capability allowlist, auth/policy refs | MCP transport and tool execution |
| Traffic/Fabric | routing intent, policy refs | MCP/A2A/LLM proxying and transport |
| Decision | model/calibration refs, confidence policy | scoring/inference implementation |

## Contract rules

1. Canonical Agent/Release/Session/Run identities remain provider-neutral.
2. Provider run/thread/workflow/sandbox IDs are external refs.
3. High-frequency events, trajectories and workflow history never become desired state.
4. Provider adapters compile intent; executors mutate external systems.
5. Dependency ordering coordinates release resources, not business workflow steps.
6. Compensation reverses control-plane apply operations; it does not replace provider recovery semantics.

## Target binding graph

```text
AgentRelease
  |
  +-- WorkflowBinding (Temporal)
  |      +-- HarnessBinding (OpenAI Agents / Codex)
  |             +-- DecisionBinding
  |             +-- ToolCapabilityBinding (MCP)
  |             +-- SandboxBinding (Kubernetes Agent Sandbox)
  |
  +-- TrafficBinding (agentgateway: MCP / A2A / LLM)
  |
  +-- Evidence + EvalGate
```

## Multi-agent semantics

Do not encode framework-specific labels such as "supervisor" or "swarm" as the portable abstraction. Model transitions using ownership:

- **dispatch**: execution ownership transfers; continuation stays with the caller.
- **handoff**: execution and continuation ownership transfer.
- **workflow**: continuation is owned by the workflow engine.
- **team/federation**: execution ownership may be distributed; global context ownership must be explicit.

This keeps the contract stable across harness and protocol changes.
