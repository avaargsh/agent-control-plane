from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Mapping

from .correlation import CorrelationContext
from .provider_runtime import ExternalResource


def project_temporal_start(
    run: Mapping[str, Any],
    resource: ExternalResource,
    *,
    trace_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Project a provider start receipt into canonical Run state and evidence.

    Canonical Session/Run IDs remain owned by the control plane. Temporal's
    execution identifiers are copied only into providerRefs and evidence.
    """
    projected = deepcopy(dict(run))
    metadata = projected.get("metadata", {})
    spec = projected.get("spec", {})
    run_id = str(metadata.get("id", "")).strip()
    session_id = str(spec.get("sessionRef", "")).strip()
    release_id = str(spec.get("releaseRef", "")).strip()
    if not run_id or not session_id or not release_id:
        raise ValueError("Run requires canonical id, sessionRef, and releaseRef")

    external_refs = {
        str(key): str(value)
        for key, value in resource.external_refs.items()
        if value
    }
    provider_refs = dict(spec.get("providerRefs", {}))
    provider_refs.update(external_refs)
    spec["providerRefs"] = provider_refs
    spec["status"] = "running"
    projected["spec"] = spec

    correlation = CorrelationContext(
        release_id=release_id,
        run_id=run_id,
        session_id=session_id,
        trace_id=trace_id,
    )
    provenance = correlation.provider_attributes(external_refs)
    provenance["provider.resource_ref"] = resource.resource_ref

    evidence_payload = {
        "resource_ref": resource.resource_ref,
        "changed": resource.changed,
        "external_refs": external_refs,
        "evidence": resource.evidence,
        "correlation": provenance,
    }
    encoded = json.dumps(
        evidence_payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    evidence_id = "evidence-" + hashlib.sha256(encoded).hexdigest()[:24]

    evidence_ref = {
        "apiVersion": "agentplane.io/v1alpha1",
        "kind": "EvidenceRef",
        "metadata": {"id": evidence_id},
        "spec": {
            "uri": f"evidence://run/{run_id}/temporal-start/{evidence_id}",
            "mediaType": "application/json",
            "checksum": "sha256:" + hashlib.sha256(encoded).hexdigest(),
            "provenance": provenance,
            "immutable": True,
        },
    }
    refs = list(spec.get("evidenceRefs", []))
    if evidence_id not in refs:
        refs.append(evidence_id)
    spec["evidenceRefs"] = refs

    return projected, evidence_ref
