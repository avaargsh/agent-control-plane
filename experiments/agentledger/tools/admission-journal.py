#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Durable, non-custodial recorder *candidate* journal.

This DB is not a payment authority. Only call 'prepare' after independent
live RPC receipt, EIP-712 payer signature, task and quote checks. It never
sends a transaction or concludes that a payment is final.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time

HEX32 = re.compile(r"^0x[0-9a-f]{64}$")
ADDRESS = re.compile(r"^0x[0-9a-f]{40}$")
FIELDS = {
    "schemaVersion", "ledger", "taskId", "payer", "nonce",
    "quoteDigest", "settlementRef", "transaction", "amountAtomic",
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS admissions (
    settlement_ref TEXT PRIMARY KEY,
    ledger TEXT NOT NULL,
    task_id TEXT NOT NULL,
    payer TEXT NOT NULL,
    nonce TEXT NOT NULL,
    quote_digest TEXT NOT NULL,
    transaction_hash TEXT NOT NULL,
    amount_atomic TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('PREPARED', 'UNKNOWN')),
    prepared_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (ledger, task_id),
    UNIQUE (ledger, payer, nonce),
    UNIQUE (ledger, quote_digest)
);
"""

def validate(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ValueError("candidate must have the exact supported fields")
    if value["schemaVersion"] != "agentledger.admission.v1":
        raise ValueError("unknown candidate schema")
    for field in ("settlementRef", "taskId", "nonce", "quoteDigest", "transaction"):
        if not isinstance(value[field], str) or not HEX32.fullmatch(value[field]):
            raise ValueError(f"invalid {field}")
    for field in ("ledger", "payer"):
        if not isinstance(value[field], str) or not ADDRESS.fullmatch(value[field]):
            raise ValueError(f"invalid {field}")
    amount = value["amountAtomic"]
    if not isinstance(amount, str) or not re.fullmatch(r"[1-9][0-9]*", amount):
        raise ValueError("amountAtomic must be a positive integer string")
    if int(amount) >= 2 ** 256:
        raise ValueError("amountAtomic overflow")
    return value

def connect(filename: Path) -> sqlite3.Connection:
    filename.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(filename), timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=10000")
    # Schema/WAL initialization can return SQLITE_BUSY on simultaneous process
    # startup, even before BEGIN IMMEDIATE obtains its transaction lock.
    for attempt in range(40):
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute(SCHEMA)
            return db
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt == 39:
                db.close()
                raise
            time.sleep(0.05)
    raise AssertionError("unreachable")

def row_dict(row: sqlite3.Row) -> dict:
    return dict(row)

def prepare(db: sqlite3.Connection, candidate: dict) -> dict:
    value = validate(candidate)
    db.execute("BEGIN IMMEDIATE")
    try:
        current = db.execute(
            "SELECT * FROM admissions WHERE settlement_ref=? OR "
            "(ledger=? AND task_id=?) OR "
            "(ledger=? AND payer=? AND nonce=?) OR "
            "(ledger=? AND quote_digest=?)",
            (value["settlementRef"], value["ledger"], value["taskId"],
             value["ledger"], value["payer"], value["nonce"],
             value["ledger"], value["quoteDigest"]),
        ).fetchall()
        if current:
            if len(current) != 1:
                raise ValueError("conflicting existing admissions")
            row = row_dict(current[0])
            expected = {
                "settlement_ref": value["settlementRef"],
                "ledger": value["ledger"], "task_id": value["taskId"],
                "payer": value["payer"], "nonce": value["nonce"],
                "quote_digest": value["quoteDigest"],
                "transaction_hash": value["transaction"],
                "amount_atomic": value["amountAtomic"],
            }
            if any(row[key] != val for key, val in expected.items()):
                raise ValueError("conflict: task, nonce, approval or settlement reference already reserved")
            result = {"result": "EXISTING_NO_NEW_SUBMISSION", "admission": row}
        else:
            db.execute(
                "INSERT INTO admissions "
                "(settlement_ref,ledger,task_id,payer,nonce,quote_digest,transaction_hash,amount_atomic,status) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (value["settlementRef"], value["ledger"], value["taskId"],
                 value["payer"], value["nonce"], value["quoteDigest"],
                 value["transaction"], value["amountAtomic"], "PREPARED"),
            )
            row = db.execute("SELECT * FROM admissions WHERE settlement_ref=?",
                             (value["settlementRef"],)).fetchone()
            result = {"result": "PREPARED_NOT_SUBMITTED", "admission": row_dict(row)}
        db.execute("COMMIT")
        return result
    except Exception:
        db.execute("ROLLBACK")
        raise

def mark_unknown(db: sqlite3.Connection, settlement_ref: str) -> dict:
    if not isinstance(settlement_ref, str) or not HEX32.fullmatch(settlement_ref):
        raise ValueError("invalid settlement reference")
    db.execute("BEGIN IMMEDIATE")
    try:
        row = db.execute("SELECT * FROM admissions WHERE settlement_ref=?",
                         (settlement_ref,)).fetchone()
        if row is None:
            raise ValueError("admission not found")
        if row["status"] == "PREPARED":
            db.execute("UPDATE admissions SET status='UNKNOWN' WHERE settlement_ref=? "
                       "AND status='PREPARED'", (settlement_ref,))
        row = db.execute("SELECT * FROM admissions WHERE settlement_ref=?",
                         (settlement_ref,)).fetchone()
        db.execute("COMMIT")
        return {"result": "RECONCILE_ONLY_NO_RESUBMIT", "admission": row_dict(row)}
    except Exception:
        db.execute("ROLLBACK")
        raise

def main(args: list[str]) -> None:
    if len(args) not in (1, 2) or args[0] not in ("prepare", "inspect", "mark-unknown"):
        raise ValueError("usage: admission-journal.py prepare | inspect REF | mark-unknown REF")
    if args[0] == "prepare" and len(args) != 1:
        raise ValueError("prepare only accepts JSON from stdin")
    if args[0] != "prepare" and len(args) != 2:
        raise ValueError("inspect/mark-unknown requires exactly one settlement ref")
    path = Path(os.environ.get("AGENTLEDGER_JOURNAL_DB", "receipts/admission.db"))
    with connect(path) as db:
        if args[0] == "prepare":
            data = json.load(sys.stdin)
            output = prepare(db, data)
        else:
            ref = args[1]
            if not HEX32.fullmatch(ref):
                raise ValueError("invalid settlement reference")
            if args[0] == "mark-unknown":
                output = mark_unknown(db, ref)
            else:
                row = db.execute("SELECT * FROM admissions WHERE settlement_ref=?", (ref,)).fetchone()
                if row is None:
                    raise ValueError("admission not found")
                output = {"result": "READ_ONLY", "admission": row_dict(row)}
    print(json.dumps(output, sort_keys=True))

if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except (ValueError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(f"JOURNAL REFUSED: {exc}", file=sys.stderr)
        sys.exit(1)
