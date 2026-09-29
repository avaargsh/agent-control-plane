# Evidence and Trace Correlation

The control plane correlates provider execution without replacing provider-native telemetry.

## Identity layers

```text
AgentRelease / Session / Run        <- canonical control-plane identity
              |
              +---- trace_id         <- telemetry correlation
              |
              +---- external_refs    <- Temporal / Sandbox / Harness IDs
```

Provider identifiers are evidence, not canonical identity.

## OpenTelemetry policy

Use existing OpenTelemetry semantic conventions for HTTP/RPC/GenAI provider spans. Control-plane-specific identity is added as a small experimental `agentplane.*` namespace:

- `agentplane.release.id`
- `agentplane.run.id`
- `agentplane.session.id`
- `agentplane.external_ref.<provider-key>`

Do not copy prompts, model outputs, credentials, tool payloads or sandbox filesystem content into release evidence by default.

## Evidence envelope

A release evidence record should carry:

1. canonical release/run/session refs,
2. trace correlation,
3. provider external refs,
4. policy decision,
5. execution receipts,
6. eval results,
7. promotion/rollback outcome.

This lets replay and EvalGate consume stable evidence while detailed runtime telemetry stays in the telemetry backend.
