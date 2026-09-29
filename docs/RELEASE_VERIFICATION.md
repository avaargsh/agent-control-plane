# Release verification

Run these commands from a fresh, real Git clone before creating `v0.1.0`:

```bash
make setup
make verify-release
make audit-history
```

`make verify-release` checks package release metadata, runs the complete pytest suite and executes the deterministic GPU XID Golden Incident. A successful run writes `.artifacts/release/verification.json`.

`make audit-history` scans the real Git history with gitleaks when available, otherwise trufflehog. It exits with status 2 if neither scanner is installed; absence of a scanner is never treated as success.

The environment-dependent integration remains a separate release gate:

```bash
make preflight
make smoke
```

A live smoke requires a compatible Kubernetes sandbox API/CRD and a Temporal service/worker. Preflight success alone is not end-to-end validation.
