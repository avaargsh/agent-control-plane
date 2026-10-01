from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .state_transition_protocol import (
    Principal,
    ProtocolViolation,
    canonical_digest,
)


@dataclass(frozen=True)
class ExecutionContextProvenance:
    """Durable, provider-neutral provenance for a context-bound execution."""

    work_id: str
    work_version: int
    work_snapshot_hash: str
    projection_hash: str
    proposal_hash: str
    proposer: Principal
    provenance_hash: str
    provenance_version: str = "execution-context-provenance/v1"

    @classmethod
    def seal(
        cls,
        *,
        work_id: str,
        work_version: int,
        work_snapshot_hash: str,
        projection_hash: str,
        proposal_hash: str,
        proposer: Principal,
    ) -> "ExecutionContextProvenance":
        provisional = cls(
            work_id=work_id,
            work_version=work_version,
            work_snapshot_hash=work_snapshot_hash,
            projection_hash=projection_hash,
            proposal_hash=proposal_hash,
            proposer=proposer,
            provenance_hash="",
        )
        provisional._validate_fields()
        return cls(
            work_id=provisional.work_id,
            work_version=provisional.work_version,
            work_snapshot_hash=provisional.work_snapshot_hash,
            projection_hash=provisional.projection_hash,
            proposal_hash=provisional.proposal_hash,
            proposer=provisional.proposer,
            provenance_hash=canonical_digest(
                provisional,
                exclude=("provenance_hash",),
            ),
        )

    def _validate_fields(self) -> None:
        if not self.work_id:
            raise ProtocolViolation(
                "execution provenance work_id is required"
            )
        if self.work_version <= 0:
            raise ProtocolViolation(
                "execution provenance work_version must be positive"
            )
        if not all(
            (
                self.work_snapshot_hash,
                self.projection_hash,
                self.proposal_hash,
            )
        ):
            raise ProtocolViolation(
                "execution context provenance hashes are required"
            )

    def verify(self) -> None:
        self._validate_fields()
        actual = canonical_digest(
            self,
            exclude=("provenance_hash",),
        )
        if actual != self.provenance_hash:
            raise ProtocolViolation(
                "execution context provenance digest mismatch"
            )

    def as_mapping(self) -> dict[str, Any]:
        self.verify()
        return {
            "work_id": self.work_id,
            "work_version": self.work_version,
            "work_snapshot_hash": self.work_snapshot_hash,
            "projection_hash": self.projection_hash,
            "proposal_hash": self.proposal_hash,
            "proposer": {
                "type": self.proposer.type,
                "subject": self.proposer.subject,
            },
            "provenance_version": self.provenance_version,
        }



@dataclass(frozen=True)
class ExecutionContextProvenanceV2:
    """Durable provenance including the context overlay observed at proposal."""

    work_id: str
    work_version: int
    work_snapshot_hash: str
    projection_hash: str
    context_revision: int
    context_head_hash: str
    context_overlay_hash: str
    proposal_hash: str
    proposer: Principal
    provenance_hash: str
    provenance_version: str = "execution-context-provenance/v2"

    @classmethod
    def seal(
        cls,
        *,
        work_id: str,
        work_version: int,
        work_snapshot_hash: str,
        projection_hash: str,
        context_revision: int,
        context_head_hash: str,
        context_overlay_hash: str,
        proposal_hash: str,
        proposer: Principal,
    ) -> "ExecutionContextProvenanceV2":
        provisional = cls(
            work_id=work_id,
            work_version=work_version,
            work_snapshot_hash=work_snapshot_hash,
            projection_hash=projection_hash,
            context_revision=context_revision,
            context_head_hash=context_head_hash,
            context_overlay_hash=context_overlay_hash,
            proposal_hash=proposal_hash,
            proposer=proposer,
            provenance_hash="",
        )
        provisional._validate_fields()
        return cls(
            work_id=provisional.work_id,
            work_version=provisional.work_version,
            work_snapshot_hash=provisional.work_snapshot_hash,
            projection_hash=provisional.projection_hash,
            context_revision=provisional.context_revision,
            context_head_hash=provisional.context_head_hash,
            context_overlay_hash=provisional.context_overlay_hash,
            proposal_hash=provisional.proposal_hash,
            proposer=provisional.proposer,
            provenance_hash=canonical_digest(
                provisional,
                exclude=("provenance_hash",),
            ),
        )

    def _validate_fields(self) -> None:
        if not self.work_id:
            raise ProtocolViolation(
                "execution provenance work_id is required"
            )
        if self.work_version <= 0:
            raise ProtocolViolation(
                "execution provenance work_version must be positive"
            )
        if self.context_revision < 0:
            raise ProtocolViolation(
                "execution provenance context_revision cannot be negative"
            )
        if not all(
            (
                self.work_snapshot_hash,
                self.projection_hash,
                self.context_head_hash,
                self.context_overlay_hash,
                self.proposal_hash,
            )
        ):
            raise ProtocolViolation(
                "execution context provenance v2 hashes are required"
            )

    def verify(self) -> None:
        self._validate_fields()
        actual = canonical_digest(
            self,
            exclude=("provenance_hash",),
        )
        if actual != self.provenance_hash:
            raise ProtocolViolation(
                "execution context provenance v2 digest mismatch"
            )

    def as_mapping(self) -> dict[str, Any]:
        self.verify()
        return {
            "work_id": self.work_id,
            "work_version": self.work_version,
            "work_snapshot_hash": self.work_snapshot_hash,
            "projection_hash": self.projection_hash,
            "context_revision": self.context_revision,
            "context_head_hash": self.context_head_hash,
            "context_overlay_hash": self.context_overlay_hash,
            "proposal_hash": self.proposal_hash,
            "proposer": {
                "type": self.proposer.type,
                "subject": self.proposer.subject,
            },
            "provenance_version": self.provenance_version,
        }



