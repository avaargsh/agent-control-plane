from agent_control_plane import validate_manifest
from agent_control_plane.authority import (
    admit_authority_change,
    authority_digest,
    build_authority_inventory,
)
from agent_control_plane.apply_reconciler import ApplyReconciler
from agent_control_plane.compiler import compile_release_plan
from test_apply_reconciler import (
    build_plan,
    executor_registry,
    provider_registry,
)


def envelope(
    *,
    name="checkout-prod",
    agent_ref="checkout-remediator",
    release_ref="checkout-remediator-v1",
    grants=None,
    runtimes=None,
    approval="human-exact",
    evidence=True,
    max_ops=1,
):
    if grants is None:
        grants = [
            {
                "effect": "write",
                "capability": "kubernetes.scale",
                "resource": (
                    "kubernetes://prod/checkout/"
                    "deployment/checkout-api"
                ),
                "verbs": ["scale"],
            }
        ]
    return {
        "apiVersion": "agentplane.io/v1alpha1",
        "kind": "AgentAuthorityEnvelope",
        "metadata": {"name": name},
        "spec": {
            "fleetRef": "prod-sre",
            "teamRef": "team://sre-platform",
            "agentRef": agent_ref,
            "releaseRef": release_ref,
            "runtimeRefs": runtimes or [
                "temporal://prod",
                "sandbox://prod",
            ],
            "grants": grants,
            "constraints": {
                "approvalMode": approval,
                "evidenceRequired": evidence,
                "maxOperationsPerRun": max_ops,
            },
        },
    }


def test_authority_envelope_validates() -> None:
    validate_manifest(envelope())


def test_digest_is_release_independent() -> None:
    first = envelope()
    second = envelope(
        name="checkout-prod-v2",
        release_ref="checkout-remediator-v2",
    )
    assert authority_digest(first) == authority_digest(second)


def test_added_grant_is_denied() -> None:
    before = envelope()
    after = envelope(
        grants=[
            *before["spec"]["grants"],
            {
                "effect": "write",
                "capability": "kubernetes.delete",
                "resource": "kubernetes://prod/checkout/pod/*",
                "verbs": ["delete"],
            },
        ]
    )

    result = admit_authority_change(before, after)

    assert result.decision == "DENY"
    assert "AUTHORITY_EXPANSION" in result.reasons


def test_tightening_is_admitted() -> None:
    before = envelope(
        grants=[
            {
                "effect": "read",
                "capability": "kubernetes.read",
                "resource": "kubernetes://prod/checkout/*",
                "verbs": ["get", "list"],
            },
            {
                "effect": "write",
                "capability": "kubernetes.scale",
                "resource": (
                    "kubernetes://prod/checkout/"
                    "deployment/checkout-api"
                ),
                "verbs": ["scale"],
            },
        ],
        max_ops=3,
    )
    after = envelope(
        grants=[before["spec"]["grants"][1]],
        max_ops=1,
    )

    result = admit_authority_change(before, after)

    assert result.decision == "ADMIT"
    assert result.diff.removed_grants
    assert result.reasons == ("NO_AUTHORITY_EXPANSION",)


def test_team_ownership_change_is_denied() -> None:
    before = envelope()
    after = envelope()
    after["spec"]["teamRef"] = "team://application"

    result = admit_authority_change(before, after)

    assert "TEAM_OWNERSHIP_CHANGED" in result.reasons


def test_runtime_boundary_change_is_denied() -> None:
    result = admit_authority_change(
        envelope(),
        envelope(
            runtimes=[
                "temporal://prod",
                "sandbox://other",
            ]
        ),
    )

    assert "RUNTIME_BOUNDARY_CHANGED" in result.reasons


def test_approval_weakening_is_denied() -> None:
    result = admit_authority_change(
        envelope(approval="human-exact"),
        envelope(approval="policy"),
    )
    assert "APPROVAL_WEAKENED" in result.reasons


def test_evidence_weakening_is_denied() -> None:
    result = admit_authority_change(
        envelope(evidence=True),
        envelope(evidence=False),
    )
    assert "EVIDENCE_REQUIREMENT_WEAKENED" in result.reasons


