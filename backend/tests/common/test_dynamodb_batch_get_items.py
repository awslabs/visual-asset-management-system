# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Contract of the shared BatchGetItem helper, common.dynamodb.batch_get_items.

BatchGetItem accepts at most 100 keys per call, rejects a key list carrying a duplicate, and answers
a partially throttled request (or one whose response hit the 16 MB cap) with HTTP 200 and the leftover
keys in UnprocessedKeys -- outside the botocore retry config. Four handlers used to carry their own
copy of the chunk + re-request loop, and they disagreed on what happens to a key the budget could not
resolve: one dropped it silently. The helper returns (rows, unresolved_keys) so a missing row and a
failed read stay distinguishable, and these tests pin that contract so every caller inherits it.
"""

import importlib.util
import os
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

# tests/conftest.py replaces the whole `common.dynamodb` module with a MagicMock (the real one
# bootstraps AWS clients at import) and binds a few real helpers back onto it. Load the real module
# by path instead so this suite tests the shipped function regardless of what conftest chose to bind.
_MODULE_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "backend", "common", "dynamodb.py"
))
_spec = importlib.util.spec_from_file_location("real_common_dynamodb_for_batch_get", _MODULE_PATH)
ddb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ddb)

TABLE = "t-assets"


def _key(index):
    return {"databaseId": "db", "assetId": f"a{index}"}


def _row(key):
    return {"databaseId": key["databaseId"], "assetId": key["assetId"], "assetName": "n"}


class Reader:
    """A batch_get_item stub that serves every key it is asked for, deferring the keys in
    `defer` into UnprocessedKeys for the first `defer_rounds` calls (forever when None).
    Records each call's key list in `chunks`."""

    def __init__(self, defer=(), defer_rounds=None):
        self.defer = {(k["databaseId"], k["assetId"]) for k in defer}
        self.defer_rounds = defer_rounds
        self.chunks = []

    def __call__(self, RequestItems):
        keys = RequestItems[TABLE]["Keys"]
        self.chunks.append(list(keys))
        deferring = self.defer_rounds is None or len(self.chunks) <= self.defer_rounds
        served, deferred = [], []
        for key in keys:
            if deferring and (key["databaseId"], key["assetId"]) in self.defer:
                deferred.append(key)
            else:
                served.append(_row(key))
        response = {"Responses": {TABLE: served}}
        if deferred:
            response["UnprocessedKeys"] = {TABLE: {"Keys": deferred}}
        return response


def _resource(reader):
    resource = MagicMock()
    resource.batch_get_item = MagicMock(side_effect=reader)
    return resource


@pytest.fixture(autouse=True)
def no_real_sleep():
    """The backoff is real time in the request path and is asserted by its argument sequence."""
    with patch.object(ddb.time, "sleep") as sleep:
        yield sleep


@pytest.mark.unit
class TestChunking:
    def test_up_to_the_limit_is_one_call(self):
        reader = Reader()
        rows, unresolved = ddb.batch_get_items(_resource(reader), TABLE, [_key(i) for i in range(100)])
        assert [len(c) for c in reader.chunks] == [100]
        assert len(rows) == 100
        assert unresolved == []

    def test_one_over_the_limit_splits_into_two_calls(self):
        reader = Reader()
        rows, _ = ddb.batch_get_items(_resource(reader), TABLE, [_key(i) for i in range(101)])
        assert [len(c) for c in reader.chunks] == [100, 1]
        assert len(rows) == 101

    def test_empty_keys_makes_no_call(self):
        reader = Reader()
        rows, unresolved = ddb.batch_get_items(_resource(reader), TABLE, [])
        assert reader.chunks == []
        assert (rows, unresolved) == ([], [])

    def test_duplicate_keys_are_sent_once(self):
        # DynamoDB rejects a chunk carrying a duplicate key, so a key set collected from a tree would
        # otherwise lose the whole chunk to a ValidationException.
        reader = Reader()
        keys = [_key(1), _key(2), {"assetId": "a1", "databaseId": "db"}, _key(2)]
        rows, _ = ddb.batch_get_items(_resource(reader), TABLE, keys)
        assert reader.chunks == [[_key(1), _key(2)]]
        assert len(rows) == 2

    def test_dedupe_counts_toward_the_chunk_boundary_after_collapsing(self):
        # 100 distinct keys listed twice is still one call.
        reader = Reader()
        keys = [_key(i) for i in range(100)] * 2
        ddb.batch_get_items(_resource(reader), TABLE, keys)
        assert [len(c) for c in reader.chunks] == [100]


