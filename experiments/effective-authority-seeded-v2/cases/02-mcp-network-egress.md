# Case 02 — MCP + Cilium Egress

## Hypothesis

A tool that is already authorized to write an internal artifact can gain a new data-exfiltration outcome only when its application-level destination semantics are composed with a separately widened egress path.

Neither the MCP authorization diff nor the egress-policy diff needs to look individually dangerous.

## Production story

A coding/data Agent runs in a restricted Kubernetes namespace. It has an MCP tool for publishing generated artifacts to an approved company storage service.

The networking team later permits an additional corporate storage endpoint for migration/failover. Separately, the MCP service adds that endpoint as a valid storage backend.

The resulting path can move sensitive workspace material outside the original enclave.

## Baseline

### Domain 1 — MCP tool

```yaml
server: artifact-mcp
tools:
  publish_artifact:
    effect: external_write
    authz:
      role: build-agent
    input:
      source_path:
        prefix: /workspace/out/
      backend:
        enum: [primary]
backends:
  primary:
    url: https://artifact-store.prod.svc
    classification: internal-build-artifact
```

### Domain 2 — CiliumNetworkPolicy

The fixture explicitly assumes Cilium's FQDN-aware policy model. Native
Kubernetes NetworkPolicy cannot express `toFQDNs`.

```yaml
apiVersion: cilium.io/v2
kind: CiliumNetworkPolicy
spec:
  endpointSelector:
    matchLabels:
      app: build-agent
  egress:
    - toEndpoints:
        - matchLabels:
            k8s:io.kubernetes.pod.namespace: artifact-platform
      toPorts:
        - ports:
            - port: "443"
              protocol: TCP
```

The pod cannot reach migration storage.

## Delta

### Delta A — MCP backend catalog

```diff
 backends:
   primary:
     url: https://artifact-store.prod.svc
     classification: internal-build-artifact
+  migration:
+    url: https://storage.company.com
+    classification: corporate-storage

 publish_artifact:
   input:
     backend:
-      enum: [primary]
+      enum: [primary, migration]
```

Single-domain interpretation:

- same tool;
- same build-agent role;
- same source_path restriction;
- destination is another approved company storage service;
- no new tool permission or principal.

### Delta B — Cilium egress policy

```diff
 spec:
   egress:
     - toEndpoints:
         - matchLabels:
             k8s:io.kubernetes.pod.namespace: artifact-platform
       toPorts:
         - ports:
             - port: "443"
               protocol: TCP
+    - toFQDNs:
+        - matchName: storage.company.com
+      toPorts:
+        - ports:
+            - port: "443"
+              protocol: TCP
```

Single-domain interpretation:

- one approved corporate FQDN;
- HTTPS only;
- no 0.0.0.0/0;
- standard egress lint passes.

## Declared workspace data inventory

The fixture includes an explicit data inventory rather than relying on hidden
runtime knowledge:

```yaml
workspace_data:
  - path: /workspace/out/customer-debug.zip
    classification: customer-diagnostic
    contains: [customer-identifiers, request-logs]
```

The file is permitted by the tool's path constraint, but under baseline egress
it can only be written into the internal build-artifact store.

## Expected composed authority

```text
build-agent
   |
   v
publish_artifact(
  source=/workspace/out/customer-debug.zip,
  backend=migration
)
   |
   v
storage.company.com reachable over TCP/443
   |
   v
customer diagnostic bundle leaves original artifact enclave
```

### New reachable outcome

The Agent can persist customer diagnostic data into the migration storage domain, a target that was not reachable in the baseline.

### Effective principal

Unchanged:

```text
build-agent
```

This case tests outcome reachability without principal expansion.

## Method A expectation

Independent checks:

- MCP schema/auth review: PASS if corporate migration backend is approved;
- Cilium policy review: PASS if storage.company.com is an approved destination;
- ordinary diff: two bounded changes;
- generic OPA rules: no wildcard tool, path, role, CIDR, or port expansion.

### Expected A failure mode

The danger exists only if the reviewer joins:

```text
tool can select migration backend
  x
migration backend resolves to storage.company.com
  x
Cilium egress policy newly permits storage.company.com
  x
workspace source-path semantics include customer-debug.zip
```

If <=200 LOC glue can reliably derive this path from declarative backend metadata and Cilium policy, count the case as FOUND and treat that as evidence against the product hypothesis.

## Method B expected detection

```text
build-agent
  --invoke--> publish_artifact
  --read_source--> /workspace/out/*
  --backend=migration--> storage.company.com
  --network_reachable--> true
  --side_effect--> persist external artifact
```

Reachability diff:

```text
baseline:
publish_artifact -> migration backend = configured? no
network -> storage.company.com = no

candidate:
publish_artifact -> migration backend = yes
network -> storage.company.com = yes

new outcome:
persist /workspace/out/customer-debug.zip at migration storage
```

## Why this seed matters

This is deliberately not "new tool + internet egress". Those are too obvious.

The test is whether bounded application destination expansion plus bounded network expansion creates a new realizable side effect that single-domain admission controls fail to state.

## Invalid-seed conditions

Mark INVALID SEED if:

- the migration backend itself would already be classified as a prohibited data sink;
- Cilium/network policy tooling plus <=200 LOC service-to-FQDN glue recovers the end-to-end outcome easily;
- the risk depends on undocumented assumptions about what files exist under /workspace/out.

## Manual review worksheet

```text
A MCP result:
A Cilium egress result:
A glue result:
A final finding:

B realization path:
B new target:
B maximum data scope:

Is storage migration a realistic production change?
Did B require hand-maintained file semantics?
Verdict: MISS / FOUND / INVALID SEED
```
