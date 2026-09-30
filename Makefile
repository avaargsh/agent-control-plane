PYTHON ?= python3
ARTIFACTS ?= .artifacts

.PHONY: setup test demo mcp-contract-proof preflight smoke verify-release audit-history clean

setup:
	$(PYTHON) -m pip install -e '.[dev]'

test:
	$(PYTHON) -m pytest -q

demo:
	mkdir -p $(ARTIFACTS)/demo
	$(PYTHON) examples/gpu_xid_golden_incident.py | tee $(ARTIFACTS)/demo/release-evidence-summary.json

mcp-contract-proof:
	$(PYTHON) experiments/v3_2/mcp_execution_contract.py

preflight:
	$(PYTHON) scripts/live_smoke_preflight.py

smoke: preflight
	@echo "Live prerequisites are reachable."
	@echo "Run the opt-in integration profile with: AGENT_STACK_LIVE_SMOKE=1 $(PYTHON) -m pytest -q tests/integration"

verify-release:
	$(PYTHON) scripts/verify_release.py

audit-history:
	sh scripts/audit_git_history.sh

clean:
	rm -rf $(ARTIFACTS)
