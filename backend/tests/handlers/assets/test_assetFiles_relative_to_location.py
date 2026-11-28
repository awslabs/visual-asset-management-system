# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""_relative_to_location lists a key under the asset-relative path (#2065).

A folder location stored with a leading slash ('/scans/') holds its keys without it
('scans/x.txt'). The listed path must be '/x.txt', not '/scans/x.txt', or the delete
cleanup looks for metadata, attributes and version records under the wrong path.
"""

import os

import pytest

# Set env vars required by assetFiles at import time
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME", "test-asset-file-versions-table")

from backend.backend.handlers.assets import assetFiles  # noqa: F401,E402


@pytest.mark.unit
class TestRelativeToLocation:
    @pytest.mark.parametrize("base_key, key, expected", [
        ("/scans/", "scans/x.txt", "/x.txt"),
        ("scans/", "scans/x.txt", "/x.txt"),
        ("/scans/", "scans/sub/x.txt", "/sub/x.txt"),
        ("/scans/", "scans/", "/"),
        ("vams-assets/xabc/", "vams-assets/xabc/models/a.ifc", "/models/a.ifc"),
        ("file.txt", "file.txt", "/file.txt"),
        ("file.txt", "file.txt/x", "/x"),
    ])
    def test_the_listed_path_is_under_the_location(self, base_key, key, expected):
        assert assetFiles._relative_to_location(base_key, key) == expected
