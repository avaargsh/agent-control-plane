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


## Target-bound evidence gate (opt-in security hardening)

A sealed `ReleaseEvidence` object proves that its contents have not changed; it
does **not** prove that the evidence belongs to the Release or Run selected for
promotion. The existing `release_ref` and `runtime_run_id` fields therefore
need an independent, trusted expected identity at the release-admission
boundary. Do not derive expected identities from the evidence payload being
checked.

For example, after an operator has independently resolved the intended
AgentRelease, current Runtime Run and trusted authority digest:

```sh
agent-control-plane gate examples/golden-slice.yaml \
  --metrics /path/to/metrics.json \
  --evidence /path/to/sealed-release-evidence.json \
  --authority-digest "sha256:${TRUSTED_AUTHORITY_SHA256}" \
  --expected-release-ref "chosen-release" \
  --expected-runtime-run-id "authoritative-run-id"
```

When an expected identity is provided, a mismatched Release or Run results in
`BLOCK`, even if the evidence is correctly sealed and every metric passes.
Expected-target options require `--evidence`; metric-only `gate` remains
available for compatibility and is **not** proof of a specific release's
production authorization. Existing callers of `evaluate_release_gate` can
supply `expected_release_ref` and `expected_runtime_run_id` independently.

This is a compatibility-safe safeguard in the pre-existing secondary release
gate, not an authorization token, operator identity proof, signed producer
attestation, canonical Session/Run authority reader, or automatic promotion.
A production admission service must source trusted identities from its own
release inventory and canonical Runtime state, and must still verify approval,
operation ownership, policy, and independent evidence before a provider
mutation. None of the frozen v0.1 TransitionPlan or proof schemas change.
