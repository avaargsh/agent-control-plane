from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Protocol
from uuid import uuid4

from .state_transition_protocol import (
    ExecutionLease,
    Principal,
    ProtocolViolation,
)


_FENCE_EPOCH_ANNOTATION = "agent-control-plane.openai.com/fence-epoch"
_FENCE_LEASE_ID_ANNOTATION = "agent-control-plane.openai.com/fence-lease-id"
_FENCE_HOLDER_ANNOTATION = "agent-control-plane.openai.com/fence-holder"


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ProtocolViolation(f"{field_name} must be timezone-aware")


def _holder_value(holder: Principal) -> str:
    return f"{holder.type}:{holder.subject}"


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    _require_aware(parsed, "stored lease timestamp")
    return parsed


class DurableLeaseState(str, Enum):
    PREPARING = "PREPARING"
    ACTIVE = "ACTIVE"
    RELEASED = "RELEASED"


@dataclass(frozen=True)
class DurableLeaseRecord:
    lease: ExecutionLease
    state: DurableLeaseState
    prepared_at: datetime
    activated_at: datetime | None = None


class ExecutionLeaseAuthority(Protocol):
    def assert_active(
        self,
        lease: ExecutionLease,
        *,
        now: datetime,
    ) -> DurableLeaseRecord:
        ...


