from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence


SENSITIVE_SCOPE_SUFFIXES = frozenset({"write", "delete", "admin", "mutate"})


class CredentialAuthorityError(RuntimeError):
    """Base error mapped by the HTTP adapter without leaking request secrets."""


class CredentialAuthorityDenied(CredentialAuthorityError, PermissionError):
    """The signed principal, policy, Run, binding, or approval was denied."""


class CredentialAuthorityUnavailable(CredentialAuthorityError):
    """An authoritative dependency is unavailable; callers must fail closed."""


class CredentialAuthorityProtocolError(CredentialAuthorityError, ValueError):
    """A signed assertion or authoritative record is malformed."""


class CredentialRequestReplay(CredentialAuthorityError):
    """A credential issuance request key has already been consumed."""


class CredentialAuthorityNotFound(CredentialAuthorityError, LookupError):
    """An authoritative resource does not exist."""


class CredentialAuthorityConflict(CredentialAuthorityError):
    """The authority changed while the request was being committed."""


def _string(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CredentialAuthorityProtocolError(f"missing or invalid field: {key}")
    return value


def _positive_int(data: Mapping[str, Any], key: str) -> int:
    value = data.get(key)
    if type(value) is not int or value <= 0:
        raise CredentialAuthorityProtocolError(f"missing or invalid field: {key}")
    return value


def _number(data: Mapping[str, Any], key: str) -> float:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CredentialAuthorityProtocolError(f"missing or invalid field: {key}")
    return float(value)


def _scopes(data: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = data.get(key)
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(scope, str) or not scope.strip() for scope in value)
        or len(set(value)) != len(value)
    ):
        raise CredentialAuthorityProtocolError(f"missing or invalid scopes: {key}")
    return tuple(sorted(value))


def _scope_needs_approval(scope: str) -> bool:
    return scope.rsplit(":", 1)[-1].lower() in SENSITIVE_SCOPE_SUFFIXES


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _b64url_decode(value: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise CredentialAuthorityProtocolError("invalid compact assertion encoding")
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, base64.binascii.Error):
        raise CredentialAuthorityProtocolError("invalid compact assertion encoding") from None


@dataclass(frozen=True)
class CredentialBindingRecord:
    binding_id: str
    capability: str
    audience: str
    allowed_scopes: tuple[str, ...]
    max_ttl_seconds: int
    active: bool
    version: int


@dataclass(frozen=True)
class ApprovalGrantRecord:
    approval_id: str
    tenant_id: str
    agent_id: str
    session_id: str
    run_id: str
    binding_id: str
    audience: str
    scopes: tuple[str, ...]
    approved_by: str
    issued_at: float
    expires_at: float
    state: str
    version: int


@dataclass(frozen=True)
class CanonicalRunBinding:
    tenant_id: str
    agent_id: str
    release_id: str
    session_id: str
    run_id: str
    sandbox_id: str
    sandbox_revision: int
    workspace_ref: str
    status: str
    session_status: str


class WorkloadAssertionVerifier(Protocol):
    """Verifies a compact signed assertion against operator-pinned issuer/key data."""

    def verify(self, token: str, *, now: float) -> Mapping[str, Any]:
        ...


class RunAuthorityReader(Protocol):
    """Reads canonical runtime identity independently of the signed assertion."""

    def get_run_binding(self, run_id: str) -> CanonicalRunBinding:
        ...


class CredentialAuthorityStore(Protocol):
    """Durable authority store; production implementations must be transactional."""

    def get_binding(self, binding_id: str) -> CredentialBindingRecord | None:
        ...

    def get_approval(self, approval_id: str) -> ApprovalGrantRecord | None:
        ...

    def issue_credential(
        self,
        *,
        record: Mapping[str, Any],
        binding_version: int,
        approval_version: int | None,
        request_digest: str,
        now: float,
    ) -> None:
        ...

    def get_credential_by_digest(self, token_digest: str) -> Mapping[str, Any] | None:
        ...

    def lookup_issue_request(
        self,
        *,
        issuer: str,
        run_id: str,
        binding_id: str,
        request_id: str,
        now: float,
    ) -> Mapping[str, Any] | None:
        ...

    def revoke_credential(
        self, credential_id: str, *, event_id: str, actor_role: str, now: float
    ) -> bool:
        ...

    def revoke_run(
        self, run_id: str, *, sandbox_revision: int, event_id: str,
        actor_role: str, now: float
    ) -> int:
        ...

    def revoke_approval(
        self,
        approval_id: str,
        *,
        event_id: str,
        actor_role: str,
        now: float,
    ) -> tuple[int, int]:
        ...

    def claim_revocation_outbox(
        self, *, claim_id: str, now: float, lease_seconds: int, limit: int
    ) -> Sequence[Mapping[str, Any]]:
        ...

    def acknowledge_revocation_outbox(
        self, *, event_id: str, claim_id: str, delivered_at: float
    ) -> bool:
        ...

    def retry_revocation_outbox(
        self,
        *,
        event_id: str,
        claim_id: str,
        available_at: float,
        sanitized_error: str,
    ) -> bool:
        ...


