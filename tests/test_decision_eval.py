import hashlib
import json

from agent_control_plane.decision_eval import (
    validate_decision_eval_artifact,
)


def artifact(*, measured: bool = False):
    payload = {
        "schema_version": "decision-eval/v1",
        "decision_type": "mcp_tool_router",
        "adapter": "candidate-logits",
        "model_ref": "qwen/test",
        "dataset": {
            "sha256": "sha256:" + "a" * 64,
            "case_count": 2,
            "case_ids": ["a", "b"],
        },
        "calibration_sha256": "sha256:" + "b" * 64,
        "metrics": {
            "accuracy": 0.95,
            "macro_f1": 0.94,
            "nll": 0.2,
            "brier": 0.08,
            "ece": 0.03,
            "mean_latency_ms": 12.0,
            "p50_latency_ms": 10.0,
            "p95_latency_ms": 18.0,
            "mean_tokens_processed_per_decision": 32.0,
        },
        "operating_point": {
            "threshold": 0.8,
            "coverage": 0.75,
            "risk": 0.02,
            "false_automation_rate": 0.01,
            "fallback_rate": 0.25,
            "risk_budget": 0.05,
        },
        "fallback_evaluation": (
            {
                "measured": True,
                "adapter": "transformers-structured-output",
                "threshold": 0.8,
                "eligible_case_count": 2,
                "fallback_case_count": 1,
                "fallback_rate": 0.5,
                "accuracy": 1.0,
                "p50_latency_ms": 20.0,
                "p95_latency_ms": 24.0,
                "mean_tokens_processed": 48.0,
                "cases": [
                    {
                        "case_id": "b",
                        "fast_confidence": 0.55,
                        "fast_predicted": "prometheus.query",
                        "fallback_predicted": "logs.search",
                        "gold": "logs.search",
                        "correct": True,
                        "latency_ms": 20.0,
                        "tokens_processed": 48,
                    }
                ],
            }
            if measured
            else {"measured": False}
        ),
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    digest = "sha256:" + hashlib.sha256(canonical).hexdigest()
    return {
        **payload,
        "artifact_id": "decision-eval:" + digest,
        "content_digest": digest,
    }


def test_verified_decision_artifact_exposes_sealed_gate_metrics():
    result = validate_decision_eval_artifact(
        artifact(measured=True)
    )

    assert result.valid is True
    assert result.reason is None
    assert result.metrics["accuracy"] == 0.95
    assert result.metrics["false_automation_rate"] == 0.01
    assert result.metrics["fallback_rate"] == 0.25
    assert result.metrics["fallback_measured"] == 1.0
    assert result.metrics["dataset_case_count"] == 2.0
    assert result.metrics["system2_accuracy"] == 1.0
    assert result.metrics["system2_fallback_rate"] == 0.5
    assert result.metrics["system2_p95_latency_ms"] == 24.0
    assert result.metrics["system2_mean_tokens_processed"] == 48.0


def test_decision_artifact_tampering_fails_closed():
    value = artifact()
    value["metrics"]["accuracy"] = 0.1

    result = validate_decision_eval_artifact(value)

    assert result.valid is False
    assert result.reason == "CONTENT_DIGEST_MISMATCH"
    assert result.metrics == {}


def test_unmeasured_fallback_is_explicit_gate_metric():
    result = validate_decision_eval_artifact(
        artifact(measured=False)
    )

    assert result.valid is True
    assert result.metrics["fallback_measured"] == 0.0



def test_measured_fallback_without_measurements_fails_closed():
    value = artifact(measured=False)
    value["fallback_evaluation"] = {"measured": True}

    payload = {
        key: item
        for key, item in value.items()
        if key not in {"artifact_id", "content_digest"}
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    digest = "sha256:" + hashlib.sha256(canonical).hexdigest()
    value["content_digest"] = digest
    value["artifact_id"] = "decision-eval:" + digest

    result = validate_decision_eval_artifact(value)

    assert result.valid is False
    assert result.reason == "FALLBACK_ADAPTER_REQUIRED"
