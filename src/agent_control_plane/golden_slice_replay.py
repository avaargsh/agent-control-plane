from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


def freeze_json_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    """Create a detached JSON snapshot suitable for replay/audit provenance."""
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return json.loads(encoded)


def canonical_replay_digest(value: Mapping[str, Any]) -> str:
    """Return a stable sha256 digest for a JSON replay artifact."""
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
