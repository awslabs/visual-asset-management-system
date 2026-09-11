# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests that assetService writes asset lifecycle history records on
edit, archive, unarchive, and permanent delete."""

import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("DATABASE_STORAGE_TABLE_NAME", "test-db-table")
os.environ.setdefault("S3_ASSET_AUXILIARY_BUCKET", "test-aux-bucket")
os.environ.setdefault("SUBSCRIPTIONS_STORAGE_TABLE_NAME", "test-subs-table")
os.environ.setdefault("SEND_EMAIL_FUNCTION_NAME", "test-email-fn")
os.environ.setdefault("ASSET_FILE_VERSION_HISTORY_STORAGE_TABLE_NAME", "test-history-table")
os.environ.setdefault("ASSET_HISTORY_STORAGE_TABLE_NAME", "test-asset-history-table")
os.environ.setdefault("ASSET_UPLOAD_TABLE_NAME", "test-upload-table")
os.environ.setdefault("ASSET_LINKS_STORAGE_TABLE_NAME", "test-links-table")
os.environ.setdefault("ASSET_LINKS_METADATA_STORAGE_TABLE_NAME", "test-links-meta-table")
os.environ.setdefault("ASSET_FILE_METADATA_STORAGE_TABLE_NAME", "test-file-meta-table")
os.environ.setdefault("FILE_ATTRIBUTE_STORAGE_TABLE_NAME", "test-file-attr-table")
os.environ.setdefault("ASSET_VERSIONS_STORAGE_TABLE_NAME", "test-versions-table")
os.environ.setdefault("ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME", "test-file-versions-table")
os.environ.setdefault("ASSET_FILE_METADATA_VERSIONS_STORAGE_TABLE_NAME", "test-file-meta-versions-table")
os.environ.setdefault("COMMENT_STORAGE_TABLE_NAME", "test-comment-table")

_ASSET_SERVICE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "assets", "assetService.py"
)

_cached_module = None


def _load():
    """Load the real assetService module by file path with boto3 stubbed."""
    global _cached_module
    if _cached_module is not None:
        return _cached_module

    stub_names = (
        "handlers.assets.assetCount", "handlers.assets.assetFiles",
        "handlers.authz", "handlers.auth",
    )
    saved = {name: sys.modules.get(name) for name in stub_names}

    count_stub = types.ModuleType("handlers.assets.assetCount")
    count_stub.update_asset_count = MagicMock()
    sys.modules["handlers.assets.assetCount"] = count_stub

    files_stub = types.ModuleType("handlers.assets.assetFiles")
    files_stub.delete_s3_prefix_all_versions = MagicMock()
    files_stub.aux_bucket_asset_file_base = (
        lambda db, key: f"{(db or '').strip('/')}/{(key or '').strip('/')}/"
    )
    sys.modules["handlers.assets.assetFiles"] = files_stub

    authz_stub = types.ModuleType("handlers.authz")
    authz_stub.CasbinEnforcer = MagicMock()
    sys.modules["handlers.authz"] = authz_stub

    auth_stub = types.ModuleType("handlers.auth")
    auth_stub.request_to_claims = MagicMock(return_value={"tokens": ["tester"]})
    sys.modules["handlers.auth"] = auth_stub

    # The mock common.dynamodb module lacks validate_pagination_info that
    # assetService imports; add it for the load.
    dynamodb_mod = sys.modules.get("common.dynamodb")
    added_attrs = []
    if dynamodb_mod is not None and not hasattr(dynamodb_mod, "validate_pagination_info"):
        dynamodb_mod.validate_pagination_info = MagicMock()
        added_attrs.append("validate_pagination_info")

    try:
        with patch("boto3.client", return_value=MagicMock()), patch(
            "boto3.resource", return_value=MagicMock()
        ):
            spec = importlib.util.spec_from_file_location(
                "assetService_history_under_test", os.path.abspath(_ASSET_SERVICE_PATH)
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
            if dynamodb_mod is not None:
                delattr(dynamodb_mod, attr)
    _cached_module = module
    return module


def _asset(db="db1", aid="a1", **extra):
    asset = {
        "databaseId": db, "assetId": aid, "assetName": "N1",
        "description": "d1", "isDistributable": True, "tags": [],
        "bucketId": "b1", "assetLocation": {"Key": f"{aid}/"},
    }
    asset.update(extra)
    return asset


@pytest.mark.unit
class TestAssetServiceHistoryHooks:
    def _prepare(self, m, asset):
        m.asset_table = MagicMock()
        m.asset_table.get_item.return_value = {"Item": dict(asset)}
        m.write_asset_history_record = MagicMock()
        m.send_subscription_email = MagicMock()
        m.get_asset_bucket_details = MagicMock(return_value={"bucketName": "bucket"})
        m.archive_multi_assetFiles = MagicMock()
        m.archive_file_preview = MagicMock()
        m.unarchive_multi_assetFiles = MagicMock(return_value=0)
        m.unarchive_file_preview = MagicMock()
        m.update_asset_count = MagicMock()

    def test_update_asset_writes_edit_record(self):
        m = _load()
        asset = _asset()
        self._prepare(m, asset)
        m.claims_and_roles = {"tokens": ["u1"]}
        m.update_asset("db1", "a1", {"assetName": "N2"}, {"tokens": ["u1"]})

        m.write_asset_history_record.assert_called_once()
        args = m.write_asset_history_record.call_args[0]
        assert args[:2] == ("db1", "a1")
        assert args[2] == m.CHANGE_SOURCE_EDIT
        assert args[3] == "u1"
        assert args[4]["assetName"] == "N2"  # post-update value

    def test_archive_asset_writes_archive_record_with_reason(self):
        m = _load()
        asset = _asset()
        self._prepare(m, asset)
        request = MagicMock()
        request.reason = "cleanup"
        m.archive_asset("db1", "a1", request, {"tokens": ["u1"]})

        args = m.write_asset_history_record.call_args[0]
        assert args[0] == "db1"  # original databaseId, not db1#deleted
        assert args[2] == m.CHANGE_SOURCE_ARCHIVE
        assert args[3] == "u1"
        assert args[4]["archivedReason"] == "cleanup"

    def test_unarchive_asset_writes_unarchive_record(self):
        m = _load()
        asset = _asset(db="db1#deleted", status="archived")
        self._prepare(m, asset)
        request = MagicMock()
        request.reason = "restore"
        request.unarchiveFiles = False
        m.unarchive_asset("db1", "a1", request, {"tokens": ["u1"]})

        args = m.write_asset_history_record.call_args[0]
        assert args[0] == "db1"
        assert args[2] == m.CHANGE_SOURCE_UNARCHIVE
        assert args[4]["unarchivedReason"] == "restore"

    def test_permanent_delete_writes_record_and_never_deletes_history(self):
        m = _load()
        asset = _asset()
        self._prepare(m, asset)
        m.claims_and_roles = {"tokens": ["u1"]}
        m.delete_s3_prefix_all_versions = MagicMock(return_value=[])
        m.delete_assetAuxiliary_files = MagicMock()
        m.delete_asset_metadata_for_permanent_deletion = MagicMock()
        m.sns_client = MagicMock()
        m.subscription_table = MagicMock()
        m.asset_links_table = None
        m.asset_upload_table = None
        m.comment_table = None
        m.versions_table = None
        m.asset_versions_files_table = None
        m.asset_file_metadata_versions_table = None
        request = MagicMock()
        request.confirmPermanentDelete = True
        m.delete_asset_permanent("db1", "a1", request, {"tokens": ["u1"]})

        m.write_asset_history_record.assert_called_once()
        args = m.write_asset_history_record.call_args[0]
        assert args[:2] == ("db1", "a1")
        assert args[2] == m.CHANGE_SOURCE_PERMANENT_DELETE
        assert args[3] == "u1"
        assert args[4]["assetName"] == "N1"  # pre-delete snapshot
