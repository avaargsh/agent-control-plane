from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "contracts" / "compute-execution" / "v0.1.schema.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())
PROFILE_VALIDATOR = Draft202012Validator(
    {
        "$schema": SCHEMA["$schema"],
        "$defs": SCHEMA["$defs"],
        "$ref": "#/$defs/ExecutionProfile",
    },
    format_checker=FormatChecker(),
)
REQUEST_VALIDATOR = Draft202012Validator(
    {
        "$schema": SCHEMA["$schema"],
        "$defs": SCHEMA["$defs"],
        "$ref": "#/$defs/ComputeRequest",
    },
    format_checker=FormatChecker(),
)


def allocation() -> dict[str, Any]:
    return {
        "apiVersion": "compute.allocation/v0.1",
        "requestId": "0123456789abcdef0123456789abcdef",
        "projectId": "project-a",
        "computePoolId": "pool-a",
        "workloadId": "workload-a",
        "generation": 3,
        "clusterId": "cluster-a",
        "provider": "kueue",
        "providerBindingGeneration": 2,
        "state": "ALLOCATED",
        "observedGeneration": 3,
        "providerRefs": [
            {
                "kind": "Job",
                "namespace": "agent-workloads",
                "name": "job-workload-a",
                "uid": "api-server-issued-uid",
                "resourceVersion": "42",
                "generation": 1,
                "imageDigest": "sha256:" + ("a" * 64),
                "ownershipProofRef": "evidence://ownership/workload-a/g3",
            }
        ],
        "conditions": [
            {
                "type": "OwnershipProven",
                "status": "TRUE",
                "reason": "CurrentGenerationBound",
                "observedAt": "2026-10-23T12:00:00Z",
            }
        ],
        "evidenceRefs": ["evidence://allocation/workload-a/g3"],
    }


def validation_errors(
    document: dict[str, Any],
    validator: Draft202012Validator = VALIDATOR,
) -> list[str]:
    return [
        error.message
        for error in sorted(
            validator.iter_errors(document),
            key=lambda item: tuple(str(part) for part in item.path),
        )
    ]


def profile(environment: str, accelerator: dict[str, Any]) -> dict[str, Any]:
    return {
        "apiVersion": "agent.execution-profile/v0.1",
        "profileId": "profile-a",
        "revision": 1,
        "projectId": "project-a",
        "environment": environment,
        "resources": {
            "cpuMilli": 500,
            "memoryMiB": 512,
            "ephemeralStorageMiB": 1024,
            "timeoutSeconds": 300,
            "maxConcurrency": 2,
        },
        "accelerator": accelerator,
        "persistence": "ephemeral",
        "networkPolicyRef": "default-deny",
        "credentialBindingRefs": [],
    }


def compute_request(image: str) -> dict[str, Any]:
    return {
        "apiVersion": "compute.request/v0.1",
        "requestId": "0123456789abcdef0123456789abcdef",
        "intentDigest": "sha256:" + ("b" * 64),
        "projectId": "project-a",
        "computePoolId": "pool-a",
        "profileRef": {"profileId": "gpu-profile", "revision": 2},
        "correlation": {
            "agentId": "agent-a",
            "sessionId": "session-a",
            "runId": "run-a",
        },
        "workload": {
            "id": "workload-a",
            "expectedGeneration": 0,
            "image": image,
            "command": ["/bin/sh", "-c", "true"],
            "accelerator": {"class": "accelerator-a", "quota": 1},
        },
    }


def test_execution_resource_schema_is_valid_draft_2020_12() -> None:
    Draft202012Validator.check_schema(SCHEMA)


def test_allocated_requires_provider_ownership_and_evidence() -> None:
    valid = allocation()
    assert validation_errors(valid) == []

    no_provider = copy.deepcopy(valid)
    no_provider["providerRefs"] = []
    assert validation_errors(no_provider)

    no_ownership_condition = copy.deepcopy(valid)
    no_ownership_condition["conditions"] = []
    assert validation_errors(no_ownership_condition)

    no_evidence = copy.deepcopy(valid)
    no_evidence["evidenceRefs"] = []
    assert validation_errors(no_evidence)


def test_job_and_pod_refs_require_digest_and_ownership_proof() -> None:
    valid = allocation()
    del valid["providerRefs"][0]["imageDigest"]
    assert validation_errors(valid)

    valid = allocation()
    del valid["providerRefs"][0]["ownershipProofRef"]
    assert validation_errors(valid)


