# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The bucket walk that says which files currently live in the asset buckets.

``list_objects_v2`` lists current versions only, so a file behind a delete marker is never listed; the
database id resolves through the asset table's BucketIdGSI and a row in the ``#deleted`` partition does
not resolve, so files of archived assets are excluded by construction. The walk is resumable: when the
caller's clock runs low it stops at a page boundary and returns a token that names the bucket and the
last key handled.

The module is loaded by file path: the root conftest registers it under ``common.indexing`` for the
handlers, and loading it here under its own name keeps these tests independent of that registration.
"""

import importlib.util
import json
import os
from unittest.mock import MagicMock

import pytest
from boto3.dynamodb.conditions import ConditionExpressionBuilder

from backend.tests.pagingStub import Pager

_MODULE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "common", "indexing", "fileEnumeration.py"
)


@pytest.fixture
def fe():
    spec = importlib.util.spec_from_file_location("fileEnumeration_under_test", os.path.abspath(_MODULE_PATH))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeS3:
    """``list_objects_v2`` pages per bucket in key order, honouring Prefix and StartAfter."""

    def __init__(self, keys_by_bucket, page_size=2):
        self.keys_by_bucket = {b: sorted(keys) for b, keys in keys_by_bucket.items()}
        self.page_size = page_size
        self.paginate_calls = []

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return self

    def paginate(self, **kwargs):
        self.paginate_calls.append(kwargs)
        keys = self.keys_by_bucket[kwargs["Bucket"]]
        prefix = kwargs.get("Prefix", "")
        keys = [k for k in keys if k.startswith(prefix)]
        start_after = kwargs.get("StartAfter")
        if start_after:
            keys = [k for k in keys if k > start_after]
        pages = [keys[i:i + self.page_size] for i in range(0, len(keys), self.page_size)] or [[]]
        return [{"Contents": [{"Key": k} for k in page]} for page in pages]


class FakeAssetTable:
    """BucketIdGSI rows keyed by (bucketId, assetId); a ``#deleted`` database id models an archived asset."""

    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    def query(self, **kwargs):
        assert kwargs["IndexName"] == "BucketIdGSI"
        built = ConditionExpressionBuilder().build_expression(
            kwargs["KeyConditionExpression"], is_key_condition=True
        )
        bucket_id, asset_id = list(built.attribute_value_placeholders.values())
        self.queries.append((bucket_id, asset_id))
        return {
            "Items": [
                {"databaseId": database_id, "assetId": asset_id, "bucketId": bucket_id}
                for database_id in self.rows.get((bucket_id, asset_id), [])
            ]
        }


def _buckets_table(*rows):
    table = MagicMock()
    pager = Pager(*[
        {"Items": [row], **({"LastEvaluatedKey": {"bucketId": row["bucketId"]}} if i < len(rows) - 1 else {})}
        for i, row in enumerate(rows)
    ], name="buckets scan")
    table.scan.side_effect = pager
    table.pager = pager
    return table


BUCKET_ONE = {"bucketId": "b1", "bucketName": "bucket-one", "baseAssetsPrefix": ""}
BUCKET_TWO = {"bucketId": "b2", "bucketName": "bucket-two", "baseAssetsPrefix": "/assets"}


