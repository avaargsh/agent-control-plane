from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

from .validator import validate_manifest


@dataclass(frozen=True)
class LoadedDocuments:
    documents: list[dict[str, Any]]

    def by_kind(self, kind: str) -> list[dict[str, Any]]:
        return [doc for doc in self.documents if doc.get("kind") == kind]


def load_yaml_documents(
    source: str | Path,
    *,
    validate: bool = True,
) -> LoadedDocuments:
    path = Path(source)
    with path.open("r", encoding="utf-8") as handle:
        documents = [
            doc for doc in yaml.safe_load_all(handle)
            if doc is not None
        ]

    if validate:
        for doc in documents:
            validate_manifest(doc)

    return LoadedDocuments(documents=documents)
