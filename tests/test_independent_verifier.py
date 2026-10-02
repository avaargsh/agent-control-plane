import copy
import json
from dataclasses import replace
from datetime import timedelta

import pytest

from agent_control_plane.authority_reservation import (
    SQLiteAuthorityReservationStore,
)
from agent_control_plane.execution_journal import (
    ExecutionAttemptState,
    SQLiteExecutionJournal,
)
from agent_control_plane.execution_lifecycle import (
    KubernetesDeploymentExecutionCoordinator,
)
from agent_control_plane.independent_verifier import (
    IndependentExecutionProof,
    verify_execution_proof_mapping,
)
from agent_control_plane.kubernetes_deployment_transition import (
    KubernetesDeploymentObserver,
    KubernetesDeploymentScaleProvider,
)
from agent_control_plane.provable_execution import (
    PlanAuthorizationBinding,
    PlanExecutionFence,
    TransitionPlan,
    VerificationCondition,
    VerificationReport,
    VerificationStatus,
)
from agent_control_plane.state_transition_protocol import (
    ProtocolViolation,
    canonical_digest,
)
from context_testkit import NOW, build_context_bound_execution_v3


class StepClock:
    def __init__(self) -> None:
        self.index = 0

    def __call__(self):
        self.index += 1
        return NOW + timedelta(seconds=4 + self.index)


def _proof_fixture(tmp_path):
    (
        fixture,
        store,
        _,
        _,
        _,
        context,
    ) = build_context_bound_execution_v3(tmp_path)
    journal = SQLiteExecutionJournal(
        tmp_path / "independent-proof-execution.db"
    )
    coordinator = KubernetesDeploymentExecutionCoordinator(
        provider=KubernetesDeploymentScaleProvider(fixture["api"]),
        journal=journal,
        reservation_store=SQLiteAuthorityReservationStore(store.path),
        clock=StepClock(),
    )

    execution = coordinator.execute(
        transition=fixture["transition"],
        evidence=fixture["evidence"],
        outcome_contract=fixture["outcome"],
        action=fixture["action"],
        authorization=fixture["authorization"],
        fence=fixture["fence"],
        active_lease=fixture["lease"],
        caller=fixture["holder"],
        context_binding=context,
    )
    attempt = execution.attempt
    assert attempt.state is ExecutionAttemptState.COMMITTED
    assert attempt.transition_plan is not None

    plan = TransitionPlan.from_mapping(attempt.transition_plan)
    authorization = PlanAuthorizationBinding.derive(
        plan=plan,
        transition=fixture["transition"],
        action=fixture["action"],
        authorization=fixture["authorization"],
    )
    fence = PlanExecutionFence.bind(
        plan=plan,
        authorization=authorization,
        lease=fixture["lease"],
    )
    assert attempt.plan_authorization_hash == authorization.binding_hash
    assert attempt.plan_fence_hash == fence.fence_hash

    fixture["api"].deployment["status"]["replicas"] = 30
    fixture["api"].deployment["status"]["readyReplicas"] = 30
    fixture["api"].deployment["status"]["availableReplicas"] = 30

    observation = KubernetesDeploymentObserver(
        fixture["api"],
        collector_name="independent-proof-observer",
        collector_version="v1",
    ).observe(
        resource=fixture["resource"],
        principal=fixture["collector"],
        observed_at=NOW + timedelta(seconds=30),
    )
    after = observation.snapshot
    verification = VerificationReport.seal(
        plan=plan,
        after_observation=after,
        conditions=(
            VerificationCondition(
                condition_type="DesiredStateReached",
                status=VerificationStatus.TRUE,
                reason="DeploymentReady",
                message="30 replicas are ready",
                evidence_digest=after.observation_hash,
            ),
            VerificationCondition(
                condition_type="OperationOwnershipProven",
                status=VerificationStatus.TRUE,
                reason="ProviderOwnershipMarkersVerified",
                message=(
                    "provider observation was independently checked "
                    "against the committed operation"
                ),
                evidence_digest=after.observation_hash,
            ),
        ),
        verifier=fixture["collector"],
        verified_at=NOW + timedelta(seconds=31),
    )

    proof = IndependentExecutionProof.seal(
        proof_id="proof-k8s-scale-20-30",
        plan=plan,
        authorization=authorization,
        fence=fence,
        attempt=attempt,
        after_observation=after,
        verification=verification,
    )
    return proof


