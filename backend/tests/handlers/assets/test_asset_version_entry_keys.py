# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A file is matched to its asset-version entry whether or not the entry's key starts with a slash (#1795).

An asset version records one row per file in the asset file versions table, keyed by `fileKey`, the file's
asset-relative path. A version built from the asset's current files stores it without a leading slash
(`inner.txt`); a version built from a caller's file list stored it as the caller wrote it, often as the file
list shows it (`/inner.txt`). The readers compared the stored key with the path without its slash, so a
slash-stored entry matched nothing: the file list and the asset export marked the file as differing from the
current asset version, file info showed no asset version for its S3 version, and the version's detailed file
listing left the file out. Each reader now matches both forms. The tables are moto tables with the
deployment's key schema and its `databaseIdAssetIdIndex`.
"""

import datetime
import os
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME", "test-asset-file-versions-table")

from backend.backend.handlers.assets import assetFiles as af  # noqa: E402
from tests.handlers.assets.test_assetExportService_authz_fail_closed import (  # noqa: E402
    _load_asset_export_service,
)
from tests.handlers.assets.test_assetExportService_file_paging import (  # noqa: E402
    _ASSET as _EXPORT_ASSET,
    _DB as _EXPORT_DB,
    _PREFIX as _EXPORT_PREFIX,
    _CountingQueryClient,
    _FakeS3,
    _asset_item,
    _offline_patches,
)

PK = "databaseId:assetId:assetVersionId"
DB = "db1"
ASSET = "a1"
PREFIX = f"{ASSET}/"
CLAIMS = {"tokens": ["alice"]}
MODIFIED = "2026-11-01T10:00:00+00:00"

# The two forms a version entry's key is stored in
SHAPES = pytest.mark.parametrize("stored_key", ["/inner.txt", "inner.txt"], ids=["slash", "no-slash"])


def _versions_table(rows, name="test-asset-file-versions-table"):
    """A moto versions table holding `rows` of (database, asset, assetVersionId, fileKey, versionId)."""
    table = boto3.resource("dynamodb", region_name="us-east-1").create_table(
        TableName=name,
        KeySchema=[{"AttributeName": PK, "KeyType": "HASH"}, {"AttributeName": "fileKey", "KeyType": "RANGE"}],
        AttributeDefinitions=[
            {"AttributeName": PK, "AttributeType": "S"},
            {"AttributeName": "fileKey", "AttributeType": "S"},
            {"AttributeName": "databaseId:assetId", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[{
            "IndexName": "databaseIdAssetIdIndex",
            "KeySchema": [{"AttributeName": "databaseId:assetId", "KeyType": "HASH"}],
            "Projection": {"ProjectionType": "ALL"},
        }],
        BillingMode="PAY_PER_REQUEST",
    )
    for database, asset, version, key, version_id in rows:
        table.put_item(Item={PK: f"{database}:{asset}:{version}", "fileKey": key,
                             "databaseId:assetId": f"{database}:{asset}", "versionId": version_id,
                             "size": 12, "lastModified": MODIFIED, "etag": "e"})
    return table


@pytest.fixture
def aws():
    with mock_aws():
        yield


def _listed(relative, version_id):
    return {
        "fileName": relative.rsplit("/", 1)[-1],
        "key": PREFIX + relative,
        "relativePath": "/" + relative,
        "isFolder": False,
        "size": 12,
        "dateCreatedCurrentVersion": MODIFIED,
        "storageClass": "STANDARD",
        "versionId": version_id,
        "isArchived": False,
    }


@pytest.mark.unit
class TestTheFileListFlag:
    """`GET .../listFiles` sets `currentAssetVersionFileVersionMismatch` from the current version's entries."""

    def _list(self, monkeypatch, rows, listed):
        monkeypatch.setattr(af, "asset_version_files_table", _versions_table(rows))
        request = MagicMock(maxItems=100, pageSize=100, startingToken=None, prefix=None,
                            includeArchived=False, basic=False)
        with patch.object(af, "list_s3_objects_with_archive_status",
                          return_value={"items": listed, "NextToken": None}):
            response = af.list_asset_files_current(DB, ASSET, {"currentVersionId": "1"}, "bucket", PREFIX, request)
        return {item.relativePath: item.currentAssetVersionFileVersionMismatch for item in response.items}

    @SHAPES
    def test_the_file_the_version_holds_does_not_differ(self, aws, monkeypatch, stored_key):
        flags = self._list(monkeypatch, [(DB, ASSET, "1", stored_key, "v-1")], [_listed("inner.txt", "v-1")])
        assert flags == {"/inner.txt": False}

    @SHAPES
    def test_control_another_s3_version_still_differs(self, aws, monkeypatch, stored_key):
        flags = self._list(monkeypatch, [(DB, ASSET, "1", stored_key, "v-1")], [_listed("inner.txt", "v-2")])
        assert flags == {"/inner.txt": True}

    def test_an_asset_holding_both_forms_matches_each_file(self, aws, monkeypatch):
        flags = self._list(monkeypatch,
                           [(DB, ASSET, "1", "/sub/a.txt", "v-a"), (DB, ASSET, "1", "b.txt", "v-b")],
                           [_listed("sub/a.txt", "v-a"), _listed("b.txt", "v-b"), _listed("c.txt", "v-c")])
        assert flags == {"/sub/a.txt": False, "/b.txt": False, "/c.txt": True}


