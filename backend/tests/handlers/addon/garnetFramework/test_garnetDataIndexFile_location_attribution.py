# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The Garnet file indexer finds a file's asset by location, not by the stamp metadata.

A file belongs to the asset whose stored location contains it in the same bucket: the record's binding,
the location index, then before the index is ready the stamp and the first folder, each checked against
the bucket and the location. Single-file locations follow their rule.
"""

import sys
from unittest.mock import MagicMock

import pytest

from backend.backend.handlers.addon.garnetFramework import garnetDataIndexFile as gf
from tests.common.fileAttributionFakes import (
    AttributionAssetTable,
    AttributionBucketsTable,
    asset_row,
    registration,
)

fa = sys.modules["common.fileAttribution"]
al = sys.modules["common.assetLocations"]

BUCKET = "asset-bucket"
REGISTRATIONS = [registration("reg-1", BUCKET, "/"), registration("reg-2", "other-bucket", "/")]
CUSTOM = asset_row("db1", "custom", "reg-1", "projects/building-a/", BUCKET)
NORMAL = asset_row("db1", "a1", "reg-1", "a1/", BUCKET, indexed=False)
OTHER = asset_row("db2", "b1", "reg-1", "b1/", BUCKET, indexed=False)
SINGLE = asset_row("db2", "one", "reg-1", "projects/model.glb", BUCKET)


@pytest.fixture
def run(monkeypatch):
    """Run handle_s3_notification on one ObjectCreated record; returns the (databaseId, assetId,
    relativePath) the file was sent to Garnet as, or None when nothing was sent."""
    fa.clear_registration_cache()
    al.clear_location_cache()

    def _run(rows, key, ready=False, metadata=None, binding=None):
        monkeypatch.setattr(fa, "index_ready", lambda name: ready)
        monkeypatch.setattr(gf, "asset_storage_table", AttributionAssetTable(rows))
        monkeypatch.setattr(gf, "s3_asset_buckets_table", AttributionBucketsTable(REGISTRATIONS))
        monkeypatch.setattr(gf, "get_s3_object_head",
                            lambda bucket, s3_key: ({"Metadata": metadata or {}, "VersionId": "v1"}, False))
        monkeypatch.setattr(gf, "get_file_metadata", lambda *a: ({}, {}))
        sent = {}

        def convert(database_id, asset_id, relative_path, *args, **kwargs):
            sent["identity"] = (database_id, asset_id, relative_path)
            return {"id": "urn:x"}

        monkeypatch.setattr(gf, "convert_file_to_ngsi_ld", convert)
        monkeypatch.setattr(gf, "send_to_garnet_ingestion_queue", lambda entity: True)
        monkeypatch.setattr(gf, "_record_sync", MagicMock())
        record = {"eventName": "ObjectCreated:Put", "s3": {"bucket": {"name": BUCKET}, "object": {"key": key}}}
        if binding:
            record["vamsBinding"] = binding
        assert gf.handle_s3_notification(record) is True
        return sent.get("identity")
    return _run


def _binding(database_id, asset_id, bucket_id="reg-1"):
    return {"databaseId": database_id, "assetId": asset_id, "bucketId": bucket_id,
            "relativePath": "/x", "resolvedBy": "locationIndex"}


@pytest.mark.unit
class TestGarnetFileAttribution:
    def test_a_direct_write_into_a_custom_root_location_is_sent_from_its_binding(self, run):
        assert run([CUSTOM], "projects/building-a/part.stp", binding=_binding("db1", "custom")) == \
            ("db1", "custom", "/part.stp")

    def test_without_a_binding_the_index_resolves_once_ready(self, run):
        assert run([CUSTOM], "projects/building-a/sub/part.stp", ready=True) == ("db1", "custom", "/sub/part.stp")

    def test_a_normal_asset_is_sent_as_before(self, run):
        assert run([NORMAL], "a1/part.stp", metadata={"databaseid": "db1", "assetid": "a1"}) == \
            ("db1", "a1", "/part.stp")

    def test_a_file_copied_into_another_assets_folder_belongs_to_that_asset(self, run):
        assert run([NORMAL, OTHER], "b1/part.stp", metadata={"databaseid": "db1", "assetid": "a1"}) == \
            ("db2", "b1", "/part.stp")

    def test_a_stamp_naming_an_asset_that_does_not_hold_the_key_sends_nothing(self, run):
        assert run([NORMAL], "loose/part.stp", metadata={"databaseid": "db1", "assetid": "a1"}) is None

    def test_a_binding_from_another_bucket_sends_nothing(self, run):
        foreign = asset_row("db9", "z9", "reg-2", "z9/", "other-bucket")
        assert run([foreign], "z9/part.stp", ready=True, binding=_binding("db9", "z9", "reg-2")) is None

    def test_an_archived_custom_root_asset_still_resolves(self, run):
        archived = asset_row("db1", "custom", "reg-1", "projects/building-a/", BUCKET, archived=True)
        assert run([archived], "projects/building-a/part.stp", ready=True) == ("db1", "custom", "/part.stp")

    @pytest.mark.parametrize("key,path", [
        ("projects/model.glb", "/model.glb"),
        ("projects/model.glb/parts/a.stp", "/parts/a.stp"),
    ])
    def test_a_single_file_asset_sends_its_object_and_companion_files(self, run, key, path):
        assert run([SINGLE], key) == ("db2", "one", path)

    def test_a_neighbour_of_a_single_file_asset_sends_nothing(self, run):
        assert run([SINGLE], "projects/model.glb.bak", ready=True,
                   metadata={"databaseid": "db2", "assetid": "one"}) is None
