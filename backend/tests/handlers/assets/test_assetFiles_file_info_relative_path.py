# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""fileInfo returns relativePath relative to the asset's base key.

An asset's base key is not always '<assetId>/': a bucket can carry a base prefix, and an asset
created on an existing S3 location keeps that location's key. relativePath is the requested
file's path under the base key, and key stays the full S3 key.
"""

import os
from datetime import datetime
from unittest.mock import MagicMock

import pytest

# Set env vars required by assetFiles at import time
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME", "test-asset-file-versions-table")

from backend.backend.handlers.assets import assetFiles  # noqa: F401,E402

BUCKET = "asset-bucket"
CLAIMS = {"tokens": ["alice"]}


def _file_info(monkeypatch, base_key, file_path):
    from backend.backend.handlers.assets import assetFiles as af

    s3_client = MagicMock()
    s3_client.head_object.return_value = {
        "ContentLength": 12,
        "ContentType": "application/octet-stream",
        "LastModified": datetime(2026, 9, 1, 0, 0, 0),
        "ETag": '"e"',
        "Metadata": {},
    }
    # No preview objects: every listing the preview lookup makes comes back empty
    s3_client.get_paginator.return_value.paginate.return_value = []
    s3_client.list_objects_v2.return_value = {"Contents": []}
    monkeypatch.setattr(af, "s3_client", s3_client)
    monkeypatch.setattr(af, "get_asset_with_permissions",
                        lambda databaseId, assetId, op, claims: {"assetId": assetId})
    monkeypatch.setattr(af, "get_asset_s3_location", lambda asset: (BUCKET, base_key))
    monkeypatch.setattr(af, "is_file_archived", lambda bucket, key, version_id=None: False)
    return af.get_file_info("db1", "xabc", file_path, False, CLAIMS)


@pytest.mark.unit
class TestFileInfoRelativePath:
    @pytest.mark.parametrize("base_key, file_path, full_key", [
        ("vams-assets/xabc/", "/models/a.ifc", "vams-assets/xabc/models/a.ifc"),
        ("prefix/deeper/existing-location/", "/models/a.ifc", "prefix/deeper/existing-location/models/a.ifc"),
        ("xabc/", "/models/a.ifc", "xabc/models/a.ifc"),
        ("xabc/", "/a.ifc", "xabc/a.ifc"),
    ])
    def test_relative_path_is_the_path_under_the_base_key(self, monkeypatch, base_key, file_path, full_key):
        response = _file_info(monkeypatch, base_key, file_path)

        assert response.relativePath == file_path
        assert response.key == full_key

    def test_a_folder_keeps_its_trailing_slash(self, monkeypatch):
        response = _file_info(monkeypatch, "vams-assets/xabc/", "/textures/")

        assert response.relativePath == "/textures/"
        assert response.key == "vams-assets/xabc/textures/"
