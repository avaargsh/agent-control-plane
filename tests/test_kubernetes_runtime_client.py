import pytest

from agent_control_plane.kubernetes_runtime_client import KubernetesSandboxClient
from agent_control_plane.runtime_clients import RuntimeMutationUncertain


class FakeApi:
    def __init__(self):
        self.resources = {}
        self.apply_calls = 0

    def get(self, *, namespace, name):
        return self.resources.get((namespace, name))

    def apply(self, *, namespace, manifest):
        self.apply_calls += 1
        value = {
            **manifest,
            "metadata": {
                **manifest["metadata"],
                "uid": "sandbox-uid-001",
                "resourceVersion": "7",
            },
        }
        # Simulate an API read that returns the desired resource shape on retry.
        self.resources[(namespace, manifest["metadata"]["name"])] = manifest
        return value

    def delete(self, *, namespace, name):
        self.resources.pop((namespace, name), None)
        return {"deleted": True, "name": name}


def desired():
    return {
        "release": "gpu-xid-remediation-v1",
        "isolation": "gvisor",
        "warm_pool": True,
        "placement": {"target": "gpu-cell-a"},
    }


def test_kubernetes_client_creates_sandbox_and_records_identity():
    api = FakeApi()
    client = KubernetesSandboxClient(api)

    result = client.ensure_sandbox(desired())

    assert result.changed is True
    assert result.resource_ref.endswith("/gpu-xid-remediation-v1-sandbox")
    assert result.evidence["uid"] == "sandbox-uid-001"


def test_kubernetes_client_is_idempotent_when_desired_resource_exists():
    api = FakeApi()
    client = KubernetesSandboxClient(api)

    first = client.ensure_sandbox(desired())
    second = client.ensure_sandbox(desired())

    assert first.changed is True
    assert second.changed is False
    assert api.apply_calls == 1


def test_kubernetes_client_delete_uses_resource_identity():
    api = FakeApi()
    client = KubernetesSandboxClient(api)
    result = client.ensure_sandbox(desired())

    deleted = client.delete_sandbox(result.resource_ref)

    assert deleted["deleted"] is True
    assert deleted["name"] == "gpu-xid-remediation-v1-sandbox"



def test_kubernetes_client_ignores_server_managed_fields_for_idempotency():
    api = FakeApi()
    client = KubernetesSandboxClient(api)
    first = client.ensure_sandbox(desired())

    name = first.resource_ref.rsplit("/", 1)[-1]
    existing = api.resources[("agent-runtime", name)]
    api.resources[("agent-runtime", name)] = {
        **existing,
        "metadata": {
            **existing["metadata"],
            "uid": "sandbox-uid-001",
            "resourceVersion": "99",
            "managedFields": [{"manager": "kube-controller-manager"}],
        },
        "status": {
            "phase": "Running",
        },
    }

    second = client.ensure_sandbox(desired())

    assert second.changed is False
    assert api.apply_calls == 1


def test_kubernetes_client_still_updates_managed_spec_drift():
    api = FakeApi()
    client = KubernetesSandboxClient(api)
    first = client.ensure_sandbox(desired())

    name = first.resource_ref.rsplit("/", 1)[-1]
    existing = api.resources[("agent-runtime", name)]
    api.resources[("agent-runtime", name)] = {
        **existing,
        "spec": {
            **existing["spec"],
            "isolation": "none",
        },
        "status": {"phase": "Running"},
    }

    second = client.ensure_sandbox(desired())

    assert second.changed is True
    assert api.apply_calls == 2



def test_kubernetes_client_ignores_defaulted_unmanaged_spec_fields():
    api = FakeApi()
    client = KubernetesSandboxClient(api)
    first = client.ensure_sandbox(desired())

    name = first.resource_ref.rsplit("/", 1)[-1]
    existing = api.resources[("agent-runtime", name)]
    api.resources[("agent-runtime", name)] = {
        **existing,
        "spec": {
            **existing["spec"],
            "runtimeClassName": "gvisor",
            "restartPolicy": "Never",
        },
    }

    second = client.ensure_sandbox(desired())

    assert second.changed is False
    assert api.apply_calls == 1



def test_kubernetes_client_records_create_update_and_restores_previous_state():
    api = FakeApi()
    client = KubernetesSandboxClient(api)

    created = client.ensure_sandbox(desired())
    assert created.evidence["changeType"] == "created"

    name = created.resource_ref.rsplit("/", 1)[-1]
    existing = api.resources[("agent-runtime", name)]
    api.resources[("agent-runtime", name)] = {
        **existing,
        "spec": {
            **existing["spec"],
            "isolation": "none",
        },
    }

    updated = client.ensure_sandbox(desired())
    assert updated.changed is True
    assert updated.evidence["changeType"] == "updated"
    previous = updated.evidence["previousManaged"]
    assert previous["spec"]["isolation"] == "none"

    restored = client.restore_sandbox(previous)
    assert restored["restored"] is True
    live = api.resources[("agent-runtime", name)]
    assert live["spec"]["isolation"] == "none"


