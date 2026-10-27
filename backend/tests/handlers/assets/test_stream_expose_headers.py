# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Issue #847: the stream handlers must expose Location and ETag to cross-origin browser clients.

A browser hides every response header from JavaScript except the CORS-safelisted set unless the
server names it in ``Access-Control-Expose-Headers``. streamAsset builds its own response-header
dictionaries (it does not use ``commonHeaders``), so the 307 redirect's ``Location`` and the HEAD
200's ``ETag`` were unreadable under the cross-origin topology (the ALB web distribution, or a
client calling the API Gateway endpoint directly).

These are DURABLE guards: the exposure must stay. They are not marked temporary.

The handler is loaded by file path with boto3 stubbed, mirroring
``test_streamAsset_range_header_casing._load`` (the mock handlers package shadows the real one).
"""

import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "86400")
os.environ.setdefault("AWS_REGION", "us-east-1")

_STREAM_ASSET_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "assets", "streamAsset.py"
)

_cached_module = None


def _load():
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
                "streamAsset_expose_under_test", os.path.abspath(_STREAM_ASSET_PATH)
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


def _rest_event(method="GET"):
    return {
        "resource": "/database/{databaseId}/assets/{assetId}/download/stream/{proxy+}",
        "path": "/database/db1/assets/asset1/download/stream/scans/pump.glb",
        "httpMethod": method,
        "headers": {"Accept": "*/*"},
        "pathParameters": {"databaseId": "db1", "assetId": "asset1", "proxy": "scans/pump.glb"},
        "queryStringParameters": None,
        "requestContext": {
            "identity": {"sourceIp": "10.0.0.7"},
            "path": "/database/db1/assets/asset1/download/stream/scans/pump.glb",
            "httpMethod": method,
        },
        "body": None,
    }


def _wire(m):
    from common.auth.apiEvent import normalize_event

    def _claims(event):
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

    mock_s3 = MagicMock()
    mock_s3.head_object.return_value = {
        "ContentType": "model/gltf-binary",
        "ContentLength": 2048,
        "ETag": '"abc123etag"',
        "VersionId": "v-1",
        "StorageClass": "STANDARD",
    }
    body = MagicMock()
    body.read.return_value = b"x" * 32
    mock_s3.get_object.return_value = {
        "ContentType": "model/gltf-binary",
        "ContentLength": 2048,
        "Body": body,
        "ResponseMetadata": {"HTTPHeaders": {
            "accept-ranges": "bytes",
            "content-type": "model/gltf-binary",
            "content-length": "2048",
        }},
    }
    mock_s3.generate_presigned_url.return_value = "https://example-bucket.s3.amazonaws.com/signed"
    m.s3_client = mock_s3
    return mock_s3


def _expose(headers):
    """The Access-Control-Expose-Headers value as a set of trimmed header names."""
    raw = headers.get("Access-Control-Expose-Headers", "")
    return {part.strip() for part in raw.split(",") if part.strip()}


@pytest.mark.unit
class TestStreamAssetExposesHeadersCrossOrigin:
    """A cross-origin client can read Location (307) and ETag (HEAD 200)."""

    def test_the_307_redirect_exposes_location(self):
        m = _load()
        _wire(m)
        response = m.lambda_handler(_rest_event("GET"), MagicMock())
        assert response["statusCode"] == 307
        assert "Location" in response["headers"]
        # POSITIVE CONTROL: the header is actually set, so exposing it is meaningful.
        assert response["headers"]["Location"]
        assert "Location" in _expose(response["headers"])

    def test_the_head_200_exposes_etag(self):
        m = _load()
        _wire(m)
        response = m.lambda_handler(_rest_event("HEAD"), MagicMock())
        assert response["statusCode"] == 200
        assert response["headers"]["ETag"] == '"abc123etag"'
        assert "ETag" in _expose(response["headers"])
