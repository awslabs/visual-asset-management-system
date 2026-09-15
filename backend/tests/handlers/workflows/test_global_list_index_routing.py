# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The global execution list reads a scoped page from the index keyed on that scope.

A workflow page (workflowId + workflowDatabaseId) used to be served from the by-date index with the
workflow as a FilterExpression and `Limit = pageSize`. DynamoDB applies Limit BEFORE the filter, so each
query evaluated 50 rows of the whole deployment's executions and kept the few that belonged to the
workflow; twenty such queries examined the newest 1,000 executions and then stopped with the
"per-request work budget" warning — an empty or partial page for any workflow whose runs were older than
that, on a table that holds tens of thousands. Measured on a live deployment: every workflow-scoped list
but one warned, most with zero rows.

Three things changed, each pinned here: the walk selects the WorkflowExecutionsByWorkflowGSI (or the
group index) when the filters key on it, so the wanted rows are the only rows read; a filtered query
evaluates GLOBAL_LIST_QUERY_LIMIT rows rather than the display page size; and the page is cut at
pageSize visible rows mid-query with a continuation that resumes after the last row evaluated, in the
shape of whichever index was walked.
"""

import base64
import json
import os

import botocore.exceptions
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

from backend.backend.common.workflows import executionRecords as er  # noqa: E402
from backend.backend.handlers.workflows import executionService as le  # noqa: E402

MOD = "backend.backend.handlers.workflows.executionService"
PAGE_SIZE = 5
RUNAWAY_QUERY_CALLS = 400


@pytest.fixture(autouse=True)
def _clear_caches():
    le._asset_details_cache.clear()
    le._authz_decision_cache.clear()
    le._disarm_authz_entity_budget()
    yield
    le._asset_details_cache.clear()
    le._authz_decision_cache.clear()
    le._disarm_authz_entity_budget()


def _row(i, workflow="wf", db="db", status="SUCCEEDED", group=""):
    """A main row carrying every key attribute any of the three indexes' continuations name."""
    return {"workflowExecutionId": f"{workflow}-{i}", "workflowId": workflow, "workflowDatabaseId": db,
            "workflowDatabaseId:workflowId": f"{db}:{workflow}",
            "allListPartition": er.ALL_EXECUTIONS_LIST_PARTITION,
            "executionGroupId": group,
            "executionStatus": status,
            "executionStartDate": f"2026-01-{(i % 28) + 1:02d}T{i % 24:02d}:00:00Z"}


def _allow_all():
    e = MagicMock()
    e.enforce.return_value = True
    e.enforceAPI.return_value = True
    return e


def _run(pages, query, repeat_last=False, raise_on_query=None):
    """Drive get_global_executions over scripted DynamoDB pages. Returns (message, query kwargs list).

    `pages` are served in order; once exhausted the stub returns an empty page with no LastEvaluatedKey
    unless `repeat_last`, which models an index the walk can never exhaust. `raise_on_query` is raised
    by the table for every query instead."""
    table = MagicMock()
    calls = []

    def _query(**kwargs):
        calls.append(dict(kwargs))
        if raise_on_query is not None:
            raise raise_on_query
        if len(calls) > RUNAWAY_QUERY_CALLS:
            raise AssertionError(f"still querying after {RUNAWAY_QUERY_CALLS} calls")
        index = len(calls) - 1
        if index < len(pages):
            return pages[index]
        return pages[-1] if repeat_last else {"Items": []}

    table.query.side_effect = _query
    le.claims_and_roles = {"tokens": ["u1"]}
    with patch(f"{MOD}.dynamodb") as ddb, \
         patch(f"{MOD}.CasbinEnforcer", return_value=_allow_all()), \
         patch(f"{MOD}.get_execution_input_assets", side_effect=lambda eid: [("db", "asset-1")]), \
         patch(f"{MOD}.get_workflow_execution_configuration_row", return_value={}), \
         patch(f"{MOD}.get_asset_details",
               side_effect=lambda d, a: {"databaseId": d, "assetId": a, "assetName": a}):
        ddb.Table.return_value = table
        ddb.batch_get_item.side_effect = lambda RequestItems: {
            "Responses": {name: [{"databaseId": k["databaseId"], "assetId": k["assetId"],
                                  "assetName": k["assetId"]}
                                 for k in spec["Keys"]]
                          for name, spec in RequestItems.items()}}
        response = le.get_global_executions({}, query)
    body = json.loads(response["body"])
    return (body.get("message") if response["statusCode"] == 200 else body), calls, response["statusCode"]


def _page(rows, last_key=None):
    page = {"Items": rows}
    if last_key is not None:
        page["LastEvaluatedKey"] = last_key
    return page


def _token(message):
    return json.loads(base64.b64decode(message["NextToken"]))


def _key_condition_literals(kwargs):
    """Every plain-string literal a boto3 Key condition compares against, read off the expression tree."""
    literals = set()

    def walk(e):
        for v in getattr(e, "_values", None) or ():
            if isinstance(v, str):
                literals.add(v)
            elif hasattr(v, "_values"):
                walk(v)
    walk(kwargs["KeyConditionExpression"])
    return literals


