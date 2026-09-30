from pathlib import Path

import yaml

from agent_control_plane.validator import validate_manifest


def test_v32_contract_examples_validate() -> None:
    path = Path(__file__).parents[1] / "examples" / "v3.2-contracts.yaml"
    documents = [doc for doc in yaml.safe_load_all(path.read_text()) if doc]
    assert {doc["kind"] for doc in documents} == {
        "CapabilityIntent",
        "SandboxBinding",
        "ToolContract",
        "EvidenceEvent",
    }
    for document in documents:
        validate_manifest(document)