def test_released_requires_cleanup_complete_condition_and_evidence() -> None:
    released = allocation()
    released["state"] = "RELEASED"
    released["providerRefs"] = []
    released["conditions"] = [
        {
            "type": "CleanupComplete",
            "status": "TRUE",
            "reason": "ProviderAbsentAndFinalized",
            "observedAt": "2026-10-23T12:05:00Z",
        }
    ]
    assert validation_errors(released) == []

    no_cleanup_proof = copy.deepcopy(released)
    no_cleanup_proof["conditions"][0]["status"] = "UNKNOWN"
    assert validation_errors(no_cleanup_proof)

    no_release_evidence = copy.deepcopy(released)
    no_release_evidence["evidenceRefs"] = []
    assert validation_errors(no_release_evidence)


def test_unknown_can_represent_missing_provider_observation() -> None:
    unknown = allocation()
    unknown["state"] = "UNKNOWN"
    unknown["observedGeneration"] = 0
    unknown["providerRefs"] = []
    unknown["conditions"] = []
    unknown["evidenceRefs"] = []
    assert validation_errors(unknown) == []



def test_execution_profile_never_treats_non_gpu_as_gpu_workload() -> None:
    # Ordinary workers, sandboxes and workspaces explicitly declare no
    # accelerator and therefore do not request a GPU allocation.
    for environment in ("worker", "sandbox", "workspace"):
        assert validation_errors(
            profile(environment, {"mode": "none"}), PROFILE_VALIDATOR
        ) == []
        assert validation_errors(
            profile(environment, {"mode": "required", "class": "gpu", "quota": 1}),
            PROFILE_VALIDATOR,
        )

    assert validation_errors(
        profile("gpu-workload", {"mode": "required", "class": "gpu", "quota": 1}),
        PROFILE_VALIDATOR,
    ) == []
    assert validation_errors(
        profile("gpu-workload", {"mode": "none"}), PROFILE_VALIDATOR
    )


def test_compute_request_requires_immutable_digest_pinned_image() -> None:
    digest = "a" * 64
    assert validation_errors(
        compute_request(f"registry.example.test/model@sha256:{digest}"),
        REQUEST_VALIDATOR,
    ) == []
    assert validation_errors(
        compute_request("registry.example.test/model:latest"), REQUEST_VALIDATOR
    )



def error(
    code: str,
    outcome: str,
    retryable: bool,
    *,
    retry_after_seconds: int | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "apiVersion": "compute.error/v0.1",
        "code": code,
        "outcome": outcome,
        "retryable": retryable,
        "requestId": "0123456789abcdef0123456789abcdef",
    }
    if retry_after_seconds is not None:
        result["retryAfterSeconds"] = retry_after_seconds
    return result


def test_compute_error_codes_freeze_outcome_and_retry_meaning() -> None:
    valid = [
        error("INVALID_ARGUMENT", "REJECTED", False),
        error("IDEMPOTENCY_CONFLICT", "REJECTED", False),
        error("QUOTA_EXCEEDED", "REJECTED", True, retry_after_seconds=30),
        error("RESOURCE_UNAVAILABLE", "NOT_COMMITTED", True),
        error("RESOURCE_UNAVAILABLE", "UNKNOWN", True),
        error("UNAVAILABLE", "UNKNOWN", True),
        error("OWNERSHIP_UNPROVEN", "UNKNOWN", True),
        error("PROVIDER_STATE_CONFLICT", "UNKNOWN", False),
        error("CLEANUP_PENDING", "UNKNOWN", True, retry_after_seconds=5),
    ]
    for document in valid:
        assert validation_errors(document, Draft202012Validator(
            {
                "$schema": SCHEMA["$schema"],
                "$defs": SCHEMA["$defs"],
                "$ref": "#/$defs/ComputeError",
            },
            format_checker=FormatChecker(),
        )) == []

    invalid = [
        error("INVALID_ARGUMENT", "UNKNOWN", True),
        error("IDEMPOTENCY_CONFLICT", "REJECTED", True),
        error("DEADLINE_EXCEEDED", "NOT_COMMITTED", True),
        error("PROVIDER_STATE_CONFLICT", "UNKNOWN", True),
        error("CLEANUP_PENDING", "COMMITTED", False),
        error("UNAVAILABLE", "UNKNOWN", False, retry_after_seconds=5),
    ]
    validator = Draft202012Validator(
        {
            "$schema": SCHEMA["$schema"],
            "$defs": SCHEMA["$defs"],
            "$ref": "#/$defs/ComputeError",
        },
        format_checker=FormatChecker(),
    )
    for document in invalid:
        assert validation_errors(document, validator)
