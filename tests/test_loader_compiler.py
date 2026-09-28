from pathlib import Path

import pytest

from agent_control_plane.compiler import ReleaseCompileError, compile_release_plan
from agent_control_plane.loader import load_yaml_documents
from agent_control_plane.state_machine import ReleasePhase, ReleaseState


def test_load_multi_document_yaml(tmp_path: Path) -> None:
    path = tmp_path / "manifests.yaml"
    path.write_text(
        """---
apiVersion: agentplane.io/v1alpha1
kind: RuntimeBinding
metadata:
  name: harness
spec:
  type: harness
  provider: demo
---
apiVersion: agentplane.io/v1alpha1
kind: RuntimeBinding
metadata:
  name: workflow
spec:
  type: workflow
  provider: temporal
""",
        encoding="utf-8",
    )

    loaded = load_yaml_documents(path)
    assert len(loaded.by_kind("RuntimeBinding")) == 2


def test_compile_release_plan() -> None:
    bundle = {
        "metadata": {"name": "agent-a"},
    }
    release = {
        "metadata": {"name": "agent-a-v1"},
        "spec": {
            "bundleRef": "agent-a",
            "version": "v1",
            "bindings": ["harness"],
            "policyRefs": ["readonly"],
            "evalGates": ["smoke"],
        },
    }
    bindings = [
        {
            "metadata": {"name": "harness"},
            "spec": {"type": "harness", "provider": "demo"},
        }
    ]

    plan = compile_release_plan(
        release=release,
        bundles=[bundle],
        bindings=bindings,
    )

    assert plan.release_name == "agent-a-v1"
    assert plan.bundle_name == "agent-a"
    assert "harness" in plan.bindings


def test_unknown_bundle_fails() -> None:
    with pytest.raises(ReleaseCompileError):
        compile_release_plan(
            release={
                "metadata": {"name": "x"},
                "spec": {"bundleRef": "missing", "bindings": []},
            },
            bundles=[],
            bindings=[],
        )


def test_release_state_machine() -> None:
    state = ReleaseState("agent-a-v1")
    state.transition(ReleasePhase.VALIDATED)
    state.transition(ReleasePhase.RESOLVED)
    state.transition(ReleasePhase.DEPLOYING)
    state.transition(ReleasePhase.EVALUATING)
    state.transition(ReleasePhase.PROMOTED)

    assert state.phase == ReleasePhase.PROMOTED


def test_invalid_release_transition() -> None:
    state = ReleaseState("agent-a-v1")
    with pytest.raises(ValueError):
        state.transition(ReleasePhase.PROMOTED)
