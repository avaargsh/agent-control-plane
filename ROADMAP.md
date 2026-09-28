# Roadmap

## v0.1 — Portable contracts
- [x] AgentBundle schema
- [x] AgentRelease schema
- [x] RuntimeBinding schema
- [x] Golden Slice
- [x] thin-control-plane ADR
- [ ] canonical Session / Run / StateRef schema
- [ ] ContextProjectionPolicy
- [ ] EvidenceRef / EvalGate schema

## v0.2 — Local controller
- [ ] manifest loader and validation
- [ ] binding resolver
- [ ] release state machine
- [ ] provider adapter interface
- [ ] local OpenAI/Codex harness adapter example
- [ ] Temporal workflow binding example

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