def test_write_budget_increase_is_denied() -> None:
    result = admit_authority_change(
        envelope(max_ops=1),
        envelope(max_ops=2),
    )
    assert "WRITE_BUDGET_EXPANDED" in result.reasons


def test_no_drift_is_admitted() -> None:
    result = admit_authority_change(
        envelope(),
        envelope(),
    )

    assert result.decision == "ADMIT"
    assert result.reasons == ("NO_DRIFT",)


def test_inventory_groups_fleet_agent_deployments() -> None:
    checkout = envelope()
    search = envelope(
        name="search-prod",
        agent_ref="search-remediator",
        release_ref="search-remediator-v2",
    )

    inventory = build_authority_inventory(
        [search, checkout]
    )

    assert inventory["fleetCount"] == 1
    assert inventory["agentCount"] == 2
    assert inventory["deploymentCount"] == 2
    assert [
        item["agentRef"]
        for item in inventory["fleets"][0]["agents"]
    ] == [
        "checkout-remediator",
        "search-remediator",
    ]


def test_release_plan_freezes_authority_reference_and_digest() -> None:
    authority = envelope()
    digest = authority_digest(authority)
    release = {
        "metadata": {"name": "checkout-remediator-v2"},
        "spec": {
            "bundleRef": "checkout-remediator",
            "bindings": [],
            "authorityRef": "checkout-prod",
            "authorityDigest": digest,
        },
    }

    plan = compile_release_plan(
        release=release,
        bundles=[{"metadata": {"name": "checkout-remediator"}}],
        bindings=[],
    )

    assert plan.authority_ref == "checkout-prod"
    assert plan.authority_digest == digest


def plan_with_authority(authority):
    plan = build_plan()
    return plan.__class__(
        **{
            **plan.__dict__,
            "release_name": authority["spec"]["releaseRef"],
            "authority_ref": authority["metadata"]["name"],
            "authority_digest": authority_digest(authority),
        }
    )


def test_reconciler_blocks_authority_expansion_before_mutation() -> None:
    deployed = envelope(
        release_ref="checkout-remediator-v1",
    )
    proposed = envelope(
        release_ref="checkout-remediator-v2",
        grants=[
            *deployed["spec"]["grants"],
            {
                "effect": "write",
                "capability": "kubernetes.delete",
                "resource": "kubernetes://prod/checkout/pod/*",
                "verbs": ["delete"],
            },
        ],
    )

    result = ApplyReconciler(
        providers=provider_registry(),
        executors=executor_registry(),
    ).reconcile(
        plan_with_authority(proposed),
        deployed_authority=deployed,
        proposed_authority=proposed,
    )

    assert result.phase == "blocked"
    assert result.receipts == ()
    assert result.authority_decision is not None
    assert "AUTHORITY_EXPANSION" in result.authority_decision.reasons
    assert result.error.startswith("AUTHORITY_ADMISSION_DENIED")


def test_reconciler_admits_tightening_and_records_authority() -> None:
    deployed = envelope(
        release_ref="checkout-remediator-v1",
        max_ops=3,
    )
    proposed = envelope(
        release_ref="checkout-remediator-v2",
        max_ops=1,
    )

    result = ApplyReconciler(
        providers=provider_registry(),
        executors=executor_registry(),
    ).reconcile(
        plan_with_authority(proposed),
        deployed_authority=deployed,
        proposed_authority=proposed,
    )

    assert result.phase == "promoted"
    assert result.authority_decision is not None
    assert result.authority_decision.admitted is True
    assert result.evidence["authority"]["decision"] == "ADMIT"


def test_reconciler_blocks_authority_digest_mismatch() -> None:
    proposed = envelope(
        release_ref="checkout-remediator-v2",
    )
    plan = plan_with_authority(proposed)
    plan = plan.__class__(
        **{
            **plan.__dict__,
            "authority_digest": "sha256:" + "0" * 64,
        }
    )

    result = ApplyReconciler(
        providers=provider_registry(),
        executors=executor_registry(),
    ).reconcile(
        plan,
        proposed_authority=proposed,
    )

    assert result.phase == "blocked"
    assert result.receipts == ()
    assert result.error == "AUTHORITY_DIGEST_MISMATCH"
