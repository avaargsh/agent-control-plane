from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol
from uuid import uuid4


class CredentialAuthorityError(RuntimeError):
    """Base error for workload credential authority operations."""


class CredentialAuthorityDenied(CredentialAuthorityError, PermissionError):
    """A signed assertion, grant, binding, or token was not authorized."""


class CredentialAuthorityConflict(CredentialAuthorityError):
    """A replay or conflicting immutable record was detected."""


class CredentialAuthorityNotFound(CredentialAuthorityError, LookupError):
    """An authoritative binding, approval, or credential does not exist."""


class CredentialAuthorityUnavailable(CredentialAuthorityError):
    """Authority persistence or a required verification dependency is unavailable."""


@dataclass(frozen=True)
class TrustedAssertionKey:
    issuer: str
    key_id: str
    public_key_ed25519: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if not self.issuer.strip() or not self.key_id.strip():
            raise ValueError("trusted assertion issuer and key ID are required")
        if len(self.public_key_ed25519) != 32:
            raise ValueError("Ed25519 public keys must be exactly 32 bytes")


@dataclass(frozen=True)
class VerifiedWorkloadAssertion:
    issuer: str
    subject: str
    tenant_id: str
    agent_id: str
    release_id: str
    session_id: str
    run_id: str
    sandbox_id: str
    sandbox_revision: int
    workspace_ref: str
    binding_id: str
    capability: str
    credential_audience: str
    allowed_scopes: tuple[str, ...]
    requested_scopes: tuple[str, ...]
    request_id: str
    requested_ttl_seconds: int
    approval_id: str | None
    issued_at: float
    expires_at: float
    token_id: str

    def identity_dict(self) -> dict[str, Any]:
        return {
            "issuer": self.issuer,
            "subject": self.subject,
            "tenant_id": self.tenant_id,
            "agent_id": self.agent_id,
            "release_id": self.release_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "sandbox_id": self.sandbox_id,
            "sandbox_revision": self.sandbox_revision,
            "workspace_ref": self.workspace_ref,
            "binding_id": self.binding_id,
            "capability": self.capability,
            "audience": self.credential_audience,
            "allowed_scopes": list(self.allowed_scopes),
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
        }


class WorkloadAssertionVerifier(Protocol):
    def verify(self, compact_jws: str) -> VerifiedWorkloadAssertion:
        ...


def _json_object(raw: bytes, *, field_name: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise CredentialAuthorityDenied(
            f"signed workload assertion {field_name} is invalid JSON"
        ) from None
    if not isinstance(value, dict):
        raise CredentialAuthorityDenied(
            f"signed workload assertion {field_name} must be an object"
        )
    return value


def _b64url_decode(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError):
        raise CredentialAuthorityDenied(
            "signed workload assertion has invalid base64url data"
        ) from None


def _required_string(claims: Mapping[str, Any], name: str) -> str:
    value = claims.get(name)
    if not isinstance(value, str) or not value.strip():
        raise CredentialAuthorityDenied(
            f"signed workload assertion claim {name} is missing"
        )
    return value


def _required_timestamp(claims: Mapping[str, Any], name: str) -> float:
    value = claims.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CredentialAuthorityDenied(
            f"signed workload assertion claim {name} is invalid"
        )
    return float(value)


def _scopes(claims: Mapping[str, Any], name: str) -> tuple[str, ...]:
    value = claims.get(name)
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(scope, str) or not scope.strip() for scope in value)
        or len(value) != len(set(value))
    ):
        raise CredentialAuthorityDenied(
            f"signed workload assertion claim {name} is invalid"
        )
    return tuple(sorted(value))


