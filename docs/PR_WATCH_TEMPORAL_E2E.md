# PR Watch: Real Temporal Workflow Integration Acceptance

**Status:** isolated Temporal time-skipping test server, not a production
Temporal cluster, not a live GitHub integration, and not a cloud computer.

The PR watcher previously had unit tests and an import-only Temporal check.
This acceptance now starts the **real Temporal SDK test server** and executes
the actual `PRWatchWorkflow` with a Temporal Worker, Task Queue, Activities,
workflow timers and persisted history.

## Run

From a Linux x86_64 development environment with network access for first
download of the Temporal test server binary:

```bash
python -m pip install -e '.[dev]' 'temporalio>=1.8,<2'
pytest -q tests/test_pr_watch_temporal_e2e.py
```

SDK testing API: `WorkflowEnvironment.start_time_skipping()`. The test
server is downloaded by the official SDK if it is not cached and destroyed
with its async context. Native ARM Linux support for the time-skipping
server is not guaranteed; use an x86_64 CI runner.

## Failure and recovery gates

| Scenario | Observation |
|---|---|
| CI red -> green -> merged | Workflow resumes across timer cycles; final state persisted; Action Required once, nonurgent updates summarized |
| Worker process/instance change | First worker shuts down after an actual Activity; replacement Worker continues same Workflow ID and task history |
| External read throws ApplicationError | Failed Activity must not mark goal complete; Workflow waits and later succeeds after an independent observation |
| Goal cancel during running Activity | Later tick observes durable CANCELLED and stops without reactivating a Goal or claiming provider success |

The Worker takeover test deliberately uses `max_cached_workflows=0`
to disable sticky Workflow caches: new Workers then rehydrate history from the
ordinary Task Queue instead of awaiting the obsolete sticky Worker queue.
The test waits for the first durable TIMER_STARTED event, shuts down Worker 1,
starts Worker 2 and sends an authenticated-test Temporal `wake` Signal to
resume without depending on time-skipping delays.

The fixture Activity exercises the existing `GoalStore.tick`, durable
PR-state transition and the `AttentionOutbox` with SQLite. It does not
use a GitHub token or make real network provider requests. This preserves
the boundary between external GitHub data, Temporal orchestration, and
ACP exact-plan authority. Time-skipping integration cannot prove external
provider idempotency, end-to-end notification delivery, or real server HA.

## Runtime correction

The production-like example Activity is now `async` and delegates its
blocking GitHub/SQLite I/O to `asyncio.to_thread`. This prevents blocking
the Temporal Worker's event loop, and defers importing provider libraries
until Activity execution. Workflow replay must not execute provider I/O.

The existing API's `pause/resume/cancel` controls remain durable SQLite
CAS operations. A running Workflow learns of state changes on its next
Activity poll (default 300 seconds), **not immediately by Signal**.
Cancellation does not retract a notification already observed or sent.

## Next constraints before service promotion

- A real Temporal server restart drill backed by durable persistence
  (PostgreSQL), not just replacement of SDK Workers.
- Trusted production identity and goal/task enrollment at the API boundary.
- Stable pinned Temporal SDK/test-server versions and Workflow code
  versioning for compatibility during rolling upgrades.
- Authenticated control-to-Workflow Signals/Updates from the HTTP API;
  the Workflow now implements `wake`, but the API does not send it.
  Lost Signal ACK requires reconciliation before asserting fast resumption.
- A real provider-backed notification delivery receipt and UNKNOWN
  reconciliation, with user-scoped recipient authorization.

Run the dedicated `pr-watch-temporal-real-e2e` GitHub Actions workflow.
The fast `pytest -q` collection skips integration acceptance when
`temporalio` is absent. Do not promote this integration evidence into
ACP v0.1 independent execution proof.
