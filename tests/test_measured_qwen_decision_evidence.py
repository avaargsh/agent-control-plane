import json
from pathlib import Path

import yaml

from agent_control_plane.decision_eval import validate_decision_eval_artifact
from agent_control_plane.eval_engine import evaluate_gate


ROOT = Path(__file__).parents[1]
ARTIFACT = (
    ROOT
    / "examples"
    / "evidence"
    / "qwen-mcp-router-2026-09-30.decision-eval.json"
)
GATE = ROOT / "examples" / "decision-system2-eval-gate.yaml"


def test_real_qwen_artifact_is_valid_but_fails_quality_gate() -> None:
    artifact = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    validation = validate_decision_eval_artifact(artifact)

    assert validation.valid is True
    assert validation.artifact_id == (
        "decision-eval:sha256:"
        "d829b647cfb0a803efeb464719610b4bf4f1de5c282a8c58c3a53b3f16d64a0b"
    )
    assert validation.metrics["fallback_measured"] == 1.0
    assert validation.metrics["system2_fallback_case_count"] == 16.0
    assert validation.metrics["system2_fallback_rate"] == 1.0
    assert validation.metrics["system2_accuracy"] == 0.375
    assert validation.metrics["system2_p95_latency_ms"] == (
        5929.371359500038
    )

    gate = yaml.safe_load(GATE.read_text(encoding="utf-8"))
    result = evaluate_gate(gate, validation.metrics)

    assert result.passed is False
    failed_metrics = {
        violation.metric
        for violation in result.violations
    }
    assert "system2_accuracy" in failed_metrics
    assert "system2_p95_latency_ms" in failed_metrics
