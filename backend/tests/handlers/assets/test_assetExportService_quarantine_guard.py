# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""assetExportService: the quarantine-blocks-download guard covers presigned export URLs.

An export with ``generatePresignedUrls`` mints a presigned GET URL for every file of every asset
on the page, which is a download by another route. The shared ``common.compliance.quarantineGuard``
runs once per asset in ``process_asset_batch``, after that asset's Tier-2 check and before any
file is listed or signed, so:

- a caller the Casbin check denies gets the unauthorized marker and never triggers the
  state-table read (no compliance-state oracle through the export route);
- an authorized caller exporting a quarantined asset without a granted exception gets 400 and
  no presigned URL is produced for any asset on the page;
- an export that mints no file URLs delivers no file bytes and is not blocked.
"""

import importlib.util
import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

import common.compliance.quarantineGuard as guard

os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_VERSIONS_STORAGE_TABLE_NAME", "test-asset-versions-table")
os.environ.setdefault("ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME", "test-asset-file-versions-table")
os.environ.setdefault("ASSET_FILE_METADATA_STORAGE_TABLE_NAME", "test-asset-file-metadata-table")
os.environ.setdefault("FILE_ATTRIBUTE_STORAGE_TABLE_NAME", "test-file-attribute-table")
os.environ.setdefault("ASSET_LINKS_STORAGE_TABLE_V2_NAME", "test-asset-links-v2-table")
os.environ.setdefault("ASSET_LINKS_METADATA_STORAGE_TABLE_NAME", "test-asset-links-metadata-table")
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("S3_ASSET_AUXILIARY_BUCKET", "test-asset-auxiliary-bucket")
os.environ.setdefault("ASSET_LINKS_FUNCTION_NAME", "test-asset-links-function")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "3600")
os.environ.setdefault("AWS_REGION", "us-east-1")

_ASSET_EXPORT_SOURCE = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "assets",
    "assetExportService.py"
)

_DB = "db1"
_ASSET = "asset-1"
_OTHER_ASSET = "asset-2"
_EXPORT_PATH = f"/database/{_DB}/assets/{_ASSET}/export"
_SIGNED_URL = "https://bucket-name.s3.amazonaws.com/signed"

_cached_module = None


def _load_real_common_dynamodb():
    path = os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "backend", "common", "dynamodb.py"
    )
    spec = importlib.util.spec_from_file_location(
        "real_common_dynamodb_for_assetexport_quarantine", os.path.abspath(path)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load():
    """Load the real assetExportService module by file path with boto3 stubbed."""
    global _cached_module
    if _cached_module is not None:
        return _cached_module

    stub_names = ("handlers.authz", "handlers.auth")
    saved = {name: sys.modules.get(name) for name in stub_names}
    authz_stub = types.ModuleType("handlers.authz")
    authz_stub.CasbinEnforcer = MagicMock()
    sys.modules["handlers.authz"] = authz_stub
    auth_stub = types.ModuleType("handlers.auth")
    auth_stub.request_to_claims = MagicMock(return_value={"tokens": ["tester"], "roles": []})
    sys.modules["handlers.auth"] = auth_stub

    dynamodb_mod = sys.modules.get("common.dynamodb")
    added_attrs = []
    if dynamodb_mod is not None:
        real_dynamodb = _load_real_common_dynamodb()
        for attr in ("query_all_items",):
            if not hasattr(dynamodb_mod, attr):
                setattr(dynamodb_mod, attr, getattr(real_dynamodb, attr))
                added_attrs.append(attr)

    try:
        with patch("boto3.client", return_value=MagicMock()), patch(
            "boto3.resource", return_value=MagicMock()
        ):
            spec = importlib.util.spec_from_file_location(
                "assetExportService_quarantine_under_test", os.path.abspath(_ASSET_EXPORT_SOURCE)
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
            else:
                sys.modules.pop(name, None)
        for attr in added_attrs:
            delattr(dynamodb_mod, attr)
    _cached_module = module
    return module


def _asset(asset_id=_ASSET):
    return {
        "databaseId": _DB, "assetId": asset_id, "assetName": "N", "isDistributable": True,
        "bucketId": "bucket-1", "assetLocation": {"Key": f"{asset_id}/"},
        "currentVersionId": "1",
    }


def _one_file(asset_id=_ASSET):
    return [{
        "fileName": "model.glb", "key": f"{asset_id}/model.glb", "relativePath": "/model.glb",
        "isFolder": False, "dateCreatedCurrentVersion": "2026-01-01T00:00:00",
        "storageClass": "STANDARD", "versionId": "v1", "isArchived": False,
        "primaryType": None, "size": 10,
    }]


def _enforcer(api_allowed=True, object_allowed=True):
    enforcer = MagicMock()
    enforcer.return_value.enforceAPI.return_value = api_allowed
    enforcer.return_value.enforce.return_value = object_allowed
    return enforcer


def _state_table(item):
    table = MagicMock(name="compliance_asset_state_table")
    table.get_item.return_value = {"Item": item} if item is not None else {}
    return table


def _quarantined(asset_id=_ASSET, exception_granted=False):
    row = {"databaseId": _DB, "assetId": asset_id, "complianceState": "quarantined"}
    if exception_granted:
        row["exceptionGranted"] = True
    return row


def _guard_enabled(table):
    return patch.multiple(guard, quarantine_blocks_download=True,
                          compliance_asset_state_table=table)


def _export_io_patches(m, identifiers):
    """Stub every read an authorized asset triggers; the authorization filter and the guard call
    stay the module's own code. Returns (patches, spies)."""
    details = {f"{a['databaseId']}:{a['assetId']}": _asset(a["assetId"]) for a in identifiers}
    spies = {
        "files": MagicMock(side_effect=lambda bucket, prefix, **kw: _one_file(prefix.rstrip("/"))),
        "sign": MagicMock(return_value=_SIGNED_URL),
    }
    patches = [
        patch.object(m, "batch_get_assets", MagicMock(return_value=details)),
        patch.object(m, "get_default_bucket_details", MagicMock(return_value={
            "bucketId": "bucket-1", "bucketName": "bucket-name", "baseAssetsPrefix": "prefix/"})),
        patch.object(m, "list_s3_files", spies["files"]),
        patch.object(m, "generate_presigned_url", spies["sign"]),
        patch.object(m, "enrich_files_with_primary_type", MagicMock()),
        patch.object(m, "get_asset_metadata", MagicMock(return_value={})),
        patch.object(m, "get_asset_version_info", MagicMock(return_value=None)),
        patch.object(m, "get_asset_file_versions", MagicMock(return_value=None)),
        patch.object(m, "prefetch_file_metadata", MagicMock(return_value={})),
        patch.object(m, "prefetch_file_attributes", MagicMock(return_value={})),
        patch.object(m, "get_file_metadata", MagicMock(return_value={})),
        patch.object(m, "get_file_attributes", MagicMock(return_value={})),
    ]
    return patches, spies


