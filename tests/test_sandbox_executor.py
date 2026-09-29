from agent_control_plane.example_adapters import KubernetesSandboxAdapter
from agent_control_plane.plan import ResolvedReleasePlan
from agent_control_plane.provider_runtime import ExternalResource
from agent_control_plane.sandbox_executor import KubernetesAgentSandboxExecutor


def plan():
    return ResolvedReleasePlan(
        release_name="sre-v3",
        bundle_name="sre",
        version="v3",
        bindings={},
    )


class FakeSandbox:
    def __init__(self):
        self.created = []
        self.deleted = []

    def ensure_sandbox(self, **kwargs):
        self.created.append(kwargs)
        return ExternalResource(
            resource_ref="k8s-sandbox://agents/sre-v3-sandbox",
            changed=True,
            external_refs={
                "sandbox.claim": kwargs["claim_name"],
                "sandbox.name": "warm-abc",
            },
            evidence={"state": "ready"},
        )

    def delete_sandbox(self, **kwargs):
        self.deleted.append(kwargs)
        return {"state": "deleted", **kwargs}


def test_sandbox_executor_uses_deterministic_claim_and_external_refs():
    binding = {
        "metadata": {"name": "sandbox"},
        "spec": {
            "type": "sandbox",
            "provider": "k8s-agent-sandbox",
            "config": {
                "namespace": "agents",
                "warmPoolRef": "sre-pool",
                "ttlSeconds": 1800,
                "runtimeClass": "gvisor",
            },
        },
    }
    prepared = KubernetesSandboxAdapter().prepare(plan(), binding)
    transport = FakeSandbox()
    executor = KubernetesAgentSandboxExecutor(transport)

    receipt = executor.apply(plan=plan(), binding=binding, prepared=prepared)

    assert prepared["claim_name"] == "sre-v3-sandbox"
    assert transport.created[0]["warm_pool"] == "sre-pool"
    assert transport.created[0]["ttl_seconds"] == 1800
    assert receipt.evidence["external_refs"]["sandbox.name"] == "warm-abc"

    rollback = executor.rollback(plan=plan(), binding=binding, receipt=receipt)
    assert rollback.rolled_back is True
    assert transport.deleted[0] == {
        "claim_name": "sre-v3-sandbox",
        "namespace": "agents",
    }
