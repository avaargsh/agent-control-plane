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
- [x] v0.1 Acceptance Artifact Contract frozen and machine-verifiable
- [x] v0.1 public API/schema/CLI compatibility snapshot executable
- [x] v0.1 execution architecture freeze contract recorded
- [x] known limitations documented
- [x] repository license selected and added (Apache-2.0)
- [x] CONTRIBUTING.md added
- [x] public README internal links checked against repository tree
- [x] current-tree secret/token pattern scan clean
- [x] current-tree private/customer identifier spot-check clean
- [x] full git-history secret scan complete (Gitleaks v8.30.1, full checkout)
- [x] direct dependency/license review complete
- [x] live kind Deployment 20 -> 30 acceptance path exercised from a clean clone
- [x] GitHub Actions runner issue documented
- [x] fresh-clone package/demo/MCP/wheel verification complete
- [x] fresh-clone Kubernetes acceptance artifact rehearsal complete
- [x] v0.1.0 release notes prepared
- [x] stale pre-RC pull requests reconciled; no open P0 correctness issue is present
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
6. Run `make fresh-clone-kind-release-rehearsal` against a clean kind cluster.
7. Verify the complete artifact set against
   `docs/V0.1_ACCEPTANCE_ARTIFACT_CONTRACT.md`.
8. Run the executable v0.1 release gate, including isolated wheel installation,
   public compatibility checks, fresh-clone package smoke, and full-history scan.
9. Confirm README claims are limited to behavior actually exercised.

### Audit performed

Current default-branch code search returned no matches for representative credential patterns:
`BEGIN PRIVATE KEY`, `AKIA`, `ghp_`, `sk-`, `api_key`, `Bearer`, `ssh-rsa`, `password`.

Spot checks also returned no matches for generic customer markers or known prior-employer naming. Repository-tree review confirmed README-linked `DEVELOPMENT.md` and `RELEASE_READINESS.md` exist.

Recent commit metadata was reviewed for suspicious credential/private-data wording with no obvious finding. The v0.1 release gate now performs `make audit-history` against a `fetch-depth: 0` checkout using pinned Gitleaks v8.30.1; the scanner completed successfully on the repository history.

The final RC audit reconciled stale pre-RC pull requests after preserving the release-acceptance contract and Golden Slice trajectory replay proof on main.

The executable v0.1 package release gate verifies the public compatibility
snapshot, full test suite, deterministic Golden Incident, MCP Execution Contract
proof, release wheel build, isolated wheel installation with declared
dependencies, packaged schemas/CLI behavior, a clean local fresh-clone smoke,
and a full-history Gitleaks scan.

The Kubernetes release gate separately creates a kind cluster and runs the
Deployment `20 -> 30` Golden Slice from a clean clone. It validates the full
Acceptance Artifact Contract, verifies the serialized IndependentExecutionProof
in a new process, and records the source commit plus required artifact file
digests in `release-evidence.json`.

## Known limitations

- The project is a reference control plane, not a production multi-tenant service.
- CLI transports prove the integration boundary but are not a substitute for hardened SDK/service clients.
- The standalone `make smoke` profile still requires a compatible Sandbox CRD and workflow worker. Separately, the v3.2 cross-repository Golden Slice does exercise real kind Pods plus Temporal for the binding/provenance contract; that proof is not equivalent to production Sandbox CRD coverage.
- Secret management, quota/budget enforcement, tenant isolation and HA deployment are intentionally incomplete.
- Internal provider adapters and coordinator implementation may still evolve before v1. The v0.1 compatibility promise is limited to the explicit public surface recorded in `release/v0.1-public-contract.json`; breaking that snapshot requires an explicit versioned contract change.
- The project does not own or redefine Temporal continuation semantics, Kubernetes sandbox semantics, MCP semantics or harness behavior.
- `ExecutionAttestation/v2` remains for compatibility but is not sufficient by itself for v0.1 release acceptance; the normative completed-execution artifact is `IndependentExecutionProof/v1`.
- The separate hash file is a trusted-digest input, not a signer-authenticity mechanism. Production authenticity remains external (for example DSSE/Sigstore/KMS).

See [RELEASE_NOTES.md](RELEASE_NOTES.md) for the v0.1.0 candidate notes.
