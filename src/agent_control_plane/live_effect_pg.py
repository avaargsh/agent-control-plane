"""Operator-provisioned PG16 current-authority example for M6 Kubernetes effects.

Owns no Task/workflow state. Runtime gets only EXECUTE on scoped SECURITY
DEFINER lookup, with SQL row locks held until the caller's PATCH completes.
This fences *participating* PostgreSQL changes, not external identity changes.
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
from typing import Any, Iterator, Mapping

from psycopg import connect, sql
from psycopg.rows import dict_row

from .live_effect_boundary import CurrentEffectAuthority
from .state_transition_protocol import Principal, ProtocolViolation

AUTHORITY_SQL = """CREATE TABLE public.acp_live_effect_authority (
  tenant_id text NOT NULL,
  project_id text NOT NULL,
  cluster_id text NOT NULL,
  work_id text NOT NULL,
  operation_id text NOT NULL,
  nonce text NOT NULL,
  version integer NOT NULL CHECK (version > 0),
  observed_at timestamptz NOT NULL,
  valid_until timestamptz NOT NULL,
  snapshot jsonb NOT NULL,
  PRIMARY KEY (tenant_id,project_id,cluster_id,work_id,operation_id),
  UNIQUE (tenant_id,project_id,nonce),
  CHECK (valid_until > observed_at),
  CHECK (jsonb_typeof(snapshot)='object')
);
CREATE TABLE public.acp_live_effect_readers (
  login_role name PRIMARY KEY,
  tenant_id text NOT NULL,
  project_id text NOT NULL,
  cluster_id text NOT NULL
);
CREATE TABLE public.acp_live_effect_schema (
  version integer PRIMARY KEY CHECK (version=1),
  checksum char(64) NOT NULL
);
"""
FUNCTION_SIGNATURE = "public.acp_live_effect_read(text,text,text,text,text)"
FUNCTION_BODY = """SELECT a.snapshot, a.version, a.observed_at, a.valid_until
FROM public.acp_live_effect_authority AS a
JOIN public.acp_live_effect_readers AS reader
  ON reader.login_role = session_user
 AND reader.tenant_id = a.tenant_id
 AND reader.project_id = a.project_id
 AND reader.cluster_id = a.cluster_id
WHERE a.tenant_id = p_tenant
  AND a.project_id = p_project
  AND a.cluster_id = p_cluster
  AND a.work_id = p_work
  AND a.operation_id = p_operation
