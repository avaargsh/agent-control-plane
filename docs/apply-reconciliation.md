# Apply Reconciliation

The control plane now separates four concerns:

```text
Portable Desired State
        |
        v
Provider prepare()
        |
        v
Provider executor apply()
        |
        v
Execution Receipts / Evidence
        |
        v
Evaluation Gates
        |
     +--+--+
     |     |
 Promote  Block
```

## Preparation vs execution

`ProviderAdapter.prepare()` is a pure translation step.

It should be safe to:

- inspect,
- test,
- dry-run,
- record as evidence.

`BindingExecutor.apply()` owns provider mutation.

This separation prevents validation code from silently creating external resources.

## Dependency-driven execution order

Bindings are applied in the explicit dependency order produced by the resolved
release graph. There is no semantic global ordering by binding type.

This matters for ownership: the control plane coordinates declared release
dependencies, but it does not infer business workflow sequencing from labels
such as `workflow`, `sandbox`, `tool`, or `harness`.

Missing dependencies and dependency cycles fail closed before provider mutation.

## Promotion

A release reaches `PROMOTED` only when every supplied EvalGate passes.

Missing metrics fail closed.

## Execution receipts

Every applied binding returns an `ExecutionReceipt` containing:

- binding name,
- provider type/name,
- resource reference,
- changed flag,
- provider evidence.

Receipts feed the release evidence package and future rollback/replay work.