def _key_condition_names(kwargs):
    """The attribute names a boto3 Key condition is built on, read off the expression object."""
    expr = kwargs["KeyConditionExpression"]
    names = set()

    def walk(e):
        values = getattr(e, "_values", None) or ()
        for v in values:
            if hasattr(v, "name"):
                names.add(v.name)
            elif hasattr(v, "_values"):
                walk(v)
    walk(expr)
    return names


# ---------------------------------------------------------------------------------------------
@pytest.mark.unit
class TestAWorkflowScopeReadsTheWorkflowIndex:
    def test_workflow_id_and_database_select_the_workflow_index_with_no_filter_expression(self):
        message, calls, _ = _run([_page([_row(i) for i in range(PAGE_SIZE)])],
                                 {"pageSize": str(PAGE_SIZE), "workflowId": "wf", "workflowDatabaseId": "db"})
        assert calls[0]["IndexName"] == "WorkflowExecutionsByWorkflowGSI"
        assert "workflowDatabaseId:workflowId" in _key_condition_names(calls[0])
        assert "FilterExpression" not in calls[0], "the workflow is the key, not a post-read filter"
        assert len(message["Items"]) == PAGE_SIZE
        assert "warnings" not in message

    def test_the_composite_partition_value_is_database_colon_workflow(self):
        _message, calls, _ = _run([_page([_row(0)])],
                                  {"pageSize": "5", "workflowId": "wf", "workflowDatabaseId": "GLOBAL"})
        # The expression carries its literal values; the composite must be exactly what the record
        # builder writes, or the query reads an empty partition and every page is empty with no error.
        assert "GLOBAL:wf" in _key_condition_literals(calls[0])

    def test_a_workflow_id_without_its_database_falls_back_to_the_by_date_walk(self):
        # The composite key cannot be built from half of it; the by-date filter is the only option left.
        _message, calls, _ = _run([_page([_row(0)])], {"pageSize": "5", "workflowId": "wf"})
        assert calls[0]["IndexName"] == "WorkflowExecutionsByDateGSI"
        assert "FilterExpression" in calls[0]

    def test_other_filters_still_apply_on_top_of_the_workflow_key(self):
        rows = [_row(0, status="FAILED"), _row(1, status="SUCCEEDED")]
        message, calls, _ = _run([_page(rows)], {"pageSize": "5", "workflowId": "wf",
                                                 "workflowDatabaseId": "db", "status": "FAILED"})
        assert calls[0]["IndexName"] == "WorkflowExecutionsByWorkflowGSI"
        assert "FilterExpression" in calls[0], "status is not part of the key and stays a filter"
        # The Python safety net still drops the row the (stubbed) server filter let through.
        assert [r["workflowExecutionId"] for r in message["Items"]] == ["wf-0"]

    def test_the_reproduction_a_workflow_older_than_the_newest_thousand_executions_is_listed(self):
        """The live symptom: 20 queries x 50 rows of OTHER workflows, then a warning and no rows.

        On the workflow index the other workflows are not in the partition at all, so the first query
        returns the wanted rows and nothing warns."""
        wanted = [_row(i, workflow="old-wf") for i in range(3)]
        message, calls, _ = _run([_page(wanted)],
                                 {"pageSize": "50", "workflowId": "old-wf", "workflowDatabaseId": "db"})
        assert len(calls) == 1
        assert [r["workflowExecutionId"] for r in message["Items"]] == ["old-wf-0", "old-wf-1", "old-wf-2"]
        assert "warnings" not in message
        assert "NextToken" not in message


@pytest.mark.unit
class TestAGroupScopeReadsTheGroupIndex:
    def test_group_id_selects_the_group_index(self):
        message, calls, _ = _run([_page([_row(0, group="g1")])], {"pageSize": "5", "groupId": "g1"})
        assert calls[0]["IndexName"] == "WorkflowExecutionsByGroupGSI"
        assert "executionGroupId" in _key_condition_names(calls[0])
        assert "FilterExpression" not in calls[0]
        assert len(message["Items"]) == 1

    def test_a_workflow_scope_wins_over_a_group_filter(self):
        # Both keyed: the workflow partition is the narrower read and the group stays a filter on it.
        _message, calls, _ = _run([_page([_row(0, group="g1")])],
                                  {"pageSize": "5", "workflowId": "wf", "workflowDatabaseId": "db", "groupId": "g1"})
        assert calls[0]["IndexName"] == "WorkflowExecutionsByWorkflowGSI"
        assert "FilterExpression" in calls[0]


