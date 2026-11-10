# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The client's certificate-name verification, against a real TLS handshake.

The certificate the EDH installer creates with ``--tls-certificate auto`` carries one name,
``EDH.DEFAULT.CREATE.YOUR.OWN.CERTIFICATE``, in its subject and no subject alternative name, so the host
in ``EDH_API_ENDPOINT`` can never match it. ``EDH_API_TLS_SERVER_NAME`` names what the certificate is
verified against instead; the chain is still verified against the bundle. A loopback HTTPS listener
presents such a self-signed, common-name-only certificate and the client's own pool connects to it,
so these tests hold urllib3 to that contract rather than to the keyword arguments handed to it.
"""

import datetime
import http.server
import ssl
import threading

import pytest
import urllib3

# A transitive dependency of the dev group (moto); the tests are skipped, not failed, without it.
pytest.importorskip("cryptography")
from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402

CERT_NAME = "EDH.DEFAULT.CREATE.YOUR.OWN.CERTIFICATE"


def _self_signed_common_name_only(tmp_path):
    """A self-signed certificate whose only name is its common name, as the EDH installer issues."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, CERT_NAME)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "edh-alb.pem"
    key_path = tmp_path / "edh-alb.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return str(cert_path), str(key_path)


class _EdhLikeHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'{"success": true, "message": []}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def tls_listener(tmp_path):
    """A loopback HTTPS listener presenting the common-name-only certificate; yields its URL and PEM."""
    cert_path, key_path = _self_signed_common_name_only(tmp_path)
    server = http.server.HTTPServer(("127.0.0.1", 0), _EdhLikeHandler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=cert_path, keyfile=key_path)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield {
            "url": f"https://127.0.0.1:{server.server_address[1]}/api/dcv/virtual_desktops/profiles",
            "bundle": cert_path,
        }
    finally:
        server.shutdown()
        server.server_close()


def _client_pool(monkeypatch, bundle, server_name):
    """The client's pool with the bundle already resolved and the server name configured."""
    import backend.backend.handlers.addon.edh.common.edhClient as ec

    ec._reset_client_state_for_tests()
    monkeypatch.setattr(ec, "_ca_bundle_path", bundle)
    monkeypatch.setattr(ec, "_ca_bundle_resolved", True)
    monkeypatch.setattr(ec, "EDH_API_TLS_SERVER_NAME", server_name)
    return ec, ec._get_http_pool()


@pytest.fixture(autouse=True)
def _reset_client():
    import backend.backend.handlers.addon.edh.common.edhClient as ec

    yield
    ec._reset_client_state_for_tests()


@pytest.mark.unit
class TestConfiguredServerName:
    def test_the_configured_name_verifies_a_common_name_only_certificate(self, monkeypatch, tls_listener):
        _ec, pool = _client_pool(monkeypatch, tls_listener["bundle"], CERT_NAME)
        response = pool.request("GET", tls_listener["url"], retries=False, redirect=False, timeout=5)
        assert response.status == 200

    def test_the_name_matches_case_insensitively(self, monkeypatch, tls_listener):
        _ec, pool = _client_pool(monkeypatch, tls_listener["bundle"], CERT_NAME.lower())
        response = pool.request("GET", tls_listener["url"], retries=False, redirect=False, timeout=5)
        assert response.status == 200

    def test_without_a_name_the_endpoint_host_must_match_the_certificate(self, monkeypatch, tls_listener):
        # Control: the same listener and bundle, verified as the endpoint host, is refused.
        _ec, pool = _client_pool(monkeypatch, tls_listener["bundle"], "")
        with pytest.raises(urllib3.exceptions.SSLError):
            pool.request("GET", tls_listener["url"], retries=False, redirect=False, timeout=5)

    def test_a_different_name_is_refused(self, monkeypatch, tls_listener):
        _ec, pool = _client_pool(monkeypatch, tls_listener["bundle"], "edh.example.com")
        with pytest.raises(urllib3.exceptions.SSLError):
            pool.request("GET", tls_listener["url"], retries=False, redirect=False, timeout=5)

    def test_the_name_does_not_relax_chain_verification(self, monkeypatch, tls_listener):
        # No bundle: the certificate is self-signed, so the chain fails whatever name it carries.
        _ec, pool = _client_pool(monkeypatch, None, CERT_NAME)
        with pytest.raises(urllib3.exceptions.SSLError):
            pool.request("GET", tls_listener["url"], retries=False, redirect=False, timeout=5)
