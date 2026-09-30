from agent_control_plane.policy_projection import authorize_request, compile_capability_intent


intent = {
    "spec": {
        "defaultDeny": True,
        "capabilities": [
            {"name": "filesystem.read", "constraints": {"paths": ["/var/log/app.log"]}},
            {"name": "network.egress", "constraints": {"hosts": ["api.internal.example"]}},
            {
                "name": "kubernetes.mutate",
                "constraints": {"verbs": ["patch"], "resources": ["deployments"]},
            },
        ],
    }
}

projection = compile_capability_intent(intent)
requests = [
    ("filesystem", "read", "~/.aws/credentials"),
    ("network", "egress", "evil.example"),
    ("kubernetes", "delete", "deployments"),
]
for target, operation, resource in requests:
    decision = authorize_request(
        projection,
        target=target,
        operation=operation,
        resource=resource,
    )
    print(
        target,
        operation,
        resource,
        "ALLOW" if decision.allowed else "DENY",
        decision.reason,
    )
