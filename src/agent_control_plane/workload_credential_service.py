from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Mapping

from .workload_credential_http import ServiceRoleAuthenticator, WorkloadCredentialWSGI
from .workload_credentials import (
    CredentialAuthorityUnavailable,
    PinnedEd25519AssertionVerifier,
    PostgresCredentialAuthorityStore,
    WorkloadCredentialAuthority,
)


_ROLE_ENV = {
    "credential.issuer": "ACP_CREDENTIAL_ISSUER_TOKEN",
    "credential.introspector": "ACP_CREDENTIAL_INTROSPECTOR_TOKEN",
    "credential.revoker": "ACP_CREDENTIAL_REVOKER_TOKEN",
    "approval.reader": "ACP_APPROVAL_READER_TOKEN",
    "approval.revoker": "ACP_APPROVAL_REVOKER_TOKEN",
}
_DEFAULT_ASSERTION_AUDIENCE = "urn:acp:workload-credential-authority"



def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON configuration object contains a duplicate key")
        result[key] = value
    return result


@dataclass(frozen=True)
class WorkloadCredentialServiceConfig:
    """Validated process configuration; secret-bearing fields are hidden from repr."""

    database_url: str = field(repr=False)
    assertion_issuer: str
    assertion_audience: str
    assertion_public_keys: Mapping[str, bytes]
    runtime_identity_base_url: str
    runtime_identity_service_token: str = field(repr=False)
    service_role_tokens: Mapping[str, str] = field(repr=False)
    runtime_identity_timeout_seconds: float = 3.0

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "WorkloadCredentialServiceConfig":
        env = os.environ if environ is None else environ

        def required(name: str) -> str:
            value = env.get(name, "")
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"required environment setting is missing: {name}")
            return value

        public_keys_raw = required("ACP_WORKLOAD_ASSERTION_PUBLIC_KEYS_JSON")
        try:
            parsed_keys = json.loads(
                public_keys_raw,
                object_pairs_hook=_unique_json_object,
            )
        except json.JSONDecodeError:
            raise ValueError("ACP_WORKLOAD_ASSERTION_PUBLIC_KEYS_JSON must be a JSON object") from None
        if not isinstance(parsed_keys, dict) or not parsed_keys:
            raise ValueError("ACP_WORKLOAD_ASSERTION_PUBLIC_KEYS_JSON must be a non-empty JSON object")

        public_keys: dict[str, bytes] = {}
        for kid, encoded in parsed_keys.items():
            if (
                not isinstance(kid, str)
                or not kid.strip()
                or kid != kid.strip()
            ):
                raise ValueError("assertion public key IDs must be non-empty and trimmed")
            if not isinstance(encoded, str) or len(encoded) != 64:
                raise ValueError("assertion public key entries must map key IDs to 32-byte hex keys")
            try:
                raw_key = bytes.fromhex(encoded)
            except ValueError:
                raise ValueError("assertion public key entries must contain valid hex") from None
            if len(raw_key) != 32:
                raise ValueError("assertion public key entries must contain 32-byte Ed25519 keys")
            public_keys[kid] = raw_key
        if len(set(public_keys.values())) != len(public_keys):
            raise ValueError("assertion public key values must be unique across key IDs")

        assertion_issuer = required("ACP_WORKLOAD_ASSERTION_ISSUER")
        if assertion_issuer != assertion_issuer.strip():
            raise ValueError("ACP_WORKLOAD_ASSERTION_ISSUER cannot contain surrounding whitespace")

        audience_raw = env.get(
            "ACP_WORKLOAD_ASSERTION_AUDIENCE",
            _DEFAULT_ASSERTION_AUDIENCE,
        )
        if not isinstance(audience_raw, str) or not audience_raw.strip():
            raise ValueError("ACP_WORKLOAD_ASSERTION_AUDIENCE must be non-empty")
        if audience_raw != audience_raw.strip():
            raise ValueError("ACP_WORKLOAD_ASSERTION_AUDIENCE cannot contain surrounding whitespace")
        assertion_audience = audience_raw

        runtime_identity_base_url = required("ACP_RUNTIME_IDENTITY_BASE_URL")
        if runtime_identity_base_url != runtime_identity_base_url.strip():
            raise ValueError("ACP_RUNTIME_IDENTITY_BASE_URL cannot contain surrounding whitespace")
        runtime_identity_service_token = required("ACP_RUNTIME_IDENTITY_SERVICE_TOKEN")
        role_tokens = {role: required(env_name) for role, env_name in _ROLE_ENV.items()}
        all_service_tokens = [*role_tokens.values(), runtime_identity_service_token]
        if any(len(token) < 32 for token in all_service_tokens):
            raise ValueError("service tokens must contain at least 32 characters")
        if any(any(char.isspace() for char in token) for token in all_service_tokens):
            raise ValueError("service tokens must not contain whitespace")
        if len(set(all_service_tokens)) != len(all_service_tokens):
            raise ValueError("Runtime identity and each authority service role require distinct tokens")

        database_url = required("ACP_WORKLOAD_CREDENTIAL_DATABASE_URL")
        if database_url != database_url.strip():
            raise ValueError("ACP_WORKLOAD_CREDENTIAL_DATABASE_URL cannot contain surrounding whitespace")

        timeout_raw = env.get("ACP_RUNTIME_IDENTITY_TIMEOUT_SECONDS", "3")
        try:
            timeout = float(timeout_raw)
        except (TypeError, ValueError):
            raise ValueError("ACP_RUNTIME_IDENTITY_TIMEOUT_SECONDS must be numeric") from None
        if not 0 < timeout <= 15:
            raise ValueError("ACP_RUNTIME_IDENTITY_TIMEOUT_SECONDS must be in (0, 15]")

        return cls(
            database_url=database_url,
            assertion_issuer=assertion_issuer,
            assertion_audience=assertion_audience,
            assertion_public_keys=public_keys,
            runtime_identity_base_url=runtime_identity_base_url,
            runtime_identity_service_token=runtime_identity_service_token,
            service_role_tokens=role_tokens,
            runtime_identity_timeout_seconds=timeout,
        )


