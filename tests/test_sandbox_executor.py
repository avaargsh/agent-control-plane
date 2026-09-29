import pytest

from agent_control_plane.example_adapters import KubernetesSandboxAdapter
from agent_control_plane.plan import ResolvedReleasePlan
from agent_control_plane.provider_runtime import ExternalResource
from agent_control_plane.sandbox_executor import KubernetesAgentSandboxExecutor


def plan() -> ResolvedReleasePlan:
    return ResolvedReleasePlan(
        release_name="sre-v1",
        bundle_name="sre",
        version="v1",
        placement={"target": "cell-b"},
        bindings={},
    )


def binding() -> dict:
    return {
        "metadata": {"name": "sandbox"},
        "spec": {
            "type": "sandbox",
            "provider": "k8s-agent-sandbox",
            "config": {
                "warmPoolRef": "sre-general",
                "runtimeClass": "gvisor",
                "ttlSeconds": 1800,
            },
        },
    }


class FakeSandbox:
    def __init__(self, *, changed=True):
        self.changed = changed
        self.ensured = None
        self.deleted = None

    def ensure_sandbox(self, **kwargs):
        self.ensured = kwargs
        return ExternalResource(
            resource_ref=f"k8s-sandbox://{kwargs['namespace']}/{kwargs['claim_name']}",
            changed=self.changed,
            external_refs={"sandbox.claim": kwargs["claim_name"]},
            evidence={"state": "created" if self.changed else "adopted-existing"},
        )

    def delete_sandbox(self, **kwargs):
        self.deleted = kwargs
        return {"state": "deleted"}


def test_adapter_compiles_stable_claim_and_runtime_intent() -> None:
    prepared = KubernetesSandboxAdapter().prepare(plan(), binding())
    assert prepared["claim_name"] == "sre-v1-sandbox"
    assert prepared["warm_pool"] == "sre-general"
    assert prepared["ttl_seconds"] == 1800
    assert prepared["isolation"] == "gvisor"


def test_executor_preserves_external_ref_and_deletes_owned_claim() -> None:
    transport = FakeSandbox()
    executor = KubernetesAgentSandboxExecutor(transport)
    b = binding()
    prepared = KubernetesSandboxAdapter().prepare(plan(), b)
    receipt = executor.apply(plan=plan(), binding=b, prepared=prepared)

    assert receipt.changed is True
    assert receipt.evidence["external_refs"]["sandbox.claim"] == "sre-v1-sandbox"
    assert transport.ensured["labels"]["agentplane.io/release"] == "sre-v1"

    rollback = executor.rollback(plan=plan(), binding=b, receipt=receipt)
    assert rollback.rolled_back is True
    assert transport.deleted["claim_name"] == "sre-v1-sandbox"


def test_executor_never_deletes_adopted_claim() -> None:
    transport = FakeSandbox(changed=False)
    executor = KubernetesAgentSandboxExecutor(transport)
    b = binding()
    prepared = KubernetesSandboxAdapter().prepare(plan(), b)
    receipt = executor.apply(plan=plan(), binding=b, prepared=prepared)
    rollback = executor.rollback(plan=plan(), binding=b, receipt=receipt)

    assert rollback.rolled_back is False
    assert transport.deleted is None


def test_executor_requires_explicit_warm_pool_intent() -> None:
    b = binding()
    b["spec"]["config"].pop("warmPoolRef")
    prepared = KubernetesSandboxAdapter().prepare(plan(), b)

    with pytest.raises(ValueError, match="warmPoolRef"):
        KubernetesAgentSandboxExecutor(FakeSandbox()).apply(
            plan=plan(), binding=b, prepared=prepared
        )
