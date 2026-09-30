import importlib.util
import json
from pathlib import Path

import yaml

from agent_control_plane.apply_reconciler import ApplyReconciler
from agent_control_plane.release_evidence import verify_release_evidence


ROOT = Path(__file__).parents[1]
ARTIFACT_PATH = (
    ROOT
    / "examples"
    / "evidence"
    / "qwen-mcp-router-2026-09-30.decision-eval.json"
)
GATE_PATH = ROOT / "examples" / "decision-system2-eval-gate.yaml"


def _golden_module():
    path = ROOT / "examples" / "gpu_xid_golden_incident.py"
    spec = importlib.util.spec_from_file_location(
        "gpu_xid_golden_incident",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_real_qwen_artifact_produces_blocked_replayable_release_evidence():
    golden = _golden_module()
    providers, executors = golden.registries()

    artifact = json.loads(
        ARTIFACT_PATH.read_text(encoding="utf-8")
    )
    gate = yaml.safe_load(
        GATE_PATH.read_text(encoding="utf-8")
    )

    result = ApplyReconciler(
        providers=providers,
        executors=executors,
    ).reconcile(
        golden.plan(),
        decision_eval_artifact=artifact,
        eval_gates=[gate],
    )

    assert result.phase == "blocked"
    assert result.eval_results[0].passed is False
    assert result.evidence["decision_eval"]["artifact_id"] == (
        artifact["artifact_id"]
    )
    assert result.evidence["phase"] == "blocked"
    assert verify_release_evidence(result.evidence)

    failed_metrics = {
        violation.metric
        for violation in result.eval_results[0].violations
    }
    assert "system2_accuracy" in failed_metrics
    assert "system2_p95_latency_ms" in failed_metrics
