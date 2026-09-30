from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math
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


def _is_finite_number(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = quantile * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return (
        ordered[lower] * (1.0 - weight)
        + ordered[upper] * weight
    )


_FALLBACK_FIELDS = {
    "measured",
    "adapter",
    "threshold",
    "eligible_case_count",
    "fallback_case_count",
    "fallback_rate",
    "accuracy",
    "p50_latency_ms",
    "p95_latency_ms",
    "mean_tokens_processed",
    "parse_valid_rate",
    "cases",
}
_FALLBACK_CASE_FIELDS = {
    "case_id",
    "fast_confidence",
    "fast_predicted",
    "fallback_predicted",
    "gold",
    "correct",
    "latency_ms",
    "tokens_processed",
    "parse_valid",
}


def _fallback_semantics_reason(
    fallback: Mapping[str, Any],
    *,
    dataset_case_ids: list[str],
) -> str | None:
    if not set(fallback).issubset(_FALLBACK_FIELDS):
        return "FALLBACK_FIELDS_INVALID"

    threshold = fallback.get("threshold")
    if (
        not _is_finite_number(threshold)
        or not 0.0 <= float(threshold) <= 1.0
    ):
        return "FALLBACK_THRESHOLD_INVALID"

    eligible = fallback.get("eligible_case_count")
    if (
        isinstance(eligible, bool)
        or not isinstance(eligible, int)
        or eligible != len(dataset_case_ids)
    ):
        return "FALLBACK_ELIGIBLE_CASE_COUNT_MISMATCH"

    fallback_count = fallback.get("fallback_case_count")
    if (
        isinstance(fallback_count, bool)
        or not isinstance(fallback_count, int)
        or not 0 <= fallback_count <= eligible
    ):
        return "FALLBACK_CASE_COUNT_INVALID"

    rate = fallback.get("fallback_rate")
    if not _is_finite_number(rate):
        return "FALLBACK_RATE_INVALID"
    expected_rate = (
        fallback_count / eligible
        if eligible
        else 0.0
    )
    if not math.isclose(
        float(rate),
        expected_rate,
        rel_tol=1e-9,
        abs_tol=1e-9,
    ):
        return "FALLBACK_RATE_MISMATCH"

    cases = fallback.get("cases")
    if not isinstance(cases, list):
        return "FALLBACK_CASES_REQUIRED"
    if len(cases) != fallback_count:
        return "FALLBACK_CASE_COUNT_MISMATCH"

    dataset_ids = set(dataset_case_ids)
    seen: set[str] = set()
    latencies: list[float] = []
    token_counts: list[int] = []
    parse_values: list[bool] = []
    correct_count = 0

    for case in cases:
        if not isinstance(case, Mapping):
            return "FALLBACK_CASE_INVALID"
        if not set(case).issubset(_FALLBACK_CASE_FIELDS):
            return "FALLBACK_CASE_FIELDS_INVALID"
        case_id = case.get("case_id")
        if (
            not isinstance(case_id, str)
            or not case_id
            or case_id not in dataset_ids
        ):
            return "FALLBACK_CASE_ID_INVALID"
        if case_id in seen:
            return "FALLBACK_CASE_ID_DUPLICATE"
        seen.add(case_id)

        confidence = case.get("fast_confidence")
        if (
            not _is_finite_number(confidence)
            or not 0.0 <= float(confidence) <= 1.0
        ):
            return "FALLBACK_CONFIDENCE_INVALID"

        for field in (
            "fast_predicted",
            "fallback_predicted",
            "gold",
        ):
            value = case.get(field)
            if not isinstance(value, str) or not value:
                return f"FALLBACK_CASE_FIELD_INVALID:{field}"

        correct = case.get("correct")
        if not isinstance(correct, bool):
            return "FALLBACK_CORRECT_INVALID"
        correct_count += int(correct)

        latency = case.get("latency_ms")
        if (
            not _is_finite_number(latency)
            or float(latency) < 0
        ):
            return "FALLBACK_LATENCY_INVALID"
        latencies.append(float(latency))

        tokens = case.get("tokens_processed")
        if tokens is not None:
            if (
                isinstance(tokens, bool)
                or not isinstance(tokens, int)
                or tokens < 0
            ):
                return "FALLBACK_TOKENS_INVALID"
            token_counts.append(tokens)

        parse_valid = case.get("parse_valid")
        if parse_valid is not None:
            if not isinstance(parse_valid, bool):
                return "FALLBACK_PARSE_VALID_INVALID"
            parse_values.append(parse_valid)

    accuracy = fallback.get("accuracy")
    p50 = fallback.get("p50_latency_ms")
    p95 = fallback.get("p95_latency_ms")
    mean_tokens = fallback.get("mean_tokens_processed")

    if fallback_count == 0:
        if any(
            value is not None
            for value in (accuracy, p50, p95, mean_tokens)
        ):
            return "FALLBACK_EMPTY_MEASUREMENTS_NON_NULL"
    else:
        if not _is_finite_number(accuracy):
            return "FALLBACK_ACCURACY_INVALID"
        expected_accuracy = correct_count / fallback_count
        if not math.isclose(
            float(accuracy),
            expected_accuracy,
            rel_tol=1e-9,
            abs_tol=1e-9,
        ):
            return "FALLBACK_ACCURACY_MISMATCH"

        if (
            not _is_finite_number(p50)
            or not _is_finite_number(p95)
        ):
            return "FALLBACK_LATENCY_PERCENTILES_INVALID"
        if not math.isclose(
            float(p50),
            _percentile(latencies, 0.5),
            rel_tol=1e-9,
            abs_tol=1e-6,
        ):
            return "FALLBACK_P50_MISMATCH"
        if not math.isclose(
            float(p95),
            _percentile(latencies, 0.95),
            rel_tol=1e-9,
            abs_tol=1e-6,
        ):
            return "FALLBACK_P95_MISMATCH"

        expected_tokens = (
            sum(token_counts) / len(token_counts)
            if token_counts
            else None
        )
        if expected_tokens is None:
            if mean_tokens is not None:
                return "FALLBACK_MEAN_TOKENS_MISMATCH"
        elif (
            not _is_finite_number(mean_tokens)
            or not math.isclose(
                float(mean_tokens),
                expected_tokens,
                rel_tol=1e-9,
                abs_tol=1e-9,
            )
        ):
            return "FALLBACK_MEAN_TOKENS_MISMATCH"

    parse_rate = fallback.get("parse_valid_rate")
    if parse_rate is not None:
        if (
            not _is_finite_number(parse_rate)
            or not 0.0 <= float(parse_rate) <= 1.0
        ):
            return "FALLBACK_PARSE_VALID_RATE_INVALID"
        if not parse_values:
            return "FALLBACK_PARSE_VALID_CASES_INCOMPLETE"
        expected_parse_rate = (
            sum(parse_values) / len(parse_values)
        )
        if not math.isclose(
            float(parse_rate),
            expected_parse_rate,
            rel_tol=1e-9,
            abs_tol=1e-9,
        ):
            return "FALLBACK_PARSE_VALID_RATE_MISMATCH"

    return None


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        return False
    digest = value.removeprefix("sha256:")
    return len(digest) == 64 and all(
        char in "0123456789abcdef"
        for char in digest
    )


_REQUIRED_TOP_LEVEL_FIELDS = {
    "schema_version",
    "artifact_id",
    "content_digest",
    "decision_type",
    "adapter",
    "dataset",
    "metrics",
    "fallback_evaluation",
}
_ALLOWED_TOP_LEVEL_FIELDS = _REQUIRED_TOP_LEVEL_FIELDS | {
    "model_ref",
    "calibration_sha256",
    "operating_point",
}
_METRIC_FIELDS = {
    "accuracy",
    "macro_f1",
    "nll",
    "brier",
    "ece",
    "mean_latency_ms",
    "p50_latency_ms",
    "p95_latency_ms",
    "mean_tokens_processed_per_decision",
}
_OPERATING_FIELDS = {
    "threshold",
    "coverage",
    "risk",
    "false_automation_rate",
    "fallback_rate",
    "risk_budget",
}


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

    artifact_fields = set(artifact)
    if (
        not _REQUIRED_TOP_LEVEL_FIELDS.issubset(artifact_fields)
        or not artifact_fields.issubset(_ALLOWED_TOP_LEVEL_FIELDS)
    ):
        return DecisionEvalValidation(
            valid=False,
            reason="ARTIFACT_FIELDS_INVALID",
            metrics={},
        )

    for field in ("decision_type", "adapter"):
        value = artifact.get(field)
        if not isinstance(value, str) or not value:
            return DecisionEvalValidation(
                valid=False,
                reason=f"{field.upper()}_INVALID",
                metrics={},
            )

    model_ref = artifact.get("model_ref")
    if model_ref is not None and not isinstance(model_ref, str):
        return DecisionEvalValidation(
            valid=False,
            reason="MODEL_REF_INVALID",
            metrics={},
        )

    calibration = artifact.get("calibration_sha256")
    if calibration is not None and not _is_sha256(calibration):
        return DecisionEvalValidation(
            valid=False,
            reason="CALIBRATION_DIGEST_INVALID",
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
    if not isinstance(dataset, Mapping) or set(dataset) != {
        "sha256",
        "case_count",
        "case_ids",
    }:
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
        or isinstance(case_count, bool)
        or not isinstance(case_count, int)
        or case_count != len(case_ids)
        or len(set(case_ids)) != len(case_ids)
    ):
        return DecisionEvalValidation(
            valid=False,
            reason="DATASET_CASES_INVALID",
            metrics={},
        )

    raw_metrics = artifact.get("metrics")
    if (
        not isinstance(raw_metrics, Mapping)
        or set(raw_metrics) != _METRIC_FIELDS
    ):
        return DecisionEvalValidation(
            valid=False,
            reason="METRICS_FIELDS_INVALID",
            metrics={},
        )

    metrics: dict[str, float] = {}
    for name in ("accuracy", "macro_f1", "ece"):
        value = raw_metrics.get(name)
        if (
            not _is_finite_number(value)
            or not 0.0 <= float(value) <= 1.0
        ):
            return DecisionEvalValidation(
                valid=False,
                reason=f"METRIC_RANGE_INVALID:{name}",
                metrics={},
            )
        metrics[name] = float(value)

    for name in (
        "nll",
        "brier",
        "mean_latency_ms",
        "p50_latency_ms",
        "p95_latency_ms",
    ):
        value = raw_metrics.get(name)
        if not _is_finite_number(value) or float(value) < 0:
            return DecisionEvalValidation(
                valid=False,
                reason=f"METRIC_RANGE_INVALID:{name}",
                metrics={},
            )
        metrics[name] = float(value)

    token_mean = raw_metrics.get(
        "mean_tokens_processed_per_decision"
    )
    if token_mean is not None:
        if (
            not _is_finite_number(token_mean)
            or float(token_mean) < 0
        ):
            return DecisionEvalValidation(
                valid=False,
                reason=(
                    "METRIC_RANGE_INVALID:"
                    "mean_tokens_processed_per_decision"
                ),
                metrics={},
            )
        metrics["mean_tokens_processed_per_decision"] = float(
            token_mean
        )

    operating = artifact.get("operating_point")
    if operating is not None:
        if (
            not isinstance(operating, Mapping)
            or set(operating) != _OPERATING_FIELDS
        ):
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
        ):
            value = operating.get(name)
            if (
                not _is_finite_number(value)
                or not 0.0 <= float(value) <= 1.0
            ):
                return DecisionEvalValidation(
                    valid=False,
                    reason=f"OPERATING_METRIC_RANGE_INVALID:{name}",
                    metrics={},
                )
            metrics[name] = float(value)

        risk_budget = operating.get("risk_budget")
        if risk_budget is not None:
            if (
                not _is_finite_number(risk_budget)
                or not 0.0 <= float(risk_budget) <= 1.0
            ):
                return DecisionEvalValidation(
                    valid=False,
                    reason="OPERATING_METRIC_RANGE_INVALID:risk_budget",
                    metrics={},
                )
            metrics["risk_budget"] = float(risk_budget)

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

    if measured:
        adapter = fallback.get("adapter")
        if not isinstance(adapter, str) or not adapter:
            return DecisionEvalValidation(
                valid=False,
                reason="FALLBACK_ADAPTER_REQUIRED",
                metrics={},
            )

        numeric_fields = {
            "threshold": "system2_threshold",
            "eligible_case_count": "system2_eligible_case_count",
            "fallback_case_count": "system2_fallback_case_count",
            "fallback_rate": "system2_fallback_rate",
            "accuracy": "system2_accuracy",
            "p50_latency_ms": "system2_p50_latency_ms",
            "p95_latency_ms": "system2_p95_latency_ms",
            "mean_tokens_processed": "system2_mean_tokens_processed",
        }
        for source, metric_name in numeric_fields.items():
            value = fallback.get(source)
            if value is None and source in {
                "accuracy",
                "p50_latency_ms",
                "p95_latency_ms",
                "mean_tokens_processed",
            }:
                continue
            if not _is_finite_number(value):
                return DecisionEvalValidation(
                    valid=False,
                    reason=f"FALLBACK_METRIC_INVALID:{source}",
                    metrics={},
                )
            metrics[metric_name] = float(value)

        cases = fallback.get("cases")
        if not isinstance(cases, list):
            return DecisionEvalValidation(
                valid=False,
                reason="FALLBACK_CASES_REQUIRED",
                metrics={},
            )
        semantic_reason = _fallback_semantics_reason(
            fallback,
            dataset_case_ids=case_ids,
        )
        if semantic_reason is not None:
            return DecisionEvalValidation(
                valid=False,
                reason=semantic_reason,
                metrics={},
            )

    return DecisionEvalValidation(
        valid=True,
        reason=None,
        metrics=metrics,
        artifact_id=str(artifact_id),
        content_digest=str(content_digest),
    )
