from agent_control_plane.evidence_provenance import AppendOnlyEvidenceStore


def event(event_id, event_type):
    return {
        "metadata": {"id": event_id},
        "spec": {
            "runRef": "run-001",
            "eventType": event_type,
            "provenance": {
                "generated_by": "tool-proxy",
                "observed_by": "sandbox-supervisor",
                "authorized_by": "opa",
                "executed_by": "mcp-gateway",
                "committed_by": "target-service",
            },
        },
    }


store = AppendOnlyEvidenceStore()
store.append(event("evt-1", "tool.request"))
store.append(event("evt-2", "tool.commit"))
harness_trace = ["tool.request", "tool.commit"]
harness_trace.clear()
store.verify()

print("harness_trace_events", len(harness_trace))
print("external_evidence_events", len(store.records))
print("head_digest", store.records[-1].digest)
