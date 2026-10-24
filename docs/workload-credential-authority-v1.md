# Workload Credential Authority v1

## Purpose and trust boundary

This package is the first service-side implementation of the versioned Runtime Workload Credential Authority contract. It does not make itself a public listener and it is not production-enabled by importing the module.

- **PinnedEd25519AssertionVerifier** accepts only EdDSA compact assertions signed by an operator-pinned public key and matching the configured issuer, authority audience, expiry and maximum lifetime. Request-provided JWKs and remote key discovery are not supported.
- **RunAuthorityReader** must fetch the canonical Run and Session independently of the assertion. It must return tenant, Agent, Release, Session, Run, sandbox ID/revision, workspace ref, Run state and Session state from the authoritative runtime store/API. Do not implement it by reading claims copied from the request.
- **PostgresCredentialAuthorityStore** stores only a digest of an opaque random token. The raw bearer token is returned once and must not be written to logs, database rows, traces or outbox messages.
- Schema creation is an explicit deployment/migration step. No HTTP request runs DDL or performs implicit migrations.
- **WorkloadCredentialWSGI** is an application layer only. It rejects requests unless the trusted WSGI environment reports `wsgi.url_scheme=https`; this is defense-in-depth, not TLS termination or certificate validation. Deploy it behind a TLS listener with service-to-service mTLS or equivalent authenticated ingress, configure the WSGI scheme only from that trusted termination point, apply a network policy, retain strict request limits and use operator-managed service secrets. Do not trust arbitrary `X-Forwarded-Proto` values. Never expose it directly to untrusted networks.

## Versioned routes and service roles

All bodies are bounded JSON objects. The service rejects unknown routes, checks each route's service role before parsing the body, returns Cache-Control: no-store, and emits sanitized failures that never include SQL messages or bearer material.

| Route | Required role | Behavior |
| --- | --- | --- |
| POST /v1/workload-credentials:issue | credential.issuer | Verify signed assertion, canonical Run/Session, binding policy, scopes and any required exact approval; atomically consume request ID and persist token digest |
| POST /v1/workload-credentials:introspect | credential.introspector | Validate token digest, expiry, audience, requested scopes, credential revocation, current binding version, current approval version/state and canonical Run/Session/sandbox revision |
| POST /v1/workload-credentials:revoke | credential.revoker | Revoke one credential with a stable event ID |
| POST /v1/workload-credentials:revoke-run | credential.revoker | Revoke Run credentials from the specified sandbox revision and older, after matching the current canonical revision |
| POST /v1/workload-credentials:revoke-approval | credential.revoker | Idempotently fan out an already authenticated approval-revocation event to all credentials bound to that approval |
| POST /v1/workload-credential-requests:lookup | credential.issuer | Reconcile an ambiguous issuance by request identity; never returns a raw token |
| GET /v1/authorization/approvals/{id} | approval.reader | Read current approval state/version, not a cached decision |
| POST /v1/authorization/approvals/{id}:revoke | approval.revoker | Atomically mark approval revoked, increment version, invalidate associated credentials and enqueue one stable outbox event |

The service role is supplied by ServiceRoleAuthenticator from a SHA-256 digest of an operator-provisioned bearer token. The WSGI adapter overwrites the internal authenticated-role field in the parsed request with the role authenticated for the route; callers cannot select that value in their JSON. Production should provision five distinct role credentials and rotate them independently.

## Database installation and privileges

Apply migrations/workload_credential_authority_v1.sql through the reviewed release/migration procedure. The dedicated workload-credential-authority-pg GitHub workflow provisions PostgreSQL 16, applies this exact migration and tests it; this does not replace a production migration review.

The schema includes:
- operator-managed acp_workload_credential_bindings with a monotonically increasing policy version;
- exact-scope acp_workload_approval_grants with version, expiry, revocation state and stable event ID;
- acp_workload_credentials with only token_digest, binding version, canonical Run/workspace identity, expiry and revocation metadata;
- durable acp_workload_credential_requests idempotency keys;
- a global acp_workload_credential_revocations event journal preventing event-ID rebinding across credentials, Runs and approvals;
- approval-revocation events plus an at-least-once transactional outbox.

Grant separate database roles to the HTTP service and operator provisioning process. The HTTP role must not own the schema, alter tables, or provision arbitrary bindings/approvals. Binding configuration is an operator-only method. register_approval is an internal hook after the normal trusted approval decision has committed; do not expose it to the credential HTTP service or Agent callers. On production PostgreSQL, use separate DB roles and grants so read and mutate responsibilities are constrained below the HTTP role layer too.

## Credential and approval semantics

- Credential expiry is bounded by requested TTL, binding maximum TTL, assertion expiry and, where required, approval expiry.
- Only recognized read, list and metadata scope suffixes avoid human approval. Unknown scope classes fail closed and require an exact approval grant.
- Sensitive approval is bound to tenant, Agent, Session, Run, binding, audience, scope set, approver and expiry.
- Request IDs are consumed atomically in PostgreSQL. A lost response must be reconciled using the same request identity; it must not mint another token.
- Introspection is fail-closed against inactive Session/Run, a new sandbox revision, revoked/expired approval, changed binding version, revoked credential, wrong audience and over-scoped use.
- Every credential/Run/approval revocation records a stable event ID and the authenticated service role. Reusing an event ID for another resource or changed request digest is a conflict.
- Approval revocation commits state and credential invalidation with the outbox record in one PostgreSQL transaction. The outbox worker must use authenticated transport/signing in its ApprovalRevocationPublisher, acknowledge only after confirmed delivery and retry with the same event ID.
- Online introspection is required at protected resource boundaries for immediate revocation. A self-contained bearer token accepted offline until expiry cannot satisfy immediate revocation by itself. External provider APIs must support token exchange/introspection/revocation or their own fencing layer.

## Production acceptance remains open

This implementation and PostgreSQL workflow are the service-side contract, not evidence that an operator deployment is production ready. Do not claim production security acceptance until all of the following have passed on the exact release commit:

1. PostgreSQL 16 integration tests for signature verification, durable replay rejection after restart/concurrent requests, policy-version fencing, approval race/revocation, event-ID reuse denial and outbox retry.
2. Real HTTPS/mTLS integration with independently provisioned issuer, introspector, credential-revoker, approval-reader and approval-revoker service identities.
3. A real canonical RunAuthorityReader connected to the authoritative Runtime/Control Plane, including current Session status and sandbox binding revision.
4. A trusted assertion issuer backed by operator-managed KMS/SPIFFE or equivalent key lifecycle, plus a trusted approval decision and signed/authenticated revocation outbox.
5. Negative end-to-end tests for cross-tenant/Agent Run references, forged sandbox revision, wrong audience, scope escalation, approval replay, expired/revoked token, token replay, lost acknowledgements and recovery after process restart.
6. Runtime resource services enforce introspection or equivalent fencing for every protected operation; CNI network enforcement and workspace destruction remain separate live-cluster gates.

Until then, use fake providers only for unit tests and do not configure production upstream credentials.
