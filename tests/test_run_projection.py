from agent_control_plane.provider_runtime import ExternalResource
from agent_control_plane.run_projection import project_temporal_start


RUN = {
    "apiVersion": "agentplane.io/v1alpha1",
    "kind": "Run",
    "metadata": {"id": "incident-run-abc"},
    "spec": {
        "sessionRef": "incident-session-def",
        "releaseRef": "sre-v4",
        "status": "created",
        "providerRefs": {},
        "artifactRefs": [],
        "evidenceRefs": [],
        "workflowRef": "temporal:incident-run-abc",
    },
}


def temporal_resource() -> ExternalResource:
    return ExternalResource(
        resource_ref="temporal://incident-run-abc/exec-77",
        changed=True,
        external_refs={
            "temporal.workflow_id": "incident-run-abc",
            "temporal.run_id": "exec-77",
        },
        evidence={"state": "started"},
    )


def test_temporal_start_preserves_canonical_identity() -> None:
    projected, evidence = project_temporal_start(
        RUN,
        temporal_resource(),
        trace_id="0123456789abcdef",
    )

    assert projected["metadata"]["id"] == "incident-run-abc"
    assert projected["spec"]["sessionRef"] == "incident-session-def"
    assert projected["spec"]["releaseRef"] == "sre-v4"
    assert projected["spec"]["status"] == "running"
    assert projected["spec"]["providerRefs"]["temporal.run_id"] == "exec-77"
    assert projected["spec"]["providerRefs"]["temporal.workflow_id"] == (
        "incident-run-abc"
    )
    assert RUN["spec"]["providerRefs"] == {}


def test_temporal_execution_id_is_external_evidence_only() -> None:
    projected, evidence = project_temporal_start(RUN, temporal_resource())

    provenance = evidence["spec"]["provenance"]
    assert provenance["agentplane.run.id"] == "incident-run-abc"
    assert provenance["agentplane.session.id"] == "incident-session-def"
    assert provenance[
        "agentplane.external_ref.temporal.run_id"
    ] == "exec-77"
    assert evidence["metadata"]["id"] in projected["spec"]["evidenceRefs"]
    assert projected["metadata"]["id"] != "exec-77"


def test_start_projection_is_evidence_idempotent() -> None:
    first, evidence_a = project_temporal_start(RUN, temporal_resource())
    second, evidence_b = project_temporal_start(first, temporal_resource())

    assert evidence_a["metadata"]["id"] == evidence_b["metadata"]["id"]
    assert second["spec"]["evidenceRefs"] == [evidence_a["metadata"]["id"]]


def test_projection_fails_closed_without_canonical_identity() -> None:
    broken = {
        **RUN,
        "metadata": {"id": ""},
    }

    try:
        project_temporal_start(broken, temporal_resource())
    except ValueError as exc:
        assert "canonical id" in str(exc)
    else:
        raise AssertionError("missing canonical identity must fail closed")
