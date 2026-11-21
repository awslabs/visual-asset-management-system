# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""An asset version snapshot that cannot be read is told apart from one with no files.

``get_asset_file_versions`` answers None for both by default, which its long-standing callers rely
on. A caller that must not report a read failure as an empty snapshot (zip create, which would
otherwise refuse the caller's selection with a 400) passes ``raise_errors=True`` and gets the
failure raised instead.
"""

import os

import pytest
from unittest.mock import patch

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME", "test-asset-file-versions-table")

from backend.backend.handlers.assets import assetVersions as av  # noqa: E402


@pytest.mark.unit
class TestSnapshotReadErrors:
    def test_a_failed_read_answers_none_by_default(self):
        with patch.object(av, "query_all_items", side_effect=RuntimeError("table unavailable")):
            assert av.get_asset_file_versions("db1", "asset1", "3") is None

    def test_a_failed_read_raises_when_asked(self):
        with patch.object(av, "query_all_items", side_effect=RuntimeError("table unavailable")):
            with pytest.raises(RuntimeError):
                av.get_asset_file_versions("db1", "asset1", "3", raise_errors=True)

    def test_a_snapshot_with_no_rows_is_none_either_way(self):
        with patch.object(av, "query_all_items", return_value=[]):
            assert av.get_asset_file_versions("db1", "asset1", "3", raise_errors=True) is None
