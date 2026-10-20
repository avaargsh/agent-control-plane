"""Fail-closed file-bound M6 evidence checks; fixtures are NOT live webhooks."""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))
from m6_acceptance_validator import GATES  # noqa: E402
from m6_materialized_evidence import verify_materialized  # noqa: E402

BASE = {
    "schema": "PG_V2_AND_WEBHOOK_EXTENSION_VERIFIED",
    "provider": "GITHUB_REPOSITORY_WEBHOOK_API",
    "github_delivery_record": "MATCHED",
    "actual_github_delivery_origin": "GITHUB_API_RECORD_CORROBORATED",
    "github_webhook_configuration": "CURRENT_ACTIVE_CONFIG_MATCHED",
    "github_webhook_tls_verification": "CURRENTLY_REQUIRED",
    "github_webhook_event_scope": "EXACT_PR_CHECK_ALLOWLIST",
    "github_webhook_secret_provisioning": "NOT_ATTESTED_BY_API",
    "webhook_endpoint": "HTTPS_DESTINATION_MATCHED",
    "pr_association": "EXACT_SCOPED_PR_MATCHED",
    "receiver_http_ack": "RESPONSE_MATCHED",
    "signal_identity": "DETERMINISTIC_MATCH",
    "inbox_state": "ACKED",
    "temporal_history": "PROVEN",
    "temporal_signal_cardinality": "EXACTLY_ONE_PROVEN",
    "raw_hmac_body_equivalence": "NOT_ATTESTED_BY_API",
    "observations_attributed_to_delivery": "NOT_PROVEN",
    "release_decision": "NO_GO",
    "scope_digest": "a" * 64,
    "workflow_digest": "b" * 64,
    "delivery_digest": "c" * 64,
    "github_event": "pull_request",
    "redelivery": False,
    "recorded_observations": 2,
}


def fixture_bundle(root):
    root.mkdir()
    manifest = {
        "schema": "m6-live-acceptance/v1",
        "source_commit": "a" * 40,
        "environment": "isolated-disposable",
        "github_delivery_kind": "real-github",
        "business_outcome_verified": False,
        "independent_proof_ref": None,
        "operator_decision": "GO",
        "reviewer_decision": "GO",
        "gates": {},
    }
    for index, gate in enumerate(GATES):
        if gate == "real_github_pull_request_delivery":
            content = json.dumps(BASE, sort_keys=True).encode()
        elif gate == "real_github_check_delivery":
            content = json.dumps({
                **BASE, "github_event": "check_run", "delivery_digest": "d" * 64,
            }, sort_keys=True).encode()
        else:
            content = ("synthetic-only-" + str(index)).encode()
        path = f"gate-{index:02d}.json"
        (root / path).write_bytes(content)
        manifest["gates"][gate] = {
            "outcome": "PASS",
            "summary": "synthetic fixture, not real-world observation",
            "evidence_sha256": hashlib.sha256(content).hexdigest(),
            "evidence_path": path,
        }
    return manifest


def test_all_bytes_match_but_never_release(tmp_path):
    manifest = fixture_bundle(tmp_path / "bundle")
    result = verify_materialized(manifest, tmp_path / "bundle")
    assert result["decision"] == "FILES_MATCHED_NOT_RELEASED"
    assert result["verified_file_count"] == len(GATES)
    assert result["release_decision"] == "NO_GO"
    assert result["business_outcome_verified"] is False
    assert result["independent_proof_ref"] is None
    assert not result["errors"]


def test_tampered_file_or_manifest_digest_denied(tmp_path):
    root = tmp_path / "bundle"
    manifest = fixture_bundle(root)
    path = root / manifest["gates"]["operator_approval"]["evidence_path"]
    path.write_bytes(b"altered after report creation")
    result = verify_materialized(manifest, root)
    assert result["decision"] == "NO_GO"
    assert any("actual file hash differs" in x for x in result["errors"])
    manifest = fixture_bundle(tmp_path / "other")
    manifest["gates"]["operator_approval"]["evidence_sha256"] = "f" * 64
    assert verify_materialized(manifest, tmp_path / "other")["decision"] == "NO_GO"


