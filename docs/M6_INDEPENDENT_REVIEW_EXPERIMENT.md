# M6 independent-review value experiment

**Status: experiment protocol only. No production evidence is implied by this document.**

## Decision to make

Does the offline M6 evidence bundle help a reviewer reach a defensible conclusion faster or more reliably than the existing GitHub PR and Checks workflow, enough to justify the extra preparation and trust setup?

This is a product validation experiment, not a release gate and not authorization to deploy.

## Participants and materials

Use one operator who prepares the same change for both review paths and one reviewer who did not implement the verifier. Prefer a second reviewer for replication.

Prepare a disposable, non-production evidence bundle. Clearly label synthetic evidence as synthetic; do not relabel fixtures as live observations. For a real-world trial, use actual evidence that can be safely shared and independently traced to its source.

The reviewer receives:
- **Path A — baseline:** the ordinary PR, GitHub Checks, workflow logs and artifacts normally available to that reviewer.
- **Path B — offline bundle:** the manifest, materialized evidence files, detached reviewer statement, pinned reviewer public key/profile delivered through a separate trusted channel, and expected deployed commit obtained independently.

Keep the underlying change and acceptance criteria identical. Randomize which path the reviewer sees first when practical. Do not let Path B inherit conclusions from Path A.

## Task

Ask the reviewer to answer the same questions for both paths:

1. Which source/deployed commit does this evidence refer to?
2. Which acceptance gates are supported, failed, blocked, or not run?
3. Is any required evidence missing, modified, unsafe, stale, or associated with the wrong commit?
4. What does the signature authenticate, and what does it not establish?
5. Is there enough evidence to recommend a next step? State the remaining uncertainty.

The verifier's `SIGNED_GO_VERIFIED_NOT_RELEASED` result is **not** a release approval. Every verifier result must retain `release_decision=NO_GO` until the separate live runbook and authorized operator process are completed.

## Record these measures

For each path, record:
- elapsed time from access to a written conclusion;
- number of manual steps and extra files/permissions needed;
- correct identification of the commit and gate status;
- whether the reviewer distinguishes byte integrity, reviewer identity, observed reality, and release authorization;
- whether a seeded tamper/missing-file/wrong-commit case is detected;
- questions or escalations required before the reviewer trusts the conclusion.

Do not use time alone as the success metric. A faster but incorrect or overconfident conclusion is a failure.

## Negative cases

Run these only on disposable copies:
- change one evidence byte after manifest creation/signing;
- change the manifest after signing;
- remove a required evidence file;
- use a different reviewer key or reviewer identity;
- provide an expected deployed commit different from the signed commit;
- expire the signed statement;
- supply a path traversal, symlink, or oversized file.

For each case, expected behavior is fail-closed verification and no release approval. Record the exact observed result; do not infer unexecuted cases passed.

## Decision rules

**Keep the offline CLI as a narrow repo capability** if reviewers detect tampering or commit mismatch more reliably, or reach a well-supported conclusion materially faster without disproportionate setup.

**Improve documentation and stop product expansion** if reviewers misunderstand what a signature proves, need help configuring trust inputs, or cannot distinguish verified review from deployment approval.

**Stop standalone productization** if the normal PR/Checks path reaches the same quality of conclusion with comparable effort and no meaningful gap in access to original evidence.

**Do not claim validation** until at least one independent reviewer has completed both paths and the results, setup cost, and limitations are recorded. A synthetic unit test is not a user experiment; a passing verifier is not proof of production behavior.

## Result record

- Date / commit under review:
- Reviewer role (avoid personal identifiers):
- Evidence type: synthetic / real sanitized:
- Path A elapsed time and result:
- Path B elapsed time and result:
- Negative cases actually executed:
- Misunderstandings / escalations:
- Decision: keep narrow CLI / improve / stop:
- Evidence location and reviewer:
