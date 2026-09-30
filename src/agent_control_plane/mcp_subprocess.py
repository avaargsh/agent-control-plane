from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from .tool_contract import RetryableToolError, ToolContractViolation


class MCPSubprocessToolAdapter:
    """Minimal MCP stdio JSON-RPC client used by the execution-contract proof.

    Each tool call starts a fresh provider process. The provider-owned state file
    deliberately survives transport/process loss so retries exercise an external
    idempotency boundary rather than in-process memory.
    """

    def __init__(
        self,
        *,
        state_path: str | Path,
        timeout_seconds: float = 1.0,
        fault_sleep_seconds: float = 3.0,
    ) -> None:
        self.state_path = Path(state_path)
        self.timeout_seconds = timeout_seconds
        self.fault_sleep_seconds = fault_sleep_seconds

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        fault_mode: str = "none",
    ) -> dict[str, Any]:
        env = os.environ.copy()
        env["MCP_FAULT_MODE"] = fault_mode
        env["MCP_FAULT_SLEEP"] = str(self.fault_sleep_seconds)

        messages = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "agentos-execution-contract-proof",
                        "version": "0.1.0",
                    },
                },
            },
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            },
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": name,
                    "arguments": arguments,
                },
            },
        ]
        wire_input = "".join(
            json.dumps(message, separators=(",", ":")) + "\n"
            for message in messages
        )

        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "agent_control_plane.mcp_fault_service",
                "--state",
                str(self.state_path),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        try:
            stdout, stderr = process.communicate(
                input=wire_input,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.communicate()
            raise RetryableToolError(
                f"MCP tool call timed out: {name}"
            ) from exc

        responses = [
            json.loads(line)
            for line in stdout.splitlines()
            if line.strip()
        ]
        tool_response = next(
            (response for response in responses if response.get("id") == 2),
            None,
        )
        if tool_response is None:
            raise RetryableToolError(
                f"MCP provider exited before tool acknowledgement: "
                f"{name}; exit={process.returncode}; stderr={stderr.strip()}"
            )
        if "error" in tool_response:
            raise ToolContractViolation(
                f"MCP tool error for {name}: "
                f"{tool_response['error'].get('message')}"
            )

        result = tool_response["result"]
        if result.get("isError"):
            raise ToolContractViolation(f"MCP tool returned isError: {name}")
        structured = result.get("structuredContent")
        if not isinstance(structured, dict):
            raise ToolContractViolation(
                f"MCP tool did not return structuredContent: {name}"
            )
        return structured

    def verify(self, idempotency_key: str) -> dict[str, Any] | None:
        observed = self.call_tool(
            "deploy.status",
            {"idempotency_key": idempotency_key},
        )
        if observed.get("committed") is True:
            return observed
        return None

    def rollback(
        self,
        idempotency_key: str,
        _result: Any,
    ) -> dict[str, Any]:
        return self.call_tool(
            "deploy.rollback",
            {"idempotency_key": idempotency_key},
        )
