# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""streamAuxiliaryPreviewAsset: the quarantine-blocks-download guard covers preview streaming.

Auxiliary preview objects (octrees, tiles, thumbnails) are derived from the asset's files, so a
deployment that blocks downloads of a quarantined asset must not hand them out either. The guard
is the shared ``common.compliance.quarantineGuard`` module, called after both authorization tiers
have passed on HEAD and GET -- a caller the Casbin checks deny gets 403 and never triggers the
state-table read.
"""

import importlib.util
import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

import common.compliance.quarantineGuard as guard

os.environ.setdefault("S3_ASSET_AUXILIARY_BUCKET", "test-asset-auxiliary-bucket")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "86400")
os.environ.setdefault("AWS_REGION", "us-east-1")

_MODULE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "assets",
    "streamAuxiliaryPreviewAsset.py",
)

_DB = "db1"
_ASSET = "asset1"
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
                "streamAuxiliaryPreviewAsset_quarantine_under_test", os.path.abspath(_MODULE_PATH)
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


def _rest_event(method):
    """An API Gateway REST (v1) proxy event, the shape the deployed handler receives."""
    path = f"/database/{_DB}/assets/{_ASSET}/auxiliaryPreviewAssets/stream/{AUX_KEY}"
    return {
        "resource": (
            "/database/{databaseId}/assets/{assetId}/auxiliaryPreviewAssets/stream/{proxy+}"
        ),
        "path": path,
        "httpMethod": method,
        "headers": {"Accept": "*/*"},
        "pathParameters": {"databaseId": _DB, "assetId": _ASSET, "proxy": AUX_KEY},
        "queryStringParameters": None,
        "requestContext": {
            "identity": {"sourceIp": "10.0.0.7"},
            "path": path,
            "httpMethod": method,
        },
        "body": None,
    }


def _wire(m, tokens=("test-user",), api_allowed=True, object_allowed=True):
    """Point the module at a mocked asset/S3 context and a scripted enforcer.

    Returns (s3 mock, enforcer mock).
    """
    from common.auth.apiEvent import normalize_event

    def _claims(event):
        normalize_event(event)
        return {"tokens": list(tokens), "roles": [], "mfaEnabled": False}

    m.request_to_claims = MagicMock(side_effect=_claims)
    enforcer = MagicMock()
    enforcer.return_value.enforceAPI.return_value = api_allowed
    enforcer.return_value.enforce.return_value = object_allowed
    m.CasbinEnforcer = enforcer
    m.get_asset_details = MagicMock(return_value={
        "databaseId": _DB, "assetId": _ASSET, "isDistributable": True,
        "bucketId": "bucket-1", "assetLocation": {"Key": f"{_ASSET}/"},
    })
    m.validateUnallowedFileExtensionAndContentType = MagicMock(return_value=True)
    m.log_file_download_streamed = MagicMock()

    body = MagicMock()
    body.read.return_value = b"octree-bytes"
    mock_s3 = MagicMock()
    mock_s3.head_object.return_value = {
        "ContentType": "application/octet-stream", "ContentLength": 2048,
    }
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
    return mock_s3, enforcer


def _state_table(item):
    table = MagicMock(name="compliance_asset_state_table")
    table.get_item.return_value = {"Item": item} if item is not None else {}
    return table


def _quarantined(exception_granted=False):
    row = {"databaseId": _DB, "assetId": _ASSET, "complianceState": "quarantined"}
    if exception_granted:
        row["exceptionGranted"] = True
    return row


def _guard_enabled(table):
    return patch.multiple(guard, quarantine_blocks_download=True,
                          compliance_asset_state_table=table)


def _s3_touched(mock_s3):
    return (mock_s3.head_object.called or mock_s3.get_object.called
            or mock_s3.generate_presigned_url.called)


_SUCCESS = {"HEAD": 200, "GET": 307}


@pytest.mark.unit
class TestTheHandlerBindsTheSharedGuard:
    def test_check_quarantine_block_is_the_shared_modules_function(self):
        m = _load()
        assert m.check_quarantine_block is guard.check_quarantine_block
        assert m.check_quarantine_block.__globals__ is guard.__dict__


@pytest.mark.unit
@pytest.mark.parametrize("method", ["HEAD", "GET"])
class TestPreviewStreamingIsQuarantineGuardedAfterAuthorization:

    def test_tier2_denied_caller_gets_403_and_the_state_table_is_never_read(self, method):
        m = _load()
        mock_s3, enforcer = _wire(m, object_allowed=False)
        table = _state_table(_quarantined())

        with _guard_enabled(table):
            response = m.lambda_handler(_rest_event(method), MagicMock())

        assert response["statusCode"] == 403
        assert "quarantin" not in response["body"].lower()
        table.get_item.assert_not_called()
        enforcer.return_value.enforce.assert_called_once()
        assert not _s3_touched(mock_s3)

    def test_tier1_denied_caller_gets_403_and_the_state_table_is_never_read(self, method):
        m = _load()
        mock_s3, _enforcer = _wire(m, api_allowed=False)
        table = _state_table(_quarantined())

        with _guard_enabled(table):
            response = m.lambda_handler(_rest_event(method), MagicMock())

        assert response["statusCode"] == 403
        table.get_item.assert_not_called()
        assert not _s3_touched(mock_s3)

    def test_empty_token_list_is_denied_before_the_state_table_is_read(self, method):
        m = _load()
        mock_s3, enforcer = _wire(m, tokens=())
        table = _state_table(_quarantined())

        with _guard_enabled(table):
            response = m.lambda_handler(_rest_event(method), MagicMock())

        assert response["statusCode"] == 403
        table.get_item.assert_not_called()
        enforcer.assert_not_called()
        assert not _s3_touched(mock_s3)

    def test_authorized_caller_on_quarantined_asset_gets_400_and_no_preview_bytes(self, method):
        """Authorized, quarantined, no exception -> 400 with the shared message; no S3 HEAD/GET
        and no presigned URL, so nothing derived from the asset leaves the deployment."""
        m = _load()
        mock_s3, enforcer = _wire(m)
        table = _state_table(_quarantined())

        with _guard_enabled(table):
            response = m.lambda_handler(_rest_event(method), MagicMock())

        assert response["statusCode"] == 400
        assert json.loads(response["body"])["message"].endswith(guard.QUARANTINE_BLOCK_MESSAGE)
        table.get_item.assert_called_once_with(Key={"databaseId": _DB, "assetId": _ASSET})
        enforcer.return_value.enforceAPI.assert_called_once()
        enforcer.return_value.enforce.assert_called_once()
        assert not _s3_touched(mock_s3)
        m.log_file_download_streamed.assert_not_called()

    def test_granted_exception_lets_the_request_proceed(self, method):
        m = _load()
        mock_s3, _enforcer = _wire(m)
        table = _state_table(_quarantined(exception_granted=True))

        with _guard_enabled(table):
            response = m.lambda_handler(_rest_event(method), MagicMock())

        assert response["statusCode"] == _SUCCESS[method]
        table.get_item.assert_called_once()
        assert _s3_touched(mock_s3)

    def test_asset_without_a_state_row_proceeds(self, method):
        m = _load()
        _wire(m)
        table = _state_table(None)

        with _guard_enabled(table):
            response = m.lambda_handler(_rest_event(method), MagicMock())

        assert response["statusCode"] == _SUCCESS[method]
        table.get_item.assert_called_once()

    def test_disabled_block_reads_no_state_table(self, method):
        m = _load()
        _wire(m)
        table = _state_table(_quarantined())

        with patch.multiple(guard, quarantine_blocks_download=False,
                            compliance_asset_state_table=table):
            response = m.lambda_handler(_rest_event(method), MagicMock())

        assert response["statusCode"] == _SUCCESS[method]
        table.get_item.assert_not_called()
