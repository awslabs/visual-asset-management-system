# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""S3-CONTRACTS-029: the auxiliary-preview stream handler reads Range case-insensitively.

``streamAuxiliaryPreviewAsset`` carries the same Range lookup as ``streamAsset`` and serves
the range-based octree / 3D-tile viewer data, where partial reads are the normal access
pattern. See test_streamAsset_range_header_casing.py for the REST-versus-HTTP-API header
casing background.
"""

import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# Env vars streamAuxiliaryPreviewAsset requires at import time.
os.environ.setdefault("S3_ASSET_AUXILIARY_BUCKET", "test-asset-auxiliary-bucket")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "86400")
os.environ.setdefault("AWS_REGION", "us-east-1")

_MODULE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "assets",
    "streamAuxiliaryPreviewAsset.py",
)

# The auxiliary preview key the viewer requests, asset-relative as the route delivers it.
AUX_KEY = "scans/pump.e57/preview/r/octree.bin"

_cached_module = None


def _load():
    """Load the real streamAuxiliaryPreviewAsset module by file path with boto3 stubbed."""
    global _cached_module
    if _cached_module is not None:
        return _cached_module

    stub_names = ("handlers.authz", "handlers.auth")
    saved = {name: sys.modules.get(name) for name in stub_names}
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
                "streamAuxiliaryPreviewAsset_under_test", os.path.abspath(_MODULE_PATH)
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


def _rest_event(headers):
    """An API Gateway REST (v1) proxy event, the shape the deployed handler receives."""
    path = f"/database/db1/assets/asset1/auxiliaryPreviewAssets/stream/{AUX_KEY}"
    return {
        "resource": (
            "/database/{databaseId}/assets/{assetId}/auxiliaryPreviewAssets/stream/{proxy+}"
        ),
        "path": path,
        "httpMethod": "GET",
        "headers": headers,
        "pathParameters": {"databaseId": "db1", "assetId": "asset1", "proxy": AUX_KEY},
        "queryStringParameters": None,
        "requestContext": {
            "identity": {"sourceIp": "10.0.0.7"},
            "path": path,
            "httpMethod": "GET",
        },
        "body": None,
    }


def _wire(m):
    """Point the module at a mocked asset/S3 context and return the S3 client mock."""
    # The root conftest registers the REAL common.auth.apiEvent in sys.modules and
    # refreshes it per test, so this is the same normalizer the handler runs behind.
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
    m.validateUnallowedFileExtensionAndContentType = MagicMock(return_value=True)
    m.log_file_download_streamed = MagicMock()

    body = MagicMock()
    body.read.return_value = b"octree-bytes"
    mock_s3 = MagicMock()
    mock_s3.get_object.return_value = {
        "ContentType": "application/octet-stream",
        "ContentLength": 2048,
        "Body": body,
        "ResponseMetadata": {"HTTPHeaders": {
            "accept-ranges": "bytes",
            "content-type": "application/octet-stream",
            "content-length": "2048",
        }},
    }
    mock_s3.generate_presigned_url.return_value = "https://aux-bucket.s3.amazonaws.com/signed"
    m.s3_client = mock_s3
    return mock_s3


def _s3_get_object_kwargs(mock_s3):
    assert mock_s3.get_object.called, "the handler never reached the S3 GetObject call"
    return mock_s3.get_object.call_args.kwargs


@pytest.mark.unit
class TestAuxiliaryPreviewRangeHeaderCasing:
    @pytest.mark.parametrize("header_name", ["Range", "range", "RANGE"])
    def test_range_reaches_s3_for_every_casing(self, header_name):
        """A ranged octree read is forwarded to S3 regardless of the header's casing."""
        m = _load()
        mock_s3 = _wire(m)
        event = _rest_event({header_name: "bytes=4096-8191"})

        response = m.lambda_handler(event, MagicMock())

        assert response["statusCode"] == 307
        assert _s3_get_object_kwargs(mock_s3)["Range"] == "bytes=4096-8191"

    def test_audit_entry_records_the_requested_range(self):
        """The audit entry records the range a capitalised-header client asked for."""
        m = _load()
        _wire(m)
        event = _rest_event({"Range": "bytes=4096-8191"})

        m.lambda_handler(event, MagicMock())

        assert m.log_file_download_streamed.called
        assert m.log_file_download_streamed.call_args[0][4]["rangeHeader"] == "bytes=4096-8191"

    def test_unranged_request_sends_no_range_to_s3(self):
        """Positive control: a plain GET must not acquire a Range."""
        m = _load()
        mock_s3 = _wire(m)
        event = _rest_event({"Accept": "*/*"})

        response = m.lambda_handler(event, MagicMock())

        assert response["statusCode"] == 307
        assert "Range" not in _s3_get_object_kwargs(mock_s3)

    def test_null_headers_do_not_break_the_request(self):
        """Positive control: REST can deliver ``headers`` as JSON null."""
        m = _load()
        mock_s3 = _wire(m)
        event = _rest_event(None)

        response = m.lambda_handler(event, MagicMock())

        assert response["statusCode"] == 307
        assert "Range" not in _s3_get_object_kwargs(mock_s3)
