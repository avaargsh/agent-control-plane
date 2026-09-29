from __future__ import annotations

from typing import Any, Mapping


def normalize_recovery_evidence(
    evidence: Mapping[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, float]]:
    if evidence is None:
        return None, {}

    if "success" not in evidence:
        raise ValueError("recovery evidence requires success")
    if "identityPreserved" not in evidence:
        raise ValueError("recovery evidence requires identityPreserved")

    success = evidence["success"]
    identity = evidence["identityPreserved"]
    if not isinstance(success, bool) or not isinstance(identity, bool):
        raise ValueError("recovery evidence flags must be boolean")

    normalized = {
        "success": success,
        "identityPreserved": identity,
        "sourceSandboxRef": evidence.get("sourceSandboxRef"),
        "targetSandboxRef": evidence.get("targetSandboxRef"),
        "snapshotRef": evidence.get("snapshotRef"),
    }
    metrics = {
        "recovery_success": 1.0 if success else 0.0,
        "recovery_identity_preserved": 1.0 if identity else 0.0,
    }
    return normalized, metrics
