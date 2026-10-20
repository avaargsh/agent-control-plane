# M6 offline acceptance manifest validator

This tool is **not** a proof verifier, a signer, or a replacement for an independent human operator. It checks that the required live-delivery evidence *claims* are complete, correctly typed and non-contradictory. A forged all-PASS manifest can still pass structural checks. The output `ELIGIBLE_FOR_INDEPENDENT_REVIEW` is intentionally **not GO**, and never authorizes deployment.

The live-delivery operator owns evidence acquisition and must preserve independent original sources and reviewer attestation outside this repository. Do not commit private evidence, HMAC secrets, access tokens or unredacted event bodies.

Usage:

```bash
PYTHONPATH=examples python examples/m6_acceptance_validator.py /path/to/redacted-m6-evidence.json
pytest -q tests/test_m6_acceptance_validator.py
```

A report must contain `schema=m6-live-acceptance/v1`, a 40-character Git commit, `environment=isolated-disposable`, `github_delivery_kind=real-github`, `operator_decision=GO`, `reviewer_decision=GO`, `business_outcome_verified=false`, `independent_proof_ref=null`, and the **exact** set of gate keys declared by `GATES` in the validator. Each gate contains `outcome=PASS|FAIL|NOT_RUN|BLOCKED`, a lowercase 64-character evidence SHA-256 and a non-secret summary.

Any missing/extra gate, fixture delivery, absent digest, failed/unknown gate, or missing reviewer returns process exit code 1 and `NO_GO`. Even an all-PASS manifest yields only `ELIGIBLE_FOR_INDEPENDENT_REVIEW`, requiring independent source verification and a separate signed operator release decision.

See `docs/M6_LIVE_DELIVERY_ACCEPTANCE.md`, ACP #185, #191 and #192. This PR does not run a live webhook, change network configuration or create a new Task ledger.


## Materialized, local-file integrity gate

The original `m6_acceptance_validator.py` is intentionally shape-only:
anyone can write a syntactically valid, fabricated all-PASS manifest.
To detect accidental substitution, missing artifacts, and hash mismatch,
a separate opt-in **read-only** command now verifies the actual files:

```bash
PYTHONPATH=examples python examples/m6_materialized_evidence.py \
  /secure/m6/manifest.json --root /secure/m6/evidence
```

For each of the existing 16 `gates`, add an `evidence_path` relative
to `--root` while preserving its existing SHA-256. Use distinct, bounded,
nonempty evidence files; the materialized validator checks their actual
bytes against the manifest hash and rejects absolute paths, `..`,
backslash paths, symbolic links, duplicate paths and oversized files.
It does not print raw evidence, secrets, network URLs or file paths.

For `real_github_pull_request_delivery` and
`real_github_check_delivery`, the referenced files **must be the
redacted JSON reports** returned by the read-only official GitHub
delivery auditor. This tool also applies the existing exact scoped
PR/check pair-correlation gate to those two files, including separate
delivery identity and same Workflow/scope digests. The CLI does **not**
fetch a new GitHub API response, recreate HMAC raw bytes or call Temporal.

Possible positive output is `FILES_MATCHED_NOT_RELEASED` (exit 0)
with `release_decision=NO_GO`. Missing/bad/unrelated files produce
`NO_GO` (exit 1). A fabricated manifest plus fabricated artifacts
could still satisfy all structural and hash checks. **A file digest
only proves integrity relative to the supplied manifest; it does not
authenticate who produced that file or whether GitHub really delivered
the event.** Independent operator review, source verification,
original raw-byte receiver HMAC evidence, approved rollout and a signed
GO/NO-GO decision remain external release prerequisites.

Run the regression suite with:

```bash
pytest -q tests/test_m6_acceptance_validator.py \
  tests/test_m6_paired_delivery_evidence.py \
  tests/test_m6_materialized_evidence.py
```

Never commit secrets, unredacted webhook bodies, or private raw evidence
into this repository.


## Evidence reader hardening

The materialized validator rejects a symbolic-link evidence root as well as
symbolic links in evidence paths and a symbolic-link manifest. The standalone
paired-delivery CLI also rejects symbolic-link report files, empty files, and
reports larger than 4 MiB before JSON parsing. These checks bound accidental
or hostile local evidence inputs; they do not authenticate the author or prove
that a report was produced from a real GitHub delivery. The final decision
remains `NO_GO` until independently verified live evidence and signed review.
