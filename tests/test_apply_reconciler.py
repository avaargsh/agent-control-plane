import hashlib
import hmac
import json

from agent_control_plane.apply_reconciler import ApplyReconciler
from agent_control_plane.decision_adapter import DecisionGatewayAdapter
from agent_control_plane.factory_attestation import (
    HMACFactoryAttestationVerifier,
)
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
from agent_control_plane.runtime_clients import RuntimeMutationOwnershipUncertain
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
            "coverage": 0.5,
            "risk": 0.02,
            "false_automation_rate": 0.01,
            "fallback_rate": 0.5,
            "risk_budget": 0.05,
        },
        "fallback_evaluation": (
            {
                "measured": True,
                "adapter": "transformers-structured-output",
                "threshold": 0.8,
                "eligible_case_count": 2,
                "fallback_case_count": 1,
                "fallback_rate": 0.5,
                "accuracy": 1.0,
                "p50_latency_ms": 20.0,
                "p95_latency_ms": 20.0,
                "mean_tokens_processed": 48.0,
                "cases": [
                    {
                        "case_id": "case-b",
                        "fast_confidence": 0.55,
                        "fast_predicted": "prometheus.query",
                        "fallback_predicted": "logs.search",
                        "gold": "logs.search",
                        "correct": True,
                        "latency_ms": 20.0,
                        "tokens_processed": 48,
                    }
                ],
            }
            if measured
            else {"measured": False}
        ),
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
    gate_status = {
        "ACCEPT": "PASS",
        "HOLD": "WARN",
        "REJECT": "FAIL",
    }[disposition]
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
                "status": gate_status,
                "reasons": (
                    []
                    if gate_status == "PASS"
                    else [f"fixture disposition: {disposition}"]
                ),
            },
            {
                "gateId": "runtime",
                "status": gate_status,
                "reasons": (
                    []
                    if gate_status == "PASS"
                    else [f"fixture disposition: {disposition}"]
                ),
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



FACTORY_GATE = {
    "spec": {
        "conditions": [
            {
                "metric": "factory_acceptance_trusted",
                "op": "eq",
                "value": 1.0,
            },
            {
                "metric": "factory_accepted",
                "op": "eq",
                "value": 1.0,
            },
        ],
        "onFailure": "block",
    }
}


def factory_attestation(
    artifact,
    *,
    key_id="commissioning-lab",
    secret=b"lab-secret",
):
    unsigned = {
        "apiVersion": "aifactory.engineering/v1alpha1",
        "kind": "AcceptanceAttestation",
        "artifactDigest": artifact["digest"],
        "keyId": key_id,
        "algorithm": "HMAC-SHA256",
    }
    message = json.dumps(
        unsigned,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return {
        **unsigned,
        "signature": hmac.new(
            secret,
            message,
            hashlib.sha256,
        ).hexdigest(),
    }


def test_trusted_factory_acceptance_can_feed_eval_gate() -> None:
    artifact = factory_acceptance_artifact()
    attestation = factory_attestation(artifact)

    result = ApplyReconciler(
        providers=provider_registry(),
        executors=executor_registry(),
        factory_attestation_verifier=HMACFactoryAttestationVerifier({
            "commissioning-lab": b"lab-secret",
        }),
    ).reconcile(
        build_plan(),
        eval_gates=[FACTORY_GATE],
        factory_acceptance_artifact=artifact,
        factory_acceptance_attestation=attestation,
    )

    assert result.phase == "promoted"
    assert result.eval_results[0].passed is True
    bound = result.evidence["factory_acceptance"]
    assert bound["trusted"] is True
    assert bound["gate_eligible"] is True
    assert bound["attestation_key_id"] == "commissioning-lab"
    assert "signature" in bound["attestation"]


def test_trusted_factory_reject_blocks_accept_gate() -> None:
    artifact = factory_acceptance_artifact(
        disposition="REJECT",
    )
    attestation = factory_attestation(artifact)

    result = ApplyReconciler(
        providers=provider_registry(),
        executors=executor_registry(),
        factory_attestation_verifier=HMACFactoryAttestationVerifier({
            "commissioning-lab": b"lab-secret",
        }),
    ).reconcile(
        build_plan(),
        eval_gates=[FACTORY_GATE],
        factory_acceptance_artifact=artifact,
        factory_acceptance_attestation=attestation,
    )

    assert result.phase == "blocked"
    assert result.eval_results[0].passed is False
    assert result.eval_results[0].violations[0].metric == "factory_accepted"
    assert result.evidence["factory_acceptance"]["trusted"] is True


def test_factory_attestation_from_unknown_key_blocks_before_provider_mutation() -> None:
    artifact = factory_acceptance_artifact()
    attestation = factory_attestation(
        artifact,
        key_id="unknown-lab",
        secret=b"unknown-secret",
    )

    result = ApplyReconciler(
        providers=provider_registry(),
        executors=executor_registry(),
        factory_attestation_verifier=HMACFactoryAttestationVerifier({
            "commissioning-lab": b"lab-secret",
        }),
    ).reconcile(
        build_plan(),
        factory_acceptance_artifact=artifact,
        factory_acceptance_attestation=attestation,
    )

    assert result.phase == "blocked"
    assert result.receipts == ()
    assert result.error == "FACTORY_ATTESTATION_INVALID:UNTRUSTED_KEY_ID"


def test_factory_attestation_tamper_blocks_before_provider_mutation() -> None:
    artifact = factory_acceptance_artifact()
    attestation = factory_attestation(artifact)
    attestation["algorithm"] = "HMAC-SHA512"

    result = ApplyReconciler(
        providers=provider_registry(),
        executors=executor_registry(),
        factory_attestation_verifier=HMACFactoryAttestationVerifier({
            "commissioning-lab": b"lab-secret",
        }),
    ).reconcile(
        build_plan(),
        factory_acceptance_artifact=artifact,
        factory_acceptance_attestation=attestation,
    )

    assert result.phase == "blocked"
    assert result.receipts == ()
    assert result.error == "FACTORY_ATTESTATION_INVALID:ALGORITHM_UNSUPPORTED"


def test_factory_attestation_requires_verifier() -> None:
    artifact = factory_acceptance_artifact()
    attestation = factory_attestation(artifact)

    result = ApplyReconciler(
        providers=provider_registry(),
        executors=executor_registry(),
    ).reconcile(
        build_plan(),
        factory_acceptance_artifact=artifact,
        factory_acceptance_attestation=attestation,
    )

    assert result.phase == "blocked"
    assert result.receipts == ()
    assert result.error == "FACTORY_ATTESTATION_VERIFIER_REQUIRED"



def _reseal_factory_artifact(artifact):
    payload = {
        key: value
        for key, value in artifact.items()
        if key != "digest"
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    artifact["digest"] = (
        "sha256:" + hashlib.sha256(canonical).hexdigest()
    )
    return artifact


def test_factory_artifact_rejects_empty_issued_at_after_reseal() -> None:
    artifact = factory_acceptance_artifact()
    artifact["issuedAt"] = ""
    _reseal_factory_artifact(artifact)

    result = ApplyReconciler(
        providers=provider_registry(),
        executors=executor_registry(),
    ).reconcile(
        build_plan(),
        factory_acceptance_artifact=artifact,
    )

    assert result.phase == "blocked"
    assert result.receipts == ()
    assert result.error == (
        "FACTORY_ACCEPTANCE_INVALID:ISSUED_AT_REQUIRED"
    )


def test_factory_artifact_rejects_invalid_gate_reasons_after_reseal() -> None:
    artifact = factory_acceptance_artifact()
    artifact["gates"][0]["reasons"] = "not-a-list"
    _reseal_factory_artifact(artifact)

    result = ApplyReconciler(
        providers=provider_registry(),
        executors=executor_registry(),
    ).reconcile(
        build_plan(),
        factory_acceptance_artifact=artifact,
    )

    assert result.phase == "blocked"
    assert result.receipts == ()
    assert result.error == (
        "FACTORY_ACCEPTANCE_INVALID:GATE_REASONS_INVALID"
    )


def test_factory_artifact_rejects_invalid_top_reasons_after_reseal() -> None:
    artifact = factory_acceptance_artifact()
    artifact["reasons"] = [123]
    _reseal_factory_artifact(artifact)

    result = ApplyReconciler(
        providers=provider_registry(),
        executors=executor_registry(),
    ).reconcile(
        build_plan(),
        factory_acceptance_artifact=artifact,
    )

    assert result.phase == "blocked"
    assert result.receipts == ()
    assert result.error == (
        "FACTORY_ACCEPTANCE_INVALID:REASONS_INVALID"
    )


def test_factory_artifact_rejects_empty_evidence_ref_after_reseal() -> None:
    artifact = factory_acceptance_artifact()
    artifact["evidenceRefs"]["compute"] = ""
    _reseal_factory_artifact(artifact)

    result = ApplyReconciler(
        providers=provider_registry(),
        executors=executor_registry(),
    ).reconcile(
        build_plan(),
        factory_acceptance_artifact=artifact,
    )

    assert result.phase == "blocked"
    assert result.receipts == ()
    assert result.error == (
        "FACTORY_ACCEPTANCE_INVALID:EVIDENCE_REFS_INVALID"
    )



def test_apply_failure_preserves_original_error_when_rollback_is_incomplete() -> None:
    class RollbackFailToolExecutor(InMemoryExecutor):
        def rollback(self, **kwargs):
            raise RuntimeError("tool rollback unavailable")

    class ApplyFailWorkflowExecutor(InMemoryExecutor):
        def apply(self, **kwargs):
            raise RuntimeError("temporal unavailable")

    executors = ExecutorRegistry()
    executors.register(
        InMemoryExecutor("sandbox", "k8s-agent-sandbox")
    )
    executors.register(
        RollbackFailToolExecutor("tool", "mcp")
    )
    executors.register(
        InMemoryExecutor("harness", "codex")
    )
    executors.register(
        ApplyFailWorkflowExecutor("workflow", "temporal")
    )
    executors.register(
        InMemoryExecutor("decision", "decision-gateway")
    )

    result = ApplyReconciler(
        providers=provider_registry(),
        executors=executors,
    ).reconcile(build_plan())

    assert result.phase == "blocked"
    assert result.error == "temporal unavailable"
    assert [
        item.binding_name
        for item in result.rollback_receipts
    ] == ["harness", "tools", "sandbox"]
    assert [
        item.rolled_back
        for item in result.rollback_receipts
    ] == [True, False, True]
    assert result.rollback_receipts[1].evidence["reason"] == "rollback-error"
    assert result.rollback_receipts[1].evidence["error"] == "tool rollback unavailable"
    assert result.evidence["apply_error"] == "temporal unavailable"
    assert result.evidence["rollback_errors"] == [
        {
            "binding": "tools",
            "provider_type": "tool",
            "provider_name": "mcp",
            "resource_ref": "local://tool/mcp/sre-v1/tools",
            "error": "tool rollback unavailable",
        }
    ]


def test_eval_rollback_failure_is_blocked_but_keeps_all_compensation_evidence() -> None:
    class RollbackFailWorkflowExecutor(InMemoryExecutor):
        def rollback(self, **kwargs):
            raise RuntimeError("temporal rollback unavailable")

    executors = ExecutorRegistry()
    executors.register(
        InMemoryExecutor("sandbox", "k8s-agent-sandbox")
    )
    executors.register(InMemoryExecutor("tool", "mcp"))
    executors.register(InMemoryExecutor("harness", "codex"))
    executors.register(
        RollbackFailWorkflowExecutor("workflow", "temporal")
    )
    executors.register(
        InMemoryExecutor("decision", "decision-gateway")
    )

    result = ApplyReconciler(
        providers=provider_registry(),
        executors=executors,
    ).reconcile(
        build_plan(),
        eval_gates=[ROLLBACK_GATE],
        metrics={"false_automation_rate": 0.20},
    )

    assert result.phase == "blocked"
    assert result.error == "ROLLBACK_INCOMPLETE"
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
    assert result.rollback_receipts[1].rolled_back is False
    assert all(
        item.rolled_back
        for index, item in enumerate(result.rollback_receipts)
        if index != 1
    )
    assert result.evidence["rollback_errors"][0]["binding"] == "workflow"
    assert (
        result.evidence["rollback_errors"][0]["error"]
        == "temporal rollback unavailable"
    )

def test_ambiguous_apply_ownership_is_recorded_without_claiming_receipt() -> None:
    class AmbiguousWorkflowExecutor(InMemoryExecutor):
        def apply(self, **kwargs):
            raise RuntimeMutationOwnershipUncertain(
                "temporal ownership ambiguous after lost ACK",
                resource_ref="temporal://workflow/sre-v1",
                operation_id="attempt-a",
                observed_operation_id="attempt-b",
            )

    executors = ExecutorRegistry()
    executors.register(
        InMemoryExecutor("sandbox", "k8s-agent-sandbox")
    )
    executors.register(InMemoryExecutor("tool", "mcp"))
    executors.register(InMemoryExecutor("harness", "codex"))
    executors.register(
        AmbiguousWorkflowExecutor("workflow", "temporal")
    )
    executors.register(
        InMemoryExecutor("decision", "decision-gateway")
    )

    result = ApplyReconciler(
        providers=provider_registry(),
        executors=executors,
    ).reconcile(build_plan())

    assert result.phase == "rolled-back"
    assert [item.binding_name for item in result.receipts] == [
        "sandbox",
        "tools",
        "harness",
    ]
    assert result.evidence["ambiguous_apply"] == {
        "binding": "workflow",
        "resource_ref": "temporal://workflow/sre-v1",
        "operation_id": "attempt-a",
        "observed_operation_id": "attempt-b",
        "ownership_proven": False,
    }
    assert result.error == "temporal ownership ambiguous after lost ACK"

