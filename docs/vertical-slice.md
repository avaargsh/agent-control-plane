# Local Vertical Slice

This repository now has one complete **dry-run control-plane slice**.

```text
AgentBundle
   |
AgentRelease
   |
Compile + Resolve
   |
ResolvedReleasePlan
   |
Provider Registry
   |
   +--> Decision Gateway
   |      low confidence -> Codex fallback
   |
   +--> Temporal Workflow
   |
   +--> MCP read-only tools
   |
   +--> Kubernetes Agent Sandbox
   |
Release Plan Evidence
   |
Eval phase
   |
Promoted (dry-run only)
```

## What is real

- portable manifests,
- schema validation,
- binding resolution,
- resolved release plan,
- provider registry,
- provider-specific preparation plans,
- release lifecycle,
- evidence record for the prepared plan.

## What is intentionally mocked

- network calls to Decision Gateway,
- Codex execution,
- Temporal workflow start,
- MCP calls,
- sandbox provisioning,
- evaluation execution,
- Kubernetes reconciliation.

This boundary is deliberate. The v0.2 goal is to prove **control-plane contracts and ownership** before adding external mutation.

## Run

```bash
python examples/reconcile_vertical_slice.py
```

The result should end in `promoted` because dry-run treats preparation as the evaluated artifact.

Real apply mode remains blocked until provider executors, deterministic policy and evidence-backed evaluation gates are implemented.
