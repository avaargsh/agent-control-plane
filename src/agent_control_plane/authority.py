from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping


_APPROVAL_STRENGTH = {
    "none": 0,
    "policy": 1,
    "human-exact": 2,
}


@dataclass(frozen=True, order=True)
class AuthorityGrant:
    effect: str
    capability: str
    resource: str
    verb: str


@dataclass(frozen=True)
class AuthorityDiff:
    added_grants: tuple[AuthorityGrant, ...] = ()
    removed_grants: tuple[AuthorityGrant, ...] = ()
    added_runtime_refs: tuple[str, ...] = ()
    removed_runtime_refs: tuple[str, ...] = ()
    approval_mode_before: str | None = None
    approval_mode_after: str | None = None
    evidence_required_before: bool | None = None
    evidence_required_after: bool | None = None
    max_operations_before: int | None = None
    max_operations_after: int | None = None

    @property
    def changed(self) -> bool:
        return any(
            (
                self.added_grants,
                self.removed_grants,
                self.added_runtime_refs,
                self.removed_runtime_refs,
                self.approval_mode_before != self.approval_mode_after,
                self.evidence_required_before != self.evidence_required_after,
                self.max_operations_before != self.max_operations_after,
            )
        )


@dataclass(frozen=True)
class AuthorityAdmissionDecision:
    decision: str
    admitted: bool
    baseline_digest: str | None
    proposed_digest: str
    reasons: tuple[str, ...]
    diff: AuthorityDiff


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def authority_digest(envelope: Mapping[str, Any]) -> str:
    """Digest only authority semantics, not release/version metadata."""
    spec = envelope.get("spec", {})
    authority_view = {
        "fleetRef": spec.get("fleetRef"),
        "teamRef": spec.get("teamRef"),
        "agentRef": spec.get("agentRef"),
        "runtimeRefs": sorted(spec.get("runtimeRefs", ())),
        "grants": sorted(
            (
                {
                    "effect": grant.get("effect"),
                    "capability": grant.get("capability"),
                    "resource": grant.get("resource"),
                    "verbs": sorted(grant.get("verbs", ())),
                }
                for grant in spec.get("grants", ())
            ),
            key=lambda item: (
                str(item["effect"]),
                str(item["capability"]),
                str(item["resource"]),
                tuple(str(value) for value in item["verbs"]),
            ),
        ),
        "constraints": spec.get("constraints", {}),
    }
    return "sha256:" + hashlib.sha256(_canonical_json(authority_view)).hexdigest()


def _grants(envelope: Mapping[str, Any]) -> set[AuthorityGrant]:
    result: set[AuthorityGrant] = set()
    for grant in envelope.get("spec", {}).get("grants", ()):
        effect = str(grant.get("effect", ""))
        capability = str(grant.get("capability", ""))
        resource = str(grant.get("resource", ""))
        for verb in grant.get("verbs", ()):
            result.add(
                AuthorityGrant(
                    effect=effect,
                    capability=capability,
                    resource=resource,
                    verb=str(verb),
                )
            )
    return result


def diff_authority(
    baseline: Mapping[str, Any],
    proposed: Mapping[str, Any],
) -> AuthorityDiff:
    before_spec = baseline.get("spec", {})
    after_spec = proposed.get("spec", {})

    before_grants = _grants(baseline)
    after_grants = _grants(proposed)
    before_runtime = set(before_spec.get("runtimeRefs", ()))
    after_runtime = set(after_spec.get("runtimeRefs", ()))
    before_constraints = before_spec.get("constraints", {})
    after_constraints = after_spec.get("constraints", {})

    return AuthorityDiff(
        added_grants=tuple(sorted(after_grants - before_grants)),
        removed_grants=tuple(sorted(before_grants - after_grants)),
        added_runtime_refs=tuple(sorted(after_runtime - before_runtime)),
        removed_runtime_refs=tuple(sorted(before_runtime - after_runtime)),
        approval_mode_before=before_constraints.get("approvalMode"),
        approval_mode_after=after_constraints.get("approvalMode"),
        evidence_required_before=before_constraints.get("evidenceRequired"),
        evidence_required_after=after_constraints.get("evidenceRequired"),
        max_operations_before=before_constraints.get("maxOperationsPerRun"),
        max_operations_after=after_constraints.get("maxOperationsPerRun"),
    )