class Ed25519WorkloadAssertionVerifier:
    """Verify compact EdDSA JWS claims using operator-pinned public keys.

    The issuer's private key must live in a KMS/workload identity service; it
    must never be mounted in an Agent workspace. This verifier accepts only an
    explicitly configured issuer + kid + Ed25519 public key and exact audience.
    """

    def __init__(
        self,
        *,
        trusted_keys: Mapping[str, TrustedAssertionKey],
        audience: str = "urn:cloud-agent-runtime:credential-authority",
        clock: Callable[[], float] = time.time,
        max_assertion_ttl_seconds: int = 120,
        clock_skew_seconds: int = 5,
    ) -> None:
        if not audience.strip():
            raise ValueError("assertion audience is required")
        if type(max_assertion_ttl_seconds) is not int or max_assertion_ttl_seconds <= 0:
            raise ValueError("max_assertion_ttl_seconds must be a positive integer")
        if type(clock_skew_seconds) is not int or clock_skew_seconds < 0:
            raise ValueError("clock_skew_seconds must be non-negative")
        if not trusted_keys:
            raise ValueError(
                "at least one operator-trusted workload assertion key is required"
            )
        self._trusted_keys = dict(trusted_keys)
        self._audience = audience
        self._clock = clock
        self._max_ttl = max_assertion_ttl_seconds
        self._skew = clock_skew_seconds

    def verify(self, compact_jws: str) -> VerifiedWorkloadAssertion:
        if not isinstance(compact_jws, str) or not compact_jws.strip():
            raise CredentialAuthorityDenied("workload assertion is missing")
        parts = compact_jws.split(".")
        if len(parts) != 3 or any(not part for part in parts):
            raise CredentialAuthorityDenied("workload assertion must be a compact JWS")
        header = _json_object(_b64url_decode(parts[0]), field_name="header")
        claims = _json_object(_b64url_decode(parts[1]), field_name="payload")
        if header.get("alg") != "EdDSA" or header.get("typ") != "JWT":
            raise CredentialAuthorityDenied(
                "workload assertion signature algorithm is not allowed"
            )
        key_id = _required_string(header, "kid")
        key = self._trusted_keys.get(key_id)
        if key is None:
            raise CredentialAuthorityDenied("workload assertion signing key is not trusted")
        issuer = _required_string(claims, "iss")
        if issuer != key.issuer:
            raise CredentialAuthorityDenied(
                "workload assertion issuer does not match its trusted key"
            )
        try:
            from cryptography.exceptions import InvalidSignature
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        except ImportError:
            raise CredentialAuthorityUnavailable(
                "cryptography is required for Ed25519 workload assertion verification"
            ) from None
        try:
            public_key = Ed25519PublicKey.from_public_bytes(key.public_key_ed25519)
            public_key.verify(
                _b64url_decode(parts[2]),
                f"{parts[0]}.{parts[1]}".encode("ascii"),
            )
        except (InvalidSignature, ValueError, UnicodeEncodeError):
            raise CredentialAuthorityDenied("workload assertion signature is invalid") from None

        if claims.get("aud") != self._audience:
            raise CredentialAuthorityDenied("workload assertion audience mismatch")
        issued_at = _required_timestamp(claims, "iat")
        expires_at = _required_timestamp(claims, "exp")
        now = self._clock()
        if issued_at > now + self._skew:
            raise CredentialAuthorityDenied("workload assertion is not yet valid")
        if expires_at <= now:
            raise CredentialAuthorityDenied("workload assertion has expired")
        if expires_at <= issued_at or expires_at - issued_at > self._max_ttl:
            raise CredentialAuthorityDenied(
                "workload assertion lifetime exceeds the allowed bound"
            )

        values = {
            name: _required_string(claims, name)
            for name in (
                "sub", "tenant_id", "agent_id", "release_id", "session_id",
                "run_id", "sandbox_id", "workspace_ref", "binding_id",
                "capability", "credential_audience", "request_id", "jti",
            )
        }
        revision = claims.get("sandbox_revision")
        if type(revision) is not int or revision <= 0:
            raise CredentialAuthorityDenied(
                "workload assertion sandbox_revision is invalid"
            )
        requested_ttl = claims.get("requested_ttl_seconds")
        if type(requested_ttl) is not int or requested_ttl <= 0:
            raise CredentialAuthorityDenied(
                "workload assertion requested_ttl_seconds is invalid"
            )
        approval_id = claims.get("approval_id")
        if approval_id is not None and (
            not isinstance(approval_id, str) or not approval_id.strip()
        ):
            raise CredentialAuthorityDenied("workload assertion approval_id is invalid")
        if values["sub"] != f"run:{values['run_id']}":
            raise CredentialAuthorityDenied(
                "workload assertion subject is not bound to its Run"
            )
        allowed_scopes = _scopes(claims, "allowed_scopes")
        requested_scopes = _scopes(claims, "requested_scopes")
        if not set(requested_scopes).issubset(set(allowed_scopes)):
            raise CredentialAuthorityDenied(
                "requested scopes exceed the signed workload grant"
            )
        return VerifiedWorkloadAssertion(
            issuer=issuer,
            subject=values["sub"],
            tenant_id=values["tenant_id"],
            agent_id=values["agent_id"],
            release_id=values["release_id"],
            session_id=values["session_id"],
            run_id=values["run_id"],
            sandbox_id=values["sandbox_id"],
            sandbox_revision=revision,
            workspace_ref=values["workspace_ref"],
            binding_id=values["binding_id"],
            capability=values["capability"],
            credential_audience=values["credential_audience"],
            allowed_scopes=allowed_scopes,
            requested_scopes=requested_scopes,
            request_id=values["request_id"],
            requested_ttl_seconds=requested_ttl,
            approval_id=approval_id,
            issued_at=issued_at,
            expires_at=expires_at,
            token_id=values["jti"],
        )


@dataclass(frozen=True)
class CredentialBindingRecord:
    binding_id: str
    capability: str
    audience: str
    allowed_scopes: tuple[str, ...]
    max_ttl_seconds: int
    enabled: bool = True

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) and value.strip() for value in (
            self.binding_id, self.capability, self.audience
        )):
            raise ValueError("credential binding identifiers and audience are required")
        if (
            not self.allowed_scopes
            or any(not isinstance(scope, str) or not scope.strip() for scope in self.allowed_scopes)
            or len(set(self.allowed_scopes)) != len(self.allowed_scopes)
        ):
            raise ValueError("credential binding scopes must be non-empty and unique")
        if type(self.max_ttl_seconds) is not int or self.max_ttl_seconds <= 0:
            raise ValueError("credential binding TTL must be a positive integer")


@dataclass(frozen=True)
class CredentialApprovalRecord:
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

    def as_grant(self) -> dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "tenant_id": self.tenant_id,
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "binding_id": self.binding_id,
            "audience": self.audience,
            "scopes": list(self.scopes),
            "approved_by": self.approved_by,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
        }


def _sensitive(scopes: tuple[str, ...]) -> bool:
    return any(
        scope.rsplit(":", 1)[-1].lower() in {"write", "delete", "admin", "mutate"}
        for scope in scopes
    )


