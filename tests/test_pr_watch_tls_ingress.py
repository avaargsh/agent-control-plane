"""Real Nginx TLS -> loopback Webhook HMAC contract, without live GitHub.

This CI test launches actual Nginx and a short-lived self-signed TLS endpoint
bound only to 127.0.0.1, using test-only Goal and secret.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import shutil
import socket
import ssl
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))

from test_pr_watch_webhook import (  # noqa: E402
    NUMBER, REPO, SECRET, journal, serve, setup,
)

TEMPLATE = (
    Path(__file__).resolve().parents[1] /
    "examples/pr_watch_nginx_staging.conf.template"
)


def unused_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def tls_ingress(tmp_path, backend_port):
    if not shutil.which("nginx") or not shutil.which("openssl"):
        pytest.skip("Nginx/OpenSSL required; CI explicitly installs them")

    cert = tmp_path / "localhost.crt"
    key = tmp_path / "localhost.key"
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048",
        "-nodes", "-days", "1", "-keyout", str(key), "-out", str(cert),
        "-subj", "/CN=localhost",
        "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
    ], check=True, capture_output=True, timeout=15)
    key.chmod(0o600)
    tls_port = unused_port()
    conf = TEMPLATE.read_text()
    values = {
        "@@ERROR_LOG@@": str(tmp_path / "nginx.error.log"),
        "@@PID_FILE@@": str(tmp_path / "nginx.pid"),
        "@@CERT_FILE@@": str(cert),
        "@@KEY_FILE@@": str(key),
        "@@PUBLIC_HOST@@": "localhost",
        "@@TLS_PORT@@": str(tls_port),
        "@@BACKEND_PORT@@": str(backend_port),
    }
    for token, value in values.items():
        assert conf.count(token) >= 1, token
        conf = conf.replace(token, value)
    assert re.search(r"@@[A-Z_]+@@", conf) is None
    generated = tmp_path / "nginx.conf"
    generated.write_text(conf)
    subprocess.run(
        ["nginx", "-t", "-p", str(tmp_path), "-c", str(generated)],
        check=True, capture_output=True, timeout=5,
    )
    process = subprocess.Popen([
        "nginx", "-p", str(tmp_path), "-c", str(generated),
        "-g", "daemon off;",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            if process.poll() is not None:
                raise AssertionError("Nginx terminated before listening")
            try:
                with socket.create_connection(("127.0.0.1", tls_port), .25):
                    break
            except OSError:
                time.sleep(.05)
        else:
            raise AssertionError("Nginx TLS listener did not start")
        ctx = ssl.create_default_context(cafile=str(cert))
        yield "https://localhost:" + str(tls_port), ctx
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def signed(raw, *, kind="pull_request", delivery_id=None):
    return {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": "sha256=" + hmac.new(
            SECRET, raw, hashlib.sha256,
        ).hexdigest(),
        "X-GitHub-Event": kind,
        "X-GitHub-Delivery": delivery_id or str(uuid4()),
    }


def send(url, ctx, *, raw, headers=None, method="POST", path="/webhooks/github"):
    request = Request(
        url + path,
        data=raw if method == "POST" else None,
        headers=headers or {}, method=method,
    )
    try:
        with urlopen(request, timeout=5, context=ctx) as response:
            return response.status, json.loads(response.read())
    except HTTPError as exc:
        return exc.code, exc.read()


def pr_payload(*, repo=REPO, number=NUMBER, action="synchronize"):
    # Noncanonical whitespace and Unicode are deliberately signed exactly.
    return (
        '{\r\n  "action": "' + action + '", '
        '"repository": {"full_name": "' + repo + '"}, '
        '"number": ' + str(number) + ', '
        '"pull_request": {"number": ' + str(number) +
        ', "title": "caf\\u00e9"}\r\n}'
    ).encode("utf-8")


def test_real_tls_proxy_preserves_raw_signed_bytes_and_replay(tmp_path):
    store, ingress, seen = setup(tmp_path)
    with serve(ingress) as backend:
        with tls_ingress(tmp_path, backend.server_port) as (url, ctx):
            raw = pr_payload()
            event = signed(raw)
            code, first = send(url, ctx, raw=raw, headers=event)
            assert (code, first["signal_state"]) == (202, "ACKED")
            code, duplicate = send(url, ctx, raw=raw, headers=event)
            assert (code, duplicate) == (
                200, {"status": "duplicate", "signal_state": "ACKED"},
            )
            assert len(seen) == 1
            assert len(journal(store)) == 1
            assert journal(store)[0][1] == hashlib.sha256(raw).hexdigest()
            assert b"caf" not in repr(journal(store)).encode()


def test_real_tls_proxy_rejects_signature_scope_and_reused_delivery(tmp_path):
    store, ingress, seen = setup(tmp_path)
    with serve(ingress) as backend:
        with tls_ingress(tmp_path, backend.server_port) as (url, ctx):
            raw = pr_payload()
            headers = signed(raw)
            changed_body = pr_payload(action="closed")
            for altered, h, code in [
                (raw, {**headers, "X-Hub-Signature-256": "sha256=" + "0" * 64}, 403),
                (pr_payload(repo="other/repo"), signed(pr_payload(repo="other/repo")), 202),
                (pr_payload(number=NUMBER+1), signed(pr_payload(number=NUMBER+1)), 202),
            ]:
                status, _ = send(url, ctx, raw=altered, headers=h)
                assert status == code
            assert seen == [] and journal(store) == []
            assert send(url, ctx, raw=raw, headers=headers)[0] == 202
            collision = signed(changed_body, delivery_id=headers["X-GitHub-Delivery"])
            assert send(url, ctx, raw=changed_body, headers=collision)[0] == 409
            assert len(seen) == 1 and len(journal(store)) == 1


def test_real_tls_proxy_deny_by_default_methods_host_origin_and_oversize(tmp_path):
    store, ingress, seen = setup(tmp_path)
    with serve(ingress) as backend:
        with tls_ingress(tmp_path, backend.server_port) as (url, ctx):
            raw = pr_payload()
            event = signed(raw)
            assert send(url, ctx, raw=raw, headers=event, path="/api/goals")[0] == 404
            assert send(url, ctx, raw=None, headers=event, method="GET")[0] == 403
            assert send(url, ctx, raw=raw, headers={
                **event, "Host": "evil.example",
            })[0] == 421
            assert send(url, ctx, raw=raw, headers={
                **event, "Origin": "https://evil.example",
            })[0] == 403
            huge = b"x" * (262_144 + 1)
            assert send(url, ctx, raw=huge, headers=signed(huge))[0] == 413
            assert seen == [] and journal(store) == []


def test_real_tls_proxy_check_run_hint_never_takes_webhook_ci_as_authority(tmp_path):
    store, ingress, seen = setup(tmp_path)
    with serve(ingress) as backend:
        with tls_ingress(tmp_path, backend.server_port) as (url, ctx):
            raw = json.dumps({
                "action": "completed",
                "repository": {"full_name": REPO},
                "check_run": {
                    "pull_requests": [{"number": NUMBER}],
                    "conclusion": "success",
                },
            }, separators=(",", ":")).encode()
            status, payload = send(
                url, ctx, raw=raw, headers=signed(raw, kind="check_run"),
            )
            assert status == 202 and payload["signal_state"] == "ACKED"
            assert len(seen) == 1 and len(journal(store)) == 1
            # The event's conclusion was not persisted as a provider
            # observation or authoritative CI result.
            assert store.get(user_id="alice", goal_id="watch").phase == ""
