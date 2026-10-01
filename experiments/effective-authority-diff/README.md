# Deploy-time Effective Authority Diff — Falsification Fixture

This directory exists to falsify one narrow hypothesis before any product implementation:

> A deployment can gain a new realizable real-world outcome, side effect, or effective principal even when no individual policy domain shows an obviously dangerous change.

This is **not** an Agent Governance Platform, runtime authorizer, identity system, MCP gateway, policy engine, registry, runtime, observability stack, or product prototype.

## Experiment shape

- baseline branch: `exp/effective-authority-v17`
- blind candidate branches: `blind/effective-authority-01` … `08`
- each candidate is reviewed independently against the baseline
- candidates are intentionally numbered so their semantic class is not disclosed
- ground truth is intentionally not committed before the blind review
- closed/superseded experiment PRs are setup history and must not be used during review

## Allowed reviewer tools

Use ordinary engineering tools only:

- git diff
- terraform plan / helm diff where applicable
- Conftest / OPA
- AWS IAM Policy Simulator / IAM Access Analyzer concepts
- kubectl / manifest inspection
- at most 200 LOC of glue

## Required output

A finding counts only when it states the semantic outcome, not merely a config or allow-rule delta:

```text
NEW EFFECTIVE CAPABILITY
actor:
outcome:
target:
effective principal:
credential:
maximum effect:
approval:
realization path:
risk:
```

## Kill criteria

Kill the direction if existing tools plus <=200 LOC glue reliably recover >=80% of seeded dangerous changes, if an engineer can stably infer the answer from ordinary diffs, or if the hard part is only config collection / table joins / hard-coded Stripe mappings.

Keep the hypothesis alive only when misses are caused by cross-domain composition, effective-principal composition, authority-separation changes, or downstream side-effect semantics.

The experiment is the deliverable. No authority-diff engine is implemented here.
