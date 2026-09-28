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

## v0.3 — Kubernetes integration
- [ ] optional CRDs for desired state
- [ ] reconciliation loop
- [ ] placement adapter
- [ ] rollout / canary controller
- [ ] policy integration

## v0.4 — Production slice
- [ ] Alertmanager -> AgentRelease
- [ ] Temporal durable workflow
- [ ] MCP read tools
- [ ] isolated sandbox
- [ ] evidence
- [ ] eval gate
- [ ] replay
