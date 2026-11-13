# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""#998: the excludeStatus query parameter of the asset and global execution lists, and abort of a
SKIPPED execution.

excludeStatus drops rows of the named statuses BEFORE the page is cut (the global list in its
FilterExpression, the asset list during its walk), so a page stays full and its cursor resumes past
the dropped rows. Validation answers fixed 400s. Without the parameter nothing changes."""

import base64
import json

import boto3
import pytest
from moto import mock_aws
from unittest.mock import MagicMock, patch

from tests.handlers.workflows.test_asset_execution_list_dual_cursor import (  # noqa: F401
    DB, ASSET, MOD, _allow_all, _cfg_rows, _decode, le,
)
from backend.backend.models import executions as em

SKIPPED = "SKIPPED"

# A fixed listing lower bound, older than every row these tests build, for the one test that
# compares two whole response messages.
PINNED_START_DATE = "2020-01-01T00:00:00Z"


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #

@pytest.mark.unit
class TestExcludeStatusValidation:
    def test_absent_and_empty_exclude_nothing(self):
        assert em.parse_list_status_filter({}) == ([], "")
        assert em.parse_list_status_filter({"excludeStatus": ""}) == ([], "")
        assert em.parse_list_status_filter(None) == ([], "")

    def test_one_or_a_list_is_parsed_and_deduplicated(self):
        assert em.parse_list_status_filter({"excludeStatus": "SKIPPED"}) == (["SKIPPED"], "")
        assert em.parse_list_status_filter({"excludeStatus": "SKIPPED, FAILED,SKIPPED"}) == (
            ["SKIPPED", "FAILED"], "")

    @pytest.mark.parametrize("value", ["skipped", "DONE", "SKIPPED,", "SKIPPED,BOGUS", ",", "<x>"])
    def test_an_unknown_value_gets_the_fixed_message(self, value):
        excluded, message = em.parse_list_status_filter({"excludeStatus": value})
        assert excluded == [] and message == em.EXCLUDE_STATUS_UNKNOWN_MESSAGE

    def test_excluding_the_selected_status_conflicts(self):
        assert em.parse_list_status_filter({"status": "SKIPPED", "excludeStatus": "SKIPPED,FAILED"}) == (
            [], em.EXCLUDE_STATUS_CONFLICT_MESSAGE)

    def test_a_different_status_and_exclusion_combine(self):
        assert em.parse_list_status_filter({"status": "RUNNING", "excludeStatus": "SKIPPED"}) == (
            ["SKIPPED"], "")


def _global_event(params):
    return {"requestContext": {"http": {"method": "GET", "path": "/workflows/executions"}},
            "queryStringParameters": params}


@pytest.mark.unit
class TestTheRoutesAnswerFixed400s:
    def test_the_global_route(self):
        with patch(f"{MOD}._enforce_api", return_value=True), \
                patch(f"{MOD}.get_global_executions") as listing:
            response = le.handle_global_list_request(_global_event({"excludeStatus": "NOPE"}))
        assert response["statusCode"] == 400
        assert json.loads(response["body"])["message"] == em.EXCLUDE_STATUS_UNKNOWN_MESSAGE
        listing.assert_not_called()

    def test_the_global_route_checks_api_access_first(self):
        with patch(f"{MOD}._enforce_api", return_value=False):
            response = le.handle_global_list_request(_global_event({"excludeStatus": "NOPE"}))
        assert response["statusCode"] == 403

    def test_the_asset_route(self):
        event = {"requestContext": {"http": {"method": "GET"}},
                 "pathParameters": {"databaseId": "db-one", "assetId": "asset1"},
                 "queryStringParameters": {"status": "FAILED", "excludeStatus": "FAILED"}}
        with patch(f"{MOD}.get_executions") as listing:
            response = le.handle_get_request(event)
        assert response["statusCode"] == 400
        assert json.loads(response["body"])["message"] == em.EXCLUDE_STATUS_CONFLICT_MESSAGE
        listing.assert_not_called()


# --------------------------------------------------------------------------- #
# Asset list: excluded during the walk
# --------------------------------------------------------------------------- #

def _run_asset(input_pages, statuses, query_params=None, cfg_pages=None, reported=None, refused=()):
    """The asset listing over canned input pages, with each execution's main row carrying the status in
    `statuses` (default SUCCEEDED); `reported` overrides the status the builder reports (a reconcile),
    and the caller is refused the executions in `refused`.
    Returns (message, ids handed to the builder, main-row reads)."""
    le.claims_and_roles = {"tokens": ["u1"]}
    inputs_table, cfg_table, main_table = MagicMock(), MagicMock(), MagicMock()
    inputs_table.query.side_effect = list(input_pages)
    cfg_table.query.side_effect = list(cfg_pages or [{"Items": []}])
    reads = []

    def _main_query(**kwargs):
        execution_id = kwargs["KeyConditionExpression"].get_expression()["values"][1]
        reads.append(execution_id)
        return {"Items": [{"workflowExecutionId": execution_id,
                           "executionStatus": statuses.get(execution_id, "SUCCEEDED")}]}

    main_table.query.side_effect = _main_query

    def _table(name):
        return {le.workflow_execution_inputs_table: inputs_table,
                le.workflow_execution_database_v2: main_table,
                le.workflow_execution_configuration_table: cfg_table}.get(name, MagicMock())

    handed = []

    def _echo(**kwargs):
        handed.extend(i["workflowExecutionId"] for i in kwargs["input_items"])
        return [{"workflowExecutionId": i["workflowExecutionId"],
                 "executionStatus": (reported or statuses).get(i["workflowExecutionId"], "SUCCEEDED")}
                for i in kwargs["input_items"]]

    with patch(f"{MOD}.dynamodb") as ddb, \
            patch.object(le.logger, "warning", MagicMock()), \
            patch(f"{MOD}.CasbinEnforcer", return_value=_allow_all()), \
            patch(f"{MOD}.get_asset_details",
                  side_effect=lambda d, a: {"databaseId": d, "assetId": a}), \
            patch(f"{MOD}._execution_access_check",
                  side_effect=lambda eid, *a, **k: (False, "denied") if eid in refused else (True, "")), \
            patch(f"{MOD}.build_execution_items", side_effect=_echo), \
            patch(f"{MOD}.sfn"):
        ddb.Table.side_effect = _table
        response = le.get_executions({}, DB, ASSET, "", "", query_params or {})
    assert response["statusCode"] == 200
    return json.loads(response["body"])["message"], handed, reads


def _input_rows(count):
    """Input rows newest-first (the GSI's order), so the walk order is also the listed order."""
    rows = []
    for i in range(count):
        seconds = 100000 - i
        rows.append({
            "workflowExecutionId": f"i{i:032d}",
            "databaseId:assetId": f"{DB}:{ASSET}",
            "databaseId:assetId:inputAssetFileKey": f"{DB}:{ASSET}:/f{i}.glb",
            "databaseId": DB, "assetId": ASSET, "inputAssetFileKey": f"/f{i}.glb",
            "workflowId": "wf", "workflowDatabaseId": "wf-db",
            "executionStartDate": f"2026-01-01T{seconds // 3600 % 24:02d}:{seconds // 60 % 60:02d}:"
                                  f"{seconds % 60:02d}Z",
        })
    return rows


def _ids(rows):
    return [r["workflowExecutionId"] for r in rows]


@pytest.mark.unit
class TestAssetListExclusion:
    def test_without_the_parameter_the_walk_reads_no_main_row_early(self):
        rows = _input_rows(5)
        statuses = {rows[1]["workflowExecutionId"]: SKIPPED}
        message, handed, _reads = _run_asset([{"Items": rows}], statuses)
        assert handed == _ids(rows)
        assert [i["workflowExecutionId"] for i in message["Items"]] == _ids(rows)
        assert "warnings" not in message and "NextToken" not in message

    def test_excluded_rows_do_not_count_against_the_page(self):
        rows = _input_rows(6)
        statuses = {rows[0]["workflowExecutionId"]: SKIPPED, rows[2]["workflowExecutionId"]: SKIPPED}
        message, handed, _reads = _run_asset(
            [{"Items": rows}], statuses, {"excludeStatus": "SKIPPED", "pageSize": "3"})
        kept = [r for r in _ids(rows) if r not in statuses]
        # The page is full with three kept rows, and the cursor is the last row kept.
        assert handed == kept[:3]
        token = _decode(message["NextToken"])
        assert token["inputsKey"]["workflowExecutionId"] == kept[2]

    def test_paging_across_pages_serves_every_kept_row_once(self):
        rows = _input_rows(9)
        statuses = {r["workflowExecutionId"]: SKIPPED for r in rows[::2]}
        kept = [r for r in _ids(rows) if r not in statuses]
        served = []
        message, handed, _ = _run_asset(
            [{"Items": rows[:4], "LastEvaluatedKey": {"k": 1}}, {"Items": rows[4:]}],
            statuses, {"excludeStatus": "SKIPPED", "pageSize": "2"})
        served += handed
        token = message["NextToken"]
        resume = rows[[r["workflowExecutionId"] for r in rows].index(handed[-1]) + 1:]
        message, handed, _ = _run_asset(
            [{"Items": resume}], statuses,
            {"excludeStatus": "SKIPPED", "pageSize": "10", "startingToken": token})
        served += handed
        assert served == kept
        assert "NextToken" not in message

    def test_the_check_budget_stops_the_page_with_a_cursor_and_a_warning(self):
        rows = _input_rows(le.MAX_EXECUTIONS_INSPECTED + 20)
        statuses = {r["workflowExecutionId"]: SKIPPED for r in rows}
        message, handed, reads = _run_asset([{"Items": rows}], statuses,
                                            {"excludeStatus": "SKIPPED"})
        assert handed == []
        assert len(reads) == le.MAX_EXECUTIONS_INSPECTED
        token = _decode(message["NextToken"])
        # Resumes at the last row decided, so the first undecided row is the next page's first.
        assert token["inputsKey"]["workflowExecutionId"] == rows[le.MAX_EXECUTIONS_INSPECTED - 1][
            "workflowExecutionId"]
        assert "checked against excludeStatus" in message["warnings"][0]

    @pytest.mark.parametrize("page_size", ["1", "2", "3"])
    def test_a_refused_rows_status_cannot_be_inferred(self, page_size):
        # Every SKIPPED row is one the caller is refused. With or without excludeStatus=SKIPPED the
        # page and its cursor are the same, so the parameter says nothing about those rows' status.
        rows = _input_rows(6)
        refused = {rows[0]["workflowExecutionId"], rows[2]["workflowExecutionId"],
                   rows[3]["workflowExecutionId"]}
        statuses = {eid: SKIPPED for eid in (rows[0]["workflowExecutionId"],
                                             rows[2]["workflowExecutionId"])}
        # Both listings pin filterStartDate, which the handler otherwise echoes back as "now minus
        # DEFAULT_EXECUTION_LOOKBACK_DAYS" to whole seconds: two calls either side of a second
        # boundary would differ in that field alone and fail the whole-message comparison below.
        # The bound predates every row, so the walk is the same as with the default.
        plain, plain_handed, _ = _run_asset(
            [{"Items": rows}], statuses,
            {"pageSize": page_size, "filterStartDate": PINNED_START_DATE}, refused=refused)
        excluded, excluded_handed, _ = _run_asset(
            [{"Items": rows}], statuses,
            {"pageSize": page_size, "excludeStatus": "SKIPPED",
             "filterStartDate": PINNED_START_DATE}, refused=refused)
        assert excluded == plain
        assert excluded_handed == plain_handed
        assert not set(plain_handed) & refused

    def test_a_refused_row_is_never_status_checked(self):
        rows = _input_rows(3)
        refused = {rows[1]["workflowExecutionId"]}
        _message, _handed, reads = _run_asset([{"Items": rows}], {}, {"excludeStatus": "SKIPPED"},
                                              refused=refused)
        assert rows[1]["workflowExecutionId"] not in reads

    def test_an_output_only_row_of_an_excluded_status_is_dropped(self):
        cfg = _cfg_rows(2)
        statuses = {cfg[0]["workflowExecutionId"]: SKIPPED}
        message, handed, _ = _run_asset([{"Items": []}], statuses, {"excludeStatus": "SKIPPED"},
                                        cfg_pages=[{"Items": cfg}])
        assert handed == [cfg[1]["workflowExecutionId"]]

    def test_a_row_reconciled_into_an_excluded_status_is_dropped_after_the_walk(self):
        rows = _input_rows(2)
        # Stored RUNNING for both, so the walk keeps both; the builder's reconcile reports FAILED.
        reported = {rows[0]["workflowExecutionId"]: "FAILED"}
        message, handed, _ = _run_asset([{"Items": rows}], {}, {"excludeStatus": "FAILED"},
                                        reported=reported)
        assert handed == _ids(rows)
        assert [i["workflowExecutionId"] for i in message["Items"]] == [rows[1]["workflowExecutionId"]]


# --------------------------------------------------------------------------- #
# Global list: excluded in the FilterExpression
# --------------------------------------------------------------------------- #

def _main_rows(count):
    rows = []
    for i in range(count):
        rows.append({
            "workflowExecutionId": f"g{i:031d}", "workflowDatabaseId:workflowId": "db:wf",
            "workflowId": "wf", "workflowDatabaseId": "db", "allListPartition": "execution",
            "executionStartDate": f"2026-11-0{1 + i // 60}T00:{i % 60:02d}:00Z",
            "executionStatus": SKIPPED if i % 3 == 0 else "SUCCEEDED",
        })
    return rows


def _global_table():
    resource = boto3.resource("dynamodb", region_name="us-east-1")
    table = resource.create_table(
        TableName=le.workflow_execution_database_v2, BillingMode="PAY_PER_REQUEST",
        KeySchema=[{"AttributeName": "workflowExecutionId", "KeyType": "HASH"},
                   {"AttributeName": "workflowDatabaseId:workflowId", "KeyType": "RANGE"}],
        AttributeDefinitions=[
            {"AttributeName": "workflowExecutionId", "AttributeType": "S"},
            {"AttributeName": "workflowDatabaseId:workflowId", "AttributeType": "S"},
            {"AttributeName": "allListPartition", "AttributeType": "S"},
            {"AttributeName": "executionStartDate", "AttributeType": "S"}],
        GlobalSecondaryIndexes=[{
            "IndexName": "WorkflowExecutionsByDateGSI",
            "KeySchema": [{"AttributeName": "allListPartition", "KeyType": "HASH"},
                          {"AttributeName": "executionStartDate", "KeyType": "RANGE"}],
            "Projection": {"ProjectionType": "ALL"}}])
    return resource, table


def _run_global(resource, params):
    le.claims_and_roles = {"tokens": ["u1"]}
    with patch.object(le, "dynamodb", resource), \
            patch(f"{MOD}.CasbinEnforcer", return_value=_allow_all()), \
            patch(f"{MOD}._execution_visible_to_caller", return_value=True), \
            patch(f"{MOD}.get_workflow_execution_configuration_row", return_value={}):
        response = le.get_global_executions({}, dict(params, filterStartDate="2026-01-01T00:00:00Z"))
    assert response["statusCode"] == 200
    return json.loads(response["body"])["message"]


@pytest.mark.unit
class TestGlobalListExclusion:
    @mock_aws
    def test_pages_are_full_of_kept_rows_and_page_to_the_end(self):
        resource, table = _global_table()
        rows = _main_rows(30)
        for row in rows:
            table.put_item(Item=row)
        kept = sorted((r for r in rows if r["executionStatus"] != SKIPPED),
                      key=lambda r: r["executionStartDate"], reverse=True)
        served, token = [], None
        while True:
            params = {"excludeStatus": "SKIPPED", "pageSize": "7"}
            if token:
                params["startingToken"] = token
            message = _run_global(resource, params)
            assert all(i["executionStatus"] != SKIPPED for i in message["Items"])
            served += _ids(message["Items"])
            token = message.get("NextToken")
            if not token:
                break
            assert len(message["Items"]) == 7
        assert served == _ids(kept)

    @mock_aws
    def test_without_the_parameter_skipped_rows_are_listed(self):
        resource, table = _global_table()
        rows = _main_rows(6)
        for row in rows:
            table.put_item(Item=row)
        message = _run_global(resource, {"pageSize": "50"})
        assert len(message["Items"]) == 6
        assert not any("skip" in k.lower() and k != "executionStatus"
                       for i in message["Items"] for k in i if i["executionStatus"] != SKIPPED)

    def test_no_parameter_builds_the_same_query_as_before(self):
        le.claims_and_roles = {"tokens": ["u1"]}
        table = MagicMock()
        table.query.return_value = {"Items": []}
        with patch(f"{MOD}.dynamodb") as ddb, \
                patch(f"{MOD}.CasbinEnforcer", return_value=_allow_all()):
            ddb.Table.return_value = table
            # includeSystem=true keeps #1121's default system-run filter out of the query, so the
            # comparison isolates excludeStatus (the system filter has its own query-shape tests).
            le.get_global_executions({}, {"status": "FAILED", "includeSystem": "true"})
            with_status = table.query.call_args.kwargs
            le.get_global_executions({}, {"includeSystem": "true"})
            plain = table.query.call_args.kwargs
        assert "FilterExpression" not in plain
        assert with_status["FilterExpression"].get_expression()["operator"] == "="

    @mock_aws
    @pytest.mark.parametrize("page_size", ["1", "2", "4"])
    def test_exclusion_says_no_more_than_the_status_filter(self, page_size):
        # The global list's exclusion is a query filter, like `status`. With rows that are SUCCEEDED or
        # SKIPPED, excludeStatus=SKIPPED and status=SUCCEEDED produce the same pages and cursors,
        # including when the caller is refused some of the rows, so it gives no new signal.
        resource, table = _global_table()
        rows = _main_rows(12)
        for row in rows:
            table.put_item(Item=row)
        refused = {r["workflowExecutionId"] for r in rows[1::4]} | {
            r["workflowExecutionId"] for r in rows if r["executionStatus"] == SKIPPED}

        def _pages(params):
            pages, token = [], None
            with patch(f"{MOD}._execution_visible_to_caller",
                       side_effect=lambda eid, *a, **k: eid not in refused):
                while True:
                    query = dict(params, pageSize=page_size)
                    if token:
                        query["startingToken"] = token
                    le.claims_and_roles = {"tokens": ["u1"]}
                    with patch.object(le, "dynamodb", resource), \
                            patch(f"{MOD}.CasbinEnforcer", return_value=_allow_all()), \
                            patch(f"{MOD}.get_workflow_execution_configuration_row", return_value={}):
                        response = le.get_global_executions(
                            {}, dict(query, filterStartDate="2026-01-01T00:00:00Z"))
                    message = json.loads(response["body"])["message"]
                    token = message.get("NextToken")
                    pages.append((_ids(message["Items"]), token))
                    if not token:
                        return pages

        assert _pages({"excludeStatus": "SKIPPED"}) == _pages({"status": "SUCCEEDED"})

    def test_the_matcher_safety_net(self):
        assert not le._global_list_matches_filters({"executionStatus": SKIPPED},
                                                   {"excludeStatus": [SKIPPED]})
        assert le._global_list_matches_filters({"executionStatus": "FAILED"},
                                               {"excludeStatus": [SKIPPED]})
        assert le._global_list_matches_filters({"executionStatus": SKIPPED}, {})


# --------------------------------------------------------------------------- #
# Abort of a SKIPPED execution
# --------------------------------------------------------------------------- #

SKIP_ROW = {"workflowExecutionId": "s" * 32, "workflowDatabaseId:workflowId": "db:wf",
            "workflowId": "wf", "workflowDatabaseId": "db", "executionStatus": SKIPPED,
            "executionStopDate": "2026-11-06T10:00:00Z", "workflow_execution_arn": ""}


def _abort(main_item, allowed=True):
    with patch(f"{MOD}.get_execution_main_row", return_value=dict(main_item) if main_item else None), \
            patch(f"{MOD}.authorize_abort", return_value=(allowed, "" if allowed else "denied")), \
            patch(f"{MOD}.get_pipeline_execution_rows", return_value=[]) as pipelines, \
            patch(f"{MOD}._persist_reconciled_main_row") as persist, \
            patch(f"{MOD}.sfn") as sfn, \
            patch(f"{MOD}.el.release_locks_for_execution") as release, \
            patch(f"{MOD}.log_actions") as audit:
        response = le.abort_execution({}, main_item["workflowExecutionId"] if main_item else "x" * 32)
    return response, {"pipelines": pipelines, "persist": persist, "sfn": sfn, "release": release,
                      "audit": audit}


@pytest.mark.unit
class TestAbortOfASkippedExecution:
    def test_a_skipped_execution_answers_400_and_writes_nothing(self):
        response, spies = _abort(SKIP_ROW)
        assert response["statusCode"] == 400
        assert json.loads(response["body"])["message"] == le.SKIPPED_EXECUTION_ABORT_MESSAGE
        spies["persist"].assert_not_called()
        spies["release"].assert_not_called()
        spies["sfn"].stop_execution.assert_not_called()
        spies["pipelines"].assert_not_called()
        spies["audit"].assert_not_called()

    def test_a_refused_caller_gets_the_same_refusal_as_before(self):
        response, spies = _abort(SKIP_ROW, allowed=False)
        assert response["statusCode"] == 403
        spies["persist"].assert_not_called()

    @pytest.mark.parametrize("status", ["SUCCEEDED", "FAILED", "ABORTED", "TIMED_OUT"])
    def test_other_terminal_statuses_keep_the_idempotent_200(self, status):
        response, spies = _abort(dict(SKIP_ROW, executionStatus=status,
                                      workflow_execution_arn="arn:x"))
        assert response["statusCode"] == 200
        spies["sfn"].stop_execution.assert_called_once()

    def test_a_group_abort_reports_a_skipped_member_and_stops_the_others(self):
        running = dict(SKIP_ROW, workflowExecutionId="r" * 32, executionStatus="RUNNING",
                       executionStopDate="")
        aborted = []

        def _member_abort(_event, execution_id):
            aborted.append(execution_id)
            return {"statusCode": 200, "body": json.dumps({"message": "Execution aborted"})}

        with patch(f"{MOD}._executions_in_group", return_value=[dict(SKIP_ROW), running]), \
                patch(f"{MOD}.authorize_abort", return_value=(True, "")), \
                patch(f"{MOD}.abort_execution", side_effect=_member_abort), \
                patch(f"{MOD}.log_actions"):
            response = le.abort_group({}, "g1")
        assert response["statusCode"] == 200
        results = json.loads(response["body"])["message"]["results"]
        assert {"executionId": SKIP_ROW["workflowExecutionId"], "status": "skipped-terminal"} in results
        assert {"executionId": "r" * 32, "status": "aborted"} in results
        assert aborted == ["r" * 32]
