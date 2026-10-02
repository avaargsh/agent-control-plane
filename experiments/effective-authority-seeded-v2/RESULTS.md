# Seeded Cases — Frozen Results

Do not fill B until the A result for that case is frozen.

| Case | A finding | A glue LOC | B finding | Classification | Manual semantics | Production realism | Notes |
|---|---|---:|---|---|---|---|---|
| 01 MCP + OAuth delegation | PENDING | 0 | PENDING | PENDING | PENDING | PENDING | |
| 02 MCP + Cilium egress | PENDING | 0 | PENDING | PENDING | PENDING | PENDING | |
| 03 IAM + workload identity | PENDING | 0 | PENDING | PENDING | PENDING | PENDING | |

## Case worksheet

Copy once per case.

```text
CASE:

A started_at:
A frozen_at:
A tools:
A glue LOC:
A result: FOUND / NO_FINDING
A effective principal:
A outcome:
A target:
A credential:
A maximum effect:
A approval:
A realization path:
A notes:

B started_at:
B baseline path:
B candidate path:
B new effective principal:
B new reachable outcome:
B maximum effect:
B manual semantics:
B notes:

classification: MISS / FOUND / INVALID SEED
production realism: HIGH / MEDIUM / LOW
why:
```

## Aggregate decision

Fill only after valid cases exist.

```text
valid dangerous cases:
A FOUND:
A detection rate:

manual-semantic burden:
<=200 LOC generic glue rerun performed: yes/no

decision:
CONTINUE / REDESIGN / KILL
```
