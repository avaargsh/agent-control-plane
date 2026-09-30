import pytest

from agent_control_plane.opa_provider import (
    OPACompileError,
    compile_opa_bundle,
)


def test_capabilities_compile_to_independent_or_rules() -> None:
    bundle = compile_opa_bundle(
        {
            "spec": {
                "defaultDeny": True,
                "capabilities": [
                    {
                        "name": "filesystem.read",
                        "constraints": {
                            "paths": ["/var/log/app.log"],
                        },
                    },
                    {
                        "name": "network.egress",
                        "constraints": {
                            "hosts": ["api.internal.example"],
                        },
                    },
                    {
                        "name": "tool.observability.query",
                    },
                ],
            }
        }
    )

    policy = bundle["policy.rego"]
    assert "import rego.v1" in policy
    assert "default allow := false" in policy
    assert policy.count("allow if {") == 3
    assert 'input.capability == "filesystem.read"' in policy
    assert 'input.resource in ["/var/log/app.log"]' in policy
    assert 'input.capability == "network.egress"' in policy
    assert 'input.resource in ["api.internal.example"]' in policy
    assert 'input.capability == "tool.observability.query"' in policy


def test_kubernetes_constraints_compile_to_verb_and_resource() -> None:
    policy = compile_opa_bundle(
        {
            "spec": {
                "capabilities": [
                    {
                        "name": "kubernetes.mutate",
                        "constraints": {
                            "verbs": ["patch"],
                            "resources": ["deployments"],
                        },
                    }
                ]
            }
        }
    )["policy.rego"]

    assert 'input.operation in ["patch"]' in policy
    assert 'input.resource in ["deployments"]' in policy


def test_explicit_deny_removes_same_name_allow_rule() -> None:
    policy = compile_opa_bundle(
        {
            "spec": {
                "capabilities": [
                    {
                        "name": "network.egress",
                        "effect": "allow",
                        "constraints": {
                            "hosts": ["evil.example"],
                        },
                    },
                    {
                        "name": "network.egress",
                        "effect": "deny",
                    },
                ]
            }
        }
    )["policy.rego"]

    assert 'input.capability == "network.egress"' not in policy
    assert "default allow := false" in policy


def test_unknown_constraint_shape_fails_closed() -> None:
    with pytest.raises(
        OPACompileError,
        match="unsupported constraints",
    ):
        compile_opa_bundle(
            {
                "spec": {
                    "capabilities": [
                        {
                            "name": "custom.capability",
                            "constraints": {
                                "tenant": "prod",
                            },
                        }
                    ]
                }
            }
        )


def test_opa_provider_rejects_default_allow_intent() -> None:
    with pytest.raises(
        OPACompileError,
        match="defaultDeny=true",
    ):
        compile_opa_bundle(
            {
                "spec": {
                    "defaultDeny": False,
                    "capabilities": [
                        {"name": "tool.query"},
                    ],
                }
            }
        )
