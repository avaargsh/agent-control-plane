from __future__ import annotations

import json

from agent_control_plane.apply_reconciler import ApplyReconciler
from agent_control_plane.decision_adapter import DecisionGatewayAdapter
from agent_control_plane.example_adapters import (
    CodexHarnessAdapter,
    KubernetesSandboxAdapter,
    TemporalWorkflowAdapter,
)
from agent_control_plane.executors import ExecutorRegistry, InMemoryExecutor
from agent_control_plane.frozen_evidence import FrozenEvidence
from agent_control_plane.plan import ResolvedReleasePlan
from agent_control_plane.registry import ProviderRegistry
from agent_control_plane.release_evidence import verify_release_evidence
from agent_control_plane.tool_adapter import MCPToolAdapter


def plan() -> ResolvedReleasePlan:
    chain = [
        ("sandbox", "sandbox", "k8s-agent-sandbox", []),
        ("tools", "tool", "mcp", ["sandbox"]),
        ("harness", "harness", "codex", ["tools"]),
        ("workflow", "workflow", "temporal", ["harness"]),
        ("decision", "decision", "decision-gateway", ["workflow"]),
    ]
    return ResolvedReleasePlan(
        release_name="gpu-xid-remediation-v1",
        bundle_name="sre-gpu-xid",
        version="v1",
        placement={"target": "gpu-cell-a"},
        bindings={
            name: {
                "metadata": {"name": name},
                "spec": {
                    "type": provider_type,
                    "provider": provider,
                    **({"dependsOn": deps} if deps else {}),
                },
            }
            for name, provider_type, provider, deps in chain
        },
    )


def registries() -> tuple[ProviderRegistry, ExecutorRegistry]:
    providers = ProviderRegistry()
    providers.register(KubernetesSandboxAdapter())
    providers.register(MCPToolAdapter())
    providers.register(CodexHarnessAdapter())
    providers.register(TemporalWorkflowAdapter())
    providers.register(DecisionGatewayAdapter())

    executors = ExecutorRegistry()
    for provider_type, provider in [
        ("sandbox", "k8s-agent-sandbox"),
        ("tool", "mcp"),
        ("harness", "codex"),
        ("workflow", "temporal"),
        ("decision", "decision-gateway"),
    ]:
        executors.register(InMemoryExecutor(provider_type, provider))
    return providers, executors


def main() -> None:
    alert_evidence = {
        "incidentId": "inc-gpu-xid-001",
        "alert": "NVIDIA XID 79",
        "node": "gpu-worker-07",
        "severity": "critical",
        "decision": {
            "action": "cordon-and-recover",
            "requiresApproval": True,
        },
        "refs": {
            "evidenceBundle": "evidence://gpu-xid/inc-gpu-xid-001",
        },
    }
    approved = FrozenEvidence.capture(alert_evidence)

    # The live source changes while approval is pending. Resume must not consume it.
    alert_evidence["severity"] = "warning"
    alert_evidence["refs"]["evidenceBundle"] = "evidence://live/changed"

    providers, executors = registries()
    result = ApplyReconciler(
        providers=providers,
        executors=executors,
    ).reconcile(
        plan(),
        approved_evidence=approved,
        eval_gates=[{
            "spec": {
                "conditions": [
                    {"metric": "remediation_success", "op": "eq", "value": 1.0},
                    {"metric": "false_automation_rate", "op": "lte", "value": 0.01},
                ],
                "onFailure": "rollback",
            }
        }],
        metrics={
            "remediation_success": 1.0,
            "false_automation_rate": 0.0,
        },
    )

    print(json.dumps({
        "incident": result.evidence["golden_slice"]["incidentId"],
        "approvedSeverity": result.evidence["golden_slice"]["severity"],
        "phase": result.phase,
        "executionOrder": [r.binding_name for r in result.receipts],
        "releaseEvidenceDigest": result.evidence["replay_digest"],
        "replayVerified": verify_release_evidence(result.evidence),
    }, indent=2))


if __name__ == "__main__":
    main()
