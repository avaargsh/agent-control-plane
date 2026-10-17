# SPDX-License-Identifier: Apache-2.0
"""Hermetic SQLite admission-journal fault and replay tests (stdlib only)."""
import concurrent.futures
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "admission-journal.py"
spec = importlib.util.spec_from_file_location("admission_journal", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

def sample(**changes):
    data = {
        "schemaVersion": "agentledger.admission.v1",
        "ledger": "0x" + "ab" * 20,
        "taskId": "0x" + "01" * 32,
        "payer": "0x" + "cd" * 20,
        "nonce": "0x" + "02" * 32,
        "quoteDigest": "0x" + "03" * 32,
        "settlementRef": "0x" + "04" * 32,
        "transaction": "0x" + "05" * 32,
        "amountAtomic": "1000",
    }
    data.update(changes)
    return data

class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_path = Path(self.temp.name) / "ledger.db"

    def db(self):
        db = module.connect(self.db_path)
        self.addCleanup(db.close)
        return db

    def test_prepare_survives_process_restart_and_replay(self):
        first = module.prepare(self.db(), sample())
        self.assertEqual(first["result"], "PREPARED_NOT_SUBMITTED")
        reopened = self.db()
        replay = module.prepare(reopened, sample())
        self.assertEqual(replay["result"], "EXISTING_NO_NEW_SUBMISSION")
        self.assertEqual(replay["admission"]["status"], "PREPARED")
        self.assertEqual(reopened.execute("SELECT count(*) FROM admissions").fetchone()[0], 1)

    def test_conflicting_settlement_reference_refused(self):
        db = self.db()
        module.prepare(db, sample())
        with self.assertRaisesRegex(ValueError, "conflict"):
            module.prepare(db, sample(taskId="0x" + "aa" * 32, nonce="0x" + "bb" * 32,
                                     quoteDigest="0x" + "cc" * 32))
        self.assertEqual(db.execute("SELECT count(*) FROM admissions").fetchone()[0], 1)

    def test_same_task_different_charge_refused(self):
        db = self.db()
        module.prepare(db, sample())
        with self.assertRaisesRegex(ValueError, "conflict"):
            module.prepare(db, sample(settlementRef="0x" + "ab" * 32,
                                     transaction="0x" + "cd" * 32))
        self.assertEqual(db.execute("SELECT count(*) FROM admissions").fetchone()[0], 1)

    def test_nonce_and_quote_replay_cannot_cross_tasks(self):
        db = self.db()
        module.prepare(db, sample())
        for changes in [
            {"taskId": "0x" + "aa" * 32, "quoteDigest": "0x" + "bb" * 32},
            {"taskId": "0x" + "aa" * 32, "nonce": "0x" + "bb" * 32},
        ]:
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "conflict"):
                module.prepare(db, sample(settlementRef="0x" + "ab" * 32, **changes))

    def test_unknown_is_fail_closed_and_not_retryable(self):
        db = self.db()
        module.prepare(db, sample())
        result = module.mark_unknown(db, sample()["settlementRef"])
        self.assertEqual(result["admission"]["status"], "UNKNOWN")
        self.assertEqual(module.prepare(db, sample())["result"], "EXISTING_NO_NEW_SUBMISSION")
        self.assertEqual(module.mark_unknown(db, sample()["settlementRef"])["admission"]["status"], "UNKNOWN")

    def test_invalid_input_cannot_mutate(self):
        db = self.db()
        for changes in [
            {"amountAtomic": "0"}, {"amountAtomic": "-1"}, {"amountAtomic": 1000},
            {"nonce": "0x123"}, {"ledger": "nope"},
            {"schemaVersion": "unexpected"}, {"amountAtomic": str(2 ** 256)},
        ]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                module.prepare(db, sample(**changes))
        self.assertEqual(db.execute("SELECT count(*) FROM admissions").fetchone()[0], 0)

    def test_concurrent_processes_have_one_new_admission(self):
        env = os.environ.copy()
        env["AGENTLEDGER_JOURNAL_DB"] = str(self.db_path)
        cmd = [sys.executable, str(SCRIPT), "prepare"]
        def submit(_):
            return subprocess.run(cmd, input=json.dumps(sample()), text=True,
                                  capture_output=True, env=env, timeout=10)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(submit, range(8)))
        self.assertTrue(all(x.returncode == 0 for x in results),
                        [(x.returncode, x.stderr) for x in results])
        labels = [json.loads(x.stdout)["result"] for x in results]
        self.assertEqual(labels.count("PREPARED_NOT_SUBMITTED"), 1)
        self.assertEqual(labels.count("EXISTING_NO_NEW_SUBMISSION"), 7)
        self.assertEqual(self.db().execute("SELECT count(*) FROM admissions").fetchone()[0], 1)

if __name__ == "__main__":
    unittest.main()
