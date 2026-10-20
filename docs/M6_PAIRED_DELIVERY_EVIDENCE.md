# M6 #185 — Pair the real PR and check delivery evidence

This is an **offline, read-only correlation gate** after an authorized
operator has independently captured two JSON reports with the existing
`pr_watch_github_delivery_evidence.py` CLI. It does not register a GitHub
webhook, connect to staging, claim that a fixture is a live delivery, or
authorize release.

## Operator-owned inputs

1. A real `pull_request` delivery GUID, numeric GitHub delivery ID and
   repository Hook ID, captured from the disposable repository's GitHub
   Webhooks → Recent Deliveries page. Run the existing read-only auditor
   against that exact GUID, scoped Goal and reviewed HTTPS endpoint, with
   Temporal History enabled. Save the redacted JSON as `pr.json`.
2. A **different** real `check_run.completed` or
   `check_suite.completed` delivery, explicitly associated with the same
   Goal's PR. Audit it independently; save as `check.json`.
   Fork checks with no explicit PR association are NOT a substitute.

Then run:

```bash
PYTHONPATH=examples python examples/m6_paired_delivery_evidence.py \
  /secure/evidence/pr.json /secure/evidence/check.json
```

The correlation checks that each report claims the expected official
GitHub Hook, delivery, exact PR association, current TLS verification,
durable ACK, and **exactly one** Temporal History wake. Both must share
the same scope and Workflow digest but have different delivery digests.
The tool does not trust an unverified provider result or infer that a
check event caused a specific GitHub state transition.

A complete pair yields
`correlation=PAIR_CORROBORATED_NOT_RELEASED` with process exit 0.
Every missing/mismatched/unsafe item yields
`correlation=NOT_CORROBORATED` and process exit 1.
**Both cases have `release_decision=NO_GO`,
`business_outcome_verified=false` and
`independent_proof_ref=null`.** Exit 0 is only a correlation result.

### What this cannot prove

The input JSON is not cryptographically authenticated. A fabricated pair
could satisfy these shape checks. The GitHub API returns parsed payload
JSON, not the original HMAC-signed HTTP bytes; current Hook settings
cannot establish historical secret provisioning. Exact Temporal History
cardinality does not establish business outcome attribution. Operators
must independently preserve source artifacts, raw receiver HMAC
verification, scoped task and Activity observations, worker restart and
rollback evidence, and separate signed GO/NO-GO review.

The 16-gate M6 manifest validator remains a separate operator review
step. This paired audit **cannot automatically mark any manifest gate
PASS**, and neither audit is an in-toto/DSSE signer proof. Keep #185 and
#192 open until an actual independently reviewed staging run completes.

Unit tests use deliberately constructed fixtures, not external GitHub
webhook delivery:

```bash
pytest -q tests/test_m6_paired_delivery_evidence.py
```
