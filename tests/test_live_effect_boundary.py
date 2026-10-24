"""Negative-first reference integration: current authority vs Kubernetes PATCH."""
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta

import pytest

from agent_control_plane.authority_reservation import AuthorityReservationState
from agent_control_plane.execution_journal import ExecutionAttemptState
from agent_control_plane.live_effect_boundary import (
    CurrentEffectAuthority,
    EffectScope,
    LiveEffectAdmissionDenied,
    LiveEffectBoundaryGate,
)
from agent_control_plane.state_transition_protocol import ProtocolViolation
from context_testkit import NOW, build_context_bound_execution_v3
from test_execution_lifecycle import _coordinator, _execute


CHECK_AT = NOW + timedelta(seconds=12)
SCOPE = EffectScope(
    tenant_id="tenant-bound-by-operator",
    project_id="project-bound-by-operator",
    cluster_id="cluster-bound-by-operator",
    nonce="fresh-operation-nonce-001",
)


class AuthoritativeReaderFixture:
    """Deliberate fake. A production caller must implement an external reader."""

    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.error = None
        self.calls = []

    def read_current(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.snapshot


def _scenario(tmp_path):
    fixture, store, _, _, proposal, context = (
        build_context_bound_execution_v3(tmp_path)
    )
    coordinator, journal, reservations = _coordinator(
        tmp_path, fixture, store
    )
    prepared = coordinator.prepare(
        transition=fixture["transition"],
        action=fixture["action"],
        authorization=fixture["authorization"],
        fence=fixture["fence"],
        active_lease=fixture["lease"],
        context_binding=context,
    )
    auth = fixture["authorization"]
    action = fixture["action"]
    lease = fixture["lease"]
    snap = CurrentEffectAuthority(
        tenant_id=SCOPE.tenant_id,
        project_id=SCOPE.project_id,
        cluster_id=SCOPE.cluster_id,
        work_id=proposal.work_id,
        operation_id=prepared.attempt.operation_id,
        nonce=SCOPE.nonce,
        resource_uid=fixture["resource"].resource_uid,
        principal=auth.principal,
        executor=fixture["holder"],
        resource_generation=fixture["transition"].expected_generation,
        lease_id=lease.lease_id,
        lease_epoch=lease.epoch,
        transition_hash=fixture["transition"].transition_hash,
        action_hash=action.action_hash,
        authorization_hash=auth.authorization_hash,
        approval_hash=context.signed_approval.approval.approval_hash,
        policy_version=auth.policy_version,
        policy_input_hash=context.policy_input.input_hash,
        policy_decision_hash=auth.policy_decision_hash,
        policy_effect="PERMIT",
        approval_state="ACTIVE",
        principal_state="ACTIVE",
        operation_state="ACTIVE",
        observed_at=CHECK_AT,
        valid_until=CHECK_AT + timedelta(minutes=1),
        version=7,
    )
    reader = AuthoritativeReaderFixture(snap)
    gate = LiveEffectBoundaryGate(
        scope=SCOPE, reader=reader, clock=lambda: CHECK_AT,
    )
    return (
        fixture, store, proposal, replace(context, live_effect_gate=gate),
        coordinator, journal, reservations, prepared, reader,
    )


def _assert_not_applied(scenario, *, expected_reads=1):
    fixture, _, _, context, coordinator, journal, reservations, prepared, reader = scenario
    with pytest.raises(LiveEffectAdmissionDenied):
        _execute(coordinator, fixture, context)
    assert fixture["api"].patch_calls == 0
    assert len(reader.calls) == expected_reads
    if expected_reads:
        assert reader.calls[0] == {
            "tenant_id": SCOPE.tenant_id,
            "project_id": SCOPE.project_id,
            "cluster_id": SCOPE.cluster_id,
            "work_id": context.proposal.work_id,
            "operation_id": prepared.attempt.operation_id,
        }
    aborted = journal.get(prepared.attempt.attempt_id)
    assert aborted is not None
    assert aborted.state is ExecutionAttemptState.ABORTED
    assert aborted.result["status"] == "NOT_APPLIED"
    assert aborted.operation_id == prepared.attempt.operation_id
    assert aborted.authority_reservation is not None
    reservation = reservations.get(
        aborted.authority_reservation["reservation_id"]
    )
    assert reservation is not None
    assert reservation.state is AuthorityReservationState.RELEASED


def test_live_authority_checked_at_last_patch_boundary(tmp_path):
    scenario = _scenario(tmp_path)
    fixture, _, _, context, coordinator, journal, _, prepared, reader = scenario
    result = _execute(coordinator, fixture, context)
    assert result.attempt.state is ExecutionAttemptState.COMMITTED
    assert journal.get(result.attempt.attempt_id) == result.attempt
    assert result.attempt.operation_id == prepared.attempt.operation_id
    assert fixture["api"].patch_calls == 1
    assert len(reader.calls) == 1


@pytest.mark.parametrize(("field", "value"), [
    ("policy_effect", "DENY"),
    ("approval_state", "REVOKED"),
    ("approval_state", "UNKNOWN"),
    ("principal_state", "REVOKED"),
    ("operation_state", "CANCELLED"),
    ("tenant_id", "other-tenant"),
    ("project_id", "other-project"),
    ("cluster_id", "other-cluster"),
    ("work_id", "other-work"),
    ("operation_id", "other-operation"),
    ("nonce", "replayed-nonce"),
    ("resource_uid", "other-uid"),
    ("resource_generation", 8),
    ("policy_version", "stale-policy"),
    ("policy_input_hash", "unrelated-policy-input"),
    ("policy_decision_hash", "unrelated-policy-decision"),
    ("approval_hash", "unrelated-approval"),
    ("authorization_hash", "unrelated-authorization"),
    ("lease_epoch", 10),
    ("lease_id", "old-lease"),
    ("transition_hash", "other-transition"),
    ("action_hash", "other-action"),
    ("version", 0),
])
def test_stale_revoked_or_cross_scope_authority_denies_patch(
    tmp_path, field, value,
):
    scenario = _scenario(tmp_path)
    reader = scenario[-1]
    reader.snapshot = replace(reader.snapshot, **{field: value})
    _assert_not_applied(scenario)


@pytest.mark.parametrize("snapshot", [None, False, "PERMIT", {"policy_effect": "PERMIT"}])
def test_missing_or_forged_authority_record_denied(tmp_path, snapshot):
    scenario = _scenario(tmp_path)
    scenario[-1].snapshot = snapshot
    _assert_not_applied(scenario)


def test_reader_outage_is_deny_not_soft_success(tmp_path):
    scenario = _scenario(tmp_path)
    scenario[-1].error = RuntimeError("authoritative service unavailable")
    _assert_not_applied(scenario)


@pytest.mark.parametrize(("field", "value"), [
    ("observed_at", CHECK_AT - timedelta(minutes=1)),
    ("observed_at", CHECK_AT + timedelta(seconds=1)),
    ("observed_at", NOW.replace(tzinfo=None)),
    ("valid_until", CHECK_AT),
    ("valid_until", NOW.replace(tzinfo=None)),
])
def test_stale_or_invalid_time_denies_patch(tmp_path, field, value):
    scenario = _scenario(tmp_path)
    reader = scenario[-1]
    reader.snapshot = replace(reader.snapshot, **{field: value})
    _assert_not_applied(scenario)


def test_effect_gate_clock_failure_denies_patch(tmp_path):
    scenario = _scenario(tmp_path)
    context = scenario[3]
    def clock_failure():
        raise RuntimeError("untrusted clock outage")
    altered = replace(context, live_effect_gate=replace(
        context.live_effect_gate, clock=clock_failure,
    ))
    changed = list(scenario)
    changed[3] = altered
    # Trusted clock failure aborts before contacting the authority reader.
    _assert_not_applied(tuple(changed), expected_reads=0)


def test_operator_scope_is_not_taken_from_request_or_dsse(tmp_path):
    scenario = _scenario(tmp_path)
    context = scenario[3]
    bad = replace(
        context, live_effect_gate=replace(
            context.live_effect_gate,
            scope=replace(SCOPE, nonce="attacker-selected-nonce"),
        ),
    )
    changed = list(scenario)
    changed[3] = bad
    _assert_not_applied(tuple(changed))


def test_policy_principal_and_controller_remain_distinct(tmp_path):
    scenario = _scenario(tmp_path)
    fixture, _, _, _, _, _, _, _, reader = scenario
    assert fixture["authorization"].principal != fixture["holder"]
    reader.snapshot = replace(
        reader.snapshot, executor=fixture["authorization"].principal,
    )
    _assert_not_applied(scenario)


def test_denied_effect_cannot_be_represented_as_committed_success(tmp_path):
    scenario = _scenario(tmp_path)
    scenario[-1].snapshot = replace(
        scenario[-1].snapshot, approval_state="REVOKED",
    )
    _assert_not_applied(scenario)
    fixture, _, _, context, coordinator, journal, _, prepared, _ = scenario
    with pytest.raises(ProtocolViolation):
        _execute(coordinator, fixture, context)
    assert fixture["api"].patch_calls == 0
    assert journal.get(prepared.attempt.attempt_id).state is ExecutionAttemptState.ABORTED



class LockedAuthorityReader(AuthoritativeReaderFixture):
    """Test adapter modeling an authority row lock held across the provider call."""

    def __init__(self, snapshot):
        super().__init__(snapshot)
        self.hold_calls = []
        self.inside_lock = False

    @contextmanager
    def hold_current(self, **kwargs):
        self.hold_calls.append(kwargs)
        self.inside_lock = True
        try:
            yield self.snapshot
        finally:
            self.inside_lock = False


def _with_locked_reader(scenario):
    _, _, _, context, _, _, _, _, original_reader = scenario
    reader = LockedAuthorityReader(original_reader.snapshot)
    gate = replace(context.live_effect_gate, reader=reader)
    changed = list(scenario)
    changed[3] = replace(context, live_effect_gate=gate)
    changed[-1] = reader
    return tuple(changed), reader


def _call_live_gate(scenario, patch):
    fixture, _, proposal, context, _, _, _, prepared, _ = scenario
    return context.live_effect_gate.invoke_with_current(
        invoke=patch,
        work_id=proposal.work_id,
        operation_id=prepared.attempt.operation_id,
        subject=fixture["resource"],
        resource_generation=fixture["transition"].expected_generation,
        action=fixture["action"],
        authorization=fixture["authorization"],
        approval_hash=context.signed_approval.approval.approval_hash,
        policy_input_hash=context.policy_input.input_hash,
        active_lease=fixture["lease"],
        caller=fixture["holder"],
    )


def test_locked_current_authority_is_the_snapshot_used_for_patch(tmp_path):
    scenario, reader = _with_locked_reader(_scenario(tmp_path))
    fixture, _, _, _, _, _, _, prepared, _ = scenario

    def patch_under_lock():
        assert reader.inside_lock is True
        assert fixture["api"].patch_calls == 0
        return "provider-result"

    result = _call_live_gate(scenario, patch_under_lock)

    assert result == "provider-result"
    assert reader.hold_calls == [{
        "tenant_id": SCOPE.tenant_id,
        "project_id": SCOPE.project_id,
        "cluster_id": SCOPE.cluster_id,
        "work_id": scenario[2].work_id,
        "operation_id": prepared.attempt.operation_id,
    }]
    # The gate must validate the yielded, locked row—not perform a second
    # unprotected read that would reintroduce a revocation race.
    assert reader.calls == []
    assert reader.inside_lock is False


def test_revoked_locked_authority_denies_before_provider_patch(tmp_path):
    scenario, reader = _with_locked_reader(_scenario(tmp_path))
    reader.snapshot = replace(reader.snapshot, approval_state="REVOKED")

    def must_not_patch():
        pytest.fail("provider patch called after locked authority was revoked")

    with pytest.raises(LiveEffectAdmissionDenied):
        _call_live_gate(scenario, must_not_patch)

    assert reader.calls == []
    assert len(reader.hold_calls) == 1
    assert reader.inside_lock is False


def test_provider_exception_after_patch_entry_is_not_relabelled_not_applied(
    tmp_path,
):
    scenario, reader = _with_locked_reader(_scenario(tmp_path))

    def ambiguous_provider_call():
        assert reader.inside_lock is True
        raise RuntimeError("provider timed out after request dispatch")

    with pytest.raises(RuntimeError, match="after request dispatch") as error:
        _call_live_gate(scenario, ambiguous_provider_call)

    assert not isinstance(error.value, LiveEffectAdmissionDenied)
    assert reader.calls == []
    assert len(reader.hold_calls) == 1
    assert reader.inside_lock is False



class BrokenLockedCapabilityReader(AuthoritativeReaderFixture):
    @property
    def hold_current(self):
        raise RuntimeError("cannot inspect authority lock capability")


def test_broken_lock_capability_fails_closed_before_provider_patch(tmp_path):
    scenario = _scenario(tmp_path)
    reader = BrokenLockedCapabilityReader(scenario[-1].snapshot)
    changed = list(scenario)
    changed[3] = replace(
        scenario[3],
        live_effect_gate=replace(scenario[3].live_effect_gate, reader=reader),
    )
    changed[-1] = reader

    def must_not_patch():
        pytest.fail("provider patch called when lock capability discovery failed")

    with pytest.raises(LiveEffectAdmissionDenied, match="lock capability"):
        _call_live_gate(tuple(changed), must_not_patch)

    assert reader.calls == []