class WorkloadCredentialStore(Protocol):
    def provision_binding(self, binding: CredentialBindingRecord) -> None: ...
    def record_approval(self, approval: CredentialApprovalRecord, *, now: float) -> None: ...
    def read_approval(self, approval_id: str, *, now: float) -> Mapping[str, Any] | None: ...
    def issue_credential(
        self, assertion: VerifiedWorkloadAssertion, *, request_id: str,
        binding_id: str, audience: str, scopes: tuple[str, ...],
        requested_ttl_seconds: int, approval_id: str | None, token: str,
        token_digest: str, now: float,
    ) -> Mapping[str, Any]: ...
    def introspect(
        self, token_digest: str, *, audience: str,
        required_scopes: tuple[str, ...], now: float,
    ) -> Mapping[str, Any]: ...
    def revoke_credential(self, credential_id: str, *, now: float) -> bool: ...
    def revoke_run(self, run_id: str, *, sandbox_revision: int, now: float) -> int: ...
    def revoke_approval(
        self, approval_id: str, *, event_id: str, source: str, now: float
    ) -> tuple[int, int]: ...
    def revoke_approval_credentials(
        self, approval_id: str, *, event_id: str, now: float
    ) -> int: ...


_AUTHORITY_SCHEMA = """CREATE TABLE IF NOT EXISTS public.acp_workload_credential_schema (
  version integer PRIMARY KEY CHECK (version=1),
  checksum char(64) NOT NULL
);
CREATE TABLE IF NOT EXISTS public.acp_workload_credential_bindings (
  binding_id text PRIMARY KEY,
  capability text NOT NULL,
  audience text NOT NULL,
  allowed_scopes jsonb NOT NULL CHECK (jsonb_typeof(allowed_scopes)='array'),
  max_ttl_seconds integer NOT NULL CHECK (max_ttl_seconds > 0),
  enabled boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS public.acp_workload_credential_approvals (
  approval_id text PRIMARY KEY,
  tenant_id text NOT NULL,
  agent_id text NOT NULL,
  session_id text NOT NULL,
  run_id text NOT NULL,
  binding_id text NOT NULL REFERENCES public.acp_workload_credential_bindings(binding_id),
  audience text NOT NULL,
  scopes jsonb NOT NULL CHECK (jsonb_typeof(scopes)='array'),
  approved_by text NOT NULL,
  issued_at timestamptz NOT NULL,
  expires_at timestamptz NOT NULL,
  state text NOT NULL CHECK (state IN ('ACTIVE','REVOKED','DENIED')),
  version integer NOT NULL CHECK (version > 0),
  revoked_event_id text,
  CHECK (expires_at > issued_at)
);
CREATE TABLE IF NOT EXISTS public.acp_workload_credential_requests (
  run_id text NOT NULL,
  binding_id text NOT NULL REFERENCES public.acp_workload_credential_bindings(binding_id),
  request_id text NOT NULL,
  request_fingerprint char(64) NOT NULL,
  credential_id text NOT NULL,
  created_at timestamptz NOT NULL,
  PRIMARY KEY (run_id,binding_id,request_id),
  UNIQUE (credential_id)
);
CREATE TABLE IF NOT EXISTS public.acp_workload_credentials (
  credential_id text PRIMARY KEY,
  token_digest char(64) NOT NULL UNIQUE,
  tenant_id text NOT NULL,
  agent_id text NOT NULL,
  release_id text NOT NULL,
  session_id text NOT NULL,
  run_id text NOT NULL,
  sandbox_id text NOT NULL,
  sandbox_revision integer NOT NULL CHECK (sandbox_revision > 0),
  workspace_ref text NOT NULL,
  binding_id text NOT NULL REFERENCES public.acp_workload_credential_bindings(binding_id),
  capability text NOT NULL,
  audience text NOT NULL,
  scopes jsonb NOT NULL CHECK (jsonb_typeof(scopes)='array'),
  issued_at timestamptz NOT NULL,
  expires_at timestamptz NOT NULL,
  approval_id text REFERENCES public.acp_workload_credential_approvals(approval_id),
  revoked_at timestamptz,
  CHECK (expires_at > issued_at)
);
CREATE INDEX IF NOT EXISTS acp_workload_credentials_by_run
  ON public.acp_workload_credentials(run_id,sandbox_revision);
CREATE INDEX IF NOT EXISTS acp_workload_credentials_by_approval
  ON public.acp_workload_credentials(approval_id) WHERE approval_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS public.acp_workload_run_fences (
  run_id text PRIMARY KEY,
  fenced_through_sandbox_revision integer NOT NULL CHECK (fenced_through_sandbox_revision >= 0),
  updated_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS public.acp_workload_approval_revocations (
  event_id text PRIMARY KEY,
  approval_id text NOT NULL,
  source text NOT NULL,
  created_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS public.acp_workload_credential_revocation_events (
  event_id text PRIMARY KEY,
  approval_id text NOT NULL,
  created_at timestamptz NOT NULL
);
"""
_AUTHORITY_SCHEMA_CHECKSUM = hashlib.sha256(_AUTHORITY_SCHEMA.encode("utf-8")).hexdigest()


