"""Local API negative tests: verified identity, ownership, CAS and SSE cursor."""
from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))
from pr_watch_harness import GoalStore, PullSnapshot
from pr_watch_service import LocalTokens, PilotConfigError, PilotHTTPServer, PilotService

TOKENS = {"alice": "a" * 40, "bob": "b" * 40}
SHA = "c" * 40
NOW = datetime(2026, 10, 18, 11, tzinfo=timezone.utc)


@contextmanager
def server(tmp_path):
    api = PilotHTTPServer(
        ("127.0.0.1", 0),
        PilotService(tmp_path / "pilot.db", LocalTokens(TOKENS)),
    )
    thread = Thread(target=api.serve_forever, daemon=True)
    thread.start()
    try:
        yield api
    finally:
        api.shutdown()
        thread.join(timeout=3)
        api.server_close()


def request(srv, method, path, *, who=None, body=None, headers=None):
    header = dict(headers or {})
    if who is not None:
        header["Authorization"] = "Bearer " + TOKENS[who]
    if body is not None:
        header["Content-Type"] = "application/json"
        body = json.dumps(body).encode("utf-8")
    req = Request(
        "http://127.0.0.1:" + str(srv.server_port) + path,
        data=body, method=method, headers=header,
    )
    try:
        with urlopen(req, timeout=3) as response:
            return response.status, response.read().decode("utf-8"), dict(response.headers)
    except HTTPError as exc:
        return exc.code, exc.read().decode("utf-8"), dict(exc.headers)


def create(srv, who="alice", goal_id="pr-1"):
    status, data, _ = request(srv, "POST", "/api/goals", who=who,
        body={"goal_id": goal_id, "repo": "avaargsh/agent-control-plane", "number": 169})
    assert status == 200, data
    return json.loads(data)["goal"]


def test_local_token_configuration_fail_closed():
    with pytest.raises(PilotConfigError):
        LocalTokens({})
    with pytest.raises(PilotConfigError):
        LocalTokens({"alice": "short"})
    with pytest.raises(PilotConfigError):
        LocalTokens({"alice": "a" * 40, "bob": "a" * 40})
    with pytest.raises(PilotConfigError):
        PilotHTTPServer(("0.0.0.0", 0), PilotService(":memory:", LocalTokens(TOKENS)))
    auth = LocalTokens(TOKENS)
    assert auth.resolve(["Bearer " + TOKENS["alice"]]) == "alice"
    assert auth.resolve(["Bearer " + TOKENS["alice"], "Bearer " + TOKENS["bob"]]) is None
    assert auth.resolve(["Bearer fake"]) is None


def test_unauthenticated_and_wrong_token_access_denied(tmp_path):
    with server(tmp_path) as api:
        assert request(api, "GET", "/api/goals")[0] == 401
        assert request(api, "POST", "/api/goals", body={})[0] == 401
        assert request(api, "GET", "/api/events", headers={"Authorization":"Bearer " + "x" * 40})[0] == 401
        assert request(api, "GET", "/healthz")[0] == 200
        assert request(api, "GET", "/api/goals", who="alice",
                       headers={"Host":"attacker.example"})[0] == 403


def test_create_list_and_cross_user_get_denied(tmp_path):
    with server(tmp_path) as api:
        goal = create(api)
        assert goal["state"] == "RUNNING" and goal["version"] == 0
        alice = json.loads(request(api, "GET", "/api/goals", who="alice")[1])
        bob = json.loads(request(api, "GET", "/api/goals", who="bob")[1])
        assert len(alice["goals"]) == 1
        assert bob["goals"] == []
        assert request(api, "GET", "/api/goals/pr-1", who="bob")[0] == 404
        assert request(api, "POST", "/api/goals/pr-1/cancel", who="bob",
                       body={"expected_version": 0})[0] == 404


