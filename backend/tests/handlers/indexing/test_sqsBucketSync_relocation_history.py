# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bucket sync writes no change-history row for a relocated version.

The v2.6 to v2.7 upgrade step `assetLocationFileToFolder` moves a single-file asset's file into its own
folder: every version is copied to a new key at the same asset path (`fileRelocate`), and the step moves
each version's file version history row to the copy's version id, with the provenance of the version it
was copied from (who uploaded it, which workflow wrote it). A row written by bucket sync for the copy
would replace that provenance with the step's, or, written first, make the step's move find its target
taken. Every other source keeps its row.
"""

from unittest.mock import MagicMock

import pytest

from common.s3MetadataKeys import (
    VAMS_CHANGE_ASSET_FILE_VERSION_FROM_METADATA_KEY,
    VAMS_CHANGE_SOURCE_FILE_MOVE,
    VAMS_CHANGE_SOURCE_METADATA_KEY,
    VAMS_CHANGE_USER_ID_METADATA_KEY,
)
from tests.handlers.indexing.test_sqsBucketSync_history import _load

# The value the upgrade step stamps (VAMS_CHANGE_SOURCE_FILE_RELOCATE).
RELOCATE = "fileRelocate"


@pytest.fixture
def sync():
    m = _load()
    saved = m.asset_file_version_history_table
    m.asset_file_version_history_table = MagicMock()
    yield m
    m.asset_file_version_history_table = saved


def _metadata(source):
    return {VAMS_CHANGE_SOURCE_METADATA_KEY: source, VAMS_CHANGE_USER_ID_METADATA_KEY: "SYSTEM_USER",
            VAMS_CHANGE_ASSET_FILE_VERSION_FROM_METADATA_KEY: "vSrc1"}


@pytest.mark.unit
class TestRelocatedVersionHistory:
    def test_a_relocated_version_gets_no_row(self, sync):
        sync.write_file_version_history("db1", "a1", "/five.glb", "vCopy1",
                                        _metadata(RELOCATE), "2026-11-26T00:32:05Z",
                                        etag='"e1"', size=10, resolved_by="locationIndex")
        sync.asset_file_version_history_table.put_item.assert_not_called()

    def test_control_a_user_move_keeps_its_row(self, sync):
        sync.write_file_version_history("db1", "a1", "/five.glb", "vMoved",
                                        _metadata(VAMS_CHANGE_SOURCE_FILE_MOVE), "2026-11-26T00:32:05Z")
        (call,) = sync.asset_file_version_history_table.put_item.call_args_list
        assert call.kwargs["Item"]["changeSource"] == VAMS_CHANGE_SOURCE_FILE_MOVE
        assert call.kwargs["Item"]["versionId"] == "vMoved"
