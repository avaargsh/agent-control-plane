from __future__ import annotations

import hashlib
import hmac
import json
import re
from typing import Mapping
from urllib.parse import unquote
from wsgiref.types import WSGIEnvironment, StartResponse

from .workload_credentials import (
    CredentialAuthorityConflict,
    CredentialAuthorityDenied,
    CredentialAuthorityNotFound,
    CredentialAuthorityProtocolError,
    CredentialAuthorityUnavailable,
    CredentialRequestReplay,
    WorkloadCredentialAuthority,
)


_ROLE_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{2,63}$")
_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ServiceRoleAuthenticator:
    """Authenticates narrowly scoped service tokens without storing plaintext.

    This is an application-layer role boundary behind TLS/mTLS or a trusted
    ingress proxy. A production deployment must protect the digest configuration
    with operator-controlled secret distribution and must not expose this WSGI
    app directly to an untrusted network.
    """

    def __init__(self, token_digests: Mapping[str, str]) -> None:
        if not token_digests:
            raise ValueError("at least one service role digest is required")
        normalized: dict[str, str] = {}
        for role, digest in token_digests.items():
            if not isinstance(role, str) or not _ROLE_PATTERN.fullmatch(role):
                raise ValueError("service role name is invalid")
            if not isinstance(digest, str) or not _DIGEST_PATTERN.fullmatch(digest):
                raise ValueError("service role token digests must be lowercase SHA-256 hex")
            normalized[role] = digest
        self._digests = normalized

    @staticmethod
    def digest_token(token: str) -> str:
        """Provisioning helper: store the digest in protected operator config."""
        if not isinstance(token, str) or len(token) < 32:
            raise ValueError("service bearer token must contain at least 32 characters")
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def authorize(self, environ: WSGIEnvironment, *, required_role: str) -> bool:
        header = environ.get("HTTP_AUTHORIZATION", "")
        if not isinstance(header, str):
            return False
        scheme, separator, token = header.partition(" ")
        if not separator or scheme.lower() != "bearer" or not token or token.strip() != token:
            return False
        expected = self._digests.get(required_role)
        if expected is None:
            return False
        actual = hashlib.sha256(token.encode("utf-8")).hexdigest()
        # compare_digest avoids leaking the matching portion of the token digest.
        return hmac.compare_digest(actual, expected)


