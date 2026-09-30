import json

from agent_control_plane.mcp_subprocess import MCPSubprocessToolAdapter
from agent_control_plane.tool_contract import execute_with_contract


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
            "verification": {
                "mode": "read-after-write",
                "tool": "deploy",
                "operation": "status",
            },
            "compensation": {
                "tool": "deploy",
                "operation": "rollback",
            },
        }
    }


def state(path):
    return json.loads(path.read_text(encoding="utf-8"))


def execute(adapter, *, action_id, fault_mode="none", health="ok"):
    return execute_with_contract(
        run_id="run-mcp-proof",
        action_id=action_id,
        contract=contract(),
        invoke=lambda key: adapter.call_tool(
            "deploy.apply",
            {
                "idempotency_key": key,
                "deployment": "v2",
                "health": health,
            },
            fault_mode=fault_mode,
        ),
        verify=adapter.verify,
        validate_result=lambda result: result.get("health") == "ok",
        compensate=adapter.rollback,
    )


def test_lost_ack_recovers_by_read_after_write_without_duplicate_effect(tmp_path):
    path = tmp_path / "lost-ack.json"
    adapter = MCPSubprocessToolAdapter(state_path=path, timeout_seconds=1.0)

    result = execute(
        adapter,
        action_id="lost-ack",
        fault_mode="lost_ack_once",
    )

    persisted = state(path)
    assert result.verified_after_error is True
    assert result.attempts == 1
    assert result.compensated is False
    assert persisted["side_effect_count"] == 1
    assert persisted["operations"][result.idempotency_key]["commit_count"] == 1


def test_timeout_before_commit_retries_same_key_and_commits_once(tmp_path):
    path = tmp_path / "timeout.json"
    adapter = MCPSubprocessToolAdapter(
        state_path=path,
        timeout_seconds=0.3,
        fault_sleep_seconds=2.0,
    )

    result = execute(
        adapter,
        action_id="timeout",
        fault_mode="timeout_before_commit_once",
    )

    persisted = state(path)
    operation = persisted["operations"][result.idempotency_key]
    assert result.attempts == 2
    assert result.verified_after_error is False
    assert persisted["side_effect_count"] == 1
    assert operation["apply_invocations"] == 2
    assert operation["commit_count"] == 1


def test_partial_commit_is_not_mistaken_for_success_and_retry_finishes_once(tmp_path):
    path = tmp_path / "partial.json"
    adapter = MCPSubprocessToolAdapter(state_path=path, timeout_seconds=1.0)

    result = execute(
        adapter,
        action_id="partial",
        fault_mode="partial_commit_once",
    )

    persisted = state(path)
    operation = persisted["operations"][result.idempotency_key]
    assert result.attempts == 2
    assert result.result["status"] == "committed"
    assert persisted["side_effect_count"] == 1
    assert operation["commit_count"] == 1


def test_failed_post_condition_executes_declared_compensation(tmp_path):
    path = tmp_path / "compensate.json"
    adapter = MCPSubprocessToolAdapter(state_path=path, timeout_seconds=1.0)

    result = execute(
        adapter,
        action_id="compensate",
        health="failed",
    )

    persisted = state(path)
    operation = persisted["operations"][result.idempotency_key]
    assert result.compensated is True
    assert result.compensation_result["status"] == "rolled_back"
    assert persisted["side_effect_count"] == 1
    assert persisted["rollback_count"] == 1
    assert operation["rollback_count"] == 1
