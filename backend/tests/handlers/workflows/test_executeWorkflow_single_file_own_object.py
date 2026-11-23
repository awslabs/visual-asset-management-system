# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Issue #1846: A workflow input selection reaches a single-file asset's own object at ``/{file name}``.

The asset's location is the object ``p1/model.glb``. Without a companion entry ``p1/model.glb/model.glb`` the
object is listed at ``/model.glb``; with one, that entry keeps the path and nothing changes. Folder assets are
unchanged. The companion check is one ``ListObjectVersions`` call (``MaxKeys=1``).
"""

from unittest.mock import MagicMock, patch

import pytest

from backend.tests.handlers.workflows.test_executeWorkflow import ewv2 as ew


LOCATION = "p1/model.glb"
COPY = "p1/model.glb/model.glb"


def _versions_listing(companion):
    if companion:
        return {"Versions": [{"Key": COPY, "VersionId": "v1", "IsLatest": True}]}
    return {"Versions": [{"Key": COPY + ".previewFile.png", "VersionId": "v9", "IsLatest": True}]}


def _asset(location=LOCATION):
    return {"databaseId": "db1", "assetId": "asset1", "bucketId": "bucket-1", "assetLocation": {"Key": location}}


# -------------------------------------------------------------------- workflow input

@pytest.mark.unit
class TestWorkflowInput:
    def _key(self, relative, companion=False, location=LOCATION):
        s3c = MagicMock()
        s3c.list_object_versions.return_value = _versions_listing(companion)
        with patch.object(ew, "s3c", s3c), \
                patch.object(ew, "_asset_bucket_details", return_value={"bucketName": "b"}):
            item = {"relativeFileKey": relative}
            key = ew._input_full_key(item, {"assetLocation": {"Key": location}, "bucketId": "b1"})
            assert ew._input_full_key(item, {}) == key
        return key, s3c

    def test_the_file_name_selects_the_object(self):
        key, s3c = self._key("/model.glb")
        assert key == LOCATION and s3c.list_object_versions.call_count == 1

    def test_with_a_companion_entry_it_selects_the_companion(self):
        assert self._key("/model.glb", companion=True)[0] == COPY

    @pytest.mark.parametrize("relative,key", [("/", "p1/model.glb/"), ("/sub/x.obj", "p1/model.glb/sub/x.obj")])
    def test_the_whole_asset_and_companion_entries_are_unchanged(self, relative, key):
        assert self._key(relative)[0] == key

    def test_control_a_folder_asset_makes_no_companion_check(self):
        key, s3c = self._key("/x.txt", location="proj/a/")
        assert key == "proj/a/x.txt" and not s3c.list_object_versions.called


@pytest.mark.unit
class TestFileUploadTrigger:
    """A new version of a single-file asset's own file triggers runs on that file, not on the asset folder."""

    def _relative(self, companion=False, location=LOCATION, key=LOCATION):
        from backend.tests.handlers.workflows.test_wb5b_trigger_dispatch import wd
        head = {"Metadata": {"databaseid": "db1", "assetid": "asset1"}}
        with patch.object(wd.s3_client, "head_object", return_value=head), \
                patch.object(wd.s3_client, "list_object_versions", return_value=_versions_listing(companion)), \
                patch.object(wd.asset_storage_table, "get_item",
                             return_value={"Item": {"assetLocation": {"Key": location}}}):
            return wd._resolve_asset_relative_key("b1", key)[2]

    def test_the_own_file_is_its_file_name(self):
        assert self._relative() == "/model.glb"

    def test_with_a_companion_entry_it_keeps_the_earlier_path(self):
        assert self._relative(companion=True) == "/"

    def test_a_companion_file_is_unchanged(self):
        assert self._relative(key="p1/model.glb/sub/x.obj") == "/sub/x.obj"
