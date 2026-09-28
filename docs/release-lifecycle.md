# Release Lifecycle

## Compile before execute

The control plane first compiles portable desired state into a resolved plan.

```text
AgentBundle
     +
AgentRelease
     +
RuntimeBindings
     |
     v
Validate
     |
Resolve
     |
ResolvedReleasePlan
     |
Provider Prepare
     |
Deploy / Execute
```

A resolved plan contains:

- canonical release identity,
- bundle identity/version,
- resolved runtime bindings,
- policy references,
- evaluation gates,
- rollout strategy,
- placement intent.

It does **not** contain high-frequency provider runtime state.

## State machine

```text
DRAFT
  |
VALIDATED
  |
RESOLVED
  |
DEPLOYING
  |
EVALUATING
 /        \
PROMOTED  BLOCKED
  |
ROLLED_BACK
```

Rollback is allowed from deploying, evaluating or promoted states.

## Provider adapters

Adapters translate the resolved portable plan into provider-specific preparation/execution.

Examples:

- Harness adapter: OpenAI Agents / Codex / OpenCode
- Workflow adapter: Temporal / Restate
- Sandbox adapter: Kubernetes Agent Sandbox / E2B
- Tool adapter: MCP gateway
- Model adapter: hosted API / vLLM / SGLang

The adapter boundary is intentionally narrow: the control plane should not become the provider implementation.
