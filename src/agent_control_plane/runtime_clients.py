from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib import request


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
