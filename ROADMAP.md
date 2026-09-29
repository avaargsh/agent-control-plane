# Roadmap

## v0.1 — Portable contracts
- [x] AgentBundle schema
- [x] AgentRelease schema
- [x] RuntimeBinding schema
- [x] Golden Slice
- [x] thin-control-plane ADR
- [x] canonical Session / Run / StateRef schema
- [x] ContextProjectionPolicy
- [x] EvidenceRef / EvalGate schema
- [x] Decision Plane binding type

## v0.2 — Local controller
- [x] manifest validation and binding resolver
- [x] resolved release plan compiler
- [x] release state machine
- [x] provider adapter / executor boundary
- [x] dependency graph, idempotency and compensation
- [x] execution receipts and release evidence
- [x] deterministic policy and EvalGate engine
- [x] Decision Gateway runtime client

## v0.3 — Real runtime bindings
The priority is to prove stable control-plane contracts against real runtimes before introducing CRDs.

- [x] OpenAI Agents HarnessBinding contract + executor seam
- [x] Temporal WorkflowBinding executor + SDK transport
- [x] Kubernetes Agent SandboxBinding executor + SDK transport
- [ ] stateless MCP ToolCapabilityBinding
- [ ] agentgateway Traffic/FabricBinding
- [ ] A2A AgentBinding
- [x] Decision Gateway Binding
- [x] provider capability discovery / conformance contract
- [ ] enforce conformance before provider mutation
- [ ] conformance tests for provider ownership boundaries

## v0.4 — Production golden slice
- [ ] Alertmanager -> AgentRelease ingress
- [ ] Temporal -> harness -> decision -> MCP -> sandbox execution
- [ ] canonical Run identity propagated to provider external refs
- [x] OTel trace/evidence correlation contract
- [ ] replay executor
- [ ] evidence-backed EvalGate
- [ ] promote / manual-review / rollback demonstration

## v0.5 — Kubernetes control plane
Only stable desired-state contracts become Kubernetes APIs.

- [ ] optional AgentRelease / AgentBinding CRDs
- [ ] reconciliation loop
- [ ] placement adapter
- [ ] rollout / canary controller
- [ ] policy integration
- [ ] dependency-aware Kubernetes apply

## v0.6 — Multi-agent federation
- [ ] execution / continuation / context ownership model
- [ ] A2A interoperability
- [ ] agentgateway routing and policy
- [ ] cross-agent canonical identity
- [ ] distributed evidence correlation

## Explicit non-goals
- building another agent framework
- implementing a durable workflow engine
- implementing a sandbox runtime
- owning MCP or A2A wire protocols
- storing high-frequency runtime state in CRDs
