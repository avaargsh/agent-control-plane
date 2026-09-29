from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class ProviderDescriptor:
    provider_type: str
    provider_name: str
    features: frozenset[str] = frozenset()
    contract_version: str = "v1alpha1"


@dataclass(frozen=True)
class ConformanceResult:
    compatible: bool
    required: tuple[str, ...]
    supported: tuple[str, ...]
    missing: tuple[str, ...]
    contract_version: str


def check_conformance(
    *,
    required: Iterable[str],
    provider: ProviderDescriptor,
) -> ConformanceResult:
    required_set = frozenset(required)
    missing = required_set - provider.features
    return ConformanceResult(
        compatible=not missing,
        required=tuple(sorted(required_set)),
        supported=tuple(sorted(provider.features)),
        missing=tuple(sorted(missing)),
        contract_version=provider.contract_version,
    )
