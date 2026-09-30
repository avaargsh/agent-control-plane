from agent_control_plane.providers.opa import compile_opa_bundle


def test_capability_intent_compiles_to_deny_by_default_opa_policy() -> None:
    bundle = compile_opa_bundle(
        {
            "spec": {
                "capabilities": [
                    {
                        "name": "filesystem.read",
                        "effect": "allow",
                    }
                ]
            }
        }
    )

    assert "policy.rego" in bundle
    assert "default allow := false" in bundle["policy.rego"]
    assert "filesystem.read" in bundle["policy.rego"]
