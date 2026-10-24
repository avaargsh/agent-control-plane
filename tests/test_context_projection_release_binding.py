from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from agent_control_plane.compiler import ReleaseCompileError, compile_release_plan
from agent_control_plane.validator import ManifestValidationError, validate_manifest


def bundle():
    return {
        "apiVersion": "agentplane.io/v1alpha1",
        "kind": "AgentBundle",
        "metadata": {"name": "support-agent"},
        "spec": {"entrypoint": "support", "capabilities": ["tickets.read"]},
    }


def release(policy_refs=("support-context",)):
    return {
        "apiVersion": "agentplane.io/v1alpha1",
        "kind": "AgentRelease",
        "metadata": {"name": "support-agent-v1"},
        "spec": {
            "bundleRef": "support-agent",
            "version": "v1",
            "bindings": ["harness"],
            "contextProjectionPolicyRefs": list(policy_refs),
        },
    }


def binding():
    return {
        "apiVersion": "agentplane.io/v1alpha1",
        "kind": "RuntimeBinding",
        "metadata": {"name": "harness"},
        "spec": {"type": "harness", "provider": "test-harness"},
    }


def context_policy(name="support-context"):
    return {
        "apiVersion": "agentplane.io/v1alpha1",
        "kind": "ContextProjectionPolicy",
        "metadata": {"name": name},
        "spec": {
            "sources": [{"ref": "session://summary", "mode": "required"}],
            "limits": {"maxTokens": 2048, "maxBytes": 8192},
            "recordProjectionEvidence": True,
        },
    }


def test_compile_release_plan_resolves_context_projection_policies():
    policy = context_policy()
    plan = compile_release_plan(
        release=release(),
        bundles=[bundle()],
        bindings=[binding()],
        context_projection_policies=[policy],
    )

    assert plan.context_projection_policies == {"support-context": policy}


def test_unknown_context_projection_policy_reference_fails_closed():
    with pytest.raises(
        ReleaseCompileError,
        match="unknown contextProjectionPolicyRef: missing-context",
    ):
        compile_release_plan(
            release=release(("missing-context",)),
            bundles=[bundle()],
            bindings=[binding()],
            context_projection_policies=[],
        )


def test_duplicate_context_projection_policy_names_are_rejected():
    with pytest.raises(
        ReleaseCompileError,
        match="duplicate context projection policy: support-context",
    ):
        compile_release_plan(
            release=release(),
            bundles=[bundle()],
            bindings=[binding()],
            context_projection_policies=[
                context_policy(),
                context_policy(),
            ],
        )


def test_release_schema_rejects_empty_context_projection_policy_ref():
    with pytest.raises(ManifestValidationError):
        validate_manifest(release(("",)))


def test_plan_cli_resolves_context_projection_policy_from_manifest(tmp_path):
    manifest = tmp_path / "release.yaml"
    manifest.write_text(
        yaml.safe_dump_all(
            [bundle(), release(), binding(), context_policy()],
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_control_plane.cli",
            "plan",
            str(manifest),
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    assert plan["context_projection_policies"]["support-context"] == context_policy()
