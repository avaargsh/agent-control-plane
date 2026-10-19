"""Shared fail-closed GitHub webhook envelope validation for SQLite and PG.

This module never treats webhook body or PR prose as a Goal/authority grant.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from uuid import UUID

MAX_BODY = 262_144
SIGNATURE = re.compile(r"^sha256=([0-9a-fA-F]{64})$")
ACTIONS = frozenset({
    "opened", "reopened", "synchronize", "closed", "ready_for_review",
    "converted_to_draft",
})


class WebhookRejected(ValueError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def verified_delivery(
    *, secret: bytes, repo: str, number: int, signature: str, event: str,
    delivery: str, body: bytes,
) -> str | None:
    """Return canonical delivery UUID if signed event targets this PR.

    None means an authenticated but irrelevant event; no storage mutation.
    Invalid signatures are rejected before JSON parsing and DB operations.
    """
    if type(body) is not bytes or not 1 <= len(body) <= MAX_BODY:
        raise WebhookRejected(413, "invalid webhook body size")
    if not isinstance(signature, str):
        raise WebhookRejected(403, "invalid webhook signature")
    match = SIGNATURE.fullmatch(signature)
    expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
    if not match or not hmac.compare_digest(match.group(1).lower(), expected):
        raise WebhookRejected(403, "invalid webhook signature")
    try:
        canonical = str(UUID(delivery))
    except (ValueError, TypeError, AttributeError) as exc:
        raise WebhookRejected(400, "invalid delivery identity") from exc
    if canonical != delivery:
        raise WebhookRejected(400, "invalid delivery identity")
    if event != "pull_request":
        return None
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeError) as exc:
        raise WebhookRejected(400, "invalid webhook JSON") from exc
    if not isinstance(payload, dict):
        raise WebhookRejected(400, "invalid webhook payload")
    source = payload.get("repository")
    pull = payload.get("pull_request")
    action = payload.get("action")
    received_number = payload.get("number")
    if (not isinstance(source, dict) or not isinstance(pull, dict)
            or not isinstance(action, str)
            or type(received_number) is not int
            or type(pull.get("number")) is not int):
        raise WebhookRejected(400, "invalid PR event shape")
    if action not in ACTIONS:
        return None
    if (source.get("full_name") != repo
            or received_number != number or pull["number"] != number):
        return None
    return canonical


def wake_signal_id(user_id: str, goal_id: str, delivery: str) -> str:
    return "pr-watch-webhook:" + hashlib.sha256(
        json.dumps([user_id, goal_id, delivery], separators=(",", ":"))
        .encode("utf-8")
    ).hexdigest()
