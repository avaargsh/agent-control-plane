import pytest

from agent_control_plane.sandbox_binding import (
    SandboxBindingState,
    SandboxRecoveryError,
    checkpoint_binding,
    rebind_after_loss,
)


def test_sandbox_can_change_without_changing_run_or_session_identity() -> None:
    before = SandboxBindingState(
        run_id="run-001",
        session_id="session-001",
        sandbox_id="sandbox-a",
        artifact_refs=("artifact://step-3",),
    )
    checkpointed = checkpoint_binding(
        before,
        checkpoint_ref="checkpoint://step-3",
        workspace_state_ref="state://workspace/step-3",
    )
    recovered = rebind_after_loss(checkpointed, new_sandbox_id="sandbox-b")

    assert recovered.run_id == before.run_id
    assert recovered.session_id == before.session_id
    assert recovered.sandbox_id == "sandbox-b"
    assert recovered.generation == 2
    assert recovered.checkpoint_ref == "checkpoint://step-3"
    assert recovered.artifact_refs == ("artifact://step-3",)


def test_rebind_fails_closed_without_checkpoint() -> None:
    state = SandboxBindingState(
        run_id="run-001",
        session_id="session-001",
        sandbox_id="sandbox-a",
    )
    with pytest.raises(SandboxRecoveryError):
        rebind_after_loss(state, new_sandbox_id="sandbox-b")
