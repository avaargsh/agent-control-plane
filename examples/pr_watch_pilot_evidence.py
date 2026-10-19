"""Read-only evidence audit for PR Watch live-delivery pilot (issue #185).

Deliberately cannot issue a GO decision: durable ACK, Temporal History and a
provider snapshot do not establish GitHub delivery origin or reviewer signoff.
No DDL, workflow starts/signals, GitHub writes, or delivery status mutation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from uuid import UUID

from pr_watch_control_bridge import workflow_id
from pr_watch_pg_admission import _id
from pr_watch_pg_store import PostgresWatchStore
from pr_watch_pg_webhook import PostgresWebhookSchema
from pr_watch_webhook_envelope import wake_signal_id

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class EvidenceAuditError(ValueError):
    """Missing, inconsistent or unsafe scoped evidence."""


def audit_delivery(*, dsn: str, user_id: str, goal_id: str,
                   delivery: str, prove=None) -> dict:
    """Inspect a single scoped PG inbox entry, optionally read Temporal History.

    Positive proof is strictly bool True. Missing/inaccessible history is not
    negative delivery evidence or permission to send again. The caller must
    supply a trusted, read-only History witness rather than arbitrary HTTP.
    """
    if not _id(user_id) or not _id(goal_id):
        raise EvidenceAuditError("invalid canonical scope")
    try:
        canonical = str(UUID(delivery))
    except (TypeError, AttributeError, ValueError) as exc:
        raise EvidenceAuditError("invalid canonical delivery UUID") from exc
    if canonical != delivery:
        raise EvidenceAuditError("invalid canonical delivery UUID")
    if prove is not None and not callable(prove):
        raise EvidenceAuditError("History proof must be callable")
    if not isinstance(dsn, str) or not dsn.strip():
        raise EvidenceAuditError("explicit PostgreSQL DSN required")

    # Read-only validation. In particular, never call install_v2()/install().
    PostgresWebhookSchema(dsn).validate()
    store = PostgresWatchStore(dsn)
    with store._connection() as db:
        db.execute("SET TRANSACTION READ ONLY")
        goal = db.execute(
            """SELECT state,phase,version,repo,number FROM pr_watch_pg_goals
               WHERE user_id=%s AND goal_id=%s""",
            (user_id, goal_id),
        ).fetchone()
        if goal is None:
            raise EvidenceAuditError("Goal absent within selected scope")
        row = db.execute(
            """SELECT payload_sha256,signal_id,state
               FROM pr_watch_pg_webhook_wakes
               WHERE user_id=%s AND goal_id=%s AND delivery=%s""",
            (user_id, goal_id, delivery),
        ).fetchone()
        if row is None:
            raise EvidenceAuditError("delivery absent within selected scope")
        evidence = db.execute(
            """SELECT COUNT(*) AS n FROM pr_watch_pg_events
               WHERE user_id=%s AND goal_id=%s""",
            (user_id, goal_id),
        ).fetchone()
    if not _SHA256.fullmatch(row["payload_sha256"]) or (
        row["signal_id"] != wake_signal_id(user_id, goal_id, delivery)
    ):
        raise EvidenceAuditError("durable delivery evidence identity mismatch")
    if row["state"] not in ("IN_FLIGHT", "ACKED", "UNKNOWN"):
        raise EvidenceAuditError("unexpected inbox state")

    history = "NOT_CHECKED"
    if prove is not None:
        try:
            history = "PROVEN" if prove(row["signal_id"]) is True else "NOT_PROVEN"
        except Exception:
            history = "UNAVAILABLE"

    # No raw user/repo IDs, private DB credentials, webhook body or GitHub
    # token in a report intended for operator evidence and issue attachments.
    scope_digest = hashlib.sha256(json.dumps(
        [user_id, goal_id], separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return {
        "schema": "PG_V2_AND_WEBHOOK_EXTENSION_VERIFIED",
        "scope_digest": scope_digest,
        "delivery_digest": hashlib.sha256(delivery.encode("ascii")).hexdigest(),
        "workflow_digest": hashlib.sha256(workflow_id(
            user_id=user_id, goal_id=goal_id,
        ).encode("utf-8")).hexdigest(),
        "goal_state": goal["state"],
        "goal_phase": goal["phase"],
        "goal_version": goal["version"],
        "inbox_state": row["state"],
        "signal_identity": "DETERMINISTIC_MATCH",
        "temporal_history": history,
        "recorded_observations": evidence["n"],
        "observations_attributed_to_delivery": "NOT_PROVEN",
        "actual_github_delivery_origin": "NOT_ATTESTED",
        "public_ingress_security_review": "NOT_ATTESTED",
        "operator_release_approval": "NOT_ATTESTED",
        "release_decision": "NO_GO",
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only PR Watch PG/Temporal pilot evidence (always NO_GO)"
    )
    parser.add_argument("--user", required=True)
    parser.add_argument("--goal", required=True)
    parser.add_argument("--delivery", required=True)
    parser.add_argument("--temporal-address")
    parser.add_argument("--temporal-namespace", default="default")
    args = parser.parse_args()
    if (os.getenv("PR_WATCH_STORAGE", "postgres") != "postgres"
            or os.getenv("PR_WATCH_DB")):
        parser.error("audit requires exclusive PostgreSQL mode")
    dsn = os.getenv("PR_WATCH_PG_DSN", "")
    try:
        witness = None
        if args.temporal_address:
            from pr_watch_webhook import TemporalWebhookWake

            witness = TemporalWebhookWake(
                args.temporal_address, args.temporal_namespace,
                args.user, args.goal,
            ).prove
        report = audit_delivery(
            dsn=dsn, user_id=args.user, goal_id=args.goal,
            delivery=args.delivery, prove=witness,
        )
    except EvidenceAuditError as exc:
        parser.error(str(exc))
    except Exception:
        # psycopg and Temporal errors can contain private hosts/DSN details.
        # Never echo their raw diagnostics in a release-evidence CLI.
        parser.error("evidence unavailable or unverified; release remains NO_GO")
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
