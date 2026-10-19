"""Offline negative/replay acceptance for read-only persistent PR Harness."""
from __future__ import annotations

import ast
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Thread

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))
from attention_plane import AttentionOutbox, DeliveryState
from pr_watch_github import GitHubReadOnlyReader, ReadOnlyGitHubError
from pr_watch_harness import GoalStore, PullSnapshot, WatchError

NOW = datetime(2026, 10, 18, 10, tzinfo=timezone.utc)
SHA = "a" * 40


class Reader:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.calls = 0

    def fetch(self, repo, number):
        assert (repo, number) == ("avaargsh/agent-control-plane", 168)
        self.calls += 1
        if isinstance(self.snapshot, Exception):
            raise self.snapshot
        return self.snapshot


def goal(tmp_path, user="u1"):
    store = GoalStore(tmp_path / "watch.db")
    store.register(user_id=user, goal_id="pr-168",
                   repo="avaargsh/agent-control-plane", number=168)
    return store


def snap(state="OPEN", ci="PENDING", review="NONE", sha=SHA, failed=()):
    return PullSnapshot(sha, state, ci, review, tuple(failed))


def tick(store, reader, user="u1", now=NOW):
    return store.tick(user_id=user, goal_id="pr-168", reader=reader, now=now)


def test_idempotent_goal_and_immutable_pr_binding(tmp_path):
    store = goal(tmp_path)
    assert store.register(user_id="u1", goal_id="pr-168",
                          repo="avaargsh/agent-control-plane", number=168).version == 0
    with pytest.raises(WatchError, match="cannot be rebound"):
        store.register(user_id="u1", goal_id="pr-168",
                       repo="avaargsh/agent-control-plane", number=169)
    with pytest.raises(WatchError):
        store.register(user_id="u1", goal_id="bad", repo="https://evil/target", number=1)


def test_cross_user_isolation(tmp_path):
    store = goal(tmp_path)
    with pytest.raises(WatchError, match="not found"):
        store.get(user_id="u2", goal_id="pr-168")
    with pytest.raises(WatchError):
        tick(store, Reader(snap()), user="u2")


def test_ci_failure_notified_once_without_untrusted_check_text(tmp_path):
    store = goal(tmp_path)
    reader = Reader(snap(ci="FAILURE", failed=("malicious check description",)))
    assert tick(store, reader)["phase"] == "CI_BLOCKED"
    assert tick(store, reader)["new"] is False
    outbox = AttentionOutbox(store.path)
    assert outbox.count(user_id="u1", state=DeliveryState.PENDING) == 1
    lease = outbox.claim_due(user_id="u1", owner="worker", now=NOW)[0]
    assert "malicious check description" not in lease.summary


def test_github_outage_never_changes_observed_state(tmp_path):
    store = goal(tmp_path)
    with pytest.raises(OSError):
        tick(store, Reader(OSError("GitHub unavailable")))
    assert store.get(user_id="u1", goal_id="pr-168").version == 0
    assert AttentionOutbox(store.path).count(user_id="u1", state=DeliveryState.PENDING) == 0


def test_pending_unknown_never_claim_green(tmp_path):
    store = goal(tmp_path)
    assert tick(store, Reader(snap(ci="UNKNOWN")))["phase"] == "WAITING"
    assert tick(store, Reader(snap(ci="PENDING", sha="b" * 40)))["phase"] == "WAITING"
    assert AttentionOutbox(store.path).count(user_id="u1", state=DeliveryState.PENDING) == 0


def test_failure_then_green_digest_then_merged_finishes(tmp_path):
    store = goal(tmp_path)
    tick(store, Reader(snap(ci="FAILURE")))
    assert tick(store, Reader(snap(ci="SUCCESS")),
                now=NOW + timedelta(minutes=1))["phase"] == "CI_CLEARED"
    assert AttentionOutbox(store.path).count(user_id="u1", state=DeliveryState.PENDING) == 1
    assert tick(store, Reader(snap(state="MERGED", ci="UNKNOWN")),
                now=NOW + timedelta(minutes=2))["state"] == "FINISHED"
    reader = Reader(OSError("should not fetch"))
    assert tick(store, reader, now=NOW + timedelta(days=1))["state"] == "FINISHED"
    assert reader.calls == 0


def test_closed_without_merge_needs_user_action(tmp_path):
    store = goal(tmp_path)
    result = tick(store, Reader(snap(state="CLOSED", ci="UNKNOWN")))
    assert result == {"state": "FINISHED", "phase": "CLOSED", "new": True}
    assert AttentionOutbox(store.path).count(user_id="u1", state=DeliveryState.PENDING) == 1


def test_changes_requested_not_falsely_ready(tmp_path):
    store = goal(tmp_path)
    assert tick(store, Reader(snap(ci="SUCCESS", review="CHANGES_REQUESTED")))["phase"] == "REVIEW_BLOCKED"


def test_crash_before_outbox_handoff_recovers(tmp_path):
    store = goal(tmp_path)
    store.observe(store.get(user_id="u1", goal_id="pr-168"),
                  snap(ci="FAILURE"), now=NOW)
    assert AttentionOutbox(store.path).count(user_id="u1", state=DeliveryState.PENDING) == 0
    assert tick(GoalStore(store.path), Reader(snap(ci="FAILURE")))["new"] is False
    assert AttentionOutbox(store.path).count(user_id="u1", state=DeliveryState.PENDING) == 1


