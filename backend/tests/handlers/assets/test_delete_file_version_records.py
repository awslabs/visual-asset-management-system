# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""`assetFiles.delete_file_version_records` removes the deleted files' version snapshot rows, and a
write it cannot make is logged as an error.

The file routes call it after the files are gone from Amazon S3. The rows live in the asset file
versions table, one per (asset version, file); an asset version lists a file from its row. The table is
a moto table with the deployment's key schema and its `databaseIdAssetIdIndex`.

A row's `fileKey` is the `relativeKey` its asset version was created with. A version built from the
asset's current files (`useLatestFiles`, the default of the web's Create version, `vamscli asset-version
create` and `sync file push --version-comment`) stores it without a leading slash (`x.txt`); a version
built from a file list stores the paths as the caller wrote them, often as the file list shows them
(`/x.txt`). One asset can hold both.
"""

import os
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME", "test-asset-file-versions-table")

from backend.backend.handlers.assets import assetFiles as af  # noqa: E402
# The file route on a versioned moto bucket holding the folder asset db1/a1 at proj/a/ (x.txt and its
# preview, sub/y.txt, sub/z.txt); the fixtures are imported so this module can drive delete_file.
from tests.handlers.assets.test_permanent_delete_s3_failures import (  # noqa: E402,F401
    CLAIMS, aux, files, s3,
)

PK = "databaseId:assetId:assetVersionId"
DELETE_FILE_VERSION_RECORDS = af.delete_file_version_records

ROWS = [
    # a1: two default versions, keys without a leading slash
    ("a1", "1", "x.txt"), ("a1", "2", "x.txt"), ("a1", "2", "sub/y.txt"), ("a1", "2", "keep.txt"),
    # a3: two versions built from a file list, keys with a leading slash
    ("a3", "1", "/x.txt"), ("a3", "2", "/x.txt"), ("a3", "2", "/keep.txt"),
    # a4: a default version, then one built from a file list
    ("a4", "1", "x.txt"), ("a4", "1", "keep.txt"), ("a4", "2", "/x.txt"), ("a4", "2", "/keep.txt"),
    # a2: another asset of the database, in both shapes
    ("a2", "1", "x.txt"), ("a2", "2", "/x.txt"),
]


def _create_versions_table(rows):
    table = boto3.resource("dynamodb", region_name="us-east-1").create_table(
        TableName="test-asset-file-versions-table",
        KeySchema=[{"AttributeName": PK, "KeyType": "HASH"}, {"AttributeName": "fileKey", "KeyType": "RANGE"}],
        AttributeDefinitions=[
            {"AttributeName": PK, "AttributeType": "S"},
            {"AttributeName": "fileKey", "AttributeType": "S"},
            {"AttributeName": "databaseId:assetId", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[{
            "IndexName": "databaseIdAssetIdIndex",
            "KeySchema": [{"AttributeName": "databaseId:assetId", "KeyType": "HASH"}],
            "Projection": {"ProjectionType": "ALL"},
        }],
        BillingMode="PAY_PER_REQUEST",
    )
    for asset, version, key in rows:
        table.put_item(Item={PK: f"db1:{asset}:{version}", "fileKey": key, "databaseId:assetId": f"db1:{asset}"})
    return table


@pytest.fixture
def versions_table(monkeypatch):
    with mock_aws():
        table = _create_versions_table(ROWS)
        monkeypatch.setattr(af, "asset_version_files_table", table)
        yield table


def _rows(table, asset=None):
    return sorted((item[PK], item["fileKey"]) for item in table.scan()["Items"]
                  if asset is None or item["databaseId:assetId"] == f"db1:{asset}")


def test_default_version_rows_are_removed_in_every_asset_version(versions_table):
    deleted = af.delete_file_version_records("db1", "a1", ["/x.txt", "sub/y.txt", "/folder/"])

    assert deleted == 3
    assert _rows(versions_table, "a1") == [("db1:a1:2", "keep.txt")]
    assert _rows(versions_table, "a2") == [("db1:a2:1", "x.txt"), ("db1:a2:2", "/x.txt")]


def test_leading_slash_rows_are_removed(versions_table):
    deleted = af.delete_file_version_records("db1", "a3", ["x.txt"])

    assert deleted == 2
    assert _rows(versions_table, "a3") == [("db1:a3:2", "/keep.txt")]


def test_a_mixed_asset_loses_each_row_by_the_key_it_is_stored_under(versions_table):
    deleted = af.delete_file_version_records("db1", "a4", ["/x.txt"])

    assert deleted == 2
    assert _rows(versions_table, "a4") == [("db1:a4:1", "keep.txt"), ("db1:a4:2", "/keep.txt")]
    assert _rows(versions_table, "a2") == [("db1:a2:1", "x.txt"), ("db1:a2:2", "/x.txt")]


def test_the_asset_root_and_folders_name_no_row(versions_table):
    assert af.delete_file_version_records("db1", "a1", ["/", "sub/", ""]) == 0
    assert len(_rows(versions_table, "a1")) == 4


def _refused(operation):
    return ClientError({"Error": {"Code": "AccessDeniedException", "Message": "not authorized"}}, operation)


def test_a_refused_write_is_logged_as_an_error_and_reports_no_row_deleted(versions_table, monkeypatch):
    refusing_table = MagicMock(wraps=versions_table)
    refusing_table.query.side_effect = versions_table.query
    refusing_table.delete_item.side_effect = _refused("DeleteItem")
    monkeypatch.setattr(af, "asset_version_files_table", refusing_table)
    logger = MagicMock()
    monkeypatch.setattr(af, "logger", logger)

    deleted = af.delete_file_version_records("db1", "a1", ["/x.txt"])

    called = [c.kwargs for c in refusing_table.delete_item.call_args_list]
    assert {"Key": {PK: "db1:a1:1", "fileKey": "x.txt"}} in called
    assert deleted == 0
    logger.exception.assert_called_once()
    assert "AccessDeniedException" in logger.exception.call_args.args[0]
    logger.warning.assert_not_called()
    # The rows are still there, and the file route goes on (the files are already gone from S3).
    assert ("db1:a1:1", "x.txt") in _rows(versions_table)


def test_a_write_refused_part_way_reports_the_rows_deleted_before_it(versions_table, monkeypatch):
    refusing_table = MagicMock(wraps=versions_table)
    refusing_table.query.side_effect = versions_table.query
    answers = iter([None])

    def delete_item(**kwargs):
        if next(answers, "refused") == "refused":
            raise _refused("DeleteItem")
        return versions_table.delete_item(**kwargs)

    refusing_table.delete_item.side_effect = delete_item
    monkeypatch.setattr(af, "asset_version_files_table", refusing_table)
    monkeypatch.setattr(af, "logger", MagicMock())

    assert af.delete_file_version_records("db1", "a1", ["/x.txt"]) == 1
    assert len([row for row in _rows(versions_table, "a1") if row[1] == "x.txt"]) == 1


def test_each_row_goes_through_its_own_delete_item_with_no_batch_write(versions_table, monkeypatch):
    """The role holds DeleteItem on this table and no BatchWriteItem: every row of a file that more
    asset versions list than one batch carries (25) goes, one DeleteItem per row."""
    for version in range(3, 33):
        versions_table.put_item(Item={PK: f"db1:a1:{version}", "fileKey": "x.txt", "databaseId:assetId": "db1:a1"})
    watched = MagicMock(wraps=versions_table)
    watched.query.side_effect = versions_table.query
    watched.delete_item.side_effect = versions_table.delete_item
    watched.batch_writer.side_effect = AssertionError("a batch write was used")
    monkeypatch.setattr(af, "asset_version_files_table", watched)

    deleted = af.delete_file_version_records("db1", "a1", ["/x.txt"])

    assert deleted == 32
    assert watched.delete_item.call_count == 32
    watched.batch_writer.assert_not_called()
    assert _rows(versions_table, "a1") == [("db1:a1:2", "keep.txt"), ("db1:a1:2", "sub/y.txt")]
    assert _rows(versions_table, "a2") == [("db1:a2:1", "x.txt"), ("db1:a2:2", "/x.txt")]


class _FakeVersionsTable:
    """The asset file versions table for row counts moto would make slow: the GSI query is paged
    (1,000 rows a page) and each DeleteItem takes a little time, so overlapping calls can be counted."""

    def __init__(self, rows, delete_seconds=0.002, refuse=None):
        self.rows = [{PK: f"db1:{asset}:{version}", "fileKey": key, "databaseId:assetId": f"db1:{asset}"}
                     for asset, version, key in rows]
        self.remaining = {(row[PK], row["fileKey"]) for row in self.rows}
        self.delete_seconds = delete_seconds
        self.refuse = refuse or (lambda key, attempt: None)
        self.lock = threading.Lock()
        self.attempts = {}
        self.calls = 0
        self.active = 0
        self.max_active = 0

    def query(self, **kwargs):
        start = kwargs.get("ExclusiveStartKey", {}).get("i", 0)
        page = self.rows[start:start + 1000]
        response = {"Items": [dict(row) for row in page]}
        if start + 1000 < len(self.rows):
            response["LastEvaluatedKey"] = {"i": start + 1000}
        return response

    def delete_item(self, Key):
        key = (Key[PK], Key["fileKey"])
        with self.lock:
            self.calls += 1
            attempt = self.attempts[key] = self.attempts.get(key, 0) + 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(self.delete_seconds)
            error = self.refuse(key, attempt)
            if error is not None:
                raise error
            with self.lock:
                self.remaining.discard(key)
        finally:
            with self.lock:
                self.active -= 1

    def batch_writer(self, *args, **kwargs):
        raise AssertionError("a batch write was used")


def _throttled(operation="DeleteItem"):
    return ClientError({"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "slow down"}},
                       operation)


def _many_rows(versions, files_per_version):
    return [("a1", str(v), f"part{f}.txt") for v in range(versions) for f in range(files_per_version)] + [("a1", "0", "keep.txt")]


@pytest.fixture
def no_waits(monkeypatch):
    """The throttle retry waits are recorded, not slept."""
    waits = []
    monkeypatch.setattr(af, "time", SimpleNamespace(monotonic=time.monotonic, sleep=waits.append), raising=False)
    return waits


def test_many_rows_are_deleted_through_delete_item_alone_a_bounded_number_at_a_time(monkeypatch):
    table = _FakeVersionsTable(_many_rows(versions=300, files_per_version=4))
    monkeypatch.setattr(af, "asset_version_files_table", table)
    monkeypatch.setattr(af, "_invocation_context", None, raising=False)

    deleted = af.delete_file_version_records("db1", "a1", [f"/part{f}.txt" for f in range(4)])

    assert deleted == 1200
    assert table.calls == 1200
    assert table.remaining == {("db1:a1:0", "keep.txt")}
    assert table.max_active > 1
    assert table.max_active <= af.VERSION_ROWS_DELETE_WORKERS


def test_a_throttled_delete_is_retried_and_the_row_goes(versions_table, monkeypatch, no_waits):
    throttled = {}
    watched = MagicMock(wraps=versions_table)
    watched.query.side_effect = versions_table.query
    watched.batch_writer.side_effect = AssertionError("a batch write was used")

    def delete_item(**kwargs):
        key = (kwargs["Key"][PK], kwargs["Key"]["fileKey"])
        throttled[key] = throttled.get(key, 0) + 1
        if throttled[key] <= 2:
            raise _throttled()
        return versions_table.delete_item(**kwargs)

    watched.delete_item.side_effect = delete_item
    monkeypatch.setattr(af, "asset_version_files_table", watched)
    logger = MagicMock()
    monkeypatch.setattr(af, "logger", logger)

    deleted = af.delete_file_version_records("db1", "a1", ["/x.txt"])

    assert deleted == 2
    assert throttled == {("db1:a1:1", "x.txt"): 3, ("db1:a1:2", "x.txt"): 3}
    assert sorted(no_waits) == [0.5, 0.5, 1.0, 1.0]
    assert _rows(versions_table, "a1") == [("db1:a1:2", "keep.txt"), ("db1:a1:2", "sub/y.txt")]
    logger.exception.assert_not_called()
    logger.error.assert_not_called()


def test_a_row_still_throttled_after_its_retries_stops_the_delete_and_the_count_is_what_went(
        monkeypatch, no_waits):
    stuck = ("db1:a1:0", "part0.txt")
    table = _FakeVersionsTable(_many_rows(versions=100, files_per_version=4), delete_seconds=0.001,
                               refuse=lambda key, attempt: _throttled() if key == stuck else None)
    monkeypatch.setattr(af, "asset_version_files_table", table)
    monkeypatch.setattr(af, "_invocation_context", None, raising=False)
    logger = MagicMock()
    monkeypatch.setattr(af, "logger", logger)

    deleted = af.delete_file_version_records("db1", "a1", [f"/part{f}.txt" for f in range(4)])

    gone = 401 - len(table.remaining)
    assert deleted == gone
    assert 0 < deleted < 400
    assert table.attempts[stuck] == af.VERSION_ROW_THROTTLE_RETRIES + 1
    assert table.calls - table.attempts[stuck] < 400
    logger.exception.assert_called_once()
    assert "ProvisionedThroughputExceededException" in logger.exception.call_args.args[0]
    logger.error.assert_called_once()
    assert f"{deleted} deleted, {400 - deleted} left listed" in logger.error.call_args.args[0]


def test_no_row_delete_starts_once_the_invocation_is_near_its_time_limit(monkeypatch):
    table = _FakeVersionsTable(_many_rows(versions=10, files_per_version=4))
    monkeypatch.setattr(af, "asset_version_files_table", table)
    monkeypatch.setattr(af, "_invocation_context", SimpleNamespace(get_remaining_time_in_millis=lambda: 30_000),
                        raising=False)
    logger = MagicMock()
    monkeypatch.setattr(af, "logger", logger)

    deleted = af.delete_file_version_records("db1", "a1", ["/part0.txt", "/part1.txt", "/part2.txt", "/part3.txt"])

    assert deleted == 0
    assert table.calls == 0
    logger.error.assert_called_once()
    message = logger.error.call_args.args[0]
    assert "time limit" in message and "0 deleted, 40 left listed" in message and "4 file(s)" in message
    logger.exception.assert_not_called()


def test_the_delete_stops_part_way_at_the_time_limit_and_reports_the_rows_deleted(monkeypatch):
    table = _FakeVersionsTable(_many_rows(versions=50, files_per_version=4), delete_seconds=0)
    monkeypatch.setattr(af, "asset_version_files_table", table)
    monkeypatch.setattr(af, "_version_rows_delete_deadline", lambda: 100.0, raising=False)
    ticks = {"n": 0}

    def monotonic():
        ticks["n"] += 1
        return 50.0 if ticks["n"] <= 30 else 150.0

    monkeypatch.setattr(af, "time", SimpleNamespace(monotonic=monotonic, sleep=time.sleep), raising=False)
    logger = MagicMock()
    monkeypatch.setattr(af, "logger", logger)

    deleted = af.delete_file_version_records("db1", "a1", [f"/part{f}.txt" for f in range(4)])

    assert deleted == 30
    assert table.calls == 30
    assert len(table.remaining) == 200 - 30 + 1
    assert "because the invocation's time limit was near: 30 deleted, 170 left listed" in logger.error.call_args.args[0]
    logger.exception.assert_not_called()


def test_the_time_limit_comes_from_the_recorded_lambda_context(monkeypatch):
    monkeypatch.setattr(af, "_invocation_context", None, raising=False)
    assert af._version_rows_delete_deadline() is None

    af.record_invocation_context({})
    assert af._version_rows_delete_deadline() is None

    af.record_invocation_context(SimpleNamespace(get_remaining_time_in_millis=lambda: 900_000))
    left = af._version_rows_delete_deadline() - time.monotonic()
    assert 900 - af.VERSION_ROWS_DELETE_TIME_MARGIN_SECONDS - 5 < left <= 900 - af.VERSION_ROWS_DELETE_TIME_MARGIN_SECONDS


ROUTE_ROWS = [
    ("a1", "1", "x.txt"), ("a1", "1", "x.txt.previewFile.png"), ("a1", "1", "sub/y.txt"),
    ("a1", "2", "x.txt"), ("a1", "2", "/sub/y.txt"), ("a1", "2", "/sub/z.txt"),
]


@pytest.fixture
def route(files, monkeypatch):
    """The file route with the real version-row cleanup and a moto versions table (in the files' moto)."""
    table = _create_versions_table(ROUTE_ROWS)
    monkeypatch.setattr(af, "asset_version_files_table", table)
    monkeypatch.setattr(af, "delete_file_version_records", DELETE_FILE_VERSION_RECORDS)
    files.versions_table = table
    return files


def test_a_prefix_delete_removes_the_deleted_files_rows_in_both_shapes(route):
    response = af.delete_file("db1", "a1", "/sub/", True, True, CLAIMS)

    assert response.success is True
    assert sorted(response.affectedFiles) == ["sub/y.txt", "sub/z.txt"]
    assert _rows(route.versions_table) == [
        ("db1:a1:1", "x.txt"), ("db1:a1:1", "x.txt.previewFile.png"), ("db1:a1:2", "x.txt")]


def test_a_single_file_delete_removes_the_files_and_its_previews_rows(route):
    response = af.delete_file("db1", "a1", "/x.txt", False, True, CLAIMS)

    assert response.success is True
    assert _rows(route.versions_table) == [
        ("db1:a1:1", "sub/y.txt"), ("db1:a1:2", "/sub/y.txt"), ("db1:a1:2", "/sub/z.txt")]
