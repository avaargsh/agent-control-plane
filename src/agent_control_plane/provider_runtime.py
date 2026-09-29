from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ExternalResource:
    resource_ref: str
    changed: bool
    external_refs: dict[str, str]
    evidence: dict[str, Any]


class SandboxTransport(Protocol):
    def ensure_sandbox(
        self,
        *,
        claim_name: str,
        namespace: str,
        warm_pool: str,
        ttl_seconds: int | None,
        labels: dict[str, str],
    ) -> ExternalResource: ...

    def delete_sandbox(
        self,
        *,
        claim_name: str,
        namespace: str,
    ) -> dict[str, Any]: ...
