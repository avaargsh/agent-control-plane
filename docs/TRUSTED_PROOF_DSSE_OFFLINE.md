# ACP #191 — Opt-in offline Ed25519 DSSE proof reference verification

**Status: experimental, source-level verifier only. NO production trust-profile acceptance.**

This adds a separately versioned, in-toto-shaped **signature layer** to the
existing IndependentExecutionProof/v1 object. Frozen v0.1 schemas,
TransitionPlan/CAS/authorization state, lease fencing, and independent
execution proof format stay unchanged. No signing server, key generation
interface, automatic admission policy, token issuance, online Sigstore,
replay ledger or provider mutation is introduced.

## Threat model and what the verifier checks

The caller MUST obtain **all** of the following independently of the
untrusted DSSE envelope:

- Exact existing independently verifiable execution-proof mapping.
- One pinned TrustedSigner(keyid, ed25519_public_key, max_age_seconds)
  from a trusted key-distribution channel (no embedded public keys).
- Exact expected context from the current authoritative operation record:
  tenant_id, project_id, cluster_id, principal_type, principal_subject,
  policy_revision, authorization_hash, operation_id, resource_uid and the
  unique operation nonce.
- Trusted timezone-aware current time.

verify_signed_proof_reference checks:

1. Exactly one DSSE signature with an exact allowlisted key ID, standard
   DSSE PAE over unchanged payload bytes, and Ed25519 validity.
2. in-toto Statement v1 / predicate type, exact subject and hash of the
   pre-existing independent-proof statement, with no duplicate JSON keys.
3. The full original ACP independent proof hash-and-provenance chain via
   verify_execution_proof_mapping.
4. Exact operation ID, immutable resource UID, authorization digest and
   principal against the original proof.
5. Exact trusted caller-supplied tenant/project/cluster/policy/nonce
   context against the signed assertion (these fields are NOT present in
   the frozen v0.1 proof and cannot be derived from that artifact).
6. Issuer-declared timestamp interval, bounded by the trusted profile and
   current verification time. Expired, future and over-long assertions
   fail closed.

Only AuthenticatedProofReference is returned. **It says nothing about**
whether a key owner was authorized to sign, whether the observation was
fresh/real, whether a previously valid authorization has been revoked, whether
a Task should complete, or whether a release may GO. Cryptographic
authentication of bytes is not independent assurance that the underlying
system actually attained a business goal.

## Example isolated consumer code

    from datetime import datetime, timezone
    from agent_control_plane.trusted_proof_envelope import (
        TrustedSigner, verify_signed_proof_reference,
    )

    # All inputs below come from independent authority, not the signed payload.
    accepted = verify_signed_proof_reference(
        proof_mapping=trusted_pre_existing_proof_mapping,
        envelope=untrusted_dsse_envelope,
        trusted_signer=TrustedSigner(
            keyid=externally_pinned_key_id,
            ed25519_public_key=externally_pinned_raw_public_key_32_bytes,
        ),
        expected_context=authoritative_operation_context,
        now=datetime.now(timezone.utc),
    )

This example deliberately omits key, proof and context construction: the
verified bytes and authoritative expected bindings must already exist.
Never construct expected_context from the untrusted envelope being checked.

## Installation and tests

    python -m pip install -e '.[dev,proof-verify]'
    pytest -q tests/test_trusted_proof_envelope.py
    ruff check src/agent_control_plane/trusted_proof_envelope.py tests/test_trusted_proof_envelope.py

Tests use ephemeral random Ed25519 keys, a fully verified existing ACP
proof fixture, wrong-operation/tenant/project/policy/nonce, broken/rotated key,
payload tampering, duplicate keys and expiry. No private key or secret
is committed.

## Remaining blockers before production integration

- A deployer-owned signer trust policy: who is authorized for a given
  tenant/project/operation, key origin, rotation/revocation, KMS/Sigstore
  issuer verification, and zero-downtime rollover.
- Durable server-side nonce/operation lease and TTL/replay registry. The
  same operation/nonce can be reverified deterministically; this helper
  does not prevent signing an alternative statement with the same
  authorized key, or create nonce state. A different operation/nonce is
  rejected only because the trusted caller provides a different binding.
- Independent observation provenance and freshness, live provider account
  control, scope and policy revalidation at actual write boundaries.
- No call from the runtime PR #35 gate or GPUPanel may treat this signed
  historical reference as an authorization token or Task=SUCCEEDED.
- Operator-owned end-to-end evidence for #185 and M6 #192 remains NO-GO.

Parent: https://github.com/avaargsh/agent-control-plane/issues/191
