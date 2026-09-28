from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class EvalViolation:
    metric: str
    op: str
    expected: float
    actual: float | None
    reason: str


@dataclass(frozen=True)
class EvalResult:
    passed: bool
    violations: tuple[EvalViolation, ...]


def _compare(actual: float, op: str, expected: float) -> bool:
    if op == "lt":
        return actual < expected
    if op == "lte":
        return actual <= expected
    if op == "gt":
        return actual > expected
    if op == "gte":
        return actual >= expected
    if op == "eq":
        return actual == expected
    raise ValueError(f"unsupported operator: {op}")


def evaluate_gate(
    gate: Mapping[str, Any],
    metrics: Mapping[str, float],
) -> EvalResult:
    violations: list[EvalViolation] = []

    for condition in gate.get("spec", {}).get("conditions", []):
        metric = condition["metric"]
        op = condition["op"]
        expected = float(condition["value"])

        if metric not in metrics:
            violations.append(
                EvalViolation(
                    metric=metric,
                    op=op,
                    expected=expected,
                    actual=None,
                    reason="MISSING_METRIC",
                )
            )
            continue

        actual = float(metrics[metric])
        if not _compare(actual, op, expected):
            violations.append(
                EvalViolation(
                    metric=metric,
                    op=op,
                    expected=expected,
                    actual=actual,
                    reason="THRESHOLD_FAILED",
                )
            )

    return EvalResult(
        passed=not violations,
        violations=tuple(violations),
    )
