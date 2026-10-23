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


def validation_errors(document: dict[str, Any]) -> list[str]:
    return [
        error.message
        for error in sorted(VALIDATOR.iter_errors(document), key=lambda item: list(item.path))
    ]


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
