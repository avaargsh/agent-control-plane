from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Mapping


SCHEMA_VERSION = "decision-eval/v1"


@dataclass(frozen=True)
class DecisionEvalValidation:
    valid: bool
    reason: str | None
    metrics: Mapping[str, float]
    artifact_id: str | None = None
    content_digest: str | None = None


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        return False
    digest = value.removeprefix("sha256:")
    return len(digest) == 64 and all(
        char in "0123456789abcdef"
        for char in digest
    )


def validate_decision_eval_artifact(
    artifact: Mapping[str, Any],
) -> DecisionEvalValidation:
    """Verify a Decision Lab eval artifact and expose only sealed metrics."""
    if artifact.get("schema_version") != SCHEMA_VERSION:
        return DecisionEvalValidation(
            valid=False,
            reason="SCHEMA_VERSION_MISMATCH",
            metrics={},
        )

    content_digest = artifact.get("content_digest")
    artifact_id = artifact.get("artifact_id")
    if not _is_sha256(content_digest):
        return DecisionEvalValidation(
            valid=False,
            reason="CONTENT_DIGEST_INVALID",
            metrics={},
        )
    if artifact_id != f"decision-eval:{content_digest}":
        return DecisionEvalValidation(
            valid=False,
            reason="ARTIFACT_ID_MISMATCH",
            metrics={},
        )

    payload = {
        key: value
        for key, value in artifact.items()
        if key not in {"artifact_id", "content_digest"}
    }
    actual_digest = "sha256:" + sha256(
        _canonical_json(payload)
    ).hexdigest()
    if actual_digest != content_digest:
        return DecisionEvalValidation(
            valid=False,
            reason="CONTENT_DIGEST_MISMATCH",
            metrics={},
            artifact_id=str(artifact_id),
            content_digest=str(content_digest),
        )

    dataset = artifact.get("dataset")
    if not isinstance(dataset, Mapping):
        return DecisionEvalValidation(
            valid=False,
            reason="DATASET_PROVENANCE_REQUIRED",
            metrics={},
        )
    if not _is_sha256(dataset.get("sha256")):
        return DecisionEvalValidation(
            valid=False,
            reason="DATASET_DIGEST_INVALID",
            metrics={},
        )
    case_ids = dataset.get("case_ids")
    case_count = dataset.get("case_count")
    if (
        not isinstance(case_ids, list)
        or not case_ids
        or not all(isinstance(item, str) and item for item in case_ids)
        or not isinstance(case_count, int)
        or case_count != len(case_ids)
    ):
        return DecisionEvalValidation(
            valid=False,
            reason="DATASET_CASES_INVALID",
            metrics={},
        )

    raw_metrics = artifact.get("metrics")
    if not isinstance(raw_metrics, Mapping):
        return DecisionEvalValidation(
            valid=False,
            reason="METRICS_REQUIRED",
            metrics={},
        )

    metrics: dict[str, float] = {}
    for name, value in raw_metrics.items():
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return DecisionEvalValidation(
                valid=False,
                reason=f"METRIC_INVALID:{name}",
                metrics={},
            )
        metrics[str(name)] = float(value)

    operating = artifact.get("operating_point")
    if operating is not None:
        if not isinstance(operating, Mapping):
            return DecisionEvalValidation(
                valid=False,
                reason="OPERATING_POINT_INVALID",
                metrics={},
            )
        for name in (
            "threshold",
            "coverage",
            "risk",
            "false_automation_rate",
            "fallback_rate",
            "risk_budget",
        ):
            value = operating.get(name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return DecisionEvalValidation(
                    valid=False,
                    reason=f"OPERATING_METRIC_INVALID:{name}",
                    metrics={},
                )
            metrics[name] = float(value)

    fallback = artifact.get("fallback_evaluation")
    if not isinstance(fallback, Mapping):
        return DecisionEvalValidation(
            valid=False,
            reason="FALLBACK_EVALUATION_REQUIRED",
            metrics={},
        )
    measured = fallback.get("measured")
    if not isinstance(measured, bool):
        return DecisionEvalValidation(
            valid=False,
            reason="FALLBACK_MEASURED_INVALID",
            metrics={},
        )
    metrics["fallback_measured"] = 1.0 if measured else 0.0
    metrics["dataset_case_count"] = float(case_count)

    return DecisionEvalValidation(
        valid=True,
        reason=None,
        metrics=metrics,
        artifact_id=str(artifact_id),
        content_digest=str(content_digest),
    )
