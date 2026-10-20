"""Real PostgreSQL 16 migration upgrade/adoption/rollback acceptance.

Each test creates its own randomly named PostgreSQL schema in the disposable
database, so migration tests never TRUNCATE unrelated schemas or user data.
"""
from __future__ import annotations

import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg import conninfo, sql  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))

import pr_watch_pg_migrations as migration_module  # noqa: E402
from pr_watch_pg_admission import AdmissionError, SCHEMA_V1, PostgresAdmission  # noqa: E402
from pr_watch_pg_migrations import (  # noqa: E402
    MigrationError, PostgresMigrations,
)
from pr_watch_pg_store import SCHEMA_V2, PostgresWatchStore  # noqa: E402

DSN = os.getenv("PR_WATCH_PG_TEST_DSN")


@pytest.fixture
def isolated():
    if not DSN:
        pytest.skip("requires EXCLUSIVE, DISPOSABLE PR_WATCH_PG_TEST_DSN")
    name = "test_mig_" + uuid4().hex[:16]
    with psycopg.connect(DSN) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
    scoped = conninfo.make_conninfo(DSN, options=f"-c search_path={name}")
    try:
        yield scoped
    finally:
        with psycopg.connect(DSN) as conn:
            conn.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name))
            )


def _rows(dsn):
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "SELECT version,checksum FROM pr_watch_pg_schema_migrations ORDER BY version"
        ).fetchall()


def test_empty_readonly_status_and_explicit_v1_to_v2(isolated):
    mgr = PostgresMigrations(isolated)
    assert mgr.status().version == 0
    assert mgr.upgrade(1).version == 1
    assert [r[0] for r in _rows(isolated)] == [1]
    assert mgr.upgrade(1).version == 1
    store = PostgresWatchStore(isolated)
    with pytest.raises(MigrationError, match="version 2 required"):
        store.validate_v2()
    assert mgr.upgrade(2).version == 2
    store.validate_v2()
    assert [r[0] for r in _rows(isolated)] == [1, 2]
    assert mgr.upgrade(2).version == 2
    assert mgr.upgrade(1).version == 2  # never downgrade implicitly
    assert len(_rows(isolated)) == 2


def test_v1_to_v2_keeps_existing_user_goal_and_launch(isolated):
    pg = PostgresAdmission(isolated)
    pg.install_v1()
    pg.register(user_id="alice", goal_id="p1", repo="o/r", number=178)
    claim = pg.claim(user_id="alice", goal_id="p1", owner="w1")
    pg.finalize(claim=claim, state="UNKNOWN")
    store = PostgresWatchStore(isolated)
    store.install_v2()
    store.validate_v2()
    assert store.goal(user_id="alice", goal_id="p1")["version"] == 0
    assert store.goal(user_id="alice", goal_id="p1")["phase"] == ""
    assert store.launch(user_id="alice", goal_id="p1")["state"] == "UNKNOWN"
    with pytest.raises(MigrationError, match="nonempty table"):
        PostgresMigrations(isolated).rollback_empty_v2(confirm_empty=True)
    assert store.launch(user_id="alice", goal_id="p1")["state"] == "UNKNOWN"
    assert [r[0] for r in _rows(isolated)] == [1, 2]


def test_complete_unversioned_legacy_v1_adoption_with_data(isolated):
    with psycopg.connect(isolated) as conn:
        conn.execute(SCHEMA_V1)
        conn.execute(
            """INSERT INTO pr_watch_pg_goals
               (user_id,goal_id,repo,number,state)
               VALUES ('legacy','g','o/r',19,'RUNNING')"""
        )
        conn.execute(
            """INSERT INTO pr_watch_pg_launches
               (user_id,goal_id,state,evidence)
               VALUES ('legacy','g','UNKNOWN','CRASH_RECOVERY')"""
        )
    mgr = PostgresMigrations(isolated)
    with pytest.raises(MigrationError, match="unversioned"):
        mgr.status()
    with pytest.raises(MigrationError, match="explicit adoption"):
        mgr.upgrade(2)
    state = mgr.upgrade(2, adopt_legacy=True)
    assert state.version == 2 and state.adopted
    PostgresWatchStore(isolated).validate_v2()
    assert [r[0] for r in _rows(isolated)] == [1, 2]
    assert PostgresWatchStore(isolated).goal(
        user_id="legacy", goal_id="g",
    )["repo"] == "o/r"
    assert PostgresWatchStore(isolated).launch(
        user_id="legacy", goal_id="g",
    )["state"] == "UNKNOWN"


