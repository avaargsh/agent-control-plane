"""Negative controls: a valid JSON proof cannot stand in for durable DB rows."""

import json
import sqlite3
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

# The scripts/ directory is not installed as a Python package by hatchling;
# load the release CLI directly, without changing import paths globally.
_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "verify_v01_acceptance_artifacts.py"
)
_SPEC = spec_from_file_location("v01_acceptance_verifier", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
verify_durable_execution_records = _MODULE.verify_durable_execution_records


def _fixture(tmp_path):
    attempt = {
        "attempt_id": "attempt-01",
        "operation_id": "operation-01",
        "resource_uid": "deployment-uid-01",
        "transition_hash": "transition-01",
        "action_hash": "action-01",
        "authorization_hash": "authorization-01",
        "lease_id": "lease-01",
        "lease_epoch": 3,
        "transition_plan_hash": "plan-01",
        "plan_authorization_hash": "plan-authorization-01",
        "plan_fence_hash": "plan-fence-01",
        "authority_reservation_hash": "reservation-01",
        "attempt_hash": "attempt-hash-01",
        "state": "COMMITTED",
        "completed_at": "2026-10-13T08:00:00+00:00",
        "result_hash": "result-01",
        "result": {"plan_hash": "plan-01", "provider": "kubernetes"},
    }
    predicate = {"transition_plan": {"plan_hash": "plan-01"},
                 "execution_attempt": attempt}
    with sqlite3.connect(tmp_path / "execution-journal.db") as db:
        db.execute("""CREATE TABLE execution_attempts (
            attempt_id TEXT, operation_id TEXT, resource_uid TEXT,
            transition_hash TEXT, action_hash TEXT, authorization_hash TEXT,
            lease_id TEXT, lease_epoch INTEGER, transition_plan_hash TEXT,
            plan_authorization_hash TEXT, plan_fence_hash TEXT,
            authority_reservation_hash TEXT, attempt_hash TEXT, state TEXT,
            completed_at TEXT, result_hash TEXT, result_json TEXT
        )""")
        db.execute(
            "INSERT INTO execution_attempts VALUES (" + ",".join("?" * 17) + ")",
            (attempt["attempt_id"], attempt["operation_id"],
             attempt["resource_uid"], attempt["transition_hash"],
             attempt["action_hash"], attempt["authorization_hash"],
             attempt["lease_id"], attempt["lease_epoch"],
             attempt["transition_plan_hash"], attempt["plan_authorization_hash"],
             attempt["plan_fence_hash"], attempt["authority_reservation_hash"],
             attempt["attempt_hash"], attempt["state"],
             attempt["completed_at"], attempt["result_hash"],
             json.dumps(attempt["result"])),
        )
    with sqlite3.connect(tmp_path / "execution-leases.db") as db:
        db.execute("CREATE TABLE execution_leases (lease_id TEXT, resource_uid TEXT, epoch INTEGER)")
        db.execute("INSERT INTO execution_leases VALUES (?, ?, ?)",
                   ("lease-01", "deployment-uid-01", 3))
    with sqlite3.connect(tmp_path / "work-context.db") as db:
        db.execute("CREATE TABLE work_snapshots (work_id TEXT)")
        db.execute("""CREATE TABLE work_authority_reservations (
            reservation_hash TEXT, operation_id TEXT, resource_uid TEXT,
            lease_id TEXT, lease_epoch INTEGER, state TEXT, work_id TEXT
        )""")
        db.execute("INSERT INTO work_snapshots VALUES ('work-01')")
        db.execute("INSERT INTO work_authority_reservations VALUES (?, ?, ?, ?, ?, ?, ?)",
                   ("reservation-01", "operation-01", "deployment-uid-01",
                    "lease-01", 3, "RELEASED", "work-01"))
    return predicate


def test_v01_verified_durable_records_match_proof(tmp_path):
    predicate = _fixture(tmp_path)
    verify_durable_execution_records(tmp_path, predicate)


@pytest.mark.parametrize(
    ("database", "mutation", "expected_error"),
    [
        ("execution-journal.db", "DELETE FROM execution_attempts",
         "durable execution attempt missing"),
        ("execution-journal.db",
         "UPDATE execution_attempts SET operation_id='another-operation'",
         "durable execution journal mismatch: operation_id"),
        ("execution-journal.db",
         "UPDATE execution_attempts SET state='PREPARED'",
         "durable execution journal mismatch: state"),
        ("execution-journal.db",
         "UPDATE execution_attempts SET result_hash='fake-result'",
         "durable execution journal mismatch: result_hash"),
        ("execution-journal.db",
         "UPDATE execution_attempts SET result_json='{}'",
         "terminal result mismatch"),
        ("execution-leases.db", "DELETE FROM execution_leases",
         "durable execution lease missing or mismatched"),
        ("execution-leases.db",
         "UPDATE execution_leases SET epoch=4",
         "durable execution lease missing or mismatched"),
        ("work-context.db", "DELETE FROM work_authority_reservations",
         "durable authority reservation missing"),
        ("work-context.db",
         "UPDATE work_authority_reservations SET state='ACTIVE'",
         "durable authority reservation missing"),
        ("work-context.db", "DELETE FROM work_snapshots",
         "authority reservation has no durable work snapshot"),
    ],
)
def test_v01_missing_or_tampered_durable_evidence_fails_closed(
    tmp_path, database, mutation, expected_error
):
    predicate = _fixture(tmp_path)
    with sqlite3.connect(tmp_path / database) as db:
        db.execute(mutation)
    with pytest.raises(SystemExit, match=expected_error):
        verify_durable_execution_records(tmp_path, predicate)


def test_v01_unexpected_proof_operation_is_not_inferred_from_db(tmp_path):
    predicate = _fixture(tmp_path)
    predicate["execution_attempt"]["operation_id"] = "forged-operation"
    with pytest.raises(SystemExit, match="journal mismatch: operation_id"):
        verify_durable_execution_records(tmp_path, predicate)


def test_v01_missing_sqlite_tables_are_not_valid_durable_evidence(tmp_path):
    predicate = _fixture(tmp_path)
    with sqlite3.connect(tmp_path / "execution-journal.db") as db:
        db.execute("DROP TABLE execution_attempts")
    with pytest.raises(SystemExit, match="database validation failed"):
        verify_durable_execution_records(tmp_path, predicate)


@pytest.mark.parametrize(
    "name",
    ("execution-journal.db", "execution-leases.db", "work-context.db"),
)
def test_v01_rejects_live_sqlite_wal_sidecar_until_closed(tmp_path, name):
    """A readable WAL database is not a self-contained .db release artifact."""
    path = tmp_path / name
    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        connection.execute("CREATE TABLE wal_record (value TEXT NOT NULL)")
        connection.execute("INSERT INTO wal_record VALUES ('committed-in-wal')")
        connection.commit()
        assert path.with_name(path.name + "-wal").exists()
        with pytest.raises(SystemExit, match="has a WAL sidecar"):
            _MODULE.verify_sqlite(path, require_closed_wal=True)
        # The immutable v0.1 compatibility verifier remains permissive;
        # a historical release may carry a WAL sidecar.
        _MODULE.verify_sqlite(path)
    finally:
        connection.close()

    # Closing the final writer checkpoints the committed rows into the .db.
    assert not path.with_name(path.name + "-wal").exists()
    _MODULE.verify_sqlite(path, require_closed_wal=True)
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        assert db.execute("SELECT value FROM wal_record").fetchone()[0] == "committed-in-wal"


@pytest.mark.parametrize(
    "name",
    ("execution-journal.db", "execution-leases.db", "work-context.db"),
)
def test_v01_strict_durable_queries_do_not_recreate_wal(tmp_path, name):
    """Strict validation reads only captured .db bytes, not a new WAL reader."""
    predicate = _fixture(tmp_path)
    path = tmp_path / name
    # Convert the real fixture file into WAL mode, then checkpoint and close
    # to make a self-contained .db release artifact.
    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] == 0
    finally:
        connection.close()
    assert not path.with_name(path.name + "-wal").exists()

    for database in ("execution-journal.db", "execution-leases.db", "work-context.db"):
        _MODULE.verify_sqlite(tmp_path / database, require_closed_wal=True)
    verify_durable_execution_records(
        tmp_path, predicate, require_closed_wal=True
    )
    # Previously these three proof lookups used mode=ro even in strict mode.
    # A strict verifier must not create an omitted WAL sidecar itself.
    assert not path.with_name(path.name + "-wal").exists()


@pytest.mark.parametrize(
    "name",
    ("execution-journal.db", "execution-leases.db", "work-context.db"),
)
def test_v01_strict_rejects_symlinked_sqlite_artifacts(tmp_path, name):
    """A path to a live/external DB is not a standalone captured evidence file."""
    predicate = _fixture(tmp_path)
    path = tmp_path / name
    moved = tmp_path / ("external-" + name)
    path.rename(moved)
    path.symlink_to(moved)

    # The v0.1 published-proof compatibility path is deliberately unchanged.
    _MODULE.verify_sqlite(path)
    verify_durable_execution_records(tmp_path, predicate)

    with pytest.raises(SystemExit, match="symlink; strict evidence requires standalone"):
        _MODULE.verify_sqlite(path, require_closed_wal=True)
    with pytest.raises(SystemExit, match="symlink; strict evidence requires standalone"):
        verify_durable_execution_records(tmp_path, predicate, require_closed_wal=True)
