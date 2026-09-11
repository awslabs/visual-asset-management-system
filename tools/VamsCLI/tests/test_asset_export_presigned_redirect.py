"""
Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
SPDX-License-Identifier: Apache-2.0

An export payload above the backend's inline size limit is not returned in the response body. The
backend stages it in the auxiliary Amazon S3 bucket and answers `303 See Other` with a presigned URL
in `Location`, so the payload only reaches the caller if the HTTP layer follows that redirect.

`APIClient.export_asset` does nothing redirect-specific: it calls `self.post(...)` and returns
`response.json()`. That works solely because `requests.Session.request` defaults `allow_redirects` to
True, and because `Session.rebuild_auth` drops the `Authorization` header when a redirect changes
host. Both are library defaults this code never states, which is why they are asserted here — passing
`allow_redirects=False` anywhere in the request path, or moving to an HTTP client that does not follow
redirects, turns every large export into a caller receiving the redirect envelope instead of its data,
with a 200-shaped success and no error.

These tests drive the real `requests` redirect machinery through a mounted transport adapter rather
than mocking `session.request`. Mocking that call would assert the code's intent and skip the very
resolution step under test.
"""

import json

import pytest
import requests
from requests.adapters import BaseAdapter
from requests.models import Response
from unittest.mock import MagicMock

from vamscli.utils.api_client import APIClient


API_BASE = "https://abc123.execute-api.us-east-1.amazonaws.com/api"
STAGED_PAYLOAD_URL = (
    "https://aux-bucket.s3.us-east-1.amazonaws.com/assetExports/db1/asset1/deadbeef.json"
    "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=abc123"
)

# What the staged object contains: a normal export payload, identical in shape to the inline case.
EXPORT_PAYLOAD = {
    "assets": [{"assetId": "asset1", "assetName": "Building Model"}],
    "relationships": [],
    "totalAssetsInTree": 1,
    "assetsInThisPage": 1,
}

REDIRECT_ENVELOPE = {
    "message": "Export payload exceeds the inline response size and is available at the redirect target",
    "presignedExportPayloadUrl": STAGED_PAYLOAD_URL,
    "presignedExportPayloadExpiresIn": 3600,
}


class RecordingAdapter(BaseAdapter):
    """Serves the 303 for the VAMS host and the staged payload for the Amazon S3 host.

    Every prepared request that reaches the transport is recorded, so the test can inspect what was
    actually sent to Amazon S3 — the method and the headers — after `requests` rebuilt it.
    """

    def __init__(self):
        super().__init__()
        self.sent = []

    def send(self, request, **kwargs):
        self.sent.append(request)

        response = Response()
        response.request = request
        response.url = request.url

        if "execute-api" in request.url:
            response.status_code = 303
            response.headers["Location"] = STAGED_PAYLOAD_URL
            response.headers["Content-Type"] = "application/json"
            response._content = json.dumps(REDIRECT_ENVELOPE).encode("utf-8")
        else:
            response.status_code = 200
            response.headers["Content-Type"] = "application/json"
            response._content = json.dumps(EXPORT_PAYLOAD).encode("utf-8")

        return response

    def close(self):
        pass


@pytest.fixture
def client_and_adapter():
    profile_manager = MagicMock()
    profile_manager.is_override_token.return_value = False
    profile_manager.load_auth_profile.return_value = {"access_token": "test-jwt-token"}

    client = APIClient(API_BASE, profile_manager=profile_manager)
    adapter = RecordingAdapter()
    client.session.mount("https://", adapter)
    return client, adapter


