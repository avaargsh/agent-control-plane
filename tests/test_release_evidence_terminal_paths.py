from agent_control_plane.apply_reconciler import ApplyReconciler
from agent_control_plane.release_evidence import verify_release_evidence
from test_apply_reconciler import (
    build_plan,
    executor_registry,
    provider_registry,
)


def test_policy_blocked_release_is_sealed_for_replay():
    plan = build_plan()
    plan = plan.__class__(
        **{
            **plan.__dict__,
            "policy_refs": ("policy://required",),
        }
    )
    result = ApplyReconciler(
        providers=provider_registry(),
        executors=executor_registry(),
    ).reconcile(plan)

    assert result.phase == "Blocked"
    assert verify_release_evidence(result.evidence)