def _run_batch(identifiers, claims_and_roles, enforcer, request_model, table, enabled=True):
    """Run process_asset_batch offline with the guard bound to ``table``. Returns (result, spies)."""
    m = _load()
    patches, spies = _export_io_patches(m, identifiers)
    patches.append(patch.object(m, "CasbinEnforcer", enforcer))
    patches.append(patch.multiple(guard, quarantine_blocks_download=enabled,
                                  compliance_asset_state_table=table))
    for p in patches:
        p.start()
    try:
        result, _page_state = m.process_asset_batch(identifiers, request_model, claims_and_roles)
    finally:
        for p in reversed(patches):
            p.stop()
    return result, spies


def _root():
    return [{"databaseId": _DB, "assetId": _ASSET, "isRoot": True}]


@pytest.mark.unit
class TestTheHandlerBindsTheSharedGuard:
    def test_check_quarantine_block_is_the_shared_modules_function(self):
        m = _load()
        assert m.check_quarantine_block is guard.check_quarantine_block
        assert m.check_quarantine_block.__globals__ is guard.__dict__


@pytest.mark.unit
class TestProcessAssetBatchQuarantineChokePoint:
    """The one place every exported asset passes after its Tier-2 check and before its files are
    listed or signed."""

    def test_quarantined_asset_fails_the_page_after_tier2_and_before_any_file_is_listed_or_signed(self):
        m = _load()
        enforcer = _enforcer()
        table = _state_table(_quarantined())
        request = m.AssetExportRequestModel(generatePresignedUrls=True)

        with pytest.raises(m.VAMSGeneralErrorResponse) as raised:
            _run_batch(_root(), {"tokens": ["alice"]}, enforcer, request, table)

        assert str(raised.value).endswith(guard.QUARANTINE_BLOCK_MESSAGE)
        # Tier 2 ran first; the state read is keyed on the asset it authorized.
        enforcer.return_value.enforce.assert_called_once()
        table.get_item.assert_called_once_with(Key={"databaseId": _DB, "assetId": _ASSET})

    def test_quarantined_asset_signs_and_lists_nothing(self):
        m = _load()
        table = _state_table(_quarantined())
        request = m.AssetExportRequestModel(generatePresignedUrls=True)
        patches, spies = _export_io_patches(m, _root())
        patches.append(patch.object(m, "CasbinEnforcer", _enforcer()))
        patches.append(_guard_enabled(table))
        for p in patches:
            p.start()
        try:
            with pytest.raises(m.VAMSGeneralErrorResponse):
                m.process_asset_batch(_root(), request, {"tokens": ["alice"]})
        finally:
            for p in reversed(patches):
                p.stop()

        spies["files"].assert_not_called()
        spies["sign"].assert_not_called()

    def test_tier2_denied_asset_never_reaches_the_state_table(self):
        """Rule 4 ordering: a denied asset is marked unauthorized and its compliance state is
        never read, so the export route cannot be used to probe quarantine status."""
        m = _load()
        table = _state_table(_quarantined())
        request = m.AssetExportRequestModel(generatePresignedUrls=True)

        result, spies = _run_batch(
            _root(), {"tokens": ["alice"]}, _enforcer(object_allowed=False), request, table)

        assert result == [{"assetId": _ASSET, "databaseId": _DB, "unauthorizedAsset": True}]
        table.get_item.assert_not_called()
        spies["sign"].assert_not_called()

    def test_empty_token_list_never_reaches_the_state_table(self):
        m = _load()
        table = _state_table(_quarantined())
        request = m.AssetExportRequestModel(generatePresignedUrls=True)
        enforcer = _enforcer()

        result, spies = _run_batch(_root(), {"tokens": []}, enforcer, request, table)

        assert result[0]["unauthorizedAsset"] is True
        enforcer.assert_not_called()
        table.get_item.assert_not_called()
        spies["sign"].assert_not_called()

    def test_granted_exception_exports_with_presigned_urls(self):
        m = _load()
        table = _state_table(_quarantined(exception_granted=True))
        request = m.AssetExportRequestModel(generatePresignedUrls=True)

        result, spies = _run_batch(_root(), {"tokens": ["alice"]}, _enforcer(), request, table)

        assert len(result) == 1 and "unauthorizedAsset" not in result[0]
        assert result[0]["files"][0]["presignedFileDownloadUrl"] == _SIGNED_URL
        table.get_item.assert_called_once()
        spies["sign"].assert_called_once()

    def test_asset_without_a_state_row_exports_with_presigned_urls(self):
        m = _load()
        table = _state_table(None)
        request = m.AssetExportRequestModel(generatePresignedUrls=True)

        result, spies = _run_batch(_root(), {"tokens": ["alice"]}, _enforcer(), request, table)

        assert result[0]["files"][0]["presignedFileDownloadUrl"] == _SIGNED_URL
        table.get_item.assert_called_once()

    def test_export_without_presigned_urls_delivers_no_bytes_and_is_not_blocked(self):
        """The block guards downloads. A metadata/listing export of a quarantined asset mints
        no file URL, so it proceeds and does not read the state table."""
        m = _load()
        table = _state_table(_quarantined())
        request = m.AssetExportRequestModel(generatePresignedUrls=False)

        result, spies = _run_batch(_root(), {"tokens": ["alice"]}, _enforcer(), request, table)

        assert len(result) == 1 and "unauthorizedAsset" not in result[0]
        assert result[0]["files"][0]["presignedFileDownloadUrl"] is None
        table.get_item.assert_not_called()
        spies["sign"].assert_not_called()

    def test_disabled_block_reads_no_state_table(self):
        """``quarantineBlocksDownload: false``: presigned export proceeds with no state read."""
        m = _load()
        table = _state_table(_quarantined())
        request = m.AssetExportRequestModel(generatePresignedUrls=True)

        result, spies = _run_batch(
            _root(), {"tokens": ["alice"]}, _enforcer(), request, table, enabled=False)

        assert result[0]["files"][0]["presignedFileDownloadUrl"] == _SIGNED_URL
        table.get_item.assert_not_called()

    def test_the_check_is_per_asset_and_only_for_authorized_assets(self):
        """Two assets on the page, one denied: the state table is read for the authorized asset
        only, keyed on that asset."""
        m = _load()
        identifiers = _root() + [{"databaseId": _DB, "assetId": _OTHER_ASSET, "isRoot": False}]
        table = _state_table(None)
        request = m.AssetExportRequestModel(generatePresignedUrls=True)
        enforcer = MagicMock()
        enforcer.return_value.enforceAPI.return_value = True
        enforcer.return_value.enforce.side_effect = (
            lambda asset, action: asset["assetId"] == _OTHER_ASSET)

        result, _ = _run_batch(identifiers, {"tokens": ["alice"]}, enforcer, request, table)

        table.get_item.assert_called_once_with(Key={"databaseId": _DB, "assetId": _OTHER_ASSET})
        by_id = {entry.get("assetid", entry.get("assetId")): entry for entry in result}
        assert by_id[_ASSET]["unauthorizedAsset"] is True
        assert "unauthorizedAsset" not in by_id[_OTHER_ASSET]


