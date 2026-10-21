"""Offline M6 evidence *file integrity* and paired-delivery correlation.

This reads an existing operator manifest and bounded local evidence files.
No network, secret access, GitHub write, webhook registration, signature
validation or release action. A fabricated collection of files can satisfy
the checks; only independent review can establish real-world provenance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from pathlib import Path, PurePosixPath

from m6_acceptance_validator import GATES, validate
from m6_paired_delivery_evidence import correlate

_MAX_BYTES = 4_000_000
_SAFE_PART = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_PAIR = (
    "real_github_pull_request_delivery",
    "real_github_check_delivery",
)


def _read_evidence(root: Path, relative: object) -> bytes:
    """Read a bounded regular file without following links during path lookup."""
    if not isinstance(relative, str) or len(relative) > 512 or not relative:
        raise ValueError("invalid evidence path")
    if "\\" in relative:
        raise ValueError("invalid evidence path")
    path = PurePosixPath(relative)
    if path.is_absolute() or len(path.parts) > 4 or any(
        part in (".", "..") or not _SAFE_PART.fullmatch(part)
        for part in path.parts
    ):
        raise ValueError("invalid evidence path")

    # Path.is_symlink() followed by Path.open() has a TOCTOU gap: a local
    # writer could swap a checked component for a symlink before it is opened.
    # Anchor each lookup to an already-open directory and reject symlinks in
    # the kernel's open operation, rather than relying on prior path checks.
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise ValueError("platform lacks safe no-follow directory opens")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY
    nofollow = os.O_NOFOLLOW
    cloexec = getattr(os, "O_CLOEXEC", 0)
    directory_fd = os.open(root, directory_flags | nofollow | cloexec)
    opened_fds = [directory_fd]
    try:
        for component in path.parts[:-1]:
            directory_fd = os.open(
                component, directory_flags | nofollow | cloexec,
                dir_fd=directory_fd,
            )
            opened_fds.append(directory_fd)
        file_fd = os.open(
            path.parts[-1], os.O_RDONLY | nofollow | cloexec
            | getattr(os, "O_NONBLOCK", 0),
            dir_fd=directory_fd,
        )
        opened_fds.append(file_fd)
        if not stat.S_ISREG(os.fstat(file_fd).st_mode):
            raise ValueError("regular evidence file required")
        chunks: list[bytes] = []
        remaining = _MAX_BYTES + 1
        while remaining:
            chunk = os.read(file_fd, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
    finally:
        for fd in reversed(opened_fds):
            os.close(fd)

    if not data or len(data) > _MAX_BYTES:
        raise ValueError("evidence file empty or exceeds byte limit")
    return data

def verify_materialized(report: object, root: Path) -> dict:
    """Bind every claimed SHA-256 to actual bytes, never infer live origin."""
    verdict = validate(report)
    errors = list(verdict["errors"])
    if not isinstance(report, dict):
        return _result(errors, 0)

    gates = report.get("gates")
    if not isinstance(gates, dict):
        return _result(errors, 0)

    try:
        # The selected root is itself part of the trust boundary. Resolving a
        # symlink first would hide that the operator selected a redirected root.
        if root.is_symlink():
            raise ValueError("evidence root must not be a symbolic link")
        root = root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("evidence root is not a directory")
    except (OSError, ValueError):
        return _result(errors + ["evidence root unavailable"], 0)

    matched = 0
    paths: set[str] = set()
    paired: dict[str, object] = {}
    for key in GATES:
        record = gates.get(key)
        if not isinstance(record, dict):
            continue
        path = record.get("evidence_path")
        if not isinstance(path, str):
            errors.append(key + ": evidence_path required for materialized review")
            continue
        if path in paths:
            errors.append(key + ": reused evidence file path")
            continue
        paths.add(path)
        try:
            payload = _read_evidence(root, path)
        except (OSError, ValueError):
            errors.append(key + ": evidence file missing, unsafe or oversized")
            continue
        claimed = record.get("evidence_sha256")
        if not isinstance(claimed, str) or not _HEX.fullmatch(claimed):
            errors.append(key + ": invalid claimed evidence digest")
            continue
        if hashlib.sha256(payload).hexdigest() != claimed:
            errors.append(key + ": actual file hash differs from manifest")
            continue
        matched += 1
        if key in _PAIR:
            try:
                parsed = json.loads(payload)
            except (UnicodeError, ValueError):
                errors.append(key + ": delivery audit is not valid JSON")
                continue
            paired[key] = parsed

    if len(paired) != 2:
        errors.append("paired real-GitHub delivery reports missing or invalid")
    else:
        pair = correlate(paired[_PAIR[0]], paired[_PAIR[1]])
        if pair["correlation"] != "PAIR_CORROBORATED_NOT_RELEASED":
            errors.append("paired delivery claims do not corroborate")
    if matched != len(GATES):
        errors.append("not all gate evidence files matched")

    return _result(errors, matched)


def _result(errors: list[str], matched: int) -> dict:
    return {
        "schema": "m6-materialized-evidence-verdict/v1",
        "decision": ("FILES_MATCHED_NOT_RELEASED" if not errors else "NO_GO"),
        "release_decision": "NO_GO",
        "business_outcome_verified": False,
        "independent_proof_ref": None,
        "verified_file_count": matched,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    try:
        # Bounded, local manifest parse. Never echo its contents or raw paths.
        if args.manifest.is_symlink():
            raise ValueError("manifest must not be a symbolic link")
        with args.manifest.open("rb") as stream:
            payload = stream.read(_MAX_BYTES + 1)
        if len(payload) > _MAX_BYTES:
            raise ValueError("manifest exceeds size bound")
        report = json.loads(payload.decode("utf-8"))
        result = verify_materialized(report, args.root)
    except (OSError, UnicodeError, ValueError):
        result = _result(["unavailable or malformed operator manifest"], 0)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["decision"] == "FILES_MATCHED_NOT_RELEASED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
