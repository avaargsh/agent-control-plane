from agent_control_plane.apply_reconciler import ApplyReconciler
from agent_control_plane.golden_slice_replay import canonical_replay_digest
from test_apply_reconciler import PASS_GATE, build_plan, executor_registry, provider_registry


def reconciler():
    return ApplyReconciler(
        providers=provider_registry(),
        executors=executor_registry(),
    )


def test_golden_slice_provenance_is_detached_from_live_input():
    golden_slice = {
        "runId": "run-001",
        "evidenceId": "e-001",
        "evidenceDigest": "sha256:" + "a" * 64,
        "decisionId": "d-001",
        "releaseId": "r-001",
        "refs": {
            "evidenceBundle": "evidence://frozen/001",
        },
    }

    result = reconciler().reconcile(
        build_plan(),
        eval_gates=[PASS_GATE],
        metrics={"accuracy": 0.95, "ece": 0.03},
        golden_slice=golden_slice,
    )

    golden_slice["refs"]["evidenceBundle"] = "evidence://live/reread"

    assert (
        result.evidence["golden_slice"]["refs"]["evidenceBundle"]
        == "evidence://frozen/001"
    )


def test_replay_digest_is_canonical_across_mapping_order():
    left = {
        "runId": "run-001",
        "evidenceDigest": "sha256:" + "a" * 64,
        "refs": {"b": "2", "a": "1"},
    }
    right = {
        "refs": {"a": "1", "b": "2"},
        "evidenceDigest": "sha256:" + "a" * 64,
        "runId": "run-001",
    }

    assert canonical_replay_digest(left) == canonical_replay_digest(right)


def test_replay_digest_detects_tampered_receipt():
    original = {
        "runId": "run-001",
        "receipts": [{"binding": "sandbox", "status": "applied"}],
    }
    tampered = {
        "runId": "run-001",
        "receipts": [{"binding": "sandbox", "status": "skipped"}],
    }

    assert canonical_replay_digest(original) != canonical_replay_digest(tampered)
