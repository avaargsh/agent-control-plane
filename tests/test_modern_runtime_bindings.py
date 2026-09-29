from pathlib import Path

from agent_control_plane.loader import load_yaml_documents


def _documents():
    path = Path(__file__).parents[1] / "examples" / "modern-runtime-bindings.yaml"
    return load_yaml_documents(path).documents


def test_modern_runtime_bindings_validate() -> None:
    documents = _documents()
    assert len(documents) == 6


def test_modern_bindings_make_ownership_explicit() -> None:
    for document in _documents():
        ownership = document["spec"]["ownership"]
        assert ownership["execution"]
        assert ownership["continuation"]
        assert ownership["context"]


def test_mcp_binding_is_stateless_capability_binding() -> None:
    tool = next(
        d for d in _documents()
        if d["metadata"]["name"] == "tools-observability"
    )

    assert tool["spec"]["provider"] == "mcp"
    assert tool["spec"]["config"]["mode"] == "stateless"
    assert "metrics.read" in tool["spec"]["capabilities"]
