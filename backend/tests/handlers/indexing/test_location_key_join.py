# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""S3 keys built from an asset's stored location key and an asset-relative file path.

A stored ``assetLocation.Key`` names the asset's folder, and one supplied as ``bucketExistingKey``
may lack its trailing ``/``. Concatenating it with the file path then runs the folder name into
the path, so the object is never found and the file's search document keeps stale metadata.
``join_asset_location_key`` joins the two with exactly one ``/``; the metadata-stream re-index in
the file indexer is driven end to end for both stream event kinds, with the conventional
trailing-slash location as the paired control.
"""

from unittest.mock import patch

import pytest

from backend.backend.common.s3PathPatterns import join_asset_location_key
from tests.handlers.indexing.test_fileIndexer_preview_skip import (  # noqa: F401 (fixture)
    _metadata_stream_record,
    fileIndexer,
)


@pytest.mark.unit
class TestJoinAssetLocationKey:
    @pytest.mark.parametrize(
        "location_key, relative_path, expected",
        [
            ("projects/building-a", "/sub/m.glb", "projects/building-a/sub/m.glb"),
            ("projects/building-a/", "/sub/m.glb", "projects/building-a/sub/m.glb"),
            ("projects/building-a", "sub/m.glb", "projects/building-a/sub/m.glb"),
            ("projects/building-a/", "sub/m.glb", "projects/building-a/sub/m.glb"),
            ("asset-1/", "//m.glb", "asset-1/m.glb"),
            ("", "/m.glb", "m.glb"),
            (None, "/m.glb", "m.glb"),
        ],
    )
    def test_the_folder_and_the_path_meet_at_one_slash(self, location_key, relative_path, expected):
        assert join_asset_location_key(location_key, relative_path) == expected


def _asset(location_key):
    return {"databaseId": "db-1", "assetId": "asset-1", "bucketId": "b-1",
            "assetLocation": {"Key": location_key}}


_BUCKET = {"bucketName": "bucket-1", "baseAssetsPrefix": ""}


@pytest.mark.unit
class TestMetadataStreamReindexKey:
    @pytest.mark.parametrize("event_name", ["INSERT", "MODIFY", "REMOVE"])
    @pytest.mark.parametrize("location_key", ["projects/building-a", "projects/building-a/"])
    def test_the_reindexed_key_is_under_the_asset_folder(self, fileIndexer, event_name, location_key):
        record = _metadata_stream_record("db-1:asset-1:/sub/m.glb", event_name)

        with patch.object(fileIndexer, "get_asset_details", return_value=_asset(location_key)), \
                patch.object(fileIndexer, "get_bucket_details", return_value=_BUCKET), \
                patch.object(fileIndexer, "process_file_index_request") as process:
            fileIndexer.handle_metadata_stream(record)

        process.assert_called_once()
        request = process.call_args[0][0]
        assert request.s3Key == "projects/building-a/sub/m.glb"
        assert request.filePath == "/sub/m.glb"
