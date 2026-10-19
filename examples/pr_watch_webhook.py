"""Opt-in loopback GitHub webhook -> durable, scoped Temporal PR Watch wake.

An authenticated webhook is a scheduling hint, never a provider observation,
approval or authority to write to GitHub. SQLite-only single-host reference.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pr_watch_control_bridge import workflow_id
from pr_watch_harness import GoalStore, WatchError

from pr_watch_webhook_envelope import (
    MAX_BODY, WebhookRejected, verified_delivery, wake_signal_id,
)


class TemporalWebhookWake:
    """One Signal RPC. Never start/restart a workflow or retry an uncertain ACK."""

    def __init__(self, address: str, namespace: str, user_id: str, goal_id: str):
        if not all((address, namespace, user_id, goal_id)):
            raise ValueError("Temporal webhook wake requires a trusted workflow binding")
        self.address, self.namespace = address, namespace
        self.workflow = workflow_id(user_id=user_id, goal_id=goal_id)

    def __call__(self, signal_id: str) -> None:
        async def send() -> None:
            from temporalio.client import Client

            client = await Client.connect(self.address, namespace=self.namespace)
            await client.get_workflow_handle(self.workflow).signal("wake", signal_id)

        asyncio.run(send())

    def prove(self, signal_id: str) -> bool:
        """Read-only positive proof in the exact deterministic Workflow History."""
        async def inspect() -> bool:
            from temporalio.client import Client

            client = await Client.connect(self.address, namespace=self.namespace)
            handle = client.get_workflow_handle(self.workflow)
            history = await handle.fetch_history()
            for event in history.events:
                attrs = event.workflow_execution_signaled_event_attributes
                if attrs.signal_name != "wake" or not attrs.input.payloads:
                    continue
                try:
                    decoded = await client.data_converter.decode(attrs.input.payloads)
                except Exception:
                    continue
                if decoded == [signal_id]:
                    return True
            return False

        return asyncio.run(inspect())


class GitHubWebhookIngress:
    """One immutable (user, goal, repository, PR) binding, never payload routing."""

    def __init__(
        self, *, store: GoalStore, user_id: str, goal_id: str,
        secret: bytes, signal,
    ) -> None:
        if type(secret) is not bytes or len(secret) < 32 or not callable(signal):
            raise ValueError("webhook secret >=32 bytes and signal transport are required")
        goal = store.get(user_id=user_id, goal_id=goal_id)
        self.store, self.user_id, self.goal_id = store, user_id, goal_id
        self.repo, self.number = goal.repo, goal.number
        self._secret, self._signal = secret, signal
        with self._db() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS pr_watch_webhook_wakes (
                    user_id TEXT NOT NULL, goal_id TEXT NOT NULL,
                    delivery TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
                    signal_id TEXT NOT NULL, state TEXT NOT NULL
                      CHECK (state IN ('IN_FLIGHT','ACKED','UNKNOWN')),
                    PRIMARY KEY (user_id, goal_id, delivery)
                )
            """)

    def _db(self):
        db = sqlite3.connect(self.store.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def accept(self, *, signature: str, event: str, delivery: str,
               body: bytes) -> dict:
        delivery = verified_delivery(
            secret=self._secret, repo=self.repo, number=self.number,
            signature=signature, event=event, delivery=delivery, body=body,
        )
        if delivery is None:
            return {"status": "ignored"}
        digest = hashlib.sha256(body).hexdigest()
        signal_id = wake_signal_id(self.user_id, self.goal_id, delivery)
        # Source of truth stays the durable Goal row. Paused/closed tasks do
        # not acquire an event wake merely because a webhook was signed.
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT repo, number, state FROM pr_watch_goals "
                "WHERE user_id=? AND goal_id=?",
                (self.user_id, self.goal_id),
            ).fetchone()
            if (row is None or row["repo"] != self.repo
                    or row["number"] != self.number):
                return {"status": "ignored"}
            # Return durable prior evidence even when the first wake has
            # already completed the Goal. Terminal state must not obscure
            # a duplicate ACK (or allow a second Signal attempt).
            prior = db.execute(
                "SELECT payload_sha256,state FROM pr_watch_webhook_wakes "
                "WHERE user_id=? AND goal_id=? AND delivery=?",
                (self.user_id, self.goal_id, delivery),
            ).fetchone()
            if prior:
                if prior["payload_sha256"] != digest:
                    raise WebhookRejected(409, "delivery identity reused")
                return {"status": "duplicate", "signal_state": prior["state"]}
            if row["state"] != "RUNNING":
                return {"status": "ignored"}
            db.execute(
                "INSERT INTO pr_watch_webhook_wakes "
                "(user_id,goal_id,delivery,payload_sha256,signal_id,state) "
                "VALUES (?,?,?,?,?,'IN_FLIGHT')",
                (self.user_id, self.goal_id, delivery, digest, signal_id),
            )
        # The journal is durable before the remote RPC. An uncertain send
        # must not be repeated; IN_FLIGHT after process death is also unsafe.
        try:
            self._signal(signal_id)
            state = "ACKED"
        except Exception:
            state = "UNKNOWN"
        with self._db() as db:
            db.execute(
                "UPDATE pr_watch_webhook_wakes SET state=? "
                "WHERE user_id=? AND goal_id=? AND delivery=? "
                "AND state='IN_FLIGHT'",
                (state, self.user_id, self.goal_id, delivery),
            )
        return {"status": "accepted", "signal_state": state}

    def reconcile(self, *, delivery: str, prove) -> dict:
        """Positive proof only; uncertainty is never permission to resend."""
        from uuid import UUID

        try:
            canonical = str(UUID(delivery))
        except (TypeError, ValueError, AttributeError) as exc:
            raise WebhookRejected(400, "invalid delivery identity") from exc
        if canonical != delivery or not callable(prove):
            raise WebhookRejected(400, "invalid reconciliation request")
        with self._db() as db:
            row = db.execute(
                "SELECT state,signal_id FROM pr_watch_webhook_wakes "
                "WHERE user_id=? AND goal_id=? AND delivery=?",
                (self.user_id, self.goal_id, delivery),
            ).fetchone()
        if row is None:
            raise WebhookRejected(404, "delivery not found within goal scope")
        if row["state"] == "ACKED":
            return {"signal_state": "ACKED"}
        try:
            proved = prove(row["signal_id"]) is True
        except Exception:
            proved = False
        if not proved:
            return {"signal_state": row["state"]}
        with self._db() as db:
            db.execute(
                "UPDATE pr_watch_webhook_wakes SET state='ACKED' "
                "WHERE user_id=? AND goal_id=? AND delivery=? "
                "AND signal_id=? AND state IN ('IN_FLIGHT','UNKNOWN')",
                (self.user_id, self.goal_id, delivery, row["signal_id"]),
            )
            current = db.execute(
                "SELECT state FROM pr_watch_webhook_wakes "
                "WHERE user_id=? AND goal_id=? AND delivery=?",
                (self.user_id, self.goal_id, delivery),
            ).fetchone()
        if current is None:
            raise WebhookRejected(404, "delivery not found within goal scope")
        return {"signal_state": current["state"]}