def test_cas_pause_resume_cancel_prevents_stale_writes_and_provider_read(tmp_path):
    with server(tmp_path) as api:
        create(api)
        status, data, _ = request(api, "POST", "/api/goals/pr-1/pause", who="alice",
                                   body={"expected_version": 0})
        assert status == 202 and json.loads(data)["goal"]["state"] == "PAUSED"
        assert request(api, "POST", "/api/goals/pr-1/resume", who="alice",
                       body={"expected_version": 0})[0] == 409
        store = api.service.goals

        class ExplodingReader:
            def fetch(self, *_):
                raise AssertionError("paused goal must not read GitHub")

        assert store.tick(user_id="alice", goal_id="pr-1", reader=ExplodingReader(),
                          now=NOW)["state"] == "PAUSED"
        status, data, _ = request(api, "POST", "/api/goals/pr-1/resume", who="alice",
                                   body={"expected_version": 1})
        assert status == 202 and json.loads(data)["goal"]["state"] == "RUNNING"
        status, data, _ = request(api, "POST", "/api/goals/pr-1/cancel", who="alice",
                                   body={"expected_version": 2})
        assert status == 202 and json.loads(data)["goal"]["state"] == "CANCELLED"
        assert store.tick(user_id="alice", goal_id="pr-1",
                          reader=ExplodingReader(), now=NOW)["state"] == "CANCELLED"
        assert request(api, "POST", "/api/goals/pr-1/resume", who="alice",
                       body={"expected_version": 3})[0] == 409


def test_pausing_while_provider_read_in_flight_drops_stale_result(tmp_path):
    with server(tmp_path) as api:
        create(api)
        store = api.service.goals
        stale = store.get(user_id="alice", goal_id="pr-1")
        store.transition(user_id="alice", goal_id="pr-1", action="pause", expected_version=0)
        assert store.observe(stale, PullSnapshot(SHA, "OPEN", "FAILURE"), now=NOW) == "INACTIVE"
        assert store.list_events(user_id="alice") == []


def test_cancellation_does_not_reanimate_closed_task(tmp_path):
    with server(tmp_path) as api:
        create(api)
        store = api.service.goals
        old = store.get(user_id="alice", goal_id="pr-1")
        store.observe(old, PullSnapshot(SHA, "MERGED", "UNKNOWN"), now=NOW)
        assert request(api, "POST", "/api/goals/pr-1/cancel", who="alice",
                       body={"expected_version": 1})[0] == 409


def test_event_cursors_are_user_scoped_and_replayable(tmp_path):
    with server(tmp_path) as api:
        create(api)
        create(api, who="bob", goal_id="pr-2")
        store = api.service.goals
        for user, goalid, status in [("alice", "pr-1", "FAILURE"),
                                     ("bob", "pr-2", "FAILURE")]:
            store.observe(
                store.get(user_id=user, goal_id=goalid),
                PullSnapshot(SHA, "OPEN", status), now=NOW,
            )
        alice = json.loads(request(api, "GET", "/api/events", who="alice")[1])["events"]
        bob = json.loads(request(api, "GET", "/api/events", who="bob")[1])["events"]
        assert len(alice) == len(bob) == 1
        assert alice[0]["goal_id"] == "pr-1"
        assert bob[0]["goal_id"] == "pr-2"
        cursor = alice[0]["cursor"]
        assert json.loads(request(api,"GET","/api/events?after="+str(cursor),
                                  who="alice")[1])["events"] == []
        status, stream, headers = request(api, "GET", "/api/events/stream?after=0", who="alice")
        assert status == 200 and "text/event-stream" in headers["Content-Type"]
        assert "id: " + str(cursor) in stream and "pr-2" not in stream
        assert request(api, "GET", "/api/events?after=-4", who="alice")[0] == 400


def test_request_validation_rejects_origin_malformed_json_and_path_encoding(tmp_path):
    with server(tmp_path) as api:
        assert request(api, "POST", "/api/goals", who="alice", body={
            "goal_id": "good", "repo": "o/r", "number": True,
        })[0] == 400
        assert request(api, "POST", "/api/goals", who="alice", body={
            "goal_id": "good", "repo": "o/r", "number": 1,
        }, headers={"Origin": "https://evil.example"})[0] == 400
        trusted = "http://127.0.0.1:" + str(api.server_port)
        assert request(api, "POST", "/api/goals", who="alice", body={
            "goal_id": "good", "repo": "o/r", "number": 1,
        }, headers={"Origin": trusted})[0] == 200
        assert request(api, "GET", "/api/goals/%2e%2e", who="alice")[0] == 400
        assert request(api, "POST", "/api/goals", who="alice", body={
            "goal_id": "bad/path", "repo": "o/r", "number": 1,
        })[0] == 400


