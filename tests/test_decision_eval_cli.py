import hashlib
import json
import sys

import pytest

from agent_control_plane.cli import main


def _artifact() -> dict:
    payload = {
        "schema_version": "decision-eval/v1",
        "decision_type": "mcp_tool_router",
        "adapter": "candidate-logits",
        "model_ref": "Qwen/Qwen3-0.6B",
        "dataset": {
            "sha256": "sha256:" + "a" * 64,
            "case_count": 2,
            "case_ids": ["case-a", "case-b"],
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
            "coverage": 0.5,
            "risk": 0.0,
            "false_automation_rate": 0.0,
            "fallback_rate": 0.5,
            "risk_budget": 0.0,
        },
        "fallback_evaluation": {
            "measured": True,
            "adapter": "transformers-structured-output",
            "threshold": 0.8,
            "eligible_case_count": 2,
            "fallback_case_count": 1,
            "fallback_rate": 0.5,
            "accuracy": 1.0,
            "p50_latency_ms": 22.0,
            "p95_latency_ms": 22.0,
            "mean_tokens_processed": 48.0,
            "cases": [
                {
                    "case_id": "case-b",
                    "fast_confidence": 0.55,
                    "fast_predicted": "prometheus.query",
                    "fallback_predicted": "logs.search",
                    "gold": "logs.search",
                    "correct": True,
                    "latency_ms": 22.0,
                    "tokens_processed": 48,
                }
            ],
        },
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


def _gate() -> str:
    return """apiVersion: agentplane.io/v1alpha1
kind: EvalGate
metadata:
  name: measured-system2
spec:
  suiteRef: benchmark://agent-decision-lab/tool-router-v1
  conditions:
    - metric: fallback_measured
      op: eq
      value: 1
    - metric: system2_accuracy
      op: gte
      value: 0.9
    - metric: system2_p95_latency_ms
      op: lte
      value: 100
  evidenceRequired: true
  onFailure: block
"""


def test_decision_eval_verify_cli_outputs_sealed_metrics(
    tmp_path,
    monkeypatch,
    capsys,
):
    artifact_path = tmp_path / "decision-eval.json"
    artifact_path.write_text(
        json.dumps(_artifact()),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "agent-control-plane",
            "decision-eval-verify",
            str(artifact_path),
        ],
    )

    main()

    output = json.loads(capsys.readouterr().out)
    assert output["valid"] is True
    assert output["metrics"]["system2_accuracy"] == 1.0
    assert output["metrics"]["system2_p95_latency_ms"] == 22.0


def test_decision_eval_gate_cli_uses_only_verified_artifact_metrics(
    tmp_path,
    monkeypatch,
    capsys,
):
    artifact_path = tmp_path / "decision-eval.json"
    artifact_path.write_text(
        json.dumps(_artifact()),
        encoding="utf-8",
    )
    gate_path = tmp_path / "gate.yaml"
    gate_path.write_text(_gate(), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "agent-control-plane",
            "decision-eval-gate",
            str(gate_path),
            "--artifact",
            str(artifact_path),
        ],
    )

    with pytest.raises(SystemExit) as exit_info:
        main()

    assert exit_info.value.code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["gate"]["passed"] is True


def test_decision_eval_gate_cli_rejects_tampered_artifact_before_gate(
    tmp_path,
    monkeypatch,
    capsys,
):
    value = _artifact()
    value["fallback_evaluation"]["accuracy"] = 0.0
    artifact_path = tmp_path / "decision-eval.json"
    artifact_path.write_text(
        json.dumps(value),
        encoding="utf-8",
    )
    gate_path = tmp_path / "gate.yaml"
    gate_path.write_text(_gate(), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "agent-control-plane",
            "decision-eval-gate",
            str(gate_path),
            "--artifact",
            str(artifact_path),
        ],
    )

    with pytest.raises(SystemExit) as exit_info:
        main()

    assert exit_info.value.code == 3
    output = json.loads(capsys.readouterr().out)
    assert output["valid"] is False
    assert output["reason"] == "CONTENT_DIGEST_MISMATCH"
