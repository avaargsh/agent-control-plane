# PR Watch — Goal-backed Task Projection (M6 Slice 0)

**Status:** opt-in, read-only local pilot projection. This is NOT a new Task
ledger, stable Task API contract, production AuthN, enterprise tenant RBAC,
or cryptographically verified business outcome. It does not change ACP v0.1
StateTransition/IndependentExecutionProof contracts.

## Product boundary

A durable PR Watch **Goal** is owned by the existing SQLite reference or
PostgreSQL V2 Goal store. Its version/CAS controls, scoped launch journal,
Temporal workflows, Webhook Inbox and provider-observation events remain
authoritative in their existing bounded contexts.

GET /api/tasks and GET /api/tasks/{goal_id} merely assemble two existing,
authenticated, user-scoped records into a view for a future GPUPanel Tasks
page. They never mutate a Goal, wake Temporal, query GitHub, execute an Agent,
create a new persisted identity, or infer a Run from a Session.

The projection keeps task_id == goal_id for this **one** PR Watch consumer;
it is not a general Task-to-Run cardinality promise. Future durable Tasks
should be introduced only after the PR Watch vertical slice proves a second
consumer's requirements.

## Supported local API

The same loopback-only pilot API credentials and Host restrictions apply.
Principal is taken from a trusted Bearer-token mapping and is **never**
accepted from request query or body. Both stores are scoped before projection.

| Endpoint | Response |
| --- | --- |
| GET /api/tasks | A schema-tagged list of at most the existing 100 scoped Goals |
| GET /api/tasks/{goal_id} | A scoped task projection or 404 |
| GET /api/events?after=N | Existing per-user event stream; no new Activity store |
| GET /api/goals/{goal_id}/launch | Existing Workflow launch journal; no new orchestration status |

Query parameters to new endpoints are rejected. There are no Task POST,
PATCH, action or write routes. Existing Goal pause/resume/cancel endpoints
remain the only CAS-controlled pilot lifecycle actions.

### Projection states

| Goal + launch evidence | Projected state | What is NOT proven |
| --- | --- | --- |
| RUNNING; launch PENDING | WAITING | Workflow start |
| RUNNING; launch UNKNOWN/IN_FLIGHT/SKIPPED | UNKNOWN | Workflow ownership or non-start |
| RUNNING; launch ACKED/OBSERVED; no blocker | ACTIVE | Success, approval or future state |
| RUNNING; launch ACKED/OBSERVED; blocked/waiting | WAITING | User was notified |
| PAUSED | PAUSED | Provider worker already paused |
| CANCELLED | CANCEL_REQUESTED | Workflow has terminated |
| FINISHED; phase MERGED/CLOSED | OBSERVED_TERMINAL | Authenticated business success |

Every record sets business_outcome_verified=false and independent_proof_ref=null.
Its optional observation_digest is the persisted hash of a GitHub snapshot,
**not** a DSSE signature, attestation or trusted producer identity.
CI_CLEARED does not mean merge eligibility or Task completion. No projected
state is SUCCEEDED.

If the source Goal/launch scope mismatches, an unknown enum or malformed
observation digest is encountered, the projection refuses to render an
optimistic result. The reader keeps authentication, user-scope boundaries
and the no-network-on-read guarantee.

## Reproducible checks

    pytest -q tests/test_pr_watch_task_projection.py
    pytest -q tests/test_pr_watch_service.py

For the existing PostgreSQL integration, use an operator-owned disposable
database and explicit migration setup. This thin projection does not install
tables or change migration checksums.

## Next proof gates

1. Real GitHub delivery through reviewed TLS ingress, PG16 Inbox and Temporal
   History; preserve PR Watch #185.
2. Trusted independent outcome/operation proof with tenant, principal, policy
   generation and target UID binding (ACP #191); do **not** convert snapshot
   digests or runtime typed receipts into signer authenticity.
3. Real worker restart, ambiguous ACK and scope/approval-revocation exercises.
4. GPUPanel consumes the **read-only** projection through a separately
   authenticated, reviewed integration instead of exposing the loopback
   reference server directly or bypassing its Core boundary.
5. Only after business-outcome trust is proven, consider a separately versioned
   terminal-success projection; no silent promotion of OBSERVED_TERMINAL.

Parent tracker: https://github.com/avaargsh/agent-control-plane/issues/192