def create_workload_credential_wsgi(
    environ: Mapping[str, str] | None = None,
) -> WorkloadCredentialWSGI:
    """Create the production WSGI application from fail-closed service config.

    The Runtime identity reader is always remote HTTPS. No in-memory or
    request-claim-derived authority is silently substituted if the Runtime
    package, network endpoint, TLS trust or credentials are unavailable.
    This function creates an application only; deployment must mount it behind
    the approved TLS/mTLS ingress and restrict network access to service callers.
    """
    config = WorkloadCredentialServiceConfig.from_environment(environ)
    try:
        from cloud_agent_runtime.authoritative_identity import HttpsJsonAuthorityTransport
        from cloud_agent_runtime.canonical_runtime_identity import HttpsRuntimeStoreRunAuthorityReader
    except ImportError:
        raise CredentialAuthorityUnavailable(
            "install the reviewed cloud-agent-runtime package for canonical Runtime identity"
        ) from None

    runtime_transport = HttpsJsonAuthorityTransport(
        base_url=config.runtime_identity_base_url,
        service_token_provider=lambda: config.runtime_identity_service_token,
        timeout_seconds=config.runtime_identity_timeout_seconds,
    )
    run_authority = HttpsRuntimeStoreRunAuthorityReader(runtime_transport)
    store = PostgresCredentialAuthorityStore.from_dsn(config.database_url)
    assertion_verifier = PinnedEd25519AssertionVerifier(
        issuer=config.assertion_issuer,
        audience=config.assertion_audience,
        public_keys=config.assertion_public_keys,
        max_lifetime_seconds=120,
        clock_skew_seconds=2,
    )
    authority = WorkloadCredentialAuthority(
        store=store,
        assertion_verifier=assertion_verifier,
        run_authority=run_authority,
        clock=time.time,
    )
    authenticator = ServiceRoleAuthenticator({
        role: ServiceRoleAuthenticator.digest_token(token)
        for role, token in config.service_role_tokens.items()
    })
    return WorkloadCredentialWSGI(
        authority,
        authenticator=authenticator,
        clock=time.time,
    )
