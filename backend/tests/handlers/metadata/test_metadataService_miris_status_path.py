# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The relative path the Miris status endpoint reduces a full object key to maps back, through the
metadata service's `validate_file_exists`, to exactly that key, for every asset root layout."""

from unittest.mock import MagicMock, patch

import pytest

from backend.backend.handlers.metadata import metadataService
from backend.backend.models.addon.miris import asset_relative_file_path

ASSET_ID = "asset-1"

# (asset root key, file's full object key)
LAYOUTS = {
    "default-bucket": (f"{ASSET_ID}/", f"{ASSET_ID}/folder/scene.usd"),
    "base-assets-prefix": (f"assets/{ASSET_ID}/", f"assets/{ASSET_ID}/folder/scene.usd"),
    "bucket-existing-key": ("projects/site-a/", "projects/site-a/sub/scene.usd"),
    "root-without-slashes": ("projects/site-a", "projects/site-a/sub/scene.usd"),
}


def _metadata_service_key(root_key, relative_path):
    """Object key the metadata service resolves for `relative_path` in an asset at `root_key`."""
    s3 = MagicMock()
    with patch.object(
        metadataService,
        "validate_asset_exists",
        return_value={"bucketId": "bucket-1", "assetLocation": {"Key": root_key}},
    ), patch.object(
        metadataService,
        "get_bucket_details",
        return_value={"bucketName": "bucket", "baseAssetsPrefix": ""},
    ), patch.object(metadataService, "s3", s3):
        assert metadataService.validate_file_exists("db-1", ASSET_ID, relative_path)
    return s3.head_object.call_args.kwargs["Key"]


@pytest.mark.unit
@pytest.mark.parametrize("layout", sorted(LAYOUTS))
def test_status_path_of_a_full_key_maps_back_to_that_key(layout):
    root_key, full_key = LAYOUTS[layout]
    relative = asset_relative_file_path("/" + full_key, ASSET_ID, root_key)
    assert relative.startswith("/")
    assert _metadata_service_key(root_key, relative) == full_key
