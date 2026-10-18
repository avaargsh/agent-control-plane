"""Opt-in, one-shot Telegram delivery adapter for reference AttentionOutbox.

A Telegram sendMessage API receipt confirms Telegram accepted a message, NOT
that the user read it. Any ambiguous HTTP/provider or DB-ACK result is UNKNOWN
and must not be retried without independent provider absence evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from attention_plane import AttentionOutbox, DeliveryLease

_USER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
_TOKEN = re.compile(r"^[0-9]+:[A-Za-z0-9_-]{20,}$")


class DeliveryConfigurationError(ValueError):
    """Local operator configuration invalid; no provider call attempted."""


class TransportUnknown(RuntimeError):
    """Send may or may not have been accepted; DO NOT automatically retry."""


@dataclass(frozen=True)
class ProviderReceipt:
    remote_message_id: str
    recipient_digest: str


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class TelegramAdapter:
    """Static trusted recipient binding. Not production OIDC/recipient proof."""

    def __init__(
        self, *, token: str, recipients: dict[str, int],
        http_client=None, timeout_seconds: float = 8.0,
    ) -> None:
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            raise DeliveryConfigurationError("invalid Telegram bot credential format")
        if not isinstance(recipients, dict) or not recipients:
            raise DeliveryConfigurationError("missing trusted user/recipient bindings")
        for user_id, chat_id in recipients.items():
            if (
                not isinstance(user_id, str) or not _USER.fullmatch(user_id)
                or type(chat_id) is not int or chat_id == 0
            ):
                raise DeliveryConfigurationError("invalid trusted recipient binding")
        if not 0 < timeout_seconds <= 30:
            raise DeliveryConfigurationError("invalid transport timeout")
        self._token = token
        self._recipients = dict(recipients)
        self._timeout = timeout_seconds
        self._http = http_client if http_client is not None else urllib.request.build_opener(_NoRedirect)

    def require_recipient(self, user_id: str) -> int:
        """Call before claiming; an unauthorized user must not mutate outbox."""
        if user_id not in self._recipients:
            raise DeliveryConfigurationError("recipient not configured for user")
        return self._recipients[user_id]

    def send(self, lease: DeliveryLease) -> ProviderReceipt:
        chat_id = self.require_recipient(lease.user_id)
        if not isinstance(lease.summary, str) or not 1 <= len(lease.summary) <= 3000:
            raise TransportUnknown("message validation failed; manual triage required")
        # Fixed Telegram origin; bot token only appears in request path, never in
        # structured logs or exception strings surfaced by this adapter.
        url = "https://api.telegram.org/bot" + self._token + "/sendMessage"
        payload = json.dumps(
            {"chat_id": chat_id, "text": lease.summary},
            ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "acp-attention-telegram-reference/0.1",
            },
            method="POST",
        )
        try:
            with self._http.open(req, timeout=self._timeout) as response:
                if response.status != 200:
                    raise TransportUnknown("Telegram acceptance not verified")
                blob = response.read(65_537)
                if len(blob) > 65_536:
                    raise TransportUnknown("Telegram receipt was oversized")
                data = json.loads(blob)
            if not isinstance(data, dict) or data.get("ok") is not True:
                raise TransportUnknown("Telegram acceptance not verified")
            result = data.get("result")
            if not isinstance(result, dict) or not isinstance(result.get("chat"), dict):
                raise TransportUnknown("Telegram receipt structure unverified")
            message_id = result.get("message_id")
            remote_chat = result["chat"].get("id")
            if type(message_id) is not int or message_id <= 0 or (
                type(remote_chat) is not int or remote_chat != chat_id
            ):
                raise TransportUnknown("Telegram recipient or receipt mismatch")
        except Exception:
            # HTTPError may embed URL containing bot token. Scrub both message and cause.
            raise TransportUnknown("Telegram send outcome unverified") from None
        digest = hashlib.sha256(
            ("telegram:chat:" + str(chat_id)).encode("utf-8")
        ).hexdigest()
        return ProviderReceipt(str(message_id), digest)


def dispatch_once(
    outbox: AttentionOutbox, adapter: TelegramAdapter,
    *, user_id: str, owner: str, now: datetime,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    limit: int = 10,
) -> dict[str, int]:
    """One scoped batch, no automatic retries for uncertain outcomes."""
    adapter.require_recipient(user_id)
    if not owner or not 1 <= limit <= 20:
        raise DeliveryConfigurationError("invalid worker owner or batch limit")
    expired = outbox.recover_expired(now=now, user_id=user_id)
    # Only PENDING can be claimed. UNKNOWN is never eligible here.
    leases = outbox.claim_due(
        user_id=user_id, owner=owner, now=now,
        limit=limit, lease_seconds=90,
    )
    accepted = unknown = 0
    for lease in leases:
        try:
            receipt = adapter.send(lease)
        except Exception:
            unknown += 1
            try:
                outbox.mark_unknown(lease, now=clock())
            except Exception:
                # A lost/expired lease still becomes UNKNOWN at recovery, never
                # PENDING. Do not claim success or retry this delivery.
                pass
            continue
        try:
            outbox.mark_delivered_with_receipt(
                lease, now=clock(), provider="telegram",
                remote_message_id=receipt.remote_message_id,
                recipient_digest=receipt.recipient_digest,
            )
            accepted += 1
        except Exception:
            unknown += 1
            try:
                outbox.mark_unknown(lease, now=clock())
            except Exception:
                pass
    return {
        "claimed": len(leases),
        "accepted_by_provider": accepted,
        "uncertain": unknown,
        "expired_to_unknown": expired,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Explicit Telegram outbox dispatcher; never automatic unknown retries",
    )
    parser.add_argument("--db", help="SQLite database path (sqlite mode only)")
    parser.add_argument("--user", required=True)
    parser.add_argument("--owner", default="local-telegram-1")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    backend = os.getenv("PR_WATCH_STORAGE", "sqlite")
    pg_dsn = os.getenv("PR_WATCH_PG_DSN")
    if backend == "postgres":
        if args.db or os.getenv("PR_WATCH_DB") or not pg_dsn:
            parser.error("PostgreSQL requires PG DSN and forbids SQLite paths")
        from pr_watch_pg_runtime import PGTelegramOutbox
        from pr_watch_pg_store import PostgresWatchStore
        store = PostgresWatchStore(pg_dsn)
        store.validate_v2()
        outbox = PGTelegramOutbox(store)
    elif backend == "sqlite":
        if not args.db or pg_dsn:
            parser.error("SQLite requires --db and forbids PR_WATCH_PG_DSN")
        outbox = AttentionOutbox(Path(args.db))
    else:
        parser.error("PR_WATCH_STORAGE must be sqlite or postgres")

    token = os.environ.get("PR_WATCH_TELEGRAM_BOT_TOKEN", "")
    try:
        recipients = json.loads(
            os.environ.get("PR_WATCH_TELEGRAM_RECIPIENTS_JSON", "{}")
        )
    except ValueError as exc:
        raise SystemExit("Invalid trusted Telegram binding JSON") from exc
    adapter = TelegramAdapter(token=token, recipients=recipients)
    output = dispatch_once(
        outbox, adapter,
        user_id=args.user, owner=args.owner, now=datetime.now(timezone.utc),
        limit=args.limit,
    )
    print(json.dumps(output, sort_keys=True))


if __name__ == "__main__":
    main()
