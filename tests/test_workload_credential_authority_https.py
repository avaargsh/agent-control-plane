"""Real loopback HTTPS/mTLS acceptance for the remote Runtime identity reader.

These tests run in the PostgreSQL authority job, which installs the optional
cryptography dependency and the sibling cloud-agent-runtime checkout. Local
certificates are generated per test; no production keys or network services
are used.
"""
from __future__ import annotations

import ipaddress
import ssl
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

import pytest

pytest.importorskip("cloud_agent_runtime.canonical_runtime_identity")
pytest.importorskip("cryptography")

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from cloud_agent_runtime.authoritative_identity import HttpsJsonAuthorityTransport
from cloud_agent_runtime.canonical_runtime_identity import (
    CANONICAL_RUN_ROUTE_PREFIX,
    HttpsRuntimeStoreRunAuthorityReader,
    RuntimeCanonicalIdentityWSGI,
    RuntimeIdentityUnavailableError,
    RuntimeStoreRunAuthorityReader,
)
from cloud_agent_runtime.models import (
    Run,
    RunStatus,
    SandboxBinding,
    Session,
    SessionStatus,
)
from cloud_agent_runtime.store import InMemoryStore


SERVICE_TOKEN = "test-only-runtime-reader-token"
RUN_ID = "tls-run-1"


def _key():
    return ec.generate_private_key(ec.SECP256R1())


def _certificate(
    *,
    common_name: str,
    public_key,
    issuer_certificate,
    issuer_key,
    is_ca: bool,
    extended_key_usage=(),
):
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    issuer = issuer_certificate.subject if issuer_certificate is not None else subject
    now = datetime.now(timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.BasicConstraints(ca=is_ca, path_length=1 if is_ca else None),
            critical=True,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=is_ca,
                crl_sign=is_ca,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
    )
    if not is_ca:
        builder = builder.add_extension(
            x509.ExtendedKeyUsage(list(extended_key_usage)),
            critical=False,
        )
    if common_name == "127.0.0.1":
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(common_name))]),
            critical=False,
        )
    return builder.sign(private_key=issuer_key, algorithm=hashes.SHA256())


def _write_key(path: Path, key) -> None:
    path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )


def _write_cert(path: Path, cert) -> None:
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


class _HttpsRequestHandler(WSGIRequestHandler):
    def get_environ(self):
        environ = super().get_environ()
        # This handler is only installed on the socket wrapped by the test TLS
        # server below, so the scheme reflects the actual HTTPS connection.
        environ["wsgi.url_scheme"] = "https"
        return environ

    def log_message(self, _format, *_args):
        return


class _RoleAuthenticator:
    def __init__(self, accepted_token: str):
        self.accepted_token = accepted_token
        self.roles: list[str] = []

    def authorize(self, environ, *, required_role: str) -> bool:
        self.roles.append(required_role)
        return (
            required_role == "runtime.identity.reader"
            and environ.get("HTTP_AUTHORIZATION") == f"Bearer {self.accepted_token}"
        )


class _RecordingApp:
    def __init__(self, app):
        self.app = app
        self.calls: list[tuple[str, str | None, str]] = []
        self.statuses: list[str] = []

    def __call__(self, environ, start_response):
        self.calls.append((
            environ.get("REQUEST_METHOD", ""),
            environ.get("HTTP_AUTHORIZATION"),
            environ.get("PATH_INFO", ""),
        ))

        def record(status, headers, exc_info=None):
            self.statuses.append(status)
            return start_response(status, headers, exc_info)

        return self.app(environ, record)


def _make_store() -> InMemoryStore:
    store = InMemoryStore()
    store.save_session(Session(
        session_id="tls-session-1",
        tenant_id="tenant-tls",
        agent_id="agent-tls",
        release_id="release-tls",
        status=SessionStatus.ACTIVE,
    ))
    workspace_ref = "sandbox://kubernetes/tls-sandbox-1"
    store.save_run(Run(
        run_id=RUN_ID,
        session_id="tls-session-1",
        status=RunStatus.RUNNING,
        sandbox_ref=workspace_ref,
        sandbox_binding=SandboxBinding(
            provider="kubernetes",
            sandbox_id="tls-sandbox-1",
            sandbox_ref=workspace_ref,
            revision=7,
        ),
    ))
    return store


