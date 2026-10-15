# SPDX-License-Identifier: Apache-2.0
"""The exact payment-attempt authorization is single-writer across processes."""
import concurrent.futures
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "charge-attempt-journal.py"
spec = importlib.util.spec_from_file_location("charge_attempt_journal", SCRIPT)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

def candidate(**updates):
    value = {
        "schemaVersion": "agentledger.precharge.v1",
        "ledger": "0x" + "11" * 20,
        "taskId": "0x" + "22" * 32,
        "payer": "0x" + "33" * 20,
        "approvalNonce": "0x" + "44" * 32,
        "paymentNonce": "0x" + "55" * 32,
        "quoteDigest": "0x" + "66" * 32,
        "payloadDigest": "0x" + "77" * 32,
    }
    value.update(updates)
    return value

class ChargeAttemptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "charge.db"

    def db(self):
        db = m.connect(self.path)
        self.addCleanup(db.close)
        return db

    def test_same_payload_or_distinct_payment_nonce_never_authorized_twice(self):
        db = self.db()
        self.assertEqual(m.reserve(db, candidate())["result"], "RESERVED_FIRST_ATTEMPT")
        self.assertEqual(m.reserve(db, candidate())["result"], "BLOCKED_EXISTING_ATTEMPT_NO_RECHARGE")
        self.assertEqual(m.reserve(db, candidate(paymentNonce="0x" + "99" * 32))["result"],
                         "BLOCKED_EXISTING_ATTEMPT_NO_RECHARGE")
        self.assertEqual(db.execute("SELECT count(*) FROM charge_attempts").fetchone()[0], 1)

    def test_payment_nonce_cannot_be_reused_on_different_task(self):
        db = self.db()
        m.reserve(db, candidate())
        self.assertEqual(m.reserve(db, candidate(
            taskId="0x" + "aa" * 32,approvalNonce="0x" + "bb" * 32,
            quoteDigest="0x" + "cc" * 32))["result"], "BLOCKED_EXISTING_ATTEMPT_NO_RECHARGE")

    def test_quote_nonce_cannot_authorize_new_task(self):
        db = self.db()
        m.reserve(db, candidate())
        self.assertEqual(m.reserve(db, candidate(
            taskId="0x" + "aa" * 32,paymentNonce="0x" + "bb" * 32,
            quoteDigest="0x" + "cc" * 32))["result"], "BLOCKED_EXISTING_ATTEMPT_NO_RECHARGE")

    def test_invalid_reservation_stays_out_of_database(self):
        db = self.db()
        for patch in [{"paymentNonce":"0x123"}, {"payer":"not address"},
                      {"schemaVersion":"bad"}, {"surprise":True}]:
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                m.reserve(db,candidate(**patch))
        self.assertEqual(db.execute("SELECT count(*) FROM charge_attempts").fetchone()[0], 0)

    def test_concurrent_processes_only_one_can_enter_payment_middleware(self):
        env = os.environ.copy()
        env["AGENTLEDGER_CHARGE_DB"] = str(self.path)
        cmd = [sys.executable, str(SCRIPT), "reserve"]
        def call(_):
            return subprocess.run(cmd,input=json.dumps(candidate()),text=True,
                                  capture_output=True,timeout=10,env=env)
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            outputs = list(pool.map(call, range(10)))
        codes = [p.returncode for p in outputs]
        self.assertEqual(codes.count(0),1,[(p.returncode,p.stdout,p.stderr) for p in outputs])
        self.assertEqual(codes.count(3),9,[(p.returncode,p.stdout,p.stderr) for p in outputs])
        self.assertEqual(self.db().execute("SELECT count(*) FROM charge_attempts").fetchone()[0],1)

if __name__ == "__main__":
    unittest.main()
