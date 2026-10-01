from dataclasses import replace
from datetime import timedelta

import pytest

from agent_control_plane.execution_attestation import ExecutionAttestation
from agent_control_plane.execution_journal import SQLiteExecutionJournal
from agent_control_plane.execution_verification import VerificationStatus
from agent_control_plane.kubernetes_deployment_transition import (
    KubernetesDeploymentObserver,
    verify_outcome,
)
from agent_control_plane.state_transition_protocol import (
    EvidenceBundle,
    EvidenceItem,
    Principal,
    ProtocolViolation,
    ResourceIdentity,
)
from context_testkit import NOW, prepare_context_attempt
from kubernetes_testkit import build_transition


def _committed_context_attempt(tmp_path):
    (
        fixture,
        _,
        _,
        _,
        journal,
        provenance,
        attempt,
    ) = prepare_context_attempt(tmp_path)
    committed = journal.commit(
        attempt,
        completed_at=NOW + timedelta(seconds=5),
        result={"status": "APPLIED"},
    )
    return fixture, journal, provenance, committed


def _successful_verification(fixture):
    fixture["api"].deployment["spec"]["replicas"] = 30
    fixture["api"].deployment["status"]["replicas"] = 30
    fixture["api"].deployment["status"]["readyReplicas"] = 30
    fixture["api"].deployment["status"]["availableReplicas"] = 30
    fixture["api"].deployment["metadata"]["generation"] = 8
    fixture["api"].deployment["metadata"]["resourceVersion"] = "101"

    observation = KubernetesDeploymentObserver(
        fixture["api"],
        collector_name="test-observer",
        collector_version="v1",
    ).observe(
        resource=fixture["resource"],
        principal=fixture["collector"],
        observed_at=NOW + timedelta(seconds=6),
    )
    verification = verify_outcome(
        transition=fixture["transition"],
        outcome_contract=fixture["outcome"],
        observation=observation,
        safety_metrics={
            "error_rate": 0.0,
            "p95_ms": 100,
        },
        checked_at=NOW + timedelta(seconds=6),
    )
    assert verification.status is VerificationStatus.SUCCEEDED
    verification.verify()
    return observation, verification


def test_attestation_binds_committed_execution_and_context_provenance(
    tmp_path,
):
    fixture, _, provenance, committed = _committed_context_attempt(
        tmp_path
    )
    observation, verification = _successful_verification(fixture)

    attestation = ExecutionAttestation.seal(
        attestation_id="attestation-001",
        attempt=committed,
        transition=fixture["transition"],
        outcome_contract=fixture["outcome"],
        observation_evidence=observation.evidence_bundle,
        verification=verification,
    )
    attestation.verify()
    attestation.verify_attempt(committed)
    attestation.verify_evidence(
        transition=fixture["transition"],
        outcome_contract=fixture["outcome"],
        observation_evidence=observation.evidence_bundle,
        verification=verification,
    )

    assert (
        attestation.context_provenance_hash
        == provenance.provenance_hash
    )
    assert attestation.attempt_hash == committed.attempt_hash
    assert attestation.terminal_result_hash == committed.result_hash
    assert attestation.verification_hash == verification.verification_hash


def test_attestation_rejects_prepared_attempt(tmp_path):
    (
        fixture,
        _,
        _,
        _,
        _,
        _,
        attempt,
    ) = prepare_context_attempt(tmp_path)
    observation, verification = _successful_verification(fixture)

    with pytest.raises(
        ProtocolViolation,
        match="requires COMMITTED attempt",
    ):
        ExecutionAttestation.seal(
            attestation_id="attestation-prepared",
            attempt=attempt,
            transition=fixture["transition"],
            outcome_contract=fixture["outcome"],
            observation_evidence=observation.evidence_bundle,
            verification=verification,
        )


def test_attestation_rejects_non_successful_verification(tmp_path):
    fixture, _, _, committed = _committed_context_attempt(tmp_path)

    observation = KubernetesDeploymentObserver(
        fixture["api"],
        collector_name="test-observer",
        collector_version="v1",
    ).observe(
        resource=fixture["resource"],
        principal=fixture["collector"],
        observed_at=NOW + timedelta(seconds=6),
    )
    verification = verify_outcome(
        transition=fixture["transition"],
        outcome_contract=fixture["outcome"],
        observation=observation,
        safety_metrics={
            "error_rate": 0.0,
            "p95_ms": 100,
        },
        checked_at=NOW + timedelta(seconds=6),
    )
    assert verification.status is VerificationStatus.DEGRADED

    with pytest.raises(
        ProtocolViolation,
        match="requires SUCCEEDED verification",
    ):
        ExecutionAttestation.seal(
            attestation_id="attestation-not-success",
            attempt=committed,
            transition=fixture["transition"],
            outcome_contract=fixture["outcome"],
            observation_evidence=observation.evidence_bundle,
            verification=verification,
        )


