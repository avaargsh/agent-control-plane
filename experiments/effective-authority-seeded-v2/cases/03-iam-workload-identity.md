# Case 03 — IAM + Workload Identity / Inherited Credential

## Hypothesis

A sub-agent can gain the parent's cloud-side effect authority even when:

- the IAM policy itself becomes narrower or remains unchanged; and
- the workload manifest change looks like ordinary credential plumbing.

The dangerous delta is loss of authority separation, not a larger IAM statement.

## Production story

A parent coding Agent can publish release artifacts to a production S3 prefix. A spawned verifier sub-agent is intentionally isolated and should only read test inputs.

To simplify credential management, the runtime team changes the sub-agent pod to reuse the parent's projected workload identity token.

At the same time, the IAM team narrows the parent role from a bucket wildcard to a release prefix. IAM review therefore appears to show reduced authority.

## Baseline

### Domain 1 — IAM

Parent role:

```json
{
  "Role": "release-agent",
  "Allow": [
    {
      "Action": ["s3:GetObject", "s3:PutObject"],
      "Resource": "arn:aws:s3:::company-release/*"
    }
  ]
}
```

Verifier role:

```json
{
  "Role": "verifier-agent",
  "Allow": [
    {
      "Action": ["s3:GetObject"],
      "Resource": "arn:aws:s3:::company-test-fixtures/*"
    }
  ]
}
```

### Domain 2 — workload identity

```yaml
parent:
  serviceAccountName: release-agent
  projectedCredential:
    role: release-agent

subagent:
  serviceAccountName: verifier-agent
  credentialMode: isolated
  projectedCredential:
    role: verifier-agent
```

Authority separation:

```text
parent -> release-agent -> production release write
subagent -> verifier-agent -> test fixture read only
```

## Delta

### Delta A — IAM policy narrowing

```diff
 Role: release-agent
 Allow:
   - Action: [s3:GetObject, s3:PutObject]
-    Resource: arn:aws:s3:::company-release/*
+    Resource: arn:aws:s3:::company-release/prod/*
```

Single-domain interpretation:

- authority becomes narrower;
- Access Analyzer / IAM diff should not classify this as expansion.

### Delta B — runtime workload identity

A performance change makes child sandboxes inherit the parent credential projection:

```diff
 subagent:
   serviceAccountName: verifier-agent
-  credentialMode: isolated
-  projectedCredential:
-    role: verifier-agent
+  credentialMode: inherit-parent
+  projectedCredential:
+    source: parent
```

The manifest still names verifier-agent as the Kubernetes service account; only the cloud credential source changes.

Single-domain interpretation:

- runtime credential plumbing changed;
- no new IAM allow statement;
- no new Kubernetes RBAC permission;
- generic manifest lint can pass.

## Expected composed authority

```text
parent Agent
  --spawn--> verifier sub-agent
                 |
                 | inherit parent projected credential
                 v
          AWS role = release-agent
                 |
                 v
     s3:PutObject company-release/prod/*
```

### New reachable outcome

The verifier sub-agent can now overwrite or create production release artifacts.

### Effective principal change

```text
nominal sub-agent identity:
  Kubernetes SA = verifier-agent

effective cloud principal:
  release-agent
```

The important delta is not the IAM policy size. It is the identity actually presented at the cloud provider.

## Method A expectation

Independent checks:

### IAM

Expected result:

```text
NO EXPANSION
resource scope narrowed:
company-release/* -> company-release/prod/*
```

### Kubernetes/RBAC

Expected result:

```text
NO RBAC EXPANSION
serviceAccountName remains verifier-agent
```

### Runtime manifest

A config-aware reviewer may notice inherit-parent, but generic policy tooling normally cannot state its downstream S3 outcome without understanding the credential projection contract.

### Expected A failure mode

The miss occurs if A does not resolve:

```text
subagent credentialMode=inherit-parent
  -> parent projected AWS credential
  -> role release-agent
  -> s3:PutObject
  -> production release prefix
```

If a small static rule can map inherit-parent to the parent role and IAM Simulator then recovers the outcome, count FOUND. That result is evidence that the problem may be <=200 LOC glue.

## Method B expected detection

Authority graph:

```text
verifier-subagent
  --inherits_credential_from--> parent-agent

parent-agent
  --projects--> aws:role/release-agent

aws:role/release-agent
  --allows--> s3:PutObject
  --resource--> company-release/prod/*
```

Reachability diff:

```text
baseline:
verifier-subagent -/-> production release write

candidate:
verifier-subagent
  -> parent credential
  -> release-agent
  -> s3:PutObject
  -> production release write
```

## Why this seed is important

It tests whether Effective Authority Diff is about:

```text
policy expansion
```

or the more difficult:

```text
who actually holds which credential at execution time
```

A system that only diffs IAM statements should miss this class even though the final side effect is ordinary S3 write authority.

## Invalid-seed conditions

Mark INVALID SEED if:

- the runtime platform already emits an explicit effective-cloud-principal diff for child sandboxes;
- mapping inherit-parent to the parent projected role is trivial and generic across the target environment;
- the scenario requires hidden runtime behavior not represented in configuration or contract metadata.

## Manual review worksheet

```text
A IAM result:
A K8s/RBAC result:
A runtime config result:
A <=200 LOC glue result:
A final finding:

B realization path:
B nominal principal:
B effective cloud principal:
B new side effect:

Was the IAM diff itself an expansion?
How much runtime-specific semantics did B require?
Verdict: MISS / FOUND / INVALID SEED
```
