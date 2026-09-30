from agent_control_plane.evidence_provenance import AppendOnlyEvidenceStore


def event(event_id: str, event_type: str):
    return {
        "apiVersion": "agentplane.io/v1alpha1",
        "kind": "EvidenceEvent",
        "metadata": {"id": event_id},
        "spec": {
            "runRef": "run-001",
            "eventType": event_type,
            "occurredAt": "2026-09-30T06:00:00Z",
            "provenance": {
                "generated_by": "tool-proxy",
                "observed_by": "sandbox-supervisor",
                "authorized_by": "opa",
                "executed_by": "mcp-gateway",
                "committed_by": "target-service",
            },
        },
    }


def test_external_append_only_evidence_survives_harness_trace_tampering() -> None:
    store = AppendOnlyEvidenceStore()
    first = store.append(event("evt-1", "tool.request"))
    store.append(event("evt-2", "tool.commit"))

    local_harness_trace = [{"event": "tool.request"}, {"event": "tool.commit"}]
    local_harness_trace.clear()

    assert local_harness_trace == []
    assert len(store.records) == 2
    assert store.records[1].previous_digest == first.digest
    store.verify()
