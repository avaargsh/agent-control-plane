from __future__ import annotations

import json
from typing import Any, Mapping


class OPACompileError(ValueError):
    pass


_HEADER = """package agentos.policy

import rego.v1

# Generated from CapabilityIntent. Runtime enforcement remains provider-owned.
default allow := false
"""


def _literal(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    )


def _allow_rule(expressions: list[str]) -> str:
    body = "\n".join(
        f"    {expression}"
        for expression in expressions
    )
    return f"allow if {{\n{body}\n}}"


def _constraint_list(
    constraints: Mapping[str, Any],
    name: str,
) -> list[str]:
    value = constraints.get(name, [])
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item
        for item in value
    ):
        raise OPACompileError(
            f"constraint {name} must be a list of non-empty strings"
        )
    return value


def compile_opa_bundle(intent: Mapping[str, Any]) -> dict[str, str]:
    """Compile CapabilityIntent into deterministic Rego v1 policy.

    Multiple capabilities become multiple definitions of allow so they retain
    Rego logical-OR semantics. Expressions within one capability rule are
    logical AND and therefore encode that capability constraints.
    """
    spec = intent.get("spec", {})
    if spec.get("defaultDeny", True) is not True:
        raise OPACompileError(
            "OPA enforcement requires CapabilityIntent defaultDeny=true"
        )

    capabilities = spec.get("capabilities", [])
    denied_names = {
        capability.get("name")
        for capability in capabilities
        if capability.get("effect", "allow") == "deny"
    }

    rules: list[str] = []
    for capability in capabilities:
        if capability.get("effect", "allow") != "allow":
            continue

        name = capability.get("name")
        if not isinstance(name, str) or not name:
            raise OPACompileError("capability name is required")
        if name in denied_names:
            continue

        constraints = capability.get("constraints", {})
        if not isinstance(constraints, Mapping):
            raise OPACompileError(
                f"constraints for {name} must be an object"
            )

        expressions = [
            f"input.capability == {_literal(name)}"
        ]

        if name in {"filesystem.read", "filesystem.write"}:
            paths = _constraint_list(constraints, "paths")
            expressions.append(
                f"input.resource in {_literal(paths)}"
            )
        elif name == "network.egress":
            hosts = _constraint_list(constraints, "hosts")
            expressions.append(
                f"input.resource in {_literal(hosts)}"
            )
        elif name == "kubernetes.mutate":
            verbs = _constraint_list(constraints, "verbs")
            resources = _constraint_list(
                constraints,
                "resources",
            )
            expressions.extend(
                [
                    f"input.operation in {_literal(verbs)}",
                    f"input.resource in {_literal(resources)}",
                ]
            )
        elif constraints:
            raise OPACompileError(
                f"unsupported constraints for capability: {name}"
            )

        rules.append(_allow_rule(expressions))

    policy = _HEADER
    if rules:
        policy += "\n\n" + "\n\n".join(rules)
    else:
        policy += "\n"

    return {"policy.rego": policy}
