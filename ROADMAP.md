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
- [x] manifest validation
- [x] binding resolver
- [x] multi-document manifest loader
- [x] resolved release plan compiler
- [x] release state machine
- [x] provider adapter interface / registry
- [x] Codex harness adapter example
- [x] Temporal workflow binding example
- [x] Kubernetes sandbox adapter example
- [x] Decision Gateway adapter example
- [x] MCP Tool adapter example
- [x] local dry-run reconciler
- [x] release-plan evidence
- [x] deterministic EvalGate engine

## v0.3 — Kubernetes integration
- [ ] optional CRDs for desired state
- [ ] reconciliation loop
- [ ] placement adapter
- [ ] rollout / canary controller
- [ ] policy integration

## v0.4 — Production slice
- [ ] Alertmanager -> AgentRelease
- [ ] real Temporal durable workflow execution
- [x] MCP read-tool binding contract
- [x] isolated sandbox binding contract
- [x] evidence contract + dry-run evidence
- [x] eval gate contract + local evaluator
- [ ] replay executor
- [ ] real provider executors / apply mode
