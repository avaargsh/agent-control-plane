#!/usr/bin/env python3
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
import tomllib
import venv


ROOT = pathlib.Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / ".artifacts" / "release"


def run(*args: str, cwd: pathlib.Path = ROOT) -> None:
    subprocess.run(args, cwd=cwd, check=True)


def venv_python(path: pathlib.Path) -> pathlib.Path:
    return path / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def main() -> int:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)

    with (ROOT / "pyproject.toml").open("rb") as fh:
        project = tomllib.load(fh)["project"]

    if project.get("version") != "0.1.0":
        raise SystemExit("release verifier requires project.version == 0.1.0")
    if project.get("license") != "Apache-2.0":
        raise SystemExit("release verifier requires Apache-2.0 package metadata")
    if not (ROOT / "LICENSE").exists():
        raise SystemExit("LICENSE is missing")

    run(sys.executable, "-m", "pytest", "-q")
    run(sys.executable, "examples/gpu_xid_golden_incident.py")
    run(sys.executable, "experiments/v3_2/mcp_execution_contract.py")

    wheel_dir = ARTIFACTS / "wheel"
    wheel_dir.mkdir(parents=True, exist_ok=True)
    run(
        sys.executable,
        "-m",
        "pip",
        "wheel",
        "--no-deps",
        "--wheel-dir",
        str(wheel_dir),
        ".",
    )
    wheels = sorted(wheel_dir.glob("agent_control_plane-*.whl"))
    if len(wheels) != 1:
        raise SystemExit(f"expected exactly one release wheel, found {len(wheels)}")

    with tempfile.TemporaryDirectory(prefix="agent-control-plane-wheel-") as tmp:
        tmp_path = pathlib.Path(tmp)
        isolated = tmp_path / "venv"
        venv.EnvBuilder(with_pip=True).create(isolated)
        python = venv_python(isolated)
        run(
            str(python),
            "-m",
            "pip",
            "install",
            str(wheels[0]),
            cwd=tmp_path,
        )
        run(
            str(python),
            "-c",
            (
                "from agent_control_plane.validator import schema_directory; "
                "p=schema_directory(); "
                "assert (p/'tool-contract.schema.json').is_file(); "
                "assert (p/'evidence-event.schema.json').is_file()"
            ),
            cwd=tmp_path,
        )
        run(
            str(python),
            "-m",
            "agent_control_plane.cli",
            "validate",
            str(ROOT / "examples" / "v3.2-contracts.yaml"),
            cwd=tmp_path,
        )

    summary = {
        "project": project["name"],
        "version": project["version"],
        "license": project["license"],
        "tests": "passed",
        "demo": "passed",
        "mcpExecutionContract": "passed",
        "wheelBuild": wheels[0].name,
        "isolatedWheelInstall": "passed",
        "gitHistoryScan": "separate make audit-history gate",
        "liveSmoke": "separate environment-dependent gate",
    }
    (ARTIFACTS / "verification.json").write_text(
        json.dumps(summary, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
