from pathlib import Path

from agent_control_plane.loader import load_documents
from agent_control_plane.validator import validate_document


def test_modern_runtime_bindings_validate() -> None:
    path = Path(__file__).parents[1] / "examples" / "modern-runtime-bindings.yaml"
    documents = load_documents(path)

    assert len(documents) == 6
    for document in documents:
        validate_document(document)


def test_modern_bindings_make_ownership_explicit() -> None:
    path = Path(__file__).parents[1] / "examples" / "modern-runtime-bindings.yaml"
    documents = load_documents(path)

    for document in documents:
        ownership = document["spec"]["ownership"]
        assert ownership["execution"]
        assert ownership["continuation"]
        assert ownership["context"]


def test_mcp_binding_is_stateless_capability_binding() -> None:
    path = Path(__file__).parents[1] / "examples" / "modern-runtime-bindings.yaml"
    documents = load_documents(path)
    tool = next(d for d in documents if d["metadata"]["name"] == "tools-observability")

    assert tool["spec"]["provider"] == "mcp"
    assert tool["spec"]["config"]["mode"] == "stateless"
    assert "metrics.read" in tool["spec"]["capabilities"]