class SQLiteExecutionLeaseStore:
    """Durable monotonic lease authority for the reference control plane.

    The store is intentionally small and dependency-free. SQLite is not the
    production HA recommendation; the contract is the important part:

    - one latest epoch per provider resource UID
    - epochs only increase
    - takeover first creates PREPARING epoch N
    - epoch N is ACTIVE only after the provider target has been fenced
    - stale lease IDs/epochs/holders fail closed after process restart
    """

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            isolation_level=None,
            timeout=30.0,
        )
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS execution_leases (
                    resource_uid TEXT PRIMARY KEY,
                    lease_id TEXT NOT NULL,
                    holder_type TEXT NOT NULL,
                    holder_subject TEXT NOT NULL,
                    epoch INTEGER NOT NULL CHECK (epoch > 0),
                    acquired_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    prepared_at TEXT NOT NULL,
                    activated_at TEXT,
                    state TEXT NOT NULL
                )
                """
            )

    @staticmethod
    def _record(row: sqlite3.Row) -> DurableLeaseRecord:
        activated_at = (
            _parse_time(row["activated_at"])
            if row["activated_at"] is not None
            else None
        )
        lease = ExecutionLease(
            lease_id=row["lease_id"],
            resource_uid=row["resource_uid"],
            holder=Principal(
                type=row["holder_type"],
                subject=row["holder_subject"],
            ),
            epoch=int(row["epoch"]),
            acquired_at=_parse_time(row["acquired_at"]),
            expires_at=_parse_time(row["expires_at"]),
        )
        return DurableLeaseRecord(
            lease=lease,
            state=DurableLeaseState(row["state"]),
            prepared_at=_parse_time(row["prepared_at"]),
            activated_at=activated_at,
        )

    def latest(self, resource_uid: str) -> DurableLeaseRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM execution_leases
                WHERE resource_uid = ?
                """,
                (resource_uid,),
            ).fetchone()
        return self._record(row) if row is not None else None

    def prepare(
        self,
        *,
        resource_uid: str,
        holder: Principal,
        now: datetime,
        ttl_seconds: int,
    ) -> DurableLeaseRecord:
        _require_aware(now, "now")
        if not resource_uid:
            raise ProtocolViolation("resource_uid is required")
        if ttl_seconds <= 0:
            raise ProtocolViolation("ttl_seconds must be positive")

        lease_id = uuid4().hex
        expires_at = now + timedelta(seconds=ttl_seconds)

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT epoch
                FROM execution_leases
                WHERE resource_uid = ?
                """,
                (resource_uid,),
            ).fetchone()
            epoch = (int(row["epoch"]) if row is not None else 0) + 1
            connection.execute(
                """
                INSERT INTO execution_leases (
                    resource_uid,
                    lease_id,
                    holder_type,
                    holder_subject,
                    epoch,
                    acquired_at,
                    expires_at,
                    prepared_at,
                    activated_at,
                    state
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                ON CONFLICT(resource_uid) DO UPDATE SET
                    lease_id = excluded.lease_id,
                    holder_type = excluded.holder_type,
                    holder_subject = excluded.holder_subject,
                    epoch = excluded.epoch,
                    acquired_at = excluded.acquired_at,
                    expires_at = excluded.expires_at,
                    prepared_at = excluded.prepared_at,
                    activated_at = NULL,
                    state = excluded.state
                """,
                (
                    resource_uid,
                    lease_id,
                    holder.type,
                    holder.subject,
                    epoch,
                    now.isoformat(),
                    expires_at.isoformat(),
                    now.isoformat(),
                    DurableLeaseState.PREPARING.value,
                ),
            )
            connection.execute("COMMIT")

        record = self.latest(resource_uid)
        if record is None:
            raise ProtocolViolation("prepared lease disappeared")
        return record

    def activate(
        self,
        *,
        resource_uid: str,
        lease_id: str,
        epoch: int,
        now: datetime,
    ) -> DurableLeaseRecord:
        _require_aware(now, "now")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT *
                FROM execution_leases
                WHERE resource_uid = ?
                """,
                (resource_uid,),
            ).fetchone()
            if row is None:
                connection.execute("ROLLBACK")
                raise ProtocolViolation("prepared lease does not exist")
            current = self._record(row)
            if (
                current.lease.lease_id != lease_id
                or current.lease.epoch != epoch
            ):
                connection.execute("ROLLBACK")
                raise ProtocolViolation(
                    "prepared lease was superseded before activation"
                )
            if current.state is not DurableLeaseState.PREPARING:
                connection.execute("ROLLBACK")
                raise ProtocolViolation(
                    "only PREPARING lease may be activated"
                )
            if now >= current.lease.expires_at:
                connection.execute("ROLLBACK")
                raise ProtocolViolation(
                    "prepared lease expired before activation"
                )
            connection.execute(
                """
                UPDATE execution_leases
                SET state = ?, activated_at = ?
                WHERE resource_uid = ? AND lease_id = ? AND epoch = ?
                """,
                (
                    DurableLeaseState.ACTIVE.value,
                    now.isoformat(),
                    resource_uid,
                    lease_id,
                    epoch,
                ),
            )
            connection.execute("COMMIT")

        record = self.latest(resource_uid)
        if record is None:
            raise ProtocolViolation("activated lease disappeared")
        return record

    def release(
        self,
        lease: ExecutionLease,
        *,
        now: datetime,
    ) -> DurableLeaseRecord:
        _require_aware(now, "now")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT *
                FROM execution_leases
                WHERE resource_uid = ?
                """,
                (lease.resource_uid,),
            ).fetchone()
            if row is None:
                connection.execute("ROLLBACK")
                raise ProtocolViolation("lease does not exist")
            current = self._record(row)
            self._assert_same(current, lease)
            connection.execute(
                """
                UPDATE execution_leases
                SET state = ?
                WHERE resource_uid = ? AND lease_id = ? AND epoch = ?
                """,
                (
                    DurableLeaseState.RELEASED.value,
                    lease.resource_uid,
                    lease.lease_id,
                    lease.epoch,
                ),
            )
            connection.execute("COMMIT")
        record = self.latest(lease.resource_uid)
        if record is None:
            raise ProtocolViolation("released lease disappeared")
        return record

    @staticmethod
    def _assert_same(
        current: DurableLeaseRecord,
        lease: ExecutionLease,
    ) -> None:
        if current.lease.lease_id != lease.lease_id:
            raise ProtocolViolation("durable lease id changed")
        if current.lease.epoch != lease.epoch:
            raise ProtocolViolation("durable lease epoch changed")
        if current.lease.holder != lease.holder:
            raise ProtocolViolation("durable lease holder changed")

    def assert_active(
        self,
        lease: ExecutionLease,
        *,
        now: datetime,
    ) -> DurableLeaseRecord:
        _require_aware(now, "now")
        current = self.latest(lease.resource_uid)
        if current is None:
            raise ProtocolViolation("durable lease does not exist")
        self._assert_same(current, lease)
        if current.state is not DurableLeaseState.ACTIVE:
            raise ProtocolViolation(
                f"durable lease is not ACTIVE: {current.state.value}"
            )
        current.lease.assert_active(now)
        return current


class KubernetesFenceTargetApi(Protocol):
    def get_deployment(
        self,
        *,
        namespace: str,
        name: str,
    ) -> Mapping[str, Any] | None:
        ...

    def patch_deployment(
        self,
        *,
        namespace: str,
        name: str,
        patch: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        ...


def assert_target_fence(
    resource: Mapping[str, Any],
    lease: ExecutionLease,
) -> None:
    metadata = resource.get("metadata", {})
    if not isinstance(metadata, Mapping):
        raise ProtocolViolation("kubernetes metadata is required")
    annotations = metadata.get("annotations", {})
    if not isinstance(annotations, Mapping):
        raise ProtocolViolation("kubernetes fence annotations are required")

    try:
        epoch = int(annotations.get(_FENCE_EPOCH_ANNOTATION, ""))
    except (TypeError, ValueError) as exc:
        raise ProtocolViolation(
            "kubernetes target fence epoch is missing or invalid"
        ) from exc

    if epoch != lease.epoch:
        raise ProtocolViolation(
            "kubernetes target fence epoch does not match active lease"
        )
    if annotations.get(_FENCE_LEASE_ID_ANNOTATION) != lease.lease_id:
        raise ProtocolViolation(
            "kubernetes target fence lease id does not match active lease"
        )
    if annotations.get(_FENCE_HOLDER_ANNOTATION) != _holder_value(lease.holder):
        raise ProtocolViolation(
            "kubernetes target fence holder does not match active lease"
        )


class KubernetesDeploymentFenceProjector:
    """Project a PREPARING durable epoch into the target Deployment.

    This metadata-only CAS write is the bridge between the lease authority and
    Kubernetes. Once it commits, every stale controller that read the target
    before takeover holds an obsolete resourceVersion. A controller reading
    afterwards sees the newer epoch and fails the target-fence check.
    """

    def __init__(self, api: KubernetesFenceTargetApi) -> None:
        self.api = api

    def project(
        self,
        *,
        record: DurableLeaseRecord,
        namespace: str,
        name: str,
    ) -> Mapping[str, Any]:
        if record.state is not DurableLeaseState.PREPARING:
            raise ProtocolViolation(
                "only PREPARING lease can be projected to provider target"
            )
        live = self.api.get_deployment(
            namespace=namespace,
            name=name,
        )
        if live is None:
            raise ProtocolViolation(
                "deployment does not exist for fence projection"
            )
        metadata = live.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise ProtocolViolation("kubernetes metadata is required")
        if metadata.get("uid") != record.lease.resource_uid:
            raise ProtocolViolation(
                "fence target uid does not match durable lease resource"
            )
        resource_version = metadata.get("resourceVersion")
        if not isinstance(resource_version, str) or not resource_version:
            raise ProtocolViolation(
                "fence target resourceVersion is required"
            )

        annotations = metadata.get("annotations", {})
        if not isinstance(annotations, Mapping):
            annotations = {}
        existing_epoch_raw = annotations.get(_FENCE_EPOCH_ANNOTATION)
        if existing_epoch_raw is not None:
            try:
                existing_epoch = int(existing_epoch_raw)
            except (TypeError, ValueError) as exc:
                raise ProtocolViolation(
                    "existing target fence epoch is invalid"
                ) from exc
            if existing_epoch > record.lease.epoch:
                raise ProtocolViolation(
                    "cannot project an older fence epoch over a newer target"
                )
            if existing_epoch == record.lease.epoch:
                if (
                    annotations.get(_FENCE_LEASE_ID_ANNOTATION)
                    != record.lease.lease_id
                    or annotations.get(_FENCE_HOLDER_ANNOTATION)
                    != _holder_value(record.lease.holder)
                ):
                    raise ProtocolViolation(
                        "target fence epoch is already owned by another lease"
                    )
                return live

        projected = self.api.patch_deployment(
            namespace=namespace,
            name=name,
            patch={
                "metadata": {
                    "resourceVersion": resource_version,
                    "annotations": {
                        _FENCE_EPOCH_ANNOTATION: str(record.lease.epoch),
                        _FENCE_LEASE_ID_ANNOTATION: record.lease.lease_id,
                        _FENCE_HOLDER_ANNOTATION: _holder_value(
                            record.lease.holder
                        ),
                    },
                },
            },
        )
        assert_target_fence(projected, record.lease)
        return projected


def acquire_fenced_execution_lease(
    *,
    authority: SQLiteExecutionLeaseStore,
    projector: KubernetesDeploymentFenceProjector,
    resource_uid: str,
    namespace: str,
    name: str,
    holder: Principal,
    now: datetime,
    ttl_seconds: int,
) -> ExecutionLease:
    """Acquire a lease only after its epoch has fenced the provider target."""

    prepared = authority.prepare(
        resource_uid=resource_uid,
        holder=holder,
        now=now,
        ttl_seconds=ttl_seconds,
    )
    projector.project(
        record=prepared,
        namespace=namespace,
        name=name,
    )
    active = authority.activate(
        resource_uid=resource_uid,
        lease_id=prepared.lease.lease_id,
        epoch=prepared.lease.epoch,
        now=now,
    )
    return active.lease
