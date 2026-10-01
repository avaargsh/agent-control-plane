import json
from datetime import timedelta

import pytest

from agent_control_plane.execution_fencing import (
    DurableLeaseState,
    KubernetesDeploymentFenceProjector,
    SQLiteExecutionLeaseStore,
    acquire_fenced_execution_lease,
)
from agent_control_plane.kubernetes_deployment_transition import (
    KubernetesDeploymentScaleProvider,
)
from agent_control_plane.state_transition_protocol import (
    ExecutionFence,
    Principal,
    ProtocolViolation,
)
from test_kubernetes_deployment_transition import NOW, build_transition


class FencedFakeDeploymentApi:
    def __init__(self):
        self.patch_calls = 0
        self.spec_patch_calls = 0
        self.before_spec_patch_hook = None
        self.deployment = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": "payment-api",
                "namespace": "prod",
                "uid": "uid-payment-api",
                "generation": 7,
                "resourceVersion": "100",
                "annotations": {},
            },
            "spec": {
                "replicas": 20,
                "selector": {
                    "matchLabels": {
                        "app": "payment-api",
                    },
                },
            },
            "status": {
                "readyReplicas": 20,
            },
        }

    @staticmethod
    def _copy(value):
        return json.loads(json.dumps(value))

    def get_deployment(self, *, namespace, name):
        if namespace != "prod" or name != "payment-api":
            return None
        return self._copy(self.deployment)

    def patch_deployment(self, *, namespace, name, patch):
        assert namespace == "prod"
        assert name == "payment-api"
        self.patch_calls += 1

        is_spec_patch = "spec" in patch
        if is_spec_patch:
            self.spec_patch_calls += 1
            hook = self.before_spec_patch_hook
            self.before_spec_patch_hook = None
            if hook is not None:
                hook()

        expected_rv = patch["metadata"]["resourceVersion"]
        live_rv = self.deployment["metadata"]["resourceVersion"]
        if expected_rv != live_rv:
            raise ProtocolViolation(
                "kubernetes resourceVersion conflict"
            )

        annotations = patch["metadata"].get("annotations", {})
        self.deployment["metadata"]["annotations"].update(annotations)

        if is_spec_patch:
            replicas = patch["spec"]["replicas"]
            if replicas != self.deployment["spec"]["replicas"]:
                self.deployment["spec"]["replicas"] = replicas
                self.deployment["metadata"]["generation"] += 1

        self.deployment["metadata"]["resourceVersion"] = str(
            int(live_rv) + 1
        )
        return self._copy(self.deployment)

    def list_pods(self, *, namespace, selector):
        return []

    def list_events(self, *, namespace, involved_object_uid):
        return []


def _activate(
    store,
    api,
    *,
    holder,
    at,
):
    return acquire_fenced_execution_lease(
        authority=store,
        projector=KubernetesDeploymentFenceProjector(api),
        resource_uid="uid-payment-api",
        namespace="prod",
        name="payment-api",
        holder=holder,
        now=at,
        ttl_seconds=300,
    )


def _bind_fixture(fixture, lease):
    fixture["lease"] = lease
    fixture["holder"] = lease.holder
    fixture["fence"] = ExecutionFence.bind(
        transition=fixture["transition"],
        action=fixture["action"],
        authorization=fixture["authorization"],
        lease=lease,
    )
    return fixture


def _execute(fixture, store):
    return KubernetesDeploymentScaleProvider(
        fixture["api"],
        lease_authority=store,
    ).execute(
        transition=fixture["transition"],
        evidence=fixture["evidence"],
        outcome_contract=fixture["outcome"],
        action=fixture["action"],
        authorization=fixture["authorization"],
        fence=fixture["fence"],
        active_lease=fixture["lease"],
        caller=fixture["holder"],
        now=NOW + timedelta(seconds=10),
    )


def test_sqlite_lease_epoch_survives_process_restart(tmp_path):
    path = tmp_path / "leases.db"
    holder_a = Principal(type="controller", subject="controller-a")
    holder_b = Principal(type="controller", subject="controller-b")

    first_store = SQLiteExecutionLeaseStore(path)
    first = first_store.prepare(
        resource_uid="uid-payment-api",
        holder=holder_a,
        now=NOW,
        ttl_seconds=300,
    )
    assert first.lease.epoch == 1
    assert first.state is DurableLeaseState.PREPARING

    restarted_store = SQLiteExecutionLeaseStore(path)
    second = restarted_store.prepare(
        resource_uid="uid-payment-api",
        holder=holder_b,
        now=NOW + timedelta(seconds=1),
        ttl_seconds=300,
    )

    assert second.lease.epoch == 2
    assert second.lease.lease_id != first.lease.lease_id
    assert second.state is DurableLeaseState.PREPARING


