# Effective Authority Diff — Seeded Cases v2

This experiment exists to answer one falsifiable product question:

> Do realistic production configuration changes create a materially new reachable outcome even when ordinary single-domain policy/config tooling does not reliably identify the composed capability?

This is not a product implementation. Do not add a graph engine, generic policy runtime, provider abstraction, or UI here.

## Compare two review methods

### A — ordinary review baseline

Use only normal engineering tools:

- git diff / GitHub Files changed
- Conftest / OPA
- IAM Simulator / IAM Access Analyzer concepts
- Cedar analyze/diff where applicable
- MCP Inspector / schema review
- Kubernetes / NetworkPolicy inspection
- at most 200 LOC of one-off glue

A finding only counts when it states the effective outcome, not merely that a config changed.

### B — composed authority reasoning

Manually model the minimum cross-domain path:

```text
principal
  -> credential / delegation
  -> runtime binding
  -> tool / operation
  -> reachable target
  -> side effect / outcome
```

Use the current control-plane concepts only as reference vocabulary:

- AuthorityHead.generation: authority freshness
- ExecutionLease.epoch: provider execution ownership
- AuthorityReservation: frozen authority during a concrete side-effect
- effective principal: who the action is actually performed on behalf of

Do not implement a reusable authority graph yet.

## Hard decision rule

For every seeded case record:

1. baseline config;
2. delta, limited to at most three domains;
3. result of each ordinary single-domain check;
4. new reachable outcome, if any;
5. whether method A found it;
6. whether method B found it;
7. why the miss is compositional rather than a parser/config-collection problem;
8. whether the scenario is realistic enough to occur in production.

### Kill criteria

Kill Effective Authority Diff if any of the following becomes true:

- A + <=200 LOC glue finds >=80% of dangerous seeded cases reliably;
- a normal platform/security engineer can infer the dangerous outcome directly from ordinary diffs without reconstructing cross-domain authority;
- B only works after users manually maintain large amounts of tool/resource/credential semantics;
- the remaining misses are mostly hard-coded joins, inventory quality, or product-specific adapters.

Keep the hypothesis alive only if B repeatedly finds a realistic class of high-risk authority composition that A systematically misses.

## Current v2 seed set

- Case 01 — MCP + OAuth delegation
- Case 02 — MCP + NetworkPolicy
- Case 03 — IAM + workload identity / inherited credential

Cases 04–10 should not be added until the first three have been manually reviewed. The purpose is to test the review method before spending time manufacturing a larger fixture set.

## Reviewer result format

```text
CASE:
A result:
A tools used:
A effective-capability finding:
B result:
B realization path:
new effective principal:
new reachable outcome:
maximum effect:
approval requirement:
why A missed / did not miss:
manual semantics required:
realism:
verdict: MISS / FOUND / INVALID SEED
```

Do not score or tune the cases while the blind reviewer is working.