@pytest.mark.unit
class TestUnprocessedKeys:
    def test_unprocessed_keys_are_re_requested_with_exponential_backoff(self, no_real_sleep):
        reader = Reader(defer=[_key(2)], defer_rounds=3)
        rows, unresolved = ddb.batch_get_items(_resource(reader), TABLE, [_key(1), _key(2)])
        # First call carries both keys; each retry carries only what was left unprocessed.
        assert reader.chunks == [[_key(1), _key(2)], [_key(2)], [_key(2)], [_key(2)]]
        assert sorted(r["assetId"] for r in rows) == ["a1", "a2"]
        assert unresolved == []
        base = ddb.BATCH_GET_RETRY_BACKOFF_SECONDS
        assert [c.args[0] for c in no_real_sleep.call_args_list] == [base, base * 2, base * 4]

    def test_exhausting_the_budget_returns_the_leftover_keys_not_raises_not_drops(self, no_real_sleep):
        reader = Reader(defer=[_key(2)])
        rows, unresolved = ddb.batch_get_items(_resource(reader), TABLE, [_key(1), _key(2)])
        assert [r["assetId"] for r in rows] == ["a1"]
        assert unresolved == [_key(2)]
        # One initial call plus the retry budget; one sleep before each retry, none after the last.
        assert len(reader.chunks) == ddb.BATCH_GET_MAX_RETRIES + 1
        assert no_real_sleep.call_count == ddb.BATCH_GET_MAX_RETRIES

    def test_a_caller_supplied_budget_and_backoff_are_honoured(self, no_real_sleep):
        reader = Reader(defer=[_key(2)])
        _, unresolved = ddb.batch_get_items(
            _resource(reader), TABLE, [_key(1), _key(2)], max_retries=4, backoff_seconds=1)
        assert len(reader.chunks) == 5
        assert unresolved == [_key(2)]
        assert [c.args[0] for c in no_real_sleep.call_args_list] == [1, 2, 4, 8]

    def test_zero_retries_is_exactly_one_call_per_chunk(self, no_real_sleep):
        reader = Reader(defer=[_key(0), _key(150)])
        rows, unresolved = ddb.batch_get_items(
            _resource(reader), TABLE, [_key(i) for i in range(200)], max_retries=0)
        assert [len(c) for c in reader.chunks] == [100, 100]
        assert len(rows) == 198
        assert unresolved == [_key(0), _key(150)]
        no_real_sleep.assert_not_called()

    def test_unresolved_keys_accumulate_across_chunks(self):
        reader = Reader(defer=[_key(5), _key(105)])
        rows, unresolved = ddb.batch_get_items(_resource(reader), TABLE, [_key(i) for i in range(110)])
        assert len(rows) == 108
        assert unresolved == [_key(5), _key(105)]

    def test_a_missing_item_is_neither_a_row_nor_unresolved(self):
        # The row simply does not come back: absent from both lists is "no such item".
        def reader(RequestItems):
            return {"Responses": {TABLE: [_row(_key(1))]}}
        rows, unresolved = ddb.batch_get_items(_resource(reader), TABLE, [_key(1), _key(2)])
        assert [r["assetId"] for r in rows] == ["a1"]
        assert unresolved == []


@pytest.mark.unit
class TestFailures:
    def test_a_client_error_on_a_chunk_propagates(self):
        # The caller owns its fallback (per-item reads, degrade, or fail), so the helper does not
        # decide for it by swallowing the error into an empty result.
        error = ClientError({"Error": {"Code": "ProvisionedThroughputExceededException",
                                       "Message": "slow down"}}, "BatchGetItem")
        resource = MagicMock()
        resource.batch_get_item = MagicMock(side_effect=error)
        with pytest.raises(ClientError):
            ddb.batch_get_items(resource, TABLE, [_key(1)])

    def test_a_client_error_on_a_later_chunk_propagates_too(self):
        calls = []

        def reader(RequestItems):
            calls.append(1)
            if len(calls) == 2:
                raise ClientError({"Error": {"Code": "InternalServerError", "Message": "x"}},
                                  "BatchGetItem")
            return {"Responses": {TABLE: [_row(k) for k in RequestItems[TABLE]["Keys"]]}}

        with pytest.raises(ClientError):
            ddb.batch_get_items(_resource(reader), TABLE, [_key(i) for i in range(101)])
        assert len(calls) == 2
