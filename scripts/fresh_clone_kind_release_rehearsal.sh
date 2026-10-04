#!/usr/bin/env sh
set -eu

ROOT="$(git rev-parse --show-toplevel)"
SOURCE_SHA="$(git -C "$ROOT" rev-parse HEAD)"
TMP="$(mktemp -d)"
OUTPUT="${REHEARSAL_OUTPUT_DIR:-$ROOT/.artifacts/v0.1-fresh-clone-kind}"
KUBE_CONTEXT_VALUE="${KUBE_CONTEXT:-kind-agent-transition}"

trap 'rm -rf "$TMP"' EXIT INT TERM

rm -rf "$OUTPUT"
mkdir -p "$OUTPUT"

git clone --quiet --local "$ROOT" "$TMP/repo"
git -C "$TMP/repo" checkout --quiet --detach "$SOURCE_SHA"
cd "$TMP/repo"

python3 -m venv .venv
. .venv/bin/activate

python -m pip install --upgrade pip >/dev/null
python -m pip install -e '.[dev]' >/dev/null

export KUBE_CONTEXT="$KUBE_CONTEXT_VALUE"

make kind-transition-smoke

python scripts/verify_execution_proof.py \
  .artifacts/kubernetes-transition/independent-execution-proof.json \
  --expected-hash-file \
  .artifacts/kubernetes-transition/independent-execution-proof.json.sha256

python scripts/verify_v01_acceptance_artifacts.py \
  .artifacts/kubernetes-transition \
  --evidence-out \
  .artifacts/kubernetes-transition/release-evidence.json

cp -R .artifacts/kubernetes-transition/. "$OUTPUT/"
printf '%s\n' "$SOURCE_SHA" > "$OUTPUT/source-commit.txt"

echo "fresh_clone_kind_transition=PASS"
echo "fresh_clone_independent_proof=PASS"
echo "fresh_clone_acceptance_artifacts=PASS"
echo "release_evidence=$OUTPUT/release-evidence.json"
