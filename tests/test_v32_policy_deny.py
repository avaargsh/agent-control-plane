from agent_control_plane.policy_projection import (
    authorize_request,
    compile_capability_intent,
)


def test_explicit_deny_overrides_same_capability_allow() -> None:
    projection = compile_capability_intent(
        {
            "spec": {
                "defaultDeny": True,
                "capabilities": [
                    {
                        "name": "network.egress",
                        "effect": "allow",
                        "constraints": {"hosts": ["evil.example"]},
                    },
                    {
                        "name": "network.egress",
                        "effect": "deny",
                    },
                ],
            }
        }
    )

    decision = authorize_request(
        projection,
        target="network",
        operation="egress",
        resource="evil.example",
    )
    assert decision.allowed is False
    assert decision.reason == "POLICY_DENIED"
