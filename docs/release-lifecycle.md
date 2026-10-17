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

## Development versus release verification

Development PRs run the fast test workflow (`.github/workflows/test.yml`) and
relevant focused checks. A fresh-clone release package proof, historical
secret scan, and the already-published v0.1 proof **must not block every
ordinary feature PR**. Those workflows run on an explicit `workflow_dispatch`
or `v*` release tag, before a public release is claimed. This does not waive
runtime authorization, CAS, identity, signature, or fail-closed tests.

Status vocabulary: **DEV_TESTED** is local/CI confidence, **TEST_INTEGRATED**
means the named integration actually ran, **RELEASE_VERIFIED** requires the
release jobs and evidence. Never upgrade a result only because it was run in
CI; state what was truly observed.
