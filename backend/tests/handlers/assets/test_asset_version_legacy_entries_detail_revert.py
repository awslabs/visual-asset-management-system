# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The version detail and a revert read a legacy version's file once and skip a "/" row (#1795, D1).

Before #1795 a version built from a caller's file list stored each path as the caller wrote it, so one version
can name a file both as `/a.txt` and `a.txt`, and can hold a `/` row that names no file. With the leading slash
stripped on read, the version detail listed `a.txt` twice and an empty path, and a revert of such a version copied
`a.txt` twice in parallel (two new S3 versions, one referenced by no version), copied the asset's folder marker for
the `/` row and counted every listed entry in its message. The version-entry reader now keeps one entry per file
(the row stored without the slash wins) and leaves out a row that names no file.
"""

from unittest.mock import MagicMock, patch

import pytest

from tests.handlers.assets.test_asset_version_detail_entry_keys import (  # noqa: E402,F401
    ASSET, CLAIMS, DB, PREFIX, _detail, av, table,
)

# A legacy version 1: one file under both spellings (the slashed row on an older S3 version), a "/" row and a
# second file
LEGACY = [("/a.txt", "v-old"), ("a.txt", "v-a"), ("/", "v-root"), ("/c.txt", "v-c")]


def _put_all(table, rows):
    for stored_key, version_id in rows:
        table.put_item(Item={"databaseId:assetId:assetVersionId": f"{DB}:{ASSET}:1", "fileKey": stored_key,
                             "versionId": version_id, "size": 12, "lastModified": "2026-11-01T10:00:00+00:00",
                             "etag": "e"})


@pytest.mark.unit
class TestTheCollapse:
    def test_one_entry_per_file_and_none_for_the_root(self):
        rows = [{"fileKey": k} for k in ("/", "/-n.txt", "/a.txt", "-n.txt", "a.txt", "//b.txt", "/b.txt", "")]
        assert [row["fileKey"] for row in av.collapse_version_entries(rows)] == ["-n.txt", "a.txt", "//b.txt", "/b.txt"]


@pytest.mark.unit
class TestTheVersionDetail:
    def test_each_file_once_and_no_empty_path(self, table):
        _put_all(table, LEGACY)
        response = _detail([{"Key": f"{PREFIX}a.txt", "VersionId": "v-a", "IsLatest": True},
                            {"Key": f"{PREFIX}c.txt", "VersionId": "v-c", "IsLatest": True}])
        assert sorted((f.relativeKey, f.versionId) for f in response.files) == [("a.txt", "v-a"), ("c.txt", "v-c")]


@pytest.mark.unit
class TestARevert:
    def test_each_file_is_copied_once_and_the_root_is_not_copied(self, table):
        _put_all(table, LEGACY)
        copies = []
        saved = []

        def _copy(source_bucket, source_key, source_version_id, dest_bucket, dest_key, **kwargs):
            copies.append((source_key, source_version_id, kwargs))
            return f"new-{source_version_id}"

        request = av.RevertAssetVersionRequestModel(assetVersionId="1", comment="back")
        with patch.object(av, "get_asset_with_permissions", return_value={"assetId": ASSET}), \
                patch.object(av, "get_asset_s3_location", return_value=("bucket", PREFIX)), \
                patch.object(av, "nested_locations_for", return_value=MagicMock(keep=lambda key: True)), \
                patch.object(av, "get_asset_version_metadata", return_value={"assetVersionId": "1"}), \
                patch.object(av, "reserve_next_asset_version", return_value="2"), \
                patch.object(av, "does_file_version_exist", return_value=True), \
                patch.object(av, "copy_s3_object_version", side_effect=_copy), \
                patch.object(av, "delete_assetAuxiliary_files"), \
                patch.object(av, "save_asset_file_versions",
                             side_effect=lambda d, a, v, files: saved.extend(files) or True), \
                patch.object(av, "save_asset_metadata_version", return_value=True), \
                patch.object(av, "stamp_asset_version_provenance"), \
                patch.object(av, "finalize_asset_version"), \
                patch.object(av, "send_subscription_email"):
            response = av.revert_asset_version(DB, ASSET, request, CLAIMS)

        assert sorted((key, version) for key, version, _ in copies) == [
            (f"{PREFIX}a.txt", "v-a"),
            (f"{PREFIX}c.txt", "v-c"),
        ]
        assert all(kwargs["change_source"] == "fileRevert" for _, _, kwargs in copies)
        assert sorted((f["relativeKey"], f["versionId"]) for f in saved) == [("a.txt", "new-v-a"),
                                                                             ("c.txt", "new-v-c")]
        assert response.skippedFiles is None
        assert response.message == "Successfully reverted to version 1 with 2 files"
