from __future__ import annotations

from typing import Any, Mapping


class OPACompileError(ValueError):
    pass


_POLICY_TEMPLATE = """package agentos.policy

# Generated from CapabilityIntent. Default deny is intentional.
default allow := false

allow {
{rules}
}
"""


def compile_opa_bundle(intent: Mapping[str, Any]) -> dict[str, str]:
    """Compile CapabilityIntent into a small deterministic OPA bundle.

    This is deliberately not an authorization engine. It is a projection
    layer: Runtime providers decide how and where to enforce the generated
    policy.
    """
    rules: list[str] = []

    for capability in intent.get("spec", {}).get("capabilities", []):
        if capability.get("effect", "allow") != "allow":
            continue

        name = capability.get("name")
        if not name:
            raise OPACompileError("capability name is required")

        rules.append(
            "    input.capability == %r" % name
        )

    return {
        "policy.rego": _POLICY_TEMPLATE.format(
            rules="\n".join(rules) if rules else "    false"
        )
    }
