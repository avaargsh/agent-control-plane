# PR Watch — scoped Task query filters

The authenticated, read-only `GET /api/tasks` endpoint supports three
optional query parameters, individually or combined:

| Parameter | Example | Meaning |
| --- | --- | --- |
| `state` | `WAITING` | Exact Task projection state: WAITING, UNKNOWN, ACTIVE, PAUSED, CANCEL_REQUESTED, OBSERVED_TERMINAL |
| `repo` | `avaargsh/agent-control-plane` | Exact owner/repository selector |
| `limit` | `20` | First 1–100 matching Tasks (default 100) |

Example: `GET /api/tasks?state=WAITING&repo=avaargsh/agent-control-plane&limit=20`.

The request identity is **never** a query parameter. The service first
retrieves a bounded, owner-scoped Goal snapshot from the existing
GoalStore/PostgresWatchStore and projects each Task using the authenticated
principal; it then applies filters and the limit. It does not introduce a
second task ledger or change the `pr-watch-task-projection/v1` response
shape. Duplicate parameters, malformed selectors, unknown states and
out-of-range limits fail closed with HTTP 400. Unauthenticated callers
still receive HTTP 401.

This is an in-memory filter over the existing bounded Goal list, **not
database-wide pagination**. A user with more than the existing Goal list
limit must not interpret an empty filtered response as a complete
global search result. No Task is promoted to SUCCEEDED by a GitHub
event, a digest or an observed merge; `business_outcome_verified`
remains false and `independent_proof_ref` remains null.

To test:

```bash
python -m pytest -q tests/test_pr_watch_task_projection.py
```
