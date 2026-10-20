"""Negative-first tests for the read-only Goal-backed Task projection."""
from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))
from pr_watch_harness import GoalStore, PullSnapshot, WatchError
from pr_watch_service import LocalTokens, PilotHTTPServer, PilotService
from pr_watch_task_projection import project_task


TOKENS = {"alice": "a" * 40, "bob": "b" * 40}
REPO = "avaargsh/agent-control-plane"
SHA = "c" * 40
NOW = datetime(2026, 10, 20, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    db = GoalStore(tmp_path / "projection.db")
    db.register(user_id="alice", goal_id="g-1", repo=REPO, number=169)
    return db


def task(db):
    return project_task(
        db.get(user_id="alice", goal_id="g-1"),
        db.launch(user_id="alice", goal_id="g-1"),
        principal="alice",
    )


def test_launch_wait_then_ambiguous_then_ack_is_explicit(store):
    assert task(store)["state"] == "WAITING"
    assert task(store)["business_outcome_verified"] is False
    assert store.claim_launch(user_id="alice", goal_id="g-1")["state"] == "IN_FLIGHT"
    assert task(store)["state"] == "UNKNOWN"
    store.record_launch(user_id="alice", goal_id="g-1", state="UNKNOWN", evidence="START_UNCERTAIN")
    assert task(store)["state"] == "UNKNOWN"
    store.record_launch(user_id="alice", goal_id="g-1", state="OBSERVED", evidence="DESCRIBE_EXISTS")
    assert task(store)["state"] == "ACTIVE"


def test_observed_merge_is_not_promoted_to_verified_success(store):
    store.claim_launch(user_id="alice", goal_id="g-1")
    store.record_launch(user_id="alice", goal_id="g-1", state="ACKED", evidence="START_ACK")
    goal = store.get(user_id="alice", goal_id="g-1")
    store.observe(goal, PullSnapshot(SHA, "OPEN", "SUCCESS"), now=NOW)
    cleared = task(store)
    assert cleared["state"] == "ACTIVE"
    assert cleared["observation_phase"] == "CI_CLEARED"
    assert cleared["observation_digest"].startswith("sha256:")
    assert cleared["independent_proof_ref"] is None
    assert cleared["business_outcome_verified"] is False
    store.observe(
        store.get(user_id="alice", goal_id="g-1"),
        PullSnapshot(SHA, "MERGED", "UNKNOWN"), now=NOW,
    )
    completed = task(store)
    assert completed["state"] == "OBSERVED_TERMINAL"
    assert completed["observation_phase"] == "MERGED"
    assert completed["business_outcome_verified"] is False
    assert completed["independent_proof_ref"] is None


def test_closed_without_merge_is_not_success(store):
    store.observe(
        store.get(user_id="alice", goal_id="g-1"),
        PullSnapshot(SHA, "CLOSED", "UNKNOWN"), now=NOW,
    )
    assert task(store)["state"] == "OBSERVED_TERMINAL"
    assert task(store)["observation_phase"] == "CLOSED"


def test_cancelled_local_goal_is_not_proven_remote_termination(store):
    store.transition(user_id="alice", goal_id="g-1", action="cancel", expected_version=0)
    assert task(store)["state"] == "CANCEL_REQUESTED"
    assert task(store)["business_outcome_verified"] is False


@pytest.mark.parametrize("mutation", [
    {"user_id": "bob"},
    {"goal_id": "different-goal"},
    {"state": "MADE_UP"},
    {"phase": "SIGNED_PROOF"},
    {"signature": "tampered"},
    {"version": True},
])
def test_malformed_or_cross_scope_records_fail_closed(store, mutation):
    goal = asdict(store.get(user_id="alice", goal_id="g-1"))
    goal.update(mutation)
    with pytest.raises(WatchError):
        project_task(goal, store.launch(user_id="alice", goal_id="g-1"), principal="alice")


def test_wrong_principal_and_forged_launch_are_rejected(store):
    goal = store.get(user_id="alice", goal_id="g-1")
    launch = store.launch(user_id="alice", goal_id="g-1")
    with pytest.raises(WatchError, match="scope"):
        project_task(goal, launch, principal="bob")
    forged_launch = {**launch, "user_id": "bob"}
    with pytest.raises(WatchError, match="scope"):
        project_task(goal, forged_launch, principal="alice")


def test_pg_mapping_shape_projects_without_second_database(store):
    goal = {
        "user_id": "alice", "goal_id": "pg-1", "repo": REPO, "number": 17,
        "state": "RUNNING", "version": 0, "phase": "", "signature": "",
    }
    launch = {
        "user_id": "alice", "goal_id": "pg-1", "state": "OBSERVED",
        "workflow_status": "RUNNING",
    }
    projected = project_task(goal, launch, principal="alice")
    assert projected["task_id"] == "pg-1"
    assert projected["state"] == "ACTIVE"
    assert projected["source_of_truth"] == "pr-watch-goal"


@contextmanager
def local_api(tmp_path):
    service = PilotService(tmp_path / "api.db", LocalTokens(TOKENS))
    http = PilotHTTPServer(("127.0.0.1", 0), service)
    thread = Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        yield http
    finally:
        http.shutdown()
        thread.join(timeout=3)
        http.server_close()


def request(http, path, *, who=None, method="GET", payload=None):
    headers = {}
    if who is not None:
        headers["Authorization"] = "Bearer " + TOKENS[who]
    body = None
    if payload is not None:
        body = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = Request(
        "http://127.0.0.1:" + str(http.server_port) + path,
        method=method, data=body, headers=headers,
    )
    try:
        with urlopen(req, timeout=3) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        return exc.code, json.load(exc)


def test_task_http_projection_is_authenticated_and_owner_scoped(tmp_path):
    with local_api(tmp_path) as api:
        status, _ = request(
            api, "/api/goals", who="alice", method="POST",
            payload={"goal_id": "shared", "repo": REPO, "number": 169},
        )
        assert status == 200
        assert request(api, "/api/tasks")[0] == 401
        assert request(api, "/api/tasks/shared", who="bob")[0] == 404
        assert request(api, "/api/tasks?user_id=bob", who="alice")[0] == 400
        assert request(api, "/api/tasks/shared?user_id=bob", who="alice")[0] == 400

        status, body = request(api, "/api/tasks", who="alice")
        assert status == 200
        assert body["schema"] == "pr-watch-task-projection/v1"
        assert len(body["tasks"]) == 1
        assert body["tasks"][0]["state"] == "WAITING"
        assert body["tasks"][0]["business_outcome_verified"] is False
        assert body["tasks"][0]["independent_proof_ref"] is None
        assert "user_id" not in body["tasks"][0]
        assert request(api, "/api/tasks", who="bob") == (
            200, {"schema": "pr-watch-task-projection/v1", "tasks": []},
        )
        status, item = request(api, "/api/tasks/shared", who="alice")
        assert status == 200 and item["task"]["task_id"] == "shared"
        assert request(api, "/api/goals/shared", who="alice")[0] == 200
        assert request(api, "/api/tasks/shared", who="alice", method="POST", payload={})[0] == 404


def test_task_list_filters_by_state_repo_and_limit_without_cross_user_leak(tmp_path):
    with local_api(tmp_path) as api:
        for who, goal_id, repo in (
            ("alice", "g-one", REPO),
            ("alice", "g-two", "example/second"),
            ("bob", "private", REPO),
        ):
            status, _ = request(
                api, "/api/goals", who=who, method="POST",
                payload={"goal_id": goal_id, "repo": repo, "number": 10},
            )
            assert status == 200
        status, body = request(api, "/api/tasks?state=WAITING", who="alice")
        assert status == 200
        assert {t["task_id"] for t in body["tasks"]} == {"g-one", "g-two"}
        status, body = request(api, "/api/tasks?repo=" + REPO, who="alice")
        assert status == 200
        assert [t["task_id"] for t in body["tasks"]] == ["g-one"]
        status, body = request(
            api, "/api/tasks?repo=" + REPO + "&state=WAITING&limit=1",
            who="alice",
        )
        assert status == 200
        assert [t["task_id"] for t in body["tasks"]] == ["g-one"]
        status, body = request(api, "/api/tasks?state=ACTIVE", who="alice")
        assert status == 200 and body["tasks"] == []
        status, body = request(api, "/api/tasks?limit=1", who="alice")
        assert status == 200 and len(body["tasks"]) == 1
        assert request(api, "/api/tasks?state=WAITING", who="bob")[1]["tasks"][0][
            "task_id"
        ] == "private"
        assert request(api, "/api/tasks?state=WAITING")[0] == 401


@pytest.mark.parametrize("query", [
    "user_id=bob", "state=SUCCEEDED", "state=", "state=WAITING&state=ACTIVE",
    "repo=", "repo=other", "repo=../admin", "repo=example%2F..",
    "repo=example/second&repo=example/second",
    "limit=0", "limit=101", "limit=-1", "limit=01", "limit=abc",
    "limit=1&limit=2", "unknown=1", "state=WAITING&user_id=bob",
])
def test_task_query_invalid_parameters_fail_closed(tmp_path, query):
    with local_api(tmp_path) as api:
        assert request(api, "/api/tasks?" + query, who="alice")[0] == 400
