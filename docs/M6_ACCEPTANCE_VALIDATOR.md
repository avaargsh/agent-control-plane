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
