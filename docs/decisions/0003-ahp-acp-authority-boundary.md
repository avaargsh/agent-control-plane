# ADR-0003: AHP/ACP compatibility without a second authority

- Status: Proposed, post-v0.1 reference-only spike
- Upstream design reference: formulahendry/ahp-acp-gateway (MIT), inspected at 9bcd0d4b
- Related constraints: ADR-0001 thin control plane and docs/V0.1_FREEZE.md

## Decision

Use AHP-style Channel / pure Reducer / sequence / Replay-or-Snapshot as an
ephemeral **read projection** of existing Control Plane facts. Introduce an ACP
Provider Adapter observation/permission-intent seam, but leave ACP process
management, tool execution and provider-specific session semantics in external
Agent harnesses.

    AHP client / IDE / Desktop (future, via official AHP SDK)
                         |
                authenticated client edge
                         | per-tenant/session read ACL
                         v
               AHP read-only projection
                         ^
                         | VERIFIED CP journal/SoT events ONLY
                         |
               Control Plane authority
         Canonical tenant/session/release identity
         Exact-plan policy / approval / fence / lease
         PREPARED -> UNKNOWN/reconcile -> proof
                         ^
                         | untrusted observations / permission intent
             ACP provider event normalizer
                         ^
                         |
         Codex / Qoder / Claude / OpenCode harness

## Authority mapping

| Concept | Authority owner | Compatibility layer permission |
|---|---|---|
| Tenant/canonical session/release identity | Control Plane | Carry bound identity; reject mismatches |
| Policy, human approval, execution authorization | Existing CP pipeline | Observe recorded decision; emit intent only |
| Execution, fence, reconciliation, proof | CP journal + independent verifier | Display verified status/proof ref only |
| ACP process/session/context | External harness/provider | Normalize ACP observations |
| AHP replay/subscriptions | Ephemeral edge projection | Per-subscriber authorized replay or snapshot |

ACP tool completion MUST NOT imply execution success or independent proof.
AHP clients MUST NOT be allowed to mutate control-plane authoritative state
through the projection. The execution/approval/policy SoT remains unchanged.

## Scope of reference implementation

- src/agent_control_plane/ahp_projection.py: in-memory ChannelKey with canonical
  tenant/session identity, strict per-channel revisions, global monotonic
  server sequence, bounded action ring, exact resend idempotence, snapshot
  fallback, read authorization hook, verified writer hook and field/status
  allowlist. VERIFIED execution requires an independent proof reference.
- src/agent_control_plane/acp_provider_adapter.py: provider-neutral ACP v1
  observation adapter protocol, canonical-to-provider session binding, text
  streaming/tool/plan observation mapping and permission intent (never approve).
- tests/test_ahp_projection.py and tests/test_acp_provider_adapter.py:
  authorization, out-of-order/conflicting events, replay overflow,
  cross-session/cross-tenant rejection, ACP tool completion and proof boundaries.

IMPORTANT: This is NOT an AHP WebSocket server, production persistent channel
store, live ACP provider launcher, or a multi-tenant authentication service.
It changes no v0.1 schema, CLI, public export, journal, fence, lease, execution
proof or provider registration. Production execution code does not import it.

A caller must verify that a source_ref came from the durable Control Plane
committed journal and that a projected status is authorized by the current
policy/approval and execution evidence. A fake verifier returning true is
suitable ONLY for isolated tests. The caller must authenticate subscriptions
and authorize each ChannelKey before reading either actions or snapshots.

## Follow-up gates (not included here)

1. Implement a source reader from **existing** CP committed journal/approval
   records; bind durable revision, canonical tenant/session, decision hash,
   lease/fence and independently verified proof. Rebuild projection at startup.
2. Add an AHP protocol edge using official SDK, validated channel URIs, secure
   authentication/Origin policy, backpressure and per-session read ACL. Do not
   adopt upstream unauthenticated local WebSocket defaults.
3. Introduce a real ACP transport with provider capability negotiation,
   process/sandbox isolation, cancellation/timeout and live Codex/Qoder smoke.
4. Run authority-negative tests: ACP prompt/tool injection cannot approve a
   change; stale fences and lost ACK remain untrusted; cross-tenant replay
   must fail; no provider side effect may bypass the exact-plan execution gate.
5. Observe replay memory, slow subscribers, redaction and trace correlation;
   establish resource budgets and recovery snapshots.

## Alternatives rejected

- Copying the upstream SQLite store as a second session/permission authority.
- Building another agent loop, terminal or workflow engine inside the CP.
- Turning ACP tool completion into a provider mutation proof.

## Rollback

Delete the two opt-in modules, their tests and this ADR. Existing v0.1
execution paths are unchanged and require no data migration.