def admit_authority_change(
    baseline: Mapping[str, Any] | None,
    proposed: Mapping[str, Any],
) -> AuthorityAdmissionDecision:
    """Fail closed on authority expansion or enforcement-boundary drift."""
    proposed_digest = authority_digest(proposed)
    if baseline is None:
        return AuthorityAdmissionDecision(
            decision="ADMIT",
            admitted=True,
            baseline_digest=None,
            proposed_digest=proposed_digest,
            reasons=("INITIAL_AUTHORITY",),
            diff=AuthorityDiff(),
        )

    baseline_digest = authority_digest(baseline)
    before_spec = baseline.get("spec", {})
    after_spec = proposed.get("spec", {})
    drift = diff_authority(baseline, proposed)
    reasons: list[str] = []

    for identity_field, reason in (
        ("fleetRef", "FLEET_IDENTITY_CHANGED"),
        ("teamRef", "TEAM_OWNERSHIP_CHANGED"),
        ("agentRef", "AGENT_IDENTITY_CHANGED"),
    ):
        if before_spec.get(identity_field) != after_spec.get(identity_field):
            reasons.append(reason)

    if drift.added_grants:
        reasons.append("AUTHORITY_EXPANSION")

    if drift.added_runtime_refs or drift.removed_runtime_refs:
        reasons.append("RUNTIME_BOUNDARY_CHANGED")

    before_mode = drift.approval_mode_before
    after_mode = drift.approval_mode_after
    if (
        before_mode in _APPROVAL_STRENGTH
        and after_mode in _APPROVAL_STRENGTH
        and _APPROVAL_STRENGTH[after_mode] < _APPROVAL_STRENGTH[before_mode]
    ):
        reasons.append("APPROVAL_WEAKENED")

    if drift.evidence_required_before is True and drift.evidence_required_after is False:
        reasons.append("EVIDENCE_REQUIREMENT_WEAKENED")

    before_max = drift.max_operations_before
    after_max = drift.max_operations_after
    if (
        isinstance(before_max, int)
        and isinstance(after_max, int)
        and after_max > before_max
    ):
        reasons.append("WRITE_BUDGET_EXPANDED")

    if reasons:
        return AuthorityAdmissionDecision(
            decision="DENY",
            admitted=False,
            baseline_digest=baseline_digest,
            proposed_digest=proposed_digest,
            reasons=tuple(reasons),
            diff=drift,
        )

    return AuthorityAdmissionDecision(
        decision="ADMIT",
        admitted=True,
        baseline_digest=baseline_digest,
        proposed_digest=proposed_digest,
        reasons=("NO_AUTHORITY_EXPANSION",) if drift.changed else ("NO_DRIFT",),
        diff=drift,
    )


def build_authority_inventory(
    envelopes: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build a deterministic Fleet -> Agent -> deployment authority inventory."""
    fleet_map: dict[str, dict[str, list[dict[str, Any]]]] = {}

    for envelope in envelopes:
        spec = envelope.get("spec", {})
        metadata = envelope.get("metadata", {})
        fleet_ref = str(spec.get("fleetRef", ""))
        agent_ref = str(spec.get("agentRef", ""))
        if not fleet_ref or not agent_ref:
            raise ValueError("authority envelope requires fleetRef and agentRef")

        fleet_map.setdefault(fleet_ref, {}).setdefault(agent_ref, []).append(
            {
                "releaseRef": spec.get("releaseRef"),
                "teamRef": spec.get("teamRef"),
                "envelopeRef": metadata.get("name"),
                "authorityDigest": authority_digest(envelope),
                "runtimeRefs": sorted(spec.get("runtimeRefs", ())),
                "grantCount": len(_grants(envelope)),
            }
        )

    fleets = []
    total_agents = 0
    total_deployments = 0
    for fleet_ref in sorted(fleet_map):
        agents = []
        for agent_ref in sorted(fleet_map[fleet_ref]):
            deployments = sorted(
                fleet_map[fleet_ref][agent_ref],
                key=lambda item: (
                    item.get("releaseRef") or "",
                    item.get("envelopeRef") or "",
                ),
            )
            agents.append(
                {
                    "agentRef": agent_ref,
                    "deploymentCount": len(deployments),
                    "deployments": deployments,
                }
            )
            total_deployments += len(deployments)

        total_agents += len(agents)
        fleets.append(
            {
                "fleetRef": fleet_ref,
                "agentCount": len(agents),
                "agents": agents,
            }
        )

    return {
        "fleetCount": len(fleets),
        "agentCount": total_agents,
        "deploymentCount": total_deployments,
        "fleets": fleets,
    }


def decision_asdict(decision: AuthorityAdmissionDecision) -> dict[str, Any]:
    return asdict(decision)
