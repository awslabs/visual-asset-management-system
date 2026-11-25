# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The version detail shows each file's path without a leading slash, however the entry was stored (#1795).

A version built from a caller's file list before #1795 stored each path as the caller wrote it, often with a
leading slash (`/inner.txt`); every version created since stores it without (`inner.txt`). The entries are not
migrated, so `GET .../getVersion/{assetVersionId}` read them back as stored and one asset's versions showed one
file under two spellings. The shared reader now hands every entry out without the slash. The versions table is
a moto table with the deployment's key schema.
"""

from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from backend.backend.handlers.assets import assetVersions as av  # noqa: E402

PK = "databaseId:assetId:assetVersionId"
DB = "db1"
ASSET = "a1"
PREFIX = f"{ASSET}/"
CLAIMS = {"tokens": ["alice"]}
SHAPES = pytest.mark.parametrize("stored_key", ["/sub/inner.txt", "sub/inner.txt", "//sub/inner.txt"],
                                 ids=["slash", "no-slash", "double-slash"])


@pytest.fixture
def table(monkeypatch):
    with mock_aws():
        created = boto3.resource("dynamodb", region_name="us-east-1").create_table(
            TableName="test-asset-file-versions-table",
            KeySchema=[{"AttributeName": PK, "KeyType": "HASH"}, {"AttributeName": "fileKey", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": PK, "AttributeType": "S"},
                                  {"AttributeName": "fileKey", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        monkeypatch.setattr(av, "asset_file_versions_table", created)
        yield created


def _put(table, stored_key, version_id="v-1", asset_version="1"):
    table.put_item(Item={PK: f"{DB}:{ASSET}:{asset_version}", "fileKey": stored_key, "versionId": version_id,
                         "size": 12, "lastModified": "2026-11-01T10:00:00+00:00", "etag": "e"})


def _detail(versions, delete_markers=()):
    request = av.GetAssetVersionRequestModel(assetVersionId="1")
    listing = {"Versions": list(versions), "DeleteMarkers": list(delete_markers)}
    with patch.object(av, "get_asset_with_permissions", return_value={"assetId": ASSET}), \
            patch.object(av, "get_asset_version_metadata", return_value={"assetVersionId": "1", "comment": "c"}), \
            patch.object(av, "get_asset_s3_location", return_value=("bucket", PREFIX)), \
            patch.object(av, "nested_locations_for", return_value=None), \
            patch.object(av, "list_all_object_versions", return_value=listing), \
            patch.object(av, "get_asset_metadata_version", return_value=[]):
        return av.get_asset_version_details(DB, ASSET, request, CLAIMS)


@pytest.mark.unit
class TestTheVersionDetail:
    @SHAPES
    def test_the_path_is_shown_without_its_leading_slash(self, table, stored_key):
        _put(table, stored_key)
        response = _detail([{"Key": f"{PREFIX}sub/inner.txt", "VersionId": "v-1", "IsLatest": True}])
        assert [(f.relativeKey, f.versionId, f.isPermanentlyDeleted, f.isLatestVersionArchived)
                for f in response.files] == [("sub/inner.txt", "v-1", False, False)]

    @SHAPES
    def test_control_the_files_state_is_still_read_from_its_key(self, table, stored_key):
        _put(table, stored_key)
        response = _detail([], delete_markers=[{"Key": f"{PREFIX}sub/inner.txt", "VersionId": "dm-1",
                                                "IsLatest": True}])
        assert [(f.relativeKey, f.isPermanentlyDeleted, f.isLatestVersionArchived) for f in response.files] == [
            ("sub/inner.txt", True, True)]


@pytest.mark.unit
class TestTheOtherReadersOfTheSharedHelper:
    @SHAPES
    def test_a_pinned_lookup_still_resolves_the_file(self, table, stored_key):
        _put(table, stored_key)
        with patch.object(av, "validate_asset_version_exists"):
            for requested in ("/sub/inner.txt", "sub/inner.txt"):
                assert av.resolve_file_version_from_asset_version(DB, ASSET, "1", requested) == "v-1"

    def test_a_revert_reports_a_skipped_legacy_entry_without_its_slash(self, table):
        _put(table, "/gone.txt", version_id="v-gone")
        request = av.RevertAssetVersionRequestModel(assetVersionId="1", comment="back")
        with patch.object(av, "get_asset_with_permissions", return_value={"assetId": ASSET}), \
                patch.object(av, "get_asset_s3_location", return_value=("bucket", PREFIX)), \
                patch.object(av, "nested_locations_for", return_value=MagicMock(keep=lambda key: True)), \
                patch.object(av, "get_asset_version_metadata", return_value={"assetVersionId": "1"}), \
                patch.object(av, "reserve_next_asset_version", return_value="2"), \
                patch.object(av, "does_file_version_exist", return_value=False), \
                patch.object(av, "save_asset_file_versions", return_value=True), \
                patch.object(av, "save_asset_metadata_version", return_value=True), \
                patch.object(av, "stamp_asset_version_provenance"), \
                patch.object(av, "finalize_asset_version"), \
                patch.object(av, "send_subscription_email"):
            response = av.revert_asset_version(DB, ASSET, request, CLAIMS)
        assert response.skippedFiles == ["gone.txt"]
