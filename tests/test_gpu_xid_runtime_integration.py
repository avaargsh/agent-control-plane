from agent_control_plane.apply_reconciler import ApplyReconciler
from agent_control_plane.decision_adapter import DecisionGatewayAdapter
from agent_control_plane.example_adapters import CodexHarnessAdapter, KubernetesSandboxAdapter, TemporalWorkflowAdapter
from agent_control_plane.executors import ExecutorRegistry, InMemoryExecutor
from agent_control_plane.frozen_evidence import FrozenEvidence
from agent_control_plane.kubernetes_runtime_client import KubernetesSandboxClient
from agent_control_plane.registry import ProviderRegistry
from agent_control_plane.release_evidence import verify_release_evidence
from agent_control_plane.runtime_executors import KubernetesSandboxExecutor, TemporalWorkflowExecutor
from agent_control_plane.temporal_runtime_client import TemporalWorkflowClient
from agent_control_plane.tool_adapter import MCPToolAdapter
from test_apply_reconciler import PASS_GATE, build_plan


class K8sApi:
    def __init__(self): self.resources = {}
    def get(self, *, namespace, name): return self.resources.get((namespace, name))
    def apply(self, *, namespace, manifest):
        value = {**manifest, "metadata": {**manifest["metadata"], "uid": "sandbox-uid-xid", "resourceVersion": "11"}}
        self.resources[(namespace, manifest["metadata"]["name"])] = manifest
        return value
    def delete(self, *, namespace, name): return {"deleted": True}


class TemporalApi:
    def __init__(self): self.workflows = {}
    def describe(self, *, workflow_id): return self.workflows.get(workflow_id)
    def start(self, *, workflow_id, workflow_type, task_queue, input):
        value = {"runId": "temporal-run-xid", "status": "RUNNING"}
        self.workflows[workflow_id] = value
        return value
    def terminate(self, *, workflow_id, reason): return {"terminated": True}


def test_gpu_xid_incident_records_runtime_identities_in_replayable_release_evidence():
    providers = ProviderRegistry()
    for provider in [KubernetesSandboxAdapter(), MCPToolAdapter(), CodexHarnessAdapter(), TemporalWorkflowAdapter(), DecisionGatewayAdapter()]:
        providers.register(provider)

    executors = ExecutorRegistry()
    executors.register(KubernetesSandboxExecutor(KubernetesSandboxClient(K8sApi())))
    executors.register(TemporalWorkflowExecutor(TemporalWorkflowClient(TemporalApi())))
    for provider_type, provider_name in [("tool", "mcp"), ("harness", "codex"), ("decision", "decision-gateway")]:
        executors.register(InMemoryExecutor(provider_type, provider_name))

    approved = FrozenEvidence.capture({
        "incidentId": "inc-gpu-xid-001",
        "alert": "NVIDIA XID 79",
        "severity": "critical",
        "decision": {"action": "cordon-and-recover", "requiresApproval": True},
    })

    result = ApplyReconciler(providers=providers, executors=executors).reconcile(
        build_plan(), approved_evidence=approved,
        eval_gates=[PASS_GATE], metrics={"accuracy": 0.99, "ece": 0.01},
    )

    receipts = {receipt.binding_name: receipt for receipt in result.receipts}
    assert result.phase == "Promoted"
    assert receipts["sandbox"].evidence["uid"] == "sandbox-uid-xid"
    assert receipts["workflow"].evidence["workflowId"].startswith("agent-release/")
    assert receipts["workflow"].evidence["runId"] == "temporal-run-xid"
    assert result.evidence["golden_slice"]["severity"] == "critical"
    assert verify_release_evidence(result.evidence)
