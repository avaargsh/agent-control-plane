from __future__ import annotations

import os
import shutil
import socket
from dataclasses import dataclass


def _env(primary: str, legacy: str | None = None) -> str | None:
    value = os.getenv(primary)
    if value:
        return value
    return os.getenv(legacy) if legacy else None


@dataclass(frozen=True)
class SmokePreflight:
    kubectl: bool
    kube_context: str | None
    kube_namespace: str | None
    temporal_address: str | None
    temporal_reachable: bool

    @property
    def ready(self) -> bool:
        return bool(
            self.kubectl
            and self.kube_context
            and self.kube_namespace
            and self.temporal_address
            and self.temporal_reachable
        )


def inspect_live_smoke_environment() -> SmokePreflight:
    address = _env(
        "TEMPORAL_ADDRESS",
        "ACP_TEMPORAL_ADDRESS",
    )
    reachable = False
    if address:
        host, _, port = address.rpartition(":")
        if host and port.isdigit():
            try:
                with socket.create_connection(
                    (host, int(port)),
                    timeout=0.25,
                ):
                    reachable = True
            except OSError:
                pass
    return SmokePreflight(
        kubectl=shutil.which("kubectl") is not None,
        kube_context=_env(
            "KUBE_CONTEXT",
            "ACP_KUBE_CONTEXT",
        ),
        kube_namespace=_env(
            "KUBE_NAMESPACE",
            "ACP_KUBE_NAMESPACE",
        ),
        temporal_address=address,
        temporal_reachable=reachable,
    )
