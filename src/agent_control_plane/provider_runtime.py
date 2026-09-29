from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ExternalResource:
    resource_ref: str
    changed: bool
    external_refs: dict[str, str]
    evidence: dict[str, Any]


class TemporalTransport(Protocol):
    def start_workflow(
        self,
        *,
        workflow_type: str,
        workflow_id: str,
        task_queue: str,
        input: dict[str, Any],
    ) -> ExternalResource: ...

    def terminate_workflow(
        self,
        *,
        workflow_id: str,
        reason: str,
    ) -> dict[str, Any]: ...
