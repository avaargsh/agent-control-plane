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
    compensated: bool = False
    compensation_result: Any | None = None


def execute_with_contract(
    *,
    run_id: str,
    action_id: str,
    contract: Mapping[str, Any],
    invoke: Callable[[str], Any],
    verify: Callable[[str], Any | None],
    validate_result: Callable[[Any], bool] | None = None,
    compensate: Callable[[str, Any], Any] | None = None,
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

    def finish(
        result: Any,
        *,
        attempt: int,
        verified_after_error: bool,
    ) -> ToolExecutionResult:
        if validate_result is None or validate_result(result):
            return ToolExecutionResult(
                idempotency_key=key,
                attempts=attempt,
                verified_after_error=verified_after_error,
                result=result,
            )

        compensation = spec.get("compensation")
        if compensation is None:
            raise ToolContractViolation(
                "post-condition failed and ToolContract has no compensation"
            )
        if compensate is None:
            raise ToolContractViolation(
                "post-condition failed but no compensation executor was supplied"
            )

        compensation_result = compensate(key, result)
        return ToolExecutionResult(
            idempotency_key=key,
            attempts=attempt,
            verified_after_error=verified_after_error,
            result=result,
            compensated=True,
            compensation_result=compensation_result,
        )

    for attempt in range(1, max_attempts + 1):
        try:
            result = invoke(key)
        except RetryableToolError:
            if verification["mode"] != "none":
                observed = verify(key)
                if observed is not None:
                    return finish(
                        observed,
                        attempt=attempt,
                        verified_after_error=True,
                    )
            if attempt == max_attempts:
                raise
            continue

        return finish(
            result,
            attempt=attempt,
            verified_after_error=False,
        )

    raise AssertionError("unreachable")
