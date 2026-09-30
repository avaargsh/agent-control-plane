from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class ResolvedReleasePlan:
    release_name: str
    bundle_name: str
    version: str | None
    bindings: Mapping[str, dict[str, Any]]
    authority_ref: str | None = None
    authority_digest: str | None = None
    policy_refs: tuple[str, ...] = field(default_factory=tuple)
    capability_intent_refs: tuple[str, ...] = field(default_factory=tuple)
    evidence_requirements: Mapping[str, Any] = field(default_factory=dict)
    eval_gates: tuple[str, ...] = field(default_factory=tuple)
    rollout: Mapping[str, Any] = field(default_factory=dict)
    placement: Mapping[str, Any] = field(default_factory=dict)
