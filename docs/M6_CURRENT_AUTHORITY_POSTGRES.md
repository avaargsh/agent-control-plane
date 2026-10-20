# M6 PG16 Current Authority — scoped, row-locked Kubernetes effect gate

**Status: opt-in reference integration only. No production issuer, deployed
approval service, new Task ledger or M6 production GO.**

## Why

ACP PR #199 added an optional read of current policy, principal and approval
state just before the Kubernetes Deployment PATCH. A single read is not
atomic with the following network call. This slice supplies an independent
operator-owned PostgreSQL 16 row as the current authority and an OPTIONAL
transaction-held shared lock across the provider's PATCH invocation.

## Authority ownership and boundaries

Operator-managed and versioned tables in an explicitly isolated DB:

- acp_live_effect_authority: exact tenant/project/cluster/work/operation,
  unique tenant/project/nonce, positive row version, timestamps and JSON
  containing the current principal, executor, approval, policy/decision,
  resource UID/generation and lease binding. **Not a Task or Temporal ledger.**
- acp_live_effect_readers: maps an actual PostgreSQL LOGIN role to exactly
  one tenant/project/cluster. The runtime cannot update or even directly read
  this table. A different LOGIN role needs a separately operator-approved
  row; no blanket multi-tenant read rights.
- acp_live_effect_schema: version and pinned DDL+function checksum.

An owner-controlled SECURITY DEFINER function uses a fixed pg_catalog
search_path, exact SQL body validation and REVOKE EXECUTE FROM PUBLIC.
Only a role enrolled in acp_live_effect_readers can ask it to read rows
in its own tenant/project/cluster. SQL FOR SHARE locks the matching reader
scope and current authority row. Verifier login receives EXECUTE on this
function and SELECT on schema ledger, not SELECT/UPDATE/DELETE on data.
Neither schema installation nor reader role provisioning happens on requests.

To use, the operator creates an isolated PostgreSQL database and runtime
role without elevated or inherited owner privileges, then explicitly calls:

    from agent_control_plane.live_effect_pg import (
        PostgresCurrentEffectAuthorityReader,
    )
    operator = PostgresCurrentEffectAuthorityReader(operator_dsn)
    operator.install()
    operator.validate()
    operator.grant_reader(role='scoped_existing_role',
        tenant_id='approved-tenant',
        project_id='approved-project',cluster_id='approved-cluster')

Operator must populate current authority records through separately
reviewed policy+approval governance. **This PR intentionally does not
provide that policy decision service, authorization API or signer issuer.**

Configure the provider's existing ContextBoundExecutionContext.live_effect_gate
with LiveEffectBoundaryGate(scope=operator_pinned_EffectScope,
reader=PostgresCurrentEffectAuthorityReader(runtime_dsn),
clock=trusted_UTC_clock). Existing signed approvals, v3 reservations,
leases, UID/generation, Kubernetes resourceVersion CAS and journal remain.

### Read versus locked PATCH

- read_current(): one authorized, scoped PG snapshot; releases row locks
  when the read returns. Useful for observability only, not a revocation fence.
- hold_current(): transaction context yields the exact locked row; SQL
  row locks remain until the context ends. The provider uses
  LiveEffectBoundaryGate.patch_with_current() to validate this exact row
  and invoke Kubernetes patch_deployment() while locks remain held.
- A revocation that updates this **same PostgreSQL authority/scope row**
  must wait for its shared lock to be released; once it commits, later
  attempts see the revoked state and are rejected before PATCH.
- A pre-PATCH denial is journaled ABORTED/NOT_APPLIED with reservation
  release. Any exception AFTER PATCH was entered, including losing the
  database commit acknowledgment, retains the PREPARED/UNKNOWN
  reconciliation path. Never convert such a failure into NOT_APPLIED.

### Testing

    python -m pip install -e '.[dev,live-effect-pg]'
    ACP_LIVE_EFFECT_TEST_DSN='postgresql://.../disposable-admin-db' \
      python -m pytest -q tests/test_live_effect_pg.py

This creates and drops disposable databases and LOGIN roles. NEVER aim at
production. Tests cover actual least-privilege login, missing/foreign scope
DENY, policy/approval/identity revocation, operator scope rebind refusal,
exact row locks through a simulated slow Kubernetes PATCH, and ambiguity
after PATCH. Additional GitHub Actions: m6-current-authority-postgres.

### Explicit remaining risks

- This is NOT an atomic PostgreSQL/Kubernetes transaction or complete
  end-to-end revocation guarantee: a revoked upstream identity or policy
  not yet materialized in this operator-owned row is invisible here.
- A DB session lock/transaction can be lost during an in-flight network
  call (e.g. connection loss or timeout). The Kubernetes API must enforce
  UID, resourceVersion and independent fencing; strong global revocation
  requires provider-recognized revision/fencing or an outbox design.
- Holding a PG transaction across a network PATCH is deliberately bounded
  with lock/statement/idle timeout. Configure an independently bounded
  Kubernetes call deadline; if the DB lock expires first, fail as
  ambiguous and use read-only reconciliation, not blind retry.
- The operator's role/table admin can change the authority row. No KMS/
  Sigstore issuer governance, cryptographic decision signer or cross-system
  identity attestations are implemented. Database privilege and WAL/HA
  guarantees must be verified in an actual deployment.
- No production current-authority service was deployed. Tests use
  operator-inserted fixtures, fake Deployment API for dynamic races,
  plus existing Kubernetes Kind smoke and core regression gates.
- Real GitHub webhook #185 and M6 #192 external business outcome/Task/GUI
  acceptance remain NO-GO. No change to frozen ACP v0.1 or DSSE evidence.
