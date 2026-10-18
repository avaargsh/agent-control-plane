"""Live PostgreSQL runtime adapters acceptance; all user identities server-bound."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import Thread

import pytest

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("temporalio")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))

from attention_telegram_adapter import (  # noqa: E402
    ProviderReceipt, TelegramAdapter, dispatch_once,
)
from pr_watch_control_bridge import TemporalControlBridge, workflow_id  # noqa: E402
from pr_watch_harness import PullSnapshot  # noqa: E402
from pr_watch_launcher import TemporalLaunchBridge  # noqa: E402
from pr_watch_pg_runtime import PGTelegramOutbox  # noqa: E402
from pr_watch_pg_store import PostgresWatchStore  # noqa: E402
from pr_watch_service import (  # noqa: E402
    LocalTokens, PilotConfigError, PilotHTTPServer, PilotService,
)
from pr_watch_temporal import read_and_persist_pr  # noqa: E402
from test_pr_watch_service import request  # noqa: E402
from temporalio.testing import WorkflowEnvironment  # noqa: E402

DSN = os.getenv("PR_WATCH_PG_TEST_DSN")
TOKENS = {"alice": "a" * 40, "bob": "b" * 40}


@pytest.fixture(scope="module")
def pg():
    if not DSN:
        pytest.skip("requires disposable PR_WATCH_PG_TEST_DSN")
    store = PostgresWatchStore(DSN)
    store.install_v2()
    # Disposable CI DB only; never point test DSN at production!
    with psycopg.connect(DSN) as conn:
        conn.execute("TRUNCATE pr_watch_pg_goals CASCADE")
    return store


@contextmanager
def http_server(pg, *, launch_bridge=None, control_bridge=None):
    service = PilotService(
        None, LocalTokens(TOKENS), control_bridge, launch_bridge,
        storage_backend="postgres", pg_dsn=DSN,
    )
    server = PilotHTTPServer(("127.0.0.1", 0), service)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_postgres_api_requires_schema_and_rejects_mixed_mode(pg):
    with pytest.raises(PilotConfigError, match="forbids SQLite"):
        PilotService(
            "legacy.sqlite", LocalTokens(TOKENS),
            storage_backend="postgres", pg_dsn=DSN,
        )
    with pytest.raises(PilotConfigError, match="requires only"):
        PilotService(
            "legacy.sqlite", LocalTokens(TOKENS),
            storage_backend="sqlite", pg_dsn=DSN,
        )
    with pytest.raises(PilotConfigError, match="unsupported"):
        PilotService(
            None, LocalTokens(TOKENS), storage_backend="not-a-backend",
        )
    pg.validate_v2()


def test_postgres_http_auth_create_pause_resume_events_reconcile(pg):
    with http_server(pg) as api:
        status, data, _ = request(api, "POST", "/api/goals", who="alice", body={
            "goal_id": "http-control", "repo": "o/repo", "number": 175,
        })
        assert status == 200
        assert json.loads(data)["launch"]["mode"] == "LOCAL_ONLY"
        assert json.loads(data)["goal"]["version"] == 0
        assert request(api, "GET", "/api/goals/http-control", who="bob")[0] == 404
        assert request(api, "GET", "/api/goals/http-control/launch", who="bob")[0] == 404
        status, data, _ = request(
            api, "POST", "/api/goals/http-control/pause", who="alice",
            body={"expected_version": 0},
        )
        assert status == 202
        parsed = json.loads(data)
        assert parsed["goal"]["state"] == "PAUSED"
        assert parsed["control"]["signal_state"] == "LOCAL_ONLY"
        assert request(
            api, "POST", "/api/goals/http-control/pause", who="alice",
            body={"expected_version": 0},
        )[0] == 409
        assert request(
            api, "GET", "/api/goals/http-control/controls", who="bob",
        )[0] == 404
        status, data, _ = request(
            api, "POST", "/api/goals/http-control/resume", who="alice",
            body={"expected_version": 1},
        )
        assert status == 202 and json.loads(data)["goal"]["state"] == "RUNNING"
        result = pg.tick(
            user_id="alice", goal_id="http-control",
            reader=type("Reader", (), {
                "fetch": lambda self, repo, number: PullSnapshot(
                    head_sha="d" * 40, state="OPEN", ci="FAILURE",
                )
            })(), now=datetime.now(timezone.utc),
        )
        assert result["phase"] == "CI_BLOCKED"
        status, data, _ = request(
            api, "GET", "/api/events?after=0", who="alice",
        )
        assert status == 200 and json.loads(data)["events"][0]["kind"] == "ACTION_REQUIRED"
        status, data, _ = request(
            api, "GET", "/api/events/stream?after=0", who="alice",
        )
        assert status == 200 and "CI failed" in data
        assert request(api, "GET", "/api/events", who="bob")[0] == 200
        assert json.loads(request(api, "GET", "/api/events", who="bob")[1])["events"] == []


def test_postgres_launch_and_control_signals_use_real_temporal(pg):
    async def scenario():
        async with await WorkflowEnvironment.start_time_skipping() as env:
            async def factory():
                return env.client
            launch = TemporalLaunchBridge(
                address="test:7233", client_factory=factory,
            )
            bridge = TemporalControlBridge(
                address="test:7233", client_factory=factory,
            )
            with http_server(pg, launch_bridge=launch, control_bridge=bridge) as api:
                status, data, _ = await asyncio.to_thread(
                    request, api, "POST", "/api/goals",
                    who="alice", body={
                        "goal_id": "temporal-runtime", "repo": "o/repo", "number": 176,
                    },
                )
                assert status == 200
                assert json.loads(data)["launch"]["state"] == "ACKED"
                key = workflow_id(user_id="alice", goal_id="temporal-runtime")
                handle = env.client.get_workflow_handle(key)
                details = await handle.describe()
                assert details.status.name == "RUNNING"
                status, data, _ = await asyncio.to_thread(
                    request, api, "POST",
                    "/api/goals/temporal-runtime/pause",
                    who="alice", body={"expected_version": 0},
                )
                assert status == 200
                assert json.loads(data)["control"]["signal_state"] == "ACKED"
                assert pg.goal(user_id="alice", goal_id="temporal-runtime")["state"] == "PAUSED"
                status, data, _ = await asyncio.to_thread(
                    request, api, "POST",
                    "/api/goals/temporal-runtime/controls/1/reconcile",
                    who="alice", body={},
                )
                assert status == 200
                assert json.loads(data)["control"]["signal_state"] == "ACKED"
    asyncio.run(scenario())


def test_temporal_activity_reads_pg_only_and_persists_event(pg, monkeypatch):
    pg.register(
        user_id="alice", goal_id="activity-pg", repo="o/repo", number=176,
    )
    class FakeReader:
        def fetch(self, repo, number):
            assert (repo, number) == ("o/repo", 176)
            return PullSnapshot(head_sha="e" * 40, state="OPEN", ci="FAILURE")
    import pr_watch_github
    monkeypatch.setattr(
        pr_watch_github.GitHubReadOnlyReader, "from_environment",
        lambda: FakeReader(),
    )
    monkeypatch.setenv("PR_WATCH_STORAGE", "postgres")
    monkeypatch.setenv("PR_WATCH_PG_DSN", DSN)
    monkeypatch.delenv("PR_WATCH_DB", raising=False)
    result = asyncio.run(read_and_persist_pr({
        "user_id": "alice", "goal_id": "activity-pg", "interval_seconds": 300,
    }))
    assert result["phase"] == "CI_BLOCKED"
    assert pg.delivery_counts(user_id="alice")["PENDING"] >= 1
    monkeypatch.setenv("PR_WATCH_DB", "legacy.sqlite")
    with pytest.raises(ValueError, match="without SQLite"):
        asyncio.run(read_and_persist_pr({
            "user_id": "alice", "goal_id": "activity-pg",
        }))
    assert not Path("legacy.sqlite").exists()


def test_postgres_telegram_dispatcher_acceptance_and_ack_loss(pg):
    pg.register(
        user_id="bob", goal_id="telegram-pg", repo="o/repo", number=175,
    )
    pg.tick(
        user_id="bob", goal_id="telegram-pg",
        reader=type("Reader", (), {
            "fetch": lambda self, repo, num: PullSnapshot(
                head_sha="c" * 40, state="OPEN", ci="FAILURE",
            )
        })(), now=datetime.now(timezone.utc),
    )
    adapter = TelegramAdapter(
        token="123456:" + "x" * 24, recipients={"bob": 123, "alice": 222},
    )
    adapter.send = lambda lease: ProviderReceipt("987", "a" * 64)
    outbox = PGTelegramOutbox(pg)
    result = dispatch_once(
        outbox, adapter, user_id="bob", owner="worker-b",
        now=datetime.now(timezone.utc),
    )
    assert result["accepted_by_provider"] == 1
    assert pg.delivery_counts(user_id="bob")["DELIVERED"] == 1
    # A missing user mapping cannot mutate claim status before validation.
    other = TelegramAdapter(
        token="123456:" + "x" * 24, recipients={"alice": 222},
    )
    with pytest.raises(Exception, match="recipient not configured"):
        dispatch_once(
            outbox, other, user_id="bob", owner="worker-b",
            now=datetime.now(timezone.utc),
        )
    assert pg.delivery_counts(user_id="bob")["DELIVERED"] == 1
