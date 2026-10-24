# Quickstart

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

## Validate manifests

```bash
agent-control-plane validate examples/manifests.yaml
```

## Compile a release plan

```bash
agent-control-plane plan examples/manifests.yaml
```

The plan stage:

1. validates each portable document,
2. resolves `bundleRef`,
3. resolves all named runtime bindings,
4. resolves every `contextProjectionPolicyRefs` entry to its full policy document,
5. produces a deterministic `ResolvedReleasePlan` containing the selected context policies.

It performs no external mutation.

A referenced ContextProjectionPolicy is included in the resolved plan so a
provider adapter can consume the exact selected policy document. Compilation
only resolves and validates the desired-state reference: it does not itself
apply redaction, restrict a live session's context, or emit projection
evidence. Those behaviors require Runtime/provider enforcement and their own
observable acceptance tests.

## Provider dry-run

```bash
python examples/provider_plan.py
```

This translates the resolved portable plan into provider-specific preparation objects for Codex, Temporal and Kubernetes Agent Sandbox.

The current adapters are examples. They intentionally do not impersonate the actual provider runtime.
