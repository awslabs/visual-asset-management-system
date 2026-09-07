# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""S3-CONTRACTS-029: the stream handlers must read the request Range header case-insensitively.

The API Gateway REST (v1) ``aws_proxy`` integration delivers ``event['headers']`` with the
casing the client sent -- an HTTP/1.1 client sends ``Range``, an HTTP/2 client lower-cases it
to ``range`` on the wire -- and ``common.auth.apiEvent.normalize_event`` normalizes the path,
method and null parameters but not header keys. A lowercase-only lookup therefore drops a
capitalised ``Range``: the S3 GetObject call carries no ``Range``, the download audit entry
records ``rangeHeader: None``, and with ``ALWAYS_REDIRECT_TO_PRESIGNED`` toggled off the
response carries the whole object instead of the requested byte range.

Coverage here is deliberately paired: every casing assertion has a lowercase counterpart
(the casing that already worked) and a no-Range counterpart, so widening the lookup cannot
be mistaken for having broken the path that was already correct.
"""

import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# Env vars streamAsset requires at import time.
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "86400")
os.environ.setdefault("AWS_REGION", "us-east-1")

_STREAM_ASSET_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "assets", "streamAsset.py"
)

_cached_module = None


def _load():
    """Load the real streamAsset module by file path with boto3 stubbed.

    The mock handlers package registered by the root conftest shadows the real package, so a
    normal import cannot reach the real module (mirrors test_downloadAsset_bulk._load).
    """
    global _cached_module
    if _cached_module is not None:
        return _cached_module

    stub_names = ("handlers.assets.assetVersions", "handlers.authz", "handlers.auth")
    saved = {name: sys.modules.get(name) for name in stub_names}
    versions_stub = types.ModuleType("handlers.assets.assetVersions")
    versions_stub.resolve_file_version_from_asset_version = MagicMock(return_value=None)
    versions_stub.resolve_asset_version_id_from_alias = MagicMock(return_value=None)
    sys.modules["handlers.assets.assetVersions"] = versions_stub
    authz_stub = types.ModuleType("handlers.authz")
    authz_stub.CasbinEnforcer = MagicMock()
    sys.modules["handlers.authz"] = authz_stub
    auth_stub = types.ModuleType("handlers.auth")
    auth_stub.request_to_claims = MagicMock(return_value={"tokens": ["test-user"], "roles": []})
    sys.modules["handlers.auth"] = auth_stub

    try:
        with patch("boto3.client", return_value=MagicMock()), patch(
            "boto3.resource", return_value=MagicMock()
        ):
            spec = importlib.util.spec_from_file_location(
                "streamAsset_under_test", os.path.abspath(_STREAM_ASSET_PATH)
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
            else:
                sys.modules.pop(name, None)
    _cached_module = module
    return module


def _rest_event(headers, method="GET"):
    """An API Gateway REST (v1) proxy event, the shape the deployed handler receives."""
    return {
        "resource": "/database/{databaseId}/assets/{assetId}/download/stream/{proxy+}",
        "path": "/database/db1/assets/asset1/download/stream/scans/pump.glb",
        "httpMethod": method,
        "headers": headers,
        "pathParameters": {
            "databaseId": "db1",
            "assetId": "asset1",
            "proxy": "scans/pump.glb",
        },
        "queryStringParameters": None,
        "requestContext": {
            "identity": {"sourceIp": "10.0.0.7"},
            "path": "/database/db1/assets/asset1/download/stream/scans/pump.glb",
            "httpMethod": method,
        },
        "body": None,
    }


def _wire(m, content_length=1024):
    """Point the module at a mocked asset/S3 context and return the S3 client mock."""
    # The root conftest registers the REAL common.auth.apiEvent in sys.modules and
    # refreshes it per test, so this is the same normalizer the handler runs behind.
    from common.auth.apiEvent import normalize_event

    def _claims(event):
        # The real request_to_claims normalizes the event; keep that so the REST-shaped
        # event under test reaches the handler exactly as it does in the deployment.
        normalize_event(event)
        return {"tokens": ["test-user"], "roles": [], "mfaEnabled": False}

    m.request_to_claims = MagicMock(side_effect=_claims)
    m.CasbinEnforcer = MagicMock()
    m.get_asset_details = MagicMock(return_value={
        "databaseId": "db1", "assetId": "asset1", "isDistributable": True,
        "bucketId": "bucket-1", "assetLocation": {"Key": "asset1/"},
    })
    m.get_default_bucket_details = MagicMock(return_value={
        "bucketId": "bucket-1", "bucketName": "test-bucket", "baseAssetsPrefix": "",
    })
    m.validateUnallowedFileExtensionAndContentType = MagicMock(return_value=True)
    m.resolve_file_version_from_asset_version = MagicMock(return_value=None)
    m.resolve_asset_version_id_from_alias = MagicMock(return_value=None)
    m.log_file_download_streamed = MagicMock()

    body = MagicMock()
    body.read.return_value = b"x" * min(content_length, 32)
    response_headers = {
        "accept-ranges": "bytes",
        "content-type": "model/gltf-binary",
        "content-length": str(content_length),
    }

    mock_s3 = MagicMock()
    mock_s3.get_object.return_value = {
        "ContentType": "model/gltf-binary",
        "ContentLength": content_length,
        "Body": body,
        "ResponseMetadata": {"HTTPHeaders": response_headers},
    }
    mock_s3.generate_presigned_url.return_value = "https://example-bucket.s3.amazonaws.com/signed"
    m.s3_client = mock_s3
    return mock_s3


def _make_range_aware(mock_s3, total_size):
    """Make the S3 stub answer like S3 does: partial content only when Range was sent.

    Without this the canned response would carry ``content-range`` even for a request that
    lost its Range, and the response assertion would hold whether or not the fix is present.
    """
    def _get_object(**kwargs):
        body = MagicMock()
        rng = kwargs.get("Range")
        if rng:
            first, last = rng.split("=", 1)[1].split("-")
            first, last = int(first), int(last)
            length = last - first + 1
            headers = {
                "accept-ranges": "bytes",
                "content-type": "model/gltf-binary",
                "content-length": str(length),
                "content-range": f"bytes {first}-{last}/{total_size}",
            }
        else:
            length = total_size
            headers = {
                "accept-ranges": "bytes",
                "content-type": "model/gltf-binary",
                "content-length": str(total_size),
            }
        body.read.return_value = b"x" * min(length, 32)
        return {
            "ContentType": "model/gltf-binary",
            "ContentLength": length,
            "Body": body,
            "ResponseMetadata": {"HTTPHeaders": headers},
        }

    mock_s3.get_object.side_effect = _get_object
    return mock_s3


def _s3_get_object_kwargs(mock_s3):
    assert mock_s3.get_object.called, "the handler never reached the S3 GetObject call"
    return mock_s3.get_object.call_args.kwargs


def _audit_detail(m):
    assert m.log_file_download_streamed.called, "the handler never wrote the download audit entry"
    return m.log_file_download_streamed.call_args[0][4]


@pytest.mark.unit
class TestRangeHeaderCasing:
    """The Range header is honoured whatever casing the client sent it with."""

    @pytest.mark.parametrize("header_name", ["Range", "range", "RANGE", "rAnGe"])
    def test_range_reaches_s3_for_every_casing(self, header_name):
        """A ranged request is forwarded to S3 regardless of the header's casing.

        Only the ``range`` spelling passes with a lowercase-only lookup; ``Range`` is what an
        HTTP/1.1 client (curl, python-requests, most SDKs) actually sends.
        """
        m = _load()
        mock_s3 = _wire(m)
        event = _rest_event({header_name: "bytes=0-1048575", "Accept": "*/*"})

        response = m.lambda_handler(event, MagicMock())

        assert response["statusCode"] == 307
        assert _s3_get_object_kwargs(mock_s3)["Range"] == "bytes=0-1048575"

    @pytest.mark.parametrize("header_name", ["Range", "range"])
    def test_audit_entry_records_the_requested_range(self, header_name):
        """The download audit entry records the range the caller actually asked for."""
        m = _load()
        _wire(m)
        event = _rest_event({header_name: "bytes=200-299"})

        m.lambda_handler(event, MagicMock())

        assert _audit_detail(m)["rangeHeader"] == "bytes=200-299"

    @pytest.mark.parametrize("header_name", ["Range", "range"])
    def test_inline_streaming_returns_the_partial_content_range(self, header_name):
        """With the presigned redirect toggled off, a ranged read streams the byte range.

        This is the branch the delivery-mode comment invites a maintainer to re-enable: a
        dropped Range makes S3 return the whole object, so the response carries no
        ``Content-Range`` and the caller silently receives more bytes than it asked for.
        """
        m = _load()
        mock_s3 = _make_range_aware(_wire(m), total_size=4096)
        event = _rest_event({header_name: "bytes=0-99"})

        with patch.object(m, "ALWAYS_REDIRECT_TO_PRESIGNED", False):
            response = m.lambda_handler(event, MagicMock())

        assert _s3_get_object_kwargs(mock_s3)["Range"] == "bytes=0-99"
        assert response["headers"]["Content-Range"] == "bytes 0-99/4096"
        assert response["headers"]["Content-Length"] == "100"

    def test_inline_streaming_without_a_range_returns_the_whole_object(self):
        """Positive control for the branch above: an unranged read still returns the object."""
        m = _load()
        mock_s3 = _make_range_aware(_wire(m), total_size=4096)
        event = _rest_event({"Accept": "*/*"})

        with patch.object(m, "ALWAYS_REDIRECT_TO_PRESIGNED", False):
            response = m.lambda_handler(event, MagicMock())

        assert "Range" not in _s3_get_object_kwargs(mock_s3)
        assert "Content-Range" not in response["headers"]
        assert response["headers"]["Content-Length"] == "4096"


@pytest.mark.unit
class TestRangeAbsentAndMalformedHeaders:
    """Requests that carry no usable Range keep behaving exactly as before."""

    def test_unranged_request_sends_no_range_to_s3(self):
        """A plain GET must not acquire a Range, and must audit rangeHeader as None."""
        m = _load()
        mock_s3 = _wire(m)
        event = _rest_event({"Accept": "*/*", "User-Agent": "curl/8.5.0"})

        response = m.lambda_handler(event, MagicMock())

        assert response["statusCode"] == 307
        assert "Range" not in _s3_get_object_kwargs(mock_s3)
        assert _audit_detail(m)["rangeHeader"] is None

    def test_null_headers_do_not_break_the_request(self):
        """REST can deliver ``headers`` as JSON null; the request still succeeds unranged."""
        m = _load()
        mock_s3 = _wire(m)
        event = _rest_event(None)

        response = m.lambda_handler(event, MagicMock())

        assert response["statusCode"] == 307
        assert "Range" not in _s3_get_object_kwargs(mock_s3)

    def test_non_string_header_keys_are_ignored(self):
        """A header map carrying a non-string key must not fail the request."""
        m = _load()
        mock_s3 = _wire(m)
        event = _rest_event({"Range": "bytes=0-9", 7: "junk"})

        response = m.lambda_handler(event, MagicMock())

        assert response["statusCode"] == 307
        assert _s3_get_object_kwargs(mock_s3)["Range"] == "bytes=0-9"

    def test_similar_header_names_are_not_mistaken_for_range(self):
        """Only the Range header itself is read -- not If-Range or a vendor range header."""
        m = _load()
        mock_s3 = _wire(m)
        event = _rest_event({"If-Range": '"etag-value"', "X-Amz-Range": "bytes=0-9"})

        m.lambda_handler(event, MagicMock())

        assert "Range" not in _s3_get_object_kwargs(mock_s3)
