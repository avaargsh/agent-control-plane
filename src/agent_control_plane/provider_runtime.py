from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ExternalResource:
    """Provider-owned resource returned through the control-plane boundary."""

    resource_ref: str
    changed: bool
    external_refs: dict[str, str]
    evidence: dict[str, Any]


class TemporalTransport(Protocol):
    """Small transport seam; Temporal SDK integration lives outside core reconciliation."""

    def start_workflow(
        self,
        *,
        workflow_type: str,
        workflow_id: str,
        task_queue: str,
        input: dict[str, Any],
    ) -> ExternalResource:
        ...

    def terminate_workflow(
        self,
        *,
        workflow_id: str,
        reason: str,
    ) -> dict[str, Any]:
        ...


class HarnessTransport(Protocol):
    """Harness seam for OpenAI Agents/Codex implementations."""

    def ensure_release(
        self,
        *,
        release_name: str,
        manifest_ref: str | None,
        capabilities: list[str],
        config: dict[str, Any],
    ) -> ExternalResource:
        ...

    def release(
        self,
        *,
        resource_ref: str,
        reason: str,
    ) -> dict[str, Any]:
        ...
