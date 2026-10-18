"""Real DBOS/Pydantic AI worker with a local continuation model; no model API."""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import os
import signal
from pathlib import Path

from dbos import DBOS, SetWorkflowID
from pydantic_ai import Agent
from pydantic_ai.durable_exec.dbos import DBOSDurability
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel


def write_json(path: Path, value: object) -> None:
    with path.open("w") as stream:
        json.dump(value, stream, indent=2, default=str)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["seed", "recover"])
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    root = args.directory.resolve()
    # The orchestrator owns directory creation; never reuse a previous experiment.
    if not root.is_dir():
        raise RuntimeError("experiment directory does not exist")
    write_json(
        root / f"{args.phase}-versions.json",
        {d.metadata["Name"]: d.version for d in importlib.metadata.distributions()},
    )

    async def model_request(messages, info):
        previous = [m for m in messages if isinstance(m, ModelResponse)]
        segment = int(previous[-1].provider_details["segment"]) + 1 if previous else 0
        with (root / "requests.jsonl").open("a") as stream:
            stream.write(json.dumps({"phase": args.phase, "segment": segment}) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        if segment == 2 and args.phase == "seed":
            # Two model results and their intervening waits have completed.
            # This third request is intentionally NOT checkpointed and may retry.
            write_json(
                root / "seed-checkpoint.json",
                {
                    "steps": await DBOS.list_workflow_steps_async("probe"),
                },
            )
            os.kill(os.getpid(), signal.SIGKILL)
        return ModelResponse(
            parts=[TextPart("done" if segment == 2 else "pending")],
            state="complete" if segment == 2 else "suspended",
            provider_response_id="local-job",
            provider_details={"segment": segment},
        )

    class PollingModel(FunctionModel):
        def continuation_delay(self, response):
            return 0.01

    agent = Agent(
        PollingModel(model_request, model_name="durability-probe"),
        name="durability-probe",
        capabilities=[DBOSDurability()],
    )

    @DBOS.workflow(name="durability-probe")
    async def run():
        return (await agent.run("synthetic continuation", run_id="probe-run")).output

    DBOS(
        config={
            "name": "durability-probe",
            "system_database_url": f"sqlite:///{root / 'dbos.sqlite'}",
            # Deliberately reuse the application version to test unsafe takeover.
            # This is an experiment condition, NOT deployment guidance.
            "application_version": "unversioned-probe",
            "executor_id": "local-probe",
            "enable_otlp": False,
            "log_level": "ERROR",
        }
    )
    DBOS.launch()
    try:
        if args.phase == "seed":
            with SetWorkflowID("probe"):
                await run()
            raise RuntimeError("seed did not reach the SIGKILL boundary")
        handle = await DBOS.retrieve_workflow_async("probe")
        outcome = {}
        try:
            outcome["output"] = await asyncio.wait_for(handle.get_result(), timeout=15)
        except Exception as exc:
            outcome.update(error_type=type(exc).__name__, error=str(exc))
        outcome["steps"] = await DBOS.list_workflow_steps_async("probe")
        write_json(root / "recovery.json", outcome)
    finally:
        DBOS.destroy()


if __name__ == "__main__":
    asyncio.run(main())
