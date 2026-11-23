# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A folder input of a workflow run names the locations of the assets nested in it (#1207).

The files under another asset's stored location, nested in the input asset's, are that asset's. A pipeline
that lists a folder input (the Zip Archive pipeline, or any custom one) cannot tell them apart from the
input asset's own files, so executeWorkflow writes those locations into the folder input's manifest entry
as `excludeLocations`. A single-file input does not carry it, and before the location index is ready a run
finds no nested location, as before the index.
"""

import sys
from unittest.mock import patch

import pytest

from tests.handlers.workflows.test_manifest_entry_bucket_id import (  # noqa: E402
    ENTRY_KEYS, MOD, _reading_the_manifest_section, er, ewv2,
)
from tests.common.fileAttributionFakes import (  # noqa: E402
    AttributionAssetTable, AttributionBucketsTable, asset_row, registration,
)

al = sys.modules['common.assetLocations']

BUCKET = "vams-assets"
OUTER = asset_row("db-a", "outer", "reg-1", "p/outer/", BUCKET)
# The nested assets: one in another database, one in the same database, one a single file
INNER = asset_row("db-b", "inner", "reg-1", "p/outer/inner/", BUCKET)
SAME_DB = asset_row("db-a", "same", "reg-1", "p/outer/models/kit/", BUCKET)
SINGLE = asset_row("db-b", "single", "reg-1", "p/outer/models/pump.glb", BUCKET)
ROWS = [OUTER, INNER, SAME_DB, SINGLE]


@pytest.fixture(autouse=True)
def _ready(monkeypatch):
    al.clear_nested_locations_cache()
    monkeypatch.setattr(al, "index_ready", lambda name: True)
    yield
    al.clear_nested_locations_cache()


def _entries(relative_keys, rows=ROWS):
    table = AttributionAssetTable(rows)
    selected = [{"databaseId": "db-a", "assetId": "outer", "relativeFileKey": key} for key in relative_keys]
    with patch(f"{MOD}._asset_bucket_details",
               side_effect=lambda bucket_id: {"bucketName": BUCKET, "baseAssetsPrefix": "p/"}), \
            patch(f"{MOD}._asset_table", return_value=table), \
            patch(f"{MOD}._buckets_table", return_value=AttributionBucketsTable([registration("reg-1", BUCKET, "p/")])):
        return ewv2._build_input_manifest_entries(selected, {("db-a", "outer"): OUTER}), table


@pytest.mark.unit
class TestAFolderInputNamesTheNestedLocations:
    def test_the_whole_asset_names_every_nested_location_whatever_its_database(self):
        (entry,), _ = _entries(["/"])
        assert entry["key"] == "p/outer/"
        assert entry["excludeLocations"] == ["p/outer/inner/", "p/outer/models/kit/", "p/outer/models/pump.glb"]

    def test_a_folder_names_only_the_locations_under_it(self):
        (entry,), _ = _entries(["/models/"])
        assert entry["excludeLocations"] == ["p/outer/models/kit/", "p/outer/models/pump.glb"]

    def test_a_folder_with_nothing_nested_under_it_carries_no_list(self):
        (entry,), _ = _entries(["/docs/"])
        assert "excludeLocations" not in entry and set(entry) == ENTRY_KEYS

    def test_a_file_input_carries_no_list_and_does_not_look(self):
        (entry,), table = _entries(["/models/readme.txt"])
        assert "excludeLocations" not in entry and set(entry) == ENTRY_KEYS
        assert table.reads == []

    def test_an_asset_with_nothing_nested_carries_no_list(self):
        (entry,), _ = _entries(["/"], rows=[OUTER])
        assert "excludeLocations" not in entry

    def test_before_the_index_is_ready_a_run_finds_no_nested_location(self, monkeypatch):
        monkeypatch.setattr(al, "index_ready", lambda name: False)
        (entry,), table = _entries(["/"])
        assert "excludeLocations" not in entry
        assert table.reads == []

    def test_the_entry_helper_writes_the_list_only_when_it_is_not_empty(self):
        assert "excludeLocations" not in er.build_manifest_entry("/", "bkt", "x/", exclude_locations=[])
        assert er.build_manifest_entry("/", "bkt", "x/", exclude_locations=["x/b/", "x/a/"])[
            "excludeLocations"] == ["x/a/", "x/b/"]


@pytest.mark.unit
class TestTheGuideDocumentsTheList:
    def test_reading_the_manifest_names_the_list(self):
        assert "`excludeLocations`" in _reading_the_manifest_section()
