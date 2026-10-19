"""ACP v1 observation adapter seam, without making ACP an authority source.

Converts untrusted ACP session updates and permission prompts to descriptive
facts/intents. Execution and approval require the existing Control Plane policy,
exact-plan authorization, fencing, journal, and independent proof boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol


class AcpAdapterViolation(ValueError):
    """Malformed, unknown, or cross-session ACP provider event."""


@dataclass(frozen=True)
class AcpSessionBinding:
    tenant_id: str
    canonical_session_id: str
    provider_id: str
    provider_session_id: str

    def __post_init__(self) -> None:
        if not all((self.tenant_id, self.canonical_session_id, self.provider_id, self.provider_session_id)):
            raise AcpAdapterViolation("canonical tenant/session/provider binding is required")


@dataclass(frozen=True)
class ProviderObservation:
    """Never a policy decision, resource write acknowledgement, or proof."""

    binding: AcpSessionBinding
    kind: str
    data: dict[str, str]


@dataclass(frozen=True)
class PermissionIntent:
    """ACP's interactive option request; approval decision remains in CP."""

    binding: AcpSessionBinding
    tool_call_id: str
    option_ids: tuple[str, ...]


class AcpProviderAdapter(Protocol):
    """ACP compatibility seam; transport/process ownership stays with harness."""

    def update(
        self, binding: AcpSessionBinding, notification: Mapping[str, object]
    ) -> ProviderObservation: ...

    def permission_intent(
        self, binding: AcpSessionBinding, request: Mapping[str, object]
    ) -> PermissionIntent: ...


class AcpV1Normalizer:
    """Narrow provider-neutral adaptation of ACP notifications, no subprocess."""

    def update(self, binding: AcpSessionBinding, notification: Mapping[str, object]) -> ProviderObservation:
        if notification.get("sessionId") != binding.provider_session_id:
            raise AcpAdapterViolation("ACP session binding mismatch")
        raw = notification.get("update")
        if not isinstance(raw, Mapping):
            raise AcpAdapterViolation("ACP session update must be an object")
        kind = raw.get("sessionUpdate")
        if kind in ("agent_message_chunk", "agent_thought_chunk"):
            content = raw.get("content")
            if not isinstance(content, Mapping) or content.get("type") != "text":
                raise AcpAdapterViolation("only ACP text chunks are supported here")
            message = content.get("text")
            if not isinstance(message, str):
                raise AcpAdapterViolation("ACP chunk text must be a string")
            return ProviderObservation(
                binding,
                "chat.delta" if kind == "agent_message_chunk" else "chat.reasoning",
                {"text": message},
            )
        if kind in ("tool_call", "tool_call_update"):
            tool_id = raw.get("toolCallId")
            if not isinstance(tool_id, str) or not tool_id:
                raise AcpAdapterViolation("ACP tool call identifier is required")
            # Tool completion may not be treated as provider resource mutation proof.
            return ProviderObservation(binding, "tool.observed", {"tool_call_id": tool_id})
        if kind == "plan":
            return ProviderObservation(binding, "plan.observed", {})
        raise AcpAdapterViolation("unsupported ACP notification (fail closed)")

    def permission_intent(
        self,
        binding: AcpSessionBinding,
        request: Mapping[str, object],
    ) -> PermissionIntent:
        if request.get("sessionId") != binding.provider_session_id:
            raise AcpAdapterViolation("ACP permission session binding mismatch")
        tool = request.get("toolCall")
        options = request.get("options")
        if not isinstance(tool, Mapping) or not isinstance(options, list) or not options:
            raise AcpAdapterViolation("ACP tool and permission options are required")
        tool_id = tool.get("toolCallId")
        if not isinstance(tool_id, str) or not tool_id:
            raise AcpAdapterViolation("ACP permission toolCallId is required")
        ids: list[str] = []
        for option in options:
            if not isinstance(option, Mapping):
                raise AcpAdapterViolation("ACP option must be an object")
            option_id = option.get("optionId")
            if not isinstance(option_id, str) or not option_id or option_id in ids:
                raise AcpAdapterViolation("ACP option ids must be unique and nonempty")
            ids.append(option_id)
        return PermissionIntent(binding, tool_id, tuple(ids))
