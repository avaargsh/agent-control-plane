#!/usr/bin/env sh
set -eu

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "audit-history must run from a real git clone" >&2
  exit 2
fi

if command -v gitleaks >/dev/null 2>&1; then
  exec gitleaks git --redact --no-banner .
fi

if command -v trufflehog >/dev/null 2>&1; then
  exec trufflehog git file://"$PWD" --only-verified
fi

echo "Install gitleaks or trufflehog before running the history audit." >&2
exit 2
