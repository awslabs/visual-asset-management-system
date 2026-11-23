# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Both reindex walks attribute files by the location index once it is ready.

`fileEnumeration.enumerate_latest_live_files` (vector reindex) and `crReindexer._process_bucket`
(OpenSearch reindex) read each bucket's `AssetLocationGSI` partition once, to its last page, and match
every listed key in memory to the asset whose stored location holds it (longest match), so files in a
custom root location are reindexed and no HeadObject is made for the stamp. The vector walk keeps live
assets only; the OpenSearch walk also reaches archived ones. Before the index is ready both keep the
first-folder logic.
"""

import importlib.util
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

from tests.common.fileAttributionFakes import AttributionAssetTable, asset_row
from tests.handlers.indexing.test_crReindexer_fileEnumeration_parity import FakeAssetTable, FakeS3

al = sys.modules['common.assetLocations']

os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_METADATA_STORAGE_TABLE_NAME", "test-file-metadata-table")
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-buckets-table")
os.environ.setdefault("OPENSEARCH_ASSET_INDEX_SSM_PARAM", "/test/asset-index")
os.environ.setdefault("OPENSEARCH_FILE_INDEX_SSM_PARAM", "/test/file-index")
os.environ.setdefault("OPENSEARCH_ENDPOINT_SSM_PARAM", "/test/endpoint")
os.environ.setdefault("OPENSEARCH_TYPE", "provisioned")

_ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend")
BUCKET_NAME = "bucket-one"
REGISTRATION = {"bucketId": "b1", "bucketName": BUCKET_NAME, "baseAssetsPrefix": "/"}
ROWS = [
    asset_row("db1", "custom", "b1", "projects/building-a/", BUCKET_NAME),
    asset_row("db1", "nested", "b1", "projects/building-a/annex/", BUCKET_NAME),
    asset_row("db2", "a1", "b1", "a1/", BUCKET_NAME),
    asset_row("db2", "one", "b1", "projects/model.glb", BUCKET_NAME),
    asset_row("db3", "old", "b1", "archive/old/", BUCKET_NAME, archived=True),
]
KEYS = [
    "a1/m.glb", "projects/building-a/x.obj", "projects/building-a/annex/y.obj", "projects/model.glb",
    "projects/model.glb/tex/a.png", "projects/model.glb.bak", "archive/old/z.glb", "loose/w.glb",
]
EXPECTED_LIVE = {
    ("db2", "a1", "/m.glb"), ("db1", "custom", "/x.obj"), ("db1", "nested", "/y.obj"),
    ("db2", "one", "/model.glb"), ("db2", "one", "/tex/a.png"),
}


def _load(path, name):
    with patch("boto3.client", return_value=MagicMock()), patch("boto3.resource", return_value=MagicMock()):
        spec = importlib.util.spec_from_file_location(name, os.path.abspath(path))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


@pytest.fixture
def fe():
    return _load(os.path.join(_ROOT, "common", "indexing", "fileEnumeration.py"), "fileEnumeration_by_index")


@pytest.fixture
def cr():
    return _load(os.path.join(_ROOT, "handlers", "indexing", "crReindexer.py"), "crReindexer_by_index")


def _buckets_table():
    table = MagicMock()
    table.scan.return_value = {"Items": [REGISTRATION]}
    return table


def _partition_loads(table):
    """Index reads that name no location: whole-partition loads of a bucket."""
    return [r for r in table.reads if r[0] == "index"
            and not {":k", ":p"} & set(r[2]["ExpressionAttributeValues"])]


@pytest.mark.unit
class TestVectorReindexEnumeration:
    def test_files_in_any_stored_location_are_enumerated_once_the_index_is_ready(self, fe, monkeypatch):
        monkeypatch.setattr(fe, "index_ready", lambda name: True)
        s3 = FakeS3(KEYS)
        table = AttributionAssetTable(ROWS, page_size=2)
        refs, token = fe.enumerate_latest_live_files(s3_client=s3, buckets_table=_buckets_table(), asset_table=table)
        assert token is None
        assert {(r.database_id, r.asset_id, r.relative_file_key) for r in refs} == EXPECTED_LIVE
        assert not s3.head_object.called
        # The partition was read to its last page, once for the bucket rather than once per key.
        loads = _partition_loads(table)
        assert loads and len(loads) < len(KEYS)
        assert any("ExclusiveStartKey" in r[2] for r in loads)

    def test_a_database_filter_keeps_only_that_databases_files(self, fe, monkeypatch):
        monkeypatch.setattr(fe, "index_ready", lambda name: True)
        refs, _ = fe.enumerate_latest_live_files(s3_client=FakeS3(KEYS), buckets_table=_buckets_table(),
                                                 asset_table=AttributionAssetTable(ROWS), database_id="db1")
        assert {(r.asset_id, r.relative_file_key) for r in refs} == {("custom", "/x.obj"), ("nested", "/y.obj")}

    def test_before_the_index_is_ready_the_first_folder_decides_as_before(self, fe, monkeypatch):
        monkeypatch.setattr(fe, "index_ready", lambda name: False)
        table = FakeAssetTable({("b1", "a1"): ["db2"]})
        table.name = "test-asset-table"
        refs, _ = fe.enumerate_latest_live_files(s3_client=FakeS3(KEYS), buckets_table=_buckets_table(),
                                                 asset_table=table)
        assert {(r.database_id, r.asset_id, r.relative_file_key) for r in refs} == {("db2", "a1", "/m.glb")}


@pytest.mark.unit
class TestOpenSearchReindex:
    def _walk(self, cr, monkeypatch, ready):
        monkeypatch.setattr(cr, "index_ready", lambda name: ready)
        captured = []

        def capture(files, timestamp):
            captured.extend(files)
            return {"success": len(files), "failed": 0, "errors": []}

        utility = cr.ReindexUtility("test-asset-table", "test-buckets-table", "test-file-metadata-table")
        s3 = FakeS3(KEYS)
        table = AttributionAssetTable(ROWS, page_size=2)
        with patch.object(cr, "s3_client", s3), patch.object(cr.dynamodb_resource, "Table", return_value=table), \
                patch.object(utility, "_update_files_in_metadata_table", side_effect=capture):
            results = utility._process_bucket(BUCKET_NAME, "/", dry_run=False, bucket_id="b1")
        return results, {(f["databaseId"], f["assetId"], f["relative_path"]) for f in captured}, s3

    def test_custom_root_and_archived_files_are_reindexed_without_head_object(self, cr, monkeypatch):
        results, files, s3 = self._walk(cr, monkeypatch, ready=True)
        assert files == EXPECTED_LIVE | {("db3", "old", "/z.glb")}
        assert not s3.head_object.called
        assert results["skipped_no_asset"] >= 2  # the neighbour of the single file and the loose key


# An archived asset nested in a live one (D10): the deepest stored location that holds a key owns it,
# live or archived, so the containing asset never gets the archived asset's files.
NESTED_ROWS = [
    asset_row("db1", "outer", "b1", "outer/", BUCKET_NAME),
    asset_row("db2", "annex", "b1", "outer/annex/", BUCKET_NAME, archived=True),
    asset_row("db1", "wing", "b1", "outer/wing/", BUCKET_NAME, archived=True),
    asset_row("db1", "twin-a", "b1", "outer/twin/", BUCKET_NAME, archived=True),
    asset_row("db2", "twin-b", "b1", "outer/twin/", BUCKET_NAME, archived=True),
    asset_row("db1", "site", "b1", "projects/site-b/", BUCKET_NAME),
    asset_row("db2", "phase1", "b1", "projects/site-b/phase1/", BUCKET_NAME, archived=True),
    asset_row("db3", "vault", "b1", "vault/", BUCKET_NAME, archived=True),
    asset_row("db3", "kept", "b1", "vault/kept/", BUCKET_NAME),
]
NESTED_KEYS = [
    "outer/a.glb", "outer/annex/b.glb", "outer/wing/c.glb", "outer/twin/f.glb",
    "projects/site-b/g.glb", "projects/site-b/phase1/h.glb", "vault/e.glb", "vault/kept/d.glb",
]
NESTED_EXPECTED_LIVE = {("db1", "outer", "/a.glb"), ("db1", "site", "/g.glb"), ("db3", "kept", "/d.glb")}
# The OpenSearch walk also reaches archived owners, under their live database id; the two archived rows
# on one location (`outer/twin/`) are ambiguous and attribute nothing.
NESTED_EXPECTED_ANY_STATE = NESTED_EXPECTED_LIVE | {
    ("db2", "annex", "/b.glb"), ("db1", "wing", "/c.glb"), ("db2", "phase1", "/h.glb"), ("db3", "vault", "/e.glb"),
}


def _nested_vector_walk(fe, monkeypatch, **kwargs):
    monkeypatch.setattr(fe, "index_ready", lambda name: True)
    refs, token = fe.enumerate_latest_live_files(
        s3_client=FakeS3(NESTED_KEYS), buckets_table=_buckets_table(),
        asset_table=AttributionAssetTable(NESTED_ROWS, page_size=3), **kwargs)
    assert token is None
    return {(r.database_id, r.asset_id, r.relative_file_key) for r in refs}


@pytest.mark.unit
class TestArchivedNestedOwner:
    def test_a_file_of_an_archived_asset_nested_in_a_live_one_is_enumerated_for_nobody(self, fe, monkeypatch):
        refs = _nested_vector_walk(fe, monkeypatch)
        assert refs == NESTED_EXPECTED_LIVE
        assert not {r for r in refs if r[2] in ("/annex/b.glb", "/wing/c.glb", "/twin/f.glb", "/phase1/h.glb")}

    def test_the_database_filter_does_not_reach_an_archived_nested_assets_files(self, fe, monkeypatch):
        # The containing assets are in db1; the archived nested ones in db1 and db2.
        assert _nested_vector_walk(fe, monkeypatch, database_id="db1") == {
            ("db1", "outer", "/a.glb"), ("db1", "site", "/g.glb")}

    def test_control_the_containing_asset_keeps_its_own_files(self, fe, monkeypatch):
        refs = _nested_vector_walk(fe, monkeypatch)
        assert {("db1", "outer", "/a.glb"), ("db1", "site", "/g.glb")} <= refs

    def test_control_a_live_asset_nested_in_an_archived_one_keeps_its_files(self, fe, monkeypatch):
        refs = _nested_vector_walk(fe, monkeypatch)
        assert ("db3", "kept", "/d.glb") in refs
        assert not {r for r in refs if r[1] == "vault"}

    def test_the_vector_walk_keeps_exactly_the_live_owners_the_opensearch_walk_names(self, fe, cr, monkeypatch):
        monkeypatch.setattr(cr, "index_ready", lambda name: True)
        captured = []

        def capture(files, timestamp):
            captured.extend(files)
            return {"success": len(files), "failed": 0, "errors": []}

        utility = cr.ReindexUtility("test-asset-table", "test-buckets-table", "test-file-metadata-table")
        table = AttributionAssetTable(NESTED_ROWS, page_size=3)
        with patch.object(cr, "s3_client", FakeS3(NESTED_KEYS)), \
                patch.object(cr.dynamodb_resource, "Table", return_value=table), \
                patch.object(utility, "_update_files_in_metadata_table", side_effect=capture):
            utility._process_bucket(BUCKET_NAME, "/", dry_run=False, bucket_id="b1")
        any_state = {(f["databaseId"], f["assetId"], f["relative_path"]) for f in captured}
        assert any_state == NESTED_EXPECTED_ANY_STATE
        live_assets = {(row["databaseId"], row["assetId"]) for row in NESTED_ROWS if row.get("status") != "archived"}
        assert _nested_vector_walk(fe, monkeypatch) == {f for f in any_state if f[:2] in live_assets}
