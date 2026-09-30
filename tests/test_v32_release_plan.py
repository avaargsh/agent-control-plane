import pytest

from agent_control_plane.compiler import ReleaseCompileError, compile_release_plan
from agent_control_plane.tool_adapter import MCPToolAdapter


def bundle():
    return {"metadata": {"name": "sre-agent"}}


def capability():
    return {
        "metadata": {"name": "sre-capabilities"},
        "spec": {"capabilities": [{"name": "logs.read"}]},
    }


def contract():
    return {
        "metadata": {"name": "deploy-contract"},
        "spec": {},
    }


def tool_binding():
    return {
        "metadata": {"name": "tools-deploy"},
        "spec": {
            "type": "tool",
            "provider": "mcp",
            "endpointRef": "service://deploy-mcp",
            "toolContractRef": "deploy-contract",
            "config": {
                "capabilities": ["deploy.apply"],
                "mode": "write",
            },
        },
    }


def release():
    return {
        "metadata": {"name": "sre-agent-v32"},
        "spec": {
            "bundleRef": "sre-agent",
            "version": "v3.2",
            "bindings": ["tools-deploy"],
            "capabilityIntentRefs": ["sre-capabilities"],
            "evidenceRequirements": {
                "requireExternalCollector": True,
                "minimumIndependentObservers": 1,
                "requiredProvenanceFields": [
                    "generated_by",
                    "observed_by",
                    "authorized_by",
                    "executed_by",
                    "committed_by",
                ],
            },
        },
    }


def test_v32_release_plan_preserves_control_plane_contracts() -> None:
    plan = compile_release_plan(
        release=release(),
        bundles=[bundle()],
        bindings=[tool_binding()],
        capability_intents=[capability()],
        tool_contracts=[contract()],
    )

    assert plan.capability_intent_refs == ("sre-capabilities",)
    assert plan.evidence_requirements["requireExternalCollector"] is True

    tool_plan = MCPToolAdapter().prepare(
        plan,
        plan.bindings["tools-deploy"],
    )
    assert tool_plan["contractRef"] == "deploy-contract"
    assert tool_plan["endpointRef"] == "service://deploy-mcp"


def test_unknown_capability_intent_fails_closed() -> None:
    with pytest.raises(
        ReleaseCompileError,
        match="unknown capabilityIntentRef",
    ):
        compile_release_plan(
            release=release(),
            bundles=[bundle()],
            bindings=[tool_binding()],
            capability_intents=[],
            tool_contracts=[contract()],
        )


def test_unknown_tool_contract_fails_closed() -> None:
    with pytest.raises(
        ReleaseCompileError,
        match="unknown ToolContract",
    ):
        compile_release_plan(
            release=release(),
            bundles=[bundle()],
            bindings=[tool_binding()],
            capability_intents=[capability()],
            tool_contracts=[],
        )