FOR SHARE OF a, reader"""
FUNCTION_SQL = f"""CREATE FUNCTION public.acp_live_effect_read(
  p_tenant text, p_project text, p_cluster text,
  p_work text, p_operation text
) RETURNS TABLE (
  snapshot jsonb, version integer, observed_at timestamptz,
  valid_until timestamptz
)
LANGUAGE sql VOLATILE SECURITY DEFINER
SET search_path = pg_catalog
AS $m6_authority$
{FUNCTION_BODY}
$m6_authority$;
REVOKE ALL ON FUNCTION {FUNCTION_SIGNATURE} FROM PUBLIC;
"""
CHECKSUM = hashlib.sha256((AUTHORITY_SQL + FUNCTION_SQL).encode()).hexdigest()
ADVISORY_LOCK = 657805283175311
PAYLOAD_KEYS = frozenset({
    "nonce", "resource_uid", "principal_type", "principal_subject",
    "executor_type", "executor_subject", "resource_generation",
    "lease_id", "lease_epoch", "transition_hash", "action_hash",
    "authorization_hash", "approval_hash", "policy_version",
    "policy_input_hash", "policy_decision_hash", "policy_effect",
    "approval_state", "principal_state", "operation_state",
})


def _operator_required(conn: Any, *, table_exists: bool) -> None:
    if table_exists:
        record = conn.execute(
            """SELECT pg_has_role(r.oid,c.relowner,'MEMBER') AS owner
               FROM pg_class AS c
               JOIN pg_roles AS r ON r.rolname=current_user
               WHERE c.oid=to_regclass('public.acp_live_effect_authority')"""
        ).fetchone()
        if record is None or record["owner"] is not True:
            raise ProtocolViolation("M6 authority operator ownership required")
    else:
        row = conn.execute(
            "SELECT has_schema_privilege(current_user,'public','CREATE') AS create"
        ).fetchone()
        if row["create"] is not True:
            raise ProtocolViolation("M6 authority installer needs CREATE permission")


def _validate_schema(conn: Any) -> None:
    records = conn.execute(
        "SELECT version,checksum FROM public.acp_live_effect_schema"
    ).fetchall()
    if records != [{"version": 1, "checksum": CHECKSUM}]:
        raise ProtocolViolation("M6 PG authority schema checksum drift")
    tables = (
        ("acp_live_effect_authority", {
            "tenant_id", "project_id", "cluster_id", "work_id",
            "operation_id", "nonce", "version", "observed_at",
            "valid_until", "snapshot",
        }),
        ("acp_live_effect_readers", {
            "login_role", "tenant_id", "project_id", "cluster_id",
        }),
    )
    for table, expected_cols in tables:
        cols = conn.execute(
            """SELECT attname, attnotnull
               FROM pg_attribute
               WHERE attrelid=to_regclass(%s)
                 AND attnum > 0 AND NOT attisdropped""",
            ("public." + table,),
        ).fetchall()
        if ({r["attname"] for r in cols} != expected_cols
                or not all(r["attnotnull"] for r in cols)):
            raise ProtocolViolation("M6 PG authority table shape drift")
        constraints = conn.execute(
            """SELECT contype,pg_get_constraintdef(oid) AS definition
               FROM pg_constraint WHERE conrelid=to_regclass(%s)""",
            ("public." + table,),
        ).fetchall()
        defs = {r["definition"] for r in constraints}
        if table == "acp_live_effect_authority":
            if not {
                "PRIMARY KEY (tenant_id, project_id, cluster_id, work_id, operation_id)",
                "UNIQUE (tenant_id, project_id, nonce)",
            }.issubset(defs) or len([r for r in constraints if r["contype"]=="c"]) != 3:
                raise ProtocolViolation("M6 PG authority uniqueness or checks drift")
        elif defs != {"PRIMARY KEY (login_role)"}:
            raise ProtocolViolation("M6 PG reader scope uniqueness drift")
    f = conn.execute(
        """SELECT p.prosrc,p.prosecdef,p.provolatile,p.proconfig,
                  p.proowner = a.relowner AS correct_owner,
                  EXISTS (
                    SELECT 1 FROM aclexplode(coalesce(
                      p.proacl,acldefault('f',p.proowner))) acl
                    WHERE acl.grantee=0 AND acl.privilege_type='EXECUTE'
                  ) AS public_execute
           FROM pg_proc p
           JOIN pg_class a ON a.oid=to_regclass('public.acp_live_effect_authority')
           WHERE p.oid=to_regprocedure(%s)""",
        (FUNCTION_SIGNATURE,),
    ).fetchone()
    if (f is None or f["prosrc"].strip()!=FUNCTION_BODY.strip()
            or f["prosecdef"] is not True or f["provolatile"]!="v"
            or f["proconfig"]!=["search_path=pg_catalog"]
            or f["correct_owner"] is not True
            or f["public_execute"] is not False):
        raise ProtocolViolation("M6 PG authority function security drift")


def _decode(row: Mapping[str, Any], keys: Mapping[str, str]) -> CurrentEffectAuthority:
    data = row["snapshot"]
    if not isinstance(data, dict) or set(data)!=PAYLOAD_KEYS:
        raise ProtocolViolation("M6 PG authority payload shape invalid")
    names = PAYLOAD_KEYS - {"resource_generation", "lease_epoch"}
    if any(not isinstance(data[k],str) or not data[k].strip() for k in names):
        raise ProtocolViolation("M6 PG authority payload fields invalid")
    if (type(data["resource_generation"]) is not int
            or type(data["lease_epoch"]) is not int):
        raise ProtocolViolation("M6 PG authority numeric bindings invalid")
    return CurrentEffectAuthority(
        **keys,
        nonce=data["nonce"], resource_uid=data["resource_uid"],
        principal=Principal(type=data["principal_type"],subject=data["principal_subject"]),
        executor=Principal(type=data["executor_type"],subject=data["executor_subject"]),
        resource_generation=data["resource_generation"],
        lease_id=data["lease_id"],lease_epoch=data["lease_epoch"],
        transition_hash=data["transition_hash"],action_hash=data["action_hash"],
        authorization_hash=data["authorization_hash"],
        approval_hash=data["approval_hash"],policy_version=data["policy_version"],
        policy_input_hash=data["policy_input_hash"],
        policy_decision_hash=data["policy_decision_hash"],
        policy_effect=data["policy_effect"], approval_state=data["approval_state"],
        principal_state=data["principal_state"],
        operation_state=data["operation_state"],
        observed_at=row["observed_at"],valid_until=row["valid_until"],
        version=row["version"],
    )


class PostgresCurrentEffectAuthorityReader:
    """A restricted read role; operator must separately grant scope and keys."""

    def __init__(self, dsn: str) -> None:
        if not isinstance(dsn,str) or not dsn.strip():
            raise ProtocolViolation("explicit PG16 authority DSN required")
        self.dsn=dsn

    def _connection(self):
        return connect(self.dsn,row_factory=dict_row,connect_timeout=5)

    def install(self) -> None:
        """Only the DB schema owner may create or upgrade the authority source."""
        with self._connection() as conn:
            conn.execute("SET LOCAL lock_timeout = '5s'")
            conn.execute("SELECT pg_advisory_xact_lock(%s)",(ADVISORY_LOCK,))
            names=("acp_live_effect_authority","acp_live_effect_readers",
                   "acp_live_effect_schema")
            present=[conn.execute(
                "SELECT to_regclass(%s) IS NOT NULL AS found",
                ("public."+table,),
            ).fetchone()["found"] for table in names]
            _operator_required(conn,table_exists=present[0])
            function_present=conn.execute(
                "SELECT to_regprocedure(%s) IS NOT NULL AS found",
                (FUNCTION_SIGNATURE,),
            ).fetchone()["found"]
            if not any(present) and not function_present:
                conn.execute(AUTHORITY_SQL)
                conn.execute(FUNCTION_SQL)
                conn.execute(
                    "INSERT INTO public.acp_live_effect_schema(version,checksum) "
                    "VALUES (1,%s)",(CHECKSUM,),
                )
            elif not all(present) or not function_present:
                raise ProtocolViolation("partial M6 PG authority schema")
            _validate_schema(conn)

    def grant_reader(
        self, *, role: str, tenant_id: str, project_id: str, cluster_id: str,
    ) -> None:
        """Operator-only explicit scope binding, no runtime role creation."""
        values=(tenant_id,project_id,cluster_id)
        if (not isinstance(role,str) or not role or len(role)>63
                or any(not isinstance(v,str) or not v.strip() for v in values)):
            raise ProtocolViolation("complete M6 reader role/scope required")
        with self._connection() as conn:
            _validate_schema(conn)
            _operator_required(conn,table_exists=True)
            identity=conn.execute(
                """SELECT rolsuper,rolcreaterole,rolcreatedb,rolreplication,
                          rolbypassrls
                   FROM pg_roles WHERE rolname=%s""",(role,),
            ).fetchone()
            if identity is None or any(identity.values()):
                raise ProtocolViolation("elevated or unknown M6 reader role")
            inherited=conn.execute(
                """SELECT pg_has_role(r.oid,a.relowner,'MEMBER') AS owns
                   FROM pg_roles AS r
                   JOIN pg_class AS a
                     ON a.oid=to_regclass('public.acp_live_effect_authority')
                   WHERE r.rolname=%s""",(role,),
            ).fetchone()
            if inherited is None or inherited["owns"]:
                raise ProtocolViolation("M6 reader cannot inherit authority owner")
            conn.execute(
                """INSERT INTO public.acp_live_effect_readers
                     (login_role,tenant_id,project_id,cluster_id)
                   VALUES (%s,%s,%s,%s)
                   ON CONFLICT (login_role) DO NOTHING""",(role,*values),
            )
            bound=conn.execute(
                "SELECT tenant_id,project_id,cluster_id "
                "FROM public.acp_live_effect_readers WHERE login_role=%s",
                (role,),
            ).fetchone()
            if bound != dict(zip(("tenant_id","project_id","cluster_id"),values,strict=True)):
                raise ProtocolViolation("M6 reader cannot silently change scope")
            conn.execute(
                sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role))
            )
            conn.execute(
                sql.SQL("GRANT SELECT ON public.acp_live_effect_schema TO {}").format(
                    sql.Identifier(role))
            )
            conn.execute(
                sql.SQL("GRANT EXECUTE ON FUNCTION "+FUNCTION_SIGNATURE+" TO {}").format(
                    sql.Identifier(role))
            )
            privileges=conn.execute(
                """SELECT
                     has_table_privilege(%s,
                       'public.acp_live_effect_authority','SELECT') AS direct_read,
                     has_table_privilege(%s,
                       'public.acp_live_effect_authority','UPDATE') AS direct_update,
                     has_table_privilege(%s,
                       'public.acp_live_effect_authority','INSERT') AS direct_insert,
                     has_table_privilege(%s,
                       'public.acp_live_effect_authority','DELETE') AS direct_delete,
                     has_table_privilege(%s,
                       'public.acp_live_effect_readers','SELECT') AS scopes_read,
                     has_table_privilege(%s,
                       'public.acp_live_effect_readers','UPDATE') AS scopes_update,
                     has_schema_privilege(%s,'public','CREATE') AS schema_create,
                     has_function_privilege(%s,%s,'EXECUTE') AS can_execute""",
                (role,)*7+(role,FUNCTION_SIGNATURE),
            ).fetchone()
            if any(privileges[k] for k in privileges if k!="can_execute") or (
                not privileges["can_execute"]
            ):
                raise ProtocolViolation("M6 reader has excessive DB privileges")

    def validate(self) -> None:
        """Read-only metadata validation; never creates schema at request time."""
        with self._connection() as conn:
            _validate_schema(conn)

    @contextmanager
    def hold_current(
        self, *, tenant_id: str, project_id: str, cluster_id: str,
        work_id: str, operation_id: str,
    ) -> Iterator[CurrentEffectAuthority | None]:
        """Holds locked authority+scope rows through caller's provider PATCH.

        Revokers using the same PostgreSQL tables block until this transaction
        ends; remote identity systems NOT using this source remain unfenced.
        """
        params=(tenant_id,project_id,cluster_id,work_id,operation_id)
        if any(not isinstance(v,str) or not v.strip() for v in params):
            raise ProtocolViolation("M6 reader requires complete scoped identity")
        with self._connection() as conn:
            conn.execute("SET LOCAL lock_timeout = '2s'")
            conn.execute("SET LOCAL statement_timeout = '15s'")
            conn.execute("SET LOCAL idle_in_transaction_session_timeout = '15s'")
            _validate_schema(conn)
            row=conn.execute(
                "SELECT snapshot,version,observed_at,valid_until "
                "FROM public.acp_live_effect_read(%s,%s,%s,%s,%s)",params,
            ).fetchone()
            keys=dict(zip(
                ("tenant_id","project_id","cluster_id","work_id","operation_id"),
                params,
                strict=True,
            ))
            yield _decode(row,keys) if row is not None else None

    def read_current(
        self, *, tenant_id: str, project_id: str, cluster_id: str,
        work_id: str, operation_id: str,
    ) -> CurrentEffectAuthority | None:
        """Compatible with PR #199 read-only gate; not revocation-fenced."""
        with self.hold_current(
            tenant_id=tenant_id,project_id=project_id,cluster_id=cluster_id,
            work_id=work_id,operation_id=operation_id,
        ) as observation:
            return observation