def test_superseded_prepared_lease_cannot_activate(tmp_path):
    store = SQLiteExecutionLeaseStore(tmp_path / "leases.db")
    first = store.prepare(
        resource_uid="uid-payment-api",
        holder=Principal(type="controller", subject="controller-a"),
        now=NOW,
        ttl_seconds=300,
    )
    second = store.prepare(
        resource_uid="uid-payment-api",
        holder=Principal(type="controller", subject="controller-b"),
        now=NOW + timedelta(seconds=1),
        ttl_seconds=300,
    )

    with pytest.raises(
        ProtocolViolation,
        match="superseded before activation",
    ):
        store.activate(
            resource_uid="uid-payment-api",
            lease_id=first.lease.lease_id,
            epoch=first.lease.epoch,
            now=NOW + timedelta(seconds=2),
        )

    activated = store.activate(
        resource_uid="uid-payment-api",
        lease_id=second.lease.lease_id,
        epoch=second.lease.epoch,
        now=NOW + timedelta(seconds=2),
    )
    assert activated.state is DurableLeaseState.ACTIVE


def test_target_fence_projection_is_metadata_only_and_monotonic(tmp_path):
    api = FencedFakeDeploymentApi()
    store = SQLiteExecutionLeaseStore(tmp_path / "leases.db")
    holder = Principal(type="controller", subject="controller-a")

    lease = _activate(
        store,
        api,
        holder=holder,
        at=NOW,
    )

    assert lease.epoch == 1
    assert api.deployment["metadata"]["generation"] == 7
    assert api.deployment["metadata"]["resourceVersion"] == "101"
    annotations = api.deployment["metadata"]["annotations"]
    assert annotations[
        "agent-control-plane.openai.com/fence-epoch"
    ] == "1"
    assert annotations[
        "agent-control-plane.openai.com/fence-lease-id"
    ] == lease.lease_id


def test_provider_requires_durable_active_lease_and_target_epoch(tmp_path):
    api = FencedFakeDeploymentApi()
    fixture = build_transition(api)
    store = SQLiteExecutionLeaseStore(tmp_path / "leases.db")
    lease = _activate(
        store,
        api,
        holder=fixture["holder"],
        at=NOW,
    )
    _bind_fixture(fixture, lease)

    receipt = _execute(fixture, store)

    assert receipt.changed is True
    assert api.deployment["spec"]["replicas"] == 30
    assert api.spec_patch_calls == 1


def test_preparing_takeover_preserves_old_active_owner_until_projection(
    tmp_path,
):
    api = FencedFakeDeploymentApi()
    fixture = build_transition(api)
    store = SQLiteExecutionLeaseStore(tmp_path / "leases.db")
    lease_a = _activate(
        store,
        api,
        holder=fixture["holder"],
        at=NOW,
    )

    prepared_b = store.prepare(
        resource_uid="uid-payment-api",
        holder=Principal(type="controller", subject="controller-b"),
        now=NOW + timedelta(seconds=1),
        ttl_seconds=300,
    )

    active = store.assert_active(
        lease_a,
        now=NOW + timedelta(seconds=2),
    )
    assert active.state is DurableLeaseState.ACTIVE
    assert prepared_b.state is DurableLeaseState.PREPARING
    assert store.latest("uid-payment-api").lease.epoch == 2
    assert store.active("uid-payment-api").lease.epoch == 1
    assert api.deployment["metadata"]["annotations"][
        "agent-control-plane.openai.com/fence-epoch"
    ] == "1"


def test_takeover_between_provider_check_and_patch_fences_stale_controller(
    tmp_path,
):
    api = FencedFakeDeploymentApi()
    fixture = build_transition(api)
    store = SQLiteExecutionLeaseStore(tmp_path / "leases.db")

    lease_a = _activate(
        store,
        api,
        holder=fixture["holder"],
        at=NOW,
    )
    _bind_fixture(fixture, lease_a)

    holder_b = Principal(
        type="controller",
        subject="agent-control-plane/controller-b",
    )

    def takeover():
        lease_b = _activate(
            store,
            api,
            holder=holder_b,
            at=NOW + timedelta(seconds=2),
        )
        assert lease_b.epoch == lease_a.epoch + 1

    api.before_spec_patch_hook = takeover

    with pytest.raises(
        ProtocolViolation,
        match="resourceVersion conflict",
    ):
        _execute(fixture, store)

    # The takeover metadata write committed, but the stale controller's scale
    # mutation did not. Generation remains unchanged because only metadata
    # changed during fencing.
    assert api.deployment["spec"]["replicas"] == 20
    assert api.deployment["metadata"]["generation"] == 7
    assert api.deployment["metadata"]["annotations"][
        "agent-control-plane.openai.com/fence-epoch"
    ] == "2"
    assert api.spec_patch_calls == 1