def _rest_event(body):
    return {
        "path": _EXPORT_PATH,
        "httpMethod": "POST",
        "requestContext": {"identity": {"sourceIp": "10.0.0.1"}},
        "pathParameters": {"databaseId": _DB, "assetId": _ASSET},
        "queryStringParameters": None,
        "headers": {},
        "body": json.dumps(body),
    }


def _invoke_export(body, table, enforcer=None, enabled=True):
    """POST /export through lambda_handler with the real Tier-1, Tier-2 and batch code, the
    per-asset I/O stubbed and the guard bound to ``table``. Returns (response, spies)."""
    m = _load()
    from common.auth.apiEvent import normalize_event

    def _claims(event):
        normalize_event(event)
        return {"tokens": ["alice"], "roles": [], "mfaEnabled": False}

    asset_table = MagicMock()
    asset_table.get_item.return_value = {"Item": _asset()}
    patches, spies = _export_io_patches(m, _root())
    patches += [
        patch.object(m, "request_to_claims", MagicMock(side_effect=_claims)),
        patch.object(m, "CasbinEnforcer", enforcer or _enforcer()),
        patch.object(m, "asset_table", asset_table),
        patch.multiple(guard, quarantine_blocks_download=enabled,
                       compliance_asset_state_table=table),
    ]
    for p in patches:
        p.start()
    try:
        response = m.lambda_handler(_rest_event(body), MagicMock())
    finally:
        for p in reversed(patches):
            p.stop()
    return response, spies