class PostgresWorkloadCredentialStore:
    """Operator-owned PostgreSQL authority store with atomic issuance and fencing.

    Runtime request handlers must never install or migrate this schema. Provision
    the schema and credential bindings using a separate operator identity.
    """

    def __init__(self, dsn: str) -> None:
        if not isinstance(dsn, str) or not dsn.strip():
            raise ValueError("explicit workload credential authority DSN is required")
        self.dsn = dsn

    def _connection(self):
        try:
            from psycopg import connect
            from psycopg.rows import dict_row
        except ImportError:
            raise CredentialAuthorityUnavailable(
                "psycopg is required for the PostgreSQL credential store"
            ) from None
        try:
            return connect(self.dsn, row_factory=dict_row, connect_timeout=5)
        except Exception:
            raise CredentialAuthorityUnavailable(
                "credential authority database is unavailable"
            ) from None

    def install_schema(self) -> None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT has_schema_privilege(current_user,'public','CREATE') AS permitted"
            ).fetchone()
            if row is None or row["permitted"] is not True:
                raise CredentialAuthorityDenied(
                    "workload credential schema installation requires operator CREATE privilege"
                )
            conn.execute(_AUTHORITY_SCHEMA)
            conn.execute(
                """INSERT INTO public.acp_workload_credential_schema(version,checksum)
                   VALUES (1,%s)
                   ON CONFLICT (version) DO NOTHING""",
                (_AUTHORITY_SCHEMA_CHECKSUM,),
            )
            row = conn.execute(
                "SELECT version,checksum FROM public.acp_workload_credential_schema"
            ).fetchall()
            if row != [{"version": 1, "checksum": _AUTHORITY_SCHEMA_CHECKSUM}]:
                raise CredentialAuthorityConflict("workload credential schema checksum drift")

    def validate_schema(self) -> None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT version,checksum FROM public.acp_workload_credential_schema"
            ).fetchall()
            if row != [{"version": 1, "checksum": _AUTHORITY_SCHEMA_CHECKSUM}]:
                raise CredentialAuthorityConflict(
                    "workload credential authority schema is missing or drifted"
                )

    def provision_binding(self, binding: CredentialBindingRecord) -> None:
        from psycopg.types.json import Jsonb

        with self._connection() as conn:
            conn.execute(
                """INSERT INTO public.acp_workload_credential_bindings
                   (binding_id,capability,audience,allowed_scopes,max_ttl_seconds,enabled)
                   VALUES (%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (binding_id) DO NOTHING""",
                (
                    binding.binding_id, binding.capability, binding.audience,
                    Jsonb(list(binding.allowed_scopes)), binding.max_ttl_seconds,
                    binding.enabled,
                ),
            )
            row = conn.execute(
                """SELECT capability,audience,allowed_scopes,max_ttl_seconds,enabled
                   FROM public.acp_workload_credential_bindings
                   WHERE binding_id=%s""",
                (binding.binding_id,),
            ).fetchone()
            if row is None or (
                row["capability"] != binding.capability
                or row["audience"] != binding.audience
                or tuple(row["allowed_scopes"]) != tuple(binding.allowed_scopes)
                or row["max_ttl_seconds"] != binding.max_ttl_seconds
                or row["enabled"] is not binding.enabled
            ):
                raise CredentialAuthorityConflict(
                    "immutable credential binding already exists with different semantics"
                )

    def record_approval(self, approval: CredentialApprovalRecord, *, now: float) -> None:
        from datetime import datetime, timezone
        from psycopg.types.json import Jsonb

        if (
            not all(isinstance(v, str) and v.strip() for v in (
                approval.approval_id, approval.tenant_id, approval.agent_id,
                approval.session_id, approval.run_id, approval.binding_id,
                approval.audience, approval.approved_by,
            ))
            or not approval.scopes
            or len(set(approval.scopes)) != len(approval.scopes)
            or approval.issued_at > now
            or approval.expires_at <= now
        ):
            raise CredentialAuthorityDenied("approval grant is invalid or expired")
        with self._connection() as conn:
            conn.execute(
                """INSERT INTO public.acp_workload_credential_approvals
                   (approval_id,tenant_id,agent_id,session_id,run_id,binding_id,
                    audience,scopes,approved_by,issued_at,expires_at,state,version)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'ACTIVE',1)
                   ON CONFLICT (approval_id) DO NOTHING""",
                (
                    approval.approval_id, approval.tenant_id, approval.agent_id,
                    approval.session_id, approval.run_id, approval.binding_id,
                    approval.audience, Jsonb(list(approval.scopes)),
                    approval.approved_by,
                    datetime.fromtimestamp(approval.issued_at, timezone.utc),
                    datetime.fromtimestamp(approval.expires_at, timezone.utc),
                ),
            )
            stored = conn.execute(
                """SELECT tenant_id,agent_id,session_id,run_id,binding_id,audience,
                          scopes,approved_by,extract(epoch from issued_at) AS issued_at,
                          extract(epoch from expires_at) AS expires_at
                   FROM public.acp_workload_credential_approvals WHERE approval_id=%s""",
                (approval.approval_id,),
            ).fetchone()
            if stored is None or (
                stored["tenant_id"] != approval.tenant_id
                or stored["agent_id"] != approval.agent_id
                or stored["session_id"] != approval.session_id
                or stored["run_id"] != approval.run_id
                or stored["binding_id"] != approval.binding_id
                or stored["audience"] != approval.audience
                or tuple(stored["scopes"]) != tuple(approval.scopes)
                or stored["approved_by"] != approval.approved_by
                or float(stored["issued_at"]) != approval.issued_at
                or float(stored["expires_at"]) != approval.expires_at
            ):
                raise CredentialAuthorityConflict(
                    "approval ID already exists with different immutable grant"
                )

    @staticmethod
    def _approval_payload(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "approval_id": row["approval_id"],
            "tenant_id": row["tenant_id"],
            "agent_id": row["agent_id"],
            "session_id": row["session_id"],
            "run_id": row["run_id"],
            "binding_id": row["binding_id"],
            "audience": row["audience"],
            "scopes": list(row["scopes"]),
            "approved_by": row["approved_by"],
            "issued_at": float(row["issued_at"]),
            "expires_at": float(row["expires_at"]),
        }

    def read_approval(self, approval_id: str, *, now: float) -> Mapping[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute(
                """SELECT approval_id,tenant_id,agent_id,session_id,run_id,binding_id,
                          audience,scopes,approved_by,extract(epoch from issued_at) AS issued_at,
                          extract(epoch from expires_at) AS expires_at,state,version
                   FROM public.acp_workload_credential_approvals WHERE approval_id=%s""",
                (approval_id,),
            ).fetchone()
        if row is None:
            return None
        state = row["state"]
        if float(row["expires_at"]) <= now and state == "ACTIVE":
            state = "EXPIRED"
        return {
            "state": state,
            "grant": self._approval_payload(row),
            "version": row["version"],
        }

    def issue_credential(
        self,
        assertion: VerifiedWorkloadAssertion,
        *,
        request_id: str,
        binding_id: str,
        audience: str,
        scopes: tuple[str, ...],
        requested_ttl_seconds: int,
        approval_id: str | None,
        token: str,
        token_digest: str,
        now: float,
    ) -> Mapping[str, Any]:
        from datetime import datetime, timezone
        from psycopg.types.json import Jsonb

        if (
            request_id != assertion.request_id
            or binding_id != assertion.binding_id
            or audience != assertion.credential_audience
            or tuple(sorted(scopes)) != assertion.requested_scopes
            or requested_ttl_seconds != assertion.requested_ttl_seconds
            or approval_id != assertion.approval_id
        ):
            raise CredentialAuthorityDenied(
                "issue request differs from signed workload assertion"
            )
        if type(requested_ttl_seconds) is not int or requested_ttl_seconds <= 0:
            raise CredentialAuthorityDenied("requested credential TTL is invalid")
        if assertion.expires_at <= now:
            raise CredentialAuthorityDenied("signed workload assertion has expired")
        if not set(scopes).issubset(set(assertion.allowed_scopes)):
            raise CredentialAuthorityDenied("requested scopes exceed signed workload scopes")

        identity = assertion.identity_dict()
        fingerprint = hashlib.sha256(json.dumps(
            {
                "request_id": request_id,
                "binding_id": binding_id,
                "audience": audience,
                "scopes": list(sorted(scopes)),
                "assertion_jti": assertion.token_id,
                "sandbox_revision": assertion.sandbox_revision,
            },
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        credential_id = str(uuid4())

        with self._connection() as conn:
            binding = conn.execute(
                """SELECT capability,audience,allowed_scopes,max_ttl_seconds,enabled
                   FROM public.acp_workload_credential_bindings
                   WHERE binding_id=%s FOR SHARE""",
                (binding_id,),
            ).fetchone()
            if binding is None or binding["enabled"] is not True:
                raise CredentialAuthorityDenied("credential binding is not enabled")
            if (
                binding["capability"] != assertion.capability
                or binding["audience"] != audience
                or set(scopes) - set(binding["allowed_scopes"])
            ):
                raise CredentialAuthorityDenied(
                    "scope or audience is outside configured credential binding"
                )

            now_dt = datetime.fromtimestamp(now, timezone.utc)
            conn.execute(
                """INSERT INTO public.acp_workload_run_fences
                   (run_id,fenced_through_sandbox_revision,updated_at)
                   VALUES (%s,0,%s) ON CONFLICT (run_id) DO NOTHING""",
                (assertion.run_id, now_dt),
            )
            fence = conn.execute(
                """SELECT fenced_through_sandbox_revision
                   FROM public.acp_workload_run_fences WHERE run_id=%s FOR UPDATE""",
                (assertion.run_id,),
            ).fetchone()
            if (
                fence is None
                or assertion.sandbox_revision <= fence["fenced_through_sandbox_revision"]
            ):
                raise CredentialAuthorityDenied(
                    "sandbox revision has been fenced by Run revocation"
                )

            approval_expires_at: float | None = None
            sensitive = _sensitive(tuple(scopes))
            if sensitive and approval_id is None:
                raise CredentialAuthorityDenied(
                    "sensitive credential scopes require an authoritative approval"
                )
            if approval_id is not None and not sensitive:
                raise CredentialAuthorityDenied(
                    "approval ID must not widen a read-only credential"
                )
            if sensitive:
                approval = conn.execute(
                    """SELECT tenant_id,agent_id,session_id,run_id,binding_id,audience,
                              scopes,approved_by,extract(epoch from issued_at) AS issued_at,
                              extract(epoch from expires_at) AS expires_at,state
                       FROM public.acp_workload_credential_approvals
                       WHERE approval_id=%s FOR SHARE""",
                    (approval_id,),
                ).fetchone()
                if approval is None or approval["state"] != "ACTIVE":
                    raise CredentialAuthorityDenied(
                        "approval is missing, denied, or revoked"
                    )
                if (
                    float(approval["expires_at"]) <= now
                    or float(approval["issued_at"]) > now
                    or approval["tenant_id"] != assertion.tenant_id
                    or approval["agent_id"] != assertion.agent_id
                    or approval["session_id"] != assertion.session_id
                    or approval["run_id"] != assertion.run_id
                    or approval["binding_id"] != binding_id
                    or approval["audience"] != audience
                    or not set(scopes).issubset(set(approval["scopes"]))
                    or not str(approval["approved_by"]).strip()
                ):
                    raise CredentialAuthorityDenied(
                        "approval is expired or not bound to the exact workload"
                    )
                approval_expires_at = float(approval["expires_at"])

            expires_at = min(
                now + requested_ttl_seconds,
                now + int(binding["max_ttl_seconds"]),
                assertion.expires_at,
                approval_expires_at if approval_expires_at is not None else float("inf"),
            )
            if expires_at <= now:
                raise CredentialAuthorityDenied("credential has no positive lifetime")

            consumed = conn.execute(
                """INSERT INTO public.acp_workload_credential_requests
                   (run_id,binding_id,request_id,request_fingerprint,credential_id,created_at)
                   VALUES (%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (run_id,binding_id,request_id) DO NOTHING
                   RETURNING request_id""",
                (
                    assertion.run_id, binding_id, request_id, fingerprint,
                    credential_id, now_dt,
                ),
            ).fetchone()
            if consumed is None:
                raise CredentialAuthorityConflict(
                    "credential issuance request ID has already been consumed"
                )

            expires_dt = datetime.fromtimestamp(expires_at, timezone.utc)
            conn.execute(
                """INSERT INTO public.acp_workload_credentials
                   (credential_id,token_digest,tenant_id,agent_id,release_id,session_id,
                    run_id,sandbox_id,sandbox_revision,workspace_ref,binding_id,capability,
                    audience,scopes,issued_at,expires_at,approval_id)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    credential_id, token_digest, assertion.tenant_id, assertion.agent_id,
                    assertion.release_id, assertion.session_id, assertion.run_id,
                    assertion.sandbox_id, assertion.sandbox_revision, assertion.workspace_ref,
                    binding_id, assertion.capability, audience, Jsonb(list(sorted(scopes))),
                    now_dt, expires_dt, approval_id if sensitive else None,
                ),
            )
        return {
            "credential_id": credential_id,
            "access_token": token,
            "audience": audience,
            "scopes": list(sorted(scopes)),
            "issued_at": now,
            "expires_at": expires_at,
            "run_id": assertion.run_id,
            "session_id": assertion.session_id,
            "binding_id": binding_id,
            "sandbox_id": assertion.sandbox_id,
            "sandbox_revision": assertion.sandbox_revision,
        }

    def introspect(
        self,
        token_digest: str,
        *,
        audience: str,
        required_scopes: tuple[str, ...],
        now: float,
    ) -> Mapping[str, Any]:
        with self._connection() as conn:
            row = conn.execute(
                """SELECT c.credential_id,c.tenant_id,c.agent_id,c.release_id,c.session_id,
                          c.run_id,c.sandbox_id,c.sandbox_revision,c.workspace_ref,
                          c.binding_id,c.capability,c.audience,c.scopes,
                          extract(epoch from c.issued_at) AS issued_at,
                          extract(epoch from c.expires_at) AS expires_at,c.approval_id,
                          c.revoked_at IS NOT NULL AS revoked,
                          a.state AS approval_state,
                          extract(epoch from a.expires_at) AS approval_expires_at,
                          COALESCE(f.fenced_through_sandbox_revision,0) AS fence
                   FROM public.acp_workload_credentials c
                   LEFT JOIN public.acp_workload_credential_approvals a
                     ON a.approval_id=c.approval_id
                   LEFT JOIN public.acp_workload_run_fences f ON f.run_id=c.run_id
                   WHERE c.token_digest=%s""",
                (token_digest,),
            ).fetchone()
        if row is None:
            return {"active": False}
        active = (
            not row["revoked"]
            and float(row["expires_at"]) > now
            and row["sandbox_revision"] > row["fence"]
            and row["audience"] == audience
            and set(required_scopes).issubset(set(row["scopes"]))
            and (
                row["approval_id"] is None
                or (
                    row["approval_state"] == "ACTIVE"
                    and row["approval_expires_at"] is not None
                    and float(row["approval_expires_at"]) > now
                )
            )
        )
        if not active:
            return {"active": False}
        return {
            "active": True,
            "credential_id": row["credential_id"],
            "audience": row["audience"],
            "scopes": list(row["scopes"]),
            "issued_at": float(row["issued_at"]),
            "expires_at": float(row["expires_at"]),
            "run_id": row["run_id"],
            "session_id": row["session_id"],
            "binding_id": row["binding_id"],
            "sandbox_id": row["sandbox_id"],
            "sandbox_revision": row["sandbox_revision"],
            "workload_identity": {
                "issuer": "cloud-agent-runtime",
                "subject": f"run:{row['run_id']}",
                "tenant_id": row["tenant_id"],
                "agent_id": row["agent_id"],
                "release_id": row["release_id"],
                "session_id": row["session_id"],
                "run_id": row["run_id"],
                "sandbox_id": row["sandbox_id"],
                "sandbox_revision": row["sandbox_revision"],
                "workspace_ref": row["workspace_ref"],
                "binding_id": row["binding_id"],
                "capability": row["capability"],
                "audience": row["audience"],
                "allowed_scopes": list(row["scopes"]),
                "issued_at": float(row["issued_at"]),
                "expires_at": float(row["expires_at"]),
            },
        }

    def revoke_credential(self, credential_id: str, *, now: float) -> bool:
        from datetime import datetime, timezone

        with self._connection() as conn:
            row = conn.execute(
                """UPDATE public.acp_workload_credentials
                   SET revoked_at=COALESCE(revoked_at,%s)
                   WHERE credential_id=%s AND revoked_at IS NULL
                   RETURNING credential_id""",
                (datetime.fromtimestamp(now, timezone.utc), credential_id),
            ).fetchone()
        return row is not None

    def revoke_run(self, run_id: str, *, sandbox_revision: int, now: float) -> int:
        from datetime import datetime, timezone

        if type(sandbox_revision) is not int or sandbox_revision <= 0:
            raise CredentialAuthorityDenied("sandbox_revision must be positive")
        now_dt = datetime.fromtimestamp(now, timezone.utc)
        with self._connection() as conn:
            conn.execute(
                """INSERT INTO public.acp_workload_run_fences
                   (run_id,fenced_through_sandbox_revision,updated_at)
                   VALUES (%s,%s,%s)
                   ON CONFLICT (run_id) DO UPDATE SET
                     fenced_through_sandbox_revision=GREATEST(
                       public.acp_workload_run_fences.fenced_through_sandbox_revision,
                       EXCLUDED.fenced_through_sandbox_revision
                     ),
                     updated_at=EXCLUDED.updated_at""",
                (run_id, sandbox_revision, now_dt),
            )
            rows = conn.execute(
                """UPDATE public.acp_workload_credentials
                   SET revoked_at=COALESCE(revoked_at,%s)
                   WHERE run_id=%s AND sandbox_revision <= %s AND revoked_at IS NULL
                   RETURNING credential_id""",
                (now_dt, run_id, sandbox_revision),
            ).fetchall()
        return len(rows)

    def revoke_approval(
        self, approval_id: str, *, event_id: str, source: str, now: float
    ) -> tuple[int, int]:
        from datetime import datetime, timezone

        if not all(isinstance(v, str) and v.strip() for v in (approval_id, event_id, source)):
            raise CredentialAuthorityDenied(
                "approval revocation requires approval_id, event_id, and source"
            )
        now_dt = datetime.fromtimestamp(now, timezone.utc)
        with self._connection() as conn:
            approval = conn.execute(
                """SELECT state,version FROM public.acp_workload_credential_approvals
                   WHERE approval_id=%s FOR UPDATE""",
                (approval_id,),
            ).fetchone()
            if approval is None:
                raise CredentialAuthorityNotFound("approval does not exist")
            event = conn.execute(
                """INSERT INTO public.acp_workload_approval_revocations
                   (event_id,approval_id,source,created_at) VALUES (%s,%s,%s,%s)
                   ON CONFLICT (event_id) DO NOTHING RETURNING event_id""",
                (event_id, approval_id, source, now_dt),
            ).fetchone()
            if event is None:
                recorded = conn.execute(
                    "SELECT approval_id FROM public.acp_workload_approval_revocations WHERE event_id=%s",
                    (event_id,),
                ).fetchone()
                if recorded is None or recorded["approval_id"] != approval_id:
                    raise CredentialAuthorityConflict("approval revocation event ID was reused")
                return int(approval["version"]), 0
            version = int(approval["version"])
            if approval["state"] == "ACTIVE":
                version += 1
                conn.execute(
                    """UPDATE public.acp_workload_credential_approvals
                       SET state='REVOKED',version=%s,revoked_event_id=%s
                       WHERE approval_id=%s""",
                    (version, event_id, approval_id),
                )
        return version, 0

    def revoke_approval_credentials(self, approval_id: str, *, event_id: str, now: float) -> int:
        from datetime import datetime, timezone

        now_dt = datetime.fromtimestamp(now, timezone.utc)
        with self._connection() as conn:
            event = conn.execute(
                """SELECT a.state,e.approval_id
                   FROM public.acp_workload_approval_revocations e
                   JOIN public.acp_workload_credential_approvals a ON a.approval_id=e.approval_id
                   WHERE e.event_id=%s""",
                (event_id,),
            ).fetchone()
            if event is None or event["approval_id"] != approval_id or event["state"] != "REVOKED":
                raise CredentialAuthorityDenied(
                    "approval revocation must be authoritative before credential fan-out"
                )
            inserted = conn.execute(
                """INSERT INTO public.acp_workload_credential_revocation_events
                   (event_id,approval_id,created_at) VALUES (%s,%s,%s)
                   ON CONFLICT (event_id) DO NOTHING RETURNING event_id""",
                (event_id, approval_id, now_dt),
            ).fetchone()
            if inserted is None:
                existing = conn.execute(
                    "SELECT approval_id FROM public.acp_workload_credential_revocation_events WHERE event_id=%s",
                    (event_id,),
                ).fetchone()
                if existing is None or existing["approval_id"] != approval_id:
                    raise CredentialAuthorityConflict("credential revocation event ID was reused")
                return 0
            rows = conn.execute(
                """UPDATE public.acp_workload_credentials
                   SET revoked_at=COALESCE(revoked_at,%s)
                   WHERE approval_id=%s AND revoked_at IS NULL
                   RETURNING credential_id""",
                (now_dt, approval_id),
            ).fetchall()
        return len(rows)


class WorkloadCredentialAuthorityService:
    """Apply request validation around the operator-owned PostgreSQL store."""

    def __init__(
        self,
        *,
        store: WorkloadCredentialStore,
        assertion_verifier: WorkloadAssertionVerifier,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self.assertion_verifier = assertion_verifier
        self.clock = clock

    def issue(self, body: Mapping[str, Any]) -> dict[str, Any]:
        assertion_text = _required_string(body, "workload_assertion")
        assertion = self.assertion_verifier.verify(assertion_text)
        request_id = _required_string(body, "request_id")
        binding_id = _required_string(body, "binding_id")
        audience = _required_string(body, "audience")
        scopes_raw = body.get("scopes")
        if (
            not isinstance(scopes_raw, list)
            or not scopes_raw
            or any(not isinstance(scope, str) or not scope.strip() for scope in scopes_raw)
            or len(scopes_raw) != len(set(scopes_raw))
        ):
            raise CredentialAuthorityDenied("credential issue scopes are invalid")
        scopes = tuple(sorted(scopes_raw))
        ttl = body.get("requested_ttl_seconds")
        if type(ttl) is not int or ttl <= 0:
            raise CredentialAuthorityDenied("requested credential TTL is invalid")
        approval_id = body.get("approval_id")
        if approval_id is not None and (
            not isinstance(approval_id, str) or not approval_id.strip()
        ):
            raise CredentialAuthorityDenied("approval_id is invalid")
        if (
            request_id != assertion.request_id
            or binding_id != assertion.binding_id
            or audience != assertion.credential_audience
            or scopes != assertion.requested_scopes
            or ttl != assertion.requested_ttl_seconds
            or approval_id != assertion.approval_id
        ):
            raise CredentialAuthorityDenied(
                "request fields do not match the signed workload assertion"
            )
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        credential = self.store.issue_credential(
            assertion,
            request_id=request_id,
            binding_id=binding_id,
            audience=audience,
            scopes=scopes,
            requested_ttl_seconds=ttl,
            approval_id=approval_id,
            token=token,
            token_digest=digest,
            now=self.clock(),
        )
        return {"credential": dict(credential)}

    def introspect(self, body: Mapping[str, Any]) -> dict[str, Any]:
        token = _required_string(body, "access_token")
        audience = _required_string(body, "audience")
        required_raw = body.get("required_scopes", [])
        if (
            not isinstance(required_raw, list)
            or any(not isinstance(scope, str) or not scope.strip() for scope in required_raw)
            or len(required_raw) != len(set(required_raw))
        ):
            raise CredentialAuthorityDenied("required_scopes is invalid")
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return {"credential": dict(self.store.introspect(
            digest,
            audience=audience,
            required_scopes=tuple(required_raw),
            now=self.clock(),
        ))}

    def revoke_credential(self, body: Mapping[str, Any]) -> dict[str, Any]:
        credential_id = _required_string(body, "credential_id")
        return {"revoked": self.store.revoke_credential(credential_id, now=self.clock())}

    def revoke_run(self, body: Mapping[str, Any]) -> dict[str, Any]:
        run_id = _required_string(body, "run_id")
        revision = body.get("sandbox_revision")
        if type(revision) is not int or revision <= 0:
            raise CredentialAuthorityDenied("sandbox_revision must be a positive integer")
        count = self.store.revoke_run(run_id, sandbox_revision=revision, now=self.clock())
        return {"revoked_count": count}

    def read_approval(self, approval_id: str) -> dict[str, Any]:
        value = self.store.read_approval(approval_id, now=self.clock())
        if value is None:
            raise CredentialAuthorityNotFound("approval does not exist")
        return dict(value)

    def record_approval(self, body: Mapping[str, Any]) -> dict[str, Any]:
        scopes = _scopes(body, "scopes")
        record = CredentialApprovalRecord(
            approval_id=_required_string(body, "approval_id"),
            tenant_id=_required_string(body, "tenant_id"),
            agent_id=_required_string(body, "agent_id"),
            session_id=_required_string(body, "session_id"),
            run_id=_required_string(body, "run_id"),
            binding_id=_required_string(body, "binding_id"),
            audience=_required_string(body, "audience"),
            scopes=scopes,
            approved_by=_required_string(body, "approved_by"),
            issued_at=_required_timestamp(body, "issued_at"),
            expires_at=_required_timestamp(body, "expires_at"),
        )
        self.store.record_approval(record, now=self.clock())
        return {"state": "ACTIVE", "grant": record.as_grant(), "version": 1}

    def revoke_approval(self, approval_id: str, body: Mapping[str, Any], *, source: str) -> dict[str, Any]:
        event_id = _required_string(body, "event_id")
        version, _ = self.store.revoke_approval(
            approval_id, event_id=event_id, source=source, now=self.clock()
        )
        return {"state": "REVOKED", "version": version}

    def revoke_approval_credentials(self, body: Mapping[str, Any]) -> dict[str, Any]:
        approval_id = _required_string(body, "approval_id")
        event_id = _required_string(body, "event_id")
        count = self.store.revoke_approval_credentials(
            approval_id, event_id=event_id, now=self.clock()
        )
        return {"revoked_count": count}


def _required_string(body: Mapping[str, Any], name: str) -> str:
    value = body.get(name)
    if not isinstance(value, str) or not value.strip():
        raise CredentialAuthorityDenied(f"request field {name} is missing or invalid")
    return value


def provision_binding_from_environment() -> None:
    """CLI helper deliberately omitted: binding provisioning is operator-managed."""
    raise CredentialAuthorityDenied(
        "provisioning requires explicit operator code and must not run in request handlers"
    )