def test_stale_controller_reading_after_takeover_is_rejected_before_patch(
    tmp_path,
):
    api = FencedFakeDeploymentApi()
    fixture = build_transition(api)
    store = SQLiteExecutionLeaseStore(tmp_path / "leases.db")

    lease_a = _activate(
        store,
        api,
        holder=fixture["holder"],
        at=NOW,
    )
    _bind_fixture(fixture, lease_a)

    lease_b = _activate(
        store,
        api,
        holder=Principal(
            type="controller",
            subject="agent-control-plane/controller-b",
        ),
        at=NOW + timedelta(seconds=2),
    )
    assert lease_b.epoch == 2

    with pytest.raises(
        ProtocolViolation,
        match="not ACTIVE: SUPERSEDED",
    ):
        _execute(fixture, store)

    assert api.spec_patch_calls == 0
    assert api.deployment["spec"]["replicas"] == 20


def test_target_fence_projection_is_idempotent_for_same_prepared_lease(tmp_path):
    api = FencedFakeDeploymentApi()
    store = SQLiteExecutionLeaseStore(tmp_path / "leases.db")
    prepared = store.prepare(
        resource_uid="uid-payment-api",
        holder=Principal(type="controller", subject="controller-a"),
        now=NOW,
        ttl_seconds=300,
    )
    projector = KubernetesDeploymentFenceProjector(api)

    first = projector.project(
        record=prepared,
        namespace="prod",
        name="payment-api",
    )
    first_rv = first["metadata"]["resourceVersion"]
    calls_after_first = api.patch_calls

    second = projector.project(
        record=prepared,
        namespace="prod",
        name="payment-api",
    )

    assert second["metadata"]["resourceVersion"] == first_rv
    assert api.patch_calls == calls_after_first


def test_equal_target_epoch_cannot_be_rebound_to_different_lease(tmp_path):
    api = FencedFakeDeploymentApi()
    store = SQLiteExecutionLeaseStore(tmp_path / "leases.db")
    prepared = store.prepare(
        resource_uid="uid-payment-api",
        holder=Principal(type="controller", subject="controller-a"),
        now=NOW,
        ttl_seconds=300,
    )
    projector = KubernetesDeploymentFenceProjector(api)
    projector.project(
        record=prepared,
        namespace="prod",
        name="payment-api",
    )

    api.deployment["metadata"]["annotations"][
        "agent-control-plane.openai.com/fence-lease-id"
    ] = "forged-lease"

    with pytest.raises(
        ProtocolViolation,
        match="already owned by another lease",
    ):
        projector.project(
            record=prepared,
            namespace="prod",
            name="payment-api",
        )


def test_crash_after_prepare_before_projection_keeps_old_owner_valid(
    tmp_path,
):
    api = FencedFakeDeploymentApi()
    fixture = build_transition(api)
    store = SQLiteExecutionLeaseStore(tmp_path / "leases.db")

    lease_a = _activate(
        store,
        api,
        holder=fixture["holder"],
        at=NOW,
    )

    prepared_b = store.prepare(
        resource_uid="uid-payment-api",
        holder=Principal(type="controller", subject="controller-b"),
        now=NOW + timedelta(seconds=1),
        ttl_seconds=300,
    )
    assert prepared_b.state is DurableLeaseState.PREPARING

    assert (
        store.assert_active(
            lease_a,
            now=NOW + timedelta(seconds=2),
        ).state
        is DurableLeaseState.ACTIVE
    )
    with pytest.raises(
        ProtocolViolation,
        match="not ACTIVE",
    ):
        store.assert_active(
            prepared_b.lease,
            now=NOW + timedelta(seconds=2),
        )

    assert api.deployment["metadata"]["annotations"][
        "agent-control-plane.openai.com/fence-epoch"
    ] == "1"


def test_crash_after_projection_before_activation_fences_old_target(
    tmp_path,
):
    api = FencedFakeDeploymentApi()
    fixture = build_transition(api)
    store = SQLiteExecutionLeaseStore(tmp_path / "leases.db")

    lease_a = _activate(
        store,
        api,
        holder=fixture["holder"],
        at=NOW,
    )
    _bind_fixture(fixture, lease_a)
    prepared_b = store.prepare(
        resource_uid="uid-payment-api",
        holder=Principal(type="controller", subject="controller-b"),
        now=NOW + timedelta(seconds=1),
        ttl_seconds=300,
    )
    KubernetesDeploymentFenceProjector(api).project(
        record=prepared_b,
        namespace="prod",
        name="payment-api",
    )

    # Durable authority still names A as ACTIVE until activation commits, but
    # target fencing has already made A unable to mutate this Deployment.
    assert (
        store.assert_active(
            lease_a,
            now=NOW + timedelta(seconds=2),
        ).state
        is DurableLeaseState.ACTIVE
    )
    with pytest.raises(
        ProtocolViolation,
        match="not ACTIVE",
    ):
        store.assert_active(
            prepared_b.lease,
            now=NOW + timedelta(seconds=2),
        )
    with pytest.raises(
        ProtocolViolation,
        match="target fence epoch",
    ):
        _execute(fixture, store)

    assert api.deployment["metadata"]["annotations"][
        "agent-control-plane.openai.com/fence-epoch"
    ] == "2"
    assert api.deployment["metadata"]["generation"] == 7
    assert api.deployment["spec"]["replicas"] == 20
