import hashlib
import json

from agent_control_plane.apply_reconciler import ApplyReconciler
from agent_control_plane.decision_adapter import DecisionGatewayAdapter
from agent_control_plane.executors import (
    ExecutorRegistry,
    InMemoryExecutor,
)
from agent_control_plane.example_adapters import (
    CodexHarnessAdapter,
    KubernetesSandboxAdapter,
    TemporalWorkflowAdapter,
)
from agent_control_plane.plan import ResolvedReleasePlan
from agent_control_plane.registry import ProviderRegistry
from agent_control_plane.tool_adapter import MCPToolAdapter


def build_plan() -> ResolvedReleasePlan:
    return ResolvedReleasePlan(
        release_name="sre-v1",
        bundle_name="sre",
        version="v1",
        placement={"target": "cell-b", "migrationStrategy": "drain-rebind"},
        bindings={
            "sandbox": {
                "metadata": {"name": "sandbox"},
                "spec": {
                    "type": "sandbox",
                    "provider": "k8s-agent-sandbox",
                },
            },
            "tools": {
                "metadata": {"name": "tools"},
                "spec": {
                    "type": "tool",
                    "provider": "mcp",
                    "dependsOn": ["sandbox"],
                },
            },
            "harness": {
                "metadata": {"name": "harness"},
                "spec": {
                    "type": "harness",
                    "provider": "codex",
                    "dependsOn": ["tools"],
                },
            },
            "workflow": {
                "metadata": {"name": "workflow"},
                "spec": {
                    "type": "workflow",
                    "provider": "temporal",
                    "dependsOn": ["harness"],
                },
            },
            "decision": {
                "metadata": {"name": "decision"},
                "spec": {
                    "type": "decision",
                    "provider": "decision-gateway",
                    "dependsOn": ["workflow"],
                },
            },
        },
    )


def provider_registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(KubernetesSandboxAdapter())
    registry.register(MCPToolAdapter())
    registry.register(CodexHarnessAdapter())
    registry.register(TemporalWorkflowAdapter())
    registry.register(DecisionGatewayAdapter())
    return registry


def executor_registry(
    *,
    failing_workflow: bool = False,
) -> ExecutorRegistry:
    registry = ExecutorRegistry()

    registry.register(
        InMemoryExecutor("sandbox", "k8s-agent-sandbox")
    )
    registry.register(InMemoryExecutor("tool", "mcp"))
    registry.register(InMemoryExecutor("harness", "codex"))

    if failing_workflow:
        class FailingWorkflowExecutor(InMemoryExecutor):
            def apply(self, **kwargs):
                raise RuntimeError("temporal unavailable")

        registry.register(
            FailingWorkflowExecutor("workflow", "temporal")
        )
    else:
        registry.register(
            InMemoryExecutor("workflow", "temporal")
        )

    registry.register(
        InMemoryExecutor("decision", "decision-gateway")
    )
    return registry


PASS_GATE = {
    "spec": {
        "conditions": [
            {"metric": "accuracy", "op": "gte", "value": 0.9},
            {"metric": "ece", "op": "lte", "value": 0.05},
        ],
        "onFailure": "block",
    }
}

ROLLBACK_GATE = {
    "spec": {
        "conditions": [
            {
                "metric": "false_automation_rate",
                "op": "lte",
                "value": 0.01,
            },
        ],
        "onFailure": "rollback",
    }
}


def test_apply_promotes_in_dependency_order() -> None:
    result = ApplyReconciler(
        providers=provider_registry(),
        executors=executor_registry(),
    ).reconcile(
        build_plan(),
        eval_gates=[PASS_GATE],
        metrics={"accuracy": 0.95, "ece": 0.03},
    )

    assert result.phase == "promoted"
    assert [
        receipt.binding_name
        for receipt in result.receipts
    ] == [
        "sandbox",
        "tools",
        "harness",
        "workflow",
        "decision",
    ]
    assert result.rollback_receipts == ()
    assert result.eval_results[0].passed is True


