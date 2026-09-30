from __future__ import annotations

import json

import pytest

from agent_control_plane.cli import main


BASELINE = """apiVersion: agentplane.io/v1alpha1
kind: AgentAuthorityEnvelope
metadata:
  name: checkout-prod
spec:
  fleetRef: prod-sre
  teamRef: team://sre-platform
  agentRef: checkout-remediator
  releaseRef: checkout-v1
  runtimeRefs:
    - temporal://prod
    - sandbox://prod
  grants:
    - effect: write
      capability: kubernetes.scale
      resource: kubernetes://prod/checkout/deployment/checkout-api
      verbs: [scale]
  constraints:
    approvalMode: human-exact
    evidenceRequired: true
    maxOperationsPerRun: 1
"""


EXPANDED = """apiVersion: agentplane.io/v1alpha1
kind: AgentAuthorityEnvelope
metadata:
  name: checkout-prod
spec:
  fleetRef: prod-sre
  teamRef: team://sre-platform
  agentRef: checkout-remediator
  releaseRef: checkout-v2
  runtimeRefs:
    - temporal://prod
    - sandbox://prod
  grants:
    - effect: write
      capability: kubernetes.scale
      resource: kubernetes://prod/checkout/deployment/checkout-api
      verbs: [scale]
    - effect: write
      capability: kubernetes.delete
      resource: kubernetes://prod/checkout/pod/*
      verbs: [delete]
  constraints:
    approvalMode: human-exact
    evidenceRequired: true
    maxOperationsPerRun: 1
"""


def test_authority_inventory_cli(tmp_path, monkeypatch, capsys):
    path = tmp_path / "inventory.yaml"
    path.write_text(BASELINE, encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        [
            "agent-control-plane",
            "authority-inventory",
            str(path),
        ],
    )

    main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["fleetCount"] == 1
    assert payload["agentCount"] == 1
    assert payload["deploymentCount"] == 1


def test_authority_admit_cli_denies_expansion(
    tmp_path,
    monkeypatch,
    capsys,
):
    baseline = tmp_path / "baseline.yaml"
    proposed = tmp_path / "proposed.yaml"
    baseline.write_text(BASELINE, encoding="utf-8")
    proposed.write_text(EXPANDED, encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        [
            "agent-control-plane",
            "authority-admit",
            str(proposed),
            "--baseline",
            str(baseline),
        ],
    )

    with pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["decision"] == "DENY"
    assert "AUTHORITY_EXPANSION" in payload["reasons"]


def test_authority_admit_cli_allows_initial_authority(
    tmp_path,
    monkeypatch,
    capsys,
):
    proposed = tmp_path / "proposed.yaml"
    proposed.write_text(BASELINE, encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        [
            "agent-control-plane",
            "authority-admit",
            str(proposed),
        ],
    )

    with pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["decision"] == "ADMIT"
