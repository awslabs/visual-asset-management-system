# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""streamAsset: the quarantine-blocks-download guard runs AFTER authorization, on HEAD and GET.

The guard reads the compliance asset-state table and answers 400 "quarantined" when the row says
so. Placed before the Casbin checks, that read turns the endpoint into an oracle: a caller with no
right to the asset learns its compliance state from the status code instead of receiving the
403 every other denied request gets (backend/CLAUDE.md Rule 4 ordering). Both methods are covered
because the handler carries two independent copies of the flow.

The guard itself is the shared ``common.compliance.quarantineGuard`` module. The handler binds
its ``check_quarantine_block`` at import, so the tests patch the GUARD module's globals rather
than the handler's -- and assert that identity first, since patching a second module object
that shares the source file would leave the handler reading the unpatched one.
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

_STREAM_ASSET_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "assets", "streamAsset.py"
)

_DB = "db1"
_ASSET = "asset1"

_cached_module = None


def _load():
    """Load the real streamAsset module by file path with boto3 stubbed (mirrors the range test)."""
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
                "streamAsset_quarantine_under_test", os.path.abspath(_STREAM_ASSET_PATH)
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
    path = f"/database/{_DB}/assets/{_ASSET}/download/stream/scans/pump.glb"
    return {
        "resource": "/database/{databaseId}/assets/{assetId}/download/stream/{proxy+}",
        "path": path,
        "httpMethod": method,
        "headers": {"Accept": "*/*"},
        "pathParameters": {"databaseId": _DB, "assetId": _ASSET, "proxy": "scans/pump.glb"},
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

    Returns (s3 mock, enforcer mock). The enforcer decides both tiers independently so a test can
    deny at Tier 1 or Tier 2 and assert what each denial reaches.
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
    m.get_default_bucket_details = MagicMock(return_value={
        "bucketId": "bucket-1", "bucketName": "test-bucket", "baseAssetsPrefix": "",
    })
    m.validateUnallowedFileExtensionAndContentType = MagicMock(return_value=True)
    m.resolve_file_version_from_asset_version = MagicMock(return_value=None)
    m.resolve_asset_version_id_from_alias = MagicMock(return_value=None)
    m.log_file_download_streamed = MagicMock()

    body = MagicMock()
    body.read.return_value = b"x" * 32
    mock_s3 = MagicMock()
    mock_s3.head_object.return_value = {"ContentType": "model/gltf-binary", "ContentLength": 1024}
    mock_s3.get_object.return_value = {
        "ContentType": "model/gltf-binary",
        "ContentLength": 1024,
        "Body": body,
        "ResponseMetadata": {"HTTPHeaders": {
            "accept-ranges": "bytes",
            "content-type": "model/gltf-binary",
            "content-length": "1024",
        }},
    }
    mock_s3.generate_presigned_url.return_value = "https://example-bucket.s3.amazonaws.com/signed"
    m.s3_client = mock_s3
    return mock_s3, enforcer


def _state_table(item):
    """A compliance asset-state table stub answering one row (or no row when ``item`` is None)."""
    table = MagicMock(name="compliance_asset_state_table")
    table.get_item.return_value = {"Item": item} if item is not None else {}
    return table


def _quarantined(exception_granted=False):
    row = {"databaseId": _DB, "assetId": _ASSET, "complianceState": "quarantined"}
    if exception_granted:
        row["exceptionGranted"] = True
    return row


def _guard_enabled(table):
    """The guard as a deployment with ``quarantineBlocksDownload: true`` loads it."""
    return patch.multiple(guard, quarantine_blocks_download=True,
                          compliance_asset_state_table=table)


def _s3_touched(mock_s3):
    return (mock_s3.head_object.called or mock_s3.get_object.called
            or mock_s3.generate_presigned_url.called)


# The success status each method answers once every check has passed: HEAD returns the object's
# metadata, GET redirects to the presigned URL (ALWAYS_REDIRECT_TO_PRESIGNED).
_SUCCESS = {"HEAD": 200, "GET": 307}


@pytest.mark.unit
class TestTheHandlerBindsTheSharedGuard:
    """Harness check: the function the handler calls IS the shared module's, so patching the
    guard module's globals reaches the handler. Fails if a handler-local copy reappears."""

    def test_check_quarantine_block_is_the_shared_modules_function(self):
        m = _load()
        assert m.check_quarantine_block is guard.check_quarantine_block
        assert m.check_quarantine_block.__globals__ is guard.__dict__

    def test_the_handler_carries_no_local_copy(self):
        m = _load()
        assert not hasattr(m, "_check_quarantine_block")
        assert not hasattr(m, "compliance_asset_state_table")


@pytest.mark.unit
@pytest.mark.parametrize("method", ["HEAD", "GET"])
class TestQuarantineGuardRunsAfterAuthorization:
    """Rule 4 ordering, per method: a denied caller gets 403 and the state table is never read."""

    def test_tier2_denied_caller_gets_403_and_the_state_table_is_never_read(self, method):
        """The row says quarantined; a caller the object-level check denies must not learn that."""
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
        """No identity: the fail-closed deny fires and nothing downstream, the guard included."""
        m = _load()
        mock_s3, enforcer = _wire(m, tokens=())
        table = _state_table(_quarantined())

        with _guard_enabled(table):
            response = m.lambda_handler(_rest_event(method), MagicMock())

        assert response["statusCode"] == 403
        table.get_item.assert_not_called()
        enforcer.assert_not_called()
        assert not _s3_touched(mock_s3)

    def test_authorized_caller_on_quarantined_asset_gets_400_and_no_file_access(self, method):
        """The block itself: authorized, quarantined, no exception -> 400 with the shared message,
        and S3 is never consulted (no HEAD, no GET, no presigned URL)."""
        m = _load()
        mock_s3, enforcer = _wire(m)
        table = _state_table(_quarantined())

        with _guard_enabled(table):
            response = m.lambda_handler(_rest_event(method), MagicMock())

        assert response["statusCode"] == 400
        # general_error renders str(VAMSGeneralErrorResponse), which prefixes the message.
        assert json.loads(response["body"])["message"].endswith(guard.QUARANTINE_BLOCK_MESSAGE)
        # The read happened, and only after both authorization tiers passed.
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

    def test_non_quarantined_state_lets_the_request_proceed(self, method):
        m = _load()
        _wire(m)
        table = _state_table({"databaseId": _DB, "assetId": _ASSET, "complianceState": "compliant"})

        with _guard_enabled(table):
            response = m.lambda_handler(_rest_event(method), MagicMock())

        assert response["statusCode"] == _SUCCESS[method]

    def test_asset_without_a_state_row_proceeds(self, method):
        """An asset no compliance schema has evaluated has no row and is not blocked."""
        m = _load()
        _wire(m)
        table = _state_table(None)

        with _guard_enabled(table):
            response = m.lambda_handler(_rest_event(method), MagicMock())

        assert response["statusCode"] == _SUCCESS[method]
        table.get_item.assert_called_once()

    def test_disabled_block_reads_no_state_table(self, method):
        """``quarantineBlocksDownload: false``: the guard is a no-op even with a table bound."""
        m = _load()
        _wire(m)
        table = _state_table(_quarantined())

        with patch.multiple(guard, quarantine_blocks_download=False,
                            compliance_asset_state_table=table):
            response = m.lambda_handler(_rest_event(method), MagicMock())

        assert response["statusCode"] == _SUCCESS[method]
        table.get_item.assert_not_called()
