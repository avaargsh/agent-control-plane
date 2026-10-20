"""Opt-in security extension for the PostgreSQL trusted proof reference ledger.

Locks the signer row with SECURITY DEFINER so a runtime verifier needs only
EXECUTE, not UPDATE privileges over revocable signer material. All DDL and
GRANTs are operator-only and never performed by verifier requests.
"""
from __future__ import annotations

import hashlib
from typing import Any

from psycopg import sql

from .state_transition_protocol import ProtocolViolation

FUNCTION_SIGNATURE = "public.acp_trusted_proof_read_signer(text,text,text)"
SIGNER_LOOKUP_BODY = """SELECT s.public_key, s.policy_revision, s.max_age_seconds, s.enabled
FROM public.acp_trusted_proof_signers AS s
WHERE s.tenant_id = p_tenant AND s.project_id = p_project AND s.keyid = p_keyid
FOR SHARE OF s"""
BOUNDARY_SQL = f"""CREATE FUNCTION public.acp_trusted_proof_read_signer(
    p_tenant text, p_project text, p_keyid text
) RETURNS TABLE (
    public_key bytea, policy_revision text, max_age_seconds integer,
    enabled boolean
)
LANGUAGE sql SECURITY DEFINER
SET search_path = pg_catalog
AS $trusted_signer$
{SIGNER_LOOKUP_BODY}
$trusted_signer$;
REVOKE ALL ON FUNCTION {FUNCTION_SIGNATURE} FROM PUBLIC;
"""
LEDGER_SQL = """CREATE TABLE public.acp_trusted_proof_security_schema (
    version integer PRIMARY KEY CHECK (version = 1),
    checksum char(64) NOT NULL
);
"""
SECURITY_CHECKSUM = hashlib.sha256(BOUNDARY_SQL.encode("utf-8")).hexdigest()
ADVISORY_LOCK = 657805283175309


def validate_security_boundary(conn: Any) -> None:
    """Fail closed on missing/partial, changed function, or PUBLIC EXECUTE."""
    ledger = conn.execute(
        "SELECT version, checksum FROM public.acp_trusted_proof_security_schema"
    ).fetchall()
    if ledger != [{"version": 1, "checksum": SECURITY_CHECKSUM}]:
        raise ProtocolViolation("trusted proof security ledger checksum drift")
    row = conn.execute(
        """SELECT p.prosrc, p.prosecdef, p.proconfig,
                  p.proowner = signer.relowner AS correct_owner,
                  EXISTS (
                      SELECT 1
                      FROM aclexplode(coalesce(
                          p.proacl, acldefault('f', p.proowner)
                      )) acl
                      WHERE acl.grantee = 0 AND acl.privilege_type = 'EXECUTE'
                  ) AS public_execute
           FROM pg_proc p
           JOIN pg_class signer
             ON signer.oid = to_regclass('public.acp_trusted_proof_signers')
           WHERE p.oid = to_regprocedure(%s)""",
        (FUNCTION_SIGNATURE,),
    ).fetchone()
    if (
        row is None
        or row["prosrc"].strip() != SIGNER_LOOKUP_BODY.strip()
        or row["prosecdef"] is not True
        or row["proconfig"] != ["search_path=pg_catalog"]
        or row["correct_owner"] is not True
        or row["public_execute"] is not False
    ):
        raise ProtocolViolation("trusted proof signer function security drift")


def install_security_boundary(conn: Any) -> None:
    """Operator-only, checksum-pinned extension over the existing v1 schema."""
    conn.execute("SET LOCAL lock_timeout = '8s'")
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (ADVISORY_LOCK,))
    ledger_exists = conn.execute(
        "SELECT to_regclass('public.acp_trusted_proof_security_schema') "
        "IS NOT NULL AS present"
    ).fetchone()["present"]
    function_exists = conn.execute(
        "SELECT to_regprocedure(%s) IS NOT NULL AS present",
        (FUNCTION_SIGNATURE,),
    ).fetchone()["present"]
    if not ledger_exists and not function_exists:
        conn.execute(LEDGER_SQL)
        conn.execute(BOUNDARY_SQL)
        conn.execute(
            "INSERT INTO public.acp_trusted_proof_security_schema(version,checksum) "
            "VALUES (1,%s)",
            (SECURITY_CHECKSUM,),
        )
    elif not ledger_exists or not function_exists:
        raise ProtocolViolation("partial trusted proof security extension")
    validate_security_boundary(conn)


