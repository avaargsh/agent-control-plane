# v0.1 Release Readiness

## Release gate

A public v0.1 candidate should satisfy all of the following:

- [x] deterministic Golden Incident demo
- [x] replay-verifiable ReleaseEvidence
- [x] explicit Kubernetes/Temporal runtime boundaries
- [x] opt-in live smoke preflight
- [x] kubectl and Temporal CLI transports
- [x] known limitations documented
- [ ] repository license selected and added
- [ ] CONTRIBUTING.md added
- [ ] public README links/screenshots checked
- [ ] secret/token scan clean
- [ ] private/customer identifiers review complete
- [ ] dependency/license review complete
- [ ] live kind/minikube + Temporal smoke exercised and documented
- [ ] GitHub Actions runner issue resolved or documented
- [ ] v0.1.0 tag/release notes prepared

## Open-source audit

Before changing repository visibility:

1. Search current tree and git history for credentials, internal endpoints, private customer names, copied production manifests and proprietary material.
2. Verify example incident IDs, node names, evidence URIs and topology names are synthetic.
3. Ensure no production ReleaseEvidence, approval payloads or secrets are committed.
4. Review provider names and integration examples for trademark/license concerns.
5. Choose a license deliberately; do not publish with no license.
6. Run `make demo` from a fresh clone.
7. Run the live smoke profile in a controlled local environment and capture the outcome outside git.
8. Confirm README claims are limited to behavior actually exercised.

## Known limitations

- The project is a reference control plane, not a production multi-tenant service.
- CLI transports prove the integration boundary but are not a substitute for hardened SDK/service clients.
- Live Kubernetes/Temporal smoke requires a compatible Sandbox CRD and workflow worker; preflight alone is not an end-to-end execution.
- Secret management, quota/budget enforcement, tenant isolation and HA deployment are intentionally incomplete.
- Provider prepare/execution contracts are still evolving; backward compatibility is not guaranteed before v1.
- The project does not own or redefine Temporal continuation semantics, Kubernetes sandbox semantics, MCP semantics or harness behavior.
- GitHub Actions has recently shown jobs failing before execution with no steps/logs; treat that as CI infrastructure state until runner execution is restored.

## Suggested v0.1 release note

**Agent Control Plane v0.1** establishes a thin, provider-neutral release/evidence plane across AgentRelease, approval snapshots, Kubernetes sandbox identity, Temporal workflow identity, evaluation gates and replay-verifiable ReleaseEvidence. It is a reference architecture and executable prototype, not a replacement for the underlying runtimes.
