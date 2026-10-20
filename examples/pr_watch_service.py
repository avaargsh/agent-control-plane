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
from dataclasses import asdict, is_dataclass
from datetime import datetime
from uuid import uuid4
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from pr_watch_harness import GoalStore, WatchConflict, WatchError
from pr_watch_control_bridge import TemporalControlBridge
from pr_watch_launcher import TemporalLaunchBridge
from pr_watch_task_projection import project_task


def _goal_payload(value) -> dict:
    """Both SQLite dataclass and PostgreSQL dict represent the same API."""
    return asdict(value) if is_dataclass(value) else dict(value)


def _control_receipt(value: dict) -> dict:
    """Stable identity derived only from an already committed CAS journal row.

    The receipt ID is not an idempotency key and does not prove signal
    delivery, workflow completion or which HTTP attempt produced the row.
    """
    record = dict(value)
    identity = json.dumps(
        [record["user_id"], record["goal_id"], record["version"], record["action"]],
        ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")
    record["receipt_id"] = "prc_" + hashlib.sha256(identity).hexdigest()[:32]
    record["cas_committed"] = True
    return record


def _json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError("unsupported JSON response value")

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
_BEARER = re.compile(r"^Bearer ([A-Za-z0-9._~+/-]{32,256})$")
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def _has_prepared_control_schema(service) -> bool:
    if service.storage_backend != "postgres":
        return True
    from pr_watch_pg_migrations import PostgresMigrations
    return PostgresMigrations(service.goals._dsn).status().version >= 3


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
        launch_bridge: TemporalLaunchBridge | None = None,
        *, storage_backend: str = "sqlite", pg_dsn: str | None = None,
    ) -> None:
        if storage_backend not in ("sqlite", "postgres"):
            raise PilotConfigError("unsupported PR Watch storage backend")
        self.storage_backend = storage_backend
        self._launch_owner = "api-" + uuid4().hex
        self.pg_launcher = None
        if storage_backend == "postgres":
            if db_path is not None or not pg_dsn:
                raise PilotConfigError("PostgreSQL mode forbids SQLite DB path")
            # Import opt-in psycopg dependency ONLY in PostgreSQL mode.
            from pr_watch_pg_store import PostgresWatchStore
            self.goals = PostgresWatchStore(pg_dsn)
            self.goals.validate_v2()  # fail closed; never auto-install tables
            if launch_bridge is not None:
                from pr_watch_pg_runtime import PGLaunchService
                self.pg_launcher = PGLaunchService(self.goals, launch_bridge)
        else:
            if db_path is None or pg_dsn:
                raise PilotConfigError("SQLite mode requires only --db")
            self.goals = GoalStore(db_path)
        self.tokens = tokens
        self.bridge = bridge
        self.launch_bridge = launch_bridge

    def start_committed(self, *, user_id: str, goal_id: str) -> dict:
        if self.launch_bridge is None:
            return {
                **self.goals.launch(user_id=user_id, goal_id=goal_id),
                "mode": "LOCAL_ONLY",
            }
        try:
            if self.pg_launcher is not None:
                return self.pg_launcher.start_committed(
                    user_id=user_id, goal_id=goal_id, owner=self._launch_owner,
                )
            return self.launch_bridge.start_committed(
                self.goals, user_id=user_id, goal_id=goal_id,
            )
        except Exception:
            # Goal and launch record already committed: never tell the client
            # it needs to re-register to repair an uncertain provider start.
            return self.goals.launch(user_id=user_id, goal_id=goal_id)

    def reconcile_launch(self, *, user_id: str, goal_id: str) -> dict:
        if self.launch_bridge is None:
            return self.start_committed(user_id=user_id, goal_id=goal_id)
        try:
            if self.pg_launcher is not None:
                return self.pg_launcher.reconcile(
                    user_id=user_id, goal_id=goal_id,
                )
            return self.launch_bridge.reconcile(
                self.goals, user_id=user_id, goal_id=goal_id,
            )
        except Exception:
            return self.goals.launch(user_id=user_id, goal_id=goal_id)

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

    def dispatch_unattempted(
        self, *, user_id: str, goal_id: str, version: int,
    ) -> dict:
        """Explicit recovery ONLY for a new keyed CAS not yet send-claimed."""
        current = self.goals.control(
            user_id=user_id, goal_id=goal_id, version=version,
        )
        if (current["signal_state"] != "PENDING"
            or current["evidence"] != "UNATTEMPTED_V3"):
            raise WatchConflict("control is not proven unattempted; read-only reconcile")
        if self.bridge is None:
            raise WatchConflict("trusted Temporal control bridge not configured")
        # A second caller racing this one is fenced by a DB claim before RPC.
        try:
            return self.bridge.signal_committed(
                self.goals, user_id=user_id, goal_id=goal_id, version=version,
                unattempted_only=True,
            )
        except Exception:
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
        data = json.dumps(
            value, sort_keys=True, ensure_ascii=False, default=_json_default,
        ).encode("utf-8")
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
            if path == "/readyz":
                if query:
                    raise WatchError("unexpected query")
                # Liveness is not database readiness. In opt-in PG mode,
                # query the authoritative schema ledger rather than
                # incorrectly claiming healthy service on a dead backend.
                service = self.server.service
                if service.storage_backend == "postgres":
                    try:
                        service.goals.validate_v2()
                    except Exception:
                        # Deliberately no DSN, driver error or credentials in
                        # an unauthenticated readiness response.
                        self._json(503, {"status": "unavailable"})
                        return
                self._json(200, {"status": "ready"})
                return
            user = self._identity()
            if user is None:
                return
            service = self.server.service
            if path == "/api/control-requests":
                if set(query) - {"limit"} or len(query.get("limit", ["100"])) != 1:
                    raise WatchError("invalid control request query")
                raw_limit = query.get("limit", ["100"])[0]
                if not re.fullmatch(r"[1-9][0-9]{0,2}", raw_limit) or int(raw_limit) > 100:
                    raise WatchError("invalid control request limit")
                if not _has_prepared_control_schema(service):
                    self._json(501, {"error": "operator must install PostgreSQL schema v3"})
                    return
                self._json(200, {
                    "schema": "pr-watch-control-requests/v1",
                    "requests": service.goals.list_control_requests(
                        user_id=user, limit=int(raw_limit),
                    ),
                })
                return
            request_lookup = re.fullmatch(
                r"/api/control-requests/([0-9a-f]{32})", path
            )
            if request_lookup:
                if query:
                    raise WatchError("unexpected query")
                if not _has_prepared_control_schema(service):
                    self._json(501, {"error": "operator must install PostgreSQL schema v3"})
                    return
                self._json(200, {
                    "request": service.goals.control_request(
                        user_id=user, request_id=request_lookup.group(1),
                    ),
                })
                return
            if path == "/api/control-receipts":
                if set(query) - {"limit"} or len(query.get("limit", ["100"])) != 1:
                    raise WatchError("invalid control receipt query")
                raw_limit = query.get("limit", ["100"])[0]
                if not re.fullmatch(r"[1-9][0-9]{0,2}", raw_limit) or int(raw_limit) > 100:
                    raise WatchError("invalid control receipt limit")
                # Owner scope originates exclusively from the bearer token.
                controls = service.goals.list_unsettled_controls(
                    user_id=user, limit=int(raw_limit),
                )
                self._json(200, {
                    "schema": "pr-watch-control-receipts/v1",
                    "controls": [_control_receipt(item) for item in controls],
                })
                return
            if path == "/api/tasks":
                # Filters are applied AFTER the authenticated owner-scoped
                # Goal query and task projection. No caller-supplied identity
                # is accepted, and the v1 response schema remains unchanged.
                allowed = {"state", "repo", "limit"}
                if set(query) - allowed or any(len(values) != 1 for values in query.values()):
                    raise WatchError("invalid task query")
                state = query.get("state", [None])[0]
                repo = query.get("repo", [None])[0]
                limit_raw = query.get("limit", ["100"])[0]
                if state is not None and state not in {
                    "WAITING", "UNKNOWN", "ACTIVE", "PAUSED",
                    "CANCEL_REQUESTED", "OBSERVED_TERMINAL",
                }:
                    raise WatchError("invalid task state filter")
                if repo is not None and (
                    not re.fullmatch(
                        r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}", repo
                    )
                    or repo.startswith(".") or "/." in repo
                ):
                    raise WatchError("invalid task repository filter")
                if not re.fullmatch(r"[1-9][0-9]{0,2}", limit_raw) or int(limit_raw) > 100:
                    raise WatchError("invalid task limit")
                # Bounded source snapshot: limit applies to matched tasks,
                # not the raw Goals, so filter results are deterministic.
                tasks = [
                    project_task(goal, service.goals.launch(
                        user_id=user, goal_id=_goal_payload(goal)["goal_id"],
                    ), principal=user)
                    for goal in service.goals.list_goals(user_id=user)
                ]
                if state is not None:
                    tasks = [task for task in tasks if task["state"] == state]
                if repo is not None:
                    tasks = [task for task in tasks if task["repo"] == repo]
                self._json(200, {
                    "schema": "pr-watch-task-projection/v1",
                    "tasks": tasks[:int(limit_raw)],
                })
                return
            task_match = re.fullmatch(
                r"/api/tasks/([A-Za-z0-9][A-Za-z0-9_.-]{0,95})", path
            )
            if task_match:
                if query:
                    raise WatchError("unexpected query")
                goal_id = task_match.group(1)
                # Scoped goal lookup happens before reading any launch record.
                task = project_task(
                    service.goals.get(user_id=user, goal_id=goal_id),
                    service.goals.launch(user_id=user, goal_id=goal_id),
                    principal=user,
                )
                self._json(200, {"task": task})
                return
            if path == "/api/goals":
                if query:
                    raise WatchError("unexpected query")
                self._json(200, {"goals": [
                    _goal_payload(goal) for goal in service.goals.list_goals(user_id=user)
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
                    lines.append("data: " + json.dumps(event, ensure_ascii=False, default=_json_default) + "\n\n")
                lines.append(": snapshot-complete\n\n")
                self._data(200, "".join(lines).encode("utf-8"), "text/event-stream; charset=utf-8")
                return
            launch_match = re.fullmatch(
                r"/api/goals/([A-Za-z0-9][A-Za-z0-9_.-]{0,95})/launch", path
            )
            if launch_match:
                if query:
                    raise WatchError("unexpected query")
                record = service.goals.launch(
                    user_id=user, goal_id=launch_match.group(1),
                )
                self._json(200, {"launch": record})
                return
            control_detail = re.fullmatch(
                r"/api/goals/([A-Za-z0-9][A-Za-z0-9_.-]{0,95})/controls/"
                r"([1-9][0-9]{0,11})", path
            )
            if control_detail:
                if query:
                    raise WatchError("unexpected query")
                # The store scopes the receipt to this authenticated user.
                record = service.goals.control(
                    user_id=user, goal_id=control_detail.group(1),
                    version=int(control_detail.group(2)),
                )
                self._json(200, {"control": _control_receipt(record)})
                return
            control_list = re.fullmatch(
                r"/api/goals/([A-Za-z0-9][A-Za-z0-9_.-]{0,95})/controls", path
            )
            if control_list:
                if query:
                    raise WatchError("unexpected query")
                self._json(200, {"controls": [_control_receipt(item) for item in service.goals.controls(
                    user_id=user, goal_id=control_list.group(1),
                )]})
                return
            match = re.fullmatch(r"/api/goals/([A-Za-z0-9][A-Za-z0-9_.-]{0,95})", path)
            if match:
                self._json(200, {"goal": _goal_payload(service.goals.get(
                    user_id=user, goal_id=match.group(1)
                ))})
                return
            self._json(404, {"error": "not found"})
        except ValueError as exc:
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
                launch = self.server.service.start_committed(
                    user_id=user, goal_id=_goal_payload(goal)["goal_id"],
                )
                code = 200 if launch["state"] in (
                    "ACKED", "OBSERVED",
                ) or launch.get("mode") == "LOCAL_ONLY" else 202
                self._json(code, {"goal": _goal_payload(goal), "launch": launch})
                return
            launch_reconcile = re.fullmatch(
                r"/api/goals/([A-Za-z0-9][A-Za-z0-9_.-]{0,95})/launch/reconcile", path
            )
            if launch_reconcile:
                if data:
                    raise WatchError("launch reconcile requires an empty JSON object")
                launch = self.server.service.reconcile_launch(
                    user_id=user, goal_id=launch_reconcile.group(1),
                )
                self._json(200, {"launch": launch})
                return
            prepare_match = re.fullmatch(
                r"/api/goals/([A-Za-z0-9][A-Za-z0-9_.-]{0,95})/controls/prepare",
                path,
            )
            if prepare_match:
                if set(data) != {"request_id", "action", "expected_version"}:
                    raise WatchError("request_id, action and expected_version required")
                if not _has_prepared_control_schema(self.server.service):
                    self._json(501, {"error": "operator must install PostgreSQL schema v3"})
                    return
                record = store.prepare_control(
                    user_id=user, goal_id=prepare_match.group(1),
                    request_id=data["request_id"], action=data["action"],
                    expected_version=data["expected_version"],
                )
                self._json(200, {"request": record})
                return
            dispatch_match = re.fullmatch(
                r"/api/goals/([A-Za-z0-9][A-Za-z0-9_.-]{0,95})/controls/"
                r"([1-9][0-9]{0,11})/dispatch-unattempted", path
            )
            if dispatch_match:
                if data:
                    raise WatchError("dispatch-unattempted requires empty JSON object")
                record = self.server.service.dispatch_unattempted(
                    user_id=user, goal_id=dispatch_match.group(1),
                    version=int(dispatch_match.group(2)),
                )
                self._json(
                    200 if record["signal_state"] == "ACKED" else 202,
                    {"control": _control_receipt(record)},
                )
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
                self._json(200, {"control": _control_receipt(record)})
                return
            match = re.fullmatch(
                r"/api/goals/([A-Za-z0-9][A-Za-z0-9_.-]{0,95})/(pause|resume|cancel)", path
            )
            if match:
                if set(data) not in ({"expected_version"}, {"expected_version", "request_id"}):
                    raise WatchError("expected_version or prepared request_id required")
                is_keyed = "request_id" in data
                if is_keyed:
                    if not _has_prepared_control_schema(self.server.service):
                        self._json(501, {"error": "operator must install PostgreSQL schema v3"})
                        return
                    goal, replayed = store.transition_with_request(
                        user_id=user, goal_id=match.group(1), action=match.group(2),
                        expected_version=data["expected_version"],
                        request_id=data["request_id"],
                    )
                else:
                    goal = store.transition(
                        user_id=user, goal_id=match.group(1), action=match.group(2),
                        expected_version=data["expected_version"],
                    )
                    replayed = False
                if replayed:
                    # This request already committed. Never re-signal Temporal
                    # or re-invoke a provider on an HTTP retry.
                    control = store.control(
                        user_id=user, goal_id=match.group(1),
                        version=data["expected_version"] + 1,
                    )
                else:
                    control = self.server.service.signal_committed(
                        user_id=user, goal_id=match.group(1),
                        version=data["expected_version"] + 1,
                    )
                response = {"goal": _goal_payload(goal), "control": _control_receipt(control)}
                if is_keyed:
                    response.update({"request_id": data["request_id"], "replayed": replayed})
                self._json(200 if control["signal_state"] == "ACKED" else 202, response)
                return
            self._json(404, {"error": "not found"})
        except WatchConflict as exc:
            self._json(409, {"error": str(exc)})
        except ValueError as exc:
            reason = str(exc)
            if ("stale version" in reason or "invalid lifecycle transition" in reason
                or "request ID already bound" in reason
                or "prepare request before execution" in reason):
                status = 409
            else:
                status = 404 if "not found within user scope" in reason else 400
            self._json(status, {"error": reason})


class PilotHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], service: PilotService) -> None:
        if address[0] not in _LOOPBACK:
            raise PilotConfigError("pilot API must bind to loopback only")
        self.service = service
        super().__init__(address, Handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Local PR-watch task API pilot")
    parser.add_argument("--db")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8792)
    args = parser.parse_args()
    storage_backend = os.getenv("PR_WATCH_STORAGE", "sqlite")
    pg_dsn = os.getenv("PR_WATCH_PG_DSN")
    if storage_backend == "postgres":
        if args.db or os.getenv("PR_WATCH_DB") or not pg_dsn:
            parser.error("PostgreSQL requires PR_WATCH_PG_DSN and forbids SQLite path")
    elif storage_backend == "sqlite":
        if not args.db or pg_dsn:
            parser.error("SQLite requires --db and forbids PR_WATCH_PG_DSN")
    else:
        parser.error("PR_WATCH_STORAGE must be sqlite or postgres")
    raw = os.environ.get("PR_WATCH_LOCAL_TOKENS_JSON", "")
    try:
        tokens = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit("Configure PR_WATCH_LOCAL_TOKENS_JSON with local user/token map") from exc
    address = os.environ.get("PR_WATCH_TEMPORAL_ADDRESS")
    namespace = os.getenv("PR_WATCH_TEMPORAL_NAMESPACE", "default")
    bridge = TemporalControlBridge(address=address, namespace=namespace) if address else None
    launcher = (
        TemporalLaunchBridge(
            address=address, namespace=namespace,
            interval_seconds=int(os.getenv("PR_WATCH_INTERVAL_SECONDS", "300")),
        ) if address else None
    )
    service = PilotService(
        args.db, LocalTokens(tokens), bridge, launcher,
        storage_backend=storage_backend, pg_dsn=pg_dsn,
    )
    server = PilotHTTPServer((args.host, args.port), service)
    if service.pg_launcher is not None:
        summary = service.pg_launcher.startup_sweep(owner=service._launch_owner)
        print("PostgreSQL Temporal enrollment sweep: " + json.dumps(summary))
    elif launcher is not None:
        # SQLite mode remains single-node; no background replay of ambiguities.
        summary = launcher.recover_on_exclusive_startup(service.goals)
        print("Temporal enrollment sweep: " + json.dumps(summary, sort_keys=True))
    print(f"Local PR-watch pilot listening at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
