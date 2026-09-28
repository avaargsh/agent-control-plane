# Evaluation Gates

Evaluation is a release-control input, not a dashboard-only concern.

## Gate example

```yaml
spec:
  suiteRef: benchmark://agent-decision-lab/tool-router-v1
  conditions:
    - metric: accuracy
      op: gte
      value: 0.90
    - metric: ece
      op: lte
      value: 0.05
    - metric: false_automation_rate
      op: lte
      value: 0.01
  onFailure: block
```

## Fail closed

A configured gate fails if:

- a required metric is missing,
- a threshold is violated,
- the evaluator cannot produce the required evidence.

A missing metric must not be interpreted as a pass.

## Why this matters

Agent releases can regress in ways that ordinary functional tests miss:

- higher false automation,
- worse calibration,
- slower decision latency,
- higher fallback rate,
- worse tool routing,
- increased unsupported claims.

These are release properties and should be promotable/rollback-able like any other production quality signal.
