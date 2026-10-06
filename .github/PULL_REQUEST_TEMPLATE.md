## Summary

<!-- What changed, and why is this the smallest useful slice? -->

## Control-plane invariant impact

Check every invariant touched by this change.

- [ ] Exact-plan authorization / approval binding
- [ ] Generation, resourceVersion, lease, or execution fencing
- [ ] Durable PREPARED / terminal execution identity
- [ ] UNKNOWN outcome reconciliation / retry semantics
- [ ] Fresh independent observation
- [ ] Operation-ownership proof / execution proof
- [ ] Schema / acceptance artifact contract
- [ ] No control-plane invariant changes

## Architecture boundary

- [ ] This change does not move Agent harness, workflow durability, MCP, sandbox, IAM, model-serving, or provider-specific authority semantics into the control plane.
- [ ] New provider behavior conforms to the provider-neutral transition / execution boundary.
- [ ] Any new operational or security assumption is documented below.

## Evidence

Commands run:

```text
# paste exact commands
```

Results / artifacts:

```text
# paste concise results or artifact paths
```

For live execution-path changes, include the relevant fresh-observation / proof verification path. Unit tests alone are not sufficient evidence for a live-provider claim.

## Compatibility and release impact

- [ ] No public schema / serialized artifact change
- [ ] Additive compatible change
- [ ] Breaking / migration-sensitive change documented
- [ ] Release notes / freeze docs updated if required

## Risk and rollback

<!-- Failure mode, blast radius, rollback/recovery path. Use "none" only when truly none. -->
