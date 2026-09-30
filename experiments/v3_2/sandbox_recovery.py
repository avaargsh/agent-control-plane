from agent_control_plane.sandbox_binding import (
    SandboxBindingState,
    checkpoint_binding,
    rebind_after_loss,
)


before = SandboxBindingState(
    run_id="run-001",
    session_id="session-001",
    sandbox_id="sandbox-a",
)
checkpointed = checkpoint_binding(
    before,
    checkpoint_ref="checkpoint://step-3",
    workspace_state_ref="state://workspace/step-3",
)
after = rebind_after_loss(checkpointed, new_sandbox_id="sandbox-b")

print("run", before.run_id, "=>", after.run_id)
print("session", before.session_id, "=>", after.session_id)
print("sandbox", before.sandbox_id, "=>", after.sandbox_id)
print("generation", before.generation, "=>", after.generation)
