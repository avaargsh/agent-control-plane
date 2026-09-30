"""OPA policy provider boundary for AgentOS v3.2.

This package translates portable CapabilityIntent into provider-specific
artifacts. The control plane owns intent; OPA owns runtime evaluation.
"""

from .compiler import compile_opa_bundle

__all__ = ["compile_opa_bundle"]
