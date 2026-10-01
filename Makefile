PYTHON ?= python3
ARTIFACTS ?= .artifacts

.PHONY: setup test demo mcp-contract-proof context-live-handoff preflight smoke kind-transition-smoke verify-release audit-history clean

setup:
	$(PYTHON) -m pip install -e '.[dev]'

test:
	$(PYTHON) -m pytest -q

demo:
	mkdir -p $(ARTIFACTS)/demo
	$(PYTHON) examples/gpu_xid_golden_incident.py | tee $(ARTIFACTS)/demo/release-evidence-summary.json

mcp-contract-proof:
	$(PYTHON) experiments/v3_2/mcp_execution_contract.py

context-live-handoff:
	AGENT_CONTEXT_LIVE_HANDOFF=1 $(PYTHON) scripts/live_cross_agent_context_handoff.py

preflight:
	$(PYTHON) scripts/live_smoke_preflight.py

smoke: preflight
	@echo "Live prerequisites are reachable."
	@echo "Run the opt-in integration profile with: AGENT_STACK_LIVE_SMOKE=1 $(PYTHON) -m pytest -q tests/integration"

kind-transition-smoke:
	mkdir -p $(ARTIFACTS)/kubernetes-transition
	KUBE_CONTEXT=${KUBE_CONTEXT:-kind-agent-transition} $(PYTHON) scripts/kubernetes_transition_smoke.py | tee $(ARTIFACTS)/kubernetes-transition/summary.json

verify-release:
	$(PYTHON) scripts/verify_release.py

audit-history:
	sh scripts/audit_git_history.sh

clean:
	rm -rf $(ARTIFACTS)