@pytest.mark.unit
class TestKeyHelpers:
    def test_normalize_base_prefix(self, fe):
        assert fe.normalize_base_prefix("/assets") == "assets"
        assert fe.normalize_base_prefix("assets") == "assets"
        assert fe.normalize_base_prefix("") == ""
        assert fe.normalize_base_prefix(None) == ""

    def test_folder_marker(self, fe):
        assert fe.is_folder_marker("a1/dir/") is True
        assert fe.is_folder_marker("a1/dir/model.glb") is False

    @pytest.mark.parametrize("key, excluded", [
        ("a1/model.glb", False),
        ("a1/model.previewFile.png", True),
        ("a1/pipelines/run/out.glb", True),
        ("a1/previews/thumb.png", True),
        ("a1/previews.txt", False),          # a segment must equal a reserved name; substrings do not count
        ("a1/temp-uploads/part", True),
    ])
    def test_excluded_keys(self, fe, key, excluded):
        assert fe.is_excluded_s3_key(key) is excluded

    def test_asset_id_from_key(self, fe):
        assert fe.extract_asset_id_from_key("a1/dir/model.glb", "") == "a1"
        assert fe.extract_asset_id_from_key("a1/dir/model.glb", "/") == "a1"
        assert fe.extract_asset_id_from_key("assets/a1/model.glb", "assets") == "a1"
        assert fe.extract_asset_id_from_key("assets/a1/model.glb", "assets/") == "a1"
        assert fe.extract_asset_id_from_key("other/a1/model.glb", "assets") is None
        assert fe.extract_asset_id_from_key("/leading.glb", "") is None

    def test_valid_asset_id_uses_the_real_validator(self, fe):
        assert fe.is_valid_asset_id("asset1") is True
        # "?" is one of the characters the ASSET_ID pattern forbids; "!" is not, so it is no test.
        assert fe.is_valid_asset_id("bad id?") is False

    def test_relative_file_key(self, fe):
        assert fe.relative_file_key("assets/a1/dir/model.glb", "assets", "a1") == "/dir/model.glb"
        assert fe.relative_file_key("a1/dir/model.glb", "", "a1") == "/dir/model.glb"
        assert fe.relative_file_key("a1/model.glb", "", "a1") == "/model.glb"

    def test_list_objects_kwargs(self, fe):
        assert fe.list_objects_kwargs("b", "") == {"Bucket": "b"}
        assert fe.list_objects_kwargs("b", "/") == {"Bucket": "b"}
        assert fe.list_objects_kwargs("b", "assets") == {"Bucket": "b", "Prefix": "assets"}
        assert fe.list_objects_kwargs("b", "assets", "assets/a1/x") == {
            "Bucket": "b", "Prefix": "assets", "StartAfter": "assets/a1/x"}

    def test_continuation_round_trip(self, fe):
        token = fe.encode_continuation("bucket-one", "a1/model.glb")
        assert json.loads(token) == {"bucket": "bucket-one", "startAfter": "a1/model.glb"}
        assert fe.decode_continuation(token) == ("bucket-one", "a1/model.glb")
        assert fe.decode_continuation(None) == (None, None)


@pytest.mark.unit
class TestResolveLiveDatabaseId:
    def test_live_row_resolves(self, fe):
        table = FakeAssetTable({("b1", "a1"): ["db1"]})
        assert fe.resolve_live_database_id(table, "b1", "a1") == "db1"

    def test_archived_row_alone_does_not_resolve(self, fe):
        table = FakeAssetTable({("b1", "a1"): ["db1#deleted"]})
        assert fe.resolve_live_database_id(table, "b1", "a1") is None

    def test_live_row_wins_over_an_archived_one(self, fe):
        table = FakeAssetTable({("b1", "a1"): ["db1#deleted", "db1"]})
        assert fe.resolve_live_database_id(table, "b1", "a1") == "db1"

    def test_missing_ids_do_not_query(self, fe):
        table = FakeAssetTable({})
        assert fe.resolve_live_database_id(table, None, "a1") is None
        assert fe.resolve_live_database_id(table, "b1", None) is None
        assert table.queries == []


@pytest.mark.unit
class TestScanBucketRegistrations:
    def test_pages_to_exhaustion(self, fe):
        table = _buckets_table(BUCKET_ONE, BUCKET_TWO)
        rows = fe.scan_bucket_registrations(table)
        assert [r["bucketName"] for r in rows] == ["bucket-one", "bucket-two"]
        table.pager.assert_paged_to_exhaustion()


def _run(fe, s3, assets, buckets, **kwargs):
    return fe.enumerate_latest_live_files(
        s3_client=s3, buckets_table=_buckets_table(*buckets), asset_table=assets, **kwargs)


