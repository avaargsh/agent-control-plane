"""Explicit PostgreSQL runtime adapters; never open the SQLite GoalStore.

The PostgresWatchStore is the sole authority for Goal, launch, control,
observation and notification persistence in this mode. This is an opt-in
local development runtime, not production AuthN or HA.
"""
from __future__ import annotations

from datetime import datetime

from attention_plane import DeliveryState
from pr_watch_pg_store import PostgresWatchStore
from pr_watch_pg_dispatcher import PostgresLaunchDispatcher
from pr_watch_launcher import TemporalLaunchBridge


class PGLaunchService:
    """Reuses deterministic Workflow IDs and single-claim PG fencing."""

    def __init__(self, store: PostgresWatchStore, temporal: TemporalLaunchBridge):
        self.store = store
        self.dispatcher = PostgresLaunchDispatcher(store=store, temporal=temporal)

    def start_committed(self, *, user_id: str, goal_id: str, owner: str) -> dict:
        return self.dispatcher.start_once(
            user_id=user_id, goal_id=goal_id, owner=owner,
        )

    def reconcile(self, *, user_id: str, goal_id: str) -> dict:
        return self.dispatcher.reconcile(user_id=user_id, goal_id=goal_id)

    def startup_sweep(self, *, owner: str, limit: int = 100) -> dict:
        """Bounded best-effort startup sweep; only truly PENDING may start."""
        expired = self.store.recover_expired()
        entries = self.store.pending(limit=limit)
        started = observed = unresolved = 0
        for entry in entries:
            try:
                if entry["state"] == "PENDING":
                    started += 1
                    result = self.start_committed(
                        user_id=entry["user_id"], goal_id=entry["goal_id"],
                        owner=owner,
                    )
                else:
                    result = self.reconcile(
                        user_id=entry["user_id"], goal_id=entry["goal_id"],
                    )
                if result["state"] == "OBSERVED":
                    observed += 1
                if result["state"] in ("PENDING", "IN_FLIGHT", "UNKNOWN"):
                    unresolved += 1
            except Exception:
                # Never retry a potentially sent Workflow start from here.
                unresolved += 1
        return {
            "expired_to_unknown": expired, "inspected": len(entries),
            "start_candidates": started, "observed": observed,
            "unresolved": unresolved,
        }


class PGTelegramOutbox:
    """Adapter for the proven opt-in Telegram dispatcher API.

    The PostgreSQL server clock decides due/expiry; caller's now argument is
    intentionally ignored. No caller can override tenant scope or lease.
    """
    def __init__(self, store: PostgresWatchStore):
        self.store = store

    def recover_expired(self, *, now: datetime, user_id: str) -> int:
        del now
        return self.store.recover_expired_deliveries(user_id=user_id)

    def claim_due(
        self, *, user_id: str, owner: str, now: datetime,
        limit: int = 10, lease_seconds: int = 60,
    ) -> list:
        del now
        return self.store.claim_due(
            user_id=user_id, owner=owner, limit=limit,
            lease_seconds=lease_seconds,
        )

    def mark_unknown(self, lease, *, now: datetime) -> None:
        del now
        self.store.complete_delivery(lease, state=DeliveryState.UNKNOWN)

    def mark_delivered_with_receipt(
        self, lease, *, now: datetime, provider: str,
        remote_message_id: str, recipient_digest: str,
    ) -> None:
        del now
        self.store.complete_delivery(
            lease, state=DeliveryState.DELIVERED, provider=provider,
            remote_message_id=remote_message_id, recipient_digest=recipient_digest,
        )
