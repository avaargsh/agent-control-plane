# Evidence-bound State Transition Protocol

The control plane authorizes **state transitions**, not provider commands.

This protocol is the minimal trust chain between frozen evidence and a real
provider side effect:

```text
EvidenceItem
    |
    v
EvidenceBundle (content-addressed manifest)
    |
    v
StateTransition
    |             \
    |              -> OutcomeContract
    v
ActionIntent
    |
    v
AuthorizationBinding
    |
    v
ExecutionFence
    |
    v
provider-edge validate_execution()
    |
    v
side effect
```

## Protocol thesis

For a transition `T`, evidence `E`, action `A`, authorization `Z`,
generation `G`, and lease `L`:

```text
Authorize(T, E, A, Policy, Approval, G) -> Z

Execute(T) iff
    hashes(T, E, A, Z) verify
    and currentGeneration == G
    and activeLease.id == fence.lease_id
    and activeLease.epoch == fence.lease_epoch
    and activeLease.holder == caller
    and authorization/fence/lease are unexpired
```

Provider acceptance is not success. Success remains a later verification
decision against the bound `OutcomeContract`.

## Identity-addressed vs content-addressed

Stable logical identities identify *which object* is being discussed:

- `transition_id`
- `action_id`
- `lease_id`
- provider resource UID

Content hashes identify *which immutable content* was authorized:

- `EvidenceItem.payload_hash`
- `EvidenceBundle.manifest_hash`
- `OutcomeContract.contract_hash`
- `StateTransition.transition_hash`
- `ActionIntent.action_hash`
- `AuthorizationBinding.authorization_hash`

Reusing a logical ID never authorizes changed content.

## Evidence

An `EvidenceItem` binds:

- source
- query semantics
- collector name/version
- collecting principal
- collection time
- canonical payload hash

An `EvidenceBundle` is a canonical-sorted manifest of evidence references,
bound to one resource identity and one generation.

Immutable storage alone is insufficient. Every consumer must verify the
content digest before trusting the artifact.

## StateTransition

A transition binds:

- provider resource UID
- expected generation
- before state
- desired state
- evidence bundle hash
- outcome contract hash

The transition contains no provider command. Provider planning happens after
the semantic state change has been defined.

## ActionIntent

Provider planning creates an immutable `ActionIntent`.

For the Kubernetes scale slice:

```json
{
  "provider": "kubernetes",
  "operation": "scale_deployment",
  "parameters": {
    "replicas": 30
  }
}
```

The action hash is bound into authorization. An approval for replicas `30`
therefore cannot be reused for replicas `40`, even if `action_id` is the
same.

## AuthorizationBinding

Authorization binds:

- transition hash
- evidence hash
- action hash
- policy version
- policy decision hash
- approval hash, when required
- generation
- authorized principal
- expiry

The provider never infers authorization from an Action ID, workflow ID, or a
previous successful call.

## ExecutionFence

A fence binds authorization to the current execution owner:

- provider resource UID
- desired generation
- lease ID
- lease holder
- monotonic lease epoch
- transition hash
- action hash
- authorization hash
- effective expiry

TTL alone does not fence a resumed stale controller. The epoch is the fencing
token.

## Provider-edge validation

`validate_execution()` is intentionally a final check immediately before the
side effect. It fails closed when any of these conditions are observed:

- evidence or outcome contract digest mismatch
- changed transition after authorization
- changed provider action after authorization
- resource generation drift
- replaced lease ID
- stale lease epoch
- changed lease holder
- caller is not the lease holder
- expired lease, authorization, or fence

This does not remove the check/execute race by itself. A real provider adapter
must evaluate the fence as close to mutation as the provider permits, ideally
using a provider-native compare-and-swap/resource-version precondition.

## Current scope

The protocol is provider-neutral, but the first vertical slice remains
Kubernetes Deployment scale:

```text
Kubernetes + Prometheus observation
        |
        v
EvidenceBundle
        |
        v
20 -> 30 StateTransition
        |
        v
OPA + exact approval
        |
        v
Generation + lease epoch fence
        |
        v
Kubernetes patch
        |
        v
independent observation
        |
        v
OutcomeContract verification
```

Recovery, receipts, reconciliation, and deterministic replay remain separate
layers. The existing runtime lost-ACK reconciliation code can now be bound to
this protocol rather than treating provider calls as the authority object.

## Acceptance invariants

The executable tests in `tests/test_state_transition_protocol.py` currently
prove:

- source payload mutation cannot mutate the captured evidence snapshot
- tampered evidence is rejected
- missing evidence is rejected when resolving a bundle
- a changed action after approval is rejected
- a changed/resealed transition cannot reuse the old authorization
- generation drift is rejected
- stale lease epochs are rejected
- replaced lease IDs are rejected
- stale controllers are rejected
- authorization expiry fails closed
- tampered outcome contracts are rejected
- canonical hashes do not depend on mapping key order
- no-op transitions are rejected

Next implementation step: bind this protocol to one real Kubernetes
Deployment scale provider and make the final patch use Kubernetes
resourceVersion/generation preconditions where possible.
