from agent_control_plane.validator import validate_manifest


def test_v32_agent_release_extension_validates() -> None:
    validate_manifest(
        {
            "apiVersion": "agentplane.io/v1alpha1",
            "kind": "AgentRelease",
            "metadata": {"name": "sre-agent-v32"},
            "spec": {
                "bundleRef": "sre-agent",
                "bindings": ["tools-deploy"],
                "capabilityIntentRefs": ["sre-capabilities"],
                "evidenceRequirements": {
                    "requireExternalCollector": True,
                    "minimumIndependentObservers": 2,
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
    )


def test_v32_runtime_binding_accepts_tool_contract_ref() -> None:
    validate_manifest(
        {
            "apiVersion": "agentplane.io/v1alpha1",
            "kind": "RuntimeBinding",
            "metadata": {"name": "tools-deploy"},
            "spec": {
                "type": "tool",
                "provider": "mcp",
                "toolContractRef": "deploy-contract",
            },
        }
    )
