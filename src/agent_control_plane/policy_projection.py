from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class PolicyProjection:
    openshell: Mapping[str, Any]
    opa: Mapping[str, Any]
    network_policy: Mapping[str, Any]
    mcp: Mapping[str, Any]
    credential_leases: tuple[Mapping[str, Any], ...]
    default_deny: bool = True


@dataclass(frozen=True)
class EnforcementDecision:
    allowed: bool
    target: str
    reason: str


def compile_capability_intent(intent: Mapping[str, Any]) -> PolicyProjection:
    spec = intent.get("spec", {})
    filesystem_read: list[str] = []
    filesystem_write: list[str] = []
    egress_hosts: list[str] = []
    kubernetes_verbs: list[str] = []
    kubernetes_resources: list[str] = []
    mcp_capabilities: list[str] = []

    for capability in spec.get("capabilities", []):
        if capability.get("effect", "allow") != "allow":
            continue
        name = capability["name"]
        constraints = capability.get("constraints", {})

        if name == "filesystem.read":
            filesystem_read.extend(constraints.get("paths", []))
        elif name == "filesystem.write":
            filesystem_write.extend(constraints.get("paths", []))
        elif name == "network.egress":
            egress_hosts.extend(constraints.get("hosts", []))
        elif name == "kubernetes.mutate":
            kubernetes_verbs.extend(constraints.get("verbs", []))
            kubernetes_resources.extend(constraints.get("resources", []))
        elif name.startswith("tool."):
            mcp_capabilities.append(name.removeprefix("tool."))

    return PolicyProjection(
        openshell={
            "filesystem": {
                "read": tuple(sorted(set(filesystem_read))),
                "write": tuple(sorted(set(filesystem_write))),
            }
        },
        opa={
            "kubernetes": {
                "verbs": tuple(sorted(set(kubernetes_verbs))),
                "resources": tuple(sorted(set(kubernetes_resources))),
            }
        },
        network_policy={"egressHosts": tuple(sorted(set(egress_hosts)))},
        mcp={"capabilities": tuple(sorted(set(mcp_capabilities)))},
        credential_leases=tuple(
            {"ref": lease["ref"], "ttlSeconds": int(lease["ttlSeconds"])}
            for lease in spec.get("credentialLeases", [])
        ),
        default_deny=bool(spec.get("defaultDeny", True)),
    )


def authorize_request(
    projection: PolicyProjection,
    *,
    target: str,
    operation: str,
    resource: str,
) -> EnforcementDecision:
    if target == "filesystem":
        allowed = resource in projection.openshell["filesystem"].get(operation, ())
    elif target == "network":
        allowed = operation == "egress" and resource in projection.network_policy["egressHosts"]
    elif target == "kubernetes":
        kube = projection.opa["kubernetes"]
        allowed = operation in kube["verbs"] and resource in kube["resources"]
    elif target == "mcp":
        allowed = resource in projection.mcp["capabilities"]
    else:
        allowed = False

    if allowed:
        return EnforcementDecision(True, target, "POLICY_ALLOWED")
    return EnforcementDecision(
        False,
        target,
        "POLICY_DENIED" if projection.default_deny else "NO_MATCH",
    )