def test_remote_reader_uses_verified_https_mutual_tls_and_service_role(tmp_path):
    root_key = _key()
    root_cert = _certificate(
        common_name="canonical-runtime-test-ca",
        public_key=root_key.public_key(),
        issuer_certificate=None,
        issuer_key=root_key,
        is_ca=True,
    )
    server_key = _key()
    server_cert = _certificate(
        common_name="127.0.0.1",
        public_key=server_key.public_key(),
        issuer_certificate=root_cert,
        issuer_key=root_key,
        is_ca=False,
        extended_key_usage=(ExtendedKeyUsageOID.SERVER_AUTH,),
    )
    client_key = _key()
    client_cert = _certificate(
        common_name="runtime-authority-reader-test",
        public_key=client_key.public_key(),
        issuer_certificate=root_cert,
        issuer_key=root_key,
        is_ca=False,
        extended_key_usage=(ExtendedKeyUsageOID.CLIENT_AUTH,),
    )

    ca_path = tmp_path / "test-ca.pem"
    server_cert_path = tmp_path / "server-cert.pem"
    server_key_path = tmp_path / "server-key.pem"
    client_cert_path = tmp_path / "client-cert.pem"
    client_key_path = tmp_path / "client-key.pem"
    _write_cert(ca_path, root_cert)
    _write_cert(server_cert_path, server_cert)
    _write_key(server_key_path, server_key)
    _write_cert(client_cert_path, client_cert)
    _write_key(client_key_path, client_key)

    store = _make_store()
    authenticator = _RoleAuthenticator(SERVICE_TOKEN)
    wsgi_app = _RecordingApp(RuntimeCanonicalIdentityWSGI(
        reader=RuntimeStoreRunAuthorityReader(store),
        authenticator=authenticator,
    ))

    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.minimum_version = ssl.TLSVersion.TLSv1_2
    server_context.load_cert_chain(str(server_cert_path), str(server_key_path))
    server_context.load_verify_locations(cafile=str(ca_path))
    server_context.verify_mode = ssl.CERT_REQUIRED

    server = make_server(
        "127.0.0.1",
        0,
        wsgi_app,
        server_class=WSGIServer,
        handler_class=_HttpsRequestHandler,
    )
    server.socket = server_context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    base_url = f"https://127.0.0.1:{server.server_port}"
    try:
        client_context = ssl.create_default_context(cafile=str(ca_path))
        client_context.minimum_version = ssl.TLSVersion.TLSv1_2
        client_context.load_cert_chain(str(client_cert_path), str(client_key_path))
        transport = HttpsJsonAuthorityTransport(
            base_url=base_url,
            service_token_provider=lambda: SERVICE_TOKEN,
            timeout_seconds=2,
            ssl_context=client_context,
        )
        reader = HttpsRuntimeStoreRunAuthorityReader(transport)

        binding = reader.get_run_binding(RUN_ID)
        assert binding.tenant_id == "tenant-tls"
        assert binding.agent_id == "agent-tls"
        assert binding.run_id == RUN_ID
        assert binding.sandbox_id == "tls-sandbox-1"
        assert binding.sandbox_revision == 7
        assert binding.status == "running"
        assert wsgi_app.calls == [(
            "GET", f"Bearer {SERVICE_TOKEN}", CANONICAL_RUN_ROUTE_PREFIX + RUN_ID
        )]
        assert wsgi_app.statuses == ["200 OK"]
        assert authenticator.roles == ["runtime.identity.reader"]

        # The TLS server requires a client certificate: a trusted server CA
        # alone is insufficient to read the canonical identity.
        no_client_cert_context = ssl.create_default_context(cafile=str(ca_path))
        no_client_cert_transport = HttpsJsonAuthorityTransport(
            base_url=base_url,
            service_token_provider=lambda: SERVICE_TOKEN,
            timeout_seconds=2,
            ssl_context=no_client_cert_context,
        )
        with pytest.raises(RuntimeIdentityUnavailableError):
            HttpsRuntimeStoreRunAuthorityReader(no_client_cert_transport).get_run_binding(RUN_ID)
        assert len(wsgi_app.calls) == 1

        # A valid client certificate cannot substitute for the narrow service
        # role/token check performed by the WSGI application.
        denied_transport = HttpsJsonAuthorityTransport(
            base_url=base_url,
            service_token_provider=lambda: "wrong-service-token",
            timeout_seconds=2,
            ssl_context=client_context,
        )
        with pytest.raises(RuntimeIdentityUnavailableError):
            HttpsRuntimeStoreRunAuthorityReader(denied_transport).get_run_binding(RUN_ID)
        assert wsgi_app.calls[-1] == (
            "GET", "Bearer wrong-service-token", CANONICAL_RUN_ROUTE_PREFIX + RUN_ID
        )
        assert wsgi_app.statuses[-1] == "403 Forbidden"

        # A peer certificate not rooted in the configured trust store is also
        # rejected even when the client presents its own valid test certificate.
        untrusted_server_context = ssl.create_default_context()
        untrusted_server_context.minimum_version = ssl.TLSVersion.TLSv1_2
        untrusted_server_context.load_cert_chain(str(client_cert_path), str(client_key_path))
        untrusted_transport = HttpsJsonAuthorityTransport(
            base_url=base_url,
            service_token_provider=lambda: SERVICE_TOKEN,
            timeout_seconds=2,
            ssl_context=untrusted_server_context,
        )
        with pytest.raises(RuntimeIdentityUnavailableError):
            HttpsRuntimeStoreRunAuthorityReader(untrusted_transport).get_run_binding(RUN_ID)
        assert len(wsgi_app.calls) == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
