from __future__ import annotations

from dataclasses import dataclass, replace


class SandboxRecoveryError(RuntimeError):
    pass


@dataclass(frozen=True)
class SandboxBindingState:
    run_id: str
    session_id: str
    sandbox_id: str
    generation: int = 1
    checkpoint_ref: str | None = None
    workspace_state_ref: str | None = None
    artifact_refs: tuple[str, ...] = ()


def checkpoint_binding(
    state: SandboxBindingState,
    *,
    checkpoint_ref: str,
    workspace_state_ref: str | None = None,
) -> SandboxBindingState:
    return replace(
        state,
        checkpoint_ref=checkpoint_ref,
        workspace_state_ref=workspace_state_ref or state.workspace_state_ref,
    )


def rebind_after_loss(
    state: SandboxBindingState,
    *,
    new_sandbox_id: str,
    require_checkpoint: bool = True,
) -> SandboxBindingState:
    if new_sandbox_id == state.sandbox_id:
        raise SandboxRecoveryError("replacement sandbox must have a new identity")
    if require_checkpoint and not state.checkpoint_ref:
        raise SandboxRecoveryError("checkpoint is required before sandbox rebind")
    return replace(
        state,
        sandbox_id=new_sandbox_id,
        generation=state.generation + 1,
    )