def _runtime_privileges(conn: Any, role: str) -> dict[str, bool]:
    return conn.execute(
        """SELECT
             has_table_privilege(%s,
               'public.acp_trusted_proof_signers', 'SELECT') AS signer_select,
             has_table_privilege(%s,
               'public.acp_trusted_proof_signers', 'UPDATE') AS signer_update,
             has_table_privilege(%s,
               'public.acp_trusted_proof_signers', 'INSERT') AS signer_insert,
             has_table_privilege(%s,
               'public.acp_trusted_proof_receipts', 'UPDATE') AS receipt_update,
             has_table_privilege(%s,
               'public.acp_trusted_proof_receipts', 'DELETE') AS receipt_delete,
             has_table_privilege(%s,
               'public.acp_trusted_proof_receipts', 'TRUNCATE') AS receipt_truncate,
             has_schema_privilege(%s, 'public', 'CREATE') AS schema_create,
             has_table_privilege(%s,
               'public.acp_trusted_proof_receipts', 'SELECT') AS receipt_select,
             has_table_privilege(%s,
               'public.acp_trusted_proof_receipts', 'INSERT') AS receipt_insert,
             has_function_privilege(%s, %s, 'EXECUTE') AS signer_execute""",
        (role,) * 9 + (role, FUNCTION_SIGNATURE),
    ).fetchone()


def grant_runtime_access(conn: Any, role: str) -> None:
    """Operator action: grant only what signed-proof verification needs.

    Refuses a role with inherited/broad privileges. Does not create roles,
    credentials or expose signer administration to application users.
    """
    if not isinstance(role, str) or not role or len(role) > 63:
        raise ProtocolViolation("explicit existing runtime role required")
    validate_security_boundary(conn)
    owner = conn.execute(
        "SELECT rolname, rolsuper, rolcreaterole, rolcreatedb, "
        "rolreplication, rolbypassrls "
        "FROM pg_roles WHERE rolname=%s",
        (role,),
    ).fetchone()
    if owner is None or any(
        owner[flag] for flag in (
            "rolsuper", "rolcreaterole", "rolcreatedb",
            "rolreplication", "rolbypassrls",
        )
    ):
        raise ProtocolViolation("runtime role has elevated privileges")
    if conn.execute(
        """SELECT pg_has_role(r.oid, c.relowner, 'MEMBER') AS is_owner
           FROM pg_roles r
           JOIN pg_class c
             ON c.oid=to_regclass('public.acp_trusted_proof_signers')
           WHERE r.rolname=%s""",
        (role,),
    ).fetchone()["is_owner"]:
        raise ProtocolViolation("runtime role inherits signer table ownership")

    # Schema privilege is needed only if the deployer removed PUBLIC USAGE.
    conn.execute(
        sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role))
    )
    for table in ("acp_trusted_proof_schema",
                  "acp_trusted_proof_security_schema"):
        conn.execute(
            sql.SQL("GRANT SELECT ON public.{} TO {}").format(
                sql.Identifier(table), sql.Identifier(role),
            )
        )
    conn.execute(
        sql.SQL(
            "GRANT SELECT, INSERT ON public.acp_trusted_proof_receipts TO {}"
        ).format(sql.Identifier(role))
    )
    conn.execute(
        sql.SQL("GRANT EXECUTE ON FUNCTION " + FUNCTION_SIGNATURE + " TO {}")
        .format(sql.Identifier(role))
    )
    state = _runtime_privileges(conn, role)
    unsafe = (
        "signer_select", "signer_update", "signer_insert",
        "receipt_update", "receipt_delete", "receipt_truncate",
        "schema_create",
    )
    necessary = ("receipt_select", "receipt_insert", "signer_execute")
    if any(state[key] for key in unsafe) or not all(
        state[key] for key in necessary
    ):
        raise ProtocolViolation("runtime DB privileges exceed least-privilege contract")
