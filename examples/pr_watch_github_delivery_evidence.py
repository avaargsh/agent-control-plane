"""Read-only GitHub delivery provenance joined to PR Watch PG/Temporal evidence.

This is a *corroboration*, not a cryptographic raw-byte signature proof,
authorization to deploy, or evidence of a successful human review.
Only GET requests to the fixed GitHub REST API host are permitted.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import (
    HTTPRedirectHandler, Request, build_opener,
)
from uuid import UUID

from pr_watch_pg_store import PostgresWatchStore
from pr_watch_pilot_evidence import EvidenceAuditError, audit_delivery
from pr_watch_webhook_envelope import ACTIONS

_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_TOKEN = re.compile(r"^[^\s\x00-\x1f\x7f]{20,2048}$")
_EVENTS = frozenset(("pull_request", "check_run", "check_suite"))
_API_VERSION = "2022-11-28"
_MAX_JSON = 2_000_000


class _DenyRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _positive(value) -> bool:
    return type(value) is int and value > 0


def _canonical_guid(value) -> bool:
    try:
        return isinstance(value, str) and str(UUID(value)) == value
    except (TypeError, ValueError, AttributeError):
        return False


def _endpoint(value: str) -> bool:
    if not isinstance(value, str) or len(value) > 2048:
        return False
    parts = urlsplit(value)
    try:
        port = parts.port
    except ValueError:
        return False
    return (
        parts.scheme == "https" and bool(parts.hostname)
        and parts.username is None and parts.password is None
        and parts.query == "" and parts.fragment == ""
        and parts.path == "/webhooks/github"
        and (port is None or 1 <= port <= 65535)
    )


def _headers(value: object) -> dict[str, str]:
    """Reject case-colliding provider headers rather than choose one."""
    if not isinstance(value, dict):
        raise EvidenceAuditError("GitHub request headers missing")
    normalized = {}
    for key, val in value.items():
        if not isinstance(key, str) or not isinstance(val, str):
            raise EvidenceAuditError("malformed GitHub request headers")
        name = key.lower()
        if name in normalized:
            raise EvidenceAuditError("ambiguous GitHub request headers")
        normalized[name] = val
    return normalized


def verify_delivery_record(
    *, record: dict, repository: dict, repo: str, number: int,
    guid: str, hook_id: int, delivery_id: int, expected_endpoint: str,
) -> dict:
    """Validate independent GitHub API metadata against a persisted Goal.

    The JSON payload returned by the GitHub API may be reserialized: it is
    NEVER substituted for the original signed HTTP body in HMAC verification.
    """
    if (
        not isinstance(repo, str) or not _REPO.fullmatch(repo)
        or type(number) is not int or number < 1
        or not _canonical_guid(guid)
        or not _positive(hook_id) or not _positive(delivery_id)
        or not _endpoint(expected_endpoint)
    ):
        raise EvidenceAuditError("invalid delivery verification scope")
    if not isinstance(repository, dict) or not isinstance(record, dict):
        raise EvidenceAuditError("GitHub provider record unavailable")
    expected_id = repository.get("id")
    if (
        not _positive(expected_id)
        or repository.get("full_name") != repo
        or record.get("id") != delivery_id
        or record.get("guid") != guid
        or record.get("repository_id") != expected_id
        or record.get("url") != expected_endpoint
    ):
        raise EvidenceAuditError("GitHub delivery identity mismatch")
    event = record.get("event")
    action = record.get("action")
    if event not in _EVENTS or not isinstance(action, str):
        raise EvidenceAuditError("GitHub event unsupported")
    if event == "pull_request":
        if action not in ACTIONS:
            raise EvidenceAuditError("GitHub PR action not watched")
    elif action != "completed":
        raise EvidenceAuditError("GitHub check action not watched")

    request = record.get("request")
    response = record.get("response")
    if not isinstance(request, dict) or not isinstance(response, dict):
        raise EvidenceAuditError("GitHub request/response evidence missing")
    headers = _headers(request.get("headers"))
    if (
        headers.get("x-github-delivery") != guid
        or headers.get("x-github-event") != event
        or headers.get("x-github-hook-id") != str(hook_id)
        or not re.fullmatch(
            r"sha256=[0-9a-fA-F]{64}",
            headers.get("x-hub-signature-256", ""),
        )
    ):
        raise EvidenceAuditError("GitHub delivery request headers mismatched")

    payload = request.get("payload")
    if not isinstance(payload, dict):
        raise EvidenceAuditError("GitHub delivery event payload missing")
    source = payload.get("repository")
    if not isinstance(source, dict) or (
        source.get("full_name") != repo or source.get("id") != expected_id
    ):
        raise EvidenceAuditError("GitHub payload repository binding mismatch")
    if event == "pull_request":
        pull = payload.get("pull_request")
        if (
            type(payload.get("number")) is not int
            or payload["number"] != number
            or not isinstance(pull, dict)
            or type(pull.get("number")) is not int
            or pull["number"] != number
            or payload.get("action") != action
        ):
            raise EvidenceAuditError("GitHub payload PR binding mismatch")
    else:
        check = payload.get(event)
        if not isinstance(check, dict) or payload.get("action") != action:
            raise EvidenceAuditError("GitHub check payload invalid")
        related = check.get("pull_requests")
        if (
            not isinstance(related, list)
            or any(
                not isinstance(p, dict)
                or type(p.get("number")) is not int
                for p in related
            )
            or not any(p["number"] == number for p in related)
        ):
            raise EvidenceAuditError("GitHub check PR association missing")

    # An HTTP 202 "ignored" response is NOT proof the receiver accepted.
    status = record.get("status_code")
    body = response.get("payload")
    if isinstance(body, str):
        if len(body) > 8192:
            raise EvidenceAuditError("GitHub response exceeds limit")
        try:
            body = json.loads(body)
        except ValueError as exc:
            raise EvidenceAuditError("GitHub response not JSON") from exc
    if (
        status not in (200, 202) or type(status) is not int
        or not isinstance(body, dict)
        or body.get("status") != ("accepted" if status == 202 else "duplicate")
        or body.get("signal_state") not in ("ACKED", "UNKNOWN", "IN_FLIGHT")
    ):
        raise EvidenceAuditError("receiver HTTP acknowledgement not verified")
    if type(record.get("redelivery")) is not bool:
        raise EvidenceAuditError("GitHub redelivery metadata absent")
    return {
        "provider": "GITHUB_REPOSITORY_WEBHOOK_API",
        "github_delivery_record": "MATCHED",
        "repository_id": "GITHUB_ID_CORROBORATED",
        "delivery_guid": "GITHUB_GUID_MATCHED",
        "webhook_endpoint": "HTTPS_DESTINATION_MATCHED",
        "pr_association": "EXACT_SCOPED_PR_MATCHED",
        "receiver_http_ack": "RESPONSE_MATCHED",
        "redelivery": record["redelivery"],
        "raw_hmac_body_equivalence": "NOT_ATTESTED_BY_API",
    }


def _read_json(*, repo: str, path: str, token: str) -> dict:
    if not isinstance(token, str) or not _TOKEN.fullmatch(token):
        raise EvidenceAuditError("GitHub webhook read token required")
    if not isinstance(repo, str) or not _REPO.fullmatch(repo):
        raise EvidenceAuditError("invalid repository selector")
    owner, name = repo.split("/", 1)
    url = ("https://api.github.com/repos/" + quote(owner, safe="")
           + "/" + quote(name, safe="") + path)
    request = Request(
        url, method="GET",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + token,
            "X-GitHub-Api-Version": _API_VERSION,
            "User-Agent": "acp-pr-watch-delivery-audit/0.1",
            "Cache-Control": "no-cache",
        },
    )
    try:
        with build_opener(_DenyRedirect()).open(request, timeout=10) as res:
            if res.status != 200:
                raise EvidenceAuditError("GitHub evidence not available")
            data = res.read(_MAX_JSON + 1)
        if len(data) > _MAX_JSON:
            raise EvidenceAuditError("GitHub evidence exceeds size bound")
        parsed = json.loads(data)
        if not isinstance(parsed, dict):
            raise EvidenceAuditError("GitHub evidence not a JSON object")
        return parsed
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        if isinstance(exc, EvidenceAuditError):
            raise
        # Never leak token/hostname/response body in a user-facing CLI.
        raise EvidenceAuditError("GitHub evidence unavailable or unverified") from None


def audit_github_delivery(
    *, dsn: str, user_id: str, goal_id: str, guid: str,
    hook_id: int, delivery_id: int, expected_endpoint: str,
    token: str, prove=None, fetch_json=None,
) -> dict:
    """Read both PG and GitHub API, never change webhook registration/state."""
    if not _positive(hook_id) or not _positive(delivery_id) or (
        not _endpoint(expected_endpoint)
    ):
        raise EvidenceAuditError("invalid hook/delivery/HTTPS endpoint")
    # DB and History proof are read-only; existence and identity must be
    # checked before making any provider requests.
    report = audit_delivery(
        dsn=dsn, user_id=user_id, goal_id=goal_id,
        delivery=guid, prove=prove,
    )
    store = PostgresWatchStore(dsn)
    goal = store.goal(user_id=user_id, goal_id=goal_id)
    repo, number = goal["repo"], goal["number"]
    if fetch_json is None:
        def fetch_json(path):
            return _read_json(repo=repo, path=path, token=token)
    repository = fetch_json("")
    record = fetch_json(
        "/hooks/" + str(hook_id) + "/deliveries/" + str(delivery_id)
    )
    matched = verify_delivery_record(
        record=record, repository=repository, repo=repo,
        number=number, guid=guid, hook_id=hook_id,
        delivery_id=delivery_id, expected_endpoint=expected_endpoint,
    )
    report.update(matched)
    report["actual_github_delivery_origin"] = "GITHUB_API_RECORD_CORROBORATED"
    # GitHub API request.payload is parsed JSON; exact raw bytes cannot be
    # reconstructed. This is not HMAC re-verification, operator signoff or GO.
    report["release_decision"] = "NO_GO"
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Corroborate GitHub webhook delivery vs PG Inbox (never GO)"
    )
    parser.add_argument("--user", required=True)
    parser.add_argument("--goal", required=True)
    parser.add_argument("--delivery", required=True, help="GitHub delivery GUID")
    parser.add_argument("--hook-id", type=int, required=True)
    parser.add_argument("--github-delivery-id", type=int, required=True)
    parser.add_argument("--expected-endpoint", required=True)
    parser.add_argument("--temporal-address")
    parser.add_argument("--temporal-namespace", default="default")
    args = parser.parse_args()
    if (
        os.getenv("PR_WATCH_STORAGE", "postgres") != "postgres"
        or os.getenv("PR_WATCH_DB")
    ):
        parser.error("exclusive PostgreSQL mode required")
    try:
        prove = None
        if args.temporal_address:
            from pr_watch_webhook import TemporalWebhookWake
            prove = TemporalWebhookWake(
                args.temporal_address, args.temporal_namespace,
                args.user, args.goal,
            ).prove
        report = audit_github_delivery(
            dsn=os.getenv("PR_WATCH_PG_DSN", ""),
            user_id=args.user, goal_id=args.goal,
            guid=args.delivery, hook_id=args.hook_id,
            delivery_id=args.github_delivery_id,
            expected_endpoint=args.expected_endpoint,
            token=os.getenv("PR_WATCH_GITHUB_HOOK_READ_TOKEN", ""),
            prove=prove,
        )
    except EvidenceAuditError as exc:
        parser.error(str(exc))
    except Exception:
        parser.error("evidence unavailable or unverified; release remains NO_GO")
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