class PinnedEd25519AssertionVerifier:
    """Verifies JWT/EdDSA workload assertions from a pinned public-key profile.

    Keys are supplied by operator configuration, not a request JWK or remote
    discovery URL. The caller should rotate keys by deploying a reviewed trust
    profile; an unknown kid or issuer fails closed.
    """

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        public_keys: Mapping[str, bytes],
        max_lifetime_seconds: int = 120,
        clock_skew_seconds: int = 2,
    ) -> None:
        if not issuer.strip() or not audience.strip() or not public_keys:
            raise ValueError("pinned assertion issuer, audience and keys are required")
        if type(max_lifetime_seconds) is not int or max_lifetime_seconds <= 0:
            raise ValueError("max assertion lifetime must be positive")
        if type(clock_skew_seconds) is not int or clock_skew_seconds < 0:
            raise ValueError("clock skew must be non-negative")
        if any(not kid.strip() or not isinstance(key, bytes) or len(key) != 32 for kid, key in public_keys.items()):
            raise ValueError("Ed25519 public keys must be 32-byte raw keys keyed by non-empty kid")
        self._issuer = issuer
        self._audience = audience
        self._keys = dict(public_keys)
        self._max_lifetime = max_lifetime_seconds
        self._skew = clock_skew_seconds

    def verify(self, token: str, *, now: float) -> Mapping[str, Any]:
        parts = token.split(".") if isinstance(token, str) else []
        if len(parts) != 3:
            raise CredentialAuthorityDenied("workload assertion is invalid")
        try:
            header = json.loads(_b64url_decode(parts[0]))
            claims = json.loads(_b64url_decode(parts[1]))
            signature = _b64url_decode(parts[2])
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise CredentialAuthorityDenied("workload assertion is invalid") from None
        if not isinstance(header, dict) or not isinstance(claims, dict):
            raise CredentialAuthorityDenied("workload assertion is invalid")
        if header.get("alg") != "EdDSA" or header.get("typ") not in (None, "JWT"):
            raise CredentialAuthorityDenied("workload assertion algorithm is not permitted")
        kid = header.get("kid")
        if not isinstance(kid, str) or kid not in self._keys:
            raise CredentialAuthorityDenied("workload assertion key is not trusted")
        if claims.get("iss") != self._issuer:
            raise CredentialAuthorityDenied("workload assertion issuer mismatch")
        aud = claims.get("aud")
        if not (aud == self._audience or isinstance(aud, list) and self._audience in aud):
            raise CredentialAuthorityDenied("workload assertion audience mismatch")

        try:
            from cryptography.exceptions import InvalidSignature
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        except ImportError:
            raise CredentialAuthorityUnavailable(
                "cryptography extra is required to verify workload assertions"
            ) from None
        try:
            Ed25519PublicKey.from_public_bytes(self._keys[kid]).verify(
                signature,
                f"{parts[0]}.{parts[1]}".encode("ascii"),
            )
        except (InvalidSignature, ValueError, UnicodeEncodeError):
            raise CredentialAuthorityDenied("workload assertion signature is invalid") from None

        issued_at = _number(claims, "iat")
        expires_at = _number(claims, "exp")
        if (
            issued_at > now + self._skew
            or expires_at <= now
            or expires_at <= issued_at
            or expires_at - issued_at > self._max_lifetime
        ):
            raise CredentialAuthorityDenied("workload assertion is expired or outside its lifetime bound")
        self._validate_claim_shape(claims)
        return claims

    @staticmethod
    def _validate_claim_shape(claims: Mapping[str, Any]) -> None:
        for key in (
            "sub",
            "workload_issuer",
            "tenant_id",
            "agent_id",
            "release_id",
            "session_id",
            "run_id",
            "sandbox_id",
            "workspace_ref",
            "binding_id",
            "capability",
            "audience",
            "request_id",
        ):
            _string(claims, key)
        _positive_int(claims, "sandbox_revision")
        _positive_int(claims, "requested_ttl_seconds")
        _scopes(claims, "allowed_scopes")
        _scopes(claims, "scopes")


