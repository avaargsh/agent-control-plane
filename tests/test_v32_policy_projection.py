from agent_control_plane.policy_projection import (
    authorize_request,
    compile_capability_intent,
)


def intent():
    return {
        "spec": {
            "defaultDeny": True,
            "capabilities": [
                {"name": "filesystem.read", "constraints": {"paths": ["/var/log/app.log"]}},
                {"name": "network.egress", "constraints": {"hosts": ["api.internal.example"]}},
                {
                    "name": "kubernetes.mutate",
                    "constraints": {"verbs": ["patch"], "resources": ["deployments"]},
                },
                {"name": "tool.observability.query"},
            ],
        }
    }


def test_model_can_propose_but_runtime_denies_out_of_policy_actions() -> None:
    projection = compile_capability_intent(intent())

    assert not authorize_request(
        projection,
        target="filesystem",
        operation="read",
        resource="~/.aws/credentials",
    ).allowed
    assert not authorize_request(
        projection,
        target="network",
        operation="egress",
        resource="evil.example",
    ).allowed
    assert not authorize_request(
        projection,
        target="kubernetes",
        operation="delete",
        resource="deployments",
    ).allowed
    assert authorize_request(
        projection,
        target="filesystem",
        operation="read",
        resource="/var/log/app.log",
    ).allowed
