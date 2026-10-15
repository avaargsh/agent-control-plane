# SPDX-License-Identifier: Apache-2.0
"""Python unit tests for AgentLedger SQLite admission journal."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from agentledger_journal.admission import prepare, mark_unknown, connect


class AdmissionJournalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = Path(self.tmp) / "admission.db"
        self.db = connect(self.db_path)

    def tearDown(self):
        self.db.close()

    def _candidate(self, **overrides):
        base = {
            "schemaVersion": "agentledger.admission.v1",
            "ledger": "0x" + "11" * 20,
            "taskId": "0x" + "01" * 32,
            "payer": "0x" + "22" * 20,
            "nonce": "0x" + "33" * 32,
            "quoteDigest": "0x" + "44" * 32,
            "settlementRef": "0x" + "55" * 32,
            "transaction": "0x" + "66" * 32,
            "amountAtomic": "1000",
        }
        base.update(overrides)
        return base

    def test_prepare_creates_admission(self):
        result = prepare(self.db, self._candidate())
        self.assertEqual(result["result"], "PREPARED_NOT_SUBMITTED")
        self.assertEqual(result["admission"]["status"], "PREPARED")

    def test_prepare_duplicate_conflict(self):
        prepare(self.db, self._candidate())
        # Same candidate -> idempotent, no error
        result = prepare(self.db, self._candidate())
        self.assertEqual(result["result"], "EXISTING_NO_NEW_SUBMISSION")
        # Same settlementRef, different amount -> conflict
        with self.assertRaises(ValueError):
            prepare(self.db, self._candidate(amountAtomic="2000"))

    def test_prepare_same_settlement_ref_different_fields_conflict(self):
        prepare(self.db, self._candidate())
        with self.assertRaises(ValueError):
            prepare(self.db, self._candidate(
                settlementRef="0x" + "55" * 32,
                amountAtomic="2000",
            ))

    def test_mark_unknown_transitions(self):
        prepare(self.db, self._candidate())
        result = mark_unknown(self.db, "0x" + "55" * 32)
        self.assertEqual(result["result"], "RECONCILE_ONLY_NO_RESUBMIT")
        self.assertEqual(result["admission"]["status"], "UNKNOWN")

    def test_mark_unknown_twice_is_idempotent(self):
        prepare(self.db, self._candidate())
        mark_unknown(self.db, "0x" + "55" * 32)
        result = mark_unknown(self.db, "0x" + "55" * 32)
        self.assertEqual(result["admission"]["status"], "UNKNOWN")

    def test_invalid_settlement_ref_rejected(self):
        with self.assertRaises(ValueError):
            mark_unknown(self.db, "not-a-hex32")

    def test_invalid_candidate_rejected(self):
        bad = self._candidate()
        del bad["schemaVersion"]
        with self.assertRaises(ValueError):
            prepare(self.db, bad)


if __name__ == "__main__":
    unittest.main()
