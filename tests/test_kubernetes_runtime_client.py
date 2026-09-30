from agent_control_plane.kubernetes_runtime_client import KubernetesSandboxClient


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
