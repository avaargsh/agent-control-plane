"""Opt-in last-moment authority check for Kubernetes Deployment mutations.

This must read a distinct authoritative owner of *current* policy, approval
and principal state. A past DSSE proof, approval signature or typed receipt
is NOT an admission token. No Task ledger or execution authority is created.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Protocol

from .state_transition_protocol import (
    ActionIntent,
    AuthorizationBinding,
    ExecutionLease,
    Principal,
    ProtocolViolation,
    ResourceIdentity,
)

Clock = Callable[[], datetime]


@dataclass(frozen=True)
class EffectScope:
    """Out-of-band operator scope; never derived from provider or DSSE input."""

    tenant_id: str
    project_id: str
    cluster_id: str
    nonce: str


@dataclass(frozen=True)
class CurrentEffectAuthority:
    """One independently read current authority record, not an LLM verdict."""

    tenant_id: str
    project_id: str
    cluster_id: str
    work_id: str
    operation_id: str
    nonce: str
    resource_uid: str
    principal: Principal
    resource_generation: int
    lease_id: str
    lease_epoch: int
    transition_hash: str
    action_hash: str
    authorization_hash: str
    approval_hash: str
    policy_version: str
    policy_input_hash: str
    policy_decision_hash: str
    policy_effect: str
    approval_state: str
    principal_state: str
    operation_state: str
    observed_at: datetime
    valid_until: datetime
    version: int


class CurrentEffectAuthorityReader(Protocol):
    """A trusted authority's fresh read, independent of incoming request data."""

    def read_current(
        self,
        *,
        tenant_id: str,
        project_id: str,
        cluster_id: str,
        work_id: str,
        operation_id: str,
    ) -> CurrentEffectAuthority | None:
        ...


@dataclass(frozen=True)
class LiveEffectBoundaryGate:
    """Fail-closed opt-in read just before provider PATCH is sent.

    This prevents pre-existing stale/revoked authority at check time; it does
    not make the policy read and Kubernetes PATCH an atomic transaction.
    Kubernetes resourceVersion/UID/fence still protect provider state.
    """

    scope: EffectScope
    reader: CurrentEffectAuthorityReader
    clock: Clock
    max_age_seconds: int = 5

    def assert_current(
        self,
        *,
        work_id: str,
        operation_id: str,
        subject: ResourceIdentity,
        resource_generation: int,
        action: ActionIntent,
        authorization: AuthorizationBinding,
        approval_hash: str,
        policy_input_hash: str,
        active_lease: ExecutionLease,
        caller: Principal,
    ) -> None:
        if not isinstance(self.scope, EffectScope) or not all(
            isinstance(v, str) and v.strip()
            for v in (
                self.scope.tenant_id, self.scope.project_id,
                self.scope.cluster_id, self.scope.nonce, work_id, operation_id,
                approval_hash, policy_input_hash,
            )
        ):
            raise ProtocolViolation("live effect authoritative scope is required")
        if (type(self.max_age_seconds) is not int
                or not 1 <= self.max_age_seconds <= 60):
            raise ProtocolViolation("invalid live authority freshness bound")
        try:
            now = self.clock()
            observed = self.reader.read_current(
                tenant_id=self.scope.tenant_id,
                project_id=self.scope.project_id,
                cluster_id=self.scope.cluster_id,
                work_id=work_id,
                operation_id=operation_id,
            )
        except Exception as exc:
            raise ProtocolViolation("live effect authority read unavailable") from exc
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ProtocolViolation("live effect clock is not timezone-aware")
        if type(observed) is not CurrentEffectAuthority:
            raise ProtocolViolation("live effect authoritative record is missing")
        if (not isinstance(observed.observed_at, datetime)
                or observed.observed_at.tzinfo is None
                or observed.observed_at.utcoffset() is None
                or not isinstance(observed.valid_until, datetime)
                or observed.valid_until.tzinfo is None
                or observed.valid_until.utcoffset() is None):
            raise ProtocolViolation("live effect authority timestamps are invalid")
        if (observed.observed_at > now
                or now - observed.observed_at > timedelta(seconds=self.max_age_seconds)
                or now >= observed.valid_until):
            raise ProtocolViolation("live effect authority is stale or expired")
        if type(observed.version) is not int or observed.version <= 0:
            raise ProtocolViolation("live effect authority version is invalid")
        authorization.verify()
        action.verify()
        if caller != authorization.principal or active_lease.holder != caller:
            raise ProtocolViolation("live effect execution principal mismatch")
        if (action.transition_hash != authorization.transition_hash
                or action.action_hash != authorization.action_hash
                or subject.resource_uid != active_lease.resource_uid):
            raise ProtocolViolation("live effect original authorization binding mismatch")
        expected = (
            self.scope.tenant_id, self.scope.project_id, self.scope.cluster_id,
            work_id, operation_id, self.scope.nonce, subject.resource_uid, caller,
            resource_generation, active_lease.lease_id, active_lease.epoch,
            action.transition_hash, action.action_hash,
            authorization.authorization_hash, approval_hash,
            authorization.policy_version, policy_input_hash,
            authorization.policy_decision_hash,
        )
        actual = (
            observed.tenant_id, observed.project_id, observed.cluster_id,
            observed.work_id, observed.operation_id, observed.nonce,
            observed.resource_uid, observed.principal,
            observed.resource_generation, observed.lease_id, observed.lease_epoch,
            observed.transition_hash, observed.action_hash,
            observed.authorization_hash, observed.approval_hash,
            observed.policy_version, observed.policy_input_hash,
            observed.policy_decision_hash,
        )
        if actual != expected:
            raise ProtocolViolation("live effect authority binding mismatch")
        if (
            observed.policy_effect != "PERMIT"
            or observed.approval_state != "ACTIVE"
            or observed.principal_state != "ACTIVE"
            or observed.operation_state != "ACTIVE"
        ):
            raise ProtocolViolation("live effect authority has been revoked or denied")
        if (now >= authorization.expires_at
                or now >= active_lease.expires_at):
            raise ProtocolViolation("live effect execution authorization expired")
