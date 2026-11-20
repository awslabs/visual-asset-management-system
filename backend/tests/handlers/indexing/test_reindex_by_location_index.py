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
