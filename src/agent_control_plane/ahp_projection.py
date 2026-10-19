"""Read-only, transport-neutral AHP-style Channel / Reducer / Replay projection.

The Control Plane journal remains authoritative. This is an ephemeral client
view, NOT an AHP wire server, permission engine, or execution journal.
"""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Mapping


class ProjectionRejected(ValueError):
    """Invalid, unproven, stale, or conflicting authoritative projection event."""


class ChannelKind(str, Enum):
    SESSION = "session"
    APPROVAL = "approval"
    EXECUTION = "execution"


_FIELDS = {
    ChannelKind.SESSION: frozenset({"status", "title", "provider_ref", "active_run_id"}),
    ChannelKind.APPROVAL: frozenset({"status", "approval_id", "operation_id", "reason"}),
    ChannelKind.EXECUTION: frozenset({"status", "operation_id", "resource_ref", "proof_ref"}),
}
_STATUSES = {
    ChannelKind.SESSION: frozenset({"CREATED", "READY", "RUNNING", "COMPLETED", "FAILED", "ARCHIVED"}),
    ChannelKind.APPROVAL: frozenset({"PENDING", "APPROVED", "DENIED", "EXPIRED"}),
    ChannelKind.EXECUTION: frozenset({"PREPARED", "COMMITTED", "ABORTED", "UNKNOWN", "VERIFIED"}),
}


@dataclass(frozen=True)
class ChannelKey:
    tenant_id: str
    session_id: str
    kind: ChannelKind
    resource_id: str

    def __post_init__(self) -> None:
        if not all((self.tenant_id, self.session_id, self.resource_id)):
            raise ProjectionRejected("canonical tenant, session and resource identity are required")
        if not isinstance(self.kind, ChannelKind):
            raise ProjectionRejected("unsupported channel kind")


@dataclass(frozen=True)
class AuthoritativeEvent:
    """Projected fact obtained from a VERIFIED Control Plane journal/SoT read.

    ``source_ref`` identifies the durable fact to validate, not an ACP notification.
    ``revision`` is consecutive *within this ChannelKey*, starting at 1.
    """

    key: ChannelKey
    revision: int
    source_ref: str
    changes: Mapping[str, object]


@dataclass(frozen=True)
class ActionEnvelope:
    server_seq: int
    event: AuthoritativeEvent


@dataclass(frozen=True)
class ChannelSnapshot:
    key: ChannelKey
    revision: int
    state: dict[str, object]


@dataclass(frozen=True)
class ReconnectResult:
    mode: str  # replay | snapshot
    server_seq: int
    actions: tuple[ActionEnvelope, ...] = ()
    snapshots: tuple[ChannelSnapshot, ...] = ()


ProofVerifier = Callable[[AuthoritativeEvent], bool]
ChannelAuthorizer = Callable[[ChannelKey], bool]


class AhpProjection:
    """In-memory state projection, deliberately outside the execution path.

    Writer: a trusted Control Plane journal subscriber must supply a verifier.
    Reader: each reconnect must supply an authorization decision for EVERY key.
    Neither AHP client requests nor ACP provider events are writer inputs.
    """

    def __init__(self, *, replay_capacity: int = 256) -> None:
        if not isinstance(replay_capacity, int) or replay_capacity < 1:
            raise ValueError("replay_capacity must be positive")
        self._server_seq = 0
        self._history: deque[ActionEnvelope] = deque(maxlen=replay_capacity)
        self._state: dict[ChannelKey, ChannelSnapshot] = {}
        self._last_event: dict[ChannelKey, AuthoritativeEvent] = {}

    @property
    def server_seq(self) -> int:
        return self._server_seq

    def ingest(self, event: AuthoritativeEvent, *, verify: ProofVerifier) -> ActionEnvelope:
        """Apply an independently verified source event; no client-side mutation.

        Consecutive revisions and exact duplicate handling are scoped per channel.
        Ingest never calls an external runtime, mutates approvals, or issues tools.
        """
        if not isinstance(event, AuthoritativeEvent):
            raise ProjectionRejected("only authoritative Control Plane facts may be ingested")
        if not callable(verify) or verify(event) is not True:
            raise PermissionError("Control Plane source verification rejected the event")
        self._validate(event)
        old = self._state.get(event.key)
        if old and event.revision == old.revision:
            if event != self._last_event[event.key]:
                raise ProjectionRejected("same revision has conflicting content")
            # Exact resend is idempotent, even after older actions leave the ring.
            return ActionEnvelope(self._server_seq, deepcopy(event))
        expected = 1 if old is None else old.revision + 1
        if event.revision != expected:
            raise ProjectionRejected(f"out of order channel revision: expected {expected}")
        state = deepcopy(old.state) if old else {}
        state.update(deepcopy(dict(event.changes)))
        if event.key.kind is ChannelKind.EXECUTION and state.get("status") == "VERIFIED":
            if not state.get("proof_ref"):
                raise ProjectionRejected("independently VERIFIED execution requires proof_ref")
        copied = deepcopy(event)
        envelope = ActionEnvelope(self._server_seq + 1, copied)
        self._state[event.key] = ChannelSnapshot(event.key, event.revision, state)
        self._last_event[event.key] = copied
        self._server_seq += 1
        self._history.append(envelope)
        return deepcopy(envelope)

    @staticmethod
    def _validate(event: AuthoritativeEvent) -> None:
        if not isinstance(event.revision, int) or isinstance(event.revision, bool) or event.revision < 1:
            raise ProjectionRejected("source revision must be a positive integer")
        if not event.source_ref or not isinstance(event.source_ref, str):
            raise ProjectionRejected("durable source_ref is required")
        if not isinstance(event.changes, Mapping) or not event.changes:
            raise ProjectionRejected("nonempty state changes are required")
        if not set(event.changes).issubset(_FIELDS[event.key.kind]):
            raise ProjectionRejected("unsupported or authority-bearing projection field")
        if "status" in event.changes and event.changes["status"] not in _STATUSES[event.key.kind]:
            raise ProjectionRejected("unsupported projected lifecycle status")
        for key, value in event.changes.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise ProjectionRejected("projection fields must be strings")

    def reconnect(
        self,
        *,
        last_seen_server_seq: int,
        keys: tuple[ChannelKey, ...],
        authorize: ChannelAuthorizer,
    ) -> ReconnectResult:
        """Replay contiguous retained actions, or send current authorized snapshots.

        Authentication, tenant scope and session ownership MUST be resolved in
        the caller's Control Plane authorization layer, not from AHP client IDs.
        """
        if (
            not isinstance(last_seen_server_seq, int)
            or isinstance(last_seen_server_seq, bool)
            or not 0 <= last_seen_server_seq <= self._server_seq
        ):
            raise ProjectionRejected("invalid client replay cursor")
        if not callable(authorize):
            raise PermissionError("Control Plane authorization is required")
        for key in keys:
            if authorize(key) is not True:
                raise PermissionError("channel subscription not authorized")
            if key not in self._state:
                raise KeyError("channel not found")
        scoped = set(keys)
        oldest = self._history[0].server_seq if self._history else self._server_seq + 1
        if last_seen_server_seq >= oldest - 1:
            return ReconnectResult(
                mode="replay",
                server_seq=self._server_seq,
                actions=tuple(
                    deepcopy(action)
                    for action in self._history
                    if action.server_seq > last_seen_server_seq and action.event.key in scoped
                ),
            )
        return ReconnectResult(
            mode="snapshot",
            server_seq=self._server_seq,
            snapshots=tuple(deepcopy(self._state[key]) for key in keys),
        )
