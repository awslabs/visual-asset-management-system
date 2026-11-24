# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""get_asset_object_from_id reads one asset by its database and asset id (#1860).

An asset id is unique within its database only, so the lookup takes both ids and reads the asset's
own key: the live row, or with include_archived and no live row, the archived row stored under
``<databaseId>#deleted``. It never resolves an asset from its id alone (the ``assetIdGSI`` index can
hold rows of several databases for one id), so a missing database id is refused rather than read.

The table stub answers get_item from a dict and fails on query and scan, so a lookup that went back
to the index, or to a table pass, fails loudly here.
"""

import os
from types import SimpleNamespace

import pytest

# tests/conftest.py replaces the whole `common.dynamodb` module with a MagicMock (the real one
# bootstraps AWS clients at import), so load the functions under test from source instead of
# importing the package - the same approach as test_dynamodb_query_all_items.py.
_MODULE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "backend", "common", "dynamodb.py"
)

_ASSET_TABLE_NAME = "assetStorageTable"


class FakeVAMSGeneralErrorResponse(Exception):
    """Stand-in for models.common.VAMSGeneralErrorResponse."""


class FakeLogger:
    def info(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass

    def exception(self, *args, **kwargs):
        pass


def _extract_function_source(source, name):
    """Slice a single top-level function out of the module source."""
    start = source.index(f"def {name}(")
    return source[start:source.index("\ndef ", start)]


def _load():
    with open(_MODULE_PATH, encoding="utf-8") as f:
        source = f.read()
    namespace = {
        "VAMSGeneralErrorResponse": FakeVAMSGeneralErrorResponse,
        "ResourceKeys": SimpleNamespace(ASSET_STORAGE_TABLE="assetStorageTable"),
        "get_table_name": lambda key: _ASSET_TABLE_NAME,
        "logger": FakeLogger(),
        "dynamodb": None,
        "_asset_table": None,
        "ARCHIVED_ASSET_DATABASE_SUFFIX": "#deleted",
    }
    for function_name in ("_asset_table_resource", "get_asset_record", "get_asset_object_from_id"):
        exec(compile(_extract_function_source(source, function_name), _MODULE_PATH, "exec"), namespace)
    return namespace["get_asset_object_from_id"], namespace


get_asset_object_from_id, _NAMESPACE = _load()


class FakeTable:
    """get_item from a dict keyed (databaseId, assetId); any query or scan is a failure."""

    def __init__(self, rows, fail=False):
        self.rows = rows
        self.fail = fail
        self.keys_read = []

    def get_item(self, Key, **kwargs):
        self.keys_read.append((Key["databaseId"], Key["assetId"]))
        if self.fail:
            raise RuntimeError("read failed")
        row = self.rows.get((Key["databaseId"], Key["assetId"]))
        return {"Item": dict(row)} if row else {}

    def query(self, **kwargs):
        raise AssertionError(f"the asset lookup must not query ({kwargs.get('IndexName', 'table')})")

    def scan(self, **kwargs):
        raise AssertionError("the asset lookup must not scan the asset table")


class FakeDynamoDBResource:
    def __init__(self, table):
        self.table = table

    def Table(self, name):
        assert name == _ASSET_TABLE_NAME
        return self.table


def _use(rows, fail=False):
    table = FakeTable(rows, fail=fail)
    _NAMESPACE["dynamodb"] = FakeDynamoDBResource(table)
    _NAMESPACE["_asset_table"] = None
    return table


LIVE_A = {"databaseId": "db-a", "assetId": "x1", "assetName": "A's x1"}
LIVE_B = {"databaseId": "db-b", "assetId": "x1", "assetName": "B's x1"}
ARCHIVED_A = {"databaseId": "db-a#deleted", "assetId": "x2", "assetName": "archived x2"}


@pytest.mark.unit
class TestTheLookupIsKeyedByBothIds:
    def test_the_named_databases_asset_is_returned(self):
        _use({("db-a", "x1"): LIVE_A, ("db-b", "x1"): LIVE_B})
        found = get_asset_object_from_id("db-b", "x1")
        assert found["assetName"] == "B's x1" and found["object__type"] == "asset"

    def test_an_asset_the_database_does_not_hold_is_none(self):
        table = _use({("db-a", "x1"): LIVE_A})
        assert get_asset_object_from_id("db-b", "x1") is None
        assert table.keys_read == [("db-b", "x1")]

    @pytest.mark.parametrize("database_id, asset_id", [(None, "x1"), ("", "x1"), ("db-a", None), ("db-a", "")])
    def test_a_missing_id_is_refused_before_any_read(self, database_id, asset_id):
        table = _use({("db-a", "x1"): LIVE_A})
        with pytest.raises(FakeVAMSGeneralErrorResponse, match="Empty assetId or databaseId"):
            get_asset_object_from_id(database_id, asset_id)
        assert table.keys_read == []

    def test_a_failed_read_is_raised_as_a_lookup_error(self):
        _use({}, fail=True)
        with pytest.raises(FakeVAMSGeneralErrorResponse):
            get_asset_object_from_id("db-a", "x1")


@pytest.mark.unit
class TestArchivedRows:
    def test_the_archived_row_is_read_only_when_asked(self):
        table = _use({("db-a#deleted", "x2"): ARCHIVED_A})
        assert get_asset_object_from_id("db-a", "x2") is None
        assert table.keys_read == [("db-a", "x2")]

    def test_with_the_flag_an_archived_asset_resolves_to_its_row(self):
        _use({("db-a#deleted", "x2"): ARCHIVED_A})
        found = get_asset_object_from_id("db-a", "x2", include_archived=True)
        assert found["databaseId"] == "db-a#deleted"
        # A row written before the status field existed is reported as archived
        assert found["status"] == "archived"

    def test_a_live_row_wins_over_an_archived_one(self):
        _use({("db-a", "x2"): {**ARCHIVED_A, "databaseId": "db-a", "assetName": "live x2"},
              ("db-a#deleted", "x2"): ARCHIVED_A})
        assert get_asset_object_from_id("db-a", "x2", include_archived=True)["assetName"] == "live x2"

    def test_another_databases_archived_row_is_never_read(self):
        table = _use({("db-b#deleted", "x2"): {**ARCHIVED_A, "databaseId": "db-b#deleted"}})
        assert get_asset_object_from_id("db-a", "x2", include_archived=True) is None
        assert set(table.keys_read) <= {("db-a", "x2"), ("db-a#deleted", "x2")}