class WebhookHandler(BaseHTTPRequestHandler):
    server_version = "PRWatchWebhook/0.1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        # Headers, signed payload and user/Goal selectors are never logged.
        pass

    def _reply(self, code: int, value: dict) -> None:
        raw = json.dumps(value, sort_keys=True).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        # Close after each response, including early validation failures
        # where the untrusted request body was intentionally not consumed.
        self.send_header("Connection", "close")
        self.close_connection = True
        self.end_headers()
        self.wfile.write(raw)

    def _one(self, key: str) -> str:
        vals = self.headers.get_all(key, [])
        if len(vals) != 1:
            raise WebhookRejected(400, "missing or duplicate webhook header")
        return vals[0]

    def do_POST(self):
        try:
            if self.path != "/webhooks/github":
                self._reply(404, {"error": "not found"})
                return
            if self._one("Host") not in {
                "127.0.0.1:" + str(self.server.server_port),
                "localhost:" + str(self.server.server_port),
            } or self.headers.get("Origin") is not None:
                raise WebhookRejected(403, "untrusted webhook origin")
            if (self.headers.get("Transfer-Encoding") is not None
                    or self._one("Content-Type").split(";", 1)[0].strip()
                    != "application/json"):
                raise WebhookRejected(400, "invalid webhook encoding")
            size_text = self._one("Content-Length")
            if not size_text.isascii() or not size_text.isdecimal():
                raise WebhookRejected(400, "invalid webhook length")
            size = int(size_text)
            if not 1 <= size <= MAX_BODY:
                raise WebhookRejected(413, "webhook body exceeds limit")
            signature = self._one("X-Hub-Signature-256")
            event = self._one("X-GitHub-Event")
            delivery = self._one("X-GitHub-Delivery")
            body = self.rfile.read(size)
            if len(body) != size:
                raise WebhookRejected(400, "incomplete webhook body")
            outcome = self.server.ingress.accept(
                signature=signature, event=event, delivery=delivery, body=body,
            )
            self._reply(200 if outcome["status"] == "duplicate" else 202, outcome)
        except WebhookRejected as exc:
            self._reply(exc.status, {"error": str(exc)})
        except Exception:
            # Do not disclose SQLite/Temporal errors or credentials.
            self._reply(503, {"error": "webhook temporarily unavailable"})


class WebhookServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], ingress: GitHubWebhookIngress):
        if address[0] not in ("127.0.0.1", "localhost"):
            raise ValueError("webhook must bind to loopback; use a trusted proxy")
        self.ingress = ingress
        super().__init__(address, WebhookHandler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Opt-in local GitHub PR wake ingress")
    parser.add_argument("--db", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--goal", required=True)
    parser.add_argument("--port", type=int, default=8793)
    parser.add_argument("--temporal-address", required=True)
    parser.add_argument("--temporal-namespace", default="default")
    args = parser.parse_args()
    if os.getenv("PR_WATCH_STORAGE", "sqlite") != "sqlite" or os.getenv("PR_WATCH_PG_DSN"):
        parser.error("webhook pilot is SQLite-only; never mix PostgreSQL")
    secret = os.getenv("PR_WATCH_WEBHOOK_SECRET", "").encode("utf-8")
    try:
        store = GoalStore(args.db)
        wake = TemporalWebhookWake(
            args.temporal_address, args.temporal_namespace, args.user, args.goal,
        )
        ingress = GitHubWebhookIngress(
            store=store, user_id=args.user, goal_id=args.goal,
            secret=secret, signal=wake,
        )
        server = WebhookServer(("127.0.0.1", args.port), ingress)
    except (ValueError, WatchError) as exc:
        parser.error(str(exc))
    print("PR Watch signed webhook ingress on loopback; no GitHub write access")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
