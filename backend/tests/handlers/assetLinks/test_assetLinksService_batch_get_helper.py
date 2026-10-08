# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""assetLinksService.batch_get_asset_details reads through the shared common.dynamodb.batch_get_items.

The handler's observable contract is unchanged: the details map is keyed ``databaseId:assetId``, a
key the budget could not resolve comes back in the second tuple element as ``(databaseId, assetId)``
rather than vanishing (so the caller counts it as unresolved, not unauthorized), the budget is still
``MAX_BATCH_GET_ATTEMPTS`` total calls, and a failed batch still degrades to one ``get_item`` per
key. The listing-level consequences are pinned in test_assetLinksService_tree_bounds.py; this file
pins the function itself for a canned table.
"""

from unittest.mock import MagicMock, patch

import pytest

from backend.backend.handlers.assetLinks import assetLinksService as als

MOD = "backend.backend.handlers.assetLinks.assetLinksService"
TABLE = als.asset_storage_table_name
DB = "db1"


def _row(key):
    return {"databaseId": key["databaseId"], "assetId": key["assetId"], "assetName": f"n-{key['assetId']}"}


def _ck(asset_id):
    """The composite key the handler maps rows under."""
    return ":".join((DB, asset_id))


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


def _run(asset_keys, reader):
    def per_item_row(asset_id, database_id):
        return _row({"databaseId": database_id, "assetId": asset_id})

    dynamo = MagicMock()
    dynamo.batch_get_item = MagicMock(side_effect=reader)
    with patch(f"{MOD}.dynamodb", dynamo), \
            patch(f"{MOD}.get_asset_details", side_effect=per_item_row) as per_item, \
            patch("time.sleep") as sleep:
        details, unresolved = als.batch_get_asset_details(asset_keys)
    return details, unresolved, per_item, sleep


@pytest.mark.unit
class TestParity:
    def test_the_map_is_keyed_database_colon_asset_and_a_missing_asset_is_just_absent(self):
        reader = Reader(existing={"a1", "a3"})
        details, unresolved, per_item, _ = _run([(DB, "a1"), (DB, "a2"), (DB, "a3")], reader)
        assert set(details) == {_ck("a1"), _ck("a3")}
        assert details[_ck("a3")]["assetName"] == "n-a3"
        # A missing asset is not "unresolved": the read completed and the row does not exist.
        assert unresolved == []
        per_item.assert_not_called()

    def test_up_to_one_hundred_keys_is_one_read(self):
        reader = Reader(existing={f"a{i}" for i in range(100)})
        details, _, _, _ = _run([(DB, f"a{i}") for i in range(100)], reader)
        assert [len(c) for c in reader.chunks] == [100]
        assert len(details) == 100

    def test_one_hundred_and_one_keys_is_two_reads(self):
        reader = Reader(existing={f"a{i}" for i in range(101)})
        details, _, _, _ = _run([(DB, f"a{i}") for i in range(101)], reader)
        assert [len(c) for c in reader.chunks] == [100, 1]
        assert len(details) == 101

    def test_no_keys_is_no_read(self):
        reader = Reader(existing=set())
        details, unresolved, per_item, _ = _run([], reader)
        assert (details, unresolved) == ({}, [])
        assert reader.chunks == []
        per_item.assert_not_called()

    def test_a_failed_batch_falls_back_to_one_get_item_per_key(self):
        def reader(RequestItems):
            raise Exception("boom")
        details, unresolved, per_item, _ = _run([(DB, "a1"), (DB, "a2")], reader)
        assert set(details) == {_ck("a1"), _ck("a2")}
        assert unresolved == []
        assert sorted(c.args for c in per_item.call_args_list) == [("a1", DB), ("a2", DB)]


@pytest.mark.unit
class TestUnprocessedKeys:
    def test_a_deferred_key_is_re_requested_and_resolved(self):
        reader = Reader(existing={"a1", "a2"}, defer={"a2"}, defer_rounds=1)
        details, unresolved, _, sleep = _run([(DB, "a1"), (DB, "a2")], reader)
        assert set(details) == {_ck("a1"), _ck("a2")}
        assert unresolved == []
        assert reader.chunks == [[{"databaseId": DB, "assetId": "a1"}, {"databaseId": DB, "assetId": "a2"}],
                                 [{"databaseId": DB, "assetId": "a2"}]]
        assert [c.args[0] for c in sleep.call_args_list] == [als.BATCH_GET_RETRY_BASE_SECONDS]

    def test_the_budget_is_still_max_batch_get_attempts_total_calls(self):
        reader = Reader(existing={"a1", "a2"}, defer={"a2"})
        details, unresolved, per_item, sleep = _run([(DB, "a1"), (DB, "a2")], reader)
        assert len(reader.chunks) == als.MAX_BATCH_GET_ATTEMPTS
        # One backoff before each re-request, doubling; none after the final attempt.
        base = als.BATCH_GET_RETRY_BASE_SECONDS
        expected_backoff = [base * (2 ** i) for i in range(als.MAX_BATCH_GET_ATTEMPTS - 1)]
        assert [c.args[0] for c in sleep.call_args_list] == expected_backoff
        # The unresolved key is reported as a (databaseId, assetId) tuple, not read individually and
        # not dropped.
        assert set(details) == {_ck("a1")}
        assert unresolved == [(DB, "a2")]
        per_item.assert_not_called()