@pytest.mark.unit
class TestTheUnscopedListStillWalksTheByDateIndex:
    def test_no_filters_reads_the_by_date_index(self):
        _message, calls, _ = _run([_page([_row(i) for i in range(PAGE_SIZE)])], {"pageSize": str(PAGE_SIZE)})
        assert calls[0]["IndexName"] == "WorkflowExecutionsByDateGSI"
        assert "allListPartition" in _key_condition_names(calls[0])
        # The evaluation limit is the same with or without a filter: a narrowly-scoped CALLER drops rows
        # after Limit exactly as a filter does, and the walk cuts the page at page_size itself.
        assert PAGE_SIZE < calls[0]["Limit"] <= le.GLOBAL_LIST_QUERY_LIMIT

    def test_a_filtered_by_date_walk_evaluates_the_query_limit_per_query(self):
        """DynamoDB applies Limit before the FilterExpression, so a status filter at Limit = pageSize
        examines pageSize candidates per query; the walk reads the larger evaluation limit instead."""
        _message, calls, _ = _run([_page([_row(0, status="FAILED")])], {"pageSize": str(PAGE_SIZE), "status": "FAILED"})
        assert "FilterExpression" in calls[0]
        # The point is that a filtered query evaluates MORE than a display page, bounded by the constant.
        assert PAGE_SIZE < calls[0]["Limit"] <= le.GLOBAL_LIST_QUERY_LIMIT
        assert le.GLOBAL_LIST_QUERY_LIMIT > le.MAX_GLOBAL_LIST_PAGE_SIZE


@pytest.mark.unit
class TestThePageIsCutAtPageSizeWithAResumableContinuation:
    def test_a_query_returning_more_than_a_page_is_cut_and_continues_after_the_last_row_served(self):
        rows = [_row(i) for i in range(PAGE_SIZE + 3)]
        message, calls, _ = _run([_page(rows, last_key={"server": "key"})], {"pageSize": str(PAGE_SIZE)})
        assert len(message["Items"]) == PAGE_SIZE, "the page must not overshoot the requested size"
        assert len(calls) == 1
        # Resumes after the LAST ROW SERVED, not from the query's own LastEvaluatedKey (which points past
        # the three rows this page never returned).
        token = _token(message)
        assert token["workflowExecutionId"] == f"wf-{PAGE_SIZE - 1}"
        assert set(token) == set(le.GLOBAL_LIST_INDEXES["date"]["keyAttributes"])

    def test_a_page_that_fills_on_the_querys_last_row_keeps_the_querys_own_continuation(self):
        rows = [_row(i) for i in range(PAGE_SIZE)]
        message, _calls, _ = _run([_page(rows, last_key={"server": "key"})], {"pageSize": str(PAGE_SIZE)})
        assert len(message["Items"]) == PAGE_SIZE
        assert _token(message) == {"server": "key"}

    def test_a_page_that_fills_on_the_last_row_of_an_exhausted_index_offers_no_token(self):
        # CONTROL: no rows left unread and no server key -> this really is the end of the list.
        message, _calls, _ = _run([_page([_row(i) for i in range(PAGE_SIZE)])], {"pageSize": str(PAGE_SIZE)})
        assert len(message["Items"]) == PAGE_SIZE
        assert "NextToken" not in message

    def test_the_continuation_takes_the_shape_of_the_index_that_was_walked(self):
        rows = [_row(i) for i in range(PAGE_SIZE + 1)]
        message, _calls, _ = _run([_page(rows, last_key={"server": "key"})],
                                  {"pageSize": str(PAGE_SIZE), "workflowId": "wf", "workflowDatabaseId": "db"})
        token = _token(message)
        assert set(token) == set(le.GLOBAL_LIST_INDEXES["workflow"]["keyAttributes"])
        assert "allListPartition" not in token, "a by-date key sent to the workflow index is rejected by DynamoDB"

    def test_a_row_key_carries_exactly_the_walked_indexes_attributes(self):
        row = _row(7, group="g1")
        assert set(le._global_list_row_key(row, le.GLOBAL_LIST_INDEXES["group"])) == {
            "executionGroupId", "executionStartDate", "workflowExecutionId", "workflowDatabaseId:workflowId"}
        assert set(le._global_list_row_key(row)) == set(le.GLOBAL_LIST_INDEXES["date"]["keyAttributes"])
        assert le._global_list_row_key({"workflowExecutionId": "x"}, le.GLOBAL_LIST_INDEXES["workflow"]) is None


@pytest.mark.unit
class TestAContinuationFromAnotherIndexIsACallerError:
    def test_a_start_key_dynamodb_rejects_is_a_400_not_a_500(self):
        token = base64.b64encode(json.dumps({"allListPartition": "execution", "executionStartDate": "x",
                                             "workflowExecutionId": "e", "workflowDatabaseId:workflowId": "db:wf"}).encode()).decode()
        err = botocore.exceptions.ClientError(
            {"Error": {"Code": "ValidationException", "Message": "The provided starting key is invalid"}}, "Query")
        body, _calls, status = _run([], {"pageSize": "5", "workflowId": "wf", "workflowDatabaseId": "db",
                                         "startingToken": token}, raise_on_query=err)
        assert status == 400
        assert "startingToken" in json.dumps(body)

    def test_a_validation_exception_without_a_start_key_still_propagates(self):
        # CONTROL: only a caller-supplied key is the caller's fault; anything else is a server fault.
        err = botocore.exceptions.ClientError(
            {"Error": {"Code": "ValidationException", "Message": "bad"}}, "Query")
        with pytest.raises(botocore.exceptions.ClientError):
            _run([], {"pageSize": "5"}, raise_on_query=err)