@pytest.mark.unit
class TestFileInfo:
    """`GET .../fileInfo?includeVersions=true` marks the current S3 version and lists the asset versions of each."""

    def _info(self, monkeypatch, rows, file_path="/inner.txt"):
        monkeypatch.setattr(af, "asset_version_files_table", _versions_table(rows))
        metadata = {
            "fileName": "inner.txt", "key": PREFIX + "inner.txt", "relativePath": "/inner.txt",
            "isFolder": False, "size": 12, "contentType": "text/plain", "lastModified": MODIFIED,
            "etag": "e", "storageClass": "STANDARD", "isArchived": False,
            "versions": [
                {"versionId": "v-2", "lastModified": "2026-11-02T10:00:00+00:00", "size": 12, "isLatest": True,
                 "storageClass": "STANDARD", "etag": "e", "isArchived": False},
                {"versionId": "v-1", "lastModified": MODIFIED, "size": 12, "isLatest": False,
                 "storageClass": "STANDARD", "etag": "e", "isArchived": False},
            ],
        }
        monkeypatch.setattr(af, "get_asset_with_permissions",
                            lambda databaseId, assetId, op, claims, include_archived=False:
                            {"assetId": assetId, "currentVersionId": "2"})
        monkeypatch.setattr(af, "get_asset_s3_location", lambda asset: ("bucket", PREFIX))
        monkeypatch.setattr(af, "get_s3_object_metadata", lambda bucket, key, include_versions: metadata)
        monkeypatch.setattr(af, "query_asset_version_history_map", lambda database_id, asset_id: {})
        monkeypatch.setattr(af, "get_all_asset_versions", lambda database_id, asset_id: [
            {"assetVersionId": "1"}, {"assetVersionId": "2", "versionAlias": "RC1"}])
        monkeypatch.setattr(af, "find_preview_files_for_base", lambda bucket, key: [])
        response = af.get_file_info(DB, ASSET, file_path, True, CLAIMS)
        return {v.versionId: (v.currentAssetVersionFileVersionMismatch, v.assetVersionIds)
                for v in response.versions}

    @SHAPES
    @pytest.mark.parametrize("file_path", ["/inner.txt", "inner.txt"], ids=["path-slash", "path-no-slash"])
    def test_the_current_version_and_each_versions_label_are_found(self, aws, monkeypatch, stored_key,
                                                                    file_path):
        versions = self._info(monkeypatch, [(DB, ASSET, "1", stored_key, "v-1"),
                                            (DB, ASSET, "2", stored_key, "v-2")], file_path)
        assert versions["v-2"] == (False, [{"id": "2", "label": "v2 (RC1)"}])
        assert versions["v-1"][1] == [{"id": "1", "label": "v1"}]

    def test_control_a_file_the_current_version_does_not_hold_differs(self, aws, monkeypatch):
        versions = self._info(monkeypatch, [(DB, ASSET, "1", "/inner.txt", "v-1"),
                                            (DB, ASSET, "2", "/other.txt", "v-9")])
        assert versions["v-2"] == (True, [])
        assert versions["v-1"][1] == [{"id": "1", "label": "v1"}]

    def test_an_unslashed_entry_is_preferred_when_a_version_holds_both_forms(self, aws, monkeypatch):
        versions = self._info(monkeypatch, [(DB, ASSET, "2", "inner.txt", "v-2"),
                                            (DB, ASSET, "2", "/inner.txt", "v-1")])
        assert versions["v-2"][0] is False


