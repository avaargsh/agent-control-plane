from __future__ import annotations

from typing import Any

from .plan import ResolvedReleasePlan
from .resolver import resolve_bindings


class ReleaseCompileError(ValueError):
    pass


def compile_release_plan(
    *,
    release: dict[str, Any],
    bundles: list[dict[str, Any]],
    bindings: list[dict[str, Any]],
) -> ResolvedReleasePlan:
    release_name = release.get("metadata", {}).get("name")
    bundle_ref = release.get("spec", {}).get("bundleRef")

    if not release_name:
        raise ReleaseCompileError("release is missing metadata.name")
    if not bundle_ref:
        raise ReleaseCompileError("release is missing spec.bundleRef")

    bundle_by_name = {
        item.get("metadata", {}).get("name"): item
        for item in bundles
        if item.get("metadata", {}).get("name")
    }

    if bundle_ref not in bundle_by_name:
        raise ReleaseCompileError(f"unknown bundleRef: {bundle_ref}")

    spec = release["spec"]
    resolved = resolve_bindings(release, bindings)

    return ResolvedReleasePlan(
        release_name=release_name,
        bundle_name=bundle_ref,
        version=spec.get("version"),
        bindings=resolved,
        policy_refs=tuple(spec.get("policyRefs", [])),
        eval_gates=tuple(spec.get("evalGates", [])),
        rollout=dict(spec.get("rollout", {})),
        placement=dict(spec.get("placement", {})),
    )
