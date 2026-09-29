# PR #8 Architecture Review

## Keep

- thin desired-state compiler,
- provider adapter/executor split,
- dependency-aware release resource reconciliation,
- deterministic policy before mutation,
- canonical identity + provider external refs,
- evidence-backed EvalGate,
- provider-native durable execution and sandbox lifecycle.

## Do not add

- custom Durable Agent Run runtime,
- workflow history in PostgreSQL/CRDs,
- custom MCP/A2A protocol implementation,
- generic multi-agent supervisor abstraction,
- direct Pod lifecycle management,
- provider SDK imports in compiler/reconciler core.

## Risks before merge

### 1. Sync/async boundary
The current executor protocol is synchronous while Temporal is async-native. The temporary SDK transport bridge must be replaced by an async worker/execution boundary before high-concurrency production use.

### 2. Sandbox ownership
Agent Sandbox claim adoption does not currently provide a reliable created-vs-adopted ownership signal through the transport. Rollback must remain fail-safe until ownership is provable.

### 3. Capability enforcement
Capability conformance exists as a pure contract but must run before mutation in ApplyReconciler.

### 4. Integration coverage
Unit tests use fake transports. Real provider integration tests remain required.

## Merge criterion

Keep the PR draft until capability enforcement is wired into apply and the branch has an executable test workflow. Real external integration tests may follow in a separate PR.
