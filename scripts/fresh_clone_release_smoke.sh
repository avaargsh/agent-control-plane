#!/usr/bin/env sh
set -eu

ROOT="$(git rev-parse --show-toplevel)"
SOURCE_SHA="$(git -C "$ROOT" rev-parse HEAD)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT INT TERM

git clone --quiet --local "$ROOT" "$TMP/repo"
git -C "$TMP/repo" checkout --quiet --detach "$SOURCE_SHA"
cd "$TMP/repo"

python3 -m venv .venv
. .venv/bin/activate

python -m pip install --upgrade pip >/dev/null
python -m pip install -e '.[dev]' >/dev/null

python scripts/verify_v01_public_contract.py > "$TMP/public-contract.json"
python -m pytest -q
python examples/gpu_xid_golden_incident.py > "$TMP/demo.json"
python experiments/v3_2/mcp_execution_contract.py > "$TMP/mcp-proof.txt"

grep -qx 'PASS' "$TMP/mcp-proof.txt"
grep '^evidence_head=sha256:' "$TMP/mcp-proof.txt" >/dev/null

python -m pip wheel --no-deps --wheel-dir "$TMP/wheel" . >/dev/null
test "$(find "$TMP/wheel" -maxdepth 1 -name 'agent_control_plane-*.whl' | wc -l | tr -d ' ')" = "1"

echo "fresh_clone_source_commit=$SOURCE_SHA"
echo "fresh_clone_public_contract=PASS"
echo "fresh_clone_demo=PASS"
echo "fresh_clone_mcp_execution_contract=PASS"
echo "fresh_clone_wheel=PASS"
