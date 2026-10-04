# v0.1 Release Readiness

## Release gate

A public v0.1 candidate should satisfy all of the following:

- [x] deterministic Golden Incident demo
- [x] replay-verifiable ReleaseEvidence
- [x] release acceptance contract covers promote / block / partial rollback / recovery identity loss
- [x] replayable Golden Slice trajectory preserves canonical identity across sandbox replacement
- [x] explicit Kubernetes/Temporal runtime boundaries
- [x] opt-in live smoke preflight
- [x] kubectl and Temporal CLI transports
- [x] real OPA v1.21.0 policy-enforcement proof
- [x] cross-repository kind + Temporal + Kubernetes sandbox-replacement provenance proof
- [x] MCP Execution Contract fault proof (lost ACK / duplicate retry / timeout / partial commit / compensation)
- [x] provider-neutral TransitionPlan proven across Kubernetes scale and GitHub PR merge
- [x] exact-plan authorization/fence and plan-native durable execution journal
- [x] durable authority reservation survives crash/takeover ambiguity
- [x] provider side-effect crash/lost-ACK falsification matrix
- [x] stale/concurrent authority falsification matrix
- [x] independent out-of-process execution proof verification and tamper rejection
- [x] live kind scale proof emits ownership-backed IndependentExecutionProof and re-verifies it in a fresh process
- [x] v0.1 execution architecture freeze contract recorded
- [x] known limitations documented
- [x] repository license selected and added (Apache-2.0)
- [x] CONTRIBUTING.md added
- [x] public README internal links checked against repository tree
- [x] current-tree secret/token pattern scan clean
- [x] current-tree private/customer identifier spot-check clean
- [x] full git-history secret scan complete (Gitleaks v8.30.1, full checkout)
- [x] direct dependency/license review complete
- [ ] live kind/minikube + Temporal smoke exercised and documented
- [x] GitHub Actions runner issue documented
- [x] fresh-clone demo / MCP proof / wheel build verification complete
- [x] v0.1.0 release notes prepared
- [x] stale pre-RC pull requests reconciled; no open pull requests remain at final RC audit
- [ ] v0.1.0 tag/release created

## Dependency/license review

Declared direct/build/dev dependencies are intentionally small:

- `jsonschema>=4.23` — MIT
- `PyYAML>=6.0` — MIT
- `pytest>=8.0` — MIT (development only)
- `hatchling>=1.25` — MIT (build backend)

No direct copyleft dependency was identified in the declared project metadata. Transitive dependencies should still be checked from a resolved lock/environment before a formal distribution review.

The package metadata explicitly declares `Apache-2.0` and includes `LICENSE`.

## Open-source audit

Before changing repository visibility:

1. Search current tree and git history for credentials, internal endpoints, private customer names, copied production manifests and proprietary material.
2. Verify example incident IDs, node names, evidence URIs and topology names are synthetic.
3. Ensure no production ReleaseEvidence, approval payloads or secrets are committed.
4. Review provider names and integration examples for trademark/license concerns.
5. Run `make verify-release` from a fresh clone.
6. Run the live smoke profile in a controlled local environment and capture the outcome outside git.
7. Run the executable v0.1 release gate, including isolated wheel installation and fresh-clone smoke.
8. Confirm README claims are limited to behavior actually exercised.

### Audit performed

Current default-branch code search returned no matches for representative credential patterns:
`BEGIN PRIVATE KEY`, `AKIA`, `ghp_`, `sk-`, `api_key`, `Bearer`, `ssh-rsa`, `password`.

Spot checks also returned no matches for generic customer markers or known prior-employer naming. Repository-tree review confirmed README-linked `DEVELOPMENT.md` and `RELEASE_READINESS.md` exist.

Recent commit metadata was reviewed for suspicious credential/private-data wording with no obvious finding. The v0.1 release gate now performs `make audit-history` against a `fetch-depth: 0` checkout using pinned Gitleaks v8.30.1; the scanner completed successfully on the repository history.

The final RC audit reconciled stale pre-RC pull requests after preserving the release-acceptance contract and Golden Slice trajectory replay proof on main.

The executable v0.1 release gate now verifies the full test suite, deterministic Golden Incident, MCP Execution Contract proof, release wheel build, isolated wheel installation with declared dependencies, packaged schemas/CLI behavior, a clean local fresh-clone smoke, and a full-history Gitleaks scan.

## Known limitations

- The project is a reference control plane, not a production multi-tenant service.
- CLI transports prove the integration boundary but are not a substitute for hardened SDK/service clients.
- The standalone `make smoke` profile still requires a compatible Sandbox CRD and workflow worker. Separately, the v3.2 cross-repository Golden Slice does exercise real kind Pods plus Temporal for the binding/provenance contract; that proof is not equivalent to production Sandbox CRD coverage.
- Secret management, quota/budget enforcement, tenant isolation and HA deployment are intentionally incomplete.
- Provider prepare/execution contracts are still evolving; backward compatibility is not guaranteed before v1.
- The project does not own or redefine Temporal continuation semantics, Kubernetes sandbox semantics, MCP semantics or harness behavior.
- GitHub Actions has recently shown jobs failing before execution with no steps/logs; treat that as CI infrastructure state until runner execution is restored.

See [RELEASE_NOTES.md](RELEASE_NOTES.md) for the v0.1.0 candidate notes.
