# Provider Adapters

## Why adapters exist

The portable core should not pretend all runtimes have identical semantics.

Instead:

```text
ResolvedReleasePlan
        |
        +--> Harness Adapter
        +--> Workflow Adapter
        +--> Sandbox Adapter
        +--> Tool Adapter
        +--> Model Adapter
```

Each adapter translates stable control-plane intent into provider-specific preparation.

## Adapter contract

A provider adapter identifies:

- `provider_type`
- `provider_name`

and implements:

```python
prepare(plan, binding) -> provider_specific_plan
```

Preparation is intentionally separated from execution.

This allows:

- validation before mutation,
- policy checks,
- plan inspection,
- dry-run,
- release evidence,
- easier testing.

## Initial examples

### Codex Harness

Owns coding-agent execution semantics. The control plane provides canonical Release/Session identity and capability bindings.

### Temporal Workflow

Owns durable workflow state, retries, timers and waits.

The control-plane runtime adapter derives a stable operation identity from the
canonical Release reference, Workflow ID, Workflow type, Task Queue, and desired
input. A retry of the same intent must reuse this identity; changing the desired
input under the same Workflow ID produces a different identity.

An existing nonterminal Workflow is attachable only when its recorded
`operationId` matches the requested identity. Missing or mismatched ownership
metadata is an ambiguity, not permission to adopt the Workflow. A concrete
Temporal transport must persist the operation identity in server-visible
Workflow metadata (for example, memo) and return it from `describe`; it must
not infer ownership from Workflow ID or RUNNING status alone.

This deterministic reference identity is an idempotency/ownership fence, not an
authorization credential or proof that a provider-side effect completed.

### Kubernetes Agent Sandbox

Owns isolated execution environment lifecycle. A binding may express portable intent such as isolation class, warm-pool preference and placement constraints.

## Non-goal

Provider adapters must not grow into a shadow implementation of the provider itself.
