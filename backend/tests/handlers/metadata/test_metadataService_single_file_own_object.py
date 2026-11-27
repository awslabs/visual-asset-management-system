# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Issue #1846: File metadata and attributes by path reaches a single-file asset's own object at ``/{file name}``.

The asset's location is the object ``p1/model.glb``. Without a companion entry ``p1/model.glb/model.glb`` the
object is listed at ``/model.glb``; with one, that entry keeps the path and nothing changes. Folder assets are
unchanged. The companion check is one ``ListObjectVersions`` call (``MaxKeys=1``).
"""

from unittest.mock import MagicMock, patch

import pytest

from backend.backend.handlers.metadata import metadataService
from models.common import VAMSGeneralErrorResponse


LOCATION = "p1/model.glb"
COPY = "p1/model.glb/model.glb"


def _versions_listing(companion):
    if companion:
        return {"Versions": [{"Key": COPY, "VersionId": "v1", "IsLatest": True}]}
    return {"Versions": [{"Key": COPY + ".previewFile.png", "VersionId": "v9", "IsLatest": True}]}


def _asset(location=LOCATION):
    return {"databaseId": "db1", "assetId": "asset1", "bucketId": "bucket-1", "assetLocation": {"Key": location}}


# -------------------------------------------------------------------------- metadata

@pytest.mark.unit
class TestFileMetadataTarget:
    def _check(self, file_path, companion=False, location=LOCATION):
        s3 = MagicMock()
        s3.list_object_versions.return_value = _versions_listing(companion)
        with patch.object(metadataService, "validate_asset_exists", return_value=_asset(location)), \
                patch.object(metadataService, "get_bucket_details", return_value={"bucketName": "b"}), \
                patch.object(metadataService, "s3", s3):
            metadataService.validate_file_exists("db1", "asset1", file_path)
        return s3.head_object.call_args.kwargs["Key"]

    def test_the_objects_metadata_is_written_for_the_object(self):
        assert self._check("/model.glb") == LOCATION

    def test_with_a_companion_entry_it_is_written_for_the_companion(self):
        assert self._check("/model.glb", companion=True) == COPY

    def test_a_companion_entrys_metadata_is_unchanged(self):
        assert self._check("/sub/x.obj") == "p1/model.glb/sub/x.obj"

    def test_a_traversal_names_no_file(self):
        with pytest.raises(VAMSGeneralErrorResponse):
            self._check("/sub/../../other.glb")

    def test_control_a_folder_asset_is_unchanged(self):
        assert self._check("/x.txt", location="proj/a/") == "proj/a/x.txt"


@pytest.mark.unit
class TestFileRecordPath:
    """The web sends a file's full key; every accepted form keys the record by the path the file is listed at."""

    def _path(self, file_path, companion=False, location=LOCATION, asset_found=True):
        s3 = MagicMock()
        s3.list_object_versions.return_value = _versions_listing(companion)
        table = MagicMock()
        table.get_item.return_value = {"Item": _asset(location)} if asset_found else {}
        with patch.object(metadataService, "asset_storage_table", table), \
                patch.object(metadataService, "get_bucket_details", return_value={"bucketName": "b"}), \
                patch.object(metadataService, "s3", s3):
            return metadataService.file_record_path("db1", "asset1", file_path)

    # The request models keep the caller's form (#1943): the web sends the full key without a leading '/'
    @pytest.mark.parametrize("file_path", ["/model.glb", "p1/model.glb"])
    def test_the_objects_path_and_its_full_key_key_the_object(self, file_path):
        assert self._path(file_path) == "/model.glb"

    def test_with_a_companion_entry_the_full_key_keeps_its_earlier_path(self):
        assert self._path("p1/model.glb", companion=True) == "/p1/model.glb"
        assert self._path("/model.glb", companion=True) == "/model.glb"

    @pytest.mark.parametrize("file_path", ["/sub/x.obj", "p1/model.glb/sub/x.obj"])
    def test_a_companion_entrys_path_and_full_key_key_the_entry(self, file_path):
        assert self._path(file_path) == "/sub/x.obj"

    def test_a_leading_slash_and_the_full_key_is_a_companion_entry(self):
        """#1943: '/{file key}/x' is the path of the companion entry '{file key}/{file key}/x'."""
        assert self._path("/p1/model.glb/sub/x.obj") == "/p1/model.glb/sub/x.obj"

    def test_a_path_that_starts_with_the_asset_id_is_a_path_in_the_asset(self):
        """#1943: the '/{assetId}/' segment is no longer dropped; the full key names the root file."""
        assert self._path("/asset1/x.txt", location="asset1/") == "/asset1/x.txt"
        assert self._path("asset1/x.txt", location="asset1/") == "/x.txt"
        assert self._path("/x.txt", location="asset1/") == "/x.txt"

    def test_an_unknown_asset_keeps_the_path_given(self):
        assert self._path("/p1/model.glb", asset_found=False) == "/p1/model.glb"
        assert self._path("/asset1/x.txt", asset_found=False) == "/asset1/x.txt"
        assert self._path("p1/model.glb", asset_found=False) == "/p1/model.glb"

    def test_a_folder_location_keys_its_full_key_by_the_path_after_the_folder(self):
        assert self._path("custom/path/x.glb", location="custom/path/") == "/x.glb"
        assert self._path("/x.glb", location="custom/path/") == "/x.glb"
        assert self._path("/custom/path/x.glb", location="custom/path/") == "/custom/path/x.glb"
