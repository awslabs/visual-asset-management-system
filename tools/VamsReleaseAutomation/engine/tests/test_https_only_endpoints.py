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
