#!/usr/bin/env python3
from __future__ import annotations

import json

from agent_control_plane.live_smoke import inspect_live_smoke_environment


def main() -> int:
    state = inspect_live_smoke_environment()
    print(json.dumps({
        "kubectl": state.kubectl,
        "kubeContext": state.kube_context,
        "kubeNamespace": state.kube_namespace,
        "temporalAddress": state.temporal_address,
        "temporalReachable": state.temporal_reachable,
        "ready": state.ready,
    }, indent=2))
    return 0 if state.ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
