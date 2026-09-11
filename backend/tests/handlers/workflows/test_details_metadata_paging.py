# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The paged execution-detail metadata read, and the bounded authorization fan-out of the global list.

The paged read walks an execution's PIPELINE EXECUTIONS in a stable order, because every metadata
collection is keyed on pipelineExecutionId rather than on the workflow execution. Its continuation token
therefore carries both the step position and the within-step LastEvaluatedKey — the central property
tested here is that walking a multi-step execution page by page yields each row exactly once, with
nothing skipped and nothing repeated.

executionService resolves its table names at import (mirrors test_executionService_wb53.py)."""

import json
import os

import pytest
from unittest.mock import MagicMock, patch

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

INPUT_SORT_KEY = "databaseId:assetId:filePath"
OUTPUT_SORT_KEY = "targetFilePath:metadataKey"


@pytest.fixture(autouse=True)
def _clear_caches():
    le._asset_details_cache.clear()
    le._authz_decision_cache.clear()
    le._disarm_authz_entity_budget()
    yield
    le._asset_details_cache.clear()
    le._authz_decision_cache.clear()
    le._disarm_authz_entity_budget()


def _allow_all():
    e = MagicMock()
    e.enforce.return_value = True
    e.enforceAPI.return_value = True
    return e


def _input_row(pexec, database_id="db", asset_id="a1", file_path="/f.glb", scope="asset"):
    """One stored input-metadata row, including the composite sort key the resume token is built from."""
    return {
        "pipelineExecutionId": pexec,
        INPUT_SORT_KEY: f"{database_id}:{asset_id}:{file_path}",
        "databaseId": database_id, "assetId": asset_id, "filePath": file_path,
        "scope": scope, "metadata": {"k": file_path},
    }


def _output_row(pexec, target="/out.glb", key="k1"):
    return {
        "pipelineExecutionId": pexec,
        OUTPUT_SORT_KEY: f"{target}:{key}",
        "targetFilePath": target, "metadataKey": key, "metadataValue": "v",
    }


class FakeTable:
    """A DynamoDB table stub honoring KeyConditionExpression / Limit / ExclusiveStartKey per partition.

    Rows are served in stored order within a partition, which is what lets a test assert that paging
    resumes at the first row the previous page left out."""

    def __init__(self, rows_by_pexec, sort_key):
        self.rows_by_pexec = rows_by_pexec
        self.sort_key = sort_key
        self.query_calls = []

    def query(self, **kwargs):
        cond = kwargs["KeyConditionExpression"]
        pexec = [v for v in getattr(cond, "_values", ()) if isinstance(v, str)][-1]
        rows = list(self.rows_by_pexec.get(pexec, []))
        start = 0
        exclusive = kwargs.get("ExclusiveStartKey")
        if exclusive:
            for index, row in enumerate(rows):
                if row.get(self.sort_key) == exclusive.get(self.sort_key):
                    start = index + 1
                    break
        limit = kwargs.get("Limit", len(rows))
        page = rows[start:start + limit]
        self.query_calls.append({"pexec": pexec, "start": start, "limit": limit})
        resp = {"Items": page}
        if start + limit < len(rows):
            resp["LastEvaluatedKey"] = {"pipelineExecutionId": pexec,
                                        self.sort_key: page[-1][self.sort_key]}
        return resp


def _steps(*pipeline_ids):
    return [{"pipelineExecutionId": f"pe-{pid}", "pipelineId": pid,
             "pipelineDatabaseId": "db"} for pid in pipeline_ids]


def _walk(table, prows, collection, page_size, pipeline_id=""):
    """Page the collection to exhaustion; returns (rows, page_count). Fails on a runaway walk."""
    with patch(f"{MOD}.dynamodb") as ddb, \
         patch(f"{MOD}.get_pipeline_execution_rows", return_value=prows):
        ddb.Table.return_value = table
        collected = []
        token = ""
        pages = 0
        while True:
            result = le.page_detail_metadata("e1000000000000000000000000000001", collection, page_size, token, pipeline_id)
            collected.extend(result["Items"])
            pages += 1
            token = result.get("NextToken")
            if not token:
                break
            assert pages < 500, "paging did not terminate"
    return collected, pages


def _identity(row, collection):
    if collection == le.DETAIL_METADATA_COLLECTION_OUTPUT:
        return (row["pipelineId"], row["targetFilePath"], row["metadataKey"])
    return (row["pipelineId"], row["databaseId"], row["assetId"], row["filePath"])


@pytest.mark.unit
class TestPagingAcrossPipelineSteps:
    """Input metadata lives per PIPELINE EXECUTION, so a page boundary can fall inside a step or
    between two. Neither may skip or repeat a row."""

    def _rows(self, per_step):
        return {f"pe-{pid}": [_input_row(f"pe-{pid}", file_path=f"/{pid}{i}.glb")
                              for i in range(count)]
                for pid, count in per_step}

    @pytest.mark.parametrize("page_size", [1, 2, 3, 5, 7, 100])
    def test_every_row_is_returned_exactly_once_at_any_page_size(self, page_size):
        per_step = [("pA", 5), ("pB", 4), ("pC", 6)]
        table = FakeTable(self._rows(per_step), INPUT_SORT_KEY)
        rows, _pages = _walk(table, _steps("pA", "pB", "pC"),
                             le.DETAIL_METADATA_COLLECTION_INPUT, page_size)
        identities = [_identity(r, le.DETAIL_METADATA_COLLECTION_INPUT) for r in rows]
        assert len(identities) == 15, f"expected every row, got {len(identities)}"
        assert len(set(identities)) == 15, "a row was repeated across pages"
        # Every step is represented with its full row count.
        counts = {}
        for row in rows:
            counts[row["pipelineId"]] = counts.get(row["pipelineId"], 0) + 1
        assert counts == {"pA": 5, "pB": 4, "pC": 6}

    def test_a_page_boundary_inside_a_step_resumes_at_the_next_unread_row(self):
        # page_size 2 over a 5-row step: the boundary falls mid-step twice.
        table = FakeTable(self._rows([("pA", 5)]), INPUT_SORT_KEY)
        rows, pages = _walk(table, _steps("pA"), le.DETAIL_METADATA_COLLECTION_INPUT, 2)
        assert [r["filePath"] for r in rows] == [f"/pA{i}.glb" for i in range(5)]
        assert pages == 3

    def test_a_page_boundary_exactly_on_a_step_edge_does_not_repeat_the_next_step(self):
        # 3 + 3 with page_size 3: page 1 ends exactly as step pA is exhausted.
        table = FakeTable(self._rows([("pA", 3), ("pB", 3)]), INPUT_SORT_KEY)
        rows, _pages = _walk(table, _steps("pA", "pB"), le.DETAIL_METADATA_COLLECTION_INPUT, 3)
        identities = [_identity(r, le.DETAIL_METADATA_COLLECTION_INPUT) for r in rows]
        assert len(identities) == len(set(identities)) == 6
        assert [r["pipelineId"] for r in rows] == ["pA"] * 3 + ["pB"] * 3

    def test_an_empty_step_between_two_populated_ones_is_walked_through(self):
        # An arity-'none' step reads no metadata; it must not stall or end the walk.
        rows_by_pexec = self._rows([("pA", 2), ("pC", 2)])
        rows_by_pexec["pe-pB"] = []
        table = FakeTable(rows_by_pexec, INPUT_SORT_KEY)
        rows, _pages = _walk(table, _steps("pA", "pB", "pC"),
                             le.DETAIL_METADATA_COLLECTION_INPUT, 1)
        assert [r["pipelineId"] for r in rows] == ["pA", "pA", "pC", "pC"]

    def test_the_last_page_omits_the_next_token(self):
        table = FakeTable(self._rows([("pA", 2)]), INPUT_SORT_KEY)
        with patch(f"{MOD}.dynamodb") as ddb, \
             patch(f"{MOD}.get_pipeline_execution_rows", return_value=_steps("pA")):
            ddb.Table.return_value = table
            result = le.page_detail_metadata("e1000000000000000000000000000001", le.DETAIL_METADATA_COLLECTION_INPUT, 10, "")
        assert len(result["Items"]) == 2
        assert "NextToken" not in result, "NextToken must be absent on the last page"

    def test_an_execution_with_no_steps_returns_an_empty_final_page(self):
        table = FakeTable({}, INPUT_SORT_KEY)
        with patch(f"{MOD}.dynamodb") as ddb, \
             patch(f"{MOD}.get_pipeline_execution_rows", return_value=[]):
            ddb.Table.return_value = table
            result = le.page_detail_metadata("e1000000000000000000000000000001", le.DETAIL_METADATA_COLLECTION_INPUT, 10, "")
        assert result["Items"] == []
        assert "NextToken" not in result

    def test_the_step_order_is_stable_rather_than_dynamodb_order(self):
        # The token names a step POSITION, so the order must not depend on the order rows come back in.
        forward = le._detail_metadata_step_order.__wrapped__ if hasattr(
            le._detail_metadata_step_order, "__wrapped__") else le._detail_metadata_step_order
        with patch(f"{MOD}.get_pipeline_execution_rows", return_value=_steps("pC", "pA", "pB")):
            first = forward("e1000000000000000000000000000001")
        with patch(f"{MOD}.get_pipeline_execution_rows", return_value=_steps("pB", "pC", "pA")):
            second = forward("e1000000000000000000000000000001")
        assert first == second, "step order must be stable across requests"
        assert [pid for _pexec, pid in first] == ["pA", "pB", "pC"]


@pytest.mark.unit
class TestCollectionSelection:
    """Each collection value addresses its own rows. 'input' and 'inputDatabase' are the scope split of
    one shared table, so the filter — not a separate table — is what separates them."""

    def _mixed(self):
        return {"pe-pA": [
            _input_row("pe-pA", file_path="/asset1.glb", scope="asset"),
            _input_row("pe-pA", database_id="src-db", asset_id="", file_path="/",
                       scope="database"),
            _input_row("pe-pA", file_path="/asset2.glb", scope="asset"),
        ]}

    def test_input_returns_only_asset_scope_rows(self):
        table = FakeTable(self._mixed(), INPUT_SORT_KEY)
        rows, _p = _walk(table, _steps("pA"), le.DETAIL_METADATA_COLLECTION_INPUT, 100)
        assert [r["filePath"] for r in rows] == ["/asset1.glb", "/asset2.glb"]
        assert all(r["scope"] == "asset" for r in rows)

    def test_input_database_returns_only_database_scope_rows(self):
        table = FakeTable(self._mixed(), INPUT_SORT_KEY)
        rows, _p = _walk(table, _steps("pA"), le.DETAIL_METADATA_COLLECTION_INPUT_DATABASE, 100)
        assert len(rows) == 1
        assert rows[0]["scope"] == "database"
        assert rows[0]["databaseId"] == "src-db"

    def test_a_row_without_a_scope_reads_as_asset_metadata(self):
        # Matches _scrub_input_metadata's default, so a row stored before the discriminator existed
        # stays visible in the asset collection rather than falling out of both.
        legacy = {"pe-pA": [{"pipelineExecutionId": "pe-pA",
                             INPUT_SORT_KEY: "db:a1:/legacy.glb",
                             "databaseId": "db", "assetId": "a1", "filePath": "/legacy.glb",
                             "metadata": {"k": "v"}}]}
        table = FakeTable(legacy, INPUT_SORT_KEY)
        as_input, _p = _walk(table, _steps("pA"), le.DETAIL_METADATA_COLLECTION_INPUT, 100)
        assert [r["filePath"] for r in as_input] == ["/legacy.glb"]
        table2 = FakeTable(legacy, INPUT_SORT_KEY)
        as_db, _p = _walk(table2, _steps("pA"), le.DETAIL_METADATA_COLLECTION_INPUT_DATABASE, 100)
        assert as_db == []

    def test_output_returns_the_output_metadata_shape(self):
        table = FakeTable({"pe-pA": [_output_row("pe-pA", key=f"k{i}") for i in range(3)]},
                          OUTPUT_SORT_KEY)
        rows, _p = _walk(table, _steps("pA"), le.DETAIL_METADATA_COLLECTION_OUTPUT, 100)
        assert [r["metadataKey"] for r in rows] == ["k0", "k1", "k2"]
        assert all(set(r) >= {"targetFilePath", "metadataKey", "metadataValue", "pipelineId"}
                   for r in rows)

    def test_output_pages_without_skipping_rows(self):
        # The output table's sort key differs from the input table's, so the resume key must be taken
        # from the row rather than assumed.
        table = FakeTable({"pe-pA": [_output_row("pe-pA", key=f"k{i}") for i in range(6)]},
                          OUTPUT_SORT_KEY)
        rows, pages = _walk(table, _steps("pA"), le.DETAIL_METADATA_COLLECTION_OUTPUT, 2)
        assert [r["metadataKey"] for r in rows] == [f"k{i}" for i in range(6)]
        assert pages == 3

    def test_rows_use_the_same_scrubbed_shape_as_the_details_view(self):
        row = _input_row("pe-pA", file_path="/f.glb")
        table = FakeTable({"pe-pA": [row]}, INPUT_SORT_KEY)
        rows, _p = _walk(table, _steps("pA"), le.DETAIL_METADATA_COLLECTION_INPUT, 10)
        expected = dict(le._scrub_input_metadata(row))
        expected["pipelineId"] = "pA"
        assert rows[0] == expected
        # No internal fields leak (the source S3 key and the composite sort key stay internal).
        assert "sourceInputMetadataFileS3Key" not in rows[0]
        assert INPUT_SORT_KEY not in rows[0]

    def test_the_collection_name_is_echoed(self):
        for collection in le.DETAIL_METADATA_COLLECTIONS:
            sort_key = (OUTPUT_SORT_KEY
                        if collection == le.DETAIL_METADATA_COLLECTION_OUTPUT else INPUT_SORT_KEY)
            table = FakeTable({}, sort_key)
            with patch(f"{MOD}.dynamodb") as ddb, \
                 patch(f"{MOD}.get_pipeline_execution_rows", return_value=[]):
                ddb.Table.return_value = table
                result = le.page_detail_metadata("e1000000000000000000000000000001", collection, 10, "")
            assert result["collection"] == collection


@pytest.mark.unit
class TestPipelineIdFilter:
    def test_the_filter_narrows_to_one_steps_rows(self):
        rows_by_pexec = {"pe-pA": [_input_row("pe-pA", file_path="/a.glb")],
                         "pe-pB": [_input_row("pe-pB", file_path="/b.glb")]}
        table = FakeTable(rows_by_pexec, INPUT_SORT_KEY)
        rows, _p = _walk(table, _steps("pA", "pB"), le.DETAIL_METADATA_COLLECTION_INPUT, 100, "pB")
        assert [r["pipelineId"] for r in rows] == ["pB"]

    def test_the_filter_still_pages_without_skipping(self):
        rows_by_pexec = {"pe-pA": [_input_row("pe-pA", file_path=f"/a{i}.glb") for i in range(3)],
                         "pe-pB": [_input_row("pe-pB", file_path=f"/b{i}.glb") for i in range(4)]}
        table = FakeTable(rows_by_pexec, INPUT_SORT_KEY)
        rows, _p = _walk(table, _steps("pA", "pB"), le.DETAIL_METADATA_COLLECTION_INPUT, 1, "pB")
        assert [r["filePath"] for r in rows] == [f"/b{i}.glb" for i in range(4)]

    def test_an_unmatched_filter_returns_an_empty_page(self):
        table = FakeTable({"pe-pA": [_input_row("pe-pA")]}, INPUT_SORT_KEY)
        rows, _p = _walk(table, _steps("pA"), le.DETAIL_METADATA_COLLECTION_INPUT, 10, "nope")
        assert rows == []


@pytest.mark.unit
class TestByteBoundResumePoint:
    """The byte bound can fire on the FIRST row a query page yields.

    The page-size bound always fires with rows already taken from the same query page, so the resume
    point can be read off the previous row. The BYTE bound cannot: a page filled by an earlier query
    (or by an earlier step) reaches its limit on row zero of the next one, and there is no previous row
    in that page to resume from. Resuming from the wrong end of the page steps over every row in it,
    and nothing in the response says so — the token still looks like an ordinary continuation.

    These tests give the stub rows large enough for the byte bound to be the one that fires, which is
    the only configuration that distinguishes the two resume points.
    """

    @staticmethod
    def _fat_row(pexec, index, megabytes):
        row = _input_row(pexec, file_path=f"/f{index:03d}.glb")
        row["metadata"] = {"k": "x" * int(megabytes * 1024 * 1024)}
        return row

    def test_the_bound_firing_on_a_pages_first_row_loses_nothing(self):
        # 1.6 MB rows: the third one crosses the 4 MB page budget, so the bound fires mid-walk while
        # the query pages hold 2 rows each.
        rows = [self._fat_row("pe-p1", i, 1.6) for i in range(6)]
        table = FakeTable({"pe-p1": rows}, INPUT_SORT_KEY)
        collected, pages = _walk(table, _steps("p1"), le.DETAIL_METADATA_COLLECTION_INPUT, 100)
        assert [r["filePath"] for r in collected] == [r["filePath"] for r in rows]
        assert pages > 1, "the byte bound never fired; the test proves nothing"

    def test_the_bound_firing_on_a_new_steps_first_row_loses_nothing(self):
        # Step one nearly fills the budget and is read to exhaustion, so the bound fires on the first
        # row of step two — a page whose rows have no predecessor in it.
        step1 = [self._fat_row("pe-p1", i, 1.9) for i in range(2)]
        step2 = [self._fat_row("pe-p2", i, 0.5) for i in range(10, 15)]
        table = FakeTable({"pe-p1": step1, "pe-p2": step2}, INPUT_SORT_KEY)
        collected, pages = _walk(table, _steps("p1", "p2"),
                                 le.DETAIL_METADATA_COLLECTION_INPUT, 100)
        expected = [r["filePath"] for r in step1 + step2]
        assert [r["filePath"] for r in collected] == expected
        assert pages > 1, "the byte bound never fired; the test proves nothing"

    def test_the_bound_firing_on_a_mid_step_query_page_boundary_loses_nothing(self):
        # A step holding more rows than one DynamoDB query returns: the walk fetches a second query page
        # within the same step, and the bound fires on ITS first row. Same missing-predecessor shape as a
        # step boundary, reached without crossing steps.
        page = le.DETAIL_METADATA_QUERY_PAGE_SIZE
        count = page + 40
        rows = [_input_row("pe-p1", file_path=f"/f{i:04d}.glb") for i in range(count)]
        # A full query page must fit the budget with less than one row to spare, so the bound fires on
        # the FIRST row of the second query page. Sized by measuring a real row rather than estimating,
        # since the serialized envelope around the payload is what decides where the bound lands.
        target = (le.MAX_DETAIL_METADATA_PAGE_BYTES // page) - 8
        probe = dict(rows[0], metadata={"k": ""})
        overhead = le._detail_metadata_row_bytes({**le._scrub_input_metadata(probe),
                                                 "pipelineId": "p1"})
        for row in rows:
            row["metadata"] = {"k": "x" * max(1, target - overhead)}
        assert sum(
            le._detail_metadata_row_bytes({**le._scrub_input_metadata(r), "pipelineId": "p1"})
            for r in rows[:page]) <= le.MAX_DETAIL_METADATA_PAGE_BYTES, (
            "a full query page must fit the budget, or the bound fires before the page boundary")
        table = FakeTable({"pe-p1": rows}, INPUT_SORT_KEY)
        collected, pages = _walk(table, _steps("p1"), le.DETAIL_METADATA_COLLECTION_INPUT, 10_000)
        assert [r["filePath"] for r in collected] == [r["filePath"] for r in rows]
        assert pages > 1, "the byte bound never fired; the test proves nothing"

    def test_a_scope_skipped_row_is_not_used_as_a_resume_point(self):
        # The two input collections share one table and are split by scope, so a walk of one steps over
        # the other's rows. A skipped row must not become the resume point for the rows after it.
        mixed = []
        for i in range(6):
            mixed.append(self._fat_row("pe-p1", i, 1.6))
            mixed.append(_input_row("pe-p1", file_path=f"/db{i:03d}", scope="database"))
        table = FakeTable({"pe-p1": mixed}, INPUT_SORT_KEY)
        collected, _ = _walk(table, _steps("p1"), le.DETAIL_METADATA_COLLECTION_INPUT, 100)
        asset_paths = [r["filePath"] for r in mixed if r["scope"] == "asset"]
        assert [r["filePath"] for r in collected] == asset_paths


@pytest.mark.unit
class TestTokenValidity:
    """An unusable token is a caller error. Restarting at page 1 would silently re-serve rows the
    caller already has; resuming at a stale step position would silently skip rows."""

    def test_a_malformed_token_is_rejected(self):
        table = FakeTable({"pe-pA": [_input_row("pe-pA")]}, INPUT_SORT_KEY)
        with patch(f"{MOD}.dynamodb") as ddb, \
             patch(f"{MOD}.get_pipeline_execution_rows", return_value=_steps("pA")):
            ddb.Table.return_value = table
            with pytest.raises(le.VAMSGeneralErrorResponse):
                le.page_detail_metadata("e1000000000000000000000000000001", le.DETAIL_METADATA_COLLECTION_INPUT, 10,
                                        "not-base64!!")

    def test_a_token_whose_step_order_changed_is_rejected(self):
        # The stepKey pins the index to the step it was issued for. If the execution's step set changes,
        # the same index now names a different step — resuming there would skip that step's rows.
        token = le._encode_detail_metadata_token(
            1, {"pipelineExecutionId": "pe-pB", INPUT_SORT_KEY: "db:a1:/b0.glb"})
        assert le._decode_detail_metadata_token(token, [("pe-pA", "pA"), ("pe-pB", "pB")]) is not None
        # Same index, different step now at that position.
        assert le._decode_detail_metadata_token(token, [("pe-pA", "pA"), ("pe-pZ", "pZ")]) is None

    def test_a_negative_or_out_of_range_step_index_is_rejected(self):
        steps = [("pe-pA", "pA")]
        assert le._decode_detail_metadata_token(
            le._encode_detail_metadata_token(-1, None), steps) is None
        assert le._decode_detail_metadata_token(
            le._encode_detail_metadata_token(9, None), steps) is None

    def test_a_non_dict_last_evaluated_key_is_rejected(self):
        import base64 as b64
        bad = b64.b64encode(json.dumps(
            {"stepIndex": 0, "stepKey": "", "lastEvaluatedKey": "nope"}).encode()).decode()
        assert le._decode_detail_metadata_token(bad, [("pe-pA", "pA")]) is None

    def test_an_index_equal_to_the_step_count_ends_the_walk(self):
        # The token issued when the final step is exhausted: valid, and yields no further rows.
        token = le._encode_detail_metadata_token(1, None)
        assert le._decode_detail_metadata_token(token, [("pe-pA", "pA")]) == (1, None)

    def test_an_invalid_token_surfaces_as_a_400_not_a_500(self):
        event = {
            "requestContext": {"http": {
                "method": "GET",
                "path": "/workflows/executions/E1/details/metadata"}, "authorizer": {}},
            "pathParameters": {"executionId": "e1000000000000000000000000000001"},
            "queryStringParameters": {"startingToken": "!!bad!!"},
        }
        le.claims_and_roles = {"tokens": ["u1"]}
        with patch(f"{MOD}.request_to_claims", return_value={"tokens": ["u1"]}), \
             patch(f"{MOD}.CasbinEnforcer", return_value=_allow_all()), \
             patch(f"{MOD}.get_execution_main_row",
                   return_value={"workflowExecutionId": "e1000000000000000000000000000001", "workflowId": "wf",
                                 "workflowDatabaseId": "db"}), \
             patch(f"{MOD}.get_workflow_execution_configuration_row", return_value={}), \
             patch(f"{MOD}.get_execution_input_assets", return_value=[]), \
             patch(f"{MOD}.get_pipeline_execution_rows", return_value=_steps("pA")), \
             patch(f"{MOD}.dynamodb"):
            resp = le.lambda_handler(event, MagicMock())
        assert resp["statusCode"] == 400
        body = json.loads(resp["body"])
        # Rule 11: the message must not echo the caller's token value back.
        assert "!!bad!!" not in json.dumps(body)


@pytest.mark.unit
class TestPageSizeCap:
    def _event(self, query):
        return {
            "requestContext": {"http": {
                "method": "GET",
                "path": "/workflows/executions/E1/details/metadata"}, "authorizer": {}},
            "pathParameters": {"executionId": "e1000000000000000000000000000001"},
            "queryStringParameters": query,
        }

    def _run(self, query, row_count):
        rows = {"pe-pA": [_input_row("pe-pA", file_path=f"/f{i}.glb") for i in range(row_count)]}
        table = FakeTable(rows, INPUT_SORT_KEY)
        le.claims_and_roles = {"tokens": ["u1"]}
        with patch(f"{MOD}.request_to_claims", return_value={"tokens": ["u1"]}), \
             patch(f"{MOD}.CasbinEnforcer", return_value=_allow_all()), \
             patch(f"{MOD}.get_execution_main_row",
                   return_value={"workflowExecutionId": "e1000000000000000000000000000001", "workflowId": "wf",
                                 "workflowDatabaseId": "db"}), \
             patch(f"{MOD}.get_workflow_execution_configuration_row", return_value={}), \
             patch(f"{MOD}.get_execution_input_assets", return_value=[]), \
             patch(f"{MOD}.get_pipeline_execution_rows", return_value=_steps("pA")), \
             patch(f"{MOD}.dynamodb") as ddb:
            ddb.Table.return_value = table
            resp = le.lambda_handler(self._event(query), MagicMock())
        assert resp["statusCode"] == 200, resp["body"]
        return json.loads(resp["body"])["message"]

    def test_an_over_large_page_size_is_clamped_to_the_cap(self):
        message = self._run({"pageSize": str(le.MAX_DETAIL_METADATA_PAGE_SIZE + 500)},
                            le.MAX_DETAIL_METADATA_PAGE_SIZE + 10)
        assert len(message["Items"]) == le.MAX_DETAIL_METADATA_PAGE_SIZE
        # Clamped rather than rejected, and the remainder is reachable.
        assert "NextToken" in message

    def test_the_default_page_size_applies_when_unspecified(self):
        message = self._run({}, le.DEFAULT_DETAIL_METADATA_PAGE_SIZE + 5)
        assert len(message["Items"]) == le.DEFAULT_DETAIL_METADATA_PAGE_SIZE

    def test_a_blank_page_size_falls_back_to_the_default(self):
        message = self._run({"pageSize": ""}, le.DEFAULT_DETAIL_METADATA_PAGE_SIZE + 5)
        assert len(message["Items"]) == le.DEFAULT_DETAIL_METADATA_PAGE_SIZE

    def test_a_smaller_page_size_is_honored(self):
        message = self._run({"pageSize": "3"}, 10)
        assert len(message["Items"]) == 3

    def test_an_invalid_collection_is_a_400(self):
        rows = {"pe-pA": []}
        table = FakeTable(rows, INPUT_SORT_KEY)
        le.claims_and_roles = {"tokens": ["u1"]}
        with patch(f"{MOD}.request_to_claims", return_value={"tokens": ["u1"]}), \
             patch(f"{MOD}.CasbinEnforcer", return_value=_allow_all()), \
             patch(f"{MOD}.get_execution_main_row",
                   return_value={"workflowExecutionId": "e1000000000000000000000000000001", "workflowId": "wf",
                                 "workflowDatabaseId": "db"}), \
             patch(f"{MOD}.get_workflow_execution_configuration_row", return_value={}), \
             patch(f"{MOD}.get_execution_input_assets", return_value=[]), \
             patch(f"{MOD}.get_pipeline_execution_rows", return_value=_steps("pA")), \
             patch(f"{MOD}.dynamodb") as ddb:
            ddb.Table.return_value = table
            resp = le.lambda_handler(self._event({"collection": "bogus"}), MagicMock())
        assert resp["statusCode"] == 400


@pytest.mark.unit
class TestPagedMetadataAuthorization:
    """The paged read applies the SAME Tier-2 rule as the details view, so the set of callers who can
    page a collection is exactly the set who can open the details page."""

    EVENT = {
        "requestContext": {"http": {
            "method": "GET",
            "path": "/workflows/executions/E1/details/metadata"}, "authorizer": {}},
        "pathParameters": {"executionId": "e1000000000000000000000000000001"},
        "queryStringParameters": {},
    }
    MAIN = {"workflowExecutionId": "e1000000000000000000000000000001", "workflowId": "wf", "workflowDatabaseId": "db"}

    def _run(self, enforcer, claims, input_assets=(), config_row=None):
        with patch(f"{MOD}.request_to_claims", return_value=claims), \
             patch(f"{MOD}.CasbinEnforcer", return_value=enforcer), \
             patch(f"{MOD}.get_execution_main_row", return_value=self.MAIN), \
             patch(f"{MOD}.get_workflow_execution_configuration_row",
                   return_value=config_row or {}), \
             patch(f"{MOD}.get_execution_input_assets", return_value=list(input_assets)), \
             patch(f"{MOD}._get_asset_details_cached",
                   side_effect=lambda d, a: {"databaseId": d, "assetId": a}), \
             patch(f"{MOD}.prewarm_asset_details"), \
             patch(f"{MOD}.get_pipeline_execution_rows", return_value=_steps("pA")), \
             patch(f"{MOD}.dynamodb") as ddb:
            ddb.Table.return_value = FakeTable({"pe-pA": []}, INPUT_SORT_KEY)
            return le.lambda_handler(dict(self.EVENT), MagicMock())

    def test_a_denied_asset_denies_the_page(self):
        enf = MagicMock()
        enf.enforceAPI.return_value = True
        enf.enforce.side_effect = lambda obj, action, *a, **k: (
            obj.get("object__type") != "asset")
        resp = self._run(enf, {"tokens": ["u1"]}, input_assets=[("db", "a1")])
        assert resp["statusCode"] == 403

    def test_a_denied_workflow_denies_the_page(self):
        enf = MagicMock()
        enf.enforceAPI.return_value = True
        enf.enforce.side_effect = lambda obj, action, *a, **k: (
            obj.get("object__type") != "workflow")
        assert self._run(enf, {"tokens": ["u1"]})["statusCode"] == 403

    def test_a_denied_metadata_source_database_denies_the_page(self):
        enf = MagicMock()
        enf.enforceAPI.return_value = True
        enf.enforce.side_effect = lambda obj, action, *a, **k: (
            obj.get("object__type") != "database")
        resp = self._run(enf, {"tokens": ["u1"]},
                         config_row={"inputMetadataDatabaseId": "src-db"})
        assert resp["statusCode"] == 403

    def test_empty_tokens_fail_closed(self):
        # No authenticated identity: authorization cannot be evaluated, so it must deny — never serve
        # a page because the enforcer happened to allow everything.
        assert self._run(_allow_all(), {"tokens": []})["statusCode"] == 403

    def test_a_denied_api_route_denies_the_page(self):
        enf = MagicMock()
        enf.enforceAPI.return_value = False   # Tier 1 refuses
        enf.enforce.return_value = True
        assert self._run(enf, {"tokens": ["u1"]})["statusCode"] == 403

    def test_an_authorized_caller_gets_the_page(self):
        assert self._run(_allow_all(), {"tokens": ["u1"]})["statusCode"] == 200

    def test_an_unknown_execution_is_a_404(self):
        with patch(f"{MOD}.request_to_claims", return_value={"tokens": ["u1"]}), \
             patch(f"{MOD}.CasbinEnforcer", return_value=_allow_all()), \
             patch(f"{MOD}.get_execution_main_row", return_value=None):
            resp = le.lambda_handler(dict(self.EVENT), MagicMock())
        assert resp["statusCode"] == 404

    def test_the_paged_route_matches_the_same_rule_as_details(self):
        # Both paths run authorize_execution_access for GET, so their verdicts agree by construction.
        enf = MagicMock()
        enf.enforceAPI.return_value = True
        enf.enforce.side_effect = lambda obj, action, *a, **k: (
            obj.get("object__type") != "asset")
        le.claims_and_roles = {"tokens": ["u1"]}
        with patch(f"{MOD}.CasbinEnforcer", return_value=enf), \
             patch(f"{MOD}.get_execution_input_assets", return_value=[("db", "a1")]), \
             patch(f"{MOD}._get_asset_details_cached",
                   side_effect=lambda d, a: {"databaseId": d, "assetId": a}), \
             patch(f"{MOD}.prewarm_asset_details"), \
             patch(f"{MOD}.get_workflow_execution_configuration_row", return_value={}):
            details_allowed, _r = le.authorize_execution_access("e1000000000000000000000000000001", self.MAIN, "GET")
        assert details_allowed is False
        assert self._run(enf, {"tokens": ["u1"]},
                         input_assets=[("db", "a1")])["statusCode"] == 403


@pytest.mark.unit
class TestRestShapedEvent:
    """Rule 16: the REST (v1) proxy event sends explicit null query/path params, which normalize_event
    coerces. The route must not 500 on the real event shape."""

    def test_null_query_string_parameters_are_tolerated(self):
        event = {
            "resource": "/workflows/executions/{executionId}/details/metadata",
            "path": "/workflows/executions/E1/details/metadata",
            "httpMethod": "GET",
            "requestContext": {"identity": {"sourceIp": "1.2.3.4"}, "authorizer": {}},
            "pathParameters": {"executionId": "e1000000000000000000000000000001"},
            "queryStringParameters": None,
            "body": None,
        }
        with patch(f"{MOD}.request_to_claims", return_value={"tokens": ["u1"]}), \
             patch(f"{MOD}.CasbinEnforcer", return_value=_allow_all()), \
             patch(f"{MOD}.get_execution_main_row",
                   return_value={"workflowExecutionId": "e1000000000000000000000000000001", "workflowId": "wf",
                                 "workflowDatabaseId": "db"}), \
             patch(f"{MOD}.get_workflow_execution_configuration_row", return_value={}), \
             patch(f"{MOD}.get_execution_input_assets", return_value=[]), \
             patch(f"{MOD}.get_pipeline_execution_rows", return_value=_steps("pA")), \
             patch(f"{MOD}.dynamodb") as ddb:
            ddb.Table.return_value = FakeTable({"pe-pA": []}, INPUT_SORT_KEY)
            resp = le.lambda_handler(event, MagicMock())
        assert resp["statusCode"] == 200, resp["body"]
        assert json.loads(resp["body"])["message"]["collection"] == "input"


def _step_tuples(*pipeline_ids):
    """The (pipelineExecutionId, pipelineId) tuples _detail_metadata_step_order returns.

    Distinct from _steps(), which builds the DynamoDB rows get_pipeline_execution_rows yields; the
    token decoder is handed the sorted tuple list, not those rows.
    """
    return sorted((f"pe-{pid}", pid) for pid in pipeline_ids)


@pytest.mark.unit
class TestTokenIsBoundToItsQuery:
    """A continuation token is only meaningful against the query that issued it.

    Each collection reads a DIFFERENT table and each pipelineId filter a DIFFERENT step list, so a
    token cross-applied to another one indexes into the wrong thing. The failure is silent: the read
    succeeds and serves rows from the wrong query (or an empty page that looks complete), which is
    worse than an error. Live, replaying an `input` token against `inputDatabase` returned 200 with
    database rows, and against `output` it 500'd.
    """

    def _first_token(self, collection, pipeline_id=""):
        """The NextToken from a bounded first page, so a real resume point is under test."""
        rows = {"pe-pA": [_input_row("pe-pA", file_path=f"/a{i}.glb") for i in range(4)],
                "pe-pB": [_input_row("pe-pB", file_path=f"/b{i}.glb") for i in range(4)]}
        table = FakeTable(rows, INPUT_SORT_KEY)
        with patch(f"{MOD}.dynamodb") as ddb, \
             patch(f"{MOD}.get_pipeline_execution_rows", return_value=_steps("pA", "pB")):
            ddb.Table.return_value = table
            result = le.page_detail_metadata("e1000000000000000000000000000001", collection,
                                             2, "", pipeline_id)
        token = result.get("NextToken")
        assert token, "the first page must be bounded for this test to exercise a resume point"
        return token

    def test_a_token_from_another_collection_is_refused(self):
        token = self._first_token(le.DETAIL_METADATA_COLLECTION_INPUT)
        for other in (le.DETAIL_METADATA_COLLECTION_INPUT_DATABASE,
                      le.DETAIL_METADATA_COLLECTION_OUTPUT):
            steps = _step_tuples("pA", "pB")
            assert le._decode_detail_metadata_token(token, steps, other, "") is None, (
                f"an input token was accepted for collection={other}")

    def test_the_same_collection_still_resumes(self):
        # Positive control: the refusals above must not come from rejecting every token.
        token = self._first_token(le.DETAIL_METADATA_COLLECTION_INPUT)
        resumed = le._decode_detail_metadata_token(
            token, _step_tuples("pA", "pB"), le.DETAIL_METADATA_COLLECTION_INPUT, "")
        assert resumed is not None, "a token replayed against its own collection must resume"

    def test_a_filterless_token_is_refused_under_a_pipeline_filter(self):
        token = self._first_token(le.DETAIL_METADATA_COLLECTION_INPUT)
        assert le._decode_detail_metadata_token(
            token, _step_tuples("pA"), le.DETAIL_METADATA_COLLECTION_INPUT, "pA") is None, (
            "a token issued without a pipelineId filter was accepted under one")

    def test_a_filtered_token_is_refused_without_the_filter(self):
        token = self._first_token(le.DETAIL_METADATA_COLLECTION_INPUT, pipeline_id="pA")
        assert le._decode_detail_metadata_token(
            token, _step_tuples("pA", "pB"), le.DETAIL_METADATA_COLLECTION_INPUT, "") is None, (
            "a filtered token was accepted for the unfiltered read")

    def test_a_filtered_token_resumes_under_the_same_filter(self):
        token = self._first_token(le.DETAIL_METADATA_COLLECTION_INPUT, pipeline_id="pA")
        assert le._decode_detail_metadata_token(
            token, _step_tuples("pA"), le.DETAIL_METADATA_COLLECTION_INPUT, "pA") is not None

    def test_a_token_predating_the_binding_is_refused(self):
        # A token carrying neither value cannot prove which query produced it. A restart is
        # recoverable; serving another query's rows is not.
        legacy = le.base64.b64encode(json.dumps(
            {"stepIndex": 1, "stepKey": "", "lastEvaluatedKey": None}).encode("utf-8")).decode("utf-8")
        assert le._decode_detail_metadata_token(
            legacy, _step_tuples("pA", "pB"), le.DETAIL_METADATA_COLLECTION_INPUT, "") is None
