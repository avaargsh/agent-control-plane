from agent_control_plane.apply_reconciler import ApplyReconciler
from agent_control_plane.golden_run import (
    RecoveryGoldenRun,
    run_recovery_golden_slice,
)

from test_apply_reconciler import (
    build_plan,
    executor_registry,
    provider_registry,
)


def test_recovery_golden_slice_promotes_only_with_preserved_identity() -> None:
    result = run_recovery_golden_slice(
        ApplyReconciler(
            providers=provider_registry(),
            executors=executor_registry(),
        ),
        build_plan(),
        RecoveryGoldenRun(
            observed_placement="cell-a",
            recovery_evidence={
                "success": True,
                "identityPreserved": True,
                "sourceSandboxRef": "sandbox://cell-a/old",
                "targetSandboxRef": "sandbox://cell-b/new",
                "snapshotRef": "snapshot://old",
            },
        ),
    )

    assert result.phase == "promoted"
    assert result.evidence["placement"]["migrating"] is True
    assert result.evidence["placement"]["target"] == "cell-b"
    assert result.evidence["recovery"]["identityPreserved"] is True


def test_recovery_golden_slice_rolls_back_on_identity_loss() -> None:
    result = run_recovery_golden_slice(
        ApplyReconciler(
            providers=provider_registry(),
            executors=executor_registry(),
        ),
        build_plan(),
        RecoveryGoldenRun(
            observed_placement="cell-a",
            recovery_evidence={
                "success": True,
                "identityPreserved": False,
                "sourceSandboxRef": "sandbox://cell-a/old",
                "targetSandboxRef": "sandbox://cell-b/new",
            },
        ),
    )

    assert result.phase == "rolled_back"