def test_assets_load_and_do_not_embed_credentials(tmp_path):
    with server(tmp_path) as api:
        status, html, headers = request(api, "GET", "/")
        assert status == 200
        assert "Content-Security-Policy" in headers
        assert "Bearer " + TOKENS["alice"] not in html
        status, script, _ = request(api, "GET", "/pr-watch-panel.js")
        assert status == 200
        assert "localStorage" not in script
        assert "textContent" in script


def test_local_control_journal_is_atomic_and_explicitly_unattached(tmp_path):
    with server(tmp_path) as api:
        create(api)
        status, body, _ = request(api, "POST", "/api/goals/pr-1/pause", who="alice",
                                  body={"expected_version": 0})
        response = json.loads(body)
        assert status == 202
        assert response["goal"]["state"] == "PAUSED"
        assert response["control"]["signal_state"] == "LOCAL_ONLY"
        assert response["control"]["version"] == 1
        assert response["control"]["action"] == "pause"
        rows = json.loads(request(api, "GET", "/api/goals/pr-1/controls",
                                  who="alice")[1])["controls"]
        assert len(rows) == 1 and rows[0]["signal_state"] == "LOCAL_ONLY"
        assert request(api, "GET", "/api/goals/pr-1/controls", who="bob")[0] == 404
        assert request(api, "POST", "/api/goals/pr-1/controls/1/reconcile",
                       who="bob", body={})[0] == 404
        status, body, _ = request(api, "POST", "/api/goals/pr-1/controls/1/reconcile",
                                  who="alice", body={})
        assert status == 200 and json.loads(body)["control"]["signal_state"] == "LOCAL_ONLY"
        assert request(api, "POST", "/api/goals/pr-1/pause", who="alice",
                       body={"expected_version": 0})[0] == 409
        assert len(api.service.goals.controls(user_id="alice", goal_id="pr-1")) == 1


def test_control_signal_bridge_ack_and_history_reconciliation(tmp_path):
    with server(tmp_path) as api:
        create(api)
        class Bridge:
            def signal_committed(self, store, *, user_id, goal_id, version):
                return store.record_control_signal(
                    user_id=user_id, goal_id=goal_id, version=version,
                    state="UNKNOWN", evidence="ACK_UNCERTAIN",
                )
            def reconcile(self, store, *, user_id, goal_id, version):
                return store.record_control_signal(
                    user_id=user_id, goal_id=goal_id, version=version,
                    state="ACKED", evidence="HISTORY_PROOF",
                )
        api.service.bridge = Bridge()
        status, body, _ = request(api, "POST", "/api/goals/pr-1/pause",
                                  who="alice", body={"expected_version": 0})
        assert status == 202 and json.loads(body)["control"]["signal_state"] == "UNKNOWN"
        status, body, _ = request(
            api, "POST", "/api/goals/pr-1/controls/1/reconcile", who="alice", body={},
        )
        assert status == 200 and json.loads(body)["control"]["signal_state"] == "ACKED"
        assert api.service.goals.control(
            user_id="alice", goal_id="pr-1", version=1,
        )["evidence"] == "HISTORY_PROOF"
        assert api.service.goals.record_control_signal(
            user_id="alice", goal_id="pr-1", version=1,
            state="UNKNOWN", evidence="ACK_UNCERTAIN",
        )["signal_state"] == "ACKED"