@dataclass(frozen=True)
class ExecutionContextProvenanceV3:
    """Durable provenance with explicit authority generation identity."""

    work_id: str
    work_version: int
    work_snapshot_hash: str
    projection_hash: str
    authority_generation: int
    authority_state_hash: str
    authority_hash: str
    context_revision: int
    context_head_hash: str
    context_overlay_hash: str
    proposal_hash: str
    proposer: Principal
    provenance_hash: str
    provenance_version: str = "execution-context-provenance/v3"

    @classmethod
    def seal(
        cls,
        *,
        work_id: str,
        work_version: int,
        work_snapshot_hash: str,
        projection_hash: str,
        authority_generation: int,
        authority_state_hash: str,
        authority_hash: str,
        context_revision: int,
        context_head_hash: str,
        context_overlay_hash: str,
        proposal_hash: str,
        proposer: Principal,
    ) -> "ExecutionContextProvenanceV3":
        provisional = cls(
            work_id=work_id,
            work_version=work_version,
            work_snapshot_hash=work_snapshot_hash,
            projection_hash=projection_hash,
            authority_generation=authority_generation,
            authority_state_hash=authority_state_hash,
            authority_hash=authority_hash,
            context_revision=context_revision,
            context_head_hash=context_head_hash,
            context_overlay_hash=context_overlay_hash,
            proposal_hash=proposal_hash,
            proposer=proposer,
            provenance_hash="",
        )
        provisional._validate_fields()
        return cls(
            work_id=provisional.work_id,
            work_version=provisional.work_version,
            work_snapshot_hash=provisional.work_snapshot_hash,
            projection_hash=provisional.projection_hash,
            authority_generation=provisional.authority_generation,
            authority_state_hash=provisional.authority_state_hash,
            authority_hash=provisional.authority_hash,
            context_revision=provisional.context_revision,
            context_head_hash=provisional.context_head_hash,
            context_overlay_hash=provisional.context_overlay_hash,
            proposal_hash=provisional.proposal_hash,
            proposer=provisional.proposer,
            provenance_hash=canonical_digest(
                provisional,
                exclude=("provenance_hash",),
            ),
        )

    def _validate_fields(self) -> None:
        if not self.work_id:
            raise ProtocolViolation(
                "execution provenance work_id is required"
            )
        if self.work_version <= 0:
            raise ProtocolViolation(
                "execution provenance work_version must be positive"
            )
        if self.authority_generation <= 0:
            raise ProtocolViolation(
                "execution provenance authority_generation must be positive"
            )
        if self.context_revision < 0:
            raise ProtocolViolation(
                "execution provenance context_revision cannot be negative"
            )
        if not all(
            (
                self.work_snapshot_hash,
                self.projection_hash,
                self.authority_state_hash,
                self.authority_hash,
                self.context_head_hash,
                self.context_overlay_hash,
                self.proposal_hash,
            )
        ):
            raise ProtocolViolation(
                "execution context provenance v3 hashes are required"
            )

    def verify(self) -> None:
        self._validate_fields()
        actual = canonical_digest(
            self,
            exclude=("provenance_hash",),
        )
        if actual != self.provenance_hash:
            raise ProtocolViolation(
                "execution context provenance v3 digest mismatch"
            )

    def as_mapping(self) -> dict[str, Any]:
        self.verify()
        return {
            "work_id": self.work_id,
            "work_version": self.work_version,
            "work_snapshot_hash": self.work_snapshot_hash,
            "projection_hash": self.projection_hash,
            "authority_generation": self.authority_generation,
            "authority_state_hash": self.authority_state_hash,
            "authority_hash": self.authority_hash,
            "context_revision": self.context_revision,
            "context_head_hash": self.context_head_hash,
            "context_overlay_hash": self.context_overlay_hash,
            "proposal_hash": self.proposal_hash,
            "proposer": {
                "type": self.proposer.type,
                "subject": self.proposer.subject,
            },
            "provenance_version": self.provenance_version,
        }