def test_complete_unversioned_legacy_v2_adoption_no_ddl(isolated):
    with psycopg.connect(isolated) as conn:
        conn.execute(SCHEMA_V1)
        conn.execute(SCHEMA_V2)
        conn.execute(
            """INSERT INTO pr_watch_pg_goals
               (user_id,goal_id,repo,number,state,phase)
               VALUES ('legacy2','g','o/r',29,'FINISHED','MERGED')"""
        )
        conn.execute(
            """INSERT INTO pr_watch_pg_launches
               (user_id,goal_id,state,evidence)
               VALUES ('legacy2','g','OBSERVED','DESCRIBE_EXISTS')"""
        )
    state = PostgresMigrations(isolated).upgrade(2, adopt_legacy=True)
    assert state.version == 2 and state.adopted
    assert len(_rows(isolated)) == 2
    store = PostgresWatchStore(isolated)
    store.validate_v2()
    assert store.goal(user_id="legacy2", goal_id="g")["phase"] == "MERGED"


def test_partial_legacy_schema_refused_without_changes(isolated):
    with psycopg.connect(isolated) as conn:
        conn.execute(
            """CREATE TABLE pr_watch_pg_goals (
               user_id TEXT NOT NULL, goal_id TEXT NOT NULL,
               PRIMARY KEY(user_id,goal_id))"""
        )
    mgr = PostgresMigrations(isolated)
    with pytest.raises(MigrationError, match="partial legacy"):
        mgr.upgrade(2, adopt_legacy=True)
    with psycopg.connect(isolated) as conn:
        assert conn.execute(
            "SELECT to_regclass('pr_watch_pg_schema_migrations')"
        ).fetchone()[0] is None
        assert conn.execute(
            "SELECT to_regclass('pr_watch_pg_launches')"
        ).fetchone()[0] is None


def test_partial_v2_unversioned_refused_even_with_complete_v1(isolated):
    with psycopg.connect(isolated) as conn:
        conn.execute(SCHEMA_V1)
        conn.execute("ALTER TABLE pr_watch_pg_goals ADD COLUMN phase TEXT")
    with pytest.raises(MigrationError, match="partially applied V2"):
        PostgresMigrations(isolated).upgrade(2, adopt_legacy=True)


def test_schema_checksum_drift_and_unknown_future_migrations_fail_closed(isolated):
    mgr = PostgresMigrations(isolated)
    mgr.upgrade(2)
    with psycopg.connect(isolated) as conn:
        conn.execute(
            "UPDATE pr_watch_pg_schema_migrations SET checksum=%s WHERE version=2",
            ("f" * 64,),
        )
    with pytest.raises(MigrationError, match="checksum drift"):
        mgr.status()
    with pytest.raises(MigrationError, match="checksum drift"):
        PostgresWatchStore(isolated).validate_v2()


def test_fault_injected_sql_rolls_back_v2_schema_and_ledger(isolated, monkeypatch):
    mgr = PostgresMigrations(isolated)
    mgr.upgrade(1)
    monkeypatch.setitem(
        migration_module.SCRIPTS, 2,
        SCHEMA_V2 + "\nSELECT * FROM this_missing_table_must_abort_migration;",
    )
    with pytest.raises(psycopg.errors.UndefinedTable):
        mgr.upgrade(2)
    assert mgr.status().version == 1
    assert [r[0] for r in _rows(isolated)] == [1]
    with psycopg.connect(isolated) as conn:
        assert conn.execute(
            "SELECT to_regclass('pr_watch_pg_controls')"
        ).fetchone()[0] is None
        assert conn.execute(
            """SELECT count(*) FROM information_schema.columns
               WHERE table_schema=current_schema()
                 AND table_name='pr_watch_pg_goals' AND column_name='phase'"""
        ).fetchone()[0] == 0


def test_two_independent_installers_are_serialized_and_idempotent(isolated):
    def upgrade(_):
        return PostgresMigrations(isolated).upgrade(2).version
    with ThreadPoolExecutor(max_workers=6) as pool:
        assert list(pool.map(upgrade, range(6))) == [2] * 6
    assert [r[0] for r in _rows(isolated)] == [1, 2]
    PostgresWatchStore(isolated).validate_v2()