def test_attestation_rejects_observation_for_other_resource(tmp_path):
    fixture, _, _, committed = _committed_context_attempt(tmp_path)
    _, verification = _successful_verification(fixture)
    other_resource = ResourceIdentity(
        provider="kubernetes",
        resource_uid="uid-other",
        namespace="prod",
        kind="Deployment",
        name="other",
    )
    item = EvidenceItem.capture(
        evidence_id="other",
        evidence_type="kubernetes_deployment",
        source="kubernetes://prod/deployment/other",
        collected_at=NOW,
        collector_name="test",
        collector_version="v1",
        principal=Principal(type="service_account", subject="observer"),
        payload={"spec": {"replicas": 30}},
    )
    other_evidence = EvidenceBundle.seal(
        resource=other_resource,
        generation=1,
        created_at=NOW,
        items=(item,),
    )

    with pytest.raises(
        ProtocolViolation,
        match="observation does not match transition resource",
    ):
        ExecutionAttestation.seal(
            attestation_id="attestation-wrong-resource",
            attempt=committed,
            transition=fixture["transition"],
            outcome_contract=fixture["outcome"],
            observation_evidence=other_evidence,
            verification=verification,
        )


def test_attestation_rejects_verification_observation_swap(tmp_path):
    fixture, _, _, committed = _committed_context_attempt(tmp_path)
    observation, verification = _successful_verification(fixture)

    mismatched = EvidenceBundle.seal(
        resource=observation.resource,
        generation=observation.evidence_bundle.generation,
        created_at=NOW + timedelta(seconds=7),
        items=observation.evidence_items,
    )
    assert mismatched.manifest_hash != verification.observation_hash

    with pytest.raises(
        ProtocolViolation,
        match="verification observation mismatch",
    ):
        ExecutionAttestation.seal(
            attestation_id="attestation-observation-swap",
            attempt=committed,
            transition=fixture["transition"],
            outcome_contract=fixture["outcome"],
            observation_evidence=mismatched,
            verification=verification,
        )


def test_verification_detects_status_tampering(tmp_path):
    fixture, _, _, _ = _committed_context_attempt(tmp_path)
    _, verification = _successful_verification(fixture)
    tampered = replace(
        verification,
        status=VerificationStatus.UNKNOWN,
    )

    with pytest.raises(
        ProtocolViolation,
        match="outcome verification digest mismatch",
    ):
        tampered.verify()


def test_attestation_detects_tampering(tmp_path):
    fixture, _, _, committed = _committed_context_attempt(tmp_path)
    observation, verification = _successful_verification(fixture)
    attestation = ExecutionAttestation.seal(
        attestation_id="attestation-tamper",
        attempt=committed,
        transition=fixture["transition"],
        outcome_contract=fixture["outcome"],
        observation_evidence=observation.evidence_bundle,
        verification=verification,
    )
    tampered = replace(
        attestation,
        observation_evidence_hash="sha256:" + "c" * 64,
    )

    with pytest.raises(
        ProtocolViolation,
        match="attestation digest mismatch",
    ):
        tampered.verify()


def test_attestation_detects_attempt_swap(tmp_path):
    fixture, _, _, committed = _committed_context_attempt(tmp_path)
    observation, verification = _successful_verification(fixture)

    attestation = ExecutionAttestation.seal(
        attestation_id="attestation-swap",
        attempt=committed,
        transition=fixture["transition"],
        outcome_contract=fixture["outcome"],
        observation_evidence=observation.evidence_bundle,
        verification=verification,
    )

    other_journal = SQLiteExecutionJournal(tmp_path / "other.db")
    other = other_journal.prepare(
        transition=fixture["transition"],
        action=fixture["action"],
        authorization=fixture["authorization"],
        fence=fixture["fence"],
        prepared_at=NOW + timedelta(seconds=7),
        context_provenance=None,
    )
    other_committed = other_journal.commit(
        other,
        completed_at=NOW + timedelta(seconds=8),
        result={"status": "APPLIED"},
    )

    with pytest.raises(
        ProtocolViolation,
        match="attempt binding mismatch",
    ):
        attestation.verify_attempt(other_committed)


def test_attestation_supports_non_context_attempt(tmp_path):
    fixture = build_transition()
    journal = SQLiteExecutionJournal(tmp_path / "plain.db")
    attempt = journal.prepare(
        transition=fixture["transition"],
        action=fixture["action"],
        authorization=fixture["authorization"],
        fence=fixture["fence"],
        prepared_at=NOW,
    )
    committed = journal.commit(
        attempt,
        completed_at=NOW + timedelta(seconds=1),
        result={"status": "APPLIED"},
    )
    observation, verification = _successful_verification(fixture)

    attestation = ExecutionAttestation.seal(
        attestation_id="attestation-plain",
        attempt=committed,
        transition=fixture["transition"],
        outcome_contract=fixture["outcome"],
        observation_evidence=observation.evidence_bundle,
        verification=verification,
    )

    assert attestation.context_provenance_hash is None
    attestation.verify_attempt(committed)
