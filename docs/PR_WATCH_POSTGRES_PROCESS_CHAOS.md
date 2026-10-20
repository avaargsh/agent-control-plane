# PR Watch PostgreSQL — Independent Process Crash Acceptance

**Scope**: opt-in, disposable PostgreSQL 16 test service, two separate
loopback HTTP processes and independently spawned Worker/dispatcher
processes. The active app remains a development Pilot; these tests **do not**
establish production high availability, a real cluster leader election, or
a real Temporal server restart.

## Actual process boundaries

This stage uses Python's `multiprocessing.get_context("spawn")` so each
child has its **own Python interpreter, database connection and HTTP
listener**. Only test-created PIDs are terminated, then joined and cleaned.
The test suite never stops a shared database server, runs Docker or kills
unrelated host processes.

The tests cover:

1. **Two API processes, one PostgreSQL Goal**: concurrent HTTP
   `POST /api/goals` on both processes creates only one scoped registration.
   Simultaneous Pause requests with the same CAS version yield **one 202 and
   one 409**, plus exactly one durable Control Journal intent. Killing one
   API leaves the surviving process able to read and resume.
2. **Six independent start-admission processes**: all compete on the same
   registered `(user_id,goal_id)`. Only the winning PostgreSQL transaction
   returns `claimed_now=true`; others must not send `start_workflow`.
3. **Hard process death after durable start claim**: kill the process
   holding the ownership after PostgreSQL commit, before the remote call
   outcome can be recorded. Following expiry, recover `IN_FLIGHT→UNKNOWN`
   without returning to `PENDING` or giving a new caller start permission.
   This is deliberately conservative, since a remote Temporal start **may**
   have been accepted before the client was terminated.
4. **Two senders, one notification**: separate Worker processes compete
   through `FOR UPDATE SKIP LOCKED`. At most one claims. Kill the holder
   before provider confirmation; the durable lease expires to `UNKNOWN`
   and no replacement Worker resends automatically.
5. **Abrupt loss of an uncommitted PostgreSQL client session**: the child
   writes a Goal and its corresponding launch inside one open transaction.
   Kill the client before COMMIT; PostgreSQL rolls back both. A subsequent
   clean registration works.

These are **fault injection** tests, not proof a real remote provider send
was delivered. Provider acceptance and ACK-loss tests using real Temporal
SDK and a fake Telegram endpoint remain in the existing PG runtime suite.

## Run

**Only against a disposable DB**: the test module truncates PostgreSQL
PR Watch Pilot tables. Do not point it at production or a DB with user data.

```bash
python -m pip install -e '.[dev,pg-pilot]' 'temporalio>=1.8,<2'
export PR_WATCH_PG_TEST_DSN='postgresql://postgres:postgres@127.0.0.1:5432/DISPOSABLE_DB'
python -m pytest -q tests/test_pr_watch_pg_process_chaos.py -x
```

CI extends `pr-watch-postgres-admission` to run the suite on Python 3.11
and 3.12 against PostgreSQL 16, alongside existing PG/Temporal tests and
full repository checks. Every child is bounded and cleaned after the test,
including in failure paths.

## Explicit remaining gaps

- **No physical PostgreSQL instance crash/restart**, no failover to a
  secondary, no backup/PITR acceptance.
- **No real Temporal server crash** or worker takeover during a pending
  activity. Existing time-skipping test-server coverage is separate.
- No multi-region, no production auth or project-level RBAC.
- No live GitHub PR polling or Telegram sends in the fault tests.
- No guarantee of automatic resumption of `UNKNOWN`: requires trusted
  positive Temporal/Telegram reconciliation; missing evidence is not
  proof of absence.
- No production-grade leader election or unbounded dispatcher.

## PostgreSQL v3 prepared-control concurrency and crash boundary

The separate `tests/test_pr_watch_pg_v3_chaos.py` suite upgrades a
**new, randomly named isolated schema** directly to v3; it never
truncates or modifies another tenant/schema. It runs five real PG16 cases:

1. Six concurrent clients execute one prepared `request_id`: exactly one
   new Goal CAS and Control Journal row, five replays of that receipt.
2. Two distinct prepared keys compete for the same expected Goal version:
   exactly one commits. The loser stays PREPARED, becomes stale, and cannot
   silently advance to a newer Goal version.
3. The same ID can be prepared by different **authenticated owners**;
   reusing it across Goals or commands within one owner is rejected.
4. SIGKILL a dedicated child **after real SQL CAS/Journal/request mutation
   but before transaction COMMIT**. PostgreSQL must roll all three writes
   back. Reopening the store reveals PREPARED; an explicit later execution
   is still possible.
5. SIGKILL after the real transaction COMMIT but **before any client
   acknowledgement**. Reopening the store reveals COMMITTED, and replay
   returns the existing receipt without another Goal CAS/Control Journal row.

The crash instrumentation wraps the production store's connection context,
not its SQL mutation logic; a child uses its own connection and PID. The
parent only kills the child it created. A separate v3 test step runs on
Python 3.11 and 3.12 within `pr-watch-postgres-admission`.

```bash
python -m pytest -vv -s tests/test_pr_watch_pg_v3_chaos.py -x --timeout=75
```

**Proof boundary:** these tests establish PostgreSQL transaction
atomicity and deterministic request replay within the pilot contract.
They do not establish exactly-once **remote** Temporal Signal delivery,
nor do they guarantee that a crash between COMMIT and the first signal
attempt causes a signal to be sent later. A durable PENDING control still
requires explicit trusted readback/reconciliation. A PREPARED request that
lost its original response must never be automatically executed on reload.
Public GitHub Webhook M6 remains NO-GO.

## V3 two-API-process HTTP idempotency

`tests/test_pr_watch_pg_v3_http.py` runs the **real loopback HTTP handler**
in two independent Python OS processes, both connected to one isolated
PostgreSQL v3 schema. It verifies two distinct network-level conditions:

- The identical prepared request is received by two API processes at the
  same time. Both return a valid receipt, but only one reports
  `replayed:false` and commits the Goal CAS; the other returns
  `replayed:true`, without another control entry. A surviving API can
  serve the same replay after its peer is killed.
- Two different prepared request IDs race to commit the same Goal version.
  Only one gets HTTP 202; the other gets HTTP 409. Its durable request
  stays PREPARED (stale) and is never automatically advanced to a newer
  Goal version. Cross-user readbacks are denied, and a request ID cannot
  be rebound to another command by the same owner.

This covers two independent HTTP listeners, authentication, routing,
database contention and the durable replay response, beyond direct store
tests. The pilot runs with its local-only Temporal bridge disabled;
thus this result **does not** prove an external Temporal Signal was
delivered exactly once. External M6 webhook acceptance stays NO-GO.
