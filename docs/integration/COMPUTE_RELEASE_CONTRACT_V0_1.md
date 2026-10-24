# Compute Release Request v0.1

Status: **additive release-operation contract; adapter wiring and live acceptance are separate gates**.

This contract fills the release half of the Compute lifecycle without mutating the frozen `compute.request/v0.1` creation request. A Run has one immutable create `requestId`; release gets a distinct immutable `requestId` that is persisted before the first DELETE and reused on retry/restart.

## Authority and fields

The schema is `contracts/compute-execution/release-request-v0.1.schema.json`.

- `requestId`: new cryptographically random 128-bit operation identity, exactly 32 lowercase hex characters. This same value is sent as the public Management API `Idempotency-Key` for DELETE.
- `sourceRequestId`: original create ComputeRequest key. It remains immutable and must differ from the release `requestId`.
- `expectedGeneration`: server-authoritative Workload Generation from the original owner-scoped create receipt reconciled with authorized Desired state. The release must use `If-Match: "<expectedGeneration>"`; it must not ask the client to invent a generation.
- `projectId`, `computePoolId`, `workloadId`, and canonical Agent/Session/Run correlation keep the release bound to the same logical resource.
- Cluster/provider/provider-binding identity is not accepted from the caller. Runtime reuses the trusted Pool -> Cluster/Provider binding persisted atomically with the create request; release cannot re-resolve to a new route after an ambiguous result.

The request's `intentDigest` is a local immutable fingerprint of the complete release intent. It is not the Management server's `requestDigest`; only the server-issued OperationReceipt owns the latter.

## Release state and retry semantics

1. Read the current allocation and validate its `requestId`, Project/Pool/Workload identity, generation, and provider binding against the persisted create intent.
2. Atomically persist a separate release request ID and the frozen release intent to RuntimeStore before issuing a side effect. Competing/restarted callers return the first persisted ID; changed `sourceRequestId`, route, or generation fails closed.
3. Through the authenticated public Management API, read the owner-scoped receipt for the release `requestId`. If absent, re-read authorized Desired state and verify identity + exact Generation before issuing DELETE with `If-Match` and the release ID as `Idempotency-Key`.
4. After success or ambiguous transport outcome, recover by the same release key. Validate the server receipt's subject/project/cluster/kind/resource/action/expected Generation/resulting Generation/request fingerprint and reconcile it with authorized Desired state.
5. HTTP 202, a delete receipt, a deleting timestamp, or a provider delete ACK means at most `RELEASING`/`UNKNOWN`. Only independently fresh current-generation observation that every Workload-owned Job/Pod/ResourceClaim is absent, plus committed finalizer/tombstone cleanup evidence, can produce `RELEASED`.
6. If a same-name/label foreign object, UID/owner-chain mismatch, stale generation, missing receipt, expired/revoked authorization, or uncertain provider state prevents proof, preserve evidence and fail closed. Never delete an object solely because its name or labels match.

`ComputeAllocation.requestId` continues to identify the original allocation request in v0.1. The separate release request is persisted and audited by Runtime; it is not substituted into the creation identity. If the wire API later needs to expose the active lifecycle-operation ID directly, that is a versioned compatibility change and must not be smuggled into the frozen v0.1 schema.

## Required conformance tests

- positive schema validation plus invalid request key, invalid source key, equal create/release IDs, Generation zero, unknown fields, and malformed correlation;
- same release ID + same canonical intent is idempotent; same release ID + changed generation/route/source is a conflict;
- two concurrent callers and Runtime/SQLite reconstruction recover the same release ID;
- HTTP commit + dropped ACK + caller SIGKILL recovers the same owner-scoped receipt without issuing a second logical delete;
- stale If-Match never deletes a newer Workload;
- duplicate DELETE does not duplicate allocation and same-name foreign provider objects are never removed;
- release remains RELEASING/UNKNOWN until Provider absence and finalization/tombstone are independently proved.

This schema does not claim an implementation or production acceptance by itself. The public Management API + PostgreSQL + kind/Kueue integration is the required final evidence.
