# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""assetExportService.batch_get_assets reads through the shared common.dynamodb.batch_get_items.

Parity first: for a canned table the map it returns (``databaseId:assetId`` -> row) and the number
of BatchGetItem calls are what the handler produced before the shared helper. Then the one deliberate
change: the old copy read ``Responses`` and nothing else, so a key DynamoDB deferred into
``UnprocessedKeys`` -- a partial throttle, or a response at the 16 MB cap -- was neither retried nor
read individually, and the export was silently missing that asset. Those keys are now re-requested,
and whatever the budget cannot resolve falls into the per-item ``get_item`` fallback the exception
path already had.
"""

import sys
from unittest.mock import MagicMock, patch

import pytest

from tests.handlers.assets.test_assetExportService_authz_fail_closed import (  # noqa: E402
    _load_asset_export_service,
    _DB,
)

m = _load_asset_export_service()
TABLE = m.asset_storage_table_name
# The handler uses the helper's default budget; the conftest binds the real constant onto the mocked
# common.dynamodb module.
BATCH_GET_MAX_RETRIES = sys.modules["common.dynamodb"].BATCH_GET_MAX_RETRIES


def _ident(index):
    return {"databaseId": _DB, "assetId": f"a{index}"}


def _row(key):
    return {"databaseId": key["databaseId"], "assetId": key["assetId"], "assetName": f"n-{key['assetId']}"}


def _ck(asset_id):
    """The composite key the handler maps rows under."""
    return ":".join((_DB, asset_id))


class Reader:
    """batch_get_item stub over an `existing` set of asset ids; keys in `defer` are answered in
    UnprocessedKeys for the first `defer_rounds` calls (forever when None)."""

    def __init__(self, existing, defer=(), defer_rounds=None):
        self.existing = set(existing)
        self.defer = set(defer)
        self.defer_rounds = defer_rounds
        self.chunks = []

    def __call__(self, RequestItems):
        keys = RequestItems[TABLE]["Keys"]
        self.chunks.append(list(keys))
        deferring = self.defer_rounds is None or len(self.chunks) <= self.defer_rounds
        served, deferred = [], []
        for key in keys:
            if deferring and key["assetId"] in self.defer:
                deferred.append(key)
            elif key["assetId"] in self.existing:
                served.append(_row(key))
        response = {"Responses": {TABLE: served}}
        if deferred:
            response["UnprocessedKeys"] = {TABLE: {"Keys": deferred}}
        return response


def _get_item(existing):
    def run(Key):
        if Key["assetId"] in existing:
            return {"Item": _row(Key)}
        return {}
    return MagicMock(side_effect=run)


def _run(identifiers, reader, get_item=None):
    dynamo = MagicMock()
    dynamo.batch_get_item = MagicMock(side_effect=reader)
    table = MagicMock()
    table.get_item = get_item or MagicMock(return_value={})
    with patch.object(m, "dynamodb", dynamo), patch.object(m, "asset_table", table), \
            patch("time.sleep"):
        result = m.batch_get_assets(identifiers)
    return result, table.get_item


@pytest.mark.unit
class TestParity:
    def test_the_map_is_keyed_database_colon_asset_and_omits_missing_assets(self):
        reader = Reader(existing={"a1", "a3"})
        result, get_item = _run([_ident(1), _ident(2), _ident(3)], reader)
        assert set(result) == {_ck("a1"), _ck("a3")}
        assert result[_ck("a1")]["assetName"] == "n-a1"
        # A missing asset is simply absent; nothing about it is read individually.
        get_item.assert_not_called()

    def test_up_to_one_hundred_identifiers_is_one_read(self):
        reader = Reader(existing={f"a{i}" for i in range(100)})
        result, _ = _run([_ident(i) for i in range(100)], reader)
        assert [len(c) for c in reader.chunks] == [100]
        assert len(result) == 100

    def test_one_hundred_and_one_identifiers_is_two_reads(self):
        reader = Reader(existing={f"a{i}" for i in range(101)})
        result, _ = _run([_ident(i) for i in range(101)], reader)
        assert [len(c) for c in reader.chunks] == [100, 1]
        assert len(result) == 101

    def test_no_identifiers_is_no_read(self):
        reader = Reader(existing=set())
        result, get_item = _run([], reader)
        assert result == {}
        assert reader.chunks == []
        get_item.assert_not_called()

    def test_a_failed_batch_falls_back_to_one_get_item_per_identifier(self):
        dynamo = MagicMock()
        dynamo.batch_get_item = MagicMock(side_effect=Exception("boom"))
        table = MagicMock()
        table.get_item = _get_item({"a1", "a2"})
        with patch.object(m, "dynamodb", dynamo), patch.object(m, "asset_table", table):
            result = m.batch_get_assets([_ident(1), _ident(2), _ident(3)])
        assert set(result) == {_ck("a1"), _ck("a2")}
        assert table.get_item.call_count == 3


@pytest.mark.unit
class TestUnprocessedKeysAreNoLongerDropped:
    def test_a_deferred_key_is_re_requested_and_present_in_the_export(self):
        # Before the shared helper this returned only a1: the deferred key was never read again.
        reader = Reader(existing={"a1", "a2"}, defer={"a2"}, defer_rounds=1)
        result, get_item = _run([_ident(1), _ident(2)], reader)
        assert set(result) == {_ck("a1"), _ck("a2")}
        assert reader.chunks == [[_ident(1), _ident(2)], [_ident(2)]]
        get_item.assert_not_called()

    def test_a_key_unresolved_after_the_budget_is_read_individually(self):
        reader = Reader(existing={"a1", "a2"}, defer={"a2"})
        result, get_item = _run([_ident(1), _ident(2)], reader, get_item=_get_item({"a1", "a2"}))
        assert set(result) == {_ck("a1"), _ck("a2")}
        assert len(reader.chunks) == BATCH_GET_MAX_RETRIES + 1
        # Only the unresolved key goes to get_item; the resolved one is not read twice.
        assert [c.kwargs["Key"]["assetId"] for c in get_item.call_args_list] == ["a2"]

    def test_a_duplicate_identifier_is_requested_once(self):
        # A tree can list the same asset under two parents; DynamoDB rejects a duplicate key.
        reader = Reader(existing={"a1", "a2"})
        result, _ = _run([_ident(1), _ident(2), _ident(1)], reader)
        assert reader.chunks == [[_ident(1), _ident(2)]]
        assert set(result) == {_ck("a1"), _ck("a2")}