def test_lost_signal_ack_does_not_erase_durable_goal_transition(tmp_path):
    with server(tmp_path) as api:
        create(api)
        class BrokenBridge:
            def signal_committed(self, *args, **kwargs):
                raise ConnectionResetError("Temporal ACK dropped after accepted")
            def reconcile(self, *args, **kwargs):
                raise ConnectionResetError("Temporal still unavailable")
        api.service.bridge = BrokenBridge()
        status, body, _ = request(api, "POST", "/api/goals/pr-1/cancel",
                                  who="alice", body={"expected_version": 0})
        assert status == 202
        assert json.loads(body)["goal"]["state"] == "CANCELLED"
        assert json.loads(body)["control"]["signal_state"] == "PENDING"
        assert request(api, "POST", "/api/goals/pr-1/cancel", who="alice",
                       body={"expected_version": 0})[0] == 409
        assert api.service.goals.get(user_id="alice", goal_id="pr-1").state == "CANCELLED"
        status, body, _ = request(
            api, "POST", "/api/goals/pr-1/controls/1/reconcile", who="alice", body={},
        )
        assert status == 200 and json.loads(body)["control"]["signal_state"] == "PENDING"



def test_launch_read_api_is_scoped_and_local_mode_is_explicit(tmp_path):
    with server(tmp_path) as api:
        goal = create(api)
        assert goal["goal_id"] == "pr-1"
        status, data, _ = request(api, "GET", "/api/goals/pr-1/launch", who="alice")
        assert status == 200
        assert json.loads(data)["launch"]["state"] == "PENDING"
        assert request(api, "GET", "/api/goals/pr-1/launch", who="bob")[0] == 404
        assert request(
            api, "POST", "/api/goals/pr-1/launch/reconcile", who="bob", body={},
        )[0] == 404
        status, body, _ = request(
            api, "POST", "/api/goals/pr-1/launch/reconcile", who="alice", body={},
        )
        assert status == 200 and json.loads(body)["launch"]["mode"] == "LOCAL_ONLY"
        assert api.service.goals.launch(user_id="alice", goal_id="pr-1")["state"] == "PENDING"


def test_registration_triggers_temporal_enrollment_only_once(tmp_path):
    with server(tmp_path) as api:
        class Launcher:
            def __init__(self):
                self.calls = 0

            def start_committed(self, store, *, user_id, goal_id):
                self.calls += 1
                claimed = store.claim_launch(user_id=user_id, goal_id=goal_id)
                if claimed["state"] == "IN_FLIGHT":
                    return store.record_launch(
                        user_id=user_id, goal_id=goal_id,
                        state="ACKED", evidence="START_ACK",
                    )
                return claimed

            def reconcile(self, store, *, user_id, goal_id):
                return store.launch(user_id=user_id, goal_id=goal_id)

        launcher = Launcher()
        api.service.launch_bridge = launcher
        status, response, _ = request(api, "POST", "/api/goals", who="alice", body={
            "goal_id": "task", "repo": "o/r", "number": 1,
        })
        assert status == 200
        assert json.loads(response)["launch"]["state"] == "ACKED"
        status, response, _ = request(api, "POST", "/api/goals", who="alice", body={
            "goal_id": "task", "repo": "o/r", "number": 1,
        })
        assert status == 200
        assert json.loads(response)["launch"]["state"] == "ACKED"
        assert launcher.calls == 2  # No new provider start from repeated call.
        assert api.service.goals.launch(user_id="alice", goal_id="task")["state"] == "ACKED"
        assert request(api, "GET", "/api/goals/task/launch", who="bob")[0] == 404


def test_panel_task_filters_are_served_and_api_remains_scoped(tmp_path):
    with server(tmp_path) as api:
        status, html, _ = request(api, "GET", "/")
        assert status == 200
        for selector in ('id="task-state"', 'id="task-repo"', 'id="task-limit"'):
            assert selector in html
        assert "independently verified success" in html
        status, js, _ = request(api, "GET", "/pr-watch-panel.js")
        assert status == 200
        assert 'api("/api/tasks?" + params.toString())' in js
        assert "new URLSearchParams()" in js
        assert "business_outcome_verified = true" not in js
        assert "localStorage" not in js
        status, css, _ = request(api, "GET", "/pr-watch-panel.css")
        assert status == 200 and ".task-toolbar" in css
        create(api)
        status, body, _ = request(
            api, "GET", "/api/tasks?state=WAITING&limit=20", who="alice",
        )
        assert status == 200
        tasks = json.loads(body)["tasks"]
        assert len(tasks) == 1
        assert tasks[0]["business_outcome_verified"] is False
        assert tasks[0]["independent_proof_ref"] is None
        assert request(api, "GET", "/api/tasks?limit=20", who="bob")[0] == 200
        assert json.loads(request(
            api, "GET", "/api/tasks?limit=20", who="bob",
        )[1])["tasks"] == []


