# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Folder markers VAMS writes carry the VAMS stamp metadata (#1060).

The asset prefix marker from createAsset and the folder from ``create_folder`` are written with
``databaseid`` / ``assetid`` / ``vams-changesource``, like every other object VAMS writes. Bucket sync
no longer copies an object onto itself to add them.
"""

from unittest.mock import MagicMock

import pytest

from backend.backend.common.s3MetadataKeys import (
    ASSET_ID_METADATA_KEY,
    DATABASE_ID_METADATA_KEY,
    VAMS_CHANGE_SOURCE_DIRECT,
    VAMS_CHANGE_SOURCE_METADATA_KEY,
    folder_marker_metadata,
)
from tests.handlers.assets.test_createAsset_conditional_put import _load as _load_create_asset
from tests.handlers.assets import test_uploadFile_key_length as key_length
from tests.handlers.indexing.test_sqsBucketSync_recreation_guard import _load as _load_bucket_sync

DATABASE_ID = "db-001"
ASSET_ID = "asset1"

EXPECTED = {
    DATABASE_ID_METADATA_KEY: DATABASE_ID,
    ASSET_ID_METADATA_KEY: ASSET_ID,
    VAMS_CHANGE_SOURCE_METADATA_KEY: VAMS_CHANGE_SOURCE_DIRECT,
}


@pytest.mark.unit
class TestFolderMarkerMetadataHelper:

    def test_carries_exactly_the_three_stamp_keys(self):
        assert folder_marker_metadata(DATABASE_ID, ASSET_ID) == EXPECTED


@pytest.mark.unit
class TestCreateAssetPrefixMarker:

    def test_prefix_marker_is_written_with_the_stamp_metadata(self):
        m = _load_create_asset(fresh=True)
        s3 = MagicMock()
        m.s3_client = s3

        m.create_prefix_folder("bucket", f"{ASSET_ID}/", DATABASE_ID, ASSET_ID)

        kwargs = s3.put_object.call_args.kwargs
        assert kwargs["Key"] == f"{ASSET_ID}/"
        assert kwargs["Metadata"] == EXPECTED
        # The cross-account ownership grant is kept.
        assert kwargs["ACL"] == "bucket-owner-full-control"


@pytest.mark.unit
class TestCreateFolderMarker:

    def test_folder_is_written_with_the_asset_stamp_metadata(self, monkeypatch):
        af, s3 = key_length._wire_file_ops(monkeypatch)

        af.create_folder(
            key_length.DATABASE_ID, key_length.ASSET_ID,
            key_length._folder_request(key_length.SHORT_FOLDER_KEY), key_length.CLAIMS,
        )

        kwargs = s3.put_object.call_args.kwargs
        assert kwargs["Metadata"] == folder_marker_metadata(key_length.DATABASE_ID, key_length.ASSET_ID)
        assert kwargs["ACL"] == "bucket-owner-full-control"