_SINGLE_ASSET_PRESIGNED = {"generatePresignedUrls": True, "fetchAssetRelationships": False}


@pytest.mark.unit
class TestExportRouteQuarantineBlock:
    """End to end through lambda_handler: the status code and body the client sees."""

    def test_quarantined_asset_export_is_400_with_no_presigned_url_in_the_body(self):
        table = _state_table(_quarantined())

        response, spies = _invoke_export(_SINGLE_ASSET_PRESIGNED, table)

        assert response["statusCode"] == 400
        assert json.loads(response["body"])["message"].endswith(guard.QUARANTINE_BLOCK_MESSAGE)
        assert _SIGNED_URL not in response["body"]
        spies["sign"].assert_not_called()
        table.get_item.assert_called_once_with(Key={"databaseId": _DB, "assetId": _ASSET})

    def test_tier2_denied_export_is_400_without_a_state_table_read(self):
        """The root-asset Tier-2 gate denies with a generic 400 before the batch runs, so the
        quarantined row is never read and the response carries no compliance wording."""
        table = _state_table(_quarantined())

        response, spies = _invoke_export(
            _SINGLE_ASSET_PRESIGNED, table, enforcer=_enforcer(object_allowed=False))

        assert response["statusCode"] == 400
        assert "quarantin" not in response["body"].lower()
        table.get_item.assert_not_called()
        spies["sign"].assert_not_called()

    def test_granted_exception_exports_a_presigned_url(self):
        table = _state_table(_quarantined(exception_granted=True))

        response, spies = _invoke_export(_SINGLE_ASSET_PRESIGNED, table)

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["assets"][0]["files"][0]["presignedFileDownloadUrl"] == _SIGNED_URL
        spies["sign"].assert_called_once()

    def test_disabled_block_exports_a_presigned_url_without_a_state_table_read(self):
        table = _state_table(_quarantined())

        response, _spies = _invoke_export(_SINGLE_ASSET_PRESIGNED, table, enabled=False)

        assert response["statusCode"] == 200
        assert _SIGNED_URL in response["body"]
        table.get_item.assert_not_called()
