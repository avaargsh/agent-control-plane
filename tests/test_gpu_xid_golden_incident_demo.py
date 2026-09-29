import runpy
from pathlib import Path


def test_gpu_xid_golden_incident_demo_executes(capsys):
    path = Path(__file__).resolve().parents[1] / "examples/gpu_xid_golden_incident.py"
    runpy.run_path(str(path), run_name="__main__")
    output = capsys.readouterr().out
    assert '"approvedSeverity": "critical"' in output
    assert '"phase": "promoted"' in output
    assert '"replayVerified": true' in output
