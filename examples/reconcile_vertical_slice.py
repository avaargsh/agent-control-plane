from pathlib import Path

from agent_control_plane.compiler import compile_release_plan
from agent_control_plane.decision_adapter import DecisionGatewayAdapter
from agent_control_plane.example_adapters import (
    CodexHarnessAdapter,
    KubernetesSandboxAdapter,
    TemporalWorkflowAdapter,
)
from agent_control_plane.loader import load_yaml_documents
from agent_control_plane.reconciler import ReleaseReconciler
from agent_control_plane.registry import ProviderRegistry
from agent_control_plane.tool_adapter import MCPToolAdapter


docs = load_yaml_documents(
    Path(__file__).with_name("vertical-slice.yaml"),
)

plan = compile_release_plan(
    release=docs.by_kind("AgentRelease")[0],
    bundles=docs.by_kind("AgentBundle"),
    bindings=docs.by_kind("RuntimeBinding"),
)

registry = ProviderRegistry()
registry.register(DecisionGatewayAdapter())
registry.register(CodexHarnessAdapter())
registry.register(TemporalWorkflowAdapter())
registry.register(MCPToolAdapter())
registry.register(KubernetesSandboxAdapter())

result = ReleaseReconciler(registry).reconcile(plan, dry_run=True)
print(result)
