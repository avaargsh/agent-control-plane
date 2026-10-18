"""Negative controls for experiment evidence; upstream packages remain opt-in."""

import copy
import importlib.util
from pathlib import Path

import pytest


_PATH = Path(__file__).parents[1] / "integration/durability/dbos_recovery_probe.py"
_SPEC = importlib.util.spec_from_file_location("dbos_recovery_probe", _PATH)
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def evidence(case="new-new"):
    old, new = _MODULE.CASES[case]
    names = [_MODULE.MODEL_STEP] * 2
    if old == _MODULE.NEW:
        names = [_MODULE.MODEL_STEP, "DBOS.sleep"] * 2
    steps = [
        dict(function_id=i, function_name=n, error=None, output=f"checkpoint-{i}")
        for i, n in enumerate(names, 1)
    ]
    recovery = {"steps": copy.deepcopy(steps)}
    calls = [dict(phase="seed", segment=n) for n in range(3)]
    if case == "old-new":
        recovery.update(
            error_type="DBOSUnexpectedStepError", error="step 2: DBOS.sleep expected"
        )
    else:
        recovery["output"] = "done"
        recovery["steps"].append(
            dict(
                function_id=len(steps) + 1, function_name=_MODULE.MODEL_STEP, error=None
            )
        )
        calls.append(dict(phase="recover", segment=2))
    return dict(
        seed_code=-9,
        recover_code=0,
        versions={
            phase: {"pydantic-ai-slim": v, "pydantic-graph": v, "dbos": "3.3.0"}
            for phase, v in (("seed", old), ("recover", new))
        },
        checkpoint={"steps": steps},
        recovery=recovery,
        requests=calls,
    )


@pytest.mark.parametrize("case", _MODULE.CASES)
def test_expected_recovery_and_incompatibility_are_distinct(case):
    result = _MODULE.evaluate_case(case, **evidence(case))
    assert result["status"] == "PASS"
    assert result["disposition"] == (
        "EXPECTED_HISTORY_INCOMPATIBILITY"
        if case == "old-new"
        else "RECOVERED_SAME_VERSION"
    )


@pytest.mark.parametrize("code", [0, 1, 137])
def test_seed_must_really_die_at_the_process_boundary(code):
    value = evidence()
    value["seed_code"] = code
    with pytest.raises(ValueError, match="SIGKILL"):
        _MODULE.evaluate_case("new-new", **value)


def test_repeated_completed_model_call_is_not_a_pass():
    value = evidence()
    value["requests"].append(dict(phase="recover", segment=0))
    with pytest.raises(ValueError, match="completed model request repeated"):
        _MODULE.evaluate_case("new-new", **value)


def test_new_history_requires_real_sleep_steps():
    value = evidence()
    value["checkpoint"]["steps"] = value["checkpoint"]["steps"][::2]
    with pytest.raises(ValueError, match="seed step sequence"):
        _MODULE.evaluate_case("new-new", **value)


def test_recovery_cannot_rewrite_a_checkpoint():
    value = evidence()
    value["recovery"]["steps"][0]["output"] = "changed"
    with pytest.raises(ValueError, match="checkpoint changed"):
        _MODULE.evaluate_case("new-new", **value)


def test_aliasing_the_two_environments_is_rejected():
    value = evidence("old-new")
    value["versions"]["recover"]["pydantic-ai-slim"] = _MODULE.OLD
    with pytest.raises(ValueError, match="wrong recover Pydantic"):
        _MODULE.evaluate_case("old-new", **value)


@pytest.mark.parametrize("error", ["TimeoutError", "ConnectionError", "ImportError"])
def test_unrelated_failures_are_not_history_incompatibility(error):
    value = evidence("old-new")
    value["recovery"]["error_type"] = error
    with pytest.raises(ValueError, match="expected history incompatibility"):
        _MODULE.evaluate_case("old-new", **value)


def test_unexpected_upgrade_success_is_not_a_pass():
    value = evidence("old-new")
    value["recovery"] = {"steps": value["checkpoint"]["steps"], "output": "done"}
    with pytest.raises(ValueError, match="expected history incompatibility"):
        _MODULE.evaluate_case("old-new", **value)
