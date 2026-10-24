from __future__ import annotations

import io
import json

import pytest

pytest.importorskip("psycopg")
pytest.importorskip("cloud_agent_runtime.canonical_runtime_identity")

from cloud_agent_runtime.canonical_runtime_identity import HttpsRuntimeStoreRunAuthorityReader

from agent_control_plane.workload_credential_http import WorkloadCredentialWSGI
from agent_control_plane.workload_credential_service import (
    WorkloadCredentialServiceConfig,
    create_workload_credential_wsgi,
)


def _environment():
    values = {
        "ACP_WORKLOAD_CREDENTIAL_DATABASE_URL": "postgresql://postgres:postgres@localhost:5432/postgres",
        "ACP_WORKLOAD_ASSERTION_ISSUER": "https://runtime.example.test",
        "ACP_WORKLOAD_ASSERTION_AUDIENCE": "urn:acp:workload-credential-authority",
        "ACP_WORKLOAD_ASSERTION_PUBLIC_KEYS_JSON": json.dumps({
            "runtime-key-v1": "11" * 32,
            "runtime-key-v2": "22" * 32,
        }),
        "ACP_RUNTIME_IDENTITY_BASE_URL": "https://runtime.example.test",
        "ACP_RUNTIME_IDENTITY_SERVICE_TOKEN": "runtime-identity-reader-token-0123456789",
        "ACP_CREDENTIAL_ISSUER_TOKEN": "credential-issuer-role-token-0123456789",
        "ACP_CREDENTIAL_INTROSPECTOR_TOKEN": "credential-introspector-role-token-0123456789",
        "ACP_CREDENTIAL_REVOKER_TOKEN": "credential-revoker-role-token-0123456789",
        "ACP_APPROVAL_READER_TOKEN": "approval-reader-role-token-0123456789",
        "ACP_APPROVAL_REVOKER_TOKEN": "approval-revoker-role-token-0123456789",
        "ACP_RUNTIME_IDENTITY_TIMEOUT_SECONDS": "2.5",
    }
    return values


def _call_wsgi(app, *, path, token, body):
    encoded = json.dumps(body).encode("utf-8")
    environ = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": path,
        "QUERY_STRING": "",
        "CONTENT_TYPE": "application/json",
        "CONTENT_LENGTH": str(len(encoded)),
        "wsgi.input": io.BytesIO(encoded),
        "HTTP_AUTHORIZATION": f"Bearer {token}",
        # Model the trusted TLS terminator setting the WSGI scheme. A random
        # caller-provided X-Forwarded-Proto is deliberately not used as trust.
        "wsgi.url_scheme": "https",
    }
    statuses = []
    headers = []

    def start_response(status, response_headers):
        statuses.append(status)
        headers.extend(response_headers)

    payload = b"".join(app(environ, start_response))
    return int(statuses[0].split()[0]), dict(headers), json.loads(payload)


def test_production_config_validates_pinned_keys_and_separate_role_tokens():
    env = _environment()
    config = WorkloadCredentialServiceConfig.from_environment(env)
    assert config.assertion_issuer == "https://runtime.example.test"
    assert config.assertion_audience == "urn:acp:workload-credential-authority"
    assert config.assertion_public_keys["runtime-key-v1"] == bytes.fromhex("11" * 32)
    assert config.runtime_identity_timeout_seconds == 2.5
    assert len(set(config.service_role_tokens.values())) == 5

    # Config reprs are routinely logged on startup/debug paths; never disclose
    # the database DSN or any credential material through that representation.
    rendered = repr(config)
    for secret in (
        env["ACP_WORKLOAD_CREDENTIAL_DATABASE_URL"],
        env["ACP_RUNTIME_IDENTITY_SERVICE_TOKEN"],
        env["ACP_CREDENTIAL_ISSUER_TOKEN"],
        env["ACP_CREDENTIAL_INTROSPECTOR_TOKEN"],
        env["ACP_CREDENTIAL_REVOKER_TOKEN"],
        env["ACP_APPROVAL_READER_TOKEN"],
        env["ACP_APPROVAL_REVOKER_TOKEN"],
    ):
        assert secret not in rendered

    env["ACP_APPROVAL_REVOKER_TOKEN"] = env["ACP_RUNTIME_IDENTITY_SERVICE_TOKEN"]
    with pytest.raises(ValueError, match="distinct tokens"):
        WorkloadCredentialServiceConfig.from_environment(env)


def test_production_factory_wires_remote_https_runtime_reader_and_role_gate():
    env = _environment()
    app = create_workload_credential_wsgi(env)
    assert isinstance(app, WorkloadCredentialWSGI)
    assert isinstance(
        app._authority._runs,
        HttpsRuntimeStoreRunAuthorityReader,
    )

    # Role authorization happens before request parsing, DB access or Runtime
    # identity lookup. A token for the read-only role cannot issue credentials.
    status, headers, payload = _call_wsgi(
        app,
        path="/v1/workload-credentials:issue",
        token=env["ACP_CREDENTIAL_INTROSPECTOR_TOKEN"],
        body={},
    )
    assert status == 403
    assert headers["Cache-Control"] == "no-store"
    assert payload["error"]["code"] == "service_role_denied"

    # The correct role reaches the authority validation boundary, but malformed
    # request data still fails before any external credential can be issued.
    status, _, payload = _call_wsgi(
        app,
        path="/v1/workload-credentials:issue",
        token=env["ACP_CREDENTIAL_ISSUER_TOKEN"],
        body={},
    )
    assert status == 400
    assert payload["error"]["code"] == "invalid_request"


@pytest.mark.parametrize(
    "changes",
    [
        {"ACP_RUNTIME_IDENTITY_BASE_URL": "http://runtime.example.test"},
        {"ACP_WORKLOAD_ASSERTION_PUBLIC_KEYS_JSON": '{"runtime-key-v1":"not-hex"}'},
        {"ACP_CREDENTIAL_ISSUER_TOKEN": "short"},
    ],
)
def test_production_factory_configuration_fails_closed(changes):
    env = _environment()
    env.update(changes)
    if "ACP_RUNTIME_IDENTITY_BASE_URL" in changes:
        with pytest.raises(ValueError, match="HTTPS"):
            create_workload_credential_wsgi(env)
    elif "ACP_WORKLOAD_ASSERTION_PUBLIC_KEYS_JSON" in changes:
        with pytest.raises(ValueError, match="public key"):
            create_workload_credential_wsgi(env)
    else:
        with pytest.raises(ValueError, match="32 characters"):
            create_workload_credential_wsgi(env)
