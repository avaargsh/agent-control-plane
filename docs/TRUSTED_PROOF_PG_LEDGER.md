# ACP #191 — Separate PostgreSQL 16 Trusted Proof Verification Ledger

**Status: experimental opt-in, reference only. Not production-authoritative.**

This extension persists authenticated historical proof references, NEVER operation
authorizations, Task terminal states, observation freshness, or runtime write tokens.
It does not mutate IndependentExecutionProof/v1 or ACP TransitionPlan/lease/CAS.

## Entry points

- TrustedProofPostgres(dsn).install(): explicit operator-only schema setup.
- TrustedProofPostgres(dsn).validate(): read-only schema/checksum verification.
- TrustedProofPostgres(dsn).verify_and_record(...): verify original independent
  execution proof, pinned Ed25519 DSSE signer from PostgreSQL trust table,
  current enabled/policy revision, and claim exact nonce + operation atomically.

The operator must approve an isolated database, review the exact SQL and plan
backup/restore before running install. The app never creates schema on a
request. No automatic schema downgrade, nonce expiration or destructive prune.

## Database and trust boundaries

Three separate tables (not PR Watch or Task tables):

- acp_trusted_proof_schema: SQL version 1 and pinned SQL checksum.
- acp_trusted_proof_signers: tenant/project/keyid, 32-byte public key, policy
  revision, maximum signature age and enabled flag (defaults FALSE).
- acp_trusted_proof_receipts: historical verification receipt. Unique keys:
  (tenant,project,cluster,operation) and (tenant,project,nonce).

A redelivery with exactly the same valid signed envelope, proof, operation
and authoritative context returns DUPLICATE. Cross-operation nonce reuse,
alternative envelope, different signature, policy, resource, or changed nonce
fail closed. Nonce claims persist; future retention must preserve tombstones.

The verifier selects a trusted key ONLY from the pre-provisioned DB signer
row, checks enabled and policy_revision, acquires FOR SHARE lock to serialize
participating revocation, and validates the signature with the PostgreSQL
clock_timestamp() inside the same transaction that inserts the receipt.
After revocation or policy revision changes, even identical redelivery fails.

## Operator validation in disposable PostgreSQL 16

    python -m pip install -e '.[dev,proof-ledger]'
    ACP_TRUSTED_PROOF_TEST_DSN='postgresql://.../disposable-admin-db' \
      python -m pytest -q tests/test_trusted_proof_pg.py
    python -m ruff check src/agent_control_plane/trusted_proof_pg.py \
      tests/test_trusted_proof_pg.py

CI creates disposable databases; tests concurrency, replay, changed nonce,
cross-cluster reuse, revoked or missing keys, stale policy, wrong signer,
expired signature, forged proof and schema checksum drift.

### Provisioning is trusted operator work, not an application endpoint

An approved operator can provision the signer with the following SQL pattern
using a DB role separate from the runtime identity (placeholders not executable):

    INSERT INTO public.acp_trusted_proof_signers
      (tenant_id,project_id,keyid,public_key,policy_revision,max_age_seconds,enabled)
    VALUES ('<tenant>','<project>','<key-id>',decode('<public-key-hex>','hex'),
            '<policy-revision>',600,TRUE);

Private keys must never be stored in this schema or committed. Revocation is
an operator-controlled update to enabled=FALSE with external audit evidence.
Do not use a shared production superuser. Separate runtime SELECT on signers
and INSERT/SELECT on receipts from operator schema/key administration.

## Hard limits and open acceptance

- This authenticates a historical reference, not a fresh observation or
  verified business outcome. A DSSE signer is not proof that a task succeeded.
- Expected tenant, project, principal, policy, operation, UID and nonce must
  come from ACP's authoritative state, NEVER from a browser or DSSE claim.
- KMS/Sigstore issuer identity, signer role authorization, rotation, key
  revocation evidence, RBAC/RLS and independent audit remain unimplemented.
- Database durability requires actually verified WAL, backup, recovery and
  role isolation. Superusers can mutate records. No immutability claim.
- Even a verified receipt MUST NOT bypass live authorization at the action
  boundary; the verifier does not grant any write or produce a Task success.
- No live GitHub delivery, ingress, GPUPanel integration or production GO.

Related: docs/TRUSTED_PROOF_DSSE_OFFLINE.md, ACP #191, #185, #192.
