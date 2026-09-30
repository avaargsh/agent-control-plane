from __future__ import annotations

from typing import Any

from .plan import ResolvedReleasePlan
from .resolver import resolve_bindings


class ReleaseCompileError(ValueError):
    pass


def _documents_by_name(
    documents: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    return {
        item.get("metadata", {}).get("name"): item
        for item in documents
        if item.get("metadata", {}).get("name")
    }


def compile_release_plan(
    *,
    release: dict[str, Any],
    bundles: list[dict[str, Any]],
    bindings: list[dict[str, Any]],
    capability_intents: list[dict[str, Any]] | None = None,
    tool_contracts: list[dict[str, Any]] | None = None,
) -> ResolvedReleasePlan:
    release_name = release.get("metadata", {}).get("name")
    bundle_ref = release.get("spec", {}).get("bundleRef")

    if not release_name:
        raise ReleaseCompileError("release is missing metadata.name")
    if not bundle_ref:
        raise ReleaseCompileError("release is missing spec.bundleRef")

    bundle_by_name = _documents_by_name(bundles)
    if bundle_ref not in bundle_by_name:
        raise ReleaseCompileError(f"unknown bundleRef: {bundle_ref}")

    spec = release["spec"]
    resolved = resolve_bindings(release, bindings)
    capability_refs = tuple(spec.get("capabilityIntentRefs", []))

    if capability_intents is not None:
        available_capabilities = _documents_by_name(capability_intents)
        missing = [
            ref for ref in capability_refs
            if ref not in available_capabilities
        ]
        if missing:
            raise ReleaseCompileError(
                "unknown capabilityIntentRef: " + ", ".join(missing)
            )

    available_contracts = (
        _documents_by_name(tool_contracts)
        if tool_contracts is not None
        else None
    )
    for name, binding in resolved.items():
        binding_spec = binding.get("spec", {})
        contract_ref = binding_spec.get("toolContractRef")
        if contract_ref is None:
            continue
        if binding_spec.get("type") != "tool":
            raise ReleaseCompileError(
                f"binding {name} uses toolContractRef but is not type=tool"
            )
        if (
            available_contracts is not None
            and contract_ref not in available_contracts
        ):
            raise ReleaseCompileError(
                f"binding {name} references unknown ToolContract: "
                f"{contract_ref}"
            )

    return ResolvedReleasePlan(
        release_name=release_name,
        bundle_name=bundle_ref,
        version=spec.get("version"),
        bindings=resolved,
        authority_ref=spec.get("authorityRef"),
        authority_digest=spec.get("authorityDigest"),
        policy_refs=tuple(spec.get("policyRefs", [])),
        capability_intent_refs=capability_refs,
        evidence_requirements=dict(
            spec.get("evidenceRequirements", {})
        ),
        eval_gates=tuple(spec.get("evalGates", [])),
        rollout=dict(spec.get("rollout", {})),
        placement=dict(spec.get("placement", {})),
    )