@pytest.mark.unit
class TestTheVersionFileListing:
    """`GET .../listFiles?assetVersionId=N` lists the files the version holds."""

    @SHAPES
    def test_the_detailed_listing_shows_the_file_at_the_versions_s3_version(self, aws, monkeypatch, stored_key):
        monkeypatch.setattr(af, "asset_version_files_table", _versions_table([(DB, ASSET, "1", stored_key, "v-1")]))
        request = MagicMock(assetVersionId="1", basic=False, maxItems=100, pageSize=100,
                            startingToken=None, prefix=None, includeArchived=False)
        s3 = MagicMock()
        # The listing holds the file, so the permanent-delete check must not be needed
        s3.list_object_versions.side_effect = AssertionError("the file is in the listing")
        with patch.object(af, "validate_asset_version_exists"), patch.object(af, "s3_client", s3), \
                patch.object(af, "list_s3_objects_with_archive_status",
                             return_value={"items": [_listed("inner.txt", "v-2")], "NextToken": None}):
            response = af.list_asset_files_from_version(DB, ASSET, {}, "bucket", PREFIX, request)
        assert [(i.relativePath, i.versionId, i.isPermanentlyDeleted) for i in response.items] == [
            ("/inner.txt", "v-1", False)]

    @SHAPES
    def test_the_basic_listing_shows_the_file(self, aws, monkeypatch, stored_key):
        monkeypatch.setattr(af, "asset_version_files_table", _versions_table([(DB, ASSET, "1", stored_key, "v-1")]))
        request = MagicMock(assetVersionId="1", basic=True)
        with patch.object(af, "validate_asset_version_exists"):
            response = af.list_asset_files_from_version(DB, ASSET, {}, "bucket", PREFIX, request)
        assert [(i.relativePath, i.key, i.versionId) for i in response.items] == [
            ("/inner.txt", PREFIX + "inner.txt", "v-1")]


@pytest.mark.unit
class TestTheAssetExport:
    """`POST .../export` sets each file's `currentAssetVersionFileVersionMismatch` from the current version."""

    def _export(self, rows, keys):
        m = _load_asset_export_service()
        real_get_asset_file_versions = m.get_asset_file_versions
        patches = _offline_patches(m, _FakeS3({_EXPORT_PREFIX: keys}), _CountingQueryClient(), [_asset_item()])
        patches += [
            patch.object(m, "get_asset_file_versions", real_get_asset_file_versions),
            patch.object(m, "asset_file_versions_table", _versions_table(rows)),
        ]
        for one in patches:
            one.start()
        try:
            response = m.export_assets(
                _EXPORT_DB, _EXPORT_ASSET,
                m.AssetExportRequestModel(fetchAssetRelationships=False, includeFileMetadata=False),
                {"tokens": ["alice"], "roles": []}, {"requestContext": {}})
        finally:
            for one in reversed(patches):
                one.stop()
        return {file["relativePath"]: file["currentAssetVersionFileVersionMismatch"]
                for asset in response["assets"] for file in asset["files"]}

    @SHAPES
    def test_the_file_the_version_holds_does_not_differ(self, aws, stored_key):
        key = f"{_EXPORT_PREFIX}inner.txt"
        flags = self._export([(_EXPORT_DB, _EXPORT_ASSET, "1", stored_key, f"current-{key}")], [key])
        assert flags == {"/inner.txt": False}

    @SHAPES
    def test_control_another_s3_version_still_differs(self, aws, stored_key):
        key = f"{_EXPORT_PREFIX}inner.txt"
        flags = self._export([(_EXPORT_DB, _EXPORT_ASSET, "1", stored_key, f"older-{key}")], [key])
        assert flags == {"/inner.txt": True}
