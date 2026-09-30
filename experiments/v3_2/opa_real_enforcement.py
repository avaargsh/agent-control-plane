from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path

from agent_control_plane.opa_provider import compile_opa_bundle


INTENT = {
    "spec": {
        "defaultDeny": True,
        "capabilities": [
            {
                "name": "filesystem.read",
                "constraints": {
                    "paths": ["/var/log/app.log"],
                },
            },
            {
                "name": "network.egress",
                "constraints": {
                    "hosts": ["api.internal.example"],
                },
            },
            {
                "name": "kubernetes.mutate",
                "constraints": {
                    "verbs": ["patch"],
                    "resources": ["deployments"],
                },
            },
            {
                "name": "tool.observability.query",
            },
        ],
    }
}

CASES = {
    "allow-filesystem.json": {
        "capability": "filesystem.read",
        "operation": "read",
        "resource": "/var/log/app.log",
    },
    "deny-aws-credentials.json": {
        "capability": "filesystem.read",
        "operation": "read",
        "resource": "~/.aws/credentials",
    },
    "deny-evil-egress.json": {
        "capability": "network.egress",
        "operation": "egress",
        "resource": "evil.example",
    },
    "deny-kubernetes-delete.json": {
        "capability": "kubernetes.mutate",
        "operation": "delete",
        "resource": "deployments",
    },
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    policy = compile_opa_bundle(INTENT)["policy.rego"]
    policy_path = args.output / "policy.rego"
    policy_path.write_text(policy, encoding="utf-8")

    for filename, payload in CASES.items():
        (args.output / filename).write_text(
            json.dumps(
                payload,
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    digest = "sha256:" + sha256(
        policy.encode("utf-8")
    ).hexdigest()
    print(f"policy_digest={digest}")
    print(f"policy_path={policy_path}")


if __name__ == "__main__":
    main()
