import pytest

from agent_control_plane import (
    BindingResolutionError,
    ManifestValidationError,
    resolve_bindings,
    validate_manifest,
)


def test_validate_runtime_binding() -> None:
    validate_manifest(
        {
            "apiVersion": "agentplane.io/v1alpha1",
            "kind": "RuntimeBinding",
            "metadata": {"name": "sandbox-k8s"},
            "spec": {"type": "sandbox", "provider": "k8s-agent-sandbox"},
        }
    )


def test_reject_unknown_kind() -> None:
    with pytest.raises(ManifestValidationError):
        validate_manifest({"kind": "Unknown"})


def test_resolve_release_bindings() -> None:
    release = {
        "spec": {
            "bindings": ["harness-openai", "workflow-temporal"],
        }
    }
    bindings = [
        {
            "metadata": {"name": "harness-openai"},
            "spec": {"type": "harness", "provider": "openai-agents"},
        },
        {
            "metadata": {"name": "workflow-temporal"},
            "spec": {"type": "workflow", "provider": "temporal"},
        },
    ]

    resolved = resolve_bindings(release, bindings)
    assert set(resolved) == {"harness-openai", "workflow-temporal"}


def test_missing_binding_fails() -> None:
    with pytest.raises(BindingResolutionError):
        resolve_bindings({"spec": {"bindings": ["missing"]}}, [])



def test_validate_run_canonical_cross_runtime_identity() -> None:
    validate_manifest(
        {
            "apiVersion": "agentplane.io/v1alpha1",
            "kind": "Run",
            "metadata": {"id": "golden-checkout-live-001"},
            "spec": {
                "sessionRef": "session://golden-checkout-live-001",
                "releaseRef": "release://sre-rca-agent-v1",
                "workflowRef": "temporal://golden-checkout-live-001",
                "sandboxRef": "sandbox://golden-checkout-live-001",
                "status": "paused",
                "evidenceRefs": [
                    "evidence://aiops/metrics-before",
                    "evidence://aiops/decision-001",
                ],
            },
        }
    )


def test_run_requires_release_identity() -> None:
    with pytest.raises(ManifestValidationError):
        validate_manifest(
            {
                "apiVersion": "agentplane.io/v1alpha1",
                "kind": "Run",
                "metadata": {"id": "run-1"},
                "spec": {
                    "sessionRef": "session://run-1",
                    "status": "running",
                },
            }
        )
