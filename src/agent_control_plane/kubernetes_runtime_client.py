from __future__ import annotations

from typing import Any, Mapping, Protocol

from .runtime_clients import RuntimeApplyResult


class KubernetesApi(Protocol):
    def get(self, *, namespace: str, name: str) -> Mapping[str, Any] | None:
        ...

    def apply(self, *, namespace: str, manifest: Mapping[str, Any]) -> Mapping[str, Any]:
        ...

    def delete(self, *, namespace: str, name: str) -> Mapping[str, Any]:
        ...


def _managed_sandbox_projection(
    resource: Mapping[str, Any],
) -> dict[str, Any]:
    metadata = resource.get("metadata")
    spec = resource.get("spec")
    return {
        "apiVersion": resource.get("apiVersion"),
        "kind": resource.get("kind"),
        "metadata": {
            "name": (
                metadata.get("name")
                if isinstance(metadata, Mapping)
                else None
            ),
            "namespace": (
                metadata.get("namespace")
                if isinstance(metadata, Mapping)
                else None
            ),
        },
        "spec": {
            "isolation": (
                spec.get("isolation")
                if isinstance(spec, Mapping)
                else None
            ),
            "warmPool": (
                spec.get("warmPool")
                if isinstance(spec, Mapping)
                else None
            ),
            "placement": (
                dict(spec.get("placement", {}))
                if isinstance(spec, Mapping)
                and isinstance(spec.get("placement", {}), Mapping)
                else {}
            ),
        },
    }


class KubernetesSandboxClient:
    """SDK-neutral client for reconciling a sandbox custom resource."""

    def __init__(self, api: KubernetesApi, *, namespace: str = "agent-runtime") -> None:
        self.api = api
        self.namespace = namespace

    def ensure_sandbox(self, desired: Mapping[str, Any]) -> RuntimeApplyResult:
        release = str(desired["release"])
        name = f"{release}-sandbox"
        existing = self.api.get(namespace=self.namespace, name=name)
        manifest = {
            "apiVersion": "agents.openai.com/v1alpha1",
            "kind": "Sandbox",
            "metadata": {"name": name, "namespace": self.namespace},
            "spec": {
                "isolation": desired.get("isolation", "gvisor"),
                "warmPool": bool(desired.get("warm_pool", False)),
                "placement": dict(desired.get("placement", {})),
            },
        }
        changed = (
            existing is None
            or _managed_sandbox_projection(existing) != manifest
        )
        resource = (
            self.api.apply(
                namespace=self.namespace,
                manifest=manifest,
            )
            if changed
            else existing
        )
        metadata = dict((resource or {}).get("metadata", {}))
        return RuntimeApplyResult(
            resource_ref=f"k8s://{self.namespace}/sandbox/{name}",
            changed=changed,
            evidence={
                "uid": metadata.get("uid"),
                "resourceVersion": metadata.get("resourceVersion"),
                "kind": "Sandbox",
            },
        )

    def delete_sandbox(self, resource_ref: str) -> Mapping[str, Any]:
        name = resource_ref.rsplit("/", 1)[-1]
        return self.api.delete(namespace=self.namespace, name=name)
