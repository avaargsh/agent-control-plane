from pathlib import Path

from agent_control_plane.compiler import compile_release_plan
from agent_control_plane.loader import load_yaml_documents


documents = load_yaml_documents(
    Path(__file__).with_name("golden-slice.yaml"),
    validate=False,
)

# golden-slice.yaml is intentionally a human-friendly aggregate document.
root = documents.documents[0]
release = root["release"]
bundles = [root["bundle"]]
bindings = root["bindings"]

plan = compile_release_plan(
    release=release,
    bundles=bundles,
    bindings=bindings,
)

print(plan)
