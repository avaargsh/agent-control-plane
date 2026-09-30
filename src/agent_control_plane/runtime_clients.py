from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Protocol
from urllib import request


@dataclass(frozen=True)
class RuntimeApplyResult:
    resource_ref: str
    changed: bool
    evidence: Mapping[str, Any]


class KubernetesRuntimeClient(Protocol):
    def ensure_sandbox(self, desired: Mapping[str, Any]) -> RuntimeApplyResult:
        ...

    def delete_sandbox(self, resource_ref: str) -> Mapping[str, Any]:
        ...

    def restore_sandbox(
        self,
        previous: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        ...


class TemporalRuntimeClient(Protocol):
    def ensure_workflow(self, desired: Mapping[str, Any]) -> RuntimeApplyResult:
        ...

    def terminate_workflow(self, resource_ref: str) -> Mapping[str, Any]:
        ...


@dataclass(frozen=True)
class DecisionGatewayClient:
    endpoint: str
    timeout_seconds: float = 5.0

    def decide(
        self,
        *,
        decision_type: str,
        candidates: list[str],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        payload = json.dumps(
            {
                "decision_type": decision_type,
                "candidates": candidates,
                "context": context,
            }
        ).encode("utf-8")

        req = request.Request(
            self.endpoint.rstrip("/") + "/decision",
            data=payload,
            headers={"content-type": "application/json"},
            method="POST",
        )

        with request.urlopen(req, timeout=self.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
