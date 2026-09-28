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

## Deterministic execution order

The local reconciler currently applies bindings in a deterministic type order:

1. sandbox
2. tool
3. model
4. context
5. memory
6. harness
7. workflow
8. decision
9. traffic

This is a bootstrap rule, not a final dependency model. A later release graph should express explicit dependencies.

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