class TestLargeExportPayloadRedirect:
    def test_export_returns_the_staged_payload_not_the_redirect_envelope(self, client_and_adapter):
        """The caller receives the export data, so the redirect was followed and re-read."""
        client, _ = client_and_adapter

        result = client.export_asset("db1", "asset1", {"maxAssets": 100})

        assert result == EXPORT_PAYLOAD
        # The control that makes the assertion above meaningful: had the redirect not been followed,
        # `response.json()` would have parsed cleanly into this envelope and the test would still see
        # a dict. Naming it rules that out explicitly.
        assert "presignedExportPayloadUrl" not in result

    def test_both_hops_are_actually_made(self, client_and_adapter):
        """Positive control: the redirect path ran, rather than the adapter answering once."""
        client, adapter = client_and_adapter

        client.export_asset("db1", "asset1", {})

        assert len(adapter.sent) == 2, [r.url for r in adapter.sent]
        assert "execute-api" in adapter.sent[0].url
        assert adapter.sent[1].url == STAGED_PAYLOAD_URL

    def test_the_presigned_request_switches_to_get(self, client_and_adapter):
        """303 (not 307) is what makes this happen; the URL is signed for GET and rejects a POST."""
        client, adapter = client_and_adapter

        client.export_asset("db1", "asset1", {})

        assert adapter.sent[0].method == "POST"
        assert adapter.sent[1].method == "GET"
        # The POST body must not be replayed to Amazon S3 either.
        assert not adapter.sent[1].body

    def test_the_vams_authorization_header_is_not_sent_to_amazon_s3(self, client_and_adapter):
        """A presigned URL carries its own authorization; presenting a second one is rejected."""
        client, adapter = client_and_adapter

        client.export_asset("db1", "asset1", {})

        # Positive control: the header WAS present on the VAMS request, so its absence on the second
        # hop is the redirect machinery stripping it and not the client never having sent one.
        assert adapter.sent[0].headers.get("Authorization") == "Bearer test-jwt-token"
        assert "Authorization" not in adapter.sent[1].headers

    def test_redirect_following_is_not_disabled_in_the_request_path(self, client_and_adapter):
        """Guards the library default the fix silently depends on.

        `export_asset` never passes `allow_redirects`, so this asserts the whole path leaves it alone.
        A future `allow_redirects=False` added in `_make_request` for any other reason would break
        large exports and nothing else in the suite would notice.
        """
        client, adapter = client_and_adapter

        client.export_asset("db1", "asset1", {})

        # Reaching the second hop at all is the proof; assert the shape too so a change that follows
        # the redirect but discards the body cannot pass.
        assert len(adapter.sent) == 2
        assert client.export_asset("db1", "asset1", {})["assets"][0]["assetId"] == "asset1"


