"""PostgreSQL enrollment dispatcher using the existing read-only Temporal bridge.

This is an opt-in testable adapter, NOT a replacement for SQLite GoalStore,
AttentionOutbox or the HTTP API. Do not run mixed SQLite/PG replicas.
"""
from __future__ import annotations

import asyncio

from pr_watch_launcher import TemporalLaunchBridge
from pr_watch_pg_admission import AdmissionError, PostgresAdmission


class PostgresLaunchDispatcher:
    def __init__(
        self, *, store: PostgresAdmission, temporal: TemporalLaunchBridge,
    ) -> None:
        self.store = store
        self.temporal = temporal

    def start_once(self, *, user_id: str, goal_id: str, owner: str) -> dict:
        """Only the PostgreSQL CAS winner sends StartWorkflowExecution."""
        claim = self.store.claim(user_id=user_id, goal_id=goal_id, owner=owner)
        if not claim.claimed_now:
            return self.store.launch(user_id=user_id, goal_id=goal_id)
        try:
            outcome = asyncio.run(self.temporal._start(
                user_id=user_id, goal_id=goal_id,
            ))
        except Exception:
            # An error may be a lost ACK after Temporal accepted a Workflow.
            return self._finish_uncertain(claim)
        if outcome == "START_ACK":
            # An expired/fenced owner cannot overwrite the outcome even if
            # Temporal accepted the start late.
            try:
                return self.store.finalize(claim=claim, state="ACKED")
            except AdmissionError:
                return self.store.launch(user_id=user_id, goal_id=goal_id)
        if outcome == "ALREADY_EXISTS":
            # A conflict is not proof of a running workflow; describe first.
            return self._describe_or_uncertain(
                user_id=user_id, goal_id=goal_id, claim=claim,
            )
        return self._finish_uncertain(claim)

    def _finish_uncertain(self, claim) -> dict:
        try:
            return self.store.finalize(claim=claim, state="UNKNOWN")
        except AdmissionError:
            return self.store.launch(user_id=claim.user_id, goal_id=claim.goal_id)

    def _describe_or_uncertain(self, *, user_id: str, goal_id: str, claim) -> dict:
        try:
            status = asyncio.run(self.temporal._describe(
                user_id=user_id, goal_id=goal_id,
            ))
            if status:
                return self.store.observe_existing(
                    user_id=user_id, goal_id=goal_id, workflow_status=status,
                )
        except Exception:
            pass
        return self._finish_uncertain(claim)

    def reconcile(self, *, user_id: str, goal_id: str) -> dict:
        """Read-only: missing Temporal execution never authorizes a new start."""
        current = self.store.launch(user_id=user_id, goal_id=goal_id)
        if current["state"] in ("PENDING", "SKIPPED"):
            return current
        try:
            status = asyncio.run(self.temporal._describe(
                user_id=user_id, goal_id=goal_id,
            ))
            if status:
                return self.store.observe_existing(
                    user_id=user_id, goal_id=goal_id, workflow_status=status,
                )
        except Exception:
            pass
        return self.store.launch(user_id=user_id, goal_id=goal_id)
