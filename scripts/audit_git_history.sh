#!/usr/bin/env sh
set -eu

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "audit-history must run from a real git clone" >&2
  exit 2
fi

if command -v gitleaks >/dev/null 2>&1; then
  # Preserve full-history coverage and nonzero exit status. On failure
  # disclose only exact finding fingerprints (NOT matches, raw secrets or
  # surrounding source lines) so false positives can be reviewed precisely.
  report="$(mktemp)"
  if gitleaks git --redact --no-banner --report-format json --report-path "$report" .; then
    rm -f "$report"
    exit 0
  fi
  if command -v python3 >/dev/null 2>&1; then
    python3 - "$report" <<'PY'
import json
import sys
try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        findings = json.load(fh)
    for item in findings:
        print("SECRET_AUDIT_FINDING_METADATA " +
              json.dumps({key: item.get(key) for key in
                          ("RuleID", "File", "Commit", "Fingerprint")},
                         sort_keys=True), file=sys.stderr)
except Exception:
    print("SECRET_AUDIT_FINDING_METADATA_UNAVAILABLE", file=sys.stderr)
PY
  fi
  rm -f "$report"
  exit 1
fi

if command -v trufflehog >/dev/null 2>&1; then
  exec trufflehog git file://"$PWD" --only-verified
fi

echo "Install gitleaks or trufflehog before running the history audit." >&2
exit 2
