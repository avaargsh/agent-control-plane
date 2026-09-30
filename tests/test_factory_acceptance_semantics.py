import hashlib
import json

from agent_control_plane.factory_acceptance import (
    validate_factory_acceptance_artifact,
)


def _digest(payload):
    return "sha256:" + hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def _artifact(*, disposition="ACCEPT", accepted=True, statuses=("PASS",)):
    payload = {
        "apiVersion": "aifactory.engineering/v1alpha1",
        "kind": "AcceptanceArtifact",
        "caseId": "factory-semantic-regression",
        "issuedAt": "2026-09-30T13:00:00+00:00",
        "disposition": disposition,
        "accepted": accepted,
        "gates": [
            {
                "gateId": f"gate-{index}",
                "status": status,
                "reasons": [],
            }
            for index, status in enumerate(statuses)
        ],
        "reasons": [],
        "evidenceRefs": {
            "runtime": "evidence://runtime/live-001",
        },
    }
    return {**payload, "digest": _digest(payload)}


def test_accept_requires_all_gate_statuses_to_pass():
    result = validate_factory_acceptance_artifact(
        _artifact(statuses=("PASS", "FAIL"))
    )

    assert result.valid is False
    assert result.reason == "GATE_DISPOSITION_MISMATCH"


def test_hold_requires_warning_or_pending_gate():
    result = validate_factory_acceptance_artifact(
        _artifact(
            disposition="HOLD",
            accepted=False,
            statuses=("PASS",),
        )
    )

    assert result.valid is False
    assert result.reason == "GATE_DISPOSITION_MISMATCH"


def test_reject_requires_failed_error_or_blocked_gate():
    result = validate_factory_acceptance_artifact(
        _artifact(
            disposition="REJECT",
            accepted=False,
            statuses=("PASS",),
        )
    )

    assert result.valid is False
    assert result.reason == "GATE_DISPOSITION_MISMATCH"


def test_duplicate_gate_ids_are_rejected_even_with_valid_digest():
    artifact = _artifact(statuses=("PASS", "PASS"))
    artifact["gates"][1]["gateId"] = artifact["gates"][0]["gateId"]
    payload = {
        key: value
        for key, value in artifact.items()
        if key != "digest"
    }
    artifact["digest"] = _digest(payload)

    result = validate_factory_acceptance_artifact(artifact)

    assert result.valid is False
    assert result.reason == "GATE_ID_DUPLICATE"
