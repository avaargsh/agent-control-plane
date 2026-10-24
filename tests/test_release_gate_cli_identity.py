"""CLI regression for release/run target binding; never use sealed evidence as a target selector."""

import json
import subprocess
import sys

import yaml

from test_release_gate import GATE, evidence


def _invoke_gate(tmp_path, *args, with_evidence=True):
    manifest = tmp_path / "gate.yaml"
    manifest.write_text(yaml.safe_dump(GATE), encoding="utf-8")
    metrics = tmp_path / "metrics.json"
    metrics.write_text(
        json.dumps({"recovery_success_rate": 1.0, "false_automation_rate": 0.0}),
        encoding="utf-8",
    )
    command = [
        sys.executable, "-m", "agent_control_plane.cli", "gate", str(manifest),
        "--metrics", str(metrics),
        "--authority-digest", "sha256:" + "a" * 64,
    ]
    if with_evidence:
        path = tmp_path / "evidence.json"
        path.write_text(json.dumps(evidence()), encoding="utf-8")
        command += ["--evidence", str(path)]
    return subprocess.run(command + list(args), capture_output=True, text=True, check=False)


def test_cli_release_and_run_target_allow_exact_identity(tmp_path):
    result = _invoke_gate(
        tmp_path,
        "--expected-release-ref", "checkout-sre-golden-v1",
        "--expected-runtime-run-id", "run-001",
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["reason"] == "EVAL_GATE_PASSED"


def test_cli_rejects_a_foreign_release_target_even_with_valid_seal(tmp_path):
    result = _invoke_gate(
        tmp_path,
        "--expected-release-ref", "unrelated-release",
        "--expected-runtime-run-id", "run-001",
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["reason"] == "RELEASE_REF_MISMATCH"


def test_cli_rejects_target_pinning_without_evidence(tmp_path):
    result = _invoke_gate(
        tmp_path,
        "--expected-release-ref", "checkout-sre-golden-v1",
        with_evidence=False,
    )
    assert result.returncode != 0
    assert "evidence" in result.stderr.lower()