def test_apply_is_idempotent_on_retry() -> None:
    executors = executor_registry()
    reconciler = ApplyReconciler(
        providers=provider_registry(),
        executors=executors,
    )

    first = reconciler.reconcile(
        build_plan(),
        eval_gates=[PASS_GATE],
        metrics={"accuracy": 0.95, "ece": 0.03},
    )
    second = reconciler.reconcile(
        build_plan(),
        eval_gates=[PASS_GATE],
        metrics={"accuracy": 0.95, "ece": 0.03},
    )

    assert all(item.changed for item in first.receipts)
    assert all(not item.changed for item in second.receipts)


def test_apply_blocks_failed_gate_without_compensation() -> None:
    result = ApplyReconciler(
        providers=provider_registry(),
        executors=executor_registry(),
    ).reconcile(
        build_plan(),
        eval_gates=[PASS_GATE],
        metrics={"accuracy": 0.70, "ece": 0.03},
    )

    assert result.phase == "blocked"
    assert result.rollback_receipts == ()
    assert result.eval_results[0].passed is False


def test_failed_rollback_gate_compensates_reverse_apply_order() -> None:
    result = ApplyReconciler(
        providers=provider_registry(),
        executors=executor_registry(),
    ).reconcile(
        build_plan(),
        eval_gates=[ROLLBACK_GATE],
        metrics={"false_automation_rate": 0.20},
    )

    assert result.phase == "rolled_back"
    assert [
        item.binding_name
        for item in result.rollback_receipts
    ] == [
        "decision",
        "workflow",
        "harness",
        "tools",
        "sandbox",
    ]
    assert all(item.rolled_back for item in result.rollback_receipts)


def test_partial_apply_failure_compensates_completed_bindings() -> None:
    result = ApplyReconciler(
        providers=provider_registry(),
        executors=executor_registry(
            failing_workflow=True,
        ),
    ).reconcile(
        build_plan(),
    )

    assert result.phase == "rolled_back"
    assert result.error == "temporal unavailable"
    assert [
        item.binding_name
        for item in result.receipts
    ] == [
        "sandbox",
        "tools",
        "harness",
    ]
    assert [
        item.binding_name
        for item in result.rollback_receipts
    ] == [
        "harness",
        "tools",
        "sandbox",
    ]


def test_missing_metric_fails_closed() -> None:
    result = ApplyReconciler(
        providers=provider_registry(),
        executors=executor_registry(),
    ).reconcile(
        build_plan(),
        eval_gates=[PASS_GATE],
        metrics={"accuracy": 0.95},
    )

    assert result.phase == "blocked"


def test_apply_records_placement_migration_evidence() -> None:
    result = ApplyReconciler(
        providers=provider_registry(),
        executors=executor_registry(),
    ).reconcile(
        build_plan(),
        observed_placement="cell-a",
    )

    assert result.phase == "promoted"
    assert result.evidence["placement"] == {
        "source": "cell-a",
        "target": "cell-b",
        "migrating": True,
        "strategy": "drain-rebind",
    }


RECOVERY_GATE = {
    "spec": {
        "conditions": [
            {
                "metric": "recovery_success",
                "op": "eq",
                "value": 1.0,
            },
            {
                "metric": "recovery_identity_preserved",
                "op": "eq",
                "value": 1.0,
            },
        ],
        "onFailure": "rollback",
    }
}


def test_recovery_evidence_feeds_eval_gate() -> None:
    result = ApplyReconciler(
        providers=provider_registry(),
        executors=executor_registry(),
    ).reconcile(
        build_plan(),
        eval_gates=[RECOVERY_GATE],
        recovery_evidence={
            "success": True,
            "identityPreserved": True,
            "sourceSandboxRef": "sandbox://cell-a/old",
            "targetSandboxRef": "sandbox://cell-b/new",
            "snapshotRef": "snapshot://old",
        },
    )

    assert result.phase == "promoted"
    assert result.evidence["recovery"]["identityPreserved"] is True


