from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class ProviderCapabilities:
    provider_type: str
    provider_name: str
    capabilities: frozenset[str]
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
    provider: ProviderCapabilities,
) -> ConformanceResult:
    required_set = frozenset(required)
    missing = required_set - provider.capabilities
    return ConformanceResult(
        compatible=not missing,
        required=tuple(sorted(required_set)),
        supported=tuple(sorted(provider.capabilities)),
        missing=tuple(sorted(missing)),
        contract_version=provider.contract_version,
    )
