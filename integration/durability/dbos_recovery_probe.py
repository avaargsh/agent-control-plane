"""Validate continuation crash/recovery using two isolated upstream installations.

PASS here means the observed matrix matched its expectations. The cross-version
case must reject recovery; it never authorizes an in-place upgrade.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import signal
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path


OLD, NEW = "2.56.0", "2.57.0"
MODEL_STEP = "durability-probe__model.request"
CASES = {"old-old": (OLD, OLD), "new-new": (NEW, NEW), "old-new": (OLD, NEW)}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def evaluate_case(
    case: str,
    *,
    seed_code: int,
    recover_code: int,
    versions: dict,
    checkpoint: dict,
    recovery: dict,
    requests: list[dict],
) -> dict:
    """Reject false positives, including a timeout masquerading as incompatibility."""
    old, new = CASES[case]
    require(seed_code == -signal.SIGKILL, "seed was not terminated by SIGKILL")
    require(recover_code == 0, "recovery worker failed")
    for phase, expected in (("seed", old), ("recover", new)):
        actual = versions[phase]
        require(
            actual.get("pydantic-ai-slim") == expected,
            f"wrong {phase} Pydantic version",
        )
        require(
            actual.get("pydantic-graph") == expected, f"wrong {phase} graph version"
        )
        require(actual.get("dbos") == "3.3.0", f"wrong {phase} DBOS version")
    seed_steps = checkpoint["steps"]
    expected_names = (
        [MODEL_STEP, "DBOS.sleep", MODEL_STEP, "DBOS.sleep"]
        if old == NEW
        else [MODEL_STEP] * 2
    )
    require(
        [s["function_name"] for s in seed_steps] == expected_names,
        "unexpected seed step sequence",
    )
    require(
        [s["function_id"] for s in seed_steps] == list(range(1, len(seed_steps) + 1)),
        "unexpected seed step IDs",
    )
    require(
        all(s["error"] is None for s in seed_steps), "seed checkpoint contains errors"
    )
    require(
        recovery["steps"][: len(seed_steps)] == seed_steps,
        "recorded checkpoint changed during recovery",
    )
    seed_calls = [r["segment"] for r in requests if r["phase"] == "seed"]
    recovered_calls = [r["segment"] for r in requests if r["phase"] == "recover"]
    require(
        len(seed_calls) + len(recovered_calls) == len(requests), "unknown request phase"
    )
    require(seed_calls == [0, 1, 2], "seed did not execute the expected requests")
    require(
        not any(n in (0, 1) for n in recovered_calls),
        "completed model request repeated",
    )
    if case == "old-new":
        require(
            recovery.get("error_type") == "DBOSUnexpectedStepError",
            "missing expected history incompatibility",
        )
        require(
            "step 2" in recovery.get("error", "") and "DBOS.sleep" in recovery["error"],
            "wrong incompatibility cause",
        )
        require(
            "output" not in recovery and recovered_calls == [],
            "incompatible history made progress",
        )
        disposition = "EXPECTED_HISTORY_INCOMPATIBILITY"
    else:
        require(
            "error_type" not in recovery and recovery.get("output") == "done",
            "same-version recovery failed",
        )
        require(recovered_calls == [2], "only the uncheckpointed request may retry")
        require(
            len(recovery["steps"]) == len(seed_steps) + 1,
            "unexpected recovered step count",
        )
        require(
            recovery["steps"][-1]["function_name"] == MODEL_STEP,
            "missing final model result",
        )
        require(
            recovery["steps"][-1]["error"] is None,
            "final model request recorded an error",
        )
        disposition = "RECOVERED_SAME_VERSION"
    return {
        "case": case,
        "status": "PASS",
        "disposition": disposition,
        "recorded_sleep_steps": expected_names.count("DBOS.sleep"),
        "completed_request_repeats": 0,
        "uncheckpointed_request_retries": len(recovered_calls),
    }


def read_json(path: Path):
    return json.loads(path.read_text())


def run_case(root: Path, case: str, index: int, interpreters: dict[str, str]) -> dict:
    directory = root / case / f"{index:04d}"
    directory.mkdir(parents=True, exist_ok=False)
    result = {
        "case": case,
        "iteration": index,
        "status": "FAIL",
        "directory": str(directory.relative_to(root)),
    }
    try:
        codes = {}
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("DBOS", "OTEL_", "LOGFIRE_"))
        }
        env["PYDANTIC_AI_NO_BANNER"] = "1"
        for phase, version in zip(("seed", "recover"), CASES[case], strict=True):
            try:
                proc = subprocess.run(
                    [
                        interpreters[version],
                        str(Path(__file__).with_name("dbos_worker.py")),
                        phase,
                        str(directory),
                    ],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            except subprocess.TimeoutExpired as exc:
                (directory / f"{phase}.stdout").write_bytes(exc.stdout or b"")
                (directory / f"{phase}.stderr").write_bytes(exc.stderr or b"")
                raise ValueError(f"{phase} timed out") from exc
            (directory / f"{phase}.stdout").write_text(proc.stdout)
            (directory / f"{phase}.stderr").write_text(proc.stderr)
            codes[phase] = proc.returncode
            (directory / "exit-codes.json").write_text(
                json.dumps(codes, indent=2) + "\n"
            )
            require(
                proc.returncode == (-signal.SIGKILL if phase == "seed" else 0),
                f"unexpected {phase} exit {proc.returncode}",
            )
        result.update(
            evaluate_case(
                case,
                seed_code=codes["seed"],
                recover_code=codes["recover"],
                versions={
                    phase: read_json(directory / f"{phase}-versions.json")
                    for phase in codes
                },
                checkpoint=read_json(directory / "seed-checkpoint.json"),
                recovery=read_json(directory / "recovery.json"),
                requests=[
                    json.loads(line)
                    for line in (directory / "requests.jsonl").read_text().splitlines()
                ],
            )
        )
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    (directory / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-python", required=True)
    parser.add_argument("--new-python", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument("--jobs", type=int, default=1)
    args = parser.parse_args()
    if sys.platform == "win32" or args.iterations < 1 or not 1 <= args.jobs <= 4:
        parser.error("requires POSIX, positive iterations, and 1..4 jobs")
    # Keep venv symlink paths: resolving them would select the base interpreter.
    interpreters = {
        OLD: os.path.abspath(args.old_python),
        NEW: os.path.abspath(args.new_python),
    }
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    rows = []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        pending = [
            pool.submit(run_case, root, case, i, interpreters)
            for case in CASES
            for i in range(args.iterations)
        ]
        for future in as_completed(pending):
            if future.cancelled():
                continue
            row = future.result()
            rows.append(row)
            print(json.dumps(row), flush=True)
            if row["status"] == "FAIL":
                # Stop queued cases; in-flight workers finish with bounded timeouts.
                for queued in pending:
                    queued.cancel()
    count = Counter(row["disposition"] for row in rows if row["status"] == "PASS")
    passed = len(rows) == len(CASES) * args.iterations and all(
        r["status"] == "PASS" for r in rows
    )
    files = {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
    summary = {
        "fixtureMode": "real-dbos-sqlite-synthetic-model",
        "matrixStatus": "PASS" if passed else "FAIL",
        "upgradeDecision": "BLOCK_UNVERSIONED_TAKEOVER",
        "liveProviderAcceptance": False,
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(),
        "iterationsPerCase": args.iterations,
        "expectedCases": len(CASES) * args.iterations,
        "completedCases": len(rows),
        "dispositions": dict(count),
        "notRunCases": len(CASES) * args.iterations - len(rows),
        "sourceSha256": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (Path(__file__), Path(__file__).with_name("dbos_worker.py"))
        },
        "cases": sorted(rows, key=lambda row: (row["case"], row["iteration"])),
        "filesSha256": files,
    }
    (root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(
        json.dumps(
            {k: v for k, v in summary.items() if k not in ("cases", "filesSha256")}
        )
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
