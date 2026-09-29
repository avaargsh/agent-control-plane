from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class CorrelationContext:
    release_id: str
    run_id: str | None = None
    session_id: str | None = None
    trace_id: str | None = None

    def evidence_attributes(self) -> dict[str, str]:
        attrs = {"agentplane.release.id": self.release_id}
        if self.run_id:
            attrs["agentplane.run.id"] = self.run_id
        if self.session_id:
            attrs["agentplane.session.id"] = self.session_id
        if self.trace_id:
            attrs["trace.id"] = self.trace_id
        return attrs

    def provider_attributes(
        self,
        external_refs: Mapping[str, str],
    ) -> dict[str, str]:
        return {
            **self.evidence_attributes(),
            **{
                f"agentplane.external_ref.{key}": value
                for key, value in external_refs.items()
                if value
            },
        }
