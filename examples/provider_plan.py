from agent_control_plane.compiler import compile_release_plan
from agent_control_plane.example_adapters import (
    CodexHarnessAdapter,
    KubernetesSandboxAdapter,
    TemporalWorkflowAdapter,
)
from agent_control_plane.registry import ProviderRegistry


bundle = {"metadata": {"name": "coding-agent"}}
release = {
    "metadata": {"name": "coding-agent-v1"},
    "spec": {
        "bundleRef": "coding-agent",
        "bindings": ["harness-codex", "workflow-temporal", "sandbox-k8s"],
        "placement": {"region": "local"},
    },
}
bindings = [
    {
        "metadata": {"name": "harness-codex"},
        "spec": {"type": "harness", "provider": "codex", "config": {}},
    },
    {
        "metadata": {"name": "workflow-temporal"},
        "spec": {
            "type": "workflow",
            "provider": "temporal",
            "endpointRef": "service://temporal",
            "config": {"workflowType": "AgentRunWorkflow"},
        },
    },
    {
        "metadata": {"name": "sandbox-k8s"},
        "spec": {
            "type": "sandbox",
            "provider": "k8s-agent-sandbox",
            "config": {"isolation": "gvisor", "warmPool": True},
        },
    },
]

plan = compile_release_plan(
    release=release,
    bundles=[bundle],
    bindings=bindings,
)

registry = ProviderRegistry()
registry.register(CodexHarnessAdapter())
registry.register(TemporalWorkflowAdapter())
registry.register(KubernetesSandboxAdapter())

print(registry.prepare(plan))
