# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The Garnet file indexer sends no entity for a relocated version's ObjectCreated record.

The v2.6 to v2.7 upgrade step `assetLocationFileToFolder` moves a single-file asset's file into its own
folder: every version is copied, oldest first, to a new key at the same asset path, stamped `fileRelocate`.
The entity id is `urn:vams:file:{databaseId}:{assetId}:{path}`, which the move does not change. The step's
search re-index of the path (a touch record on the file metadata table) reaches Garnet through the file
metadata stream and sends the entity once, with the new key and the newest version, so the S3 records of the
copies send nothing. A delete marker on a relocated file (an archived file stays archived) is still sent,
and a user's move is sent as before.
"""

import sys
from unittest.mock import MagicMock

import pytest

from backend.backend.handlers.addon.garnetFramework import garnetDataIndexFile as gf
from tests.common.fileAttributionFakes import AttributionAssetTable, AttributionBucketsTable, asset_row, registration

fa = sys.modules["common.fileAttribution"]
al = sys.modules["common.assetLocations"]

BUCKET = "asset-bucket"
KEY = "projects/model.glb/model.glb"
MOVED = asset_row("db1", "one", "reg-1", "projects/model.glb/", BUCKET)
# The value the upgrade step stamps (VAMS_CHANGE_SOURCE_FILE_RELOCATE).
RELOCATE = "fileRelocate"


@pytest.fixture
def sent(monkeypatch):
    """Run handle_s3_notification on one record of the moved file; returns the entities sent to Garnet."""
    fa.clear_registration_cache()
    al.clear_location_cache()
    monkeypatch.setattr(fa, "index_ready", lambda name: True)
    monkeypatch.setattr(gf, "asset_storage_table", AttributionAssetTable([MOVED]))
    monkeypatch.setattr(gf, "s3_asset_buckets_table", AttributionBucketsTable([registration("reg-1", BUCKET, "/")]))
    monkeypatch.setattr(gf, "get_bucket_details", lambda bucket_id: {"bucketId": "reg-1", "bucketName": BUCKET,
                                                                      "baseAssetsPrefix": "/"})
    monkeypatch.setattr(gf, "get_file_metadata", lambda *a: ({}, {}))
    monkeypatch.setattr(gf, "_record_sync", MagicMock())
    entities = []
    monkeypatch.setattr(gf, "send_to_garnet_ingestion_queue", lambda entity: entities.append(entity) or True)

    def _run(change_source, event_name="ObjectCreated:Copy", archived=False):
        head = {"Metadata": {"vams-changesource": change_source, "vams-changeassetfileversionfrom": "vSrc1"},
                "VersionId": "vCopy3", "ContentLength": 3}
        monkeypatch.setattr(gf, "get_s3_object_head", lambda bucket, s3_key: (head, archived))
        record = {"eventName": event_name, "s3": {"bucket": {"name": BUCKET}, "object": {"key": KEY}}}
        assert gf.handle_s3_notification(record) is True
        return entities
    return _run


@pytest.mark.unit
class TestRelocatedVersion:
    def test_a_relocated_copy_sends_no_entity(self, sent):
        assert sent(RELOCATE) == []

    def test_control_a_user_move_is_sent(self, sent):
        (entity,) = sent("fileMove")
        assert entity["id"] == "urn:vams:file:db1:one:%2Fmodel.glb"
        assert entity["s3Key"]["value"] == KEY

    def test_control_a_delete_marker_on_a_relocated_file_is_sent_as_archived(self, sent):
        (entity,) = sent(RELOCATE, event_name="ObjectRemoved:DeleteMarkerCreated", archived=True)
        assert entity["isArchived"]["value"] is True
