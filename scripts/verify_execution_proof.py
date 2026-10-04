#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_control_plane.independent_verifier import (
    verify_execution_proof_mapping,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify a serialized IndependentExecutionProof without the "
            "original Agent session, workflow history, or execution process."
        )
    )
    parser.add_argument("proof")
    parser.add_argument(
        "--expected-hash-file",
        help=(
            "Optional file containing the trusted statement hash produced "
            "out of band by the execution process."
        ),
    )
    args = parser.parse_args()

    proof_path = Path(args.proof)
    payload = json.loads(proof_path.read_text(encoding="utf-8"))

    expected_hash = None
    if args.expected_hash_file:
        expected_hash = Path(args.expected_hash_file).read_text(
            encoding="utf-8"
        ).strip()
        if not expected_hash:
            raise SystemExit("expected hash file is empty")

    proof = verify_execution_proof_mapping(
        payload,
        expected_statement_hash=expected_hash,
    )
    print(
        json.dumps(
            {
                "proof_id": proof.proof_id,
                "predicate_type": proof.predicate_type,
                "statement_hash": proof.statement_hash,
                "subject": proof.subject,
                "verified": True,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
