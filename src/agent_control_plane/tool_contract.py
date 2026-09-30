from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping


class RetryableToolError(RuntimeError):
    pass


class ToolContractViolation(RuntimeError):
    pass


@dataclass(frozen=True)
class ToolExecutionResult:
    idempotency_key: str
    attempts: int
    verified_after_error: bool
    result: Any


def execute_with_contract(
    *,
    run_id: str,
    action_id: str,
    contract: Mapping[str, Any],
    invoke: Callable[[str], Any],
    verify: Callable[[str], Any | None],
) -> ToolExecutionResult:
    spec = contract["spec"]
    idempotency = spec["idempotency"]
    retry = spec["retry"]
    verification = spec["verification"]

    if (
        spec["effect"] != "read-only"
        and idempotency["mode"] == "none"
        and retry["maxAttempts"] > 1
    ):
        raise ToolContractViolation(
            "side-effecting retries require idempotency support"
        )

    template = idempotency.get("keyTemplate", "{run_id}:{action_id}")
    key = template.format(run_id=run_id, action_id=action_id)
    max_attempts = int(retry["maxAttempts"])

    for attempt in range(1, max_attempts + 1):
        try:
            result = invoke(key)
            return ToolExecutionResult(
                idempotency_key=key,
                attempts=attempt,
                verified_after_error=False,
                result=result,
            )
        except RetryableToolError:
            if verification["mode"] != "none":
                observed = verify(key)
                if observed is not None:
                    return ToolExecutionResult(
                        idempotency_key=key,
                        attempts=attempt,
                        verified_after_error=True,
                        result=observed,
                    )
            if attempt == max_attempts:
                raise

    raise AssertionError("unreachable")
