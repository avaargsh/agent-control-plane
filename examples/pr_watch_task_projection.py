"""Read-only PR Watch Goal -> Task view; never a new authoritative ledger.

The GoalStore/PostgresWatchStore remains the owner of business state. Temporal
owns workflow continuation; neither a GitHub webhook nor a digest alone proves
an authenticated external business outcome.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Mapping

from pr_watch_harness import WatchError


GOAL_PHASES = {
    "", "WAITING", "CI_BLOCKED", "REVIEW_BLOCKED", "CI_CLEARED", "MERGED", "CLOSED",
}
GOAL_STATES = {"RUNNING", "PAUSED", "FINISHED", "CANCELLED"}
LAUNCH_STATES = {"PENDING", "IN_FLIGHT", "UNKNOWN", "ACKED", "OBSERVED", "SKIPPED"}
WORKFLOW_STATES = {"RUNNING", "COMPLETED", "FAILED", "CANCELED", "TERMINATED", "TIMED_OUT"}


def _mapping(value: Any) -> dict:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, Mapping):
        return dict(value)
    raise WatchError("invalid PR Watch projection source")


def project_task(goal: Any, launch: Any, *, principal: str) -> dict:
    """Project already scoped, durable Goal+launch records without writes.

    Principal scope must come from the authenticated server, never request data.
    This deliberately has no SUCCEEDED state: the current Goal observation
    signature is a SHA-256 fingerprint, NOT independently authenticated proof.
    """
    goal_record, launch_record = _mapping(goal), _mapping(launch)
    if (
        not isinstance(principal, str) or not principal
        or goal_record.get("user_id") != principal or launch_record.get("user_id") != principal
        or goal_record.get("goal_id") != launch_record.get("goal_id")
    ):
        raise WatchError("task projection scope mismatch")
    if (
        goal_record.get("state") not in GOAL_STATES
        or goal_record.get("phase") not in GOAL_PHASES
        or launch_record.get("state") not in LAUNCH_STATES
        or not isinstance(goal_record.get("repo"), str) or "/" not in goal_record["repo"]
        or type(goal_record.get("number")) is not int or goal_record["number"] < 1
        or type(goal_record.get("version")) is not int or goal_record["version"] < 0
        or not isinstance(goal_record.get("goal_id"), str) or not goal_record["goal_id"]
    ):
        raise WatchError("invalid PR Watch projection record")
    workflow = launch_record.get("workflow_status")
    if workflow is not None and workflow not in WORKFLOW_STATES:
        raise WatchError("invalid workflow observation")
    signature = goal_record.get("signature")
    if not isinstance(signature, str) or (
        signature and (len(signature) != 64 or any(ch not in "0123456789abcdef" for ch in signature))
    ):
        raise WatchError("invalid observation digest")

    state = goal_record["state"]
    phase = goal_record["phase"]
    started = launch_record["state"]
    if state == "FINISHED":
        # A stored GitHub observation may be terminal, but it is not a
        # trusted ACP proof. Never promote it to verified SUCCEEDED.
        task_state = "OBSERVED_TERMINAL" if phase in {"MERGED", "CLOSED"} else "UNKNOWN"
    elif state == "CANCELLED":
        # Local CAS cancellation != proven workflow termination.
        task_state = "CANCEL_REQUESTED"
    elif state == "PAUSED":
        task_state = "PAUSED"
    elif started in {"UNKNOWN", "IN_FLIGHT", "SKIPPED"}:
        task_state = "UNKNOWN"
    elif started == "PENDING":
        task_state = "WAITING"
    elif phase in {"CI_BLOCKED", "REVIEW_BLOCKED", "WAITING"}:
        task_state = "WAITING"
    else:
        task_state = "ACTIVE"

    return {
        "schema": "pr-watch-task-projection/v1",
        "task_id": goal_record["goal_id"],
        "kind": "github-pr-watch",
        "source_of_truth": "pr-watch-goal",
        "repo": goal_record["repo"],
        "pull_number": goal_record["number"],
        "version": goal_record["version"],
        "state": task_state,
        "goal_state": state,
        "observation_phase": phase or None,
        "observation_digest": "sha256:" + signature if signature else None,
        "launch_state": started,
        "workflow_status": workflow,
        "business_outcome_verified": False,
        "independent_proof_ref": None,
    }
