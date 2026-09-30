from agent_control_plane.tool_contract import RetryableToolError, execute_with_contract


contract = {
    "spec": {
        "tool": "deploy",
        "operation": "apply",
        "effect": "external-side-effect",
        "idempotency": {
            "mode": "required",
            "keyTemplate": "{run_id}:{action_id}",
        },
        "retry": {"maxAttempts": 3},
        "verification": {"mode": "read-after-write"},
    }
}
committed = {}


def invoke(key):
    if key not in committed:
        committed[key] = {"deployment": "v2"}
        raise RetryableToolError("lost ack after commit")
    return committed[key]


result = execute_with_contract(
    run_id="run-001",
    action_id="deploy-001",
    contract=contract,
    invoke=invoke,
    verify=committed.get,
)
print("key", result.idempotency_key)
print("attempts", result.attempts)
print("verified_after_error", result.verified_after_error)
print("side_effect_count", len(committed))
