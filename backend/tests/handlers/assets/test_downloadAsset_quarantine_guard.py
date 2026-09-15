# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""downloadAsset: the quarantine-blocks-download guard is the shared module, still post-authz.

downloadAsset always evaluated the block after its Casbin checks; what changes is that the check
is the shared ``common.compliance.quarantineGuard`` rather than a handler-local copy with its own
table bootstrap. These tests pin that binding and the ordering it must keep: a denied caller gets
403 with no state-table read, an authorized caller on a quarantined asset gets 400 with the shared
message and no presigned URL.
"""

import importlib.util
import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

import common.compliance.quarantineGuard as guard

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "86400")
os.environ.setdefault("AWS_REGION", "us-east-1")

_DOWNLOAD_ASSET_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "assets", "downloadAsset.py"
)

_DB = "db1"
_ASSET = "asset1"
_SIGNED_URL = "https://signed.example/asset1/model.glb"

_cached_module = None


def _load():
    """Load the real downloadAsset module by file path with boto3 stubbed."""
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
                "downloadAsset_quarantine_under_test", os.path.abspath(_DOWNLOAD_ASSET_PATH)
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


def _event():
    return {
        "path": f"/database/{_DB}/assets/{_ASSET}/download",
        "httpMethod": "POST",
        "requestContext": {"identity": {"sourceIp": "10.0.0.1"}},
        "pathParameters": {"databaseId": _DB, "assetId": _ASSET},
        "queryStringParameters": None,
        "headers": {},
        "body": json.dumps({"downloadType": "assetFile", "key": "/model.glb"}),
    }


def _wire(m, tokens=("test-user",), api_allowed=True, object_allowed=True):
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
    m.get_default_bucket_details = MagicMock(return_value={
        "bucketId": "bucket-1", "bucketName": "test-bucket", "baseAssetsPrefix": "",
    })
    m.validateUnallowedFileExtensionAndContentType = MagicMock(return_value=True)
    m.log_file_download = MagicMock()
    m.log_file_download_bulk = MagicMock()
    m.s3 = MagicMock()
    m.s3.head_object.return_value = {"ContentType": "model/gltf-binary", "ContentLength": 10}
    m.s3.generate_presigned_url.return_value = _SIGNED_URL
    return m.s3, enforcer


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


@pytest.mark.unit
class TestTheHandlerBindsTheSharedGuard:
    def test_check_quarantine_block_is_the_shared_modules_function(self):
        m = _load()
        assert m.check_quarantine_block is guard.check_quarantine_block
        assert m.check_quarantine_block.__globals__ is guard.__dict__

    def test_the_handler_carries_no_local_copy(self):
        m = _load()
        assert not hasattr(m, "_check_quarantine_block")
        assert not hasattr(m, "compliance_asset_state_table")


@pytest.mark.unit
class TestDownloadQuarantineBlockAfterAuthorization:

    def test_tier2_denied_caller_gets_403_and_the_state_table_is_never_read(self):
        m = _load()
        s3, enforcer = _wire(m, object_allowed=False)
        table = _state_table(_quarantined())

        with _guard_enabled(table):
            response = m.lambda_handler(_event(), MagicMock())

        assert response["statusCode"] == 403
        assert "quarantin" not in response["body"].lower()
        table.get_item.assert_not_called()
        enforcer.return_value.enforce.assert_called_once()
        s3.generate_presigned_url.assert_not_called()

    def test_empty_token_list_is_denied_before_the_state_table_is_read(self):
        m = _load()
        s3, enforcer = _wire(m, tokens=())
        table = _state_table(_quarantined())

        with _guard_enabled(table):
            response = m.lambda_handler(_event(), MagicMock())

        assert response["statusCode"] == 403
        table.get_item.assert_not_called()
        enforcer.assert_not_called()
        s3.generate_presigned_url.assert_not_called()

    def test_authorized_caller_on_quarantined_asset_gets_400_and_no_presigned_url(self):
        m = _load()
        s3, enforcer = _wire(m)
        table = _state_table(_quarantined())

        with _guard_enabled(table):
            response = m.lambda_handler(_event(), MagicMock())

        assert response["statusCode"] == 400
        assert json.loads(response["body"])["message"].endswith(guard.QUARANTINE_BLOCK_MESSAGE)
        assert _SIGNED_URL not in response["body"]
        table.get_item.assert_called_once_with(Key={"databaseId": _DB, "assetId": _ASSET})
        enforcer.return_value.enforce.assert_called_once()
        s3.generate_presigned_url.assert_not_called()
        m.log_file_download.assert_not_called()

    def test_granted_exception_returns_a_presigned_url(self):
        m = _load()
        s3, _enforcer = _wire(m)
        table = _state_table(_quarantined(exception_granted=True))

        with _guard_enabled(table):
            response = m.lambda_handler(_event(), MagicMock())

        assert response["statusCode"] == 200
        assert json.loads(response["body"])["downloadUrl"] == _SIGNED_URL
        table.get_item.assert_called_once()
        s3.generate_presigned_url.assert_called_once()

    def test_disabled_block_reads_no_state_table(self):
        m = _load()
        _wire(m)
        table = _state_table(_quarantined())

        with patch.multiple(guard, quarantine_blocks_download=False,
                            compliance_asset_state_table=table):
            response = m.lambda_handler(_event(), MagicMock())

        assert response["statusCode"] == 200
        table.get_item.assert_not_called()
