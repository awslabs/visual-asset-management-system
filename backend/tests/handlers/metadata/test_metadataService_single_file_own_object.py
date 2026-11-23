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