@pytest.mark.parametrize("unsafe_path", [
    "../outside.json", "/etc/passwd", "folder/../../secret.txt",
    "folder\\hidden", "no-file.json",
])
def test_missing_traversal_and_absolute_paths_denied(tmp_path, unsafe_path):
    root = tmp_path / "bundle"
    manifest = fixture_bundle(root)
    manifest["gates"]["operator_approval"]["evidence_path"] = unsafe_path
    result = verify_materialized(manifest, root)
    assert result["decision"] == "NO_GO"


def test_symlink_and_reused_evidence_paths_denied(tmp_path):
    root = tmp_path / "bundle"
    manifest = fixture_bundle(root)
    link = root / "pointer.json"
    link.symlink_to(root / manifest["gates"]["operator_approval"]["evidence_path"])
    manifest["gates"]["operator_approval"]["evidence_path"] = link.name
    assert verify_materialized(manifest, root)["decision"] == "NO_GO"
    manifest = fixture_bundle(tmp_path / "other")
    manifest["gates"]["isolated_environment"]["evidence_path"] = (
        manifest["gates"]["operator_approval"]["evidence_path"]
    )
    result = verify_materialized(manifest, tmp_path / "other")
    assert any("reused evidence file path" in x for x in result["errors"])


def test_missing_pair_identity_or_fake_delivery_denied(tmp_path):
    root = tmp_path / "bundle"
    manifest = fixture_bundle(root)
    item = manifest["gates"]["real_github_check_delivery"]
    path = root / item["evidence_path"]
    forged = json.loads(path.read_text())
    forged["workflow_digest"] = "f" * 64
    payload = json.dumps(forged, sort_keys=True).encode()
    path.write_bytes(payload)
    item["evidence_sha256"] = hashlib.sha256(payload).hexdigest()
    result = verify_materialized(manifest, root)
    assert result["decision"] == "NO_GO"
    assert any("paired delivery claims" in x for x in result["errors"])

    manifest["github_delivery_kind"] = "fixture"
    assert verify_materialized(manifest, root)["decision"] == "NO_GO"


def test_oversized_and_missing_gate_file_denied(tmp_path):
    root = tmp_path / "bundle"
    manifest = fixture_bundle(root)
    gate = manifest["gates"]["operator_approval"]
    (root / gate["evidence_path"]).write_bytes(b"x" * 4_000_001)
    assert verify_materialized(manifest, root)["decision"] == "NO_GO"
    manifest = fixture_bundle(tmp_path / "other")
    del manifest["gates"]["operator_approval"]["evidence_path"]
    assert verify_materialized(manifest, tmp_path / "other")["decision"] == "NO_GO"


def test_cli_only_file_matched_does_not_release_and_fails_on_missing_root(tmp_path):
    root = tmp_path / "bundle"
    manifest = fixture_bundle(root)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    cmd = [sys.executable, str(
        Path(__file__).resolve().parents[1] / "examples" /
        "m6_materialized_evidence.py",
    ), str(manifest_path), "--root", str(root)]
    ok = subprocess.run(cmd, check=False, capture_output=True, text=True)
    assert ok.returncode == 0
    parsed = json.loads(ok.stdout)
    assert parsed["decision"] == "FILES_MATCHED_NOT_RELEASED"
    assert parsed["release_decision"] == "NO_GO"
    assert "synthetic-only" not in ok.stdout
    missing = subprocess.run(
        cmd[:-1] + [str(tmp_path / "missing")],
        check=False, capture_output=True, text=True,
    )
    assert missing.returncode == 1
    assert json.loads(missing.stdout)["decision"] == "NO_GO"


def test_untrusted_non_object_manifest_is_no_go(tmp_path):
    root = tmp_path / "bundle"
    fixture_bundle(root)
    for value in (None, [], "text", 1, True):
        result = verify_materialized(copy.deepcopy(value), root)
        assert result["decision"] == "NO_GO"
        assert result["release_decision"] == "NO_GO"
