"""Opt-in PostgreSQL 16 verifier journal for ACP #191.

This is a durable, scoped *historical proof-reference* receipt, not a
provider write permission, live observer, signer provisioning API or GO gate.
The operator installs schema and provisions trusted public keys separately.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from psycopg import connect
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row

from .state_transition_protocol import ProtocolViolation
from .trusted_proof_pg_security import (
    grant_runtime_access,
    install_security_boundary,
    require_schema_operator,
    validate_security_boundary,
)
from .trusted_proof_envelope import (
    CONTEXT_KEYS,
    TrustedSigner,
    verify_signed_proof_reference,
)

SCHEMA_SQL = """
CREATE TABLE public.acp_trusted_proof_signers (
    tenant_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    keyid TEXT NOT NULL,
    public_key BYTEA NOT NULL CHECK (octet_length(public_key)=32),
    policy_revision TEXT NOT NULL,
    max_age_seconds INTEGER NOT NULL CHECK (max_age_seconds BETWEEN 1 AND 3600),
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (tenant_id, project_id, keyid)
);
CREATE TABLE public.acp_trusted_proof_receipts (
    tenant_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    cluster_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    resource_uid TEXT NOT NULL,
    nonce TEXT NOT NULL,
    signer_keyid TEXT NOT NULL,
    policy_revision TEXT NOT NULL,
    proof_id TEXT NOT NULL,
    proof_statement_hash TEXT NOT NULL,
    envelope_sha256 CHAR(64) NOT NULL,
    context_sha256 CHAR(64) NOT NULL,
    issued_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    accepted_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_id, project_id, cluster_id, operation_id),
    UNIQUE (tenant_id, project_id, nonce),
    FOREIGN KEY (tenant_id, project_id, signer_keyid)
        REFERENCES public.acp_trusted_proof_signers(tenant_id, project_id, keyid)
        ON DELETE RESTRICT
);
"""
LEDGER_SQL = """
CREATE TABLE public.acp_trusted_proof_schema (
    version INTEGER PRIMARY KEY CHECK (version=1),
    checksum CHAR(64) NOT NULL
);
"""
SCHEMA_CHECKSUM = hashlib.sha256(SCHEMA_SQL.encode("utf-8")).hexdigest()
ADVISORY_LOCK = 657805283175307


@dataclass(frozen=True)
class DurableProofReceipt:
    """Database-backed idempotent authentication record, NOT authorization."""

    status: str  # INSERTED or DUPLICATE
    proof_id: str
    proof_statement_hash: str
    signer_keyid: str
    operation_id: str
    nonce: str


class TrustedProofPostgres:
    """Uses an explicitly installed, separately versioned PostgreSQL schema."""

    def __init__(self, dsn: str) -> None:
        if not isinstance(dsn, str) or not dsn.strip():
            raise ProtocolViolation("explicit isolated PostgreSQL DSN is required")
        self.dsn = dsn

    def _connection(self):
        return connect(self.dsn, row_factory=dict_row, connect_timeout=5)

    @staticmethod
    def _verify_schema(conn) -> None:
        records = conn.execute(
            "SELECT version, checksum FROM public.acp_trusted_proof_schema"
        ).fetchall()
        if records != [{"version": 1, "checksum": SCHEMA_CHECKSUM}]:
            raise ProtocolViolation("trusted proof schema checksum/version mismatch")
        expected = {
            "acp_trusted_proof_signers": {
                "tenant_id": "text", "project_id": "text", "keyid": "text",
                "public_key": "bytea", "policy_revision": "text",
                "max_age_seconds": "integer", "enabled": "boolean",
            },
            "acp_trusted_proof_receipts": {
                "tenant_id": "text", "project_id": "text",
                "cluster_id": "text", "operation_id": "text",
                "resource_uid": "text", "nonce": "text",
                "signer_keyid": "text", "policy_revision": "text",
                "proof_id": "text", "proof_statement_hash": "text",
                "envelope_sha256": "character(64)",
                "context_sha256": "character(64)",
                "issued_at": "timestamp with time zone",
                "expires_at": "timestamp with time zone",
                "accepted_at": "timestamp with time zone",
            },
        }
        for table, columns in expected.items():
            rows = conn.execute(
                """SELECT attname, format_type(atttypid,atttypmod) AS type,
                          attnotnull AS required
                   FROM pg_attribute
                   WHERE attrelid=to_regclass(%s)
                     AND attnum > 0 AND NOT attisdropped""",
                ("public." + table,),
            ).fetchall()
            if ({r["attname"]: r["type"] for r in rows} != columns
                    or not all(r["required"] for r in rows)):
                raise ProtocolViolation("trusted proof table shape mismatch: " + table)
            constraints = conn.execute(
                """SELECT contype, pg_get_constraintdef(oid) AS definition
                   FROM pg_constraint
                   WHERE conrelid=to_regclass(%s)""",
                ("public." + table,),
            ).fetchall()
            defs = {r["contype"]: [
                x["definition"] for x in constraints if x["contype"] == r["contype"]
            ] for r in constraints}
            if table == "acp_trusted_proof_signers":
                if defs.get("p") != [
                    "PRIMARY KEY (tenant_id, project_id, keyid)"
                ] or len(defs.get("c", [])) != 2:
                    raise ProtocolViolation("trusted signer key or checks drift")
            else:
                if (defs.get("p") != [
                    "PRIMARY KEY (tenant_id, project_id, cluster_id, operation_id)"
                ] or defs.get("u") != ["UNIQUE (tenant_id, project_id, nonce)"]
                        or len(defs.get("f", [])) != 1
                        or "ON DELETE RESTRICT" not in defs["f"][0]):
                    raise ProtocolViolation("trusted receipt uniqueness or FK drift")
        validate_security_boundary(conn)

    def install(self) -> None:
        """Explicit operator action only; never invoked during verification."""
        with self._connection() as conn:
            require_schema_operator(conn)
            conn.execute("SET LOCAL lock_timeout = '8s'")
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (ADVISORY_LOCK,))
            names = (
                "acp_trusted_proof_schema",
                "acp_trusted_proof_signers",
                "acp_trusted_proof_receipts",
            )
            present = [
                conn.execute(
                    "SELECT to_regclass(%s) IS NOT NULL AS present",
                    ("public." + name,),
                ).fetchone()["present"]
                for name in names
            ]
            if not any(present):
                conn.execute(LEDGER_SQL)
                conn.execute(SCHEMA_SQL)
                conn.execute(
                    "INSERT INTO public.acp_trusted_proof_schema(version,checksum) "
                    "VALUES (1,%s)", (SCHEMA_CHECKSUM,),
                )
            elif not all(present):
                raise ProtocolViolation("partial trusted proof schema; review required")
            # Existing SQL v1 and its pinned checksum stay untouched. This
            # separately versioned function is an explicit operator upgrade.
            install_security_boundary(conn)
            self._verify_schema(conn)

    def grant_runtime(self, role: str) -> None:
        """Operator-only GRANT with postcondition denying signer/receipt writes."""
        with self._connection() as conn:
            require_schema_operator(conn)
            self._verify_schema(conn)
            grant_runtime_access(conn, role)

    def validate(self) -> None:
        """Read-only validation; no implicit schema creation."""
        with self._connection() as conn:
            self._verify_schema(conn)

    def verify_and_record(
        self, *,
        proof_mapping: Mapping[str, Any],
        envelope: Mapping[str, Any],
        expected_context: Mapping[str, str],
    ) -> DurableProofReceipt:
        """Atomic identity validation and receipt claim against *DB* clock.

        expected_context must originate from independent ACP authority.
        No HTTP request or signed envelope may dictate these expected values.
        A repeated exact envelope for the same operation returns DUPLICATE,
        but a different nonce, signed payload, key, proof, policy or target
        conflicts even when the original external operation later changes.
        """
        if (not isinstance(expected_context, Mapping)
                or set(expected_context) != CONTEXT_KEYS
                or any(not isinstance(v, str) or not v.strip()
                       for v in expected_context.values())):
            raise ProtocolViolation("trusted expected context is incomplete")
        if not isinstance(envelope, Mapping):
            raise ProtocolViolation("DSSE envelope mapping required")
        try:
            envelope_bytes = json.dumps(
                envelope, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False, allow_nan=False,
            ).encode("utf-8")
            context_bytes = json.dumps(
                dict(expected_context), sort_keys=True, separators=(",", ":"),
                ensure_ascii=False, allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ProtocolViolation("unserializable proof input") from exc
        if len(envelope_bytes) > 100000 or len(context_bytes) > 8192:
            raise ProtocolViolation("oversized trusted proof claim")
        envelope_hash = hashlib.sha256(envelope_bytes).hexdigest()
        context_hash = hashlib.sha256(context_bytes).hexdigest()
        tenant = expected_context["tenant_id"]
        project = expected_context["project_id"]
        cluster = expected_context["cluster_id"]
        operation = expected_context["operation_id"]
        nonce = expected_context["nonce"]
        with self._connection() as conn:
            self._verify_schema(conn)
            # SECURITY DEFINER row lock serializes revocations and acceptance.
            # A revoked or rotated signer is denied even on exact redelivery.
            # No network request is made while holding the lock.
            keyid = None
            if (isinstance(envelope.get("signatures"), list)
                    and len(envelope["signatures"]) == 1
                    and isinstance(envelope["signatures"][0], Mapping)):
                keyid = envelope["signatures"][0].get("keyid")
            if not isinstance(keyid, str) or not keyid:
                raise ProtocolViolation("missing signed key identifier")
            # SECURITY DEFINER locks the exact signer row as the table owner.
            # Runtime requires EXECUTE, not signer-table UPDATE privilege.
            signer_row = conn.execute(
                """SELECT public_key, policy_revision, max_age_seconds, enabled
                   FROM public.acp_trusted_proof_read_signer(%s,%s,%s)""",
                (tenant, project, keyid),
            ).fetchone()
            if signer_row is None or signer_row["enabled"] is not True:
                raise ProtocolViolation("trusted signer missing or revoked")
            if signer_row["policy_revision"] != expected_context["policy_revision"]:
                raise ProtocolViolation("stale trusted signer policy revision")
            verified = verify_signed_proof_reference(
                proof_mapping=proof_mapping,
                envelope=envelope,
                trusted_signer=TrustedSigner(
                    keyid=keyid,
                    ed25519_public_key=bytes(signer_row["public_key"]),
                    max_age_seconds=signer_row["max_age_seconds"],
                ),
                expected_context=expected_context,
                now=conn.execute(
                    "SELECT clock_timestamp() AS now"
                ).fetchone()["now"],
            )
            # PostgreSQL unique constraints are the durable cross-process
            # arbiter. No automatic expiry or deletion of nonce claims.
            try:
                inserted = conn.execute(
                    """INSERT INTO public.acp_trusted_proof_receipts (
                         tenant_id, project_id, cluster_id, operation_id,
                         resource_uid, nonce, signer_keyid, policy_revision,
                         proof_id, proof_statement_hash, envelope_sha256,
                         context_sha256, issued_at, expires_at
                       ) VALUES (
                         %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                       ) ON CONFLICT DO NOTHING
                       RETURNING operation_id""",
                    (tenant, project, cluster, operation,
                     expected_context["resource_uid"], nonce, keyid,
                     expected_context["policy_revision"], verified.proof_id,
                     verified.proof_statement_hash, envelope_hash, context_hash,
                     verified.issued_at, verified.expires_at),
                ).fetchone()
            except UniqueViolation as exc:
                raise ProtocolViolation("replay proof uniqueness conflict") from exc
            row = conn.execute(
                """SELECT * FROM public.acp_trusted_proof_receipts
                   WHERE tenant_id=%s AND project_id=%s
                     AND cluster_id=%s AND operation_id=%s""",
                (tenant, project, cluster, operation),
            ).fetchone()
            if row is None:
                raise ProtocolViolation("nonce already bound to another operation")
            expected = {
                "resource_uid": expected_context["resource_uid"],
                "nonce": nonce,
                "signer_keyid": keyid,
                "policy_revision": expected_context["policy_revision"],
                "proof_id": verified.proof_id,
                "proof_statement_hash": verified.proof_statement_hash,
                "envelope_sha256": envelope_hash,
                "context_sha256": context_hash,
                "issued_at": verified.issued_at,
                "expires_at": verified.expires_at,
            }
            if any(row[k] != v for k, v in expected.items()):
                raise ProtocolViolation("conflicting proof for claimed operation")
            return DurableProofReceipt(
                status="INSERTED" if inserted else "DUPLICATE",
                proof_id=verified.proof_id,
                proof_statement_hash=verified.proof_statement_hash,
                signer_keyid=keyid,
                operation_id=operation,
                nonce=nonce,
            )
