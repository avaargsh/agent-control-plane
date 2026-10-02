# Case 01 — MCP + OAuth Delegation

## Hypothesis

A model/runtime can gain a new real-world PII export capability even though:

- the MCP change alone remains role-gated; and
- the OAuth change alone appears to add only a short-lived, audience-restricted delegation.

The dangerous capability appears only after composing the delegated effective principal with the tool authorization rule.

## Production story

A support organization exposes a CRM MCP server. Human support users already have an export function. The Agent runtime normally operates as a low-privilege service principal.

A platform team separately enables RFC 8693-style token exchange so the runtime can act on behalf of a delegated support identity for selected CRM operations.

Neither team believes it is granting the Agent an unconstrained export capability.

## Baseline

### Domain 1 — MCP server

```yaml
server: crm-mcp
audience: crm-mcp
tools:
  get_customer:
    effect: read
    authz:
      any_role: [viewer, support]
  export_customer:
    effect: write_artifact
    side_effect: create_customer_export
    data_classification: pii
    authz:
      all:
        - claim: role
          equals: support
        - claim: human_present
          equals: true
    parameters:
      customer_id:
        type: string
```

Important baseline property:

```text
export_customer requires:
role=support AND human_present=true
```

The Agent service token has:

```yaml
sub: agent-runtime
role: viewer
human_present: false
aud: crm-mcp
```

### Domain 2 — OAuth/token broker

```yaml
token_exchange:
  subject: agent-runtime
  allowed_delegations: []
```

## Delta

Only two domains change.

### Delta A — MCP auth rule

A support migration removes the explicit interactive-human claim because new support automation is expected to run asynchronously:

```diff
 export_customer:
   authz:
     all:
       - claim: role
         equals: support
-      - claim: human_present
-        equals: true
```

Single-domain interpretation:

- export remains restricted to role=support;
- no viewer/service principal is directly authorized;
- MCP schema/policy lint passes.

### Delta B — OAuth delegation

The identity team enables short-lived delegation:

```diff
 token_exchange:
   subject: agent-runtime
-  allowed_delegations: []
+  allowed_delegations:
+    - delegated_subject: support-user
+      audience: crm-mcp
+      ttl_seconds: 300
+      claims:
+        role: support
```

Single-domain interpretation:

- delegation is narrow;
- audience is only crm-mcp;
- TTL is five minutes;
- no wildcard resource or administrator role is introduced;
- IAM/OAuth policy review can reasonably classify it as constrained delegation.

## Expected composed authority

```text
agent-runtime
   |
   | token exchange
   v
effective principal = support-user
claims: role=support
   |
   v
crm-mcp.export_customer
   |
   v
create PII export artifact
```

### New reachable outcome

The Agent can now create a customer PII export without a human support user being present.

### New effective principal

```text
nominal caller: agent-runtime
effective principal: delegated support-user
```

### Maximum effect

One export per tool invocation for any customer_id accepted by the CRM MCP server.

The seed intentionally does not add wildcard admin authority; the risk is representation plus PII side effect, not obvious privilege escalation.

## Method A expectation

Run independently:

- MCP Inspector/schema/policy lint;
- OAuth policy review / token exchange validation;
- ordinary git diff;
- <=200 LOC glue if desired.

### Expected A failure mode

MCP review sees a role-gated support-only export.

OAuth review sees short-lived audience-restricted delegation.

The miss occurs if the reviewer never computes:

```text
delegated token claims
  x
MCP role predicate
  x
tool side-effect semantics
```

A reviewer who explicitly joins those three facts counts as A finding the case. Do not force a MISS.

## Method B expected detection

Minimal authority edges:

```text
agent-runtime
  --delegate_as--> support-user

support-user
  --has_claim--> role=support

role=support
  --authorizes--> export_customer

export_customer
  --produces--> customer_pii_export
```

Reachability diff:

```text
baseline:
agent-runtime -/-> customer_pii_export

candidate:
agent-runtime -> support-user -> export_customer -> customer_pii_export
```

## Why this seed is valid only if

- ordinary OAuth tooling does not already report the end-to-end PII export outcome;
- ordinary MCP tooling does not know which principals the token broker can synthesize;
- the reviewer does not need a bespoke CRM table beyond the tool's declared side-effect metadata.

## Invalid-seed conditions

Mark INVALID SEED if:

- the delegation diff is considered obviously equivalent to granting support authority;
- a <=200 LOC join over exported OAuth claims and MCP predicates reliably identifies it with little semantic work;
- the only hard part is discovering the two config files.

## Manual review worksheet

```text
A MCP result:
A OAuth result:
A glue result:
A final finding:

B realization path:
B effective principal:
B outcome:

Would this occur in a real support automation rollout?
How much manual tool semantics were required?
Verdict: MISS / FOUND / INVALID SEED
```