def test_lost_ack_after_outbox_insert_replay_safe(tmp_path, monkeypatch):
    store = goal(tmp_path)
    store.observe(store.get(user_id="u1", goal_id="pr-168"),
                  snap(ci="FAILURE"), now=NOW)
    original = AttentionOutbox.submit
    first = [True]

    def lost_ack(self, event, preferences, *, now):
        result = original(self, event, preferences, now=now)
        if first[0]:
            first[0] = False
            raise OSError("ACK lost after commit")
        return result

    monkeypatch.setattr(AttentionOutbox, "submit", lost_ack)
    with pytest.raises(OSError):
        store.drain(user_id="u1", goal_id="pr-168", now=NOW)
    assert GoalStore(store.path).drain(user_id="u1", goal_id="pr-168", now=NOW) == 1
    assert AttentionOutbox(store.path).count(user_id="u1", state=DeliveryState.PENDING) == 1


def test_new_failure_revision_is_new_incident(tmp_path):
    store = goal(tmp_path)
    tick(store, Reader(snap(ci="FAILURE", failed=("check-1",))))
    tick(store, Reader(snap(ci="FAILURE", failed=("check-2",))),
         now=NOW + timedelta(minutes=2))
    assert AttentionOutbox(store.path).count(user_id="u1", state=DeliveryState.PENDING) == 2


def test_two_observers_dedupe_one_event(tmp_path):
    store = goal(tmp_path)
    errors = []

    def run():
        try:
            tick(GoalStore(store.path), Reader(snap(ci="FAILURE")))
        except Exception as exc:
            errors.append(exc)

    first, second = Thread(target=run), Thread(target=run)
    first.start()
    second.start()
    first.join()
    second.join()
    assert errors == []
    assert AttentionOutbox(store.path).count(user_id="u1", state=DeliveryState.PENDING) == 1


def github(fake):
    reader = GitHubReadOnlyReader(token="test-read-only-token")
    reader._get = fake
    return reader


def base(path):
    if "/reviews" in path:
        return []
    if "/check-runs" in path:
        return {"total_count": 1, "check_runs": [
            {"id": 1, "status": "completed", "conclusion": "success"}]}
    if path.endswith("/status"):
        return {"statuses": []}
    return {"head": {"sha": SHA}, "state": "open", "merged": False}


def test_read_only_github_observation_green_not_merge_proof():
    snapshot = github(base).fetch("avaargsh/agent-control-plane", 168)
    assert snapshot.ci == "SUCCESS" and snapshot.state == "OPEN"


def test_zero_and_truncated_checks_fail_conservatively():
    def empty(path):
        if "/check-runs" in path:
            return {"total_count": 0, "check_runs": []}
        return base(path)
    assert github(empty).fetch("avaargsh/agent-control-plane", 168).ci == "UNKNOWN"

    def truncated(path):
        if "/check-runs" in path:
            return {"total_count": 101, "check_runs": []}
        return base(path)
    with pytest.raises(ReadOnlyGitHubError, match="unobserved"):
        github(truncated).fetch("avaargsh/agent-control-plane", 168)


def test_git_checks_and_latest_review_conflict():
    def response(path):
        if "/check-runs" in path:
            return {"total_count": 1, "check_runs": [
                {"id": 2, "status": "completed", "conclusion": "failure"}]}
        if "/reviews" in path:
            return [{"user": {"id": 11}, "state": "CHANGES_REQUESTED"},
                    {"user": {"id": 11}, "state": "APPROVED"},
                    {"user": {"id": 12}, "state": "CHANGES_REQUESTED"}]
        return base(path)
    snapshot = github(response).fetch("avaargsh/agent-control-plane", 168)
    assert snapshot.ci == "FAILURE"
    assert snapshot.review == "CHANGES_REQUESTED"
    assert "check-2" in snapshot.failed_checks


def test_pr_selector_rejects_dot_segments_and_boolean_numbers_before_get():
    reader = GitHubReadOnlyReader(token="test-read-only-token")

    def no_http(path):
        raise AssertionError("invalid selector reached GitHub API")

    reader._get = no_http
    for bad_repo in ("../target", "./repo", "owner/..", "owner/."):
        with pytest.raises(WatchError, match="invalid GitHub PR selector"):
            reader.fetch(bad_repo, 168)
    for bad_number in (True, False, 0, -1):
        with pytest.raises(WatchError, match="invalid GitHub PR selector"):
            reader.fetch("owner/repo", bad_number)


def test_untrusted_pr_head_never_selects_another_rest_resource():
    pr_path = "/repos/avaargsh/agent-control-plane/pulls/168"
    for bad_head in (None, "", "a" * 39, "g" * 40, "../commits/other",
                     "a" * 40 + "/check-runs", "a" * 64):
        paths = []

        def response(path):
            paths.append(path)
            if path != pr_path:
                raise AssertionError("untrusted head reached secondary REST path")
            return {"head": {"sha": bad_head}, "state": "open", "merged": False}

        with pytest.raises(ReadOnlyGitHubError, match="invalid head revision"):
            github(response).fetch("avaargsh/agent-control-plane", 168)
        assert paths == [pr_path]


def test_bad_git_lifecycle_never_guessed():
    def response(path):
        if path.endswith("/pulls/168"):
            return {"head": {"sha": SHA}, "state": "closed", "merged": None}
        return base(path)
    with pytest.raises(ReadOnlyGitHubError, match="unverifiable"):
        github(response).fetch("avaargsh/agent-control-plane", 168)


def test_temporal_module_parses_and_github_is_get_only():
    root = Path(__file__).resolve().parents[1] / "examples"
    source = (root / "pr_watch_temporal.py").read_text()
    ast.parse(source)
    assert "workflow.continue_as_new(spec)" in source
    assert 'method="GET"' in (root / "pr_watch_github.py").read_text()
