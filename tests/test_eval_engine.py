from agent_control_plane.eval_engine import evaluate_gate


GATE = {
    "spec": {
        "conditions": [
            {"metric": "accuracy", "op": "gte", "value": 0.9},
            {"metric": "false_automation_rate", "op": "lte", "value": 0.01},
        ]
    }
}


def test_eval_gate_passes() -> None:
    result = evaluate_gate(
        GATE,
        {
            "accuracy": 0.95,
            "false_automation_rate": 0.005,
        },
    )
    assert result.passed is True
    assert result.violations == ()


def test_eval_gate_reports_threshold_failure() -> None:
    result = evaluate_gate(
        GATE,
        {
            "accuracy": 0.8,
            "false_automation_rate": 0.005,
        },
    )
    assert result.passed is False
    assert result.violations[0].metric == "accuracy"
    assert result.violations[0].reason == "THRESHOLD_FAILED"


def test_eval_gate_fails_closed_on_missing_metric() -> None:
    result = evaluate_gate(
        GATE,
        {
            "accuracy": 0.95,
        },
    )
    assert result.passed is False
    assert result.violations[0].metric == "false_automation_rate"
    assert result.violations[0].reason == "MISSING_METRIC"



AIOPS_REPLAY_GATE = {
    "spec": {
        "suiteRef": "agentic-aiops/replay-regression",
        "evidenceRequired": True,
        "onFailure": "block",
        "conditions": [
            {"metric": "unsupported_claim_rate", "op": "lte", "value": 0.02},
            {"metric": "false_automation_rate", "op": "eq", "value": 0.0},
            {"metric": "escalation_accuracy", "op": "gte", "value": 0.95},
            {"metric": "unnecessary_tool_rate", "op": "lte", "value": 0.10},
            {"metric": "recovery_success_rate", "op": "gte", "value": 0.90},
        ],
    }
}


def test_aiops_replay_metrics_can_promote_release() -> None:
    result = evaluate_gate(
        AIOPS_REPLAY_GATE,
        {
            "unsupported_claim_rate": 0.01,
            "false_automation_rate": 0.0,
            "escalation_accuracy": 0.98,
            "unnecessary_tool_rate": 0.05,
            "recovery_success_rate": 0.95,
        },
    )
    assert result.passed is True


def test_aiops_replay_gate_blocks_release_when_recovery_metric_is_unknown() -> None:
    result = evaluate_gate(
        AIOPS_REPLAY_GATE,
        {
            "unsupported_claim_rate": 0.01,
            "false_automation_rate": 0.0,
            "escalation_accuracy": 0.98,
            "unnecessary_tool_rate": 0.05,
        },
    )
    assert result.passed is False
    assert result.violations[0].metric == "recovery_success_rate"
    assert result.violations[0].reason == "MISSING_METRIC"
