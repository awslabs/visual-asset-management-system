# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for selective asset unarchive.

Asset archive records assetArchive provenance per archived file (delete-marker
VersionId). Unarchive with unarchiveFiles=true removes ONLY those markers —
files archived individually beforehand stay archived — and stamps an
assetUnarchive history record per restored file. With no provenance records
(legacy archive), nothing is restored.
"""

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
                "assetService_under_test", os.path.abspath(_ASSET_SERVICE_PATH)
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


@pytest.mark.unit
class TestGetAssetArchiveMarkerVersions:
    def test_maps_paths_to_marker_versions(self):
        m = _load()
        m.asset_file_version_history_table = MagicMock()
        m.asset_file_version_history_table.query.return_value = {
            "Items": [
                {"changeSource": "assetArchive", "filePath": "/a.txt", "versionId": "m1"},
                {"changeSource": "assetArchive", "filePath": "/sub/b.txt", "versionId": "m2"},
                {"changeSource": "fileArchive", "filePath": "/c.txt", "versionId": "m3"},
                {"changeSource": "upload", "filePath": "/a.txt", "versionId": "v0"},
            ]
        }
        markers = m.get_asset_archive_marker_versions("db1", "a1")
        assert markers == {"/a.txt": {"m1"}, "/sub/b.txt": {"m2"}}

    def test_empty_when_no_provenance(self):
        m = _load()
        m.asset_file_version_history_table = MagicMock()
        m.asset_file_version_history_table.query.return_value = {"Items": []}
        assert m.get_asset_archive_marker_versions("db1", "a1") == {}

    def test_empty_when_table_not_configured(self):
        m = _load()
        saved = m.asset_file_version_history_table
        m.asset_file_version_history_table = None
        try:
            assert m.get_asset_archive_marker_versions("db1", "a1") == {}
        finally:
            m.asset_file_version_history_table = saved


@pytest.mark.unit
class TestSelectiveUnarchive:
    def _paginator(self, m, markers):
        pag = MagicMock()
        pag.paginate.return_value = [{"DeleteMarkers": markers}]
        m.s3 = MagicMock()
        m.s3.get_paginator.return_value = pag
        return m.s3

    def test_restores_only_asset_archive_markers(self):
        m = _load()
        m.get_asset_archive_marker_versions = MagicMock(return_value={
            "/a.txt": {"m1"},
        })
        m.asset_file_version_history_table = MagicMock()
        s3 = self._paginator(m, [
            {"Key": "a1/a.txt", "VersionId": "m1", "IsLatest": True},       # asset-archived
            {"Key": "a1/pre.txt", "VersionId": "x9", "IsLatest": True},     # individually archived
        ])

        restored = m.unarchive_multi_assetFiles(
            {"Key": "a1/"}, "bucket", "db1", "a1", "tester")

        assert restored == 1
        s3.delete_object.assert_called_once_with(Bucket="bucket", Key="a1/a.txt", VersionId="m1")
        # assetUnarchive history record written for the restored file
        put_item = m.asset_file_version_history_table.put_item.call_args.kwargs["Item"]
        assert put_item["changeSource"] == "assetUnarchive"
        assert put_item["filePath"] == "/a.txt"
        assert put_item["changeUserId"] == "tester"

    def test_no_provenance_restores_nothing(self):
        m = _load()
        m.get_asset_archive_marker_versions = MagicMock(return_value={})
        s3 = self._paginator(m, [
            {"Key": "a1/a.txt", "VersionId": "m1", "IsLatest": True},
        ])

        restored = m.unarchive_multi_assetFiles(
            {"Key": "a1/"}, "bucket", "db1", "a1", "tester")

        assert restored == 0
        s3.delete_object.assert_not_called()

    def test_non_latest_markers_ignored(self):
        m = _load()
        m.get_asset_archive_marker_versions = MagicMock(return_value={"/a.txt": {"m1"}})
        s3 = self._paginator(m, [
            {"Key": "a1/a.txt", "VersionId": "m1", "IsLatest": False},  # buried marker
        ])

        restored = m.unarchive_multi_assetFiles(
            {"Key": "a1/"}, "bucket", "db1", "a1", "tester")

        assert restored == 0
        s3.delete_object.assert_not_called()


@pytest.mark.unit
class TestArchiveProvenanceRecording:
    def test_archive_writes_asset_archive_history(self):
        m = _load()
        m.asset_file_version_history_table = MagicMock()
        m.s3 = MagicMock()
        pag = MagicMock()
        pag.paginate.return_value = [{"Contents": [{"Key": "a1/a.txt"}, {"Key": "a1/"}]}]
        m.s3.get_paginator.return_value = pag
        m.s3.delete_object.return_value = {"VersionId": "marker-1"}
        m.mark_file_as_archived = lambda key, bucket: m.s3.delete_object(Bucket=bucket, Key=key)

        m.archive_multi_assetFiles({"Key": "a1/"}, "bucket", "db1", "a1", "tester")

        # History written for the file, not the folder marker
        assert m.asset_file_version_history_table.put_item.call_count == 1
        item = m.asset_file_version_history_table.put_item.call_args.kwargs["Item"]
        assert item["changeSource"] == "assetArchive"
        assert item["filePath"] == "/a.txt"
        assert item["versionId"] == "marker-1"

    def test_build_record_shape(self):
        m = _load()
        rec = m.build_asset_archive_history_record("db1", "a1", "sub/x.glb", "mv1", "alice")
        assert rec["databaseId:assetId:filePath"] == "db1:a1:/sub/x.glb"
        assert rec["databaseId:assetId"] == "db1:a1"
        assert rec["versionId"] == "mv1"
        assert rec["changeSource"] == "assetArchive"
        assert rec["changeUserId"] == "alice"
