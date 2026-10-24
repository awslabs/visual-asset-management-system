# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The EDH root-key loader and the TLS trust of the client's connection pool.

The root key is a cluster-wide admin credential: it is read from an operator-managed secret, cached
for an hour so a rotation is picked up without a container recycle, and refused when the secret is
not the documented ``{"rootKey": "..."}`` object or cannot be read at all -- both are the typed
credential failure, never a raw botocore exception. The pool always verifies the server certificate;
a private-CA bundle, when configured, is written to /tmp once and handed to urllib3 as ``ca_certs``,
and a bundle that cannot be read or written is the typed unavailable failure, retried on the next call.
A configured server name is asserted against the certificate in place of the endpoint host, through a
context that also matches a common name -- the only name the EDH installer's certificate carries.
"""

import inspect
import json
import re
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from backend.backend.handlers.addon.edh.common import edhClient as _ec  # noqa: F401

PEM = "-----BEGIN CERTIFICATE-----\nMIIBszCCAVoCCQD\n-----END CERTIFICATE-----\n"
CA_ARN = "arn:aws:secretsmanager:us-east-1:123456789012:secret:vams-edh-ca-bundle-AbCdEf"


class FakeSecretsManager:
    """Answers successive reads from ``values``: a string is the SecretString, an exception is raised."""

    def __init__(self, values):
        self.values = list(values)
        self.reads = []

    def get_secret_value(self, SecretId=None):
        self.reads.append(SecretId)
        value = self.values.pop(0)
        if isinstance(value, Exception):
            raise value
        return {"SecretString": value}


def _client_error(code):
    return ClientError({"Error": {"Code": code, "Message": f"{code} said so"}}, "GetSecretValue")


def _install_secret(monkeypatch, *values):
    import backend.backend.handlers.addon.edh.common.edhClient as ec

    fake = FakeSecretsManager(values)
    monkeypatch.setattr(ec, "_get_secretsmanager_client", lambda: fake)
    return ec, fake


@pytest.mark.unit
class TestRootKeyLoader:
    def test_reads_the_root_key_field_of_the_json_secret(self, monkeypatch):
        ec, fake = _install_secret(monkeypatch, json.dumps({"rootKey": "k1"}))
        assert ec._load_edh_root_key() == "k1"
        assert fake.reads == [ec.EDH_ADMIN_KEY_SECRET_ARN]

    def test_is_cached_within_the_ttl(self, monkeypatch):
        ec, fake = _install_secret(monkeypatch, json.dumps({"rootKey": "k1"}), json.dumps({"rootKey": "k2"}))
        assert ec._load_edh_root_key() == "k1"
        assert ec._load_edh_root_key() == "k1"
        assert len(fake.reads) == 1

    def test_is_reread_once_the_ttl_lapses(self, monkeypatch):
        ec, fake = _install_secret(monkeypatch, json.dumps({"rootKey": "k1"}), json.dumps({"rootKey": "k2"}))
        assert ec._load_edh_root_key() == "k1"
        monkeypatch.setattr(ec, "_cached_root_key_fetched_at",
                            ec._cached_root_key_fetched_at - (ec._SECRET_CACHE_TTL_SECONDS + 1))
        assert ec._load_edh_root_key() == "k2"
        assert len(fake.reads) == 2

    def test_invalidation_forces_a_reread(self, monkeypatch):
        ec, fake = _install_secret(monkeypatch, json.dumps({"rootKey": "k1"}), json.dumps({"rootKey": "k2"}))
        assert ec._load_edh_root_key() == "k1"
        ec._invalidate_secret_cache()
        assert ec._load_edh_root_key() == "k2"
        assert len(fake.reads) == 2

    def test_the_ttl_is_one_hour(self):
        import backend.backend.handlers.addon.edh.common.edhClient as ec
        assert ec._SECRET_CACHE_TTL_SECONDS == 3600

    @pytest.mark.parametrize("secret_string", [
        "not json",
        json.dumps("k1"),
        json.dumps({"token": "k1"}),
        json.dumps({"rootKey": ""}),
        json.dumps({"rootKey": 7}),
    ])
    def test_a_malformed_secret_is_an_auth_error(self, monkeypatch, secret_string):
        ec, _fake = _install_secret(monkeypatch, secret_string)
        with pytest.raises(ec.EdhAuthError):
            ec._load_edh_root_key()

    def test_the_secret_field_name_is_root_key(self):
        import backend.backend.handlers.addon.edh.common.edhClient as ec
        assert ec.EDH_ROOT_KEY_SECRET_FIELD == "rootKey"

    def test_a_secrets_manager_error_is_an_auth_error(self, monkeypatch):
        ec, fake = _install_secret(monkeypatch, _client_error("AccessDeniedException"))
        with pytest.raises(ec.EdhAuthError):
            ec._load_edh_root_key()
        assert fake.reads == [ec.EDH_ADMIN_KEY_SECRET_ARN]
        assert ec._cached_root_key is None

    def test_a_transport_failure_reading_the_root_key_is_an_auth_error(self, monkeypatch):
        ec, _fake = _install_secret(monkeypatch, EndpointConnectionError(endpoint_url="https://secretsmanager.example"))
        with pytest.raises(ec.EdhAuthError):
            ec._load_edh_root_key()
        assert ec._cached_root_key is None

    def test_a_failed_read_leaves_the_cache_empty_so_the_next_load_reads_again(self, monkeypatch):
        ec, fake = _install_secret(monkeypatch, _client_error("ThrottlingException"), json.dumps({"rootKey": "k1"}))
        with pytest.raises(ec.EdhAuthError):
            ec._load_edh_root_key()
        assert ec._load_edh_root_key() == "k1"
        assert len(fake.reads) == 2

    def test_a_failed_read_logs_the_error_class_and_nothing_of_the_error(self, monkeypatch):
        ec, _fake = _install_secret(monkeypatch, _client_error("AccessDeniedException"))
        recorder = MagicMock()
        monkeypatch.setattr(ec, "logger", recorder)
        with pytest.raises(ec.EdhAuthError):
            ec._load_edh_root_key()
        recorder.warning.assert_called_once()
        assert recorder.warning.call_args.kwargs["extra"] == {"error": "ClientError"}
        assert "said so" not in repr(recorder.mock_calls)


@pytest.mark.unit
class TestCaBundle:
    def test_no_bundle_arn_means_no_secret_read_and_no_bundle(self, monkeypatch):
        ec, fake = _install_secret(monkeypatch)
        monkeypatch.setattr(ec, "EDH_CA_BUNDLE_SECRET_ARN", "")
        assert ec._load_ca_bundle_path() is None
        assert fake.reads == []

    def test_a_bundle_arn_writes_the_pem_to_tmp_once(self, monkeypatch, tmp_path):
        ec, fake = _install_secret(monkeypatch, PEM)
        monkeypatch.setattr(ec, "EDH_CA_BUNDLE_SECRET_ARN", CA_ARN)
        target = str(tmp_path / "edh-ca-bundle.pem")
        monkeypatch.setattr(ec, "_CA_BUNDLE_PATH", target)
        assert ec._load_ca_bundle_path() == target
        assert ec._load_ca_bundle_path() == target
        assert fake.reads == [CA_ARN]
        with open(target, "r", encoding="utf-8") as handle:
            assert handle.read() == PEM

    def test_the_default_bundle_path_is_under_tmp(self):
        import backend.backend.handlers.addon.edh.common.edhClient as ec
        assert ec._CA_BUNDLE_PATH.startswith("/tmp/")

    def test_a_bundle_read_failure_is_unavailable(self, monkeypatch, tmp_path):
        ec, fake = _install_secret(monkeypatch, _client_error("ResourceNotFoundException"))
        monkeypatch.setattr(ec, "EDH_CA_BUNDLE_SECRET_ARN", CA_ARN)
        monkeypatch.setattr(ec, "_CA_BUNDLE_PATH", str(tmp_path / "edh-ca-bundle.pem"))
        with pytest.raises(ec.EdhUnavailableError):
            ec._load_ca_bundle_path()
        assert fake.reads == [CA_ARN]
        assert (ec._ca_bundle_resolved, ec._ca_bundle_path) == (False, None)

    def test_a_bundle_write_failure_is_unavailable_and_the_next_call_tries_again(self, monkeypatch, tmp_path):
        ec, fake = _install_secret(monkeypatch, PEM, PEM)
        monkeypatch.setattr(ec, "EDH_CA_BUNDLE_SECRET_ARN", CA_ARN)
        monkeypatch.setattr(ec, "_CA_BUNDLE_PATH", str(tmp_path / "no-such-dir" / "edh-ca-bundle.pem"))
        with pytest.raises(ec.EdhUnavailableError):
            ec._load_ca_bundle_path()
        assert ec._ca_bundle_resolved is False
        target = str(tmp_path / "edh-ca-bundle.pem")
        monkeypatch.setattr(ec, "_CA_BUNDLE_PATH", target)
        assert ec._load_ca_bundle_path() == target
        assert fake.reads == [CA_ARN, CA_ARN]

    def test_the_pool_requires_certificates_and_uses_the_bundle(self, monkeypatch, tmp_path):
        ec, _fake = _install_secret(monkeypatch, PEM)
        monkeypatch.setattr(ec, "EDH_CA_BUNDLE_SECRET_ARN", CA_ARN)
        target = str(tmp_path / "ca.pem")
        monkeypatch.setattr(ec, "_CA_BUNDLE_PATH", target)
        built = []

        class FakePool:
            def __init__(self, **kwargs):
                built.append(kwargs)

        monkeypatch.setattr(ec.urllib3, "PoolManager", FakePool)
        ec._get_http_pool()
        assert built == [{"cert_reqs": "CERT_REQUIRED", "ca_certs": target}]

    def test_a_configured_server_name_is_asserted_through_a_common_name_aware_context(
        self, monkeypatch, tmp_path
    ):
        ec, _fake = _install_secret(monkeypatch, PEM)
        monkeypatch.setattr(ec, "EDH_CA_BUNDLE_SECRET_ARN", CA_ARN)
        monkeypatch.setattr(ec, "EDH_API_TLS_SERVER_NAME", "EDH.DEFAULT.CREATE.YOUR.OWN.CERTIFICATE")
        target = str(tmp_path / "ca.pem")
        monkeypatch.setattr(ec, "_CA_BUNDLE_PATH", target)
        built = []
        contexts = []

        class FakePool:
            def __init__(self, **kwargs):
                built.append(kwargs)

        class FakeContext:
            def __init__(self):
                self.loaded = []
                self.hostname_checks_common_name = False

            def load_verify_locations(self, cafile=None, capath=None, cadata=None):
                self.loaded.append(cafile)

        def fake_create_context():
            contexts.append(FakeContext())
            return contexts[-1]

        monkeypatch.setattr(ec, "create_urllib3_context", fake_create_context)
        monkeypatch.setattr(ec.urllib3, "PoolManager", FakePool)
        ec._get_http_pool()
        [kwargs] = built
        assert kwargs["cert_reqs"] == "CERT_REQUIRED"
        assert kwargs["ca_certs"] == target
        assert kwargs["assert_hostname"] == "EDH.DEFAULT.CREATE.YOUR.OWN.CERTIFICATE"
        [context] = contexts
        assert kwargs["ssl_context"] is context
        assert context.loaded == [target]
        assert context.hostname_checks_common_name is True

    def test_the_pool_requires_certificates_without_a_bundle(self, monkeypatch):
        ec, _fake = _install_secret(monkeypatch)
        monkeypatch.setattr(ec, "EDH_CA_BUNDLE_SECRET_ARN", "")
        built = []

        class FakePool:
            def __init__(self, **kwargs):
                built.append(kwargs)

        monkeypatch.setattr(ec.urllib3, "PoolManager", FakePool)
        ec._get_http_pool()
        ec._get_http_pool()
        assert built == [{"cert_reqs": "CERT_REQUIRED"}]

    def test_the_seam_disables_urllib3_retries_and_redirects_and_forwards_kwargs(self, monkeypatch):
        ec, _fake = _install_secret(monkeypatch)
        calls = []

        class FakePool:
            def request(self, method, url, **kwargs):
                calls.append((method, url, kwargs))
                return object()

        monkeypatch.setattr(ec, "_get_http_pool", lambda: FakePool())
        ec._http_request("GET", "https://edh.example.internal/api/x", headers={"a": "b"})
        assert calls[0][0:2] == ("GET", "https://edh.example.internal/api/x")
        assert calls[0][2]["retries"] is False
        assert calls[0][2]["redirect"] is False
        assert calls[0][2]["headers"] == {"a": "b"}

    def test_no_certificate_verification_toggle_exists(self):
        import backend.backend.handlers.addon.edh.common.edhClient as ec
        source = inspect.getsource(ec)
        assert "CERT_NONE" not in source
        # A configured server name is asserted; hostname checking is never switched off.
        assert not re.search(r"assert_hostname\W*[=:]\W*False", source)
        assert not re.search(r"check_hostname\s*=\s*False", source)
        # Positive controls: the guard reads the real source, which names the required mode and
        # asserts the configured name.
        assert "CERT_REQUIRED" in source
        assert 'pool_kwargs["assert_hostname"] = EDH_API_TLS_SERVER_NAME' in source
