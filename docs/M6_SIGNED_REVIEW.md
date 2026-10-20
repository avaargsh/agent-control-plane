# M6 signed reviewer decision verification

**Status: verification utility only. It does not authorize deployment or return release GO.**

The offline M6 manifest validator checks structure and the materialized-evidence
validator checks actual file bytes against manifest digests. Neither proves
who reviewed the bundle. This verifier adds a detached DSSE/Ed25519 signature
over the exact manifest bytes and binds the decision to an independently
supplied deployed source commit and reviewer trust profile.

## Trust boundary

The verifier requires, out of band from the evidence bundle:

- The reviewer's 32-byte raw Ed25519 public key.
- The exact trusted key ID and stable reviewer identity.
- The exact deployed Git commit SHA, obtained from deployment provenance.
- The evidence root containing the materialized files referenced by the manifest.

Do not take the key, key ID, reviewer identity, or expected source SHA from the
manifest or DSSE envelope. The reviewer public key/fingerprint must be pinned
through a separate organization-controlled channel. Never place private keys
in this repository or the evidence directory.

## Signed DSSE statement

The payload type is `application/vnd.in-toto+json`, signed using standard
DSSE PAE. Its in-toto Statement v1 has:

- Subject name `m6-live-acceptance/manifest.json` and the SHA-256 of the
  **exact manifest bytes**.
- Predicate type `https://agent-control-plane.dev/m6-signed-review/v1`.
- Predicate schema `m6-signed-review/v1`, manifest digest, source commit,
  `decision=GO|NO_GO`, reviewer ID, issue time and expiry time.

Exactly one signature from the pinned key is accepted. Duplicate JSON keys,
wrong or stale keys, modified manifest bytes, a different deployed commit,
expired/future/overlong statements, reviewer identity mismatch, and
manifest/signature decision conflicts fail closed. The maximum review TTL is
24 hours by default.

The reviewer must sign the manifest **after** all evidence files are frozen.
For a signed GO, verification also re-reads every materialized evidence file,
checks its digest and path safety, and correlates the PR and check delivery
reports. Missing or modified artifacts deny verification.

## Verify an independently signed bundle

Install the optional verifier dependency:

```bash
python -m pip install '.[proof-verify]'
```

Then run the command with the trust profile and deployed commit supplied from
independent authority:

```bash
PYTHONPATH=examples python examples/m6_signed_review.py \
  /secure/m6/manifest.json \
  /secure/m6/reviewer.dsse.json \
  --evidence-root /secure/m6/evidence \
  --trusted-public-key /etc/acp/trust/m6-reviewer.ed25519.pub \
  --trusted-keyid 'm6-review-key-2026' \
  --trusted-reviewer-id 'security-reviewer-1' \
  --expected-source-commit '0123456789abcdef0123456789abcdef01234567'
```

All paths, IDs and SHAs above are examples. The public-key file contains
exactly 32 raw bytes. The command does not create signatures or keys, call
GitHub/Temporal, change infrastructure, or contact a deployment system.

## Output semantics

- `SIGNED_GO_VERIFIED_NOT_RELEASED`: the pinned reviewer signed GO for the
  exact manifest and source commit, all 16 gates pass structurally, and all
  materialized files and the paired delivery claims match.
- `SIGNED_NO_GO_VERIFIED`: the pinned reviewer signed NO_GO for the exact
  manifest and source commit.
- `NO_GO`: signature, trust, source, expiry, or evidence verification failed.

**Every output has `release_decision=NO_GO`.** A valid signature authenticates
the reviewer's statement; it does not prove that the real-world observations
are true, that a business outcome is complete, or that an authorized deployer
has executed a separately approved rollout. The actual public webhook remains
NO-GO until the operator completes the live runbook and independent review.