@pytest.mark.unit
class TestEnumerateLatestLiveFiles:
    KEYS = {
        "bucket-one": [
            "a1/model.glb", "a1/dir/", "a1/dir/scan.e57", "a1/model.previewFile.png",
            "a1/pipelines/run/out.glb", "arch/old.glb", "orphan/x.glb", "bad id?/y.glb",
        ],
        "bucket-two": ["assets/a2/img.png", "assets/a2/notes.txt", "outside/a2/z.glb"],
    }
    ROWS = {("b1", "a1"): ["db1"], ("b1", "arch"): ["db1#deleted"], ("b2", "a2"): ["db2"]}

    def test_only_live_asset_files_are_returned(self, fe):
        refs, token = _run(fe, FakeS3(self.KEYS), FakeAssetTable(self.ROWS), [BUCKET_ONE, BUCKET_TWO])
        assert token is None
        assert sorted((r.database_id, r.asset_id, r.relative_file_key, r.bucket_name, r.base_assets_prefix, r.s3_key) for r in refs) == [
            ("db1", "a1", "/dir/scan.e57", "bucket-one", "", "a1/dir/scan.e57"),
            ("db1", "a1", "/model.glb", "bucket-one", "", "a1/model.glb"),
            ("db2", "a2", "/img.png", "bucket-two", "assets", "assets/a2/img.png"),
            ("db2", "a2", "/notes.txt", "bucket-two", "assets", "assets/a2/notes.txt"),
        ]

    def test_database_id_is_resolved_once_per_asset(self, fe):
        assets = FakeAssetTable(self.ROWS)
        _run(fe, FakeS3(self.KEYS), assets, [BUCKET_ONE, BUCKET_TWO])
        assert sorted(assets.queries) == [("b1", "a1"), ("b1", "arch"), ("b1", "orphan"), ("b2", "a2")]

    def test_prefix_is_passed_only_for_buckets_that_have_one(self, fe):
        s3 = FakeS3(self.KEYS)
        _run(fe, s3, FakeAssetTable(self.ROWS), [BUCKET_ONE, BUCKET_TWO])
        assert s3.paginate_calls == [{"Bucket": "bucket-one"}, {"Bucket": "bucket-two", "Prefix": "assets"}]

    def test_database_filter_keeps_that_database_only(self, fe):
        refs, _ = _run(fe, FakeS3(self.KEYS), FakeAssetTable(self.ROWS), [BUCKET_ONE, BUCKET_TWO], database_id="db2")
        assert {r.database_id for r in refs} == {"db2"}
        assert len(refs) == 2

    def test_no_head_object_is_issued(self, fe):
        s3 = FakeS3(self.KEYS)
        s3.head_object = MagicMock()
        _run(fe, s3, FakeAssetTable(self.ROWS), [BUCKET_ONE, BUCKET_TWO])
        assert s3.head_object.call_args_list == []

    def test_time_guard_returns_a_token_at_a_page_boundary_and_resumes(self, fe):
        clock = iter([100000, 1000, 1000, 1000, 1000, 1000])
        first, token = _run(
            fe, FakeS3(self.KEYS, page_size=2), FakeAssetTable(self.ROWS), [BUCKET_ONE, BUCKET_TWO],
            time_remaining_fn=lambda: next(clock), min_remaining_ms=90000)
        assert token is not None
        bucket, start_after = fe.decode_continuation(token)
        assert bucket == "bucket-one"
        # Pages of two: the guard fires after the second page, whose last key is the fourth sorted key.
        assert start_after == sorted(self.KEYS["bucket-one"])[3]
        assert [r.s3_key for r in first] == ["a1/dir/scan.e57", "a1/model.glb"]

        s3 = FakeS3(self.KEYS, page_size=2)
        rest, token2 = _run(fe, s3, FakeAssetTable(self.ROWS), [BUCKET_ONE, BUCKET_TWO], start_after=token)
        assert token2 is None
        assert s3.paginate_calls[0] == {"Bucket": "bucket-one", "StartAfter": start_after}
        full, _ = _run(fe, FakeS3(self.KEYS), FakeAssetTable(self.ROWS), [BUCKET_ONE, BUCKET_TWO])
        assert sorted(r.s3_key for r in first + rest) == sorted(r.s3_key for r in full)

    def test_a_token_for_a_later_bucket_skips_earlier_buckets(self, fe):
        s3 = FakeS3(self.KEYS)
        token = fe.encode_continuation("bucket-two", "assets/a2/img.png")
        refs, _ = _run(fe, s3, FakeAssetTable(self.ROWS), [BUCKET_ONE, BUCKET_TWO], start_after=token)
        assert [c["Bucket"] for c in s3.paginate_calls] == ["bucket-two"]
        assert [r.s3_key for r in refs] == ["assets/a2/notes.txt"]

    def test_no_clock_means_no_guard(self, fe):
        _, token = _run(fe, FakeS3(self.KEYS), FakeAssetTable(self.ROWS), [BUCKET_ONE, BUCKET_TWO])
        assert token is None

    def test_bucket_rows_without_a_name_are_skipped(self, fe):
        refs, _ = _run(fe, FakeS3(self.KEYS), FakeAssetTable(self.ROWS), [{"bucketId": "bx"}, BUCKET_ONE])
        assert {r.bucket_name for r in refs} == {"bucket-one"}
