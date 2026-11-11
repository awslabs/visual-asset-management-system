# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Token-bearing HTTP calls go to https:// URLs only.

The Bitbucket API (``githost.apiUrl``) and the GitLab deployments API of the approval-record CI script
(``CI_API_V4_URL``) both carry a token; a plain-http or ``file:`` URL is refused before any request is built,
so the token never leaves over an unencrypted or local scheme."""

from __future__ import annotations

import importlib.util
import urllib.request
from pathlib import Path

import pytest

from vra.errors import UsageError
from vra.githost import BitbucketHost

PKG = Path(__file__).resolve().parents[2]


def _approval_record():
    spec = importlib.util.spec_from_file_location(
        "approval_record_https", PKG / "cicd/codebuild/scripts/approval_record.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("no request may be sent")
    monkeypatch.setattr(urllib.request, "urlopen", refuse)


@pytest.mark.parametrize("url", ["http://bitbucket.example.com/2.0", "file:///etc/passwd"])
def test_bitbucket_refuses_a_non_https_api_url(url, no_network):
    host = BitbucketHost(None, {"type": "bitbucket", "repo": "team/fork", "apiUrl": url, "tokenSecret": "vra/bb"})
    with pytest.raises(UsageError, match="https://"):
        host._api("GET", "/repositories/team/fork/pullrequests")


@pytest.mark.parametrize("url", ["http://gitlab.example.com/api/v4", "file:///tmp/api"])
def test_gitlab_approval_lookup_refuses_a_non_https_api_url(url, no_network):
    mod = _approval_record()
    env = {"CI_API_V4_URL": url, "CI_PROJECT_ID": "7", "VRA_GITLAB_API_TOKEN": "t", "CI_JOB_ID": "1",
           "CI_ENVIRONMENT_NAME": "prod"}
    with pytest.raises(mod.Refused, match="https://"):
        mod.gitlab_deployment_of_job(env)


# ------------------------------------------------------------------ redirects
import http.server  # noqa: E402
import threading  # noqa: E402
import urllib.error  # noqa: E402

from vra import githost  # noqa: E402


def _server(handler_cls):
    srv = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def redirect_pair():
    """A server that redirects every request to a second server, which records what it received."""
    seen = []

    class Target(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            seen.append(dict(self.headers))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *a):
            pass

    target = _server(Target)

    class Redirect(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{target.server_port}/elsewhere")
            self.end_headers()

        def log_message(self, *a):
            pass

    origin = _server(Redirect)
    yield f"http://127.0.0.1:{origin.server_port}/api", seen
    origin.shutdown()
    target.shutdown()


@pytest.mark.parametrize("opener", ["githost", "approval_record"])
def test_token_requests_never_follow_a_redirect(redirect_pair, opener):
    url, seen = redirect_pair
    make = githost.no_redirect_opener if opener == "githost" else _approval_record().no_redirect_opener
    req = urllib.request.Request(url, headers={"Authorization": "Bearer secret-token", "PRIVATE-TOKEN": "t"})
    with pytest.raises(urllib.error.HTTPError) as ei:
        make().open(req, timeout=10)
    assert ei.value.code == 302
    assert seen == []           # the redirect target never got the request, so never the token


def test_plain_urlopen_would_have_sent_the_token(redirect_pair):
    """Why the opener exists: urllib's default redirect handler copies the headers to the redirect target."""
    url, seen = redirect_pair
    req = urllib.request.Request(url, headers={"Authorization": "Bearer secret-token"})
    urllib.request.urlopen(req, timeout=10).read()  # noqa: S310 # nosec B310 - local test server
    assert seen and seen[0].get("Authorization") == "Bearer secret-token"