def test_failed_recovery_rolls_back_release() -> None:
    result = ApplyReconciler(
        providers=provider_registry(),
        executors=executor_registry(),
    ).reconcile(
        build_plan(),
        eval_gates=[RECOVERY_GATE],
        recovery_evidence={
            "success": False,
            "identityPreserved": True,
        },
    )

    assert result.phase == "rolled_back"
    assert result.eval_results[0].passed is False


def test_provider_feature_mismatch_blocks_before_mutation() -> None:
    plan = build_plan()
    plan.bindings["workflow"]["spec"]["requires"] = {
        "features": ["durable-execution"]
    }

    class CountingExecutor(InMemoryExecutor):
        calls = 0

        def apply(self, **kwargs):
            type(self).calls += 1
            return super().apply(**kwargs)

    executors = ExecutorRegistry()
    for provider_type, provider_name in [
        ("sandbox", "k8s-agent-sandbox"),
        ("tool", "mcp"),
        ("harness", "codex"),
        ("workflow", "temporal"),
        ("decision", "decision-gateway"),
    ]:
        executors.register(CountingExecutor(provider_type, provider_name))

    result = ApplyReconciler(
        providers=provider_registry(),
        executors=executors,
    ).reconcile(plan)

    assert result.phase == "blocked"
    assert result.receipts == ()
    assert CountingExecutor.calls == 0
    assert "workflow: durable-execution" in result.error


def test_bindings_without_provider_feature_requirements_remain_compatible() -> None:
    plan = build_plan()
    result = ApplyReconciler(
        providers=provider_registry(),
        executors=executor_registry(),
    ).reconcile(plan)

    assert result.phase == "promoted"



DECISION_ARTIFACT_GATE = {
    "spec": {
        "conditions": [
            {"metric": "accuracy", "op": "gte", "value": 0.9},
            {
                "metric": "false_automation_rate",
                "op": "lte",
                "value": 0.01,
            },
            {"metric": "fallback_measured", "op": "eq", "value": 1.0},
        ],
        "onFailure": "block",
    }
}


