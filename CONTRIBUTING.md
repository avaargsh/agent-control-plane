# Contributing

Thanks for your interest in improving this project.

## Before opening a change

- Keep changes small and focused.
- Preserve the thin-control-plane boundary. Do not move Temporal, sandbox, MCP or harness execution semantics into the control plane.
- Prefer existing provider/executor/runtime-client seams before creating new abstractions.
- Do not commit credentials, production ReleaseEvidence, approval payloads, customer identifiers, private endpoints, proprietary configs, or copied vendor material.
- Add or update tests for behavior changes.
- Keep demo incidents, resource names and evidence URIs synthetic.

## Development

```bash
make setup
make test
make demo
```

For live validation:

```bash
make preflight
make smoke
```

Normal unit tests must remain independent of external Kubernetes or Temporal services.

## Pull requests

A pull request should explain:

1. what changed,
2. which control-plane invariant is affected,
3. who owns continuation/execution after the change,
4. how evidence and replay semantics are preserved,
5. how the change was tested,
6. any new operational or security assumptions.

## Compatibility

v0.x contracts may evolve. Prefer additive changes and explicit migration notes, but backward compatibility is not guaranteed before v1.0.

## License

By contributing, you agree that your contributions will be licensed under the Apache License 2.0.
