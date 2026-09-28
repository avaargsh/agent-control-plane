import json

import pytest

from agent_control_plane.cli import main


GATE = """apiVersion: agentplane.io/v1alpha1
kind: EvalGate
metadata:
  name: replay
spec:
  suiteRef: replay
  onFailure: block
  conditions:
    - metric: false_automation_rate
      op: eq
      value: 0
    - metric: escalation_accuracy
      op: gte
      value: 0.95
"""


def test_gate_cli_passes(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    gate = tmp_path / "gate.yaml"
    gate.write_text(
        GATE,
        encoding="utf-8",
    )
    metrics = tmp_path / "metrics.json"
    metrics.write_text(
        json.dumps(
            {
                "false_automation_rate": 0,
                "escalation_accuracy": 0.98,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "sys.argv",
        [
            "agent-control-plane",
            "gate",
            str(gate),
            "--metrics",
            str(metrics),
        ],
    )

    with pytest.raises(
        SystemExit,
    ) as exc:
        main()

    assert exc.value.code == 0
    assert '"passed": true' in (
        capsys.readouterr().out
    )


def test_gate_cli_fails_closed(
    tmp_path,
    monkeypatch,
) -> None:
    gate = tmp_path / "gate.yaml"
    gate.write_text(
        GATE,
        encoding="utf-8",
    )
    metrics = tmp_path / "metrics.json"
    metrics.write_text(
        json.dumps(
            {
                "escalation_accuracy": 0.98,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "sys.argv",
        [
            "agent-control-plane",
            "gate",
            str(gate),
            "--metrics",
            str(metrics),
        ],
    )

    with pytest.raises(
        SystemExit,
    ) as exc:
        main()

    assert exc.value.code == 2
