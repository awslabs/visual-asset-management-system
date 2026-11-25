# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A legacy version that names one file twice, or names "/", reads as one entry per file (#1795, D1).

Before #1795 a version built from a caller's file list stored each path as the caller wrote it, so one version
can name a file both as `/a.txt` and `a.txt`, and can hold a `/` row that names no file. Once the readers stripped
the leading slash, those rows became one key twice and an empty key: file info labelled the file's S3 version with
the asset version twice, and the version's file listing added the asset root as a member. The file and export
readers now keep one entry per file (the row stored without the slash wins) and leave out a row that names no file.
"""

from unittest.mock import MagicMock, patch

import pytest

# The harness of the single-entry tests; imported as a module so its test classes are not collected here too
from tests.handlers.assets import test_asset_version_entry_keys as entry_keys  # noqa: E402
from tests.handlers.assets.test_asset_version_entry_keys import (  # noqa: E402,F401
    _EXPORT_ASSET, _EXPORT_DB, _EXPORT_PREFIX, ASSET, DB, MODIFIED, PREFIX, _listed, _versions_table, af, aws,
)

# A legacy version 1: one file under both spellings, a "/" row, and a file whose name sorts before "/"
LEGACY = [(DB, ASSET, "1", "/a.txt", "v-a"), (DB, ASSET, "1", "a.txt", "v-a"), (DB, ASSET, "1", "/", "v-root"),
          (DB, ASSET, "1", "-n.txt", "v-new"), (DB, ASSET, "1", "/-n.txt", "v-old")]


def _root_marker():
    return {"fileName": "", "key": PREFIX, "relativePath": "/", "isFolder": True, "size": 0,
            "dateCreatedCurrentVersion": MODIFIED, "storageClass": "STANDARD", "versionId": "v-root",
            "isArchived": False}


@pytest.mark.unit
class TestTheCollapse:
    def test_one_entry_per_file_and_none_for_the_root(self):
        rows = [{"fileKey": k} for k in ("/", "/-n.txt", "/a.txt", "-n.txt", "a.txt", "//b.txt", "/b.txt", "")]
        kept = [row["fileKey"] for row in af.collapse_version_entries(rows)]
        assert kept == ["-n.txt", "a.txt", "//b.txt"]


@pytest.mark.unit
class TestTheFileListFlag:
    def test_the_row_stored_without_the_slash_decides(self, aws, monkeypatch):
        monkeypatch.setattr(af, "asset_version_files_table", _versions_table(LEGACY))
        request = MagicMock(maxItems=100, pageSize=100, startingToken=None, prefix=None,
                            includeArchived=False, basic=False)
        with patch.object(af, "list_s3_objects_with_archive_status", return_value={
                "items": [_listed("a.txt", "v-a"), _listed("-n.txt", "v-new")], "NextToken": None}):
            response = af.list_asset_files_current(DB, ASSET, {"currentVersionId": "1"}, "bucket", PREFIX, request)
        assert {i.relativePath: i.currentAssetVersionFileVersionMismatch for i in response.items} == {
            "/a.txt": False, "/-n.txt": False}


@pytest.mark.unit
class TestFileInfoLabels:
    def test_the_s3_version_is_labelled_once_per_asset_version(self, aws, monkeypatch):
        versions = entry_keys.TestFileInfo()._info(monkeypatch, [
            (DB, ASSET, "1", "/inner.txt", "v-1"), (DB, ASSET, "1", "inner.txt", "v-1"),
            (DB, ASSET, "2", "/inner.txt", "v-2"), (DB, ASSET, "2", "inner.txt", "v-2")])
        assert versions["v-2"] == (False, [{"id": "2", "label": "v2 (RC1)"}])
        assert versions["v-1"][1] == [{"id": "1", "label": "v1"}]

    def test_the_row_stored_without_the_slash_names_the_s3_version(self, aws, monkeypatch):
        versions = entry_keys.TestFileInfo()._info(monkeypatch, [
            (DB, ASSET, "2", "inner.txt", "v-2"), (DB, ASSET, "2", "/inner.txt", "v-1")])
        assert versions["v-2"] == (False, [{"id": "2", "label": "v2 (RC1)"}])
        assert versions["v-1"][1] == []


@pytest.mark.unit
class TestTheVersionFileListing:
    def _list(self, monkeypatch, basic):
        monkeypatch.setattr(af, "asset_version_files_table", _versions_table(LEGACY))
        request = MagicMock(assetVersionId="1", basic=basic, maxItems=100, pageSize=100,
                            startingToken=None, prefix=None, includeArchived=False)
        s3 = MagicMock()
        s3.list_object_versions.side_effect = AssertionError("every file is in the listing")
        with patch.object(af, "validate_asset_version_exists"), patch.object(af, "s3_client", s3), \
                patch.object(af, "list_s3_objects_with_archive_status", return_value={
                    "items": [_root_marker(), _listed("a.txt", "v-x"), _listed("-n.txt", "v-x")],
                    "NextToken": None}):
            response = af.list_asset_files_from_version(DB, ASSET, {}, "bucket", PREFIX, request)
        return sorted((i.relativePath, i.versionId, i.isFolder) for i in response.items)

    @pytest.mark.parametrize("basic", [False, True], ids=["detailed", "basic"])
    def test_each_file_once_and_no_asset_root(self, aws, monkeypatch, basic):
        assert self._list(monkeypatch, basic) == [("/-n.txt", "v-new", False), ("/a.txt", "v-a", False)]


@pytest.mark.unit
class TestTheAssetExport:
    def test_the_row_stored_without_the_slash_decides(self, aws):
        key = f"{_EXPORT_PREFIX}-n.txt"
        flags = entry_keys.TestTheAssetExport()._export([
            (_EXPORT_DB, _EXPORT_ASSET, "1", "-n.txt", f"current-{key}"),
            (_EXPORT_DB, _EXPORT_ASSET, "1", "/-n.txt", f"older-{key}"),
            (_EXPORT_DB, _EXPORT_ASSET, "1", "/", "v-root")], [key])
        assert flags == {"/-n.txt": False}
