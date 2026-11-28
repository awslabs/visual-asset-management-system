# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""executionService._batch_get_rows reads through the shared common.dynamodb.batch_get_items.

Its contract to prewarm_asset_details / get_pipeline_definitions is unchanged: the rows that came
back, never an exception -- a failed or incomplete batch yields fewer rows and the caller resolves
the remainder with its per-item read. The caller-level behaviour (memo contents, fallback reads) is
pinned in test_executionService_batch_reads.py; this file pins the wrapper itself for a canned table.
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "t-assets")
os.environ.setdefault("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "t-exec-v2")
os.environ.setdefault("WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME", "t-wf-inputs")
os.environ.setdefault("PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME", "t-pexec")
os.environ.setdefault("WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME", "t-wf-cfg")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_FILES_STORAGE_TABLE_NAME", "t-pin-files")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME", "t-pin-md")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME", "t-pin-cfg")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME", "t-of")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE_NAME", "t-om")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE_NAME", "t-or")
os.environ.setdefault("PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME", "t-logs")
os.environ.setdefault("WORKFLOW_STORAGE_TABLE_NAME", "t-workflows")
os.environ.setdefault("PIPELINE_STORAGE_TABLE_NAME", "t-pipelines")
os.environ.setdefault("EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME", "t-execv2")

from backend.backend.handlers.workflows import executionService as le  # noqa: E402

MOD = "backend.backend.handlers.workflows.executionService"
TABLE = le.asset_storage_table_name
BATCH_GET_MAX_RETRIES = sys.modules["common.dynamodb"].BATCH_GET_MAX_RETRIES


def _key(index):
    return {"databaseId": "db", "assetId": f"a{index}"}


def _row(key):
    return {"databaseId": key["databaseId"], "assetId": key["assetId"], "assetName": f"n-{key['assetId']}"}


class Reader:
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


def _run(keys, reader):
    with patch(f"{MOD}.dynamodb") as ddb, patch("time.sleep") as sleep:
        ddb.batch_get_item = MagicMock(side_effect=reader)
        rows = le._batch_get_rows(TABLE, keys)
    return rows, sleep


@pytest.mark.unit
class TestParity:
    def test_returns_the_rows_that_exist_and_omits_the_rest(self):
        reader = Reader(existing={"a1", "a3"})
        rows, _ = _run([_key(1), _key(2), _key(3)], reader)
        assert sorted(r["assetId"] for r in rows) == ["a1", "a3"]

    def test_two_hundred_and_fifty_keys_is_three_reads(self):
        reader = Reader(existing={f"a{i}" for i in range(250)})
        rows, _ = _run([_key(i) for i in range(250)], reader)
        assert [len(c) for c in reader.chunks] == [100, 100, 50]
        assert len(rows) == 250

    def test_no_keys_is_no_read(self):
        reader = Reader(existing=set())
        rows, _ = _run([], reader)
        assert rows == []
        assert reader.chunks == []

    def test_a_deferred_key_is_re_requested(self):
        reader = Reader(existing={"a1", "a2"}, defer={"a2"}, defer_rounds=1)
        rows, sleep = _run([_key(1), _key(2)], reader)
        assert sorted(r["assetId"] for r in rows) == ["a1", "a2"]
        assert reader.chunks == [[_key(1), _key(2)], [_key(2)]]
        assert sleep.call_count == 1

    def test_the_budget_is_unchanged_and_the_leftover_simply_yields_fewer_rows(self):
        reader = Reader(existing={"a1", "a2"}, defer={"a2"})
        rows, _ = _run([_key(1), _key(2)], reader)
        assert [r["assetId"] for r in rows] == ["a1"]
        assert len(reader.chunks) == BATCH_GET_MAX_RETRIES + 1

    def test_a_failed_batch_returns_no_rows_and_does_not_raise(self):
        def reader(RequestItems):
            raise Exception("throttled")
        rows, _ = _run([_key(1), _key(2)], reader)
        assert rows == []
