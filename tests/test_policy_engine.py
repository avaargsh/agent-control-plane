from agent_control_plane.apply_reconciler import ApplyReconciler
from agent_control_plane.executors import ExecutorRegistry
from agent_control_plane.plan import ResolvedReleasePlan
from agent_control_plane.policy_engine import MappingPolicyEngine
from agent_control_plane.registry import ProviderRegistry


def plan(*policy_refs: str) -> ResolvedReleasePlan:
    return ResolvedReleasePlan(
        release_name="release-v1",
        bundle_name="bundle",
        version="v1",
        bindings={},
        policy_refs=tuple(policy_refs),
    )


def reconciler(policy_engine=None) -> ApplyReconciler:
    return ApplyReconciler(
        providers=ProviderRegistry(),
        executors=ExecutorRegistry(),
        policy_engine=policy_engine,
    )


def test_policy_refs_without_engine_fail_closed() -> None:
    result = reconciler().reconcile(
        plan("production-safe")
    )

    assert result.phase == "blocked"
    assert result.receipts == ()
    assert result.policy_decision is not None
    assert result.policy_decision.allowed is False
    assert result.policy_decision.reasons == (
        "POLICY_ENGINE_REQUIRED",
    )


def test_unknown_policy_fails_closed() -> None:
    result = reconciler(
        MappingPolicyEngine(
            decisions={}
        )
    ).reconcile(
        plan("unknown")
    )

    assert result.phase == "blocked"
    assert result.policy_decision is not None
    assert result.policy_decision.reasons == (
        "UNKNOWN_POLICY:unknown",
    )


def test_explicit_policy_denial_blocks() -> None:
    result = reconciler(
        MappingPolicyEngine(
            decisions={
                "production-safe": False,
            }
        )
    ).reconcile(
        plan("production-safe")
    )

    assert result.phase == "blocked"
    assert result.policy_decision is not None
    assert result.policy_decision.reasons == (
        "POLICY_DENIED:production-safe",
    )


def test_all_referenced_policies_allow_release() -> None:
    result = reconciler(
        MappingPolicyEngine(
            decisions={
                "read-only": True,
                "tenant-safe": True,
            }
        )
    ).reconcile(
        plan(
            "read-only",
            "tenant-safe",
        )
    )

    assert result.phase == "promoted"
    assert result.policy_decision is not None
    assert result.policy_decision.allowed is True
