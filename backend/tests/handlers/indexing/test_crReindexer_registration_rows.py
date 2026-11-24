# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The file reindex walks the active registrations, one per location (#1889, #1891).

A retired registration (it left the configuration) keeps its row for the assets that name it, but no
bucket sync and no grants serve it, so walking it only fails (`ListObjectsV2` is denied). Two rows an
earlier release wrote for one bucket name and prefix are one location: it is walked once, as the row
that answers for it, and an asset of either row is reindexed. `fileEnumeration` walks the same way.
"""

import importlib.util
import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_METADATA_STORAGE_TABLE_NAME", "test-file-metadata-table")
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-buckets-table")
os.environ.setdefault("OPENSEARCH_ASSET_INDEX_SSM_PARAM", "/test/asset-index")
os.environ.setdefault("OPENSEARCH_FILE_INDEX_SSM_PARAM", "/test/file-index")
os.environ.setdefault("OPENSEARCH_ENDPOINT_SSM_PARAM", "/test/endpoint")
os.environ.setdefault("OPENSEARCH_TYPE", "provisioned")

_CR_REINDEXER_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "indexing", "crReindexer.py"
)

ACTIVE = {"bucketId": "b1", "bucketName": "bucket-one", "baseAssetsPrefix": ""}
DUPLICATE = {"bucketId": "b1d", "bucketName": "bucket-one", "baseAssetsPrefix": "",
             "duplicateOfBucketId": "b1"}
RETIRED = {"bucketId": "b9", "bucketName": "bucket-gone", "baseAssetsPrefix": "", "retired": True}


@pytest.fixture
def crReindexer():
    with patch("boto3.client", return_value=MagicMock()), patch("boto3.resource", return_value=MagicMock()):
        spec = importlib.util.spec_from_file_location("crReindexer_registration_rows",
                                                      os.path.abspath(_CR_REINDEXER_PATH))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


def _utility(module):
    return module.ReindexUtility(asset_table_name="test-asset-table", s3_buckets_table_name="test-buckets-table",
                                 assets_metadata_table_name="test-metadata-table")


@pytest.mark.unit
class TestReindexWalksActiveRegistrations:
    def test_a_retired_row_is_not_walked_and_a_location_is_walked_once(self, crReindexer):
        m = crReindexer
        walked = []

        def _process(self, bucket_name, base_prefix, dry_run, limit=None, bucket_id=None, member_bucket_ids=None):
            walked.append((bucket_name, bucket_id, member_bucket_ids))
            return {"success": 0, "failed": 0, "total": 0, "objects_scanned": 0, "errors": []}

        with patch.object(m.ReindexUtility, "_scan_s3_buckets_table", return_value=[DUPLICATE, RETIRED, ACTIVE]), \
                patch.object(m.ReindexUtility, "_process_bucket", _process):
            results = _utility(m).reindex_files(dry_run=True)

        assert walked == [("bucket-one", "b1", ["b1", "b1d"])]
        assert results["buckets_processed"] == 1
        assert results["errors"] == []

    def test_an_asset_of_the_duplicate_row_is_reindexed(self, crReindexer):
        m = crReindexer
        s3 = MagicMock()
        s3.get_paginator.return_value.paginate.return_value = [
            {"Contents": [{"Key": "a1/model.glb"}, {"Key": "a2/scan.e57"}]}]
        s3.head_object.return_value = {"Metadata": {}}
        owners = {("b1", "a1"): "db1", ("b1d", "a2"): "db2"}

        with patch.object(m, "s3_client", s3), \
                patch.object(m, "index_ready", return_value=False), \
                patch.object(m.ReindexUtility, "_scan_s3_buckets_table", return_value=[ACTIVE, DUPLICATE]), \
                patch.object(m.ReindexUtility, "_resolve_database_id",
                             side_effect=lambda bucket_id, asset_id: owners.get((bucket_id, asset_id))):
            results = _utility(m).reindex_files(dry_run=True)

        assert results["total_count"] == 2
        assert results["skipped_no_asset"] == 0
        s3.head_object.assert_not_called()

    def test_registrations_to_walk(self, crReindexer):
        walked, members = crReindexer.registrations_to_walk([DUPLICATE, RETIRED, ACTIVE, {"bucketId": "x"}])
        assert [row["bucketId"] for row in walked] == ["b1"]
        assert members == {"b1": ["b1", "b1d"]}


@pytest.mark.unit
class TestIndexOwnerMustBeTheWalkedRegistration:
    """Once the location index is ready, the owner of a key is found by bucket name. A walk of an active
    root registration lists the keys of a retired registration nested under it on the same bucket, and
    their owner (an asset of the retired registration) must not be reindexed under the root's walk."""

    def test_a_nested_retired_registrations_files_are_skipped(self, crReindexer):
        from tests.common.fileAttributionFakes import AttributionAssetTable, asset_row
        from tests.handlers.indexing.test_crReindexer_fileEnumeration_parity import FakeS3

        m = crReindexer
        root = {"bucketId": "b1", "bucketName": "bucket-one", "baseAssetsPrefix": "/"}
        nested_retired = {"bucketId": "b9", "bucketName": "bucket-one", "baseAssetsPrefix": "ext/", "retired": True}
        rows = [asset_row("db1", "a1", "b1", "a1/", "bucket-one"),
                asset_row("db9", "e1", "b9", "ext/e1/", "bucket-one")]
        captured = []

        def capture(files, timestamp):
            captured.extend(files)
            return {"success": len(files), "failed": 0, "errors": []}

        utility = _utility(m)
        with patch.object(m, "s3_client", FakeS3(["a1/m.glb", "ext/e1/n.glb"])), \
                patch.object(m, "index_ready", return_value=True), \
                patch.object(m.dynamodb_resource, "Table", return_value=AttributionAssetTable(rows)), \
                patch.object(m.ReindexUtility, "_scan_s3_buckets_table", return_value=[root, nested_retired]), \
                patch.object(utility, "_update_files_in_metadata_table", side_effect=capture):
            results = utility.reindex_files(dry_run=False)

        assert {(f["databaseId"], f["assetId"], f["relative_path"]) for f in captured} == {("db1", "a1", "/m.glb")}
        assert results["skipped_no_asset"] == 1
