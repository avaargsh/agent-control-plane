# v0.1 Release Readiness

## Release gate

A public v0.1 candidate should satisfy all of the following:

- [x] deterministic Golden Incident demo
- [x] replay-verifiable ReleaseEvidence
- [x] explicit Kubernetes/Temporal runtime boundaries
- [x] opt-in live smoke preflight
- [x] kubectl and Temporal CLI transports
- [x] known limitations documented
- [x] repository license selected and added (Apache-2.0)
- [x] CONTRIBUTING.md added
- [x] public README internal links checked against repository tree
- [x] current-tree secret/token pattern scan clean
- [x] current-tree private/customer identifier spot-check clean
- [ ] full git-history secret/private-data scan complete
- [x] direct dependency/license review complete
- [ ] live kind/minikube + Temporal smoke exercised and documented
- [x] GitHub Actions runner issue documented
- [ ] fresh-clone demo verification complete
- [x] v0.1.0 release notes prepared
- [ ] v0.1.0 tag/release created

## Dependency/license review

Declared direct/build/dev dependencies are intentionally small:

- `jsonschema>=4.23` — MIT
- `PyYAML>=6.0` — MIT
- `pytest>=8.0` — MIT (development only)
- `hatchling>=1.25` — MIT (build backend)

No direct copyleft dependency was identified in the declared project metadata. Transitive dependencies should still be checked from a resolved lock/environment before a formal distribution review.

The package metadata now explicitly declares `Apache-2.0` and includes `LICENSE`.

## Open-source audit

Before changing repository visibility:

1. Search current tree and git history for credentials, internal endpoints, private customer names, copied production manifests and proprietary material.
2. Verify example incident IDs, node names, evidence URIs and topology names are synthetic.
3. Ensure no production ReleaseEvidence, approval payloads or secrets are committed.
4. Review provider names and integration examples for trademark/license concerns.
5. Run `make demo` from a fresh clone.
6. Run the live smoke profile in a controlled local environment and capture the outcome outside git.
7. Confirm README claims are limited to behavior actually exercised.

### Audit performed

Current default-branch code search returned no matches for representative credential patterns:
`BEGIN PRIVATE KEY`, `AKIA`, `ghp_`, `sk-`, `api_key`, `Bearer`, `ssh-rsa`, `password`.

Spot checks also returned no matches for generic customer markers or known prior-employer naming. Repository-tree review confirmed README-linked `DEVELOPMENT.md` and `RELEASE_READINESS.md` exist.

Recent commit metadata was reviewed for suspicious credential/private-data wording with no obvious finding. This does **not** inspect every historical blob: a local full-history scanner such as gitleaks/trufflehog remains required before repository visibility changes.

## Known limitations

- The project is a reference control plane, not a production multi-tenant service.
- CLI transports prove the integration boundary but are not a substitute for hardened SDK/service clients.
- Live Kubernetes/Temporal smoke requires a compatible Sandbox CRD and workflow worker; preflight alone is not an end-to-end execution.
- Secret management, quota/budget enforcement, tenant isolation and HA deployment are intentionally incomplete.
- Provider prepare/execution contracts are still evolving; backward compatibility is not guaranteed before v1.
- The project does not own or redefine Temporal continuation semantics, Kubernetes sandbox semantics, MCP semantics or harness behavior.
- GitHub Actions has recently shown jobs failing before execution with no steps/logs; treat that as CI infrastructure state until runner execution is restored.

See [RELEASE_NOTES.md](RELEASE_NOTES.md) for the v0.1.0 candidate notes.
