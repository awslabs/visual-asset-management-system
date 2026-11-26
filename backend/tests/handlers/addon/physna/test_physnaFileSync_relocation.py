# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Physna file sync keeps its copy of a file whose new version is a relocation.

The v2.6 to v2.7 upgrade step `assetLocationFileToFolder` moves a single-file asset's file into its own
folder: every version is copied, oldest first, to a new key at the same asset path, stamped
`fileRelocate`. The Physna copy is keyed by that asset path and holds the same bytes, so no copy is
uploaded again (each would re-run Physna's processing). A user's move is still uploaded.
"""

import sys
from unittest.mock import patch

import pytest

from backend.backend.handlers.addon.physna import physnaCommon, physnaFileSync
from tests.common.fileAttributionFakes import (
    AttributionAssetTable,
    AttributionBucketsTable,
    asset_row,
    registration,
)

fa = sys.modules["common.fileAttribution"]
al = sys.modules["common.assetLocations"]

BUCKET = "asset-bucket"
KEY = "projects/model.glb/model.glb"
MOVED = asset_row("db1", "one", "reg-1", "projects/model.glb/", BUCKET)


@pytest.fixture
def wire(monkeypatch):
    fa.clear_registration_cache()
    al.clear_location_cache()

    def _wire(change_source):
        monkeypatch.setattr(fa, "index_ready", lambda name: True)
        monkeypatch.setattr(physnaCommon, "asset_storage_table", AttributionAssetTable([MOVED]))
        monkeypatch.setattr(physnaCommon, "s3_asset_buckets_table",
                            AttributionBucketsTable([registration("reg-1", BUCKET, "/")]))
        metadata = {"vams-changesource": change_source, "vams-changeassetfileversionfrom": "vSrc1"}
        monkeypatch.setattr(physnaFileSync, "_head_object_with_encoding_fallback",
                            lambda bucket, key: {"response": {"Metadata": metadata}, "key": key})
    return _wire


def _created():
    return {"eventName": "ObjectCreated:Copy", "s3": {"bucket": {"name": BUCKET}, "object": {"key": KEY}}}


@pytest.mark.unit
class TestRelocatedVersion:
    def test_a_relocated_version_is_not_uploaded(self, wire):
        wire("fileRelocate")
        with patch.object(physnaFileSync, "_upload_file_to_physna") as upload:
            assert physnaFileSync._handle_s3_record(_created()) is True
        upload.assert_not_called()

    def test_control_a_user_move_is_uploaded(self, wire):
        wire("fileMove")
        with patch.object(physnaFileSync, "_upload_file_to_physna", return_value=True) as upload:
            assert physnaFileSync._handle_s3_record(_created()) is True
        upload.assert_called_once()
        assert upload.call_args.args[:3] == ("db1", "one", "/model.glb")