def test_task_detail_and_activity_panel_uses_scoped_read_only_endpoints(tmp_path):
    with server(tmp_path) as api:
        create(api)
        status, html, _ = request(api, "GET", "/")
        assert status == 200
        assert 'id="activity-goal"' in html
        assert 'id="task-detail"' in html
        status, script, _ = request(api, "GET", "/pr-watch-panel.js")
        assert status == 200
        assert 'api("/api/tasks/" + encodeURIComponent(selectedTask))' in script
        assert "event.goal_id === selectedTask" in script
        assert "textContent = task.task_id" in script
        assert "innerHTML" not in script
        assert "localStorage" not in script
        status, data, _ = request(api, "GET", "/api/tasks/pr-1", who="alice")
        assert status == 200
        detail = json.loads(data)["task"]
        assert detail["task_id"] == "pr-1"
        assert detail["business_outcome_verified"] is False
        assert detail["independent_proof_ref"] is None
        assert request(api, "GET", "/api/tasks/pr-1", who="bob")[0] == 404
        assert request(api, "GET", "/api/events?after=0", who="bob")[0] == 200


def test_panel_exposes_scoped_launch_and_control_reconciliation(tmp_path):
    with server(tmp_path) as api:
        create(api)
        status, script, _ = request(api, "GET", "/pr-watch-panel.js")
        assert status == 200
        assert '"/launch/reconcile"' in script
        assert '"/controls/" + control.version + "/reconcile"' in script
        assert "Control journal" in script
        assert "does not prove workflow completion" in script
        assert "not execution proof" in script
        assert "innerHTML" not in script
        assert "localStorage" not in script
        assert request(api, "GET", "/api/goals/pr-1/launch", who="bob")[0] == 404
        assert request(api, "GET", "/api/goals/pr-1/controls", who="bob")[0] == 404
        status, data, _ = request(
            api, "POST", "/api/goals/pr-1/pause", who="alice",
            body={"expected_version": 0},
        )
        assert status == 202
        assert json.loads(data)["control"]["signal_state"] == "LOCAL_ONLY"
        status, data, _ = request(
            api, "GET", "/api/goals/pr-1/controls", who="alice",
        )
        assert status == 200
        assert len(json.loads(data)["controls"]) == 1
        status, data, _ = request(
            api, "POST", "/api/goals/pr-1/launch/reconcile",
            who="alice", body={},
        )
        assert status == 200
        assert json.loads(data)["launch"]["mode"] == "LOCAL_ONLY"


def test_panel_discards_stale_async_results_and_confirms_cancel(tmp_path):
    with server(tmp_path) as api:
        status, script, _ = request(api, "GET", "/pr-watch-panel.js")
        assert status == 200
        assert "const epoch = ++refreshEpoch" in script
        assert "if (!isCurrent()) return;" in script
        assert "requestToken !== token" in script
        assert "const detailTaskId = selectedTask" in script
        assert 'encodeURIComponent(detailTaskId)' in script
        assert 'window.confirm("Cancel "' in script
        assert "innerHTML" not in script
        assert "localStorage" not in script


def test_panel_does_not_retain_stale_task_views_on_refresh_error(tmp_path):
    with server(tmp_path) as api:
        status, script, _ = request(api, "GET", "/pr-watch-panel.js")
        assert status == 200
        assert 'result.json().catch(() => ({}))' in script
        assert '"HTTP " + result.status' in script
        assert "Unable to load tasks; retry Refresh." in script
        assert "Task details unavailable." in script
        assert "Activity unavailable." in script
        assert "if (isCurrent())" in script
        assert "innerHTML" not in script


