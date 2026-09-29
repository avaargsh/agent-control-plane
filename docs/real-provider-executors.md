# Real Provider Executors

Real provider SDKs are intentionally kept behind transport seams.

```text
ApplyReconciler
      |
      v
BindingExecutor
      |
      +-- TemporalWorkflowExecutor
      |       |
      |       v
      |   TemporalTransport ----> Temporal SDK / service
      |
      +-- OpenAIAgentsHarnessExecutor
              |
              v
          HarnessTransport -----> OpenAI Agents SDK / harness service
```

## Why

The reconciler owns release mutation ordering and compensation. It must not own Temporal workflow history or an Agents SDK model loop.

The executor returns an `ExecutionReceipt` with a canonical control-plane resource ref plus provider-owned `external_refs`. Provider identifiers never replace Agent/Release/Session/Run identities.

## Temporal

The prepared binding supplies workflow type, task queue and a deterministic workflow ID derived from release + binding identity. A production transport should use the official Temporal client and treat "already exists" as an idempotent result rather than creating a second workflow.

Rollback terminates only a workflow created by the current apply receipt. Normal Temporal retry/replay/recovery remains provider-owned.

## Harness

The harness executor declares release intent (manifest and capabilities) through `HarnessTransport`. The transport may be backed by OpenAI Agents SDK, a harness service, or another compatible runtime.

The control plane does not call `Runner` directly and does not own handoff/tool-loop semantics.
