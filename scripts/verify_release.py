#!/usr/bin/env python3
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / ".artifacts" / "release"


def run(*args: str) -> None:
    subprocess.run(args, cwd=ROOT, check=True)


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

    summary = {
        "project": project["name"],
        "version": project["version"],
        "license": project["license"],
        "tests": "passed",
        "demo": "passed",
        "gitHistoryScan": "separate make audit-history gate",
        "liveSmoke": "separate environment-dependent gate",
    }
    (ARTIFACTS / "verification.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
