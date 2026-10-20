# ACP #191 — PostgreSQL signer-lock privilege boundary (V1 schema unchanged)

**Status:** optional isolated pilot. This is not a production signing authority,
new Task ledger, current authorization, or proof that a business action succeeded.

## Problem

PostgreSQL 16 requires UPDATE privilege to use SELECT ... FOR SHARE.
PR #197 used that lock directly in the caller. Giving the runtime account
UPDATE permission on signer state would allow bypassing operator revocation.
The previous guidance to use only SELECT + INSERT was therefore incomplete.

## Narrow opt-in solution

The original acp_trusted_proof_schema v1 and its pinned SCHEMA_SQL checksum
remain byte-for-byte unchanged. An independently pinned extension installs:

- acp_trusted_proof_security_schema (its own schema version/checksum);
- public.acp_trusted_proof_read_signer(text,text,text), a SQL SECURITY DEFINER
  function that selects only the exact scoped signer row under FOR SHARE;
- explicit REVOKE EXECUTE FROM PUBLIC, applied in the same transaction.

Runtime verification now calls the function and never requires UPDATE on
signers. The insert/lookup of receipt rows does not request FOR UPDATE:
the pre-existing primary key and unique nonce index serialize contenders.
Schema validation checks the function body, SECURITY DEFINER flag, protected
search_path, owner equality with the signer table, and absence of PUBLIC EXECUTE.
Partial installs, modified function code and ledger drift all fail closed.

## Operator provisioning and upgrade

Use a disposable isolated PostgreSQL 16 database with an operator-owned
schema. After explicit approval and backup, install or upgrade the extension:

    from agent_control_plane.trusted_proof_pg import TrustedProofPostgres
    operator = TrustedProofPostgres(operator_dsn)
    operator.install()    # creates or validates V1, installs security extension
    operator.validate()   # no migration/DDL

Previously installed V1 databases receive only the separately versioned
security extension; no automatic request-time upgrade. Use independent
recovery/rollback and never drop receipts or recycle spent nonces.

Create a verifier LOGIN/NOINHERIT role **outside** the app, with no
CREATEDB, CREATEROLE, SUPERUSER, REPLICATION or BYPASSRLS privileges.
The operator grants only the reviewed privileges:

    operator.grant_runtime('precreated_verifier_role')

The method refuses privileged/member-owner roles and checks effective
permissions (including inherited/public grants) before committing GRANTs.
The verifier receives USAGE public, SELECT on versioned schema ledgers,
SELECT + INSERT on proof receipts, and EXECUTE on only the signer lookup
function. No direct signer SELECT/INSERT/UPDATE, receipt UPDATE/DELETE,
or public schema CREATE are permitted.

## Real acceptance

CI: trusted-proof-postgres-ledger, Python 3.11 + 3.12, real PostgreSQL 16.
The security tests create disposable databases and separate LOGIN accounts.
They prove normal signed-proof INSERT/DUPLICATE under restricted identity,
forbidden signer reads/writes, forbidden receipt edits/deletion and DDL,
operator-only install/GRANT, revocation after acceptance, policy rotation,
forged PUBLIC EXECUTE and SECURITY INVOKER changes, and checksum drift.

Run in an operator-owned disposable environment:

    python -m pip install -e '.[dev,proof-ledger]'
    ACP_TRUSTED_PROOF_TEST_DSN='postgresql://.../disposable-admin-db' \
      python -m pytest -q tests/test_trusted_proof_pg_security.py

Do NOT pass a production DSN to this test; it creates and drops databases
and ephemeral roles. The CI admin user is for isolated test setup ONLY.

## What remains unproven

- SQL SECURITY DEFINER is an owner-controlled trust boundary. A compromised
  schema owner/superuser can still rewrite signer data or receipt history.
- The runtime verifier still has INSERT on receipts. Database possession
  alone must NOT be accepted as proof of a signed outcome or authorization.
- This does not solve signer issuance identity, dynamic RBAC or KMS/Sigstore
  trust, RLS, audit retention, production HA/restore, or live GitHub delivery.
- Expected operation context must come from ACP authority. Each side effect
  must independently revalidate live policy, approval and external outcome.
- ACP #185/#192 remain NO-GO until actual external delivery and operator signoff.

References: #191; docs/TRUSTED_PROOF_PG_LEDGER.md.
