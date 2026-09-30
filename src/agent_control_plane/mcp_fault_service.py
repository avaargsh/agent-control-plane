from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "operations": {},
            "side_effect_count": 0,
            "rollback_count": 0,
        }
    return json.loads(path.read_text(encoding="utf-8"))


def _save(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(state, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


class FaultInjectedDeployService:
    def __init__(self, state_path: Path) -> None:
        self.state_path = state_path

    def _receipt(self, key: str, operation: dict[str, Any]) -> dict[str, Any]:
        return {
            "idempotency_key": key,
            "status": operation["status"],
            "deployment": operation.get("deployment"),
            "health": operation.get("health"),
            "apply_invocations": operation.get("apply_invocations", 0),
            "commit_count": operation.get("commit_count", 0),
            "rollback_count": operation.get("rollback_count", 0),
        }

    def apply(self, arguments: dict[str, Any]) -> dict[str, Any]:
        key = str(arguments["idempotency_key"])
        deployment = str(arguments.get("deployment", "v2"))
        health = str(arguments.get("health", "ok"))
        fault = os.environ.get("MCP_FAULT_MODE", "none")
        state = _load(self.state_path)
        operations = state["operations"]
        operation = operations.setdefault(
            key,
            {
                "status": "new",
                "deployment": deployment,
                "health": health,
                "apply_invocations": 0,
                "commit_count": 0,
                "rollback_count": 0,
            },
        )
        operation["apply_invocations"] += 1
        invocation = operation["apply_invocations"]
        _save(self.state_path, state)

        if operation["status"] == "committed":
            receipt = self._receipt(key, operation)
            receipt["duplicate"] = True
            return receipt

        if fault == "timeout_before_commit_once" and invocation == 1:
            time.sleep(float(os.environ.get("MCP_FAULT_SLEEP", "3")))

        if fault == "partial_commit_once" and invocation == 1:
            operation["status"] = "prepared"
            _save(self.state_path, state)
            os._exit(71)

        operation["status"] = "committed"
        operation["deployment"] = deployment
        operation["health"] = health
        if operation["commit_count"] == 0:
            operation["commit_count"] = 1
            state["side_effect_count"] += 1
        _save(self.state_path, state)

        if fault == "lost_ack_once" and invocation == 1:
            os._exit(70)

        receipt = self._receipt(key, operation)
        receipt["duplicate"] = False
        return receipt

    def status(self, arguments: dict[str, Any]) -> dict[str, Any]:
        key = str(arguments["idempotency_key"])
        state = _load(self.state_path)
        operation = state["operations"].get(key)
        if operation is None:
            return {
                "idempotency_key": key,
                "status": "missing",
                "committed": False,
            }
        receipt = self._receipt(key, operation)
        receipt["committed"] = operation["status"] == "committed"
        return receipt

    def rollback(self, arguments: dict[str, Any]) -> dict[str, Any]:
        key = str(arguments["idempotency_key"])
        state = _load(self.state_path)
        operation = state["operations"].get(key)
        if operation is None:
            raise ValueError("cannot rollback missing operation")
        if operation["status"] == "committed":
            operation["status"] = "rolled_back"
            operation["rollback_count"] += 1
            state["rollback_count"] += 1
            _save(self.state_path, state)
        return self._receipt(key, operation)


def _result(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, sort_keys=True),
            }
        ],
        "structuredContent": payload,
        "isError": False,
    }


def _handle(service: FaultInjectedDeployService, request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")

    if method == "notifications/initialized":
        return None
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "agentos-fault-injected-deploy",
                    "version": "0.1.0",
                },
            },
        }
    if method == "tools/list":
        tools = [
            {
                "name": "deploy.apply",
                "description": "Apply one idempotent deployment side effect.",
                "inputSchema": {
                    "type": "object",
                    "required": ["idempotency_key"],
                    "properties": {
                        "idempotency_key": {"type": "string"},
                        "deployment": {"type": "string"},
                        "health": {"type": "string"},
                    },
                },
            },
            {
                "name": "deploy.status",
                "description": "Read operation state by idempotency key.",
                "inputSchema": {
                    "type": "object",
                    "required": ["idempotency_key"],
                    "properties": {
                        "idempotency_key": {"type": "string"},
                    },
                },
            },
            {
                "name": "deploy.rollback",
                "description": "Compensate a committed deployment operation.",
                "inputSchema": {
                    "type": "object",
                    "required": ["idempotency_key"],
                    "properties": {
                        "idempotency_key": {"type": "string"},
                    },
                },
            },
        ]
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"tools": tools},
        }
    if method == "tools/call":
        params = request.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name == "deploy.apply":
            payload = service.apply(arguments)
        elif name == "deploy.status":
            payload = service.status(arguments)
        elif name == "deploy.rollback":
            payload = service.rollback(arguments)
        else:
            raise ValueError(f"unknown tool: {name}")
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": _result(payload),
        }

    if request_id is None:
        return None
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {
            "code": -32601,
            "message": f"method not found: {method}",
        },
    }


def serve_stdio(state_path: Path) -> None:
    service = FaultInjectedDeployService(state_path)
    for line in sys.stdin:
        if not line.strip():
            continue
        request = json.loads(line)
        try:
            response = _handle(service, request)
        except Exception as exc:
            if request.get("id") is None:
                continue
            response = {
                "jsonrpc": "2.0",
                "id": request["id"],
                "error": {
                    "code": -32000,
                    "message": str(exc),
                },
            }
        if response is not None:
            sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
            sys.stdout.flush()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", required=True, type=Path)
    args = parser.parse_args()
    serve_stdio(args.state)


if __name__ == "__main__":
    main()
