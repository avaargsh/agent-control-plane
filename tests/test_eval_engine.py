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
