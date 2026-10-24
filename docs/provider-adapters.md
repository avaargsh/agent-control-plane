# Provider Adapters

## Why adapters exist

The portable core should not pretend all runtimes have identical semantics.

Instead:

```text
ResolvedReleasePlan
        |
        +--> Harness Adapter
        +--> Workflow Adapter
        +--> Sandbox Adapter
        +--> Tool Adapter
        +--> Model Adapter
```

Each adapter translates stable control-plane intent into provider-specific preparation.

## Adapter contract

A provider adapter identifies:

- `provider_type`
- `provider_name`

and implements:

```python
prepare(plan, binding) -> provider_specific_plan
```

Preparation is intentionally separated from execution.

This allows:

- validation before mutation,
- policy checks,
- plan inspection,
- dry-run,
- release evidence,
- easier testing.

## Initial examples

### Codex Harness

Owns coding-agent execution semantics. The control plane provides canonical Release/Session identity and capability bindings.

### Temporal Workflow

Owns durable workflow state, retries, timers and waits.

The control-plane runtime adapter derives a stable operation identity from the
canonical Release reference, Workflow ID, Workflow type, Task Queue, and desired
input. A retry of the same intent must reuse this identity; changing the desired
input under the same Workflow ID produces a different identity.

An existing nonterminal Workflow is attachable only when its recorded
`operationId` matches the requested identity. Missing or mismatched ownership
metadata is an ambiguity, not permission to adopt the Workflow. A concrete
Temporal transport must persist the operation identity in server-visible
Workflow metadata (for example, memo) and return it from `describe`; it must
not infer ownership from Workflow ID or RUNNING status alone.

This deterministic reference identity is an idempotency/ownership fence, not an
authorization credential or proof that a provider-side effect completed.

#### Official Temporal Python SDK transport (opt-in developer reference)

Install the optional dependency with `pip install -e '.[temporal]'`. A
small synchronous transport now delegates to the official Temporal Python SDK:

```python
from agent_control_plane.temporal_sdk_api import TemporalSDKApi
from agent_control_plane.temporal_runtime_client import TemporalWorkflowClient

api = TemporalSDKApi(target_host="127.0.0.1:7233", namespace="default")
controller = TemporalWorkflowClient(api)
result = controller.ensure_workflow({
    "release": "example-release",
    "workflow_type": "ExistingWorkerWorkflow",
    "task_queue": "existing-worker-task-queue",
    "input": {"request_id": "request-123"},
})
```

The Workflow type and Worker are **external** and must already be deployed;
ACP does not host or implement them. The adapter persists the deterministic
ACP `operationId` in a server-visible Workflow Memo
(`acp_operation_id`) and re-reads it from Temporal after starting. Existing
RUNNING workflows can be attached only when the Memo matches. SDK
`WorkflowIDConflictPolicy.FAIL` and
`WorkflowIDReusePolicy.REJECT_DUPLICATE` prevent a duplicate or terminal
Workflow ID from silently starting a replacement.

This simple reference is **loopback-only** and intentionally has no remote
TLS/authentication configuration: non-loopback Temporal targets fail closed.
It uses a fresh SDK connection per operation because the ACP API is
synchronous. Do not call it on an asyncio event-loop thread
(use `asyncio.to_thread`). Production connectivity requires a separate,
authenticated TLS transport, proper async client lifecycle, and approval
before any provider mutation.

Termination is *only* a transport request: it must be entered through an
already-authorized ACP exact-plan boundary. The SDK transport now requires
both `expected_operation_id` and `expected_run_id` from the original
execution receipt and compares them to a fresh Temporal description.
Missing, foreign or terminal targets fail closed; no termination RPC is sent.
The normal Temporal executor forwards these identities from its apply receipt.
For an explicit controller operation, use:
```python
controller.terminate_workflow(
    result.resource_ref,
    expected_operation_id=result.evidence["operationId"],
    expected_run_id=result.evidence["runId"],
)
```
The adapter pins the termination RPC to that exact Temporal Run ID and
returns `terminationRequested`, **not** independently verified completion.
An ambiguous ACK and missing follow-up observation stay UNKNOWN; an explicit
ownership denial can never be reconciled into success. These IDs are
idempotency/ownership fences, not authentication credentials. External ACP
authorization is still required before invoking the transport. Do not expose
this adapter directly as a public mutation API.

The focused integration test starts the Temporal SDK local test server (not
Temporal Cloud or a production server):

```sh
python -m pip install -e '.[dev,temporal]'
pytest -q tests/test_temporal_sdk_api.py
```

This verifies SDK transport interoperability, memo identity, duplicate-ID
conflicts and the request path. It does not prove a real Worker executed,
production auth/TLS, persisted Temporal server restart, or v0.1 independent
execution proof.

### Kubernetes Agent Sandbox

Owns isolated execution environment lifecycle. A binding may express portable intent such as isolation class, warm-pool preference and placement constraints.

## Non-goal

Provider adapters must not grow into a shadow implementation of the provider itself.
