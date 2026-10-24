import pytest

from agent_control_plane.workload_credentials import _scope_needs_approval


@pytest.mark.parametrize(
    "scope",
    [
        "github:repository:read",
        "github:pull_request:list",
        "kubernetes:deployment:get",
        "kubernetes:pod:describe",
        "metrics:inspect",
        "cloud:resource:READ",
    ],
)
def test_explicit_read_only_scope_verbs_do_not_require_approval(scope):
    assert _scope_needs_approval(scope) is False


@pytest.mark.parametrize(
    "scope",
    [
        "bank:transfer",
        "orders:create",
        "orders:update",
        "runtime:execute",
        "repository:publish",
        "workflow:approve",
        "kubernetes:deployment:write",
        "kubernetes:deployment:delete",
        "cloud:admin",
        "resource:mutate",
        "custom:unknown",
        "legacy-scope-with-no-action-verb",
    ],
)
def test_mutating_or_unknown_scope_verbs_require_approval(scope):
    assert _scope_needs_approval(scope) is True


def test_approval_exemption_depends_only_on_explicit_terminal_action_verb():
    # The scope name's final colon-delimited component is its action verb.
    # An unknown verb is never treated as read-only merely because it is not
    # present in a sensitive-verb denylist.
    assert _scope_needs_approval("payments:transfer") is True
    assert _scope_needs_approval("payments:read") is False
