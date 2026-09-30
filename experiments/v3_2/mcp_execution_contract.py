from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import tempfile

from agent_control_plane.evidence_provenance import AppendOnlyEvidenceStore
from agent_control_plane.mcp_subprocess import MCPSubprocessToolAdapter
from agent_control_plane.tool_contract import execute_with_contract


CONTRACT = {
    "spec": {
        "tool": "deploy",
        "operation": "apply",
        "effect": "external-side-effect",
        "idempotency": {
            "mode": "required",
            "keyTemplate": "{run_id}:{action_id}",
        },
        "retry": {
            "maxAttempts": 3,
            "retryableErrors": [
                "lost-ack",
                "timeout",
                "provider-exit",
            ],
        },
        "verification": {
            "mode": "read-after-write",
            "tool": "deploy",
            "operation": "status",
        },
        "compensation": {
            "tool": "deploy",
            "operation": "rollback",
        },
    }
}


def evidence_event(event_id: str, scenario: str, payload: dict):
    payload_bytes = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    payload_digest = "sha256:" + sha256(payload_bytes).hexdigest()
    return {
        "apiVersion": "agentplane.io/v1alpha1",
        "kind": "EvidenceEvent",
        "metadata": {"id": event_id},
        "spec": {
            "runRef": "run-mcp-execution-contract",
            "actionId": scenario,
            "eventType": "tool.execution.receipt",
            "occurredAt": "2026-09-30T09:00:00Z",
            "payloadRef": f"evidence://mcp-execution-contract/{event_id}",
            "payloadDigest": payload_digest,
            "provenance": {
                "generated_by": "mcp-side-effect-provider",
                "observed_by": "execution-contract-proof",
                "authorized_by": "tool-contract",
                "executed_by": "mcp-stdio-jsonrpc",
                "committed_by": "fault-injected-deploy-service",
            },
        },
    }


def run_scenario(
    root: Path,
    *,
    scenario: str,
    fault_mode: str = "none",
    health: str = "ok",
    timeout: float = 1.0,
) -> tuple[dict, dict]:
    state_path = root / f"{scenario}.json"
    adapter = MCPSubprocessToolAdapter(
        state_path=state_path,
        timeout_seconds=timeout,
        fault_sleep_seconds=2.0,
    )
    result = execute_with_contract(
        run_id="run-mcp-execution-contract",
        action_id=scenario,
        contract=CONTRACT,
        invoke=lambda key: adapter.call_tool(
            "deploy.apply",
            {
                "idempotency_key": key,
                "deployment": "v2",
                "health": health,
            },
            fault_mode=fault_mode,
        ),
        verify=adapter.verify,
        validate_result=lambda receipt: receipt.get("health") == "ok",
        compensate=adapter.rollback,
    )
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    return asdict(result), persisted


def main() -> None:
    evidence = AppendOnlyEvidenceStore()
    with tempfile.TemporaryDirectory(prefix="agentos-mcp-proof-") as tmp:
        root = Path(tmp)
        cases = [
            ("lost_ack", "lost_ack_once", "ok", 1.0),
            (
                "duplicate_retry",
                "lost_ack_stale_verify_once",
                "ok",
                1.0,
            ),
            ("timeout", "timeout_before_commit_once", "ok", 0.3),
            ("partial_commit", "partial_commit_once", "ok", 1.0),
            ("compensation", "none", "failed", 1.0),
        ]

        for index, (scenario, fault, health, timeout) in enumerate(cases, 1):
            result, persisted = run_scenario(
                root,
                scenario=scenario,
                fault_mode=fault,
                health=health,
                timeout=timeout,
            )
            operation = persisted["operations"][result["idempotency_key"]]
            payload = {
                "scenario": scenario,
                "result": result,
                "provider_state": {
                    "status": operation["status"],
                    "apply_invocations": operation["apply_invocations"],
                    "commit_count": operation["commit_count"],
                    "rollback_count": operation["rollback_count"],
                    "side_effect_count": persisted["side_effect_count"],
                },
            }
            record = evidence.append(
                evidence_event(
                    f"mcp-proof-{index}",
                    scenario,
                    payload,
                )
            )
            print(
                json.dumps(
                    {
                        "scenario": scenario,
                        "attempts": result["attempts"],
                        "verified_after_error": result[
                            "verified_after_error"
                        ],
                        "compensated": result["compensated"],
                        "provider_status": operation["status"],
                        "side_effect_count": persisted["side_effect_count"],
                        "evidence_digest": record.digest,
                    },
                    sort_keys=True,
                )
            )

        evidence.verify()
        print(f"evidence_events={len(evidence.records)}")
        print(f"evidence_head={evidence.records[-1].digest}")
        print("PASS")


if __name__ == "__main__":
    main()
