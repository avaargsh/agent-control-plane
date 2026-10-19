"""GitHub REST read-only observer; untrusted PR prose never enters policy."""
from __future__ import annotations

import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from pr_watch_harness import PullSnapshot, WatchError

_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_HEAD_SHA = re.compile(r"^[0-9a-fA-F]{40}$")
_BAD = {"failure", "timed_out", "cancelled", "action_required", "startup_failure", "error"}
_GOOD = {"success", "neutral", "skipped"}


class ReadOnlyGitHubError(RuntimeError):
    pass


class GitHubReadOnlyReader:
    def __init__(self, *, token: str, timeout: float = 15) -> None:
        if not token or not token.strip():
            raise ReadOnlyGitHubError("a GitHub read-only token is required")
        self._token = token.strip()
        self._timeout = timeout

    @classmethod
    def from_environment(cls) -> "GitHubReadOnlyReader":
        return cls(token=os.getenv("GITHUB_TOKEN", ""))

    def _get(self, path: str) -> object:
        # Fixed origin: provider response text cannot inject a network destination.
        request = Request(
            "https://api.github.com" + path,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "acp-pr-watch-reference/0.1",
                "X-GitHub-Api-Version": "2022-11-28",
                "Cache-Control": "no-cache",
            },
            method="GET",
        )
        try:
            with urlopen(request, timeout=self._timeout) as response:
                if 'rel="next"' in response.headers.get("Link", ""):
                    raise ReadOnlyGitHubError("paginated GitHub response; cannot decide safely")
                data = response.read(2_000_001)
                if len(data) > 2_000_000:
                    raise ReadOnlyGitHubError("GitHub response exceeds bounded size")
        except (HTTPError, URLError, TimeoutError) as exc:
            # Don't echo API body, URL or Authorization header into exceptions.
            raise ReadOnlyGitHubError("GitHub read-only observation unavailable") from exc
        try:
            return json.loads(data)
        except (ValueError, UnicodeError) as exc:
            raise ReadOnlyGitHubError("GitHub supplied invalid JSON") from exc

    def fetch(self, repo: str, number: int) -> PullSnapshot:
        if not isinstance(repo, str) or not _REPO.fullmatch(repo) or type(number) is not int or number < 1:
            raise WatchError("invalid GitHub PR selector")
        owner, name = repo.split("/", 1)
        # quote() never escapes periods. Reject dot segments before building
        # a path, rather than trusting URL/client normalization.
        if owner in (".", "..") or name in (".", ".."):
            raise WatchError("invalid GitHub PR selector")
        prefix = "/repos/" + quote(owner, safe="") + "/" + quote(name, safe="")
        pr = self._get(f"{prefix}/pulls/{number}")
        if not isinstance(pr, dict) or not isinstance(pr.get("head"), dict):
            raise ReadOnlyGitHubError("invalid PR response")
        head = pr["head"].get("sha")
        # This value comes from an external provider response and is later
        # interpolated into REST paths. Fail closed before constructing any
        # check/status request if it is not an exact Git object SHA-1.
        if not isinstance(head, str) or not _HEAD_SHA.fullmatch(head):
            raise ReadOnlyGitHubError("PR has invalid head revision")
        if pr.get("state") == "open":
            state = "OPEN"
        elif pr.get("state") == "closed" and pr.get("merged") is True:
            state = "MERGED"
        elif pr.get("state") == "closed" and pr.get("merged") is False:
            state = "CLOSED"
        else:
            raise ReadOnlyGitHubError("unverifiable PR lifecycle status")
        if state != "OPEN":
            return PullSnapshot(head_sha=head, state=state, ci="UNKNOWN")

        checks = self._get(f"{prefix}/commits/{head}/check-runs?per_page=100")
        statuses = self._get(f"{prefix}/commits/{head}/status")
        reviews = self._get(f"{prefix}/pulls/{number}/reviews?per_page=100")
        if not isinstance(checks, dict) or not isinstance(statuses, dict) or not isinstance(reviews, list):
            raise ReadOnlyGitHubError("incomplete status or review response")
        runs = checks.get("check_runs")
        status_contexts = statuses.get("statuses")
        if not isinstance(runs, list) or not isinstance(status_contexts, list):
            raise ReadOnlyGitHubError("invalid GitHub checks/status shape")
        if checks.get("total_count") != len(runs):
            raise ReadOnlyGitHubError("unobserved check runs; cannot infer CI green")
        if len(reviews) >= 100 or len(status_contexts) >= 100:
            raise ReadOnlyGitHubError("ambiguous review/status pagination")

        failed = []
        pending = False
        succeeded = 0
        for item in runs:
            if not isinstance(item, dict):
                raise ReadOnlyGitHubError("invalid check-run item")
            result = item.get("conclusion")
            if item.get("status") != "completed":
                pending = True
            elif result in _BAD:
                failed.append("check-" + str(item.get("id", "unknown")))
            elif result in _GOOD:
                succeeded += 1
            else:
                pending = True
        for item in status_contexts:
            if not isinstance(item, dict):
                raise ReadOnlyGitHubError("invalid commit-status item")
            result = item.get("state")
            if result in ("failure", "error"):
                failed.append("status-" + str(item.get("id", "unknown")))
            elif result == "success":
                succeeded += 1
            else:
                pending = True
        ci = (
            "FAILURE" if failed else
            "PENDING" if pending else
            "SUCCESS" if succeeded else
            "UNKNOWN"
        )

        latest_reviews: dict[str, str] = {}
        for review in reviews:
            if not isinstance(review, dict) or not isinstance(review.get("user"), dict):
                raise ReadOnlyGitHubError("invalid review identity")
            reviewer_id = review["user"].get("id")
            review_state = review.get("state")
            if not isinstance(reviewer_id, int) or not isinstance(review_state, str):
                raise ReadOnlyGitHubError("invalid review record")
            if review_state.upper() in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
                latest_reviews[str(reviewer_id)] = review_state.upper()
        review_state = (
            "CHANGES_REQUESTED" if "CHANGES_REQUESTED" in latest_reviews.values()
            else "NONE"
        )
        return PullSnapshot(
            head_sha=head, state=state, ci=ci, review=review_state,
            failed_checks=tuple(sorted(failed)),
        )