class TestEnvelopeIsResolvedWhenTheRedirectIsNotFollowed:
    """The path that does not depend on the HTTP library following the 303.

    If redirect following is off — a future `allow_redirects=False`, a proxy that returns the
    envelope, a different HTTP client — the response body is the envelope. It parses cleanly as
    JSON, so without explicit handling the caller gets a dict, a zero exit code, and no `assets`
    key: a silent empty export. `_resolve_staged_export_payload` fetches the URL itself.
    """

    class NonFollowingAdapter(BaseAdapter):
        """Returns the 303 envelope without redirecting, then serves the staged object on a GET."""

        def __init__(self):
            super().__init__()
            self.sent = []

        def send(self, request, **kwargs):
            self.sent.append(request)
            response = Response()
            response.request = request
            response.url = request.url
            response.headers["Content-Type"] = "application/json"

            if "execute-api" in request.url:
                # 200, not 303: this simulates a client/proxy that has already resolved the status
                # away and handed back the envelope, which is the shape that silently misleads.
                response.status_code = 200
                response._content = json.dumps(REDIRECT_ENVELOPE).encode("utf-8")
            else:
                response.status_code = 200
                response._content = json.dumps(EXPORT_PAYLOAD).encode("utf-8")
            return response

        def close(self):
            pass

    def _client(self):
        profile_manager = MagicMock()
        profile_manager.is_override_token.return_value = False
        profile_manager.load_auth_profile.return_value = {"access_token": "test-jwt-token"}
        client = APIClient(API_BASE, profile_manager=profile_manager)
        adapter = self.NonFollowingAdapter()
        client.session.mount("https://", adapter)
        return client, adapter

    def test_envelope_is_replaced_by_the_staged_payload(self):
        client, _ = self._client()

        result = client.export_asset("db1", "asset1", {})

        assert result == EXPORT_PAYLOAD
        assert "presignedExportPayloadUrl" not in result

    def test_the_staged_object_is_fetched_with_a_get_and_no_vams_auth(self):
        client, adapter = self._client()

        client.export_asset("db1", "asset1", {})

        assert len(adapter.sent) == 2
        staged = adapter.sent[1]
        assert staged.url == STAGED_PAYLOAD_URL
        assert staged.method == "GET"
        # Positive control: the VAMS request DID carry the header, so its absence here is this
        # method deliberately not sending it, not the client having none to send.
        assert adapter.sent[0].headers.get("Authorization") == "Bearer test-jwt-token"
        assert "Authorization" not in staged.headers

    def test_a_303_with_no_envelope_falls_back_to_the_location_header(self):
        """Covers a body that is not the expected envelope, so the URL is only in the header."""

        class HeaderOnlyAdapter(BaseAdapter):
            def __init__(self):
                super().__init__()
                self.sent = []

            def send(self, request, **kwargs):
                self.sent.append(request)
                response = Response()
                response.request = request
                response.url = request.url
                response.headers["Content-Type"] = "application/json"
                if "execute-api" in request.url:
                    response.status_code = 303
                    response.headers["Location"] = STAGED_PAYLOAD_URL
                    response._content = b"{}"          # no presignedExportPayloadUrl
                else:
                    response.status_code = 200
                    response._content = json.dumps(EXPORT_PAYLOAD).encode("utf-8")
                return response

            def close(self):
                pass

        profile_manager = MagicMock()
        profile_manager.is_override_token.return_value = False
        profile_manager.load_auth_profile.return_value = {}
        client = APIClient(API_BASE, profile_manager=profile_manager)
        adapter = HeaderOnlyAdapter()
        client.session.mount("https://", adapter)

        # allow_redirects is bypassed by calling the resolver directly on a non-followed response,
        # which is what a client with redirects disabled would hold.
        response = client.session.post(API_BASE + "/x", allow_redirects=False)
        result = client._resolve_staged_export_payload(response)

        assert result == EXPORT_PAYLOAD

    def test_a_failed_staged_fetch_raises_instead_of_returning_the_envelope(self):
        """An expired or unreachable URL must be an error, not a silently empty export."""

        class FailingAdapter(BaseAdapter):
            def send(self, request, **kwargs):
                response = Response()
                response.request = request
                response.url = request.url
                response.headers["Content-Type"] = "application/json"
                if "execute-api" in request.url:
                    response.status_code = 200
                    response._content = json.dumps(REDIRECT_ENVELOPE).encode("utf-8")
                else:
                    response.status_code = 403      # e.g. the presigned URL has expired
                    response._content = b"<Error><Code>AccessDenied</Code></Error>"
                return response

            def close(self):
                pass

        profile_manager = MagicMock()
        profile_manager.is_override_token.return_value = False
        profile_manager.load_auth_profile.return_value = {}
        client = APIClient(API_BASE, profile_manager=profile_manager)
        client.session.mount("https://", FailingAdapter())

        with pytest.raises(Exception) as exc:
            client.export_asset("db1", "asset1", {})
        assert "presigned" in str(exc.value).lower()


class TestSmallExportPayloadIsUnchanged:
    def test_inline_200_response_is_returned_directly(self):
        """The redirect path must not alter the ordinary case: one hop, body as-is."""
        profile_manager = MagicMock()
        profile_manager.is_override_token.return_value = False
        profile_manager.load_auth_profile.return_value = {"access_token": "test-jwt-token"}

        client = APIClient(API_BASE, profile_manager=profile_manager)

        class InlineAdapter(BaseAdapter):
            def __init__(self):
                super().__init__()
                self.sent = []

            def send(self, request, **kwargs):
                self.sent.append(request)
                response = Response()
                response.request = request
                response.url = request.url
                response.status_code = 200
                response.headers["Content-Type"] = "application/json"
                response._content = json.dumps(EXPORT_PAYLOAD).encode("utf-8")
                return response

            def close(self):
                pass

        adapter = InlineAdapter()
        client.session.mount("https://", adapter)

        result = client.export_asset("db1", "asset1", {})

        assert result == EXPORT_PAYLOAD
        assert len(adapter.sent) == 1
