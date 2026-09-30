import json
from pathlib import Path
import subprocess
import sys


def test_gpu_xid_five_repo_contract_fixture_replays() -> None:
    root = Path(__file__).parents[1]
    completed = subprocess.run(
        [
            sys.executable,
            "examples/gpu_xid_golden_incident.py",
        ],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["fixtureMode"] == "synthetic-contract-fixture"
    assert payload["phase"] == "promoted"
    assert payload["replayVerified"] is True
    assert payload["system2FallbackMeasured"] is True
    assert payload["factoryTrusted"] is True
    assert payload["factoryAttestationKeyId"] == "commissioning-lab"
    assert payload["computeWorkload"] == {
        "id": "gpu-xid-validation",
        "generation": 7,
    }
    assert payload["identity"]["runId"] == "run-gpu-xid-001"
    assert payload["identity"]["temporalWorkflowId"] == (
        "agent-run/run-gpu-xid-001"
    )
    assert payload["identity"]["sandbox"]["replacementLineage"] == [
        "sandbox-a",
        "sandbox-b",
    ]
    assert payload["mcpReceiptRef"].startswith("synthetic://mcp/")
    assert payload["releaseEvidenceDigest"].startswith("sha256:")
    assert payload["executionOrder"] == [
        "sandbox",
        "tools",
        "harness",
        "workflow",
        "decision",
    ]
