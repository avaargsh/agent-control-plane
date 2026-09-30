from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Mapping


_REQUIRED_PROVENANCE = (
    "generated_by",
    "observed_by",
    "authorized_by",
    "executed_by",
    "committed_by",
)


class EvidenceIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True)
class EvidenceRecord:
    event: Mapping[str, Any]
    previous_digest: str | None
    digest: str


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _record_digest(event: Mapping[str, Any], previous_digest: str | None) -> str:
    material = {"event": event, "previous_digest": previous_digest}
    return "sha256:" + sha256(_canonical_bytes(material)).hexdigest()


class AppendOnlyEvidenceStore:
    def __init__(self) -> None:
        self._records: list[EvidenceRecord] = []
        self._event_ids: set[str] = set()

    @property
    def records(self) -> tuple[EvidenceRecord, ...]:
        return tuple(self._records)

    def append(self, event: Mapping[str, Any]) -> EvidenceRecord:
        event_id = str(event.get("metadata", {}).get("id", ""))
        if not event_id:
            raise EvidenceIntegrityError("evidence event requires metadata.id")
        if event_id in self._event_ids:
            raise EvidenceIntegrityError(f"duplicate evidence event: {event_id}")

        provenance = event.get("spec", {}).get("provenance", {})
        missing = [field for field in _REQUIRED_PROVENANCE if field not in provenance]
        if missing:
            raise EvidenceIntegrityError(
                "missing provenance fields: " + ",".join(missing)
            )

        frozen = json.loads(_canonical_bytes(event).decode("utf-8"))
        previous_digest = self._records[-1].digest if self._records else None
        record = EvidenceRecord(
            event=frozen,
            previous_digest=previous_digest,
            digest=_record_digest(frozen, previous_digest),
        )
        self._records.append(record)
        self._event_ids.add(event_id)
        return record

    def verify(self) -> None:
        previous_digest: str | None = None
        for record in self._records:
            if record.previous_digest != previous_digest:
                raise EvidenceIntegrityError("evidence hash chain was reordered")
            expected = _record_digest(record.event, previous_digest)
            if expected != record.digest:
                raise EvidenceIntegrityError("evidence record was modified")
            previous_digest = record.digest
