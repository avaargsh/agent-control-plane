from agent_control_plane.alertmanager_ingress import (
    AlertmanagerIngressError,
    compile_alertmanager_incidents,
)


PAYLOAD = {
    "version": "4",
    "groupKey": '{}:{alertname="GPUXidError", cluster="prod"}',
    "status": "firing",
    "alerts": [
        {
            "status": "firing",
            "labels": {
                "alertname": "GPUXidError",
                "cluster": "prod",
                "instance": "gpu-17",
            },
            "annotations": {"summary": "GPU Xid observed"},
            "fingerprint": "aabbcc001122",
        }
    ],
}


def test_alertmanager_ingress_is_deterministic_on_retry() -> None:
    first = compile_alertmanager_incidents(PAYLOAD, release_ref="sre-v4")
    second = compile_alertmanager_incidents(PAYLOAD, release_ref="sre-v4")

    assert first == second
    assert first[0].release_ref == "sre-v4"
    assert first[0].session_id.startswith("incident-session-")
    assert first[0].run_id.startswith("incident-run-")
    assert first[0].incident_key.endswith(":aabbcc001122")


def test_alert_fingerprint_separates_runs_inside_group() -> None:
    payload = dict(PAYLOAD)
    payload["alerts"] = [
        PAYLOAD["alerts"][0],
        {**PAYLOAD["alerts"][0], "fingerprint": "ddeeff334455"},
    ]

    incidents = compile_alertmanager_incidents(payload, release_ref="sre-v4")

    assert incidents[0].session_id == incidents[1].session_id
    assert incidents[0].run_id != incidents[1].run_id


def test_ingress_fails_closed_without_identity_fields() -> None:
    try:
        compile_alertmanager_incidents(
            {"version": "4", "alerts": PAYLOAD["alerts"]},
            release_ref="sre-v4",
        )
    except AlertmanagerIngressError as exc:
        assert "groupKey" in str(exc)
    else:
        raise AssertionError("missing groupKey must fail closed")
