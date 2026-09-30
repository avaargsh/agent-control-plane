from __future__ import annotations

from typing import Any, Mapping, Protocol
from uuid import uuid4

from .runtime_clients import (
    RuntimeApplyResult,
    RuntimeMutationOwnershipUncertain,
    RuntimeMutationUncertain,
)

_OPERATION_ID_ANNOTATION = "agent-control-plane.openai.com/operation-id"


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
        previous_managed = (
            _managed_sandbox_projection(existing)
            if existing is not None
            else None
        )
        changed = (
            existing is None
            or previous_managed != manifest
        )
        verified_after_uncertain_mutation = False
        operation_id: str | None = None
        if changed:
            operation_id = uuid4().hex
            apply_manifest = {
                **manifest,
                "metadata": {
                    **manifest["metadata"],
                    "annotations": {
                        _OPERATION_ID_ANNOTATION: operation_id,
                    },
                },
            }
            try:
                resource = self.api.apply(
                    namespace=self.namespace,
                    manifest=apply_manifest,
                )
            except RuntimeMutationUncertain as exc:
                observed = self.api.get(
                    namespace=self.namespace,
                    name=name,
                )
                if (
                    observed is None
                    or _managed_sandbox_projection(observed) != manifest
                ):
                    raise
                observed_metadata = observed.get("metadata")
                observed_annotations = (
                    observed_metadata.get("annotations", {})
                    if isinstance(observed_metadata, Mapping)
                    else {}
                )
                observed_operation_id = (
                    observed_annotations.get(_OPERATION_ID_ANNOTATION)
                    if isinstance(observed_annotations, Mapping)
                    else None
                )
                if observed_operation_id != operation_id:
                    raise RuntimeMutationOwnershipUncertain(
                        "sandbox postcondition exists after uncertain mutation "
                        "but operation ownership is not proven",
                        resource_ref=(
                            f"k8s://{self.namespace}/sandbox/{name}"
                        ),
                        operation_id=operation_id,
                        observed_operation_id=(
                            str(observed_operation_id)
                            if observed_operation_id is not None
                            else None
                        ),
                    ) from exc
                resource = observed
                verified_after_uncertain_mutation = True
        else:
            resource = existing

        metadata = dict((resource or {}).get("metadata", {}))
        change_type = (
            "created"
            if existing is None
            else ("updated" if changed else "unchanged")
        )
        evidence: dict[str, Any] = {
            "uid": metadata.get("uid"),
            "resourceVersion": metadata.get("resourceVersion"),
            "kind": "Sandbox",
            "changeType": change_type,
            "verifiedAfterUncertainMutation": (
                verified_after_uncertain_mutation
            ),
        }
        if operation_id is not None:
            evidence["operationId"] = operation_id
        if change_type == "updated":
            evidence["previousManaged"] = previous_managed
        return RuntimeApplyResult(
            resource_ref=f"k8s://{self.namespace}/sandbox/{name}",
            changed=changed,
            evidence=evidence,
        )

    def delete_sandbox(self, resource_ref: str) -> Mapping[str, Any]:
        name = resource_ref.rsplit("/", 1)[-1]
        try:
            return self.api.delete(
                namespace=self.namespace,
                name=name,
            )
        except RuntimeMutationUncertain:
            observed = self.api.get(
                namespace=self.namespace,
                name=name,
            )
            if observed is not None:
                raise
            return {
                "deleted": True,
                "name": name,
                "verifiedAfterUncertainMutation": True,
            }

    def restore_sandbox(
        self,
        previous: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        metadata = previous.get("metadata")
        if not isinstance(metadata, Mapping):
            raise ValueError("previous sandbox metadata is required")
        name = metadata.get("name")
        namespace = metadata.get("namespace")
        if not isinstance(name, str) or not name:
            raise ValueError("previous sandbox name is required")
        if namespace != self.namespace:
            raise ValueError("previous sandbox namespace mismatch")
        verified_after_uncertain_mutation = False
        operation_id = uuid4().hex
        restore_manifest = {
            **dict(previous),
            "metadata": {
                **dict(metadata),
                "annotations": {
                    **(
                        dict(metadata.get("annotations", {}))
                        if isinstance(metadata.get("annotations", {}), Mapping)
                        else {}
                    ),
                    _OPERATION_ID_ANNOTATION: operation_id,
                },
            },
        }
        try:
            resource = self.api.apply(
                namespace=self.namespace,
                manifest=restore_manifest,
            )
        except RuntimeMutationUncertain as exc:
            observed = self.api.get(
                namespace=self.namespace,
                name=name,
            )
            if (
                observed is None
                or _managed_sandbox_projection(observed)
                != _managed_sandbox_projection(previous)
            ):
                raise
            observed_metadata = observed.get("metadata")
            observed_annotations = (
                observed_metadata.get("annotations", {})
                if isinstance(observed_metadata, Mapping)
                else {}
            )
            observed_operation_id = (
                observed_annotations.get(_OPERATION_ID_ANNOTATION)
                if isinstance(observed_annotations, Mapping)
                else None
            )
            if observed_operation_id != operation_id:
                raise RuntimeMutationOwnershipUncertain(
                    "sandbox restore postcondition exists after uncertain mutation "
                    "but operation ownership is not proven",
                    resource_ref=(
                        f"k8s://{self.namespace}/sandbox/{name}"
                    ),
                    operation_id=operation_id,
                    observed_operation_id=(
                        str(observed_operation_id)
                        if observed_operation_id is not None
                        else None
                    ),
                ) from exc
            resource = observed
            verified_after_uncertain_mutation = True

        restored_metadata = dict(resource.get("metadata", {}))
        return {
            "restored": True,
            "name": name,
            "uid": restored_metadata.get("uid"),
            "resourceVersion": restored_metadata.get("resourceVersion"),
            "operationId": operation_id,
            "verifiedAfterUncertainMutation": (
                verified_after_uncertain_mutation
            ),
        }
