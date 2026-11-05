# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""``crReindexer._process_bucket`` and ``fileEnumeration.enumerate_latest_live_files`` agree on which files
an asset bucket holds.

The reindexer keeps its own loop (batching, dry-run, counters, the HeadObject fallback for keys that
do not resolve) but every decision about a key -- marker, reserved segment, preview object, asset id,
relative path, live database id -- comes from ``common.indexing.fileEnumeration``. With the fallback
disarmed (HeadObject returns no metadata) the two walks must yield the same (database, asset, path)
set; the delegation tests pin that the reindexer's helper methods are the shared functions.
"""

import importlib.util
import os
from unittest.mock import MagicMock, patch

import pytest
from boto3.dynamodb.conditions import ConditionExpressionBuilder

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
_ENUMERATION_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "common", "indexing", "fileEnumeration.py"
)


@pytest.fixture
def crReindexer():
    with patch("boto3.client", return_value=MagicMock()), patch("boto3.resource", return_value=MagicMock()):
        spec = importlib.util.spec_from_file_location("crReindexer_parity_under_test", os.path.abspath(_CR_REINDEXER_PATH))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


@pytest.fixture
def fe():
    spec = importlib.util.spec_from_file_location("fileEnumeration_parity_under_test", os.path.abspath(_ENUMERATION_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeS3:
    def __init__(self, keys, page_size=3):
        self.keys = sorted(keys)
        self.page_size = page_size
        self.head_object = MagicMock(return_value={"Metadata": {}})

    def get_paginator(self, name):
        return self

    def paginate(self, **kwargs):
        keys = self.keys
        prefix = kwargs.get("Prefix", "")
        keys = [k for k in keys if k.startswith(prefix)]
        start_after = kwargs.get("StartAfter")
        if start_after:
            keys = [k for k in keys if k > start_after]
        pages = [keys[i:i + self.page_size] for i in range(0, len(keys), self.page_size)] or [[]]
        return [{"Contents": [{"Key": k} for k in page]} for page in pages]


class FakeAssetTable:
    def __init__(self, rows):
        self.rows = rows

    def query(self, **kwargs):
        built = ConditionExpressionBuilder().build_expression(kwargs["KeyConditionExpression"], is_key_condition=True)
        bucket_id, asset_id = list(built.attribute_value_placeholders.values())
        return {"Items": [{"databaseId": d} for d in self.rows.get((bucket_id, asset_id), [])]}


KEYS = [
    "assets/a1/model.glb", "assets/a1/dir/", "assets/a1/dir/scan.e57", "assets/a1/model.previewFile.png",
    "assets/a1/pipelines/run/out.glb", "assets/arch/old.glb", "assets/orphan/x.glb", "assets/a2/img.png",
    "elsewhere/a1/z.glb",
]
ROWS = {("b1", "a1"): ["db1"], ("b1", "arch"): ["db1#deleted"], ("b1", "a2"): ["db2"]}
BUCKET = {"bucketId": "b1", "bucketName": "bucket-one", "baseAssetsPrefix": "/assets"}


def _reindexer_walk(m):
    """The (databaseId, assetId, relativePath) triples crReindexer hands to its metadata-touch batch."""
    captured = []

    def capture(files, timestamp):
        captured.extend(files)
        return {"success": len(files), "failed": 0, "errors": []}

    utility = m.ReindexUtility("test-asset-table", "test-buckets-table", "test-file-metadata-table")
    with patch.object(m, "s3_client", FakeS3(KEYS)), \
            patch.object(m.dynamodb_resource, "Table", return_value=FakeAssetTable(ROWS)), \
            patch.object(utility, "_update_files_in_metadata_table", side_effect=capture):
        results = utility._process_bucket("bucket-one", "/assets", dry_run=False, bucket_id="b1")
    return results, sorted((f["databaseId"], f["assetId"], f["relative_path"]) for f in captured)


@pytest.mark.unit
class TestParity:
    def test_the_two_walks_agree(self, crReindexer, fe):
        results, reindexer_files = _reindexer_walk(crReindexer)
        buckets_table = MagicMock()
        buckets_table.scan.return_value = {"Items": [BUCKET]}
        refs, token = fe.enumerate_latest_live_files(
            s3_client=FakeS3(KEYS), buckets_table=buckets_table, asset_table=FakeAssetTable(ROWS))
        assert token is None
        assert reindexer_files == sorted((r.database_id, r.asset_id, r.relative_file_key) for r in refs)
        assert reindexer_files == [
            ("db1", "a1", "/dir/scan.e57"),
            ("db1", "a1", "/model.glb"),
            ("db2", "a2", "/img.png"),
        ]

    def test_reindexer_counters_are_unchanged_by_the_delegation(self, crReindexer):
        results, _ = _reindexer_walk(crReindexer)
        assert results["total"] == 3
        assert results["success"] == 3
        assert results["skipped_excluded"] == 2          # the preview object and the pipelines/ key
        assert results["skipped_no_asset"] == 2          # the archived asset and the orphan
        # The key outside the base prefix is never listed (Prefix is passed to list_objects_v2).
        assert results["objects_scanned"] == len(KEYS) - 1


@pytest.mark.unit
class TestDelegation:
    def test_static_helpers_delegate_to_the_shared_functions(self, crReindexer):
        m = crReindexer
        assert m.ReindexUtility._extract_asset_id_from_key("assets/a1/x.glb", "assets") == "a1"
        assert m.ReindexUtility._extract_asset_id_from_key("other/a1/x.glb", "assets") is None
        assert m.ReindexUtility._is_valid_asset_id("asset1") is True
        assert m.ReindexUtility._is_valid_asset_id("bad id?") is False

    def test_resolve_database_id_uses_the_shared_resolver_and_caches(self, crReindexer):
        m = crReindexer
        table = FakeAssetTable({("b1", "a1"): ["db1#deleted", "db1"], ("b1", "arch"): ["db1#deleted"]})
        table.query = MagicMock(side_effect=table.query)
        utility = m.ReindexUtility("test-asset-table", "test-buckets-table", "test-file-metadata-table")
        with patch.object(m.dynamodb_resource, "Table", return_value=table):
            assert utility._resolve_database_id("b1", "a1") == "db1"
            assert utility._resolve_database_id("b1", "a1") == "db1"
            assert utility._resolve_database_id("b1", "arch") is None
        assert table.query.call_count == 2

    def test_scan_s3_buckets_table_pages_through_the_shared_scan(self, crReindexer):
        m = crReindexer
        table = MagicMock()
        table.scan.side_effect = [
            {"Items": [{"bucketName": "one"}], "LastEvaluatedKey": {"bucketId": "b1"}},
            {"Items": [{"bucketName": "two"}]},
        ]
        utility = m.ReindexUtility("test-asset-table", "test-buckets-table", "test-file-metadata-table")
        with patch.object(m.dynamodb_resource, "Table", return_value=table):
            rows = utility._scan_s3_buckets_table()
        assert [r["bucketName"] for r in rows] == ["one", "two"]
        assert table.scan.call_args_list[1].kwargs == {"ExclusiveStartKey": {"bucketId": "b1"}}

    def test_module_imports_the_shared_helpers(self, crReindexer):
        for name in ("extract_asset_id_from_key", "is_excluded_s3_key", "is_folder_marker", "is_valid_asset_id",
                     "list_objects_kwargs", "normalize_base_prefix", "relative_file_key",
                     "resolve_live_database_id", "scan_bucket_registrations"):
            assert getattr(crReindexer, name).__module__ == "common.indexing.fileEnumeration", name
