# Production Golden Slice

The project now targets one production-shaped reference path rather than broad framework coverage.

```text
Alertmanager
    |
    v
Agent Gateway
    |
    v
AgentRelease + Release Policy
    |
    v
ResolvedReleasePlan
    |
    +--> capability / conformance check
    |
    v
Temporal WorkflowBinding
    |
    v
OpenAI Agents HarnessBinding
    |
    +--> Decision Gateway
    |      confidence gate
    |      low confidence -> System-2 harness
    |
    +--> MCP read-only capability
    |
    +--> Kubernetes Agent Sandbox claim
    |
    v
ExecutionReceipt + external_refs
    |
    v
OTel trace correlation
    |
    v
Evidence / Replay Metrics
    |
    v
EvalGate
    |
    +--> promote
    +--> manual review
    +--> rollback
```

## Control-plane ownership

The control plane owns release intent, binding resolution, capability requirements, policy, placement intent, evidence correlation and promotion gates.

It does not own Temporal workflow history, the harness model loop, MCP transport semantics, sandbox process lifecycle, or provider-native telemetry.

## Definition of done

The golden slice is production-shaped when:

- every mutable provider action returns an idempotent receipt,
- canonical Run/Session/Release identity survives provider changes,
- provider IDs remain external refs,
- required capabilities fail closed before mutation,
- one trace can correlate workflow, harness, decision, tool and sandbox activity,
- replay metrics feed EvalGate,
- rollback only compensates resources proven to be owned by the apply operation.

## Remaining executable work

1. run conformance checks automatically before provider apply,
2. add async provider worker boundary,
3. connect Alertmanager ingress to AgentRelease,
4. execute replay from stored evidence,
5. add integration tests against real Temporal and Agent Sandbox environments.
