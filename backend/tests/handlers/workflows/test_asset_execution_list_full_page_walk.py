# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The asset-scoped executions list, paged to exhaustion against real DynamoDB indexes.

The unit tests in test_asset_execution_list_dual_cursor.py drive the walk over canned query pages,
which pins the cursor and token mechanics but cannot answer the question the caller actually asks:
after paging from the first token to the last, did I see every one of this asset's executions, exactly
once? Both halves of that are reachable defects, and they pull in opposite directions — the guard that
stops a dual-role execution (an input for the asset AND its output target) being served twice is also
what can withhold an output-only execution for good.

So the walk here runs against moto, where the two GSIs are really keyed and the synthesized
continuation cursors really position the next query, and the assertions are over the UNION of every
page: no id twice (S2-BACKEND-043), and no id missing.

executionService resolves its table names at import (mirrors test_executionService_wb53.py)."""

import json
import os
from datetime import datetime, timedelta, timezone

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

DB, ASSET = "db", "A"
COMPOSITE = "wf-db:wf"

# Dates are relative to now: the listing's default lower bound is 90 days back, so a fixed calendar
# date would drop out of the window and the walk would legitimately return nothing.
_NOW = datetime.now(timezone.utc)


def _iso(days_ago):
    return (_NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


# Four executions that READ the asset, oldest last. Two of them are also its output target, so they
# appear in BOTH indexes — the shape that can be served twice.
INPUT_EXECUTIONS = [(f"i{n:031d}", _iso(19 + n)) for n in range(1, 5)]
DUAL_ROLE = {INPUT_EXECUTIONS[0][0], INPUT_EXECUTIONS[3][0]}
# Eight executions that only WROTE to the asset (inputFileArity 'none' writes no input rows), and are
# NEWER than every input-direction one — the ordinary case, since a pipeline writing into an asset is
# usually its most recent activity.
OUTPUT_ONLY_EXECUTIONS = [(f"o{n:031d}", _iso(n)) for n in range(1, 9)]
EVERY_EXECUTION = {eid for eid, _ in INPUT_EXECUTIONS + OUTPUT_ONLY_EXECUTIONS}


def _create_tables(ddb):
    """The three tables the asset listing reads, with the two indexes it queries."""
    ddb.create_table(
        TableName=le.workflow_execution_database_v2,
        KeySchema=[{"AttributeName": "workflowExecutionId", "KeyType": "HASH"},
                   {"AttributeName": "workflowDatabaseId:workflowId", "KeyType": "RANGE"}],
        AttributeDefinitions=[
            {"AttributeName": "workflowExecutionId", "AttributeType": "S"},
            {"AttributeName": "workflowDatabaseId:workflowId", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST")
    ddb.create_table(
        TableName=le.workflow_execution_inputs_table,
        KeySchema=[{"AttributeName": "workflowExecutionId", "KeyType": "HASH"},
                   {"AttributeName": "databaseId:assetId:inputAssetFileKey", "KeyType": "RANGE"}],
        AttributeDefinitions=[
            {"AttributeName": "workflowExecutionId", "AttributeType": "S"},
            {"AttributeName": "databaseId:assetId:inputAssetFileKey", "AttributeType": "S"},
            {"AttributeName": "databaseId:assetId", "AttributeType": "S"},
            {"AttributeName": "executionStartDate", "AttributeType": "S"}],
        GlobalSecondaryIndexes=[{
            "IndexName": "WorkflowExecInputsByAssetGSI",
            "KeySchema": [{"AttributeName": "databaseId:assetId", "KeyType": "HASH"},
                          {"AttributeName": "executionStartDate", "KeyType": "RANGE"}],
            "Projection": {"ProjectionType": "ALL"}}],
        BillingMode="PAY_PER_REQUEST")
    ddb.create_table(
        TableName=le.workflow_execution_configuration_table,
        KeySchema=[{"AttributeName": "workflowExecutionId", "KeyType": "HASH"},
                   {"AttributeName": "recordType", "KeyType": "RANGE"}],
        AttributeDefinitions=[
            {"AttributeName": "workflowExecutionId", "AttributeType": "S"},
            {"AttributeName": "recordType", "AttributeType": "S"},
            {"AttributeName": "outputDatabaseId:outputAssetId", "AttributeType": "S"},
            {"AttributeName": "executionStartDate", "AttributeType": "S"}],
        GlobalSecondaryIndexes=[{
            "IndexName": "WorkflowExecConfigByOutputAssetGSI",
            "KeySchema": [{"AttributeName": "outputDatabaseId:outputAssetId", "KeyType": "HASH"},
                          {"AttributeName": "executionStartDate", "KeyType": "RANGE"}],
            "Projection": {"ProjectionType": "ALL"}}],
        BillingMode="PAY_PER_REQUEST")


def _seed(ddb, inputs=INPUT_EXECUTIONS, output_only=OUTPUT_ONLY_EXECUTIONS, dual_role=DUAL_ROLE):
    inputs_table = ddb.Table(le.workflow_execution_inputs_table)
    cfg_table = ddb.Table(le.workflow_execution_configuration_table)
    main_table = ddb.Table(le.workflow_execution_database_v2)
    for index, (execution_id, date) in enumerate(inputs):
        inputs_table.put_item(Item={
            "workflowExecutionId": execution_id,
            "databaseId:assetId": f"{DB}:{ASSET}",
            "databaseId:assetId:inputAssetFileKey": f"{DB}:{ASSET}:/f{index}.glb",
            "databaseId": DB, "assetId": ASSET, "inputAssetFileKey": f"/f{index}.glb",
            "workflowId": "wf", "workflowDatabaseId": "wf-db",
            "executionStartDate": date})
    # A dual-role execution's configuration row carries the same start date as its input rows — it is
    # one execution, so the two indexes place it at the same point in the order.
    output_rows = [(eid, date) for eid, date in inputs if eid in dual_role] + list(output_only)
    for execution_id, date in output_rows:
        cfg_table.put_item(Item={
            "workflowExecutionId": execution_id, "recordType": "configuration",
            "outputDatabaseId:outputAssetId": le.er.output_asset_partition_key(DB, ASSET),
            "executionStartDate": date})
    for execution_id, date in list(inputs) + list(output_only):
        # Terminal rows, so the listing's lazy reconcile never polls Step Functions — the walk is what
        # is under test here.
        main_table.put_item(Item={
            "workflowExecutionId": execution_id, "workflowDatabaseId:workflowId": COMPOSITE,
            "workflowDatabaseId": "wf-db", "workflowId": "wf",
            "workflow_execution_arn": "arn:ex",
            "executionStatus": "SUCCEEDED", "executionStartDate": date, "executionStopDate": date,
            "executionError": "", "executionLog": "", "lastSfnSyncCheckDate": date})


def _walk(page_size, max_pages=20, **seed_kwargs):
    """Page the asset listing from the first request to the last; returns the ids served per page."""
    import boto3
    from moto import mock_aws
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        _create_tables(ddb)
        _seed(ddb, **seed_kwargs)
        le.claims_and_roles = {"tokens": ["u1"]}
        enforcer = MagicMock()
        enforcer.enforce.return_value = True
        pages, token = [], None
        with patch.object(le, "dynamodb", ddb), \
                patch(f"{MOD}.CasbinEnforcer", return_value=enforcer), \
                patch(f"{MOD}.get_asset_details",
                      side_effect=lambda d, a: {"databaseId": d, "assetId": a}), \
                patch(f"{MOD}._execution_access_check", return_value=(True, "")), \
                patch(f"{MOD}.sfn"):
            for _ in range(max_pages):
                query_params = {"pageSize": str(page_size)}
                if token:
                    query_params["startingToken"] = token
                response = le.get_executions({}, DB, ASSET, "", "", query_params)
                assert response["statusCode"] == 200, response
                message = json.loads(response["body"])["message"]
                pages.append([i["workflowExecutionId"] for i in message["Items"]])
                token = message.get("NextToken")
                if not token:
                    break
            else:
                raise AssertionError(f"the walk did not finish within {max_pages} pages: {pages}")
        return pages


def _served(pages):
    return [execution_id for page in pages for execution_id in page]


def _output_query_date_operands(page_size, filter_end_date=None, max_pages=20, **seed_kwargs):
    """Page the listing to exhaustion, recording the date operands of every output-GSI query it issues.

    The output query carries one date operand when it is bounded only by the listing's own lower bound
    (`gte(filterStartDate)`) and two when a `between` narrows it. The high-water mark narrowing the
    range is what withheld output-only executions for good, so counting the operands says which rule
    the walk ran under — an assertion the served ids alone cannot make on a fixture where the mark
    happens to cover nothing.
    """
    import boto3
    from moto import mock_aws

    def _dates(condition):
        operands, stack = [], [condition]
        while stack:
            node = stack.pop()
            for value in getattr(node, "_values", ()):
                if isinstance(value, str):
                    operands.append(value)
                elif hasattr(value, "_values"):
                    stack.append(value)
        return [v for v in operands if v.endswith("Z")]

    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        _create_tables(ddb)
        _seed(ddb, **seed_kwargs)
        le.claims_and_roles = {"tokens": ["u1"]}
        enforcer = MagicMock()
        enforcer.enforce.return_value = True
        recorded = []

        class _Recorder:
            """The real moto table, with the output-GSI query's key condition recorded on the way past."""

            def __init__(self, table, name):
                self._table, self._name = table, name

            def __getattr__(self, item):
                return getattr(self._table, item)

            def query(self, **kwargs):
                if self._name == le.workflow_execution_configuration_table:
                    recorded.append(_dates(kwargs["KeyConditionExpression"]))
                return self._table.query(**kwargs)

        proxy = MagicMock()
        proxy.Table.side_effect = lambda name: _Recorder(ddb.Table(name), name)
        token = None
        with patch.object(le, "dynamodb", proxy), \
                patch(f"{MOD}.CasbinEnforcer", return_value=enforcer), \
                patch(f"{MOD}.get_asset_details",
                      side_effect=lambda d, a: {"databaseId": d, "assetId": a}), \
                patch(f"{MOD}._execution_access_check", return_value=(True, "")), \
                patch(f"{MOD}.sfn"):
            for _ in range(max_pages):
                query_params = {"pageSize": str(page_size)}
                if filter_end_date:
                    query_params["filterEndDate"] = filter_end_date
                if token:
                    query_params["startingToken"] = token
                response = le.get_executions({}, DB, ASSET, "", "", query_params)
                assert response["statusCode"] == 200, response
                token = json.loads(response["body"])["message"].get("NextToken")
                if not token:
                    break
            else:
                raise AssertionError(f"the walk did not finish within {max_pages} pages")
        return recorded