def test_committed_control_receipt_survives_service_restart_and_is_scoped(tmp_path):
    # A committed CAS control is durable independent of response delivery.
    with server(tmp_path) as api:
        create(api)
        create(api, who="bob", goal_id="private-bob")
        store = api.service.goals
        store.transition(
            user_id="alice", goal_id="pr-1", action="pause", expected_version=0,
        )
        store.record_control_signal(
            user_id="alice", goal_id="pr-1", version=1,
            state="UNKNOWN", evidence="ACK_UNCERTAIN",
        )
        status, data, _ = request(
            api, "GET", "/api/control-receipts?limit=10", who="alice",
        )
        assert status == 200
        response = json.loads(data)
        assert response["schema"] == "pr-watch-control-receipts/v1"
        assert len(response["controls"]) == 1
        item = response["controls"][0]
        assert item["goal_id"] == "pr-1" and item["version"] == 1
        assert item["action"] == "pause" and item["signal_state"] == "UNKNOWN"
        assert item["cas_committed"] is True
        assert item["receipt_id"].startswith("prc_")
        receipt_id = item["receipt_id"]
        status, data, _ = request(
            api, "GET", "/api/goals/pr-1/controls/1", who="alice",
        )
        assert status == 200
        assert json.loads(data)["control"]["receipt_id"] == receipt_id
        assert request(
            api, "GET", "/api/goals/pr-1/controls/1", who="bob",
        )[0] == 404
        assert request(
            api, "GET", "/api/control-receipts?limit=10", who="bob",
        )[0] == 200
        assert json.loads(request(
            api, "GET", "/api/control-receipts", who="bob",
        )[1])["controls"] == []
        assert request(api, "GET", "/api/control-receipts")[0] == 401
        for invalid in (
            "/api/control-receipts?limit=0",
            "/api/control-receipts?limit=101",
            "/api/control-receipts?limit=1&limit=2",
            "/api/control-receipts?user_id=bob",
        ):
            assert request(api, "GET", invalid, who="alice")[0] == 400

    # Reconstruct from persisted journal, without using browser storage
    # or claiming that UNKNOWN Temporal delivery means uncommitted Goal CAS.
    with server(tmp_path) as api:
        status, data, _ = request(
            api, "GET", "/api/control-receipts", who="alice",
        )
        assert status == 200
        item = json.loads(data)["controls"][0]
        assert item["receipt_id"] == receipt_id
        assert item["signal_state"] == "UNKNOWN"
        assert item["cas_committed"] is True
        assert request(
            api, "GET", "/api/goals/pr-1/controls/1?unexpected=1",
            who="alice",
        )[0] == 400


def test_unsettled_control_readback_only_returns_pending_or_unknown(tmp_path):
    with server(tmp_path) as api:
        create(api)
        store = api.service.goals
        store.transition(
            user_id="alice", goal_id="pr-1", action="pause", expected_version=0,
        )
        status, data, _ = request(api, "GET", "/api/control-receipts", who="alice")
        assert status == 200
        assert json.loads(data)["controls"][0]["signal_state"] == "PENDING"
        store.record_control_signal(
            user_id="alice", goal_id="pr-1", version=1,
            state="LOCAL_ONLY", evidence="BRIDGE_DISABLED",
        )
        assert json.loads(request(
            api, "GET", "/api/control-receipts", who="alice",
        )[1])["controls"] == []
        item = json.loads(request(
            api, "GET", "/api/goals/pr-1/controls/1", who="alice",
        )[1])["control"]
        assert item["signal_state"] == "LOCAL_ONLY"
        assert item["cas_committed"] is True


