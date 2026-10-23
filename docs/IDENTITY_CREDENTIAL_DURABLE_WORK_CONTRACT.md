# Identity, Credential Binding, and Durable Work Contract

Status: proposed, documentation-only. This contract does not change ACP v0.1 authority, enable a listener, issue credentials, or claim production acceptance.

## Purpose

Define the smallest integration contract for a governed durable task without creating a second task ledger, generic IAM implementation, or custom distributed workflow engine.

The first consumer remains the existing GitHub PR Watch Golden Slice. General Agent workloads are out of scope until that slice passes operator-owned live acceptance.

## Ownership boundaries

- **ACP application service:** owns business Task/Goal state, owner and tenant/project scope, trigger Inbox, approval state, and business terminal transitions.
- **Temporal:** owns durable workflow history, timers, signals, retry scheduling, and worker takeover. Temporal history is not a second business Task database or authorization authority.
- **Agent SDK / harness:** executes agent work; it does not independently authorize external side effects or declare business success.
- **Identity / credential issuer:** authenticates the logical principal and execution instance, resolves a trusted CredentialBinding, and issues or brokers short-lived credentials through an established issuer. Do not implement a general IAM or secret vault here.
- **Provider adapter:** checks current authority immediately before an effect, applies the exact operation, and preserves ambiguous outcomes for reconciliation.
- **Independent observer:** obtains external state independently of the agent/provider ACK and produces provenance-bound evidence.
- **GPUPanel or other UI:** displays an owner-scoped read-only projection; it is not a privileged execution path.

## Identity vocabulary

| Concept | Meaning | Must not be used as |
|---|---|---|
| `AgentIdentity` | Stable logical agent/principal identity under an owner and tenant/project scope | Proof that a particular process is running or authorized for every operation |
| `WorkloadIdentity` | One execution incarnation, bound to its authenticated runtime/workload and lifecycle | A durable conversation identifier or reusable identity after replacement |
| `SessionIdentity` | Stable correlation handle for a conversation/work session across restart | An authorization credential or substitute for principal identity |
| `CredentialBinding` | Policy reference binding an agent principal and permitted workload to a specific external application, audience, scopes, and issuer policy | A place to store bearer tokens or private keys |
| `Run` | One execution attempt/incarnation associated with a business Task and workflow | The business Task itself |
| `Task` | Durable business intent and authoritative lifecycle state | A copy of Temporal Workflow History |

Every authorization decision must resolve the current trusted binding; matching IDs alone is not authorization. A session may survive while workloads are replaced, and every replacement must authenticate as a new workload incarnation.

## Credential handling requirements

1. Credentials are short-lived and least-privilege. Validate exact issuer, audience, scope, subject/workload binding, tenant/project, operation/target, not-before and expiry.
2. Re-check current binding, policy and revocation at the actual provider effect boundary. A credential or approval valid at task creation is not permanent authority.
3. Enforce replay resistance and operation idempotency with durable server-side state; do not rely on a caller-supplied nonce without checking it against authoritative state.
4. Do not place bearer credentials, private keys, refresh tokens or authorization headers in Task rows, Temporal payloads/history, traces, logs, prompts, screenshots or evidence artifacts. Persist only non-secret references and redacted audit facts.
5. Credential issuer outages, unknown keys, revoked identities, stale policy, expiry, wrong audience/scope and ambiguous authority reads fail closed before an effect. If an external effect may already have happened, record UNKNOWN and reconcile; never claim NOT_APPLIED without evidence.
6. Key trust, issuance, rotation, revocation, audit ownership and recovery are operator/deployer responsibilities until an explicit, tested issuer integration exists. Offline DSSE signature validation alone does not prove the signer was authorized or the observation is fresh.

## Durable work and recovery

- Use the existing ACP business Task/Goal record and PG16 Inbox as the business source of truth. Do not introduce a second Task table or copy Temporal history into PostgreSQL.
- Use Temporal for durable orchestration, timers, signals, retry/backoff, and worker recovery. Use deterministic workflow code and keep external side effects behind idempotent, authority-checked activities.
- Deduplicate webhook/event delivery durably before waking a workflow. A lost acknowledgement must not cause an unbounded second signal or duplicate side effect.
- Bind each attempt to immutable Task ID, Run ID, workflow reference, exact operation ID, tenant/project, provider target, and where applicable immutable resource UID/generation.
- Approval is a scoped, expiring input to a specific operation and target. Delayed, revoked, stale, cross-project or mismatched approval is denied at the effect boundary.
- A worker/session/sandbox replacement resumes the same Task identity but creates a distinct execution incarnation; recovery must not silently transfer old workload credentials.
- Distinguish retryable known failure from ambiguous provider outcome. Preserve UNKNOWN until independent observation or durable provider reconciliation resolves it.

## Business outcome and evidence

The following are not sufficient by themselves to mark a Task `SUCCEEDED`: an LLM statement, agent tool result, provider HTTP 2xx/ACK, desired state, webhook payload, Temporal workflow completion, trace, or a signature over untrusted claims.

A success transition requires:
- independently fetched current external state;
- exact Task/Run/operation/target and tenant/project correlation;
- trusted observer provenance and freshness;
- proof authentication against an operator-controlled trust profile;
- current policy/authorization checks appropriate to the effect; and
- durable, redacted, content-addressed evidence references.

If any required observation or trust check is missing, contradictory, stale, or UNKNOWN, keep the business outcome unverified and do not issue release GO.

## Required negative tests

At minimum, test:
- wrong principal, workload, tenant, project, application, audience, scope or target;
- expired, not-yet-valid, revoked or rotated-away credential and signer;
- duplicate delivery, conflicting delivery ID, replayed nonce and same-name resource recreated with a different UID;
- session retained across worker restart while old workload credentials are rejected;
- stale or withdrawn approval and policy change immediately before effect;
- worker SIGKILL, lost ACK, Temporal/observer outage, PostgreSQL connection loss, retry and process restart;
- provider outcome ambiguity and independent observer disagreement;
- evidence referring to a different Task, Run, operation, resource UID/generation or tenant;
- unauthorized read/write attempts and redaction of secrets from logs/history/evidence.

## Acceptance grades and sequencing

Keep these evidence grades separate: unit/synthetic tests; real PG16 + Temporal integration; operator-owned isolated staging with actual external event delivery; live provider/observer acceptance; production readiness.

1. Finish ACP [#185](https://github.com/avaargsh/agent-control-plane/issues/185): operator-owned isolated environment, real GitHub delivery, independently observed outcome, duplicate/lost-ACK recovery, rollback, and signed GO/NO-GO.
2. Finish ACP [#191](https://github.com/avaargsh/agent-control-plane/issues/191): trusted signer/issuer lifecycle, current-policy and replay-safe proof admission, fresh independent observation, and consumer integration.
3. Add only the identity and credential binding fields proven necessary by the first live slice; keep the frozen ACP v0.1 authority contract unchanged.
4. Run restart/chaos and negative cases against one pinned environment and exact source SHA. Obtain independent review before any non-draft merge or release claim.

Until these gates pass, this document is a proposed integration contract, not an implementation or production-security claim.
