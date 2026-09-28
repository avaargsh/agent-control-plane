# Decision Plane Binding

Bounded decisions are modeled as a separate runtime concern rather than being hidden inside a Harness.

## Why

Some Agent decisions are:

- high frequency,
- closed-set,
- verifiable,
- latency/cost sensitive.

Examples:

- MCP tool routing,
- severity / relevance,
- allow / deny / escalate,
- fallback / continuation.

These decisions can be routed through a specialized Decision Gateway while the Harness remains responsible for open-ended reasoning and generation.

## Binding

```yaml
apiVersion: agentplane.io/v1alpha1
kind: RuntimeBinding
metadata:
  name: decision-hot-path
spec:
  type: decision
  provider: decision-gateway
  endpointRef: service://agent-decision-lab
  config:
    decisionTypes:
      - mcp_tool_router
      - severity
      - escalation
    fallbackBinding: harness-codex
    policyRef: bounded-automation-default
```

## Control flow

```text
Task
  |
Decision Type Match?
  | yes
  v
Decision Gateway
  |
Confidence Gate
  |---------------- low confidence ----------------|
  v                                              v
Bounded Decision                           Harness / System-2
  |                                              |
  +-------------------------+--------------------+
                            v
                Deterministic Authorization
```

## Boundary

The Decision Plane may recommend a bounded action.

It does not replace:

- authorization,
- approval,
- budget,
- irreversible-action policy,
- provider execution.

Those remain deterministic control/runtime responsibilities.
