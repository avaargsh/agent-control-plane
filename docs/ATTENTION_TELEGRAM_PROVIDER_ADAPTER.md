# Telegram Notification Adapter — Fail-Closed Delivery Reference

**Status:** opt-in external Harness example, not ACP v0.1 core, not a
hosted notification service, and not evidence of human message receipt.
The real Telegram `sendMessage` API is supported when the operator supplies
credentials; CI uses isolated stub transports and **never sends a live message**.

## Architecture

```text
PR Watch Goal observation -> Attention decision + SQLite delivery intent
                                      |
                           Authenticated recipient binding (local operator)
                                      |
                     claim_due(user, owner, generation, lease)
                                      |
                     HTTPS Telegram Bot API sendMessage (one attempt)
                              /                  \
              valid Telegram receipt         timeout / missing /
             matching chat + message_id       ambiguous receipt
                         |                            |
             atomic DELIVERED + receipt             UNKNOWN
                         |                            |
             no further sends                 NO AUTOMATIC RETRY
```

A valid Telegram receipt shows the Telegram API **accepted** a send request.
It does not prove the user received, saw, read or acted on a message. Neither
GitHub PR CI success nor this receipt is ACP execution-outcome proof.

## Opt-in live delivery (single user, one batch)

Prerequisites: existing durable notification PENDING intents populated by
`examples/pr_watch_harness.py` or another trusted Attention producer.

```bash
python -m pip install -e '.[dev]'
export PR_WATCH_TELEGRAM_BOT_TOKEN='...from secret manager...'
export PR_WATCH_TELEGRAM_RECIPIENTS_JSON='{"demo":123456789}'
python examples/attention_telegram_adapter.py \
  --db .artifacts/pr-watch/watch.db --user demo --owner local-telegram-1
```

- The bot must have permission to message the configured destination; users
  must opt in / start the bot where required by Telegram.
- `PR_WATCH_TELEGRAM_RECIPIENTS_JSON` is an **operator-trusted user -> chat ID
  mapping**, not an identity assertion from incoming messages or a proof of
  recipient entitlement. Never derive it from untrusted PR text.
- Use secret manager permissions rather than config files in Git or browser
  tokens. Run only on the local development host; do not expose this adapter
  as an unauthenticated HTTP service.
- The Bot API URL has a fixed HTTPS host. Redirects are disabled to reduce
  credential forwarding risk. Adapter exceptions never include the bot token.
- This is a **one-shot command**. Run it manually or via a trusted scheduler
  with an explicit user scope; it does not continuously poll by itself.
- Under `balanced` Attention preferences, successful PR work commonly
  becomes DIGEST, not PENDING; `ACTION_REQUIRED` creates notification intents.

## Durable receipt semantics

The `delivery_receipts` table is distinct from the existing
`deliveries` decision and lease tables. It records `provider=telegram`,
the remote `message_id`, an SHA-256 digest of the configured recipient,
the acknowledgement timestamp and lease owner/generation, but never the
bot token or raw recipient ID.

A matching API `message_id` and `chat.id` triggers **one SQLite transaction**
which CAS-updates the original lease and writes the receipt. Both changes
commit or neither does. If the provider may have accepted a message but
the local DB acknowledgement fails, the dispatcher marks the intent UNKNOWN
when the lease is still valid. Otherwise the lease expiry job marks it UNKNOWN.
**Neither condition returns it to PENDING automatically.**

All HTTP errors (including rate limit), timeouts, connection resets, malformed
JSON, redirects, mismatched recipients, and unverified provider replies are
treated as `UNKNOWN`: no automated resend.

For a lost acknowledgement Telegram Bot API does **not** supply an independent
outbound message lookup guaranteeing non-delivery. A timeout therefore cannot
be declared absent merely by checking for a missing local receipt. The
existing `requeue_verified_absent` reference requires independent,
operator-verified provider absence evidence; its `evidence_ref` string is
NOT cryptographic proof or a verification service. **Do not requeue on guesswork.**
In some cases the correct operational choice is a manually reviewed, new
incident or user-acknowledged duplicate risk.

The dispatcher calls scoped `recover_expired(now, user_id=...)`, never mutating
other users' outbox leases. `UNKNOWN` and `DELIVERED` remain excluded from
ordinary claims; quiet-hours due timestamps and per-user dedupe still apply.

## Test locally

```bash
pytest -q tests/test_attention_telegram_adapter.py tests/test_attention_plane_reference.py
ruff check examples/attention_plane.py examples/attention_telegram_adapter.py
```

Coverage includes accepted remote message ID, cross-user receipt isolation,
malformed/mismatched receipts, timeouts, lost local DB ACK, late receipt past
lease expiration, provider ID conflicts, quiet hours, user-scoped recovery
and simultaneous workers. CI runs under Python 3.11 and 3.12 without a Bot
token. Live Telegram API traffic and human delivery are explicitly **not**
verified by CI.

## Before production rollout

Replace local user/chat bindings with an authenticated, user-consented
recipient verification flow and explicit revocation. Add per-user message
quotas and delivery rate limits, robust database migrations (PostgreSQL),
structured non-secret audit, sender error classification with external
provider evidence, token rotation, delivery-adapter monitoring and user
controls. Never conflate Attention notification ACK with ACP side-effect
attestation or Temporal execution ownership.
