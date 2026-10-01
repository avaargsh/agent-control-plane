from dataclasses import replace
from datetime import datetime, timezone

import pytest

from agent_control_plane.execution_verification import (
    ConditionEvaluation,
    OutcomeVerificationResult,
    VerificationStatus,
)
from agent_control_plane.state_transition_protocol import ProtocolViolation


NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def _condition(*, passed: bool | None) -> ConditionEvaluation:
    return ConditionEvaluation(
        source="kubernetes",
        expression="deployment.status.readyReplicas",
        comparator="eq",
        expected=30,
        observed=30 if passed is True else 20,
        passed=passed,
    )


def test_successful_verification_is_hash_sealed():
    result = OutcomeVerificationResult.seal(
        status=VerificationStatus.SUCCEEDED,
        transition_hash="sha256:" + "a" * 64,
        outcome_contract_hash="sha256:" + "b" * 64,
        observation_hash="sha256:" + "c" * 64,
        checked_at=NOW,
        desired=(_condition(passed=True),),
        safety=(),
        reasons=("all desired and safety conditions passed",),
    )

    result.verify()
    assert result.verification_hash


def test_success_status_rejects_failed_desired_condition():
    with pytest.raises(
        ProtocolViolation,
        match="requires all desired conditions",
    ):
        OutcomeVerificationResult.seal(
            status=VerificationStatus.SUCCEEDED,
            transition_hash="sha256:" + "a" * 64,
            outcome_contract_hash="sha256:" + "b" * 64,
            observation_hash="sha256:" + "c" * 64,
            checked_at=NOW,
            desired=(_condition(passed=False),),
            safety=(),
            reasons=("caller tried to overstate success",),
        )


def test_success_status_rejects_unknown_safety_condition():
    safety = ConditionEvaluation(
        source="prometheus",
        expression="error_rate",
        comparator="lt",
        expected=0.01,
        observed=None,
        passed=None,
    )

    with pytest.raises(
        ProtocolViolation,
        match="requires all safety conditions",
    ):
        OutcomeVerificationResult.seal(
            status=VerificationStatus.SUCCEEDED,
            transition_hash="sha256:" + "a" * 64,
            outcome_contract_hash="sha256:" + "b" * 64,
            observation_hash="sha256:" + "c" * 64,
            checked_at=NOW,
            desired=(_condition(passed=True),),
            safety=(safety,),
            reasons=("caller tried to overstate success",),
        )


def test_verification_detects_condition_tampering():
    result = OutcomeVerificationResult.seal(
        status=VerificationStatus.SUCCEEDED,
        transition_hash="sha256:" + "a" * 64,
        outcome_contract_hash="sha256:" + "b" * 64,
        observation_hash="sha256:" + "c" * 64,
        checked_at=NOW,
        desired=(_condition(passed=True),),
        safety=(),
        reasons=("all desired and safety conditions passed",),
    )
    tampered = replace(
        result,
        desired=(_condition(passed=False),),
    )

    with pytest.raises(
        ProtocolViolation,
        match="requires all desired conditions",
    ):
        tampered.verify()


def test_verification_detects_hash_tampering():
    result = OutcomeVerificationResult.seal(
        status=VerificationStatus.DEGRADED,
        transition_hash="sha256:" + "a" * 64,
        outcome_contract_hash="sha256:" + "b" * 64,
        observation_hash="sha256:" + "c" * 64,
        checked_at=NOW,
        desired=(_condition(passed=False),),
        safety=(),
        reasons=("desired state is not yet satisfied",),
    )
    tampered = replace(
        result,
        reasons=("different explanation",),
    )

    with pytest.raises(
        ProtocolViolation,
        match="outcome verification digest mismatch",
    ):
        tampered.verify()
