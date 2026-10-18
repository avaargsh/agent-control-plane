# Persistent PR Watch — Harness Vertical Slice

**Status:** experimental standalone example under ACP; not part of ACP v0.1
authority, not production multi-tenant infrastructure, not an installed service.

This is the first concrete vertical slice towards **long-lived Agent goals with
low-noise Attention decisions**. It watches a GitHub PR and durable task state;
it never writes to GitHub, never runs arbitrary code, never uses a Cloud PC, and
does not send notifications to a real channel. An external Harness owns
authentication, per-channel delivery, memory, and user-facing UX.

## Boundaries

```text
User goal (trusted CLI only)
   -> SQLite pr_watch_goals (user_id, goal_id, repo, PR number, version)
   -> GitHub GET-only observer (PR, check-runs, commit status, reviews)
   -> Durable observation and pending pr_watch_events (same SQLite transaction)
   -> existing AttentionOutbox.submit (deduped, durable)
   -> SILENT / DIGEST / PENDING notification intent
   -> [External delivery adapter not yet implemented]
```

If the process crashes after updating the goal but before calling the
AttentionOutbox, the un-emitted event is replayed on the next tick. If it
crashes after the Outbox committed but before acknowledging that event, the
Outbox deduplicates the replay. Concurrent workers compare the persisted
goal version before changing state. Provider uncertainty cannot mark a PR
successful. The notification Outbox still has its independent UNKNOWN
reconciliation contract for delivery ACK loss.

## Local offline demonstration

Requires Python 3.11+, `pip install -e '.[dev]'` from the ACP repository.

```bash
mkdir -p .artifacts/pr-watch
python examples/pr_watch_harness.py register \
  --db .artifacts/pr-watch/watch.db --user demo --goal review-168 \
  --repo avaargsh/agent-control-plane --pr 168

printf '%s\n' '{"head_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","state":"OPEN","ci":"FAILURE","review":"NONE","failed_checks":["check-1"]}' > .artifacts/pr-watch/failure.json
python examples/pr_watch_harness.py tick \
  --db .artifacts/pr-watch/watch.db --user demo --goal review-168 \
  --fixture .artifacts/pr-watch/failure.json

python examples/pr_watch_harness.py status \
  --db .artifacts/pr-watch/watch.db --user demo --goal review-168
```

Expected first offline state: `CI_BLOCKED`, `RUNNING`.
The AttentionOutbox DB contains a PENDING delivery intent, not a delivered
notification. Repeating the same fixture does not enqueue duplicate events.
Use a different synthetic SHA to test a new incident.

## Optional live GitHub observation

Create a fine-grained **read-only** token with access to the target
repository's Pull requests and Actions/status metadata as required by the
GitHub API. Never commit the token.

```bash
export GITHUB_TOKEN=...    # local secret or secret manager only
python examples/pr_watch_harness.py tick \
  --db .artifacts/pr-watch/watch.db --user demo --goal review-168
```

The adapter calls only these GET endpoints:
`/pulls/{number}`, `/commits/{sha}/check-runs`,
`/commits/{sha}/status`, `/pulls/{number}/reviews`.
It refuses incomplete pagination or ambiguous GitHub responses. PR body and
untrusted check names are **not** presented to a model or notification
decision. Green checks are not evidence of merge eligibility, and no
ACP exact-plan authorization, code change, or PR merge is attempted.

## Optional Temporal workflow (real SDK)

```bash
pip install 'temporalio>=1.8,<2'
export PR_WATCH_DB="$(pwd)/.artifacts/pr-watch/watch.db"
export GITHUB_TOKEN=...  # worker process only
# Start a separate local Temporal development server (not provided here).
python examples/pr_watch_temporal.py worker --address localhost:7233
# In another shell with the same durable DB path:
python examples/pr_watch_temporal.py start --user demo --goal review-168 \
  --interval 300 --address localhost:7233
```

One Workflow ID is deterministically derived from the (user, goal) identity;
the workflow polls through Activities, sleeps via Temporal timers, and uses
Continue-As-New after 100 iterations. GitHub and SQLite I/O is never executed
inside Workflow replay. Actual Temporal end-to-end execution requires an
available server and is **not** proven by offline unit/CI tests.
All workers must share the same stable SQLite file in this reference; a
production service requires PostgreSQL and authenticated goal APIs.

## Acceptance and explicit limits

Run `pytest -q tests/test_pr_watch_harness.py`.

- Validated: repeat/dedupe, crash before/after outbox submission, unknown
  status, failed/cleared CI, review blocker, merged/closed PR, concurrent
  worker version guard, cross-user goal lookup denial and provider outages.
- Not validated: live GitHub token permissions/rate limits against your
  installed account, running Temporal server, remote delivery or mobile push,
  provider delivery reconciliation, workflow cancellation, browser takeover,
  multi-process/high-availability PostgreSQL, rate/notification budgets.
- CLI `--user` is a **local trusted scope selector**, NOT authentication.
  Do not expose this CLI behind a user-facing unauthenticated API.
- An `ACTION_REQUIRED` Attention decision is a user work item, not an ACP
  authorization grant; all future GitHub writes need explicit provider
  authorization and ACP execution evidence.
- A repeated identical observable CI failure is deduped indefinitely for the
  goal. A later issue that reproduces with precisely the same SHA and check
  results requires a fresh incident identity/episode policy in production.

## Next slice

Add a scoped API + authenticated Web dashboard, a real notification adapter
with confirmed receipts and UNKNOWN reconciliation, explicit cancel/pause
workflow signals, and an end-to-end Temporal + GitHub fixture acceptance.
Use a replaceable on-demand Sandbox Provider only when the task truly needs
code execution; do not deploy one VM per user by default.
