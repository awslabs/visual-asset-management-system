"""
Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
SPDX-License-Identifier: Apache-2.0

A request the VAMS API redirects (the staged export payload, behind a presigned Amazon S3 URL) can fail
at the host it was redirected to. The error the CLI prints, on the console and under --json-output,
names that host and the status or the failure's type, and none of the URL's query string.
"""

import json

import pytest
import requests
from requests.adapters import BaseAdapter
from requests.models import Response
from unittest.mock import MagicMock

from vamscli.utils.api_client import APIClient

API_BASE = "https://abc123.execute-api.us-east-1.amazonaws.com/api"
S3_HOST = "aux-bucket.s3.us-east-1.amazonaws.com"
SIGNATURE = "5d41402abc4b2a76b9719d911017c592"
SESSION_TOKEN = "IQoJb3JpZ2luX2VjEXAMPLESESSIONTOKEN"
STAGED_URL = (
    f"https://{S3_HOST}/vams.assetExports/db1/asset1/deadbeef.json"
    f"?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Security-Token={SESSION_TOKEN}&X-Amz-Signature={SIGNATURE}"
)
ENVELOPE = {"presignedExportPayloadUrl": STAGED_URL, "presignedExportPayloadExpiresIn": 3600}


class _Adapter(BaseAdapter):
    """Answers the VAMS host with a 303 (or with the envelope, when `follow` is False), and the
    Amazon S3 host with `s3_status`, or raises `s3_raises` as the transport would."""

    def __init__(self, follow=True, s3_status=404, s3_raises=None):
        super().__init__()
        self.follow, self.s3_status, self.s3_raises = follow, s3_status, s3_raises
        self.s3_requests = 0

    def send(self, request, **kwargs):
        response = Response()
        response.request = request
        response.url = request.url
        response.headers["Content-Type"] = "application/json"
        if "execute-api" in request.url:
            response.status_code = 303 if self.follow else 200
            if self.follow:
                response.headers["Location"] = STAGED_URL
            response._content = json.dumps(ENVELOPE).encode("utf-8")
            return response
        self.s3_requests += 1
        if self.s3_raises is not None:
            # The transport's own message names the full URL, as urllib3's does.
            raise self.s3_raises(
                f"HTTPSConnectionPool(host='{S3_HOST}', port=443): Max retries exceeded with url: "
                f"{request.path_url}",
                request=request,
            )
        response.status_code = self.s3_status
        response._content = b"<Error><Code>NoSuchKey</Code></Error>"
        return response

    def close(self):
        pass


def _client(adapter):
    profile_manager = MagicMock()
    profile_manager.is_override_token.return_value = False
    profile_manager.load_auth_profile.return_value = {"access_token": "test-jwt-token"}
    client = APIClient(API_BASE, profile_manager=profile_manager)
    client.session.mount("https://", adapter)
    return client


def _export_error(adapter):
    with pytest.raises(Exception) as excinfo:
        _client(adapter).export_asset("db1", "asset1", {})
    # The adapter was reached, so the error is the one this test is about.
    assert adapter.s3_requests == 1
    return excinfo.value


def _printed_chain(error):
    """The exceptions a printed traceback shows: the error, then each cause or unsuppressed context."""
    while error is not None:
        yield error
        error = error.__cause__ or (None if error.__suppress_context__ else error.__context__)


def _assert_names_only_the_host(error):
    assert S3_HOST in str(error)
    chain = list(_printed_chain(error))
    for raised in chain:
        for secret in (SIGNATURE, SESSION_TOKEN, "deadbeef.json", "X-Amz-"):
            assert secret not in str(raised)
    # The transport's own exception, whose text names the full URL, is not in what a traceback prints.
    assert not any(isinstance(raised, requests.exceptions.RequestException) for raised in chain)


@pytest.mark.parametrize("follow", [True, False], ids=["redirect-followed", "envelope"])
def test_a_status_from_the_redirect_target_names_the_host_and_status(follow):
    error = _export_error(_Adapter(follow=follow, s3_status=404))
    _assert_names_only_the_host(error)
    assert "404" in str(error)


@pytest.mark.parametrize("follow", [True, False], ids=["redirect-followed", "envelope"])
@pytest.mark.parametrize(
    "raised", [requests.exceptions.ConnectionError, requests.exceptions.ReadTimeout]
)
def test_a_failure_to_reach_the_redirect_target_names_the_host_and_failure(follow, raised):
    error = _export_error(_Adapter(follow=follow, s3_raises=raised))
    _assert_names_only_the_host(error)
    assert raised.__name__ in str(error)


def test_a_failure_at_the_api_host_keeps_its_details_without_a_signature():
    """The API host's own connection failure still reports the transport's detail, scrubbed."""

    class _Refusing(BaseAdapter):
        def send(self, request, **kwargs):
            raise requests.exceptions.ConnectionError(
                f"Max retries exceeded with url: {request.path_url}?X-Amz-Signature={SIGNATURE}",
                request=request,
            )

        def close(self):
            pass

    with pytest.raises(Exception) as excinfo:
        _client(_Refusing()).get("/database")
    text = str(excinfo.value)
    assert "Connection error" in text and "Max retries exceeded" in text
    assert SIGNATURE not in text
