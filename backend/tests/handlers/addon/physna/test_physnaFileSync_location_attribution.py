# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Physna file sync finds a file's asset by location, not by the stamp metadata.

A file belongs to the asset whose stored location contains it in the same bucket: the record's binding,
the location index, then before the index is ready the stamp and the first folder, each checked against
the bucket and the location. Both resolvers (object present, and object gone on ObjectRemoved) and the
S3 existence check follow it, single-file locations included.
"""

import sys

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
REGISTRATIONS = [registration("reg-1", BUCKET, "/"), registration("reg-2", "other-bucket", "/")]
CUSTOM = asset_row("db1", "custom", "reg-1", "projects/building-a/", BUCKET)
NORMAL = asset_row("db1", "a1", "reg-1", "a1/", BUCKET, indexed=False)
OTHER = asset_row("db2", "b1", "reg-1", "b1/", BUCKET, indexed=False)
SINGLE = asset_row("db2", "one", "reg-1", "projects/model.glb", BUCKET)


@pytest.fixture
def wire(monkeypatch):
    fa.clear_registration_cache()
    al.clear_location_cache()

    def _wire(rows, ready=False, metadata=None):
        monkeypatch.setattr(fa, "index_ready", lambda name: ready)
        monkeypatch.setattr(physnaCommon, "asset_storage_table", AttributionAssetTable(rows))
        monkeypatch.setattr(physnaCommon, "s3_asset_buckets_table", AttributionBucketsTable(REGISTRATIONS))
        monkeypatch.setattr(physnaFileSync, "_head_object_with_encoding_fallback",
                            lambda bucket, key: {"response": {"Metadata": metadata or {}}, "key": key})
    return _wire


def _record(key, binding=None):
    record = {"eventName": "ObjectCreated:Put", "s3": {"bucket": {"name": BUCKET}, "object": {"key": key}}}
    if binding:
        record["vamsBinding"] = binding
    return record


def _binding(database_id, asset_id, bucket_id="reg-1"):
    return {"databaseId": database_id, "assetId": asset_id, "bucketId": bucket_id,
            "relativePath": "/x", "resolvedBy": "locationIndex"}


def _identity(resolved):
    return resolved and (resolved["databaseId"], resolved["assetId"], resolved["relativePath"])


@pytest.mark.unit
class TestPresentObjects:
    def test_a_direct_write_into_a_custom_root_location_resolves_from_its_binding(self, wire):
        wire([CUSTOM])
        key = "projects/building-a/part.step"
        resolved = physnaFileSync._resolve_asset_from_s3_event(BUCKET, key, _record(key, _binding("db1", "custom")))
        assert _identity(resolved) == ("db1", "custom", "/part.step")

    def test_without_a_binding_the_index_resolves_once_ready(self, wire):
        wire([CUSTOM], ready=True)
        key = "projects/building-a/sub/part.step"
        assert _identity(physnaFileSync._resolve_asset_from_s3_event(BUCKET, key, _record(key))) == \
            ("db1", "custom", "/sub/part.step")

    def test_before_the_index_is_ready_a_normal_asset_resolves_as_before(self, wire):
        wire([NORMAL], metadata={"databaseid": "db1", "assetid": "a1"})
        key = "a1/part.step"
        assert _identity(physnaFileSync._resolve_asset_from_s3_event(BUCKET, key, _record(key))) == \
            ("db1", "a1", "/part.step")

    def test_a_file_copied_into_another_assets_folder_belongs_to_that_asset(self, wire):
        wire([NORMAL, OTHER], metadata={"databaseid": "db1", "assetid": "a1"})
        key = "b1/part.step"
        assert _identity(physnaFileSync._resolve_asset_from_s3_event(BUCKET, key, _record(key))) == \
            ("db2", "b1", "/part.step")

    def test_a_stamp_naming_an_asset_that_does_not_hold_the_key_is_not_used(self, wire):
        wire([NORMAL], metadata={"databaseid": "db1", "assetid": "a1"})
        key = "loose/part.step"
        assert physnaFileSync._resolve_asset_from_s3_event(BUCKET, key, _record(key)) is None

    def test_a_binding_to_a_registration_of_another_bucket_is_not_used(self, wire):
        foreign = asset_row("db9", "z9", "reg-2", "z9/", "other-bucket")
        wire([foreign], ready=True)
        key = "z9/part.step"
        assert physnaFileSync._resolve_asset_from_s3_event(
            BUCKET, key, _record(key, _binding("db9", "z9", "reg-2"))) is None

    @pytest.mark.parametrize("key,path", [
        ("projects/model.glb", "/model.glb"),
        ("projects/model.glb/parts/a.step", "/parts/a.step"),
    ])
    def test_a_single_file_asset_syncs_its_object_and_companion_files(self, wire, key, path):
        wire([SINGLE])
        assert _identity(physnaFileSync._resolve_asset_from_s3_event(BUCKET, key, _record(key))) == \
            ("db2", "one", path)

    def test_a_neighbour_of_a_single_file_asset_is_not_synced_under_it(self, wire):
        wire([SINGLE], ready=True, metadata={"databaseid": "db2", "assetid": "one"})
        key = "projects/model.glb.bak"
        assert physnaFileSync._resolve_asset_from_s3_event(BUCKET, key, _record(key)) is None


@pytest.mark.unit
class TestRemovedObjects:
    def test_a_removed_custom_root_file_of_an_archived_asset_resolves(self, wire):
        archived = asset_row("db1", "custom", "reg-1", "projects/building-a/", BUCKET, archived=True)
        wire([archived], ready=True)
        resolved = physnaFileSync._resolve_asset_from_s3_key_without_metadata(
            BUCKET, "projects/building-a/part.step")
        assert _identity(resolved) == ("db1", "custom", "/part.step")

    def test_a_removed_key_outside_its_first_folders_asset_is_not_resolved(self, wire):
        elsewhere = asset_row("db1", "projects", "reg-1", "library/projects/", BUCKET, indexed=False)
        wire([elsewhere])
        assert physnaFileSync._resolve_asset_from_s3_key_without_metadata(BUCKET, "projects/part.step") is None


@pytest.mark.unit
class TestExistenceCheck:
    def test_a_single_file_assets_own_path_checks_its_object(self, wire, monkeypatch):
        wire([SINGLE])
        heads = []
        monkeypatch.setattr(physnaFileSync, "_head_object_with_encoding_fallback",
                            lambda bucket, key: heads.append(key) or {"response": {}, "key": key})
        assert physnaFileSync._s3_object_still_exists("db2", "one", "/model.glb") is True
        assert heads == ["projects/model.glb"]