def test_prepared_control_durable_replay_and_cross_owner_isolation(tmp_path):
    request_id = "1" * 32
    with server(tmp_path) as api:
        create(api)
        prepared = {
            "request_id": request_id, "action": "pause", "expected_version": 0,
        }
        path = "/api/goals/pr-1/controls/prepare"
        status, data, _ = request(api, "POST", path, who="alice", body=prepared)
        assert status == 200 and json.loads(data)["request"]["state"] == "PREPARED"
        status, data, _ = request(api, "POST", path, who="alice", body=prepared)
        assert status == 200 and json.loads(data)["request"]["state"] == "PREPARED"
        assert api.service.goals.get(user_id="alice", goal_id="pr-1").version == 0
        assert request(api, "GET", "/api/control-requests/" + request_id, who="bob")[0] == 404
        assert json.loads(request(api, "GET", "/api/control-requests", who="bob")[1])["requests"] == []
        assert request(api, "POST", path, who="bob", body=prepared)[0] == 404
        assert request(api, "POST", path, who="alice", body={
            **prepared, "action": "cancel",
        })[0] == 409
        status, data, _ = request(api, "GET", "/api/control-requests", who="alice")
        assert status == 200
        assert len(json.loads(data)["requests"]) == 1
        for url in (
            "/api/control-requests?limit=0",
            "/api/control-requests?limit=101",
            "/api/control-requests?user=bob",
            "/api/control-requests?limit=1&limit=2",
        ):
            assert request(api, "GET", url, who="alice")[0] == 400
        assert request(api, "GET", "/api/control-requests")[0] == 401
        assert request(api, "POST", "/api/goals/pr-1/pause", who="alice",
                       body={"expected_version": 0, "request_id": "2" * 32})[0] == 409
        assert api.service.goals.get(user_id="alice", goal_id="pr-1").version == 0
    # The prepare response may have been lost and the browser reloaded.
    with server(tmp_path) as api:
        status, data, _ = request(
            api, "GET", "/api/control-requests/" + request_id, who="alice",
        )
        assert status == 200 and json.loads(data)["request"]["state"] == "PREPARED"
        body = {"expected_version": 0, "request_id": request_id}
        status, data, _ = request(api, "POST", "/api/goals/pr-1/pause", who="alice", body=body)
        assert status == 202
        assert json.loads(data)["replayed"] is False
        assert json.loads(data)["control"]["version"] == 1
        assert json.loads(request(
            api, "GET", "/api/control-requests/" + request_id, who="alice",
        )[1])["request"]["state"] == "COMMITTED"
        status, data, _ = request(api, "POST", "/api/goals/pr-1/pause", who="alice", body=body)
        assert status == 202
        assert json.loads(data)["replayed"] is True
        assert json.loads(data)["goal"]["version"] == 1
        assert len(api.service.goals.controls(user_id="alice", goal_id="pr-1")) == 1
        assert request(api, "POST", "/api/goals/pr-1/cancel", who="alice", body=body)[0] == 409


def test_idempotent_retry_never_resends_temporal_signal(tmp_path):
    request_id = "a" * 32
    class CountingBridge:
        calls = 0

        def signal_committed(self, store, *, user_id, goal_id, version):
            self.calls += 1
            return store.record_control_signal(
                user_id=user_id, goal_id=goal_id, version=version,
                state="ACKED", evidence="SIGNAL_ACK",
            )

    with server(tmp_path) as api:
        create(api)
        bridge = CountingBridge()
        api.service.bridge = bridge
        preparation = {"request_id": request_id, "action": "cancel", "expected_version": 0}
        assert request(api, "POST", "/api/goals/pr-1/controls/prepare",
                       who="alice", body=preparation)[0] == 200
        payload = {"expected_version": 0, "request_id": request_id}
        for i in range(3):
            status, data, _ = request(
                api, "POST", "/api/goals/pr-1/cancel", who="alice", body=payload,
            )
            assert status == 200
            assert json.loads(data)["replayed"] is (i != 0)
        assert bridge.calls == 1
        assert len(api.service.goals.controls(user_id="alice", goal_id="pr-1")) == 1
        assert api.service.goals.get(user_id="alice", goal_id="pr-1").state == "CANCELLED"
        for invalid in ("abc", "Z" * 32, "f" * 33):
            assert request(api, "POST", "/api/goals/pr-1/controls/prepare",
                           who="alice", body={**preparation, "request_id": invalid})[0] == 400
