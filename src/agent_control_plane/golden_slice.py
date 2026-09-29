from __future__ import annotations

from typing import Any, Mapping


_PHASE_ORDER = {
    "observed": 0,
    "evidence_frozen": 1,
    "decided": 2,
    "approval_pending": 3,
    "executing": 4,
    "verifying": 5,
    "promoted": 6,
    "blocked": 6,
    "rolled_back": 6,
}
_TERMINAL = {"promoted", "blocked", "rolled_back"}
_IMMUTABLE_ONCE_SET = (
    "runId",
    "evidenceId",
    "evidenceDigest",
    "decisionId",
    "releaseId",
    "workflowId",
)


def validate_golden_slice_transition(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> None:
    """Validate causal continuity between two already schema-valid envelopes."""
    before_phase = str(before["phase"])
    after_phase = str(after["phase"])

    if before_phase in _TERMINAL:
        raise ValueError(f"terminal Golden Slice phase cannot transition: {before_phase}")
    if after_phase not in _PHASE_ORDER or before_phase not in _PHASE_ORDER:
        raise ValueError("unknown Golden Slice phase")
    if _PHASE_ORDER[after_phase] < _PHASE_ORDER[before_phase]:
        raise ValueError(f"Golden Slice phase cannot move backwards: {before_phase} -> {after_phase}")

    for field in _IMMUTABLE_ONCE_SET:
        if field in before and after.get(field) != before[field]:
            raise ValueError(f"Golden Slice identity changed: {field}")

    # sandboxId intentionally excluded: execution environments are replaceable.
