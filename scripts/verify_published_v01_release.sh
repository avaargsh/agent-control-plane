#!/usr/bin/env bash
set -euo pipefail

TAG="${ACP_RELEASE_TAG:-v0.1.0}"
EXPECTED_SOURCE_COMMIT="${ACP_RELEASE_SOURCE_COMMIT:-62da9f33035e29ec5d29c224a7d676a687141ac5}"
K8S_ASSET="v01-kubernetes-acceptance-proof.zip"
PACKAGE_ASSET="v01-release-gate-proof.zip"
K8S_SHA256="${ACP_K8S_ASSET_SHA256:-ea81ca1f0784692fbdcaff378a4835dc23731caeb41f8be1ffb1d2607593463d}"
PACKAGE_SHA256="${ACP_PACKAGE_ASSET_SHA256:-5e41f02d65be5b66d1b6896ad821ebcde78fb907d4023c32e9640b820996a10e}"
BASE_URL="https://github.com/avaargsh/agent-control-plane/releases/download/${TAG}"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

curl -fL --retry 3 --retry-delay 2 "${BASE_URL}/${K8S_ASSET}" -o "$tmp/${K8S_ASSET}"
curl -fL --retry 3 --retry-delay 2 "${BASE_URL}/${PACKAGE_ASSET}" -o "$tmp/${PACKAGE_ASSET}"

printf '%s  %s\n' "$K8S_SHA256" "$tmp/$K8S_ASSET" | sha256sum -c -
printf '%s  %s\n' "$PACKAGE_SHA256" "$tmp/$PACKAGE_ASSET" | sha256sum -c -

mkdir "$tmp/k8s" "$tmp/package"
unzip -q "$tmp/$K8S_ASSET" -d "$tmp/k8s"
unzip -q "$tmp/$PACKAGE_ASSET" -d "$tmp/package"

proof="$(find "$tmp/k8s" -type f -name independent-execution-proof.json -print -quit)"
if [[ -z "$proof" ]]; then
  echo "published Kubernetes proof is missing independent-execution-proof.json" >&2
  exit 2
fi
artifact_dir="$(dirname "$proof")"

test -f "$artifact_dir/independent-execution-proof.json.sha256"
test -f "$artifact_dir/release-evidence.json"
test -f "$artifact_dir/source-commit.txt"

published_source_commit="$(tr -d '\r\n' < "$artifact_dir/source-commit.txt")"
if [[ "$published_source_commit" != "$EXPECTED_SOURCE_COMMIT" ]]; then
  echo "published source commit mismatch: $published_source_commit != $EXPECTED_SOURCE_COMMIT" >&2
  exit 2
fi

python scripts/verify_execution_proof.py   "$artifact_dir/independent-execution-proof.json"   --expected-hash-file "$artifact_dir/independent-execution-proof.json.sha256"

python scripts/verify_v01_acceptance_artifacts.py "$artifact_dir"

python - "$artifact_dir/release-evidence.json" "$EXPECTED_SOURCE_COMMIT" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
expected = sys.argv[2]
payload = json.loads(path.read_text(encoding="utf-8"))
if payload.get("sourceCommit") != expected:
    raise SystemExit(
        f"release-evidence sourceCommit mismatch: {payload.get('sourceCommit')} != {expected}"
    )
if not payload.get("verified"):
    raise SystemExit("release-evidence is not marked verified")
print(json.dumps({
    "publishedReleaseEvidence": str(path),
    "sourceCommit": expected,
    "verified": True,
}, sort_keys=True))
PY

verification="$(find "$tmp/package" -type f -name verification.json -print -quit)"
fresh_clone_log="$(find "$tmp/package" -type f -name fresh-clone-release.log -print -quit)"
test -n "$verification"
test -n "$fresh_clone_log"

python - "$verification" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if not payload:
    raise SystemExit("published package verification.json is empty")
print(json.dumps({
    "publishedPackageVerification": sys.argv[1],
    "verified": True,
}, sort_keys=True))
PY

echo "published v0.1.0 release proof regression check: PASS"
