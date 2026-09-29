from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping


class AlertmanagerIngressError(ValueError):
    pass


@dataclass(frozen=True)
class IncidentIngress:
    release_ref: str
    session_id: str
    run_id: str
    incident_key: str
    status: str
    payload: Mapping[str, Any]


def _stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


def compile_alertmanager_incidents(
    payload: Mapping[str, Any],
    *,
    release_ref: str,
) -> tuple[IncidentIngress, ...]:
    """Compile an Alertmanager webhook into deterministic incident run identities.

    Alertmanager may retry webhook delivery. Identity therefore derives from the
    notification group key plus the alert fingerprint rather than request time or
    a random UUID. Provider execution IDs remain external refs and are not used as
    canonical Agent Control Plane identities.
    """
    version = str(payload.get("version", ""))
    if version != "4":
        raise AlertmanagerIngressError(
            f"unsupported Alertmanager webhook version: {version or '<missing>'}"
        )

    group_key = str(payload.get("groupKey", "")).strip()
    if not group_key:
        raise AlertmanagerIngressError("Alertmanager webhook is missing groupKey")

    alerts = payload.get("alerts")
    if not isinstance(alerts, list) or not alerts:
        raise AlertmanagerIngressError("Alertmanager webhook has no alerts")

    session_id = _stable_id("incident-session", group_key)
    compiled: list[IncidentIngress] = []

    for alert in alerts:
        if not isinstance(alert, Mapping):
            raise AlertmanagerIngressError("Alertmanager alert must be an object")
        fingerprint = str(alert.get("fingerprint", "")).strip()
        if not fingerprint:
            raise AlertmanagerIngressError(
                "Alertmanager alert is missing fingerprint"
            )
        incident_key = f"{group_key}:{fingerprint}"
        compiled.append(
            IncidentIngress(
                release_ref=release_ref,
                session_id=session_id,
                run_id=_stable_id("incident-run", incident_key),
                incident_key=incident_key,
                status=str(alert.get("status", payload.get("status", "firing"))),
                payload=dict(alert),
            )
        )

    return tuple(compiled)