def decision_eval_artifact(*, measured: bool = True):
    payload = {
        "schema_version": "decision-eval/v1",
        "decision_type": "mcp_tool_router",
        "adapter": "candidate-logits",
        "model_ref": "qwen/test",
        "dataset": {
            "sha256": "sha256:" + "a" * 64,
            "case_count": 2,
            "case_ids": ["case-a", "case-b"],
        },
        "calibration_sha256": "sha256:" + "b" * 64,
        "metrics": {
            "accuracy": 0.95,
            "macro_f1": 0.94,
            "nll": 0.2,
            "brier": 0.08,
            "ece": 0.03,
            "mean_latency_ms": 12.0,
            "p50_latency_ms": 10.0,
            "p95_latency_ms": 18.0,
            "mean_tokens_processed_per_decision": 32.0,
        },
        "operating_point": {
            "threshold": 0.8,
            "coverage": 0.75,
            "risk": 0.02,
            "false_automation_rate": 0.01,
            "fallback_rate": 0.25,
            "risk_budget": 0.05,
        },
        "fallback_evaluation": {
            "measured": measured,
        },
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    digest = "sha256:" + hashlib.sha256(canonical).hexdigest()
    return {
        **payload,
        "artifact_id": "decision-eval:" + digest,
        "content_digest": digest,
    }


def test_verified_decision_eval_artifact_feeds_release_gate() -> None:
    result = ApplyReconciler(
        providers=provider_registry(),
        executors=executor_registry(),
    ).reconcile(
        build_plan(),
        eval_gates=[DECISION_ARTIFACT_GATE],
        decision_eval_artifact=decision_eval_artifact(
            measured=True
        ),
    )

    assert result.phase == "promoted"
    assert result.eval_results[0].passed is True
    assert (
        result.evidence["decision_eval"]["schema_version"]
        == "decision-eval/v1"
    )


def test_unmeasured_system2_fallback_blocks_gate() -> None:
    result = ApplyReconciler(
        providers=provider_registry(),
        executors=executor_registry(),
    ).reconcile(
        build_plan(),
        eval_gates=[DECISION_ARTIFACT_GATE],
        decision_eval_artifact=decision_eval_artifact(
            measured=False
        ),
    )

    assert result.phase == "blocked"
    assert result.eval_results[0].passed is False
    assert (
        result.eval_results[0].violations[0].metric
        == "fallback_measured"
    )


def test_tampered_decision_eval_blocks_before_provider_mutation() -> None:
    artifact = decision_eval_artifact()
    artifact["metrics"]["accuracy"] = 0.10

    result = ApplyReconciler(
        providers=provider_registry(),
        executors=executor_registry(),
    ).reconcile(
        build_plan(),
        eval_gates=[DECISION_ARTIFACT_GATE],
        decision_eval_artifact=artifact,
    )

    assert result.phase == "blocked"
    assert result.receipts == ()
    assert result.error == (
        "DECISION_EVAL_INVALID:CONTENT_DIGEST_MISMATCH"
    )



def factory_acceptance_artifact(
    *,
    disposition: str = "ACCEPT",
):
    accepted = disposition == "ACCEPT"
    payload = {
        "apiVersion": "aifactory.engineering/v1alpha1",
        "kind": "AcceptanceArtifact",
        "caseId": "controlled-lab-golden",
        "issuedAt": "2026-09-30T10:00:00+00:00",
        "disposition": disposition,
        "accepted": accepted,
        "gates": [
            {
                "gateId": "compute",
                "status": "PASS",
                "reasons": [],
            },
            {
                "gateId": "runtime",
                "status": "PASS",
                "reasons": [],
            },
        ],
        "reasons": [],
        "evidenceRefs": {
            "compute": "evidence://gpu/dcgm-001",
            "runtime": "evidence://runtime/slo-001",
        },
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return {
        **payload,
        "digest": "sha256:" + hashlib.sha256(canonical).hexdigest(),
    }


def test_factory_acceptance_is_bound_as_untrusted_evidence_only() -> None:
    artifact = factory_acceptance_artifact()

    result = ApplyReconciler(
        providers=provider_registry(),
        executors=executor_registry(),
    ).reconcile(
        build_plan(),
        factory_acceptance_artifact=artifact,
    )

    assert result.phase == "promoted"
    bound = result.evidence["factory_acceptance"]
    assert bound["artifact_digest"] == artifact["digest"]
    assert bound["integrity_verified"] is True
    assert bound["trusted"] is False
    assert bound["gate_eligible"] is False
    assert bound["artifact"]["accepted"] is True


def test_untrusted_factory_reject_does_not_become_authorization() -> None:
    artifact = factory_acceptance_artifact(
        disposition="REJECT",
    )

    result = ApplyReconciler(
        providers=provider_registry(),
        executors=executor_registry(),
    ).reconcile(
        build_plan(),
        factory_acceptance_artifact=artifact,
    )

    assert result.phase == "promoted"
    bound = result.evidence["factory_acceptance"]
    assert bound["artifact"]["disposition"] == "REJECT"
    assert bound["trusted"] is False
    assert bound["gate_eligible"] is False


def test_tampered_factory_acceptance_blocks_before_provider_mutation() -> None:
    artifact = factory_acceptance_artifact()
    artifact["accepted"] = False

    class CountingExecutor(InMemoryExecutor):
        calls = 0

        def apply(self, **kwargs):
            type(self).calls += 1
            return super().apply(**kwargs)

    executors = ExecutorRegistry()
    for provider_type, provider_name in [
        ("sandbox", "k8s-agent-sandbox"),
        ("tool", "mcp"),
        ("harness", "codex"),
        ("workflow", "temporal"),
        ("decision", "decision-gateway"),
    ]:
        executors.register(
            CountingExecutor(provider_type, provider_name)
        )

    result = ApplyReconciler(
        providers=provider_registry(),
        executors=executors,
    ).reconcile(
        build_plan(),
        factory_acceptance_artifact=artifact,
    )

    assert result.phase == "blocked"
    assert result.receipts == ()
    assert CountingExecutor.calls == 0
    assert result.error == (
        "FACTORY_ACCEPTANCE_INVALID:DIGEST_MISMATCH"
    )
