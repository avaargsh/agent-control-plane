from agent_control_plane.apply_reconciler import ApplyReconciler
from agent_control_plane.golden_run import RecoveryGoldenRun, run_recovery_golden_slice

from tests.test_apply_reconciler import (
    PASS_GATE,
    build_plan,
    executor_registry,
    provider_registry,
)


def reconciler(*, failing_workflow: bool = False) -> ApplyReconciler:
    return ApplyReconciler(
        providers=provider_registry(),
        executors=executor_registry(failing_workflow=failing_workflow),
    )


def test_acceptance_happy_path_promotes_with_release_evidence() -> None:
    result = reconciler().reconcile(
        build_plan(),
        eval_gates=[PASS_GATE],
        metrics={"accuracy": 0.95, "ece": 0.03},
        observed_placement="cell-b",
    )

    assert result.phase == "promoted"
    assert result.error is None
    assert result.evidence["kind"] == "ReleaseEvidence"
    assert result.evidence["phase"] == "promoted"
    assert len(result.evidence["receipts"]) == 5
    assert result.evidence["rollback_receipts"] == []
    assert result.evidence["eval_results"][0]["passed"] is True


def test_acceptance_gate_failure_blocks_with_violation_evidence() -> None:
    result = reconciler().reconcile(
        build_plan(),
        eval_gates=[PASS_GATE],
        metrics={"accuracy": 0.70, "ece": 0.03},
    )

    assert result.phase == "blocked"
    assert result.evidence["phase"] == "blocked"
    violation = result.evidence["eval_results"][0]["violations"][0]
    assert violation["metric"] == "accuracy"
    assert violation["reason"] == "THRESHOLD_FAILED"
    assert result.evidence["rollback_receipts"] == []


def test_acceptance_apply_failure_rolls_back_only_completed_work() -> None:
    result = reconciler(failing_workflow=True).reconcile(build_plan())

    assert result.phase == "rolled_back"
    assert result.error == "temporal unavailable"
    assert [item["binding_name"] for item in result.evidence["receipts"]] == [
        "sandbox",
        "tools",
        "harness",
    ]
    assert [
        item["binding_name"] for item in result.evidence["rollback_receipts"]
    ] == ["harness", "tools", "sandbox"]
    assert result.evidence["apply_error"] == "temporal unavailable"


def test_acceptance_recovery_identity_loss_rolls_back_with_evidence() -> None:
    result = run_recovery_golden_slice(
        reconciler(),
        build_plan(),
        RecoveryGoldenRun(
            observed_placement="cell-a",
            recovery_evidence={
                "success": True,
                "identityPreserved": False,
                "sourceSandboxRef": "sandbox://cell-a/old",
                "targetSandboxRef": "sandbox://cell-b/new",
                "snapshotRef": "snapshot://old",
            },
        ),
    )

    assert result.phase == "rolled_back"
    assert result.evidence["recovery"]["identityPreserved"] is False
    assert result.evidence["placement"]["migrating"] is True
    violations = result.evidence["eval_results"][0]["violations"]
    assert any(
        item["metric"] == "recovery_identity_preserved"
        and item["reason"] == "THRESHOLD_FAILED"
        for item in violations
    )
    assert len(result.evidence["rollback_receipts"]) == 5