class LostAckCreateApi(FakeApi):
    def apply(self, *, namespace, manifest):
        value = super().apply(namespace=namespace, manifest=manifest)
        raise RuntimeMutationUncertain("connection reset after commit")


def test_kubernetes_client_recovers_create_receipt_after_lost_ack():
    api = LostAckCreateApi()
    client = KubernetesSandboxClient(api)

    result = client.ensure_sandbox(desired())

    assert result.changed is True
    assert result.evidence["changeType"] == "created"
    assert result.evidence["verifiedAfterUncertainMutation"] is True
    assert result.resource_ref.endswith("/gpu-xid-remediation-v1-sandbox")


class LostAckUpdateApi(FakeApi):
    def __init__(self):
        super().__init__()
        self.raise_after_apply = False

    def apply(self, *, namespace, manifest):
        value = super().apply(namespace=namespace, manifest=manifest)
        if self.raise_after_apply:
            raise RuntimeMutationUncertain("unexpected EOF after update")
        return value


def test_kubernetes_client_recovers_update_receipt_and_previous_state_after_lost_ack():
    api = LostAckUpdateApi()
    client = KubernetesSandboxClient(api)
    created = client.ensure_sandbox(desired())
    name = created.resource_ref.rsplit("/", 1)[-1]
    api.resources[("agent-runtime", name)] = {
        **api.resources[("agent-runtime", name)],
        "spec": {
            **api.resources[("agent-runtime", name)]["spec"],
            "isolation": "none",
        },
    }
    api.raise_after_apply = True

    result = client.ensure_sandbox(desired())

    assert result.changed is True
    assert result.evidence["changeType"] == "updated"
    assert result.evidence["previousManaged"]["spec"]["isolation"] == "none"
    assert result.evidence["verifiedAfterUncertainMutation"] is True


class UncommittedUncertainApi(FakeApi):
    def apply(self, *, namespace, manifest):
        raise RuntimeMutationUncertain("connection refused before verification")


def test_kubernetes_client_propagates_uncertain_error_when_postcondition_is_absent():
    api = UncommittedUncertainApi()
    client = KubernetesSandboxClient(api)

    with pytest.raises(RuntimeMutationUncertain):
        client.ensure_sandbox(desired())


class LostAckDeleteApi(FakeApi):
    def delete(self, *, namespace, name):
        super().delete(namespace=namespace, name=name)
        raise RuntimeMutationUncertain("connection reset after delete")


def test_kubernetes_delete_recovers_after_lost_ack():
    api = LostAckDeleteApi()
    client = KubernetesSandboxClient(api)
    result = client.ensure_sandbox(desired())

    deleted = client.delete_sandbox(result.resource_ref)

    assert deleted["deleted"] is True
    assert deleted["verifiedAfterUncertainMutation"] is True


class UncommittedDeleteApi(FakeApi):
    def delete(self, *, namespace, name):
        raise RuntimeMutationUncertain("connection refused")


def test_kubernetes_delete_propagates_when_resource_still_exists():
    api = UncommittedDeleteApi()
    client = KubernetesSandboxClient(api)
    result = client.ensure_sandbox(desired())

    with pytest.raises(RuntimeMutationUncertain):
        client.delete_sandbox(result.resource_ref)


class LostAckRestoreApi(FakeApi):
    def __init__(self):
        super().__init__()
        self.raise_after_apply = False

    def apply(self, *, namespace, manifest):
        value = super().apply(namespace=namespace, manifest=manifest)
        if self.raise_after_apply:
            raise RuntimeMutationUncertain("unexpected EOF after restore")
        return value


def test_kubernetes_restore_recovers_after_lost_ack():
    api = LostAckRestoreApi()
    client = KubernetesSandboxClient(api)
    created = client.ensure_sandbox(desired())
    name = created.resource_ref.rsplit("/", 1)[-1]
    previous = {
        "apiVersion": "agents.openai.com/v1alpha1",
        "kind": "Sandbox",
        "metadata": {
            "name": name,
            "namespace": "agent-runtime",
        },
        "spec": {
            "isolation": "none",
            "warmPool": False,
            "placement": {},
        },
    }
    api.raise_after_apply = True

    restored = client.restore_sandbox(previous)

    assert restored["restored"] is True
    assert restored["verifiedAfterUncertainMutation"] is True
    assert api.resources[("agent-runtime", name)]["spec"]["isolation"] == "none"
