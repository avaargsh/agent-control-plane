PYTHON ?= python3
ARTIFACTS ?= .artifacts

.PHONY: setup test demo preflight smoke verify-release audit-history clean

setup:
	$(PYTHON) -m pip install -e '.[dev]'

test:
	$(PYTHON) -m pytest -q

demo:
	mkdir -p $(ARTIFACTS)/demo
	$(PYTHON) examples/gpu_xid_golden_incident.py | tee $(ARTIFACTS)/demo/release-evidence-summary.json

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
