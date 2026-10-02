# Seeded Authority-Change Review Runbook

Run Case 01–03 before creating Case 04–10.

The purpose is not to make Method B win. The purpose is to learn whether there
is a stable class of composition that Method A misses.

## Reviewer separation

Prefer two passes:

- Pass A reviewer receives baseline + delta + ordinary tool access.
- Pass B reviewer reconstructs the composed authority path only after A is
  frozen.

If one person performs both passes, write and freeze the A result before
starting B.

Do not edit a case after seeing its review result. Mark a weak case
`INVALID SEED` instead.

## Pass A

Timebox: 60–90 minutes per case.

Allowed:

- git diff / Files changed
- OPA / Conftest
- IAM simulator / Access Analyzer reasoning
- Cedar diff/analyze when applicable
- MCP Inspector / JSON Schema inspection
- Cilium/Kubernetes manifest inspection
- <=200 LOC one-off glue

Required output:

```text
A_FINDING = FOUND | NO_FINDING
effective principal:
outcome:
target:
credential:
maximum effect:
approval:
realization path:
tools used:
glue LOC:
confidence:
```

A finding counts only if it names the new end-to-end outcome. Statements such
as "role changed", "egress widened", "inherit-parent added", or "new backend"
do not count by themselves.

## Freeze A

Before starting B:

1. commit the A worksheet or record its hash/timestamp;
2. record glue LOC;
3. record whether the reviewer needed undocumented product knowledge;
4. do not retroactively upgrade A after seeing the composed answer.

## Pass B

Build only the minimum graph necessary for the case.

Suggested edge vocabulary:

```text
delegates_as
inherits_credential_from
projects_credential
authorizes
invokes
reads
writes
reaches
acts_on_behalf_of
produces
```

Required output:

```text
baseline reachable path:
candidate reachable path:
new effective principal:
new reachable outcome:
maximum effect:
manual semantics supplied:
```

Do not build reusable code unless the same edge extraction repeats across at
least three validated cases.

## Case classification

### MISS

Use only when:

- A did not identify the end-to-end dangerous outcome;
- B did;
- all necessary semantics were represented in configs/contracts/inventory;
- the scenario is realistic.

### FOUND

Use when A identifies the same material outcome, including with <=200 LOC glue.

FOUND is evidence against the Effective Authority Diff product hypothesis.

### INVALID SEED

Use when:

- the delta is obviously dangerous by itself;
- the case depends on hidden/manual facts;
- the scenario is contrived;
- B requires bespoke semantic knowledge that a product could not reasonably
  obtain.

Do not count INVALID SEED in the numerator or denominator.

## Decision after first three

Do not expand to 10 automatically.

After Case 01–03:

- if all three are FOUND or INVALID SEED, stop and redesign/kill before adding
  more fixtures;
- if at least one is a clean MISS, add cases that test the same *class* plus
  one adversarial near-miss;
- if A's only miss is solved by a tiny generic join, implement the <=200 LOC
  glue and rerun before claiming a product gap.

## Final kill calculation

After enough valid seeds:

```text
A detection rate =
FOUND dangerous cases / all valid dangerous cases
```

If A detection rate >= 80%, kill the direction.

Also kill if B depends on large manually maintained semantic maps even when its
detection rate is high.
