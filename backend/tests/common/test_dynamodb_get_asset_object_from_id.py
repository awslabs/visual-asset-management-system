# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Resolution behavior of get_asset_object_from_id's no-databaseId lookup (S2-BACKEND-077 / FIX-033).

The legacy no-databaseId path locates an asset from an assetId alone. It reads the
``assetIdGSI`` index, which is partitioned on assetId, and pages that query to exhaustion:
the index can hold several rows for one assetId (the same ID in a second database, plus the
archived copy written under a ``{databaseId}#deleted`` partition), and those rows can straddle
a page boundary.

Three outcomes are pinned here, because each one used to be a silently wrong single object:
a live match returns the record, no live match returns None instead of an all-None object
annotated ``object__type='asset'`` (which Casbin would evaluate as though the asset existed),
and more than one live match raises instead of resolving to whichever row came back first.

The low-level client stub fails on every call, so a filtered table Scan fails loudly here
rather than quietly producing an answer from one page of the table.
"""

import os
from types import SimpleNamespace
from typing import Dict, List

import pytest
from boto3.dynamodb.conditions import Key

# tests/conftest.py replaces the whole `common.dynamodb` module with a MagicMock (the real one
# bootstraps AWS clients at import), so load the functions under test from source instead of
# importing the package - the same approach as test_dynamodb_query_all_items.py.
_MODULE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "backend", "common", "dynamodb.py"
)

_ASSET_TABLE_NAME = "assetStorageTable"
_ASSET_ID_INDEX = "assetIdGSI"


class FakeVAMSGeneralErrorResponse(Exception):
    """Stand-in for models.common.VAMSGeneralErrorResponse."""


class FakeLogger:
    def __init__(self):
        self.messages = []

    def info(self, *args, **kwargs):
        self.messages.append(args[0] if args else "")

    def error(self, *args, **kwargs):
        self.messages.append(args[0] if args else "")

    def exception(self, *args, **kwargs):
        self.messages.append(args[0] if args else "")


def _extract_function_source(source, name):
    """Slice a single top-level function out of the module source."""
    start = source.index(f"def {name}(")
    return source[start:source.index("\ndef ", start)]


def _load_get_asset_object_from_id():
    """Extract get_asset_object_from_id (and the pager it uses) from the real module source."""
    with open(_MODULE_PATH, encoding="utf-8") as f:
        source = f.read()

    namespace = {
        "Key": Key,
        "Dict": Dict,
        "List": List,
        "VAMSGeneralErrorResponse": FakeVAMSGeneralErrorResponse,
        "ResourceKeys": SimpleNamespace(ASSET_STORAGE_TABLE="assetStorageTable"),
        "get_table_name": lambda key: _ASSET_TABLE_NAME,
        "logger": FakeLogger(),
        "dynamodb_client": None,
        "dynamodb": None,
        # The module builds the asset table handle once at import and _asset_table_resource()
        # memoizes into this global, so each test resets it (see _use_query_pages) to be handed the
        # stub it installed rather than the previous test's.
        "_asset_table": None,
    }
    for function_name in ("_asset_table_resource", "query_all_items", "get_asset_object_from_id"):
        exec(
            compile(_extract_function_source(source, function_name), _MODULE_PATH, "exec"),
            namespace,
        )
    return namespace["get_asset_object_from_id"], namespace


get_asset_object_from_id, _NAMESPACE = _load_get_asset_object_from_id()


class NoScanClient:
    """Low-level client stub that trips on every call: this lookup must not scan the table."""

    def get_paginator(self, operation_name):
        raise AssertionError(
            f"the asset lookup must not use the low-level {operation_name} paginator"
        )

    def scan(self, **kwargs):
        raise AssertionError("the asset lookup must not scan the asset table")


class FakeTable:
    """Resource-API table stub serving a scripted sequence of query pages.

    Pages are handed out through LastEvaluatedKey/ExclusiveStartKey exactly as DynamoDB does,
    so a caller that reads only the first page sees only the first page.
    """

    def __init__(self, pages):
        self.pages = pages
        self.query_calls = []

    def query(self, **kwargs):
        self.query_calls.append(kwargs)
        start_key = kwargs.get("ExclusiveStartKey")
        index = start_key["page"] if start_key else 0
        response = {"Items": self.pages[index]}
        if index + 1 < len(self.pages):
            response["LastEvaluatedKey"] = {"page": index + 1}
        return response


class FakeDynamoDBResource:
    def __init__(self, table):
        self.table = table
        self.requested_tables = []

    def Table(self, name):
        self.requested_tables.append(name)
        return self.table


def _asset_item(asset_id, database_id="unit-test-db", **overrides):
    """An asset row as the resource-API index query returns it (already deserialized)."""
    item = {
        "assetId": asset_id,
        "databaseId": database_id,
        "assetName": f"{asset_id}-name",
        "assetType": ".glb",
        "tags": ["public"],
    }
    item.update(overrides)
    return item


def _use_query_pages(pages):
    """Point the loaded function at a table stub serving `pages`, and return that stub."""
    table = FakeTable(pages)
    _NAMESPACE["dynamodb"] = FakeDynamoDBResource(table)
    # Drop the memoized handle so this stub is the one the lookup gets.
    _NAMESPACE["_asset_table"] = None
    # A client that raises on use proves the lookup goes through the index query, not a scan.
    _NAMESPACE["dynamodb_client"] = NoScanClient()
    return table


@pytest.mark.unit
class TestGetAssetObjectFromIdNoDatabaseId:
    def test_asset_is_resolved_from_the_asset_id_index(self):
        """The single-match case: keyed index query, and the shape the callers enforce on."""
        table = _use_query_pages([[_asset_item("target-asset", database_id="restricted-db",
                                               tags=["confidential"])]])

        asset_object = get_asset_object_from_id(None, "target-asset")

        assert asset_object == {
            "object__type": "asset",
            "assetId": "target-asset",
            "assetName": "target-asset-name",
            "databaseId": "restricted-db",
            "assetType": ".glb",
            "tags": ["confidential"],
        }
        assert len(table.query_calls) == 1
        assert table.query_calls[0]["IndexName"] == _ASSET_ID_INDEX
        assert table.query_calls[0]["KeyConditionExpression"] == Key("assetId").eq("target-asset")

    def test_the_no_scan_client_stub_would_fail_a_scan(self):
        """Positive control on the stub every other test relies on: it really does trip."""
        with pytest.raises(AssertionError):
            NoScanClient().scan(TableName=_ASSET_TABLE_NAME)
        with pytest.raises(AssertionError):
            NoScanClient().get_paginator("scan")

    def test_every_index_page_is_read(self):
        """A live row on a later page must be found, not missed because page one was archived."""
        table = _use_query_pages([
            [_asset_item("target-asset", database_id="unit-test-db#deleted")],
            [_asset_item("target-asset", database_id="unit-test-db")],
        ])

        asset_object = get_asset_object_from_id(None, "target-asset")

        assert asset_object["databaseId"] == "unit-test-db"
        # Both pages were read: a single-call query would have seen only the archived row.
        assert len(table.query_calls) == 2
        assert table.query_calls[1]["ExclusiveStartKey"] == {"page": 1}

    def test_ambiguous_live_matches_are_refused(self):
        """Two live assets sharing an assetId cannot be told apart, so neither is chosen."""
        table = _use_query_pages([
            [_asset_item("shared-asset", database_id="db-a")],
            [_asset_item("shared-asset", database_id="db-b")],
        ])

        with pytest.raises(FakeVAMSGeneralErrorResponse):
            get_asset_object_from_id(None, "shared-asset")

        # The second match sat on the second page, so the refusal depends on full paging.
        assert len(table.query_calls) == 2

    def test_an_archived_copy_does_not_make_a_live_match_ambiguous(self):
        """Positive control for the refusal above: the archive+live pair still resolves."""
        _use_query_pages([[
            _asset_item("target-asset", database_id="unit-test-db#deleted"),
            _asset_item("target-asset", database_id="unit-test-db"),
        ]])

        asset_object = get_asset_object_from_id(None, "target-asset")

        assert asset_object["databaseId"] == "unit-test-db"

    def test_archived_partition_match_is_not_treated_as_the_live_asset(self):
        """An assetId that only exists in a #deleted partition resolves to nothing."""
        _use_query_pages([[_asset_item("archived-asset", database_id="unit-test-db#deleted")]])

        assert get_asset_object_from_id(None, "archived-asset") is None

        # Positive control: the identical row in the live partition does resolve, so the
        # None above comes from the archived partition and not from an inert stub.
        _use_query_pages([[_asset_item("archived-asset", database_id="unit-test-db")]])

        assert get_asset_object_from_id(None, "archived-asset")["databaseId"] == "unit-test-db"

    def test_archived_status_match_is_not_treated_as_the_live_asset(self):
        """Archived rows that kept their live partition key are recognized by status."""
        _use_query_pages([[_asset_item("stale-asset", status="archived")]])

        assert get_asset_object_from_id(None, "stale-asset") is None

        # Positive control: same row, no archived status.
        _use_query_pages([[_asset_item("stale-asset")]])

        assert get_asset_object_from_id(None, "stale-asset")["assetId"] == "stale-asset"

    def test_no_match_anywhere_returns_not_found(self):
        """No match must be None, never an all-None object annotated as an asset."""
        table = _use_query_pages([[], [], []])

        assert get_asset_object_from_id(None, "missing-asset") is None
        assert len(table.query_calls) == 3

        # Positive control: the same stub shape with a row present returns the record, so
        # the None above is a real not-found rather than a stub that can never match.
        _use_query_pages([[], [], [_asset_item("missing-asset")]])

        assert get_asset_object_from_id(None, "missing-asset")["assetId"] == "missing-asset"

    def test_legacy_row_without_tags_or_asset_type_does_not_raise(self):
        """Rows predating those attributes are reached more often now, and must not KeyError."""
        item = _asset_item("legacy-asset")
        del item["tags"]
        del item["assetType"]
        _use_query_pages([[item]])

        asset_object = get_asset_object_from_id(None, "legacy-asset")

        assert asset_object["assetType"] is None
        assert asset_object["tags"] == []
        # Positive control: the attributes are read when present, so the defaults above are
        # not masking a lookup that always yields nothing.
        _use_query_pages([[_asset_item("legacy-asset")]])

        asset_object = get_asset_object_from_id(None, "legacy-asset")

        assert asset_object["assetType"] == ".glb"
        assert asset_object["tags"] == ["public"]

    def test_empty_asset_id_is_rejected(self):
        _use_query_pages([[]])

        with pytest.raises(FakeVAMSGeneralErrorResponse):
            get_asset_object_from_id(None, "")

    def test_accepts_two_positional_arguments(self):
        """Guards the (databaseId, assetId) signature the callers and test stubs pass."""
        from inspect import signature

        assert list(signature(get_asset_object_from_id).parameters) == ["databaseId", "assetId"]


@pytest.mark.unit
class TestGetAssetObjectFromIdWithDatabaseId:
    def test_database_id_path_still_queries_and_annotates(self):
        """The keyed path is unaffected: one table query, item returned with object__type."""
        table = _use_query_pages([[{"assetId": "keyed-asset", "databaseId": "unit-test-db"}]])

        asset_object = get_asset_object_from_id("unit-test-db", "keyed-asset")

        assert asset_object["assetId"] == "keyed-asset"
        assert asset_object["object__type"] == "asset"
        assert len(table.query_calls) == 1
        # The keyed read goes to the table, not the assetId index.
        assert "IndexName" not in table.query_calls[0]

    def test_database_id_path_returns_none_when_absent(self):
        _use_query_pages([[]])

        assert get_asset_object_from_id("unit-test-db", "missing-asset") is None