def test_independent_verifier_survives_json_process_boundary(tmp_path):
    proof = _proof_fixture(tmp_path)

    serialized = json.dumps(
        proof.as_mapping(),
        sort_keys=True,
        separators=(",", ":"),
    )
    decoded = json.loads(serialized)

    verified = verify_execution_proof_mapping(
        decoded,
        expected_statement_hash=proof.statement_hash,
    )

    assert verified == proof
    assert verified.predicate_type == (
        "agent-control-plane/state-transition-execution/v1"
    )
    assert verified.subject["name"].startswith("execution/")


def test_independent_proof_contains_no_agent_session_or_hidden_reasoning(
    tmp_path,
):
    proof = _proof_fixture(tmp_path)
    encoded = json.dumps(proof.as_mapping(), sort_keys=True).lower()

    assert "agent_session" not in encoded
    assert "chain_of_thought" not in encoded
    assert "hidden_reasoning" not in encoded
    assert "workflow_history" not in encoded


def test_independent_verifier_rejects_nested_terminal_result_tamper(
    tmp_path,
):
    proof = _proof_fixture(tmp_path)
    tampered = copy.deepcopy(proof.as_mapping())
    attempt = tampered["predicate"]["execution_attempt"]
    attempt["result"]["plan_hash"] = "sha256:" + "f" * 64

    # Recompute only the outer statement hash to prove nested artifact
    # verification is not replaced by one top-level checksum.
    provisional = IndependentExecutionProof(
        proof_id=tampered["proof_id"],
        subject=tampered["subject"],
        predicate_type=tampered["predicate_type"],
        predicate=tampered["predicate"],
        statement_hash="",
        statement_version=tampered["statement_version"],
    )
    tampered["statement_hash"] = canonical_digest(
        provisional,
        exclude=("statement_hash",),
    )

    with pytest.raises(
        ProtocolViolation,
        match="execution result digest mismatch",
    ):
        verify_execution_proof_mapping(tampered)


def test_out_of_band_statement_digest_detects_fully_resealed_envelope(
    tmp_path,
):
    proof = _proof_fixture(tmp_path)
    trusted_digest = proof.statement_hash

    # Changing envelope identity is structurally valid once re-sealed, but an
    # out-of-band signed/trusted digest must still reject the replacement.
    forged = replace(
        proof,
        proof_id="forged-proof-id",
        statement_hash="",
    )
    forged = replace(
        forged,
        statement_hash=canonical_digest(
            forged,
            exclude=("statement_hash",),
        ),
    )
    forged.verify()
    assert forged.statement_hash != trusted_digest

    with pytest.raises(
        ProtocolViolation,
        match="trusted digest mismatch",
    ):
        verify_execution_proof_mapping(
            forged.as_mapping(),
            expected_statement_hash=trusted_digest,
        )


def test_independent_verifier_rejects_observation_swap(tmp_path):
    proof = _proof_fixture(tmp_path)
    tampered = copy.deepcopy(proof.as_mapping())
    observation = tampered["predicate"]["after_observation"]
    observation["state"]["readyReplicas"] = 29

    provisional = IndependentExecutionProof(
        proof_id=tampered["proof_id"],
        subject=tampered["subject"],
        predicate_type=tampered["predicate_type"],
        predicate=tampered["predicate"],
        statement_hash="",
        statement_version=tampered["statement_version"],
    )
    tampered["statement_hash"] = canonical_digest(
        provisional,
        exclude=("statement_hash",),
    )

    with pytest.raises(
        ProtocolViolation,
        match="observation digest mismatch",
    ):
        verify_execution_proof_mapping(tampered)
