import pytest

from agent_control_plane.example_adapters import (
    CodexHarnessAdapter,
    KubernetesSandboxAdapter,
    TemporalWorkflowAdapter,
)
from agent_control_plane.plan import ResolvedReleasePlan
from agent_control_plane.registry import ProviderRegistry


def make_plan():
    return ResolvedReleasePlan(
        release_name="agent-v1",
        bundle_name="agent",
        version="v1",
        bindings={
            "harness": {
                "metadata": {"name": "harness"},
                "spec": {"type": "harness", "provider": "codex", "config": {}},
            },
            "workflow": {
                "metadata": {"name": "workflow"},
                "spec": {"type": "workflow", "provider": "temporal"},
            },
            "sandbox": {
                "metadata": {"name": "sandbox"},
                "spec": {
                    "type": "sandbox",
                    "provider": "k8s-agent-sandbox",
                    "config": {"warmPool": True},
                },
            },
        },
    )


def test_registry_prepares_provider_plans() -> None:
    registry = ProviderRegistry()
    registry.register(CodexHarnessAdapter())
    registry.register(TemporalWorkflowAdapter())
    registry.register(KubernetesSandboxAdapter())

    prepared = registry.prepare(make_plan())

    assert prepared["harness"]["provider"] == "codex"
    assert prepared["workflow"]["provider"] == "temporal"
    assert prepared["sandbox"]["warm_pool"] == "default"


def test_unknown_provider_fails() -> None:
    registry = ProviderRegistry()

    with pytest.raises(KeyError):
        registry.prepare(make_plan())