class PostgresCredentialAuthorityStore:
    """PostgreSQL implementation. Schema must be operator-installed separately."""

    def __init__(self, connect: Callable[[], Any]) -> None:
        self._connect = connect

    @classmethod
    def from_dsn(cls, dsn: str) -> "PostgresCredentialAuthorityStore":
        if not isinstance(dsn, str) or not dsn.strip():
            raise ValueError("credential authority DSN is required")
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError:
            raise CredentialAuthorityUnavailable(
                "install the identity-authority extra for PostgreSQL support"
            ) from None

        return cls(lambda: psycopg.connect(dsn, row_factory=dict_row))

    @staticmethod
    def _binding(row: Mapping[str, Any] | None) -> CredentialBindingRecord | None:
        if row is None:
            return None
        return CredentialBindingRecord(
            binding_id=row["binding_id"],
            capability=row["capability"],
            audience=row["audience"],
            allowed_scopes=tuple(row["allowed_scopes"]),
            max_ttl_seconds=int(row["max_ttl_seconds"]),
            active=bool(row["active"]),
            version=int(row["version"]),
        )

    @staticmethod
    def _approval(row: Mapping[str, Any] | None) -> ApprovalGrantRecord | None:
        if row is None:
            return None
        return ApprovalGrantRecord(
            approval_id=row["approval_id"],
            tenant_id=row["tenant_id"],
            agent_id=row["agent_id"],
            session_id=row["session_id"],
            run_id=row["run_id"],
            binding_id=row["binding_id"],
            audience=row["audience"],
            scopes=tuple(row["scopes"]),
            approved_by=row["approved_by"],
            issued_at=float(row["issued_at"]),
            expires_at=float(row["expires_at"]),
            state=row["state"],
            version=int(row["version"]),
        )

    def get_binding(self, binding_id: str) -> CredentialBindingRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT binding_id, capability, audience, allowed_scopes,
                       max_ttl_seconds, active, version
                FROM public.acp_workload_credential_bindings
                WHERE binding_id = %s
                """,
                (binding_id,),
            ).fetchone()
        return self._binding(row)

    def get_approval(self, approval_id: str) -> ApprovalGrantRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT approval_id, tenant_id, agent_id, session_id, run_id,
                       binding_id, audience, scopes, approved_by, issued_at,
                       expires_at, state, version
                FROM public.acp_workload_approval_grants
                WHERE approval_id = %s
                """,
                (approval_id,),
            ).fetchone()
        return self._approval(row)

    def configure_binding(
        self,
        *,
        binding_id: str,
        capability: str,
        audience: str,
        allowed_scopes: Sequence[str],
        max_ttl_seconds: int,
        active: bool = True,
    ) -> CredentialBindingRecord:
        """Operator provisioning function; intentionally not exposed over HTTP."""
        if not binding_id.strip() or not capability.strip() or not audience.strip():
            raise ValueError("binding identifiers and audience are required")
        scopes = tuple(sorted(set(allowed_scopes)))
        if not scopes or any(not scope.strip() for scope in scopes):
            raise ValueError("binding scopes must be unique and non-empty")
        if type(max_ttl_seconds) is not int or max_ttl_seconds <= 0:
            raise ValueError("max_ttl_seconds must be positive")
        with self._connect() as conn:
            row = conn.execute(
                """
                INSERT INTO public.acp_workload_credential_bindings
                    (binding_id, capability, audience, allowed_scopes,
                     max_ttl_seconds, active, version)
                VALUES (%s, %s, %s, %s::jsonb, %s, %s, 1)
                ON CONFLICT(binding_id) DO UPDATE SET
                    capability = EXCLUDED.capability,
                    audience = EXCLUDED.audience,
                    allowed_scopes = EXCLUDED.allowed_scopes,
                    max_ttl_seconds = EXCLUDED.max_ttl_seconds,
                    active = EXCLUDED.active,
                    version = public.acp_workload_credential_bindings.version + 1
                RETURNING binding_id, capability, audience, allowed_scopes,
                          max_ttl_seconds, active, version
                """,
                (
                    binding_id, capability, audience, json.dumps(list(scopes)),
                    max_ttl_seconds, active,
                ),
            ).fetchone()
        return self._binding(row)

    def register_approval(self, grant: ApprovalGrantRecord) -> ApprovalGrantRecord:
        """Trusted application-layer hook after the human/policy approval commits."""
        if grant.state != "ACTIVE" or grant.version != 1:
            raise ValueError("new approval grants must start ACTIVE at version 1")
        if grant.expires_at <= grant.issued_at:
            raise ValueError("approval lifetime must be positive")
        with self._connect() as conn:
            row = conn.execute(
                """
                INSERT INTO public.acp_workload_approval_grants
                    (approval_id, tenant_id, agent_id, session_id, run_id,
                     binding_id, audience, scopes, approved_by, issued_at,
                     expires_at, state, version)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb,
                        %s, %s, %s, 'ACTIVE', 1)
                RETURNING approval_id, tenant_id, agent_id, session_id, run_id,
                          binding_id, audience, scopes, approved_by, issued_at,
                          expires_at, state, version
                """,
                (
                    grant.approval_id, grant.tenant_id, grant.agent_id,
                    grant.session_id, grant.run_id, grant.binding_id,
                    grant.audience, json.dumps(list(grant.scopes)),
                    grant.approved_by, grant.issued_at, grant.expires_at,
                ),
            ).fetchone()
        return self._approval(row)

    def issue_credential(
        self,
        *,
        record: Mapping[str, Any],
        binding_version: int,
        approval_version: int | None,
        request_digest: str,
        now: float,
    ) -> None:
        key = (
            record["workload_issuer"],
            record["run_id"],
            record["binding_id"],
            record["request_id"],
        )
        try:
            with self._connect() as conn:
                existing = conn.execute(
                    """
                    SELECT 1
                    FROM public.acp_workload_credential_requests
                    WHERE issuer = %s AND run_id = %s
                      AND binding_id = %s AND request_id = %s
                    """,
                    key,
                ).fetchone()
                if existing is not None:
                    raise CredentialRequestReplay("credential request ID was already consumed")

                binding = conn.execute(
                    """
                    SELECT capability, audience, allowed_scopes,
                           max_ttl_seconds, active, version
                    FROM public.acp_workload_credential_bindings
                    WHERE binding_id = %s FOR UPDATE
                    """,
                    (record["binding_id"],),
                ).fetchone()
                if (
                    binding is None
                    or not binding["active"]
                    or int(binding["version"]) != binding_version
                    or binding["capability"] != record["capability"]
                    or binding["audience"] != record["audience"]
                    or set(record["scopes"]) - set(binding["allowed_scopes"])
                    or record["expires_at"] - now > int(binding["max_ttl_seconds"])
                ):
                    raise CredentialAuthorityConflict("credential binding changed during issuance")

                approval_id = record.get("approval_id")
                if approval_id is not None:
                    approval = conn.execute(
                        """
                        SELECT tenant_id, agent_id, session_id, run_id, binding_id,
                               audience, scopes, approved_by, issued_at, expires_at,
                               state, version
                        FROM public.acp_workload_approval_grants
                        WHERE approval_id = %s FOR UPDATE
                        """,
                        (approval_id,),
                    ).fetchone()
                    if (
                        approval is None
                        or approval["state"] != "ACTIVE"
                        or int(approval["version"]) != approval_version
                        or float(approval["expires_at"]) <= now
                        or approval["tenant_id"] != record["tenant_id"]
                        or approval["agent_id"] != record["agent_id"]
                        or approval["session_id"] != record["session_id"]
                        or approval["run_id"] != record["run_id"]
                        or approval["binding_id"] != record["binding_id"]
                        or approval["audience"] != record["audience"]
                        or not set(record["scopes"]).issubset(set(approval["scopes"]))
                    ):
                        raise CredentialAuthorityDenied("approval changed or was revoked during issuance")

                conn.execute(
                    """
                    INSERT INTO public.acp_workload_credentials (
                        credential_id, token_digest, workload_issuer, subject,
                        tenant_id, agent_id, release_id, session_id, run_id,
                        sandbox_id, sandbox_revision, workspace_ref, binding_id,
                        capability, audience, allowed_scopes, scopes, issued_at,
                        expires_at, approval_id, approval_version, binding_version, revoked_at,
                        revoke_event_id
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s, NULL, NULL
                    )
                    """,
                    (
                        record["credential_id"], record["token_digest"],
                        record["workload_issuer"], record["subject"],
                        record["tenant_id"], record["agent_id"], record["release_id"],
                        record["session_id"], record["run_id"], record["sandbox_id"],
                        record["sandbox_revision"], record["workspace_ref"],
                        record["binding_id"], record["capability"], record["audience"],
                        json.dumps(list(record["allowed_scopes"])),
                        json.dumps(list(record["scopes"])), record["issued_at"],
                        record["expires_at"], approval_id, approval_version,
                        binding_version,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO public.acp_workload_credential_requests
                        (issuer, run_id, binding_id, request_id, request_digest,
                         credential_id, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (*key, request_digest, record["credential_id"], now),
                )
        except CredentialRequestReplay:
            raise
        except Exception as exc:
            if getattr(exc, "sqlstate", None) == "23505":
                raise CredentialRequestReplay("credential request ID was already consumed") from None
            raise

    def get_credential_by_digest(self, token_digest: str) -> Mapping[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT c.*,
                       a.state AS current_approval_state,
                       a.version AS current_approval_version,
                       a.expires_at AS current_approval_expires_at,
                       b.active AS current_binding_active,
                       b.version AS current_binding_version
                FROM public.acp_workload_credentials AS c
                LEFT JOIN public.acp_workload_approval_grants AS a
                  ON a.approval_id = c.approval_id
                JOIN public.acp_workload_credential_bindings AS b
                  ON b.binding_id = c.binding_id
                WHERE c.token_digest = %s
                """,
                (token_digest,),
            ).fetchone()
        return row

    def lookup_issue_request(
        self,
        *,
        issuer: str,
        run_id: str,
        binding_id: str,
        request_id: str,
        now: float,
    ) -> Mapping[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT r.issuer, r.run_id, r.binding_id, r.request_id,
                       r.credential_id, c.expires_at, c.revoked_at
                FROM public.acp_workload_credential_requests AS r
                JOIN public.acp_workload_credentials AS c
                  ON c.credential_id = r.credential_id
                WHERE r.issuer = %s AND r.run_id = %s
                  AND r.binding_id = %s AND r.request_id = %s
                """,
                (issuer, run_id, binding_id, request_id),
            ).fetchone()
        if row is None:
            return None
        state = "ISSUED"
        if row["revoked_at"] is not None:
            state = "REVOKED"
        elif float(row["expires_at"]) <= now:
            state = "EXPIRED"
        return {
            "issuer": row["issuer"],
            "run_id": row["run_id"],
            "binding_id": row["binding_id"],
            "request_id": row["request_id"],
            "credential_id": row["credential_id"],
            "state": state,
            "expires_at": float(row["expires_at"]),
        }

    def revoke_credential(
        self,
        credential_id: str,
        *,
        event_id: str,
        actor_role: str,
        now: float,
    ) -> bool:
        if not event_id.strip() or not actor_role.strip():
            raise ValueError("stable event ID and authenticated actor role are required")
        request_digest = hashlib.sha256(
            _canonical_json({"resource_type": "credential", "resource_id": credential_id})
        ).hexdigest()
        with self._connect() as conn:
            prior = conn.execute(
                """
                SELECT resource_type, resource_id, request_digest, revoked_count
                FROM public.acp_workload_credential_revocations
                WHERE event_id = %s FOR UPDATE
                """,
                (event_id,),
            ).fetchone()
            if prior is not None:
                if (
                    prior["resource_type"] != "credential"
                    or prior["resource_id"] != credential_id
                    or prior["request_digest"] != request_digest
                ):
                    raise CredentialAuthorityConflict("revocation event ID was reused for another operation")
                return int(prior["revoked_count"]) > 0

            changed = conn.execute(
                """
                UPDATE public.acp_workload_credentials
                SET revoked_at = %s, revoke_event_id = %s, revoked_by = %s
                WHERE credential_id = %s AND revoked_at IS NULL
                RETURNING credential_id
                """,
                (now, event_id, actor_role, credential_id),
            ).fetchone()
            count = 1 if changed is not None else 0
            conn.execute(
                """
                INSERT INTO public.acp_workload_credential_revocations
                    (event_id, resource_type, resource_id, actor_role,
                     request_digest, revoked_count, approval_version, created_at)
                VALUES (%s, 'credential', %s, %s, %s, %s, NULL, %s)
                """,
                (event_id, credential_id, actor_role, request_digest, count, now),
            )
        return count > 0

    def revoke_run(
        self,
        run_id: str,
        *,
        sandbox_revision: int,
        event_id: str,
        actor_role: str,
        now: float,
    ) -> int:
        if not event_id.strip() or not actor_role.strip():
            raise ValueError("stable event ID and authenticated actor role are required")
        request_digest = hashlib.sha256(
            _canonical_json({
                "resource_type": "run",
                "resource_id": run_id,
                "sandbox_revision": sandbox_revision,
            })
        ).hexdigest()
        with self._connect() as conn:
            prior = conn.execute(
                """
                SELECT resource_type, resource_id, request_digest, revoked_count
                FROM public.acp_workload_credential_revocations
                WHERE event_id = %s FOR UPDATE
                """,
                (event_id,),
            ).fetchone()
            if prior is not None:
                if (
                    prior["resource_type"] != "run"
                    or prior["resource_id"] != run_id
                    or prior["request_digest"] != request_digest
                ):
                    raise CredentialAuthorityConflict("revocation event ID was reused for another operation")
                return int(prior["revoked_count"])

            rows = conn.execute(
                """
                UPDATE public.acp_workload_credentials
                SET revoked_at = %s, revoke_event_id = %s, revoked_by = %s
                WHERE run_id = %s AND sandbox_revision <= %s
                  AND revoked_at IS NULL
                RETURNING credential_id
                """,
                (now, event_id, actor_role, run_id, sandbox_revision),
            ).fetchall()
            count = len(rows)
            conn.execute(
                """
                INSERT INTO public.acp_workload_credential_revocations
                    (event_id, resource_type, resource_id, actor_role,
                     request_digest, revoked_count, approval_version, created_at)
                VALUES (%s, 'run', %s, %s, %s, %s, NULL, %s)
                """,
                (event_id, run_id, actor_role, request_digest, count, now),
            )
        return count

    def revoke_approval(
        self,
        approval_id: str,
        *,
        event_id: str,
        actor_role: str,
        now: float,
    ) -> tuple[int, int]:
        if not event_id.strip() or not actor_role.strip():
            raise ValueError("stable event ID and authenticated actor role are required")
        request_digest = hashlib.sha256(
            _canonical_json({"resource_type": "approval", "resource_id": approval_id})
        ).hexdigest()
        with self._connect() as conn:
            prior_operation = conn.execute(
                """
                SELECT resource_type, resource_id, request_digest,
                       revoked_count, approval_version
                FROM public.acp_workload_credential_revocations
                WHERE event_id = %s FOR UPDATE
                """,
                (event_id,),
            ).fetchone()
            if prior_operation is not None:
                if (
                    prior_operation["resource_type"] != "approval"
                    or prior_operation["resource_id"] != approval_id
                    or prior_operation["request_digest"] != request_digest
                ):
                    raise CredentialAuthorityConflict("revocation event ID was reused for another operation")
                return (
                    int(prior_operation["approval_version"] or 0),
                    int(prior_operation["revoked_count"]),
                )

            approval = conn.execute(
                """
                SELECT state, version
                FROM public.acp_workload_approval_grants
                WHERE approval_id = %s FOR UPDATE
                """,
                (approval_id,),
            ).fetchone()
            if approval is None:
                raise CredentialAuthorityNotFound("approval does not exist")
            version = int(approval["version"])
            if approval["state"] != "REVOKED":
                version += 1
                conn.execute(
                    """
                    UPDATE public.acp_workload_approval_grants
                    SET state = 'REVOKED', version = %s, revoke_event_id = %s
                    WHERE approval_id = %s
                    """,
                    (version, event_id, approval_id),
                )
            updated = conn.execute(
                """
                UPDATE public.acp_workload_credentials
                SET revoked_at = COALESCE(revoked_at, %s),
                    revoke_event_id = COALESCE(revoke_event_id, %s),
                    revoked_by = COALESCE(revoked_by, %s)
                WHERE approval_id = %s AND revoked_at IS NULL
                RETURNING credential_id
                """,
                (now, event_id, actor_role, approval_id),
            ).fetchall()
            count = len(updated)
            conn.execute(
                """
                INSERT INTO public.acp_workload_credential_revocations
                    (event_id, resource_type, resource_id, actor_role,
                     request_digest, revoked_count, approval_version, created_at)
                VALUES (%s, 'approval', %s, %s, %s, %s, %s, %s)
                """,
                (event_id, approval_id, actor_role, request_digest, count, version, now),
            )
            conn.execute(
                """
                INSERT INTO public.acp_workload_approval_revocation_events
                    (event_id, approval_id, approval_version, revoked_count, actor_role, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (event_id, approval_id, version, count, actor_role, now),
            )
            conn.execute(
                """
                INSERT INTO public.acp_workload_credential_revocation_outbox
                    (event_id, approval_id, approval_version, payload, created_at,
                     attempts, available_at)
                VALUES (%s, %s, %s, %s::jsonb, %s, 0, %s)
                """,
                (
                    event_id,
                    approval_id,
                    version,
                    json.dumps({
                        "event_id": event_id,
                        "approval_id": approval_id,
                        "state": "REVOKED",
                        "version": version,
                    }),
                    now,
                    now,
                ),
            )
        return version, count

    def claim_revocation_outbox(
        self,
        *,
        claim_id: str,
        now: float,
        lease_seconds: int,
        limit: int,
    ) -> Sequence[Mapping[str, Any]]:
        if not claim_id.strip() or type(lease_seconds) is not int or lease_seconds <= 0:
            raise ValueError("outbox claim and positive lease are required")
        if type(limit) is not int or limit <= 0 or limit > 100:
            raise ValueError("outbox limit must be between 1 and 100")
        with self._connect() as conn:
            rows = conn.execute(
                """
                WITH candidates AS (
                    SELECT event_id
                    FROM public.acp_workload_credential_revocation_outbox
                    WHERE delivered_at IS NULL
                      AND available_at <= %s
                      AND (lease_until IS NULL OR lease_until <= %s)
                    ORDER BY created_at, event_id
                    FOR UPDATE SKIP LOCKED
                    LIMIT %s
                )
                UPDATE public.acp_workload_credential_revocation_outbox AS outbox
                SET claim_id = %s, lease_until = %s, attempts = attempts + 1
                FROM candidates
                WHERE outbox.event_id = candidates.event_id
                RETURNING outbox.event_id, outbox.approval_id,
                          outbox.approval_version, outbox.payload,
                          outbox.attempts
                """,
                (now, now, limit, claim_id, now + lease_seconds),
            ).fetchall()
        return rows

    def acknowledge_revocation_outbox(
        self, *, event_id: str, claim_id: str, delivered_at: float
    ) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                UPDATE public.acp_workload_credential_revocation_outbox
                SET delivered_at = %s, claim_id = NULL, lease_until = NULL,
                    last_error = NULL
                WHERE event_id = %s AND claim_id = %s AND delivered_at IS NULL
                RETURNING event_id
                """,
                (delivered_at, event_id, claim_id),
            ).fetchone()
        return row is not None

    def retry_revocation_outbox(
        self,
        *,
        event_id: str,
        claim_id: str,
        available_at: float,
        sanitized_error: str,
    ) -> bool:
        safe_error = sanitized_error[:256]
        with self._connect() as conn:
            row = conn.execute(
                """
                UPDATE public.acp_workload_credential_revocation_outbox
                SET available_at = %s, claim_id = NULL, lease_until = NULL,
                    last_error = %s
                WHERE event_id = %s AND claim_id = %s AND delivered_at IS NULL
                RETURNING event_id
                """,
                (available_at, safe_error, event_id, claim_id),
            ).fetchone()
        return row is not None


class WorkloadCredentialAuthority:
    """Fail-closed credential issuer/introspector backed by operator-owned state."""

    def __init__(
        self,
        *,
        store: CredentialAuthorityStore,
        assertion_verifier: WorkloadAssertionVerifier,
        run_authority: RunAuthorityReader,
        clock: Callable[[], float] = time.time,
        token_factory: Callable[[], str] = lambda: secrets.token_urlsafe(32),
        request_id_max_length: int = 200,
    ) -> None:
        if type(request_id_max_length) is not int or request_id_max_length < 16:
            raise ValueError("request_id_max_length is too small")
        self._store = store
        self._assertions = assertion_verifier
        self._runs = run_authority
        self._clock = clock
        self._token_factory = token_factory
        self._request_id_max_length = request_id_max_length

    @staticmethod
    def _assert_current_run(
        claims: Mapping[str, Any],
        canonical: CanonicalRunBinding,
        *,
        require_running: bool,
    ) -> None:
        if require_running and (
            canonical.status.lower() != "running"
            or canonical.session_status.lower() != "active"
        ):
            raise CredentialAuthorityDenied("credentials require a running Run in an active Session")
        expected = {
            "tenant_id": canonical.tenant_id,
            "agent_id": canonical.agent_id,
            "release_id": canonical.release_id,
            "session_id": canonical.session_id,
            "run_id": canonical.run_id,
            "sandbox_id": canonical.sandbox_id,
            "sandbox_revision": canonical.sandbox_revision,
            "workspace_ref": canonical.workspace_ref,
        }
        for key, value in expected.items():
            if claims.get(key) != value:
                raise CredentialAuthorityDenied("workload identity does not match canonical Run state")

    @staticmethod
    def _grant_matches(
        grant: ApprovalGrantRecord,
        claims: Mapping[str, Any],
        *,
        binding_id: str,
        audience: str,
        scopes: Sequence[str],
        now: float,
    ) -> bool:
        return (
            grant.state == "ACTIVE"
            and grant.expires_at > now
            and grant.tenant_id == claims["tenant_id"]
            and grant.agent_id == claims["agent_id"]
            and grant.session_id == claims["session_id"]
            and grant.run_id == claims["run_id"]
            and grant.binding_id == binding_id
            and grant.audience == audience
            and set(scopes).issubset(set(grant.scopes))
            and bool(grant.approved_by.strip())
        )

    def issue(self, body: Mapping[str, Any]) -> Mapping[str, Any]:
        now = self._clock()
        request_id = _string(body, "request_id")
        if len(request_id) > self._request_id_max_length:
            raise CredentialAuthorityProtocolError("request ID exceeds maximum length")
        binding_id = _string(body, "binding_id")
        audience = _string(body, "audience")
        requested_scopes = _scopes(body, "scopes")
        requested_ttl = _positive_int(body, "requested_ttl_seconds")
        assertion = _string(body, "workload_assertion")
        approval_id = body.get("approval_id")
        if approval_id is not None and (not isinstance(approval_id, str) or not approval_id.strip()):
            raise CredentialAuthorityProtocolError("approval ID is malformed")

        claims = self._assertions.verify(assertion, now=now)
        if (
            claims.get("request_id") != request_id
            or claims.get("binding_id") != binding_id
            or claims.get("audience") != audience
            or tuple(sorted(claims.get("scopes", ()))) != requested_scopes
            or claims.get("requested_ttl_seconds") != requested_ttl
            or claims.get("approval_id") != approval_id
        ):
            raise CredentialAuthorityDenied("request fields differ from the signed workload assertion")

        binding = self._store.get_binding(binding_id)
        if binding is None or not binding.active:
            raise CredentialAuthorityDenied("credential binding is not active")
        if (
            binding.capability != claims.get("capability")
            or binding.audience != audience
            or set(requested_scopes) - set(binding.allowed_scopes)
            or set(requested_scopes) - set(claims.get("allowed_scopes", ()))
        ):
            raise CredentialAuthorityDenied("binding audience or scopes do not authorize this request")

        try:
            canonical = self._runs.get_run_binding(claims["run_id"])
        except CredentialAuthorityError:
            raise
        except Exception:
            raise CredentialAuthorityUnavailable("canonical Run authority is unavailable") from None
        self._assert_current_run(claims, canonical, require_running=True)

        needs_approval = any(_scope_needs_approval(scope) for scope in requested_scopes)
        if needs_approval != (approval_id is not None):
            raise CredentialAuthorityDenied("sensitive credentials require an exact approval reference")
        grant = None
        if approval_id is not None:
            grant = self._store.get_approval(approval_id)
            if grant is None or not self._grant_matches(
                grant,
                claims,
                binding_id=binding_id,
                audience=audience,
                scopes=requested_scopes,
                now=now,
            ):
                raise CredentialAuthorityDenied("approval is missing, expired, revoked or mismatched")

        expires_at = min(
            now + requested_ttl,
            now + binding.max_ttl_seconds,
            _number(claims, "exp"),
            grant.expires_at if grant is not None else float("inf"),
        )
        if expires_at <= now:
            raise CredentialAuthorityDenied("no positive credential lifetime remains")

        token = self._token_factory()
        if not isinstance(token, str) or len(token) < 32:
            raise CredentialAuthorityUnavailable("token generator did not return a sufficiently strong token")
        token_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        credential_id = secrets.token_hex(16)
        effective_claims = {
            "issuer": _string(claims, "workload_issuer"),
            "subject": _string(claims, "sub"),
            "tenant_id": claims["tenant_id"],
            "agent_id": claims["agent_id"],
            "release_id": claims["release_id"],
            "session_id": claims["session_id"],
            "run_id": claims["run_id"],
            "sandbox_id": claims["sandbox_id"],
            "sandbox_revision": claims["sandbox_revision"],
            "workspace_ref": claims["workspace_ref"],
            "binding_id": binding_id,
            "capability": binding.capability,
            "audience": audience,
            "allowed_scopes": tuple(sorted(claims["allowed_scopes"])),
            "issued_at": now,
            "expires_at": expires_at,
        }
        record = {
            **effective_claims,
            "credential_id": credential_id,
            "token_digest": token_digest,
            "scopes": requested_scopes,
            "request_id": request_id,
            "workload_issuer": claims["workload_issuer"],
            "approval_id": approval_id,
            "approval_version": grant.version if grant is not None else None,
            "binding_version": binding.version,
        }
        request_digest = hashlib.sha256(
            _canonical_json({
                "request_id": request_id,
                "binding_id": binding_id,
                "audience": audience,
                "scopes": list(requested_scopes),
                "requested_ttl_seconds": requested_ttl,
                "approval_id": approval_id,
                "assertion_digest": hashlib.sha256(assertion.encode("utf-8")).hexdigest(),
            })
        ).hexdigest()
        self._store.issue_credential(
            record=record,
            binding_version=binding.version,
            approval_version=grant.version if grant is not None else None,
            request_digest=request_digest,
            now=now,
        )
        return {
            "credential": {
                "credential_id": credential_id,
                "access_token": token,
                "audience": audience,
                "scopes": list(requested_scopes),
                "issued_at": now,
                "expires_at": expires_at,
                "run_id": claims["run_id"],
                "session_id": claims["session_id"],
                "binding_id": binding_id,
                "sandbox_id": claims["sandbox_id"],
                "sandbox_revision": claims["sandbox_revision"],
            }
        }

    def introspect(self, body: Mapping[str, Any]) -> Mapping[str, Any]:
        token = _string(body, "access_token")
        audience = _string(body, "audience")
        required_scopes = body.get("required_scopes", [])
        if (
            not isinstance(required_scopes, list)
            or any(not isinstance(scope, str) or not scope.strip() for scope in required_scopes)
            or len(set(required_scopes)) != len(required_scopes)
        ):
            raise CredentialAuthorityProtocolError("required scopes are invalid")
        now = self._clock()
        token_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        record = self._store.get_credential_by_digest(token_digest)
        if record is None:
            return {"credential": {"active": False}}

        scopes = tuple(record["scopes"])
        if (
            record["revoked_at"] is not None
            or float(record["expires_at"]) <= now
            or record["audience"] != audience
            or record["current_binding_active"] is not True
            or int(record["current_binding_version"]) != int(record["binding_version"])
            or set(required_scopes) - set(scopes)
        ):
            return {"credential": {"active": False}}
        approval_id = record["approval_id"]
        if approval_id is not None and (
            record["current_approval_state"] != "ACTIVE"
            or int(record["current_approval_version"]) != int(record["approval_version"])
            or float(record["current_approval_expires_at"]) <= now
        ):
            return {"credential": {"active": False}}

        try:
            canonical = self._runs.get_run_binding(record["run_id"])
        except CredentialAuthorityNotFound:
            return {"credential": {"active": False}}
        except Exception:
            raise CredentialAuthorityUnavailable("canonical Run authority is unavailable") from None
        claims = {
            "tenant_id": record["tenant_id"],
            "agent_id": record["agent_id"],
            "release_id": record["release_id"],
            "session_id": record["session_id"],
            "run_id": record["run_id"],
            "sandbox_id": record["sandbox_id"],
            "sandbox_revision": record["sandbox_revision"],
            "workspace_ref": record["workspace_ref"],
        }
        try:
            self._assert_current_run(claims, canonical, require_running=True)
        except CredentialAuthorityDenied:
            return {"credential": {"active": False}}

        identity = {
            "issuer": record["workload_issuer"],
            "subject": record["subject"],
            "tenant_id": record["tenant_id"],
            "agent_id": record["agent_id"],
            "release_id": record["release_id"],
            "session_id": record["session_id"],
            "run_id": record["run_id"],
            "sandbox_id": record["sandbox_id"],
            "sandbox_revision": int(record["sandbox_revision"]),
            "workspace_ref": record["workspace_ref"],
            "binding_id": record["binding_id"],
            "capability": record["capability"],
            "audience": record["audience"],
            "allowed_scopes": list(record["allowed_scopes"]),
            "issued_at": float(record["issued_at"]),
            "expires_at": float(record["expires_at"]),
        }
        return {
            "credential": {
                "active": True,
                "audience": record["audience"],
                "scopes": list(scopes),
                "expires_at": float(record["expires_at"]),
                "sandbox_id": record["sandbox_id"],
                "sandbox_revision": int(record["sandbox_revision"]),
                "workload_identity": identity,
            }
        }

    def revoke_credential(self, body: Mapping[str, Any]) -> Mapping[str, Any]:
        credential_id = _string(body, "credential_id")
        event_id = _string(body, "event_id")
        actor_role = _string(body, "_authenticated_service_role")
        return {
            "revoked": self._store.revoke_credential(
                credential_id,
                event_id=event_id,
                actor_role=actor_role,
                now=self._clock(),
            )
        }

    def revoke_run(self, body: Mapping[str, Any]) -> Mapping[str, Any]:
        run_id = _string(body, "run_id")
        sandbox_revision = _positive_int(body, "sandbox_revision")
        event_id = _string(body, "event_id")
        actor_role = _string(body, "_authenticated_service_role")
        try:
            canonical = self._runs.get_run_binding(run_id)
        except CredentialAuthorityError:
            raise
        except Exception:
            raise CredentialAuthorityUnavailable("canonical Run authority is unavailable") from None
        if canonical.run_id != run_id or canonical.sandbox_revision != sandbox_revision:
            raise CredentialAuthorityDenied("Run sandbox revision is stale or mismatched")
        return {
            "revoked_count": self._store.revoke_run(
                run_id,
                sandbox_revision=sandbox_revision,
                event_id=event_id,
                actor_role=actor_role,
                now=self._clock(),
            )
        }

    def revoke_approval(self, body: Mapping[str, Any]) -> Mapping[str, Any]:
        approval_id = _string(body, "approval_id")
        event_id = _string(body, "event_id")
        actor_role = _string(body, "_authenticated_service_role")
        _, count = self._store.revoke_approval(
            approval_id,
            event_id=event_id,
            actor_role=actor_role,
            now=self._clock(),
        )
        return {"revoked_count": count}

    def get_approval(self, approval_id: str) -> Mapping[str, Any]:
        grant = self._store.get_approval(approval_id)
        if grant is None:
            raise CredentialAuthorityNotFound("approval does not exist")
        if grant.state != "ACTIVE":
            return {"state": grant.state, "version": grant.version}
        if grant.expires_at <= self._clock():
            return {"state": "EXPIRED", "version": grant.version}
        return {
            "state": "ACTIVE",
            "version": grant.version,
            "grant": {
                "approval_id": grant.approval_id,
                "tenant_id": grant.tenant_id,
                "agent_id": grant.agent_id,
                "session_id": grant.session_id,
                "run_id": grant.run_id,
                "binding_id": grant.binding_id,
                "audience": grant.audience,
                "scopes": list(grant.scopes),
                "approved_by": grant.approved_by,
                "issued_at": grant.issued_at,
                "expires_at": grant.expires_at,
            },
        }

    def revoke_approval_authority(
        self, approval_id: str, body: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        event_id = _string(body, "event_id")
        actor_role = _string(body, "_authenticated_service_role")
        version, _ = self._store.revoke_approval(
            approval_id,
            event_id=event_id,
            actor_role=actor_role,
            now=self._clock(),
        )
        return {"state": "REVOKED", "version": version}

    def lookup_issue_request(self, body: Mapping[str, Any]) -> Mapping[str, Any]:
        issuer = _string(body, "issuer")
        run_id = _string(body, "run_id")
        binding_id = _string(body, "binding_id")
        request_id = _string(body, "request_id")
        result = self._store.lookup_issue_request(
            issuer=issuer,
            run_id=run_id,
            binding_id=binding_id,
            request_id=request_id,
            now=self._clock(),
        )
        if result is None:
            raise CredentialAuthorityNotFound("credential request does not exist")
        return {"request": dict(result)}


class ApprovalRevocationPublisher(Protocol):
    """Publishes an already durable revocation to a trusted downstream consumer."""

    def publish(self, payload: Mapping[str, Any]) -> None:
        ...


class ApprovalRevocationOutboxConsumer:
    """At-least-once publisher; receivers must dedupe by the stable event ID."""

    def __init__(
        self,
        *,
        store: CredentialAuthorityStore,
        publisher: ApprovalRevocationPublisher,
        clock: Callable[[], float] = time.time,
        lease_seconds: int = 30,
        retry_seconds: int = 5,
    ) -> None:
        self._store = store
        self._publisher = publisher
        self._clock = clock
        self._lease_seconds = lease_seconds
        self._retry_seconds = retry_seconds

    def run_once(self, *, limit: int = 20) -> Mapping[str, int]:
        claim_id = secrets.token_hex(16)
        now = self._clock()
        claimed = self._store.claim_revocation_outbox(
            claim_id=claim_id,
            now=now,
            lease_seconds=self._lease_seconds,
            limit=limit,
        )
        delivered = failed = 0
        for item in claimed:
            event_id = str(item["event_id"])
            try:
                self._publisher.publish(item["payload"])
            except Exception:
                self._store.retry_revocation_outbox(
                    event_id=event_id,
                    claim_id=claim_id,
                    available_at=self._clock() + self._retry_seconds,
                    sanitized_error="downstream revocation delivery failed",
                )
                failed += 1
            else:
                if self._store.acknowledge_revocation_outbox(
                    event_id=event_id,
                    claim_id=claim_id,
                    delivered_at=self._clock(),
                ):
                    delivered += 1
        return {"claimed": len(claimed), "delivered": delivered, "failed": failed}
