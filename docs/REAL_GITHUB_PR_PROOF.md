# Controlled Real GitHub Pull-Request Proof

Status: **transport implemented; live mutation evidence still required**.

This slice moves the existing provider-neutral GitHub pull-request transition from
an in-memory API fixture to a real, authenticated `gh api` transport without
changing the frozen v0.1 execution semantics.

## Boundary

`GitHubCliPullRequestApi` implements the existing `GitHubPullRequestApi`
protocol only. It does not add a new authority model, workflow engine, policy
language, or proof shape.

The provider still owns the same chain:

```text
fresh PR observation
  -> exact approved head SHA
  -> TransitionPlan
  -> execution identity / ownership marker
  -> GitHub merge side effect
  -> fresh PR + merge-commit observation
  -> ownership-aware reconciliation / verification
```

A successful GitHub API response is not independent proof. A PR that happens to
be merged is also not proof that this operation owned the merge.

## Transport

The transport reuses GitHub CLI authentication:

```bash
gh auth status
```

Reads use `gh api /repos/<owner>/<repo>/pulls/<number>` and
`/repos/<owner>/<repo>/commits/<sha>`.

The mutation uses GitHub's pull-request merge endpoint with all three values
bound in one request:

- the exact approved head SHA;
- the approved merge method;
- the control-plane ownership commit message.

Transport failures that can represent a lost acknowledgement are classified as
`RuntimeMutationUncertain`; the existing provider then re-observes GitHub and
requires the merge commit to prove both operation identity and plan hash.

## Controlled live-proof gate

Do not use an important pull request for the first live proof.

Use a disposable repository or disposable PR where the account running `gh`
is allowed to merge. Record at least:

1. repository and PR number;
2. approved head SHA before execution;
3. canonical TransitionPlan hash;
4. operation ID;
5. merge API outcome;
6. fresh PR observation after the call;
7. merge commit SHA and ownership markers;
8. reconciliation status;
9. VerificationReport;
10. IndependentExecutionProof statement hash.

Required negative controls:

- push a new commit after approval and prove execution is rejected before merge;
- simulate an uncertain mutation acknowledgement and prove fresh observation is
  required;
- merge with another actor/without the ownership markers and prove the control
  plane returns UNKNOWN rather than claiming success.

Until one controlled run produces those artifacts, this transport is
**implementation evidence only**, not a production provider acceptance claim.
