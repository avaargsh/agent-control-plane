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
