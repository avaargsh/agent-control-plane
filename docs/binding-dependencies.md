# Binding Dependency Graph

Provider binding order is now explicit.

## Why

A fixed type order is a bootstrap shortcut. Real releases have dependencies that cannot be inferred safely from provider type.

Examples:

```text
sandbox
   |
 tools
   |
 harness
  /   \
workflow decision
  \   /
 traffic
```

A Decision Gateway may depend on a Harness fallback. A Harness may depend on Tool and Sandbox bindings. Traffic should not shift until its upstream execution path exists.

## Manifest

```yaml
spec:
  type: harness
  provider: codex
  dependsOn:
    - tools-observability
    - sandbox-k8s
```

## Rules

- dependency names must exist in the resolved release,
- duplicate dependencies are invalid,
- self-dependency is invalid,
- cycles are rejected,
- apply order is a deterministic topological order,
- compensation runs in reverse actual-apply order.

## Idempotency

Executor apply is expected to be safe to retry.

An `ExecutionReceipt` contains a `changed` flag:

- `true`: this reconciliation changed provider state,
- `false`: desired state was already satisfied.

This distinction is required for safe retries and compensation.
