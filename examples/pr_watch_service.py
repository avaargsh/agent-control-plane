"""Loopback-only authenticated PR-watch pilot API.

No public/production AuthN claims, no Github write routes, no implicit polling.
Bearer credentials map to canonical users from trusted server configuration;
the caller cannot supply a user_id in an API payload.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from pr_watch_harness import GoalStore, WatchConflict, WatchError
from pr_watch_control_bridge import TemporalControlBridge

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
_BEARER = re.compile(r"^Bearer ([A-Za-z0-9._~+/-]{32,256})$")
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


class PilotConfigError(ValueError):
    pass


class LocalTokens:
    """Fixed local pilot credentials; NOT OIDC or enterprise authorization."""

    def __init__(self, user_tokens: dict[str, str]) -> None:
        if not isinstance(user_tokens, dict) or not user_tokens:
            raise PilotConfigError("a nonempty user/token mapping is required")
        self._digests: list[tuple[bytes, str]] = []
        seen = set()
        for user, token in user_tokens.items():
            if not isinstance(user, str) or not _ID.fullmatch(user):
                raise PilotConfigError("invalid canonical user identity")
            if not isinstance(token, str) or not _BEARER.fullmatch(f"Bearer {token}"):
                raise PilotConfigError("each bearer token must be at least 32 characters")
            digest = hashlib.sha256(token.encode("ascii")).digest()
            if digest in seen:
                raise PilotConfigError("one bearer token cannot grant multiple identities")
            seen.add(digest)
            self._digests.append((digest, user))

    def resolve(self, authorization_values: list[str]) -> str | None:
        if len(authorization_values) != 1:
            return None
        match = _BEARER.fullmatch(authorization_values[0])
        if not match:
            return None
        digest = hashlib.sha256(match.group(1).encode("ascii")).digest()
        # Compare every stored digest rather than returning on the first match.
        matches = [
            user for candidate, user in self._digests
            if hmac.compare_digest(candidate, digest)
        ]
        return matches[0] if len(matches) == 1 else None


def _cursor(value: str) -> int:
    if not value.isdecimal() or len(value) > 18:
        raise WatchError("invalid event cursor")
    return int(value)


class PilotService:
    def __init__(
        self, db_path: str | Path, tokens: LocalTokens,
        bridge: TemporalControlBridge | None = None,
    ) -> None:
        self.goals = GoalStore(db_path)
        self.tokens = tokens
        self.bridge = bridge

    def signal_committed(self, *, user_id: str, goal_id: str, version: int) -> dict:
        if self.bridge is None:
            return self.goals.record_control_signal(
                user_id=user_id, goal_id=goal_id, version=version,
                state="LOCAL_ONLY", evidence="BRIDGE_DISABLED",
            )
        try:
            return self.bridge.signal_committed(
                self.goals, user_id=user_id, goal_id=goal_id, version=version,
            )
        except Exception:
            # The CAS commit already succeeded. An ACK/DB failure after
            # Temporal accepted the Signal must not become an HTTP retry
            # which issues a second state transition or a phantom failure.
            return self.goals.control(
                user_id=user_id, goal_id=goal_id, version=version,
            )

    def reconcile(self, *, user_id: str, goal_id: str, version: int) -> dict:
        if self.bridge is None:
            return self.goals.control(
                user_id=user_id, goal_id=goal_id, version=version,
            )
        try:
            return self.bridge.reconcile(
                self.goals, user_id=user_id, goal_id=goal_id, version=version,
            )
        except Exception:
            return self.goals.control(
                user_id=user_id, goal_id=goal_id, version=version,
            )


class Handler(BaseHTTPRequestHandler):
    server_version = "PRWatchPilot/0.1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        # Avoid logging paths, Authorization or user identifiers from raw requests.
        pass

    def _headers(self, content_type: str, content_length: int) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(content_length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", (
            "default-src 'none'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"
        ))
        self.end_headers()

    def _data(self, status: int, data: bytes, content_type: str) -> None:
        self.send_response(status)
        self._headers(content_type, len(data))
        self.wfile.write(data)

    def _json(self, status: int, value: object) -> None:
        data = json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")
        self._data(status, data, "application/json; charset=utf-8")

    def _identity(self) -> str | None:
        allowed_hosts = {
            "127.0.0.1:" + str(self.server.server_port),
            "localhost:" + str(self.server.server_port),
        }
        if self.headers.get("Host") not in allowed_hosts:
            self._json(403, {"error": "untrusted Host"})
            return None
        user = self.server.service.tokens.resolve(self.headers.get_all("Authorization", []))
        if user is None:
            self._json(401, {"error": "authentication required"})
        return user

    def _body(self) -> dict:
        if self.headers.get("Transfer-Encoding") is not None:
            raise WatchError("chunked requests are not accepted")
        values = self.headers.get_all("Content-Length", [])
        if len(values) != 1 or not values[0].isdecimal():
            raise WatchError("a single content length is required")
        size = int(values[0])
        if size == 0 or size > 4096:
            raise WatchError("JSON body must be 1..4096 bytes")
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            raise WatchError("Content-Type must be application/json")
        try:
            data = json.loads(self.rfile.read(size))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise WatchError("invalid JSON body") from exc
        if not isinstance(data, dict):
            raise WatchError("JSON object is required")
        return data

    def _route(self) -> tuple[str, dict[str, list[str]]]:
        parts = urlsplit(self.path)
        if "%" in parts.path:
            decoded = unquote(parts.path)
            if decoded != parts.path or "%" in decoded:
                # Path encoding is unnecessary and can mask authorization scope.
                raise WatchError("encoded paths are not accepted")
        return parts.path, parse_qs(parts.query, keep_blank_values=True)

    def _events(self, user: str, query: dict[str, list[str]]) -> list[dict]:
        if set(query) - {"after"} or len(query.get("after", ["0"])) != 1:
            raise WatchError("invalid event query")
        last = self.headers.get("Last-Event-ID")
        after = _cursor(last if last is not None else query.get("after", ["0"])[0])
        return self.server.service.goals.list_events(user_id=user, after=after)

    def do_GET(self) -> None:
        try:
            path, query = self._route()
            if path in ("/", "/pr-watch-panel.js", "/pr-watch-panel.css"):
                if query:
                    raise WatchError("unexpected query")
                filename = {
                    "/": "pr_watch_panel.html",
                    "/pr-watch-panel.js": "pr_watch_panel.js",
                    "/pr-watch-panel.css": "pr_watch_panel.css",
                }[path]
                mime = ("text/html" if path == "/" else
                        "application/javascript" if path.endswith(".js") else "text/css")
                self._data(200, Path(__file__).with_name(filename).read_bytes(), mime)
                return
            if path == "/healthz":
                self._json(200, {"status": "ok"})
                return
            user = self._identity()
            if user is None:
                return
            service = self.server.service
            if path == "/api/goals":
                if query:
                    raise WatchError("unexpected query")
                self._json(200, {"goals": [
                    asdict(goal) for goal in service.goals.list_goals(user_id=user)
                ]})
                return
            if path == "/api/events":
                self._json(200, {"events": self._events(user, query)})
                return
            if path == "/api/events/stream":
                events = self._events(user, query)
                lines = ["retry: 5000\n"]
                for event in events:
                    lines.append(f"id: {event['cursor']}\n")
                    lines.append("event: pr-watch\n")
                    lines.append("data: " + json.dumps(event, ensure_ascii=False) + "\n\n")
                lines.append(": snapshot-complete\n\n")
                self._data(200, "".join(lines).encode("utf-8"), "text/event-stream; charset=utf-8")
                return
            control_list = re.fullmatch(
                r"/api/goals/([A-Za-z0-9][A-Za-z0-9_.-]{0,95})/controls", path
            )
            if control_list:
                if query:
                    raise WatchError("unexpected query")
                self._json(200, {"controls": service.goals.controls(
                    user_id=user, goal_id=control_list.group(1),
                )})
                return
            match = re.fullmatch(r"/api/goals/([A-Za-z0-9][A-Za-z0-9_.-]{0,95})", path)
            if match:
                self._json(200, {"goal": asdict(service.goals.get(
                    user_id=user, goal_id=match.group(1)
                ))})
                return
            self._json(404, {"error": "not found"})
        except WatchError as exc:
            status = 404 if "not found within user scope" in str(exc) else 400
            self._json(status, {"error": str(exc)})

    def do_POST(self) -> None:
        try:
            path, query = self._route()
            user = self._identity()
            if user is None:
                return
            if query:
                raise WatchError("unexpected query")
            # Loopback-only and no CORS. Block cross-origin browser writes.
            origin = self.headers.get("Origin")
            trusted_origins = {
                "http://127.0.0.1:" + str(self.server.server_port),
                "http://localhost:" + str(self.server.server_port),
            }
            if origin is not None and origin not in trusted_origins:
                raise WatchError("cross-origin writes are not allowed")
            data = self._body()
            store = self.server.service.goals
            if path == "/api/goals":
                if set(data) != {"goal_id", "repo", "number"}:
                    raise WatchError("goal_id, repo and number are required")
                if not isinstance(data["goal_id"], str) or not _ID.fullmatch(data["goal_id"]):
                    raise WatchError("invalid goal ID")
                if type(data["number"]) is not int or not isinstance(data["repo"], str):
                    raise WatchError("invalid PR selector")
                goal = store.register(user_id=user, goal_id=data["goal_id"],
                                      repo=data["repo"], number=data["number"])
                self._json(200, {"goal": asdict(goal)})
                return
            reconcile_match = re.fullmatch(
                r"/api/goals/([A-Za-z0-9][A-Za-z0-9_.-]{0,95})/controls/"
                r"([0-9]{1,12})/reconcile", path
            )
            if reconcile_match:
                if data:
                    raise WatchError("reconcile requires an empty JSON object")
                record = self.server.service.reconcile(
                    user_id=user, goal_id=reconcile_match.group(1),
                    version=int(reconcile_match.group(2)),
                )
                self._json(200, {"control": record})
                return
            match = re.fullmatch(
                r"/api/goals/([A-Za-z0-9][A-Za-z0-9_.-]{0,95})/(pause|resume|cancel)", path
            )
            if match:
                if set(data) != {"expected_version"}:
                    raise WatchError("expected_version is required")
                goal = store.transition(
                    user_id=user, goal_id=match.group(1), action=match.group(2),
                    expected_version=data["expected_version"],
                )
                control = self.server.service.signal_committed(
                    user_id=user, goal_id=goal.goal_id, version=goal.version,
                )
                self._json(
                    200 if control["signal_state"] == "ACKED" else 202,
                    {"goal": asdict(goal), "control": control},
                )
                return
            self._json(404, {"error": "not found"})
        except WatchConflict as exc:
            self._json(409, {"error": str(exc)})
        except WatchError as exc:
            status = 404 if "not found within user scope" in str(exc) else 400
            self._json(status, {"error": str(exc)})


class PilotHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], service: PilotService) -> None:
        if address[0] not in _LOOPBACK:
            raise PilotConfigError("pilot API must bind to loopback only")
        self.service = service
        super().__init__(address, Handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Local PR-watch task API pilot")
    parser.add_argument("--db", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8792)
    args = parser.parse_args()
    raw = os.environ.get("PR_WATCH_LOCAL_TOKENS_JSON", "")
    try:
        tokens = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit("Configure PR_WATCH_LOCAL_TOKENS_JSON with local user/token map") from exc
    address = os.environ.get("PR_WATCH_TEMPORAL_ADDRESS")
    bridge = (
        TemporalControlBridge(
            address=address, namespace=os.getenv("PR_WATCH_TEMPORAL_NAMESPACE", "default"),
        ) if address else None
    )
    server = PilotHTTPServer(
        (args.host, args.port), PilotService(args.db, LocalTokens(tokens), bridge),
    )
    print(f"Local PR-watch pilot listening at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
