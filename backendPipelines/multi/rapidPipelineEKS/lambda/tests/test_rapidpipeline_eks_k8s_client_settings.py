#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The Kubernetes client settings `get_k8s_client` intends must reach the requests it sends.

`kubernetes.client.Configuration` has no `timeout`, `socket_timeout`, `connect_timeout`, `user_agent` or
`retries_config` setting, so assigning them changes nothing: requests had no timeout, the default user
agent, and no retry of 429/5xx responses. The client takes a timeout per call (`_request_timeout`), the user
agent on the ApiClient, and a retry policy as a urllib3 `Retry` in `Configuration.retries`.

These tests run the real client against a local HTTP server, so they observe what is actually sent.
"""

import ast
import json
import os
import sys
import threading
import time
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import MagicMock, patch

import pytest
import urllib3
from kubernetes import client as k8s_client

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

if "customLogging" not in sys.modules:
    _cl_pkg = types.ModuleType("customLogging")
    _cl_logger = types.ModuleType("customLogging.logger")
    _cl_logger.safeLogger = lambda **kw: MagicMock()
    _cl_pkg.logger = _cl_logger
    sys.modules["customLogging"] = _cl_pkg
    sys.modules["customLogging.logger"] = _cl_logger

import kubernetes_utils as ku  # noqa: E402

_SOURCE_PATH = os.path.join(_LAMBDA_DIR, "kubernetes_utils.py")
_VERSION_BODY = json.dumps(
    {
        "major": "1",
        "minor": "36",
        "gitVersion": "v1.36.2",
        "gitCommit": "0" * 40,
        "gitTreeState": "clean",
        "buildDate": "2026-07-01T00:00:00Z",
        "goVersion": "go1.25",
        "compiler": "gc",
        "platform": "linux/amd64",
    }
).encode()


class _Server:
    """Local HTTP server answering every request with `status` after `delay` seconds."""

    def __init__(self, status=200, delay=0.0):
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _answer(self):
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                outer.requests.append((self.command, self.path, self.headers.get("User-Agent")))
                if delay:
                    time.sleep(delay)
                body = _VERSION_BODY if status == 200 else b'{"kind":"Status","code":%d}' % status
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = do_DELETE = _answer

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server_factory():
    servers = []

    def make(**kwargs):
        s = _Server(**kwargs)
        servers.append(s)
        return s

    yield make
    for s in servers:
        s.close()


def _api_client(url):
    """The client get_k8s_client builds, pointed at a local server (no TLS, no EKS token)."""
    configuration = k8s_client.Configuration()
    configuration.host = url
    configuration.connection_pool_maxsize = 20
    configuration.retries = ku.k8s_retry_policy()
    return ku.new_k8s_api_client(k8s_client, configuration)


@pytest.mark.unit
class TestNoIgnoredConfigurationSettings:
    def test_every_configuration_attribute_assigned_is_a_client_setting(self):
        tree = ast.parse(open(_SOURCE_PATH, encoding="utf-8").read())
        assigned = {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.ctx, ast.Store)
            and isinstance(node.value, ast.Name)
            and node.value.id == "configuration"
        }
        assert {"host", "verify_ssl", "ssl_ca_cert", "retries", "api_key"} <= assigned, assigned
        settings = set(dir(k8s_client.Configuration()))
        assert sorted(assigned - settings) == []

    def test_get_k8s_client_builds_its_api_client_through_the_helper(self):
        tree = ast.parse(open(_SOURCE_PATH, encoding="utf-8").read())
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "get_k8s_client")
        calls = [ast.unparse(n.func) for n in ast.walk(fn) if isinstance(n, ast.Call)]
        assert "new_k8s_api_client" in calls
        assert "client.ApiClient" not in calls


@pytest.mark.unit
class TestRetryPolicy:
    def test_policy(self):
        retry = ku.k8s_retry_policy()
        assert isinstance(retry, urllib3.util.Retry)
        assert retry.total == 5
        assert retry.backoff_factor == 1
        assert set(retry.status_forcelist) == {429, 500, 502, 503, 504}
        assert retry.raise_on_status is False
        assert "POST" not in retry.allowed_methods

    def test_idempotent_call_is_retried_then_reported_as_api_exception(self, server_factory, monkeypatch):
        monkeypatch.setattr(ku, "K8S_RETRY_BACKOFF_FACTOR", 0)
        server = server_factory(status=503)
        version_api = k8s_client.VersionApi(_api_client(server.url))
        with pytest.raises(k8s_client.ApiException) as err:
            version_api.get_code()
        assert err.value.status == 503
        assert len(server.requests) == 1 + ku.K8S_RETRY_TOTAL

    def test_job_creation_is_not_resent(self, server_factory, monkeypatch):
        monkeypatch.setattr(ku, "K8S_RETRY_BACKOFF_FACTOR", 0)
        server = server_factory(status=503)
        batch = k8s_client.BatchV1Api(_api_client(server.url))
        with pytest.raises(k8s_client.ApiException) as err:
            batch.create_namespaced_job(namespace="default", body={"metadata": {"name": "j"}})
        assert err.value.status == 503
        assert [r[0] for r in server.requests] == ["POST"]


@pytest.mark.unit
class TestTimeoutAndUserAgent:
    def test_user_agent_is_sent(self, server_factory):
        server = server_factory()
        k8s_client.VersionApi(_api_client(server.url)).get_code()
        assert server.requests[0][2] == ku.K8S_USER_AGENT

    def test_default_timeout_reaches_urllib3(self, server_factory):
        server = server_factory()
        api_client = _api_client(server.url)
        pool = api_client.rest_client.pool_manager
        with patch.object(pool, "request", wraps=pool.request) as spy:
            k8s_client.VersionApi(api_client).get_code()
        timeout = spy.call_args.kwargs["timeout"]
        assert isinstance(timeout, urllib3.Timeout)
        assert (timeout.connect_timeout, timeout.read_timeout) == ku.K8S_REQUEST_TIMEOUT

    def test_explicit_timeout_is_kept(self, server_factory):
        server = server_factory()
        api_client = _api_client(server.url)
        pool = api_client.rest_client.pool_manager
        with patch.object(pool, "request", wraps=pool.request) as spy:
            k8s_client.VersionApi(api_client).get_code(_request_timeout=(5, 7))
        timeout = spy.call_args.kwargs["timeout"]
        assert (timeout.connect_timeout, timeout.read_timeout) == (5, 7)

    def test_unresponsive_server_fails_at_the_read_timeout(self, server_factory, monkeypatch):
        monkeypatch.setattr(ku, "K8S_REQUEST_TIMEOUT", (1, 0.5))
        monkeypatch.setattr(ku, "K8S_RETRY_TOTAL", 0)
        server = server_factory(delay=3)
        version_api = k8s_client.VersionApi(_api_client(server.url))
        started = time.monotonic()
        with pytest.raises(urllib3.exceptions.HTTPError):
            version_api.get_code()
        assert time.monotonic() - started < 2
