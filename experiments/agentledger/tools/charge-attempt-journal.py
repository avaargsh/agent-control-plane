#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fail-closed, local pre-charge admission of one x402 authorization.

A row is written BEFORE invoking the x402 settlement middleware. Any later
request for the same task, quote nonce or EIP-3009 nonce is refused, including
identical retries. Inspect/reconcile manually after crashes or lost ACKs.
Never interprets a reservation as a completed charge or final settlement.
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
ADDR = re.compile(r"^0x[0-9a-f]{40}$")
FIELDS = {
    "schemaVersion", "ledger", "taskId", "payer", "approvalNonce",
    "paymentNonce", "quoteDigest", "payloadDigest",
}
DDL = """
CREATE TABLE IF NOT EXISTS charge_attempts (
    ledger TEXT NOT NULL,
    task_id TEXT NOT NULL,
    payer TEXT NOT NULL,
    approval_nonce TEXT NOT NULL,
    payment_nonce TEXT PRIMARY KEY,
    quote_digest TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('RESERVED_RECONCILE_ONLY')),
    recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE(ledger, task_id),
    UNIQUE(ledger, payer, approval_nonce),
    UNIQUE(ledger, quote_digest)
);
"""

def validate(payload):
    if not isinstance(payload, dict) or set(payload) != FIELDS:
        raise ValueError("invalid reservation shape")
    if payload["schemaVersion"] != "agentledger.precharge.v1":
        raise ValueError("invalid reservation schema")
    for field in ("taskId", "approvalNonce", "paymentNonce", "quoteDigest", "payloadDigest"):
        if not isinstance(payload[field], str) or not HEX32.fullmatch(payload[field]):
            raise ValueError("invalid " + field)
    for field in ("ledger", "payer"):
        if not isinstance(payload[field], str) or not ADDR.fullmatch(payload[field]):
            raise ValueError("invalid " + field)
    return payload

def connect(db_path):
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path), timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=10000")
    for n in range(40):
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute(DDL)
            return db
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or n == 39:
                db.close()
                raise
            time.sleep(0.05)
    raise RuntimeError("unreachable")

def reserve(db, payload):
    value = validate(payload)
    db.execute("BEGIN IMMEDIATE")
    try:
        conflict = db.execute("""
            SELECT payment_nonce, task_id, approval_nonce, quote_digest
            FROM charge_attempts
            WHERE payment_nonce=? OR (ledger=? AND task_id=?)
               OR (ledger=? AND payer=? AND approval_nonce=?)
               OR (ledger=? AND quote_digest=?)
        """, (value["paymentNonce"], value["ledger"], value["taskId"],
              value["ledger"], value["payer"], value["approvalNonce"],
              value["ledger"], value["quoteDigest"])).fetchone()
        if conflict is not None:
            db.execute("ROLLBACK")
            return {
                "result": "BLOCKED_EXISTING_ATTEMPT_NO_RECHARGE",
                "status": "RECONCILE_ONLY",
            }
        db.execute("""
            INSERT INTO charge_attempts
            (ledger,task_id,payer,approval_nonce,payment_nonce,quote_digest,payload_digest,status)
            VALUES (?,?,?,?,?,?,?,'RESERVED_RECONCILE_ONLY')
        """, (value["ledger"], value["taskId"], value["payer"],
              value["approvalNonce"], value["paymentNonce"],
              value["quoteDigest"], value["payloadDigest"]))
        db.execute("COMMIT")
        return {
            "result": "RESERVED_FIRST_ATTEMPT",
            "status": "RECONCILE_ONLY_AFTER_THIS_ATTEMPT",
        }
    except Exception:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise

def inspect(db, payment_nonce):
    if not isinstance(payment_nonce, str) or not HEX32.fullmatch(payment_nonce):
        raise ValueError("invalid payment nonce")
    row = db.execute("SELECT * FROM charge_attempts WHERE payment_nonce=?",
                     (payment_nonce,)).fetchone()
    if row is None:
        raise ValueError("no precharge reservation exists for this authorization")
    return {"result": "PRECHARGE_RECORD_FOUND", "admission": dict(row)}

def main(args):
    if args != ["reserve"] and not (len(args) == 2 and args[0] == "inspect"):
        raise ValueError("usage: charge-attempt-journal.py reserve | inspect PAYMENT_NONCE")
    path = os.environ.get("AGENTLEDGER_CHARGE_DB", "receipts/charge-attempts.db")
    with connect(path) as db:
        result = reserve(db, json.load(sys.stdin)) if args == ["reserve"] else inspect(db, args[1])
    print(json.dumps(result, sort_keys=True))
    # Conflict must be an error, otherwise middleware may settle again.
    if args == ["reserve"] and result["result"] != "RESERVED_FIRST_ATTEMPT":
        sys.exit(3)

if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except (ValueError, sqlite3.Error, json.JSONDecodeError) as exc:
        print("PRECHARGE REFUSED: " + str(exc), file=sys.stderr)
        sys.exit(1)