def test_empty_v2_can_rollback_to_v1_then_upgrade_again(isolated):
    mgr = PostgresMigrations(isolated)
    mgr.upgrade(2)
    with pytest.raises(MigrationError, match="explicit confirm_empty"):
        mgr.rollback_empty_v2()
    assert mgr.status().version == 2
    assert mgr.rollback_empty_v2(confirm_empty=True).version == 1
    assert [r[0] for r in _rows(isolated)] == [1]
    with pytest.raises(MigrationError, match="version 2 required"):
        PostgresWatchStore(isolated).validate_v2()
    assert mgr.upgrade(2).version == 2
    PostgresWatchStore(isolated).validate_v2()


def test_rollback_data_guard_checks_all_v2_tables(isolated):
    mgr = PostgresMigrations(isolated)
    mgr.upgrade(2)
    with psycopg.connect(isolated) as conn:
        conn.execute(
            """INSERT INTO pr_watch_pg_attention_decisions
               (user_id,event_id,goal_id,disposition,reason,requires_approval)
               VALUES ('u','e','g','NOTIFY','reason',true)"""
        )
    with pytest.raises(MigrationError, match="nonempty table"):
        mgr.rollback_empty_v2(confirm_empty=True)
    assert mgr.status().version == 2


def test_drifted_physical_schema_refused_despite_valid_checksums(isolated):
    mgr = PostgresMigrations(isolated)
    mgr.upgrade(2)
    with psycopg.connect(isolated) as conn:
        conn.execute("DROP INDEX pr_watch_pg_deliveries_due")
    with pytest.raises(MigrationError, match="missing required index"):
        mgr.status()
    with pytest.raises(MigrationError, match="missing required index"):
        mgr.upgrade(2)


def test_startup_v2_requires_versioned_history_not_legacy_db(isolated):
    with psycopg.connect(isolated) as conn:
        conn.execute(SCHEMA_V1)
        conn.execute(SCHEMA_V2)
    with pytest.raises(MigrationError, match="unversioned"):
        PostgresWatchStore(isolated).validate_v2()


def test_v3_prepared_control_requests_have_atomic_cas_and_no_replay_write(isolated):
    migrations = PostgresMigrations(isolated)
    assert migrations.upgrade(2).version == 2
    original = _rows(isolated)
    assert migrations.upgrade(3).version == 3
    assert _rows(isolated)[:2] == original  # V1/V2 digests remain untouched.
    store = PostgresWatchStore(isolated)
    store.validate_v2()  # The existing runtime must accept additive V3.
    store.register(user_id="alice", goal_id="g1", repo="o/r", number=12)
    request_id = "3" * 32
    prepared = dict(
        user_id="alice", goal_id="g1", request_id=request_id,
        action="pause", expected_version=0,
    )
    assert store.prepare_control(**prepared)["state"] == "PREPARED"
    assert store.prepare_control(**prepared)["state"] == "PREPARED"
    assert store.goal(user_id="alice", goal_id="g1")["version"] == 0
    assert store.list_control_requests(user_id="bob") == []
    with pytest.raises(AdmissionError, match="not found"):
        store.control_request(user_id="bob", request_id=request_id)
    with pytest.raises(AdmissionError, match="already bound"):
        store.prepare_control(**{**prepared, "action": "cancel"})
    store.register(user_id="alice", goal_id="g2", repo="o/r", number=13)
    with pytest.raises(AdmissionError, match="already bound"):
        store.prepare_control(**{**prepared, "goal_id": "g2"})
    with pytest.raises(AdmissionError, match="prepare request"):
        store.transition_with_request(**{**prepared, "request_id": "4" * 32})
    goal, replayed = store.transition_with_request(**prepared)
    assert goal["version"] == 1 and goal["state"] == "PAUSED" and not replayed
    assert store.control_request(user_id="alice", request_id=request_id)[
        "committed_version"
    ] == 1
    goal, replayed = store.transition_with_request(**prepared)
    assert goal["version"] == 1 and replayed
    assert store.controls(user_id="alice", goal_id="g1")[0]["version"] == 1
    assert len(store.list_control_requests(user_id="alice")) == 1
    assert migrations.status().version == 3
    assert migrations.upgrade(2).version == 3  # Never downgrade on install_v2.
    assert store.control_request(user_id="alice", request_id=request_id)[
        "state"
    ] == "COMMITTED"
