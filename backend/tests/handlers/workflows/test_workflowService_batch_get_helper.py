# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""workflowService._batch_pipeline_system_configs reads through the shared common.dynamodb.batch_get_items
with ``max_retries=0``.

The aggregates it feeds are display-only hints on a listing page, so the handler deliberately spends no
retry on UnprocessedKeys: a partial read degrades the hint and logs, it never slows the list. That is
the one caller whose budget is zero, and the shared helper's ``max_retries=0`` has to mean exactly one
call per chunk for the handler to keep that property. Everything else is parity: a map keyed
``(databaseId, pipelineId)`` -> systemConfig, one call per 100 distinct pipelines, a missing pipeline
simply absent, and a failed read answering ``{}`` rather than raising.
"""

from unittest.mock import MagicMock, patch

import pytest

from backend.backend.handlers.workflows import workflowService as ws

MOD = "backend.backend.handlers.workflows.workflowService"
TABLE = ws.pipeline_table_name
DB = "db1"


def _workflow(*pipeline_ids):
    return {"specifiedPipelines": [{"pipelineDatabaseId": DB, "pipelineId": pid} for pid in pipeline_ids]}


def _record(key):
    return {"databaseId": key["databaseId"], "pipelineId": key["pipelineId"],
            "systemConfig": {"name": key["pipelineId"]}}


class Reader:
    """batch_get_item stub over an `existing` set of pipeline ids; keys in `defer` are always
    answered in UnprocessedKeys."""

    def __init__(self, existing, defer=()):
        self.existing = set(existing)
        self.defer = set(defer)
        self.chunks = []

    def __call__(self, RequestItems):
        keys = RequestItems[TABLE]["Keys"]
        self.chunks.append(list(keys))
        served = [_record(k) for k in keys if k["pipelineId"] in self.existing and k["pipelineId"] not in self.defer]
        deferred = [k for k in keys if k["pipelineId"] in self.defer]
        response = {"Responses": {TABLE: served}}
        if deferred:
            response["UnprocessedKeys"] = {TABLE: {"Keys": deferred}}
        return response


def _run(workflows, reader):
    with patch(f"{MOD}.dynamodb") as ddb, patch(f"{MOD}.logger") as logger, patch("time.sleep") as sleep:
        ddb.batch_get_item = MagicMock(side_effect=reader)
        configs = ws._batch_pipeline_system_configs(workflows)
    return configs, logger, sleep


@pytest.mark.unit
class TestParity:
    def test_configs_are_keyed_by_database_and_pipeline_and_a_missing_pipeline_is_absent(self):
        reader = Reader(existing={"p1", "p2"})
        configs, _, _ = _run([_workflow("p1", "p2"), _workflow("p2", "gone")], reader)
        assert configs == {(DB, "p1"): {"name": "p1"}, (DB, "p2"): {"name": "p2"}}

    def test_a_pipeline_shared_by_many_workflows_is_requested_once(self):
        reader = Reader(existing={"p1"})
        _run([_workflow("p1")] * 50, reader)
        assert reader.chunks == [[{"databaseId": DB, "pipelineId": "p1"}]]

    def test_one_hundred_and_one_distinct_pipelines_is_two_reads(self):
        ids = [f"p{i}" for i in range(101)]
        reader = Reader(existing=set(ids))
        configs, _, _ = _run([_workflow(*ids)], reader)
        assert [len(c) for c in reader.chunks] == [100, 1]
        assert len(configs) == 101

    def test_no_pipeline_references_is_no_read(self):
        reader = Reader(existing=set())
        configs, _, _ = _run([{"specifiedPipelines": []}, {}], reader)
        assert configs == {}
        assert reader.chunks == []

    def test_a_failed_read_answers_empty_rather_than_raising(self):
        def reader(RequestItems):
            raise Exception("boom")
        configs, logger, _ = _run([_workflow("p1")], reader)
        assert configs == {}
        logger.exception.assert_called_once()


@pytest.mark.unit
class TestNoRetry:
    def test_unprocessed_keys_are_not_re_requested_and_the_page_is_flagged(self):
        reader = Reader(existing={"p1", "p2"}, defer={"p2"})
        configs, logger, sleep = _run([_workflow("p1", "p2")], reader)
        # Exactly one call: the display-only aggregates do not buy a retry.
        assert len(reader.chunks) == 1
        sleep.assert_not_called()
        assert configs == {(DB, "p1"): {"name": "p1"}}
        logger.warning.assert_called_once()
        assert "unprocessed" in logger.warning.call_args.args[0]

    def test_still_one_call_per_chunk_when_several_chunks_defer(self):
        ids = [f"p{i}" for i in range(150)]
        reader = Reader(existing=set(ids), defer={"p0", "p120"})
        configs, _, sleep = _run([_workflow(*ids)], reader)
        assert [len(c) for c in reader.chunks] == [100, 50]
        assert len(configs) == 148
        sleep.assert_not_called()