@pytest.mark.unit
class TestTheWholeWalkServesEveryExecutionOnce:
    """Paging from the first token to the last must be a partition of the asset's history.

    A duplicate is recoverable client-side; a row no page returns is not, and neither is detectable
    from a single page. The two are governed by the same high-water mark, so they are asserted
    together, over the union of the pages, at page sizes that stop the walk in different places.
    """

    @pytest.mark.aws
    @pytest.mark.parametrize("page_size", [2, 3, 4])
    def test_an_output_only_execution_survives_a_page_that_caps_on_the_inputs(self, page_size):
        # The input walk caps on the first page, so that page never reaches the output query. The next
        # page serves the remaining input rows and DRAINS them — at which point the mark it inherited
        # is the oldest date served, not "everything newer was served", because no output-only
        # execution has been served at all. Bounding the output range there returns nothing but the
        # already-served rows, the page does not cap, and the walk ends: every output-only execution,
        # all of them newer than the mark, is lost with no token left to reach it by.
        pages = _walk(page_size)
        served = _served(pages)
        missing = sorted({eid for eid, _ in OUTPUT_ONLY_EXECUTIONS} - set(served))
        assert not missing, (
            f"pageSize={page_size} lost output-only executions no later page can reach: {missing} "
            f"(pages: {pages})")
        assert set(served) == EVERY_EXECUTION, f"pages: {pages}"

    @pytest.mark.aws
    @pytest.mark.parametrize("page_size", [2, 3, 4, 5, 6])
    def test_no_execution_is_served_on_two_pages(self, page_size):
        # S2-BACKEND-043's invariant, stated over the whole walk rather than over one token's contents.
        # At pageSize 5 and 6 the inputs drain on page 1 and the output walk then caps, which is the
        # exact shape that emitted a token with no mark and re-served every dual-role execution below
        # the output cursor.
        pages = _walk(page_size)
        served = _served(pages)
        duplicates = sorted({eid for eid in served if served.count(eid) > 1})
        assert not duplicates, (
            f"pageSize={page_size} served these executions on more than one page: {duplicates} "
            f"(pages: {pages})")

    @pytest.mark.aws
    @pytest.mark.parametrize("page_size", [5, 6])
    def test_a_page_that_drains_its_inputs_and_then_caps_still_serves_everything(self, page_size):
        # The positive control for the withholding half: these page sizes let page 1 drain the inputs
        # AND reach the output query, so the following pages decide per row whether the input
        # direction already served each candidate. That test must not withhold a row that was never
        # served.
        pages = _walk(page_size)
        assert set(_served(pages)) == EVERY_EXECUTION, f"pages: {pages}"

    @pytest.mark.aws
    def test_a_dual_role_execution_is_served_exactly_once(self):
        # Named on its own because it is the row the two halves fight over: it has an input row AND is
        # the output target, so the input direction must serve it and the output direction must not.
        pages = _walk(4)
        served = _served(pages)
        for execution_id in DUAL_ROLE:
            assert served.count(execution_id) == 1, (
                f"the dual-role execution {execution_id} was served {served.count(execution_id)} "
                f"times (pages: {pages})")

    @pytest.mark.aws
    def test_an_asset_with_only_output_only_executions_is_fully_served(self):
        # Control on the other extreme: with no input rows at all there is no mark to inherit, so the
        # walk is the output direction alone. A results-only asset's whole history lives here.
        pages = _walk(3, inputs=[], dual_role=set())
        assert set(_served(pages)) == {eid for eid, _ in OUTPUT_ONLY_EXECUTIONS}, f"pages: {pages}"

    @pytest.mark.aws
    def test_an_asset_with_only_input_executions_is_fully_served(self):
        # The mirror control: no configuration rows, so the output query contributes nothing and the
        # walk must still page the input direction to exhaustion.
        pages = _walk(2, output_only=[], dual_role=set())
        assert set(_served(pages)) == {eid for eid, _ in INPUT_EXECUTIONS}, f"pages: {pages}"

    @pytest.mark.aws
    def test_a_page_size_above_the_history_serves_it_in_one_page(self):
        # Control that the paging under test is the caller's, not an unavoidable one: asked for more
        # than the asset has, the listing answers in a single page with no token.
        pages = _walk(50)
        assert len(pages) == 1, f"expected one page: {pages}"
        assert set(pages[0]) == EVERY_EXECUTION

    @pytest.mark.aws
    @pytest.mark.parametrize("page_size", [1, 2, 3, 4, 5, 6])
    def test_no_page_of_the_walk_narrows_the_output_range_by_the_mark(self, page_size):
        # The mechanism behind the two assertions above, pinned where a future change would break it.
        # The output query is only reached on a page whose input walk did not fill the budget — which is
        # a page whose input side has drained — so the mark can never mean "everything newer was
        # served" there, and narrowing by it is what lost the output-only executions. Asserted over
        # EVERY query of the walk, so a page size that reaches the output side by a different route is
        # covered too. Without this, the union assertions still pass on a fixture whose mark happens to
        # cover nothing, and the narrowing returns unnoticed.
        operands = _output_query_date_operands(page_size)
        assert operands, "fixture: the walk must reach the output query or this proves nothing"
        narrowed = [dates for dates in operands if len(dates) > 1]
        assert not narrowed, (
            f"pageSize={page_size}: the output range was narrowed by the high-water mark {narrowed}, "
            f"which withholds every output-only execution above it with no cursor left to reach them")

    @pytest.mark.aws
    def test_the_callers_end_date_does_narrow_the_output_range(self):
        # The positive control for the assertion above: it must fail when a bound IS applied, or it
        # proves only that the operand count never moves. The caller's filterEndDate is a real upper
        # bound and produces exactly the two-operand `between` shape the mark would have produced.
        operands = _output_query_date_operands(4, filter_end_date=_iso(3))
        assert operands, "fixture: the walk must reach the output query"
        assert all(len(dates) == 2 for dates in operands), (
            f"a caller end date must bound the output range: {operands}")

    @pytest.mark.aws
    def test_an_end_date_still_bounds_both_directions_of_the_walk(self):
        # The caller's own upper bound is a different thing from the internal high-water mark and must
        # keep working: the three newest output-only executions fall outside it and the rest do not.
        import boto3
        from moto import mock_aws
        with mock_aws():
            ddb = boto3.resource("dynamodb", region_name="us-east-1")
            _create_tables(ddb)
            _seed(ddb)
            le.claims_and_roles = {"tokens": ["u1"]}
            enforcer = MagicMock()
            enforcer.enforce.return_value = True
            served, token = [], None
            with patch.object(le, "dynamodb", ddb), \
                    patch(f"{MOD}.CasbinEnforcer", return_value=enforcer), \
                    patch(f"{MOD}.get_asset_details",
                          side_effect=lambda d, a: {"databaseId": d, "assetId": a}), \
                    patch(f"{MOD}._execution_access_check", return_value=(True, "")), \
                    patch(f"{MOD}.sfn"):
                for _ in range(20):
                    query_params = {"pageSize": "4", "filterEndDate": _iso(3)}
                    if token:
                        query_params["startingToken"] = token
                    response = le.get_executions({}, DB, ASSET, "", "", query_params)
                    assert response["statusCode"] == 200, response
                    message = json.loads(response["body"])["message"]
                    served.extend(i["workflowExecutionId"] for i in message["Items"])
                    token = message.get("NextToken")
                    if not token:
                        break
        excluded = {eid for eid, date in OUTPUT_ONLY_EXECUTIONS if date > _iso(3)}
        assert excluded, "fixture: the end date must exclude something or this proves nothing"
        assert set(served) == EVERY_EXECUTION - excluded, served