class WorkloadCredentialWSGI:
    """Small bounded WSGI surface for the workload credential authority.

    Service endpoints are intentionally independent from user-facing ACP routes.
    There are no HTTP endpoints for creating approval grants or changing binding
    policy; those operations remain in trusted application/operator code.
    """

    MAX_REQUEST_BYTES = 64 * 1024
    MAX_RESPONSE_BYTES = 1024 * 1024

    ROLE_ISSUER = "credential.issuer"
    ROLE_INTROSPECTOR = "credential.introspector"
    ROLE_REVOKER = "credential.revoker"
    ROLE_APPROVAL_READER = "approval.reader"
    ROLE_APPROVAL_REVOKER = "approval.revoker"

    def __init__(
        self,
        authority: WorkloadCredentialAuthority,
        *,
        authenticator: ServiceRoleAuthenticator,
        clock,
    ) -> None:
        self._authority = authority
        self._authenticator = authenticator
        self._clock = clock

    def _body(self, environ: WSGIEnvironment) -> Mapping[str, object]:
        content_type = environ.get("CONTENT_TYPE", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise CredentialAuthorityProtocolError("Content-Type must be application/json")
        raw_length = environ.get("CONTENT_LENGTH", "")
        if not isinstance(raw_length, str) or not raw_length.isdigit():
            raise CredentialAuthorityProtocolError("Content-Length is invalid")
        length = int(raw_length)
        if length <= 0 or length > self.MAX_REQUEST_BYTES:
            raise CredentialAuthorityProtocolError("request body size is outside the permitted range")
        stream = environ.get("wsgi.input")
        if stream is None:
            raise CredentialAuthorityProtocolError("request body is missing")
        # Read exactly Content-Length; attempting length+1 can block on
        # WSGI streams whose server waits for more request-body bytes.
        raw = stream.read(length)
        if len(raw) != length:
            raise CredentialAuthorityProtocolError("request body length mismatch")
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise CredentialAuthorityProtocolError("request body is not valid JSON") from None
        if not isinstance(value, dict):
            raise CredentialAuthorityProtocolError("request body must be a JSON object")
        return value

    @staticmethod
    def _query_one(environ: WSGIEnvironment, key: str) -> str:
        from urllib.parse import parse_qs

        values = parse_qs(str(environ.get("QUERY_STRING", "")), keep_blank_values=True).get(key)
        if not values or len(values) != 1 or not values[0].strip():
            raise CredentialAuthorityProtocolError(f"query parameter {key} is required exactly once")
        return values[0]

    @staticmethod
    def _match_approval_path(path: str, *, suffix: str = "") -> str | None:
        prefix = "/v1/authorization/approvals/"
        if not path.startswith(prefix):
            return None
        tail = path[len(prefix):]
        if suffix:
            if not tail.endswith(suffix):
                return None
            tail = tail[:-len(suffix)]
        elif ":" in tail:
            return None
        if not tail or "/" in tail:
            return None
        try:
            approval_id = unquote(tail, errors="strict")
        except (UnicodeDecodeError, ValueError):
            return None
        return approval_id if approval_id and approval_id.strip() else None

    def _dispatch(self, environ: WSGIEnvironment) -> tuple[int, Mapping[str, object]]:
        method = str(environ.get("REQUEST_METHOD", ""))
        path = str(environ.get("PATH_INFO", ""))

        if method == "POST" and path == "/v1/workload-credentials:issue":
            role = self.ROLE_ISSUER
            operation = "issue"
        elif method == "POST" and path == "/v1/workload-credentials:introspect":
            role = self.ROLE_INTROSPECTOR
            operation = "introspect"
        elif method == "POST" and path == "/v1/workload-credentials:revoke":
            role = self.ROLE_REVOKER
            operation = "revoke_credential"
        elif method == "POST" and path == "/v1/workload-credentials:revoke-run":
            role = self.ROLE_REVOKER
            operation = "revoke_run"
        elif method == "POST" and path == "/v1/workload-credentials:revoke-approval":
            role = self.ROLE_REVOKER
            operation = "revoke_approval"
        elif method == "POST" and path == "/v1/workload-credential-requests:lookup":
            role = self.ROLE_ISSUER
            operation = "lookup_issue_request"
        elif method == "GET":
            approval_id = self._match_approval_path(path)
            if approval_id is not None:
                role = self.ROLE_APPROVAL_READER
                operation = ("get_approval", approval_id)
            else:
                return 404, {"error": {"code": "not_found", "message": "resource not found"}}
        elif method == "POST":
            approval_id = self._match_approval_path(path, suffix=":revoke")
            if approval_id is not None:
                role = self.ROLE_APPROVAL_REVOKER
                operation = ("revoke_approval_authority", approval_id)
            else:
                return 404, {"error": {"code": "not_found", "message": "resource not found"}}
        else:
            return 405, {"error": {"code": "method_not_allowed", "message": "method not allowed"}}

        if not self._authenticator.authorize(environ, required_role=role):
            return 403, {"error": {"code": "service_role_denied", "message": "service role authorization denied"}}

        if method == "GET":
            return 200, self._authority.get_approval(operation[1])

        body = self._body(environ)
        if isinstance(operation, tuple):
            return 200, getattr(self._authority, operation[0])(operation[1], body)

        result = getattr(self._authority, operation)(body)
        return 200, result

    def __call__(
        self,
        environ: WSGIEnvironment,
        start_response: StartResponse,
    ) -> list[bytes]:
        try:
            status, payload = self._dispatch(environ)
        except CredentialRequestReplay:
            status, payload = 409, {
                "error": {"code": "request_replay", "message": "credential request ID has already been consumed"}
            }
        except CredentialAuthorityConflict:
            status, payload = 409, {
                "error": {"code": "authority_conflict", "message": "authoritative state changed; reconcile and retry safely"}
            }
        except CredentialAuthorityNotFound:
            status, payload = 404, {
                "error": {"code": "not_found", "message": "resource not found"}
            }
        except CredentialAuthorityDenied:
            status, payload = 403, {
                "error": {"code": "denied", "message": "authoritative authorization denied"}
            }
        except CredentialAuthorityProtocolError:
            status, payload = 400, {
                "error": {"code": "invalid_request", "message": "request or authoritative record is invalid"}
            }
        except CredentialAuthorityUnavailable:
            status, payload = 503, {
                "error": {"code": "authority_unavailable", "message": "authoritative service unavailable"}
            }
        except (KeyError, TypeError, ValueError):
            status, payload = 400, {
                "error": {"code": "invalid_request", "message": "request is invalid"}
            }
        except Exception:
            # No exception text is serialized: DB details, bearer values, signed
            # assertions, and token digests must never leak through API errors.
            status, payload = 500, {
                "error": {"code": "internal_error", "message": "internal authority error"}
            }

        try:
            encoded = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError):
            status = 500
            encoded = b'{"error":{"code":"internal_error","message":"internal authority error"}}'
        if len(encoded) > self.MAX_RESPONSE_BYTES:
            status = 500
            encoded = b'{"error":{"code":"internal_error","message":"internal authority error"}}'

        reasons = {
            200: "200 OK",
            400: "400 Bad Request",
            403: "403 Forbidden",
            404: "404 Not Found",
            405: "405 Method Not Allowed",
            409: "409 Conflict",
            500: "500 Internal Server Error",
            503: "503 Service Unavailable",
        }
        start_response(
            reasons.get(status, "500 Internal Server Error"),
            [
                ("Content-Type", "application/json; charset=utf-8"),
                ("Content-Length", str(len(encoded))),
                ("Cache-Control", "no-store"),
                ("Pragma", "no-cache"),
                ("X-Content-Type-Options", "nosniff"),
            ],
        )
        return [encoded]
