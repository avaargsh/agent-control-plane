PYTHON ?= python3
ARTIFACTS ?= .artifacts
CONTEXT_DB ?= $(ARTIFACTS)/cross-agent-context/live-handoff.db

.PHONY: setup quality-context test demo mcp-contract-proof context-live-handoff preflight smoke kind-transition-smoke verify-release audit-history clean

setup:
	$(PYTHON) -m pip install -e '.[dev]'

quality-context:
	$(PYTHON) -m ruff check \
		src/agent_control_plane/work_context.py \
		src/agent_control_plane/context_mcp.py \
		src/agent_control_plane/context_overlay.py \
		src/agent_control_plane/context_transition.py \
		src/agent_control_plane/execution_provenance.py \
		src/agent_control_plane/execution_verification.py \
		src/agent_control_plane/execution_journal.py \
		src/agent_control_plane/execution_attestation.py \
		src/agent_control_plane/kubernetes_deployment_transition.py \
		scripts/kubernetes_transition_smoke.py \
		scripts/live_cross_agent_context_handoff.py \
		tests/kubernetes_testkit.py \
		tests/context_testkit.py \
		tests/test_work_context.py \
		tests/test_context_mcp.py \
		tests/test_context_overlay.py \
		tests/test_context_transition.py \
		tests/test_context_transition_v2.py \
		tests/test_context_transition_v3.py \
		tests/test_context_host_configs.py \
		tests/test_kubernetes_deployment_transition.py \
		tests/test_kubernetes_context_bound_execution.py \
		tests/test_execution_journal.py \
		tests/test_execution_context_provenance.py \
		tests/test_execution_context_provenance_v2.py \
		tests/test_execution_context_provenance_v3.py \
		tests/test_execution_verification.py \
		tests/test_execution_attestation.py

test:
	$(PYTHON) -m pytest -q

demo:
	mkdir -p $(ARTIFACTS)/demo
	$(PYTHON) examples/gpu_xid_golden_incident.py | tee $(ARTIFACTS)/demo/release-evidence-summary.json

mcp-contract-proof:
	$(PYTHON) experiments/v3_2/mcp_execution_contract.py

context-live-handoff:
	AGENT_CONTEXT_LIVE_HANDOFF=1 $(PYTHON) scripts/live_cross_agent_context_handoff.py --db $(CONTEXT_DB) --cwd $(CURDIR)

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
