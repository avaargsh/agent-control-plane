from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ExternalResource:
    resource_ref: str
    changed: bool
    external_refs: dict[str, str]
    evidence: dict[str, Any]
