import pytest

from agent_control_plane.tool_contract import (
    RetryableToolError,
    ToolContractViolation,
    execute_with_contract,
)


def contract(max_attempts=3):
    return {
        "spec": {
            "tool": "deploy",
            "operation": "apply",
            "effect": "external-side-effect",
            "idempotency": {
                "mode": "required",
                "keyTemplate": "{run_id}:{action_id}",
            },
            "retry": {"maxAttempts": max_attempts},
            "verification": {"mode": "read-after-write"},
        }
    }


def test_lost_ack_is_recovered_by_verification_without_duplicate_side_effect() -> None:
    committed = {}
    calls = []

    def invoke(key):
        calls.append(key)
        if key not in committed:
            committed[key] = {"deployment": "v2"}
            raise RetryableToolError("lost ack after commit")
        return committed[key]

    def verify(key):
        return committed.get(key)

    result = execute_with_contract(
        run_id="run-001",
        action_id="deploy-001",
        contract=contract(),
        invoke=invoke,
        verify=verify,
    )

    assert result.idempotency_key == "run-001:deploy-001"
    assert result.verified_after_error is True
    assert result.attempts == 1
    assert len(calls) == 1
    assert committed == {"run-001:deploy-001": {"deployment": "v2"}}


def test_side_effect_retry_without_idempotency_fails_closed() -> None:
    unsafe = contract()
    unsafe["spec"]["idempotency"]["mode"] = "none"
    with pytest.raises(ToolContractViolation):
        execute_with_contract(
            run_id="run-001",
            action_id="deploy-001",
            contract=unsafe,
            invoke=lambda key: None,
            verify=lambda key: None,
        )
