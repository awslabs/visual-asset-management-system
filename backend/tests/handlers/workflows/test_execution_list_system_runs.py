# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Runs of system workflows are hidden from the execution lists by default (#1121).

The launch copies the workflow's stored isSystem onto the execution main row. Both list routes
(GET /workflows/executions and the asset route) then leave those rows out unless the caller sends
includeSystem=true, or the listing is scoped to ONE workflow that is itself a system workflow the
caller may GET. Each way an existing caller could break is pinned here:

  - a legacy row (no isSystem attribute) or any row whose flag is not exactly True stays listed;
  - the server-side FilterExpression and the Python safety net agree;
  - a window dense with system rows pages without skipping or repeating a user row, and the
    continuation is the same row-key shape as before;
  - an explicit includeSystem=false still hides a system workflow's runs, and an invalid value is a 400;
  - the default flip never applies for a workflow the caller cannot GET;
  - per-row visibility (a restricted role) is unchanged by the flag.

executionService resolves its table names at import (mirrors test_global_list_index_routing.py)."""

import base64
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

from backend.backend.common.workflows import executionRecords as er  # noqa: E402
from backend.backend.handlers.workflows import executionService as le  # noqa: E402
from backend.backend.models import executions as em  # noqa: E402

MOD = "backend.backend.handlers.workflows.executionService"
PAGE_SIZE = 5


@pytest.fixture(autouse=True)
def _clear_caches():
    le._asset_details_cache.clear()
    le._authz_decision_cache.clear()
    le._disarm_authz_entity_budget()
    yield
    le._asset_details_cache.clear()
    le._authz_decision_cache.clear()
    le._disarm_authz_entity_budget()


def _row(name, i, system=None, workflow="wf", db="db"):
    """A main row with every key attribute the walked indexes name. `system` None omits the attribute
    (a row written before #1121)."""
    row = {"workflowExecutionId": f"{name}{i:03d}", "workflowId": workflow, "workflowDatabaseId": db,
           "workflowDatabaseId:workflowId": f"{db}:{workflow}",
           "allListPartition": er.ALL_EXECUTIONS_LIST_PARTITION,
           "executionStatus": "SUCCEEDED",
           "executionStopDate": "2026-01-01T01:00:00Z",
           "executionStartDate": f"2026-01-01T00:{(999 - i) // 60 % 60:02d}:{(999 - i) % 60:02d}Z"}
    if system is not None:
        row["isSystem"] = system
    return row


def _page(rows, last_key=None):
    page = {"Items": rows}
    if last_key is not None:
        page["LastEvaluatedKey"] = last_key
    return page


def _allow_all():
    e = MagicMock()
    e.enforce.return_value = True
    e.enforceAPI.return_value = True
    return e


def _deny_workflow(workflow_id):
    """Allows everything except GET on the named workflow object."""
    e = MagicMock()
    e.enforceAPI.return_value = True
    e.enforce.side_effect = lambda obj, action, *a, **k: not (
        obj.get("object__type") == "workflow" and obj.get("workflowId") == workflow_id)
    return e


def _apply_filter(rows, expression):
    """Evaluate the boto3 FilterExpression the handler built over `rows` the way DynamoDB would, for
    the operators it can contain (eq, ne, attribute_not_exists, AND, OR). Lets a scripted table drop
    exactly the rows the real server-side filter drops."""
    if expression is None:
        return rows

    def matches(row, cond):
        spec = cond.get_expression()
        op = spec["operator"]
        values = spec["values"]
        if op == "AND":
            return all(matches(row, v) for v in values)
        if op == "OR":
            return any(matches(row, v) for v in values)
        name = values[0].name
        if op == "attribute_not_exists":
            return name not in row
        if name not in row:
            # DynamoDB: a comparison against a missing attribute is false, for <> as well.
            return False
        # DynamoDB compares typed values: the number 1 is not the boolean true.
        same = type(row[name]) is type(values[1]) and row[name] == values[1]
        if op == "=":
            return same
        if op == "<>":
            return not same
        raise AssertionError(f"unexpected operator {op}")

    return [r for r in rows if matches(r, expression)]


class _ScriptedIndex:
    """A newest-first index over `rows` that honours Limit, ExclusiveStartKey and FilterExpression
    like DynamoDB (Limit counts rows EVALUATED, before the filter)."""

    def __init__(self, rows, limit=None):
        self.rows = rows
        self.limit = limit
        self.calls = []

    def query(self, **kwargs):
        self.calls.append(dict(kwargs))
        start = 0
        key = kwargs.get("ExclusiveStartKey")
        if key:
            ids = [r["workflowExecutionId"] for r in self.rows]
            start = ids.index(key["workflowExecutionId"]) + 1
        limit = self.limit or kwargs.get("Limit") or len(self.rows)
        window = self.rows[start:start + limit]
        page = {"Items": _apply_filter(window, kwargs.get("FilterExpression"))}
        if start + limit < len(self.rows) and window:
            page["LastEvaluatedKey"] = le._global_list_row_key(window[-1], le.GLOBAL_LIST_INDEXES["date"])
        return page

    def get_item(self, **_kwargs):
        return {}


def _run_global(table, query, enforcer=None, workflow_definition=None):
    le.claims_and_roles = {"tokens": ["u1"]}
    with patch(f"{MOD}.dynamodb") as ddb, \
         patch(f"{MOD}.CasbinEnforcer", return_value=enforcer or _allow_all()), \
         patch(f"{MOD}.get_execution_input_assets", side_effect=lambda eid: [("db", "asset-1")]), \
         patch(f"{MOD}.get_workflow_execution_configuration_row", return_value={}), \
         patch(f"{MOD}.get_workflow_definition",
               side_effect=lambda d, w: dict(workflow_definition or {})), \
         patch(f"{MOD}.get_asset_details",
               side_effect=lambda d, a: {"databaseId": d, "assetId": a, "assetName": a}):
        ddb.Table.return_value = table
        ddb.batch_get_item.side_effect = lambda RequestItems: {
            "Responses": {name: [{"databaseId": k["databaseId"], "assetId": k["assetId"],
                                  "assetName": k["assetId"]} for k in spec["Keys"]]
                          for name, spec in RequestItems.items()}}
        response = le.get_global_executions({}, query)
    body = json.loads(response["body"])
    return response["statusCode"], body.get("message", body)


def _ids(message):
    return [r["workflowExecutionId"] for r in message["Items"]]


# ---------------------------------------------------------------------------------------------
@pytest.mark.unit
class TestTheLaunchStampsTheFlag:
    def test_the_builder_writes_the_workflow_flag(self):
        args = ("E1", "db", "wf", "arn:sm", "arn:ex", "2026-01-01T00:00:00Z", "RUNNING",
                "user@x", "Manual", "arn:lg")
        assert er.build_workflow_execution_record(*args, is_system=True)["isSystem"] is True
        assert er.build_workflow_execution_record(*args)["isSystem"] is False

    def test_only_a_boolean_true_marks_a_system_run(self):
        args = ("E1", "db", "wf", "arn:sm", "arn:ex", "2026-01-01T00:00:00Z", "RUNNING",
                "user@x", "Manual", "arn:lg")
        for value in ("true", 1, "True", None, MagicMock()):
            assert er.build_workflow_execution_record(*args, is_system=value)["isSystem"] is False

    def test_the_record_model_declares_the_flag_and_defaults_it_false(self):
        record = em.WorkflowExecutionRecord(
            workflowExecutionId="E1", workflowId="wf", workflowDatabaseId="db")
        assert record.isSystem is False


# ---------------------------------------------------------------------------------------------
@pytest.mark.unit
class TestTheGlobalListHidesSystemRunsByDefault:
    def test_default_hides_system_rows_and_keeps_legacy_and_user_rows(self):
        rows = [_row("s", 0, True), _row("u", 1, False), _row("l", 2, None), _row("s", 3, True)]
        status, message = _run_global(_ScriptedIndex(rows), {"pageSize": "50"})
        assert status == 200
        assert _ids(message) == ["u001", "l002"]
        assert message["includeSystem"] is False
        assert all(r["isSystem"] is False for r in message["Items"])

    def test_include_system_true_lists_every_row_with_its_flag(self):
        rows = [_row("s", 0, True), _row("u", 1, False), _row("l", 2, None)]
        status, message = _run_global(_ScriptedIndex(rows), {"pageSize": "50", "includeSystem": "TRUE"})
        assert status == 200
        assert _ids(message) == ["s000", "u001", "l002"]
        assert [r["isSystem"] for r in message["Items"]] == [True, False, False]
        assert message["includeSystem"] is True

    def test_a_non_boolean_flag_is_never_hidden(self):
        """Fail-visible: only an exact boolean True hides a row, on both filter layers."""
        rows = [_row("x", 0, "true"), _row("y", 1, 1)]
        status, message = _run_global(_ScriptedIndex(rows), {"pageSize": "50"})
        assert status == 200
        assert _ids(message) == ["x000", "y001"]

    def test_the_python_safety_net_drops_a_system_row_the_server_filter_let_through(self):
        table = MagicMock()
        table.query.return_value = _page([_row("s", 0, True), _row("u", 1, False)])
        status, message = _run_global(table, {"pageSize": "50"})
        assert status == 200
        assert _ids(message) == ["u001"]

    def test_the_server_filter_is_attribute_not_exists_or_not_true(self):
        table = _ScriptedIndex([_row("u", 0, False)])
        _run_global(table, {"pageSize": "50"})
        spec = table.calls[0]["FilterExpression"].get_expression()
        assert spec["operator"] == "OR"
        parts = {(v.get_expression()["operator"], v.get_expression()["values"][0].name)
                 for v in spec["values"]}
        assert parts == {("attribute_not_exists", "isSystem"), ("<>", "isSystem")}

    def test_existing_filters_still_and_with_the_system_term(self):
        rows = [_row("u", 0, False), dict(_row("f", 1, False), executionStatus="FAILED"),
                dict(_row("s", 2, True), executionStatus="FAILED")]
        status, message = _run_global(_ScriptedIndex(rows), {"pageSize": "50", "status": "FAILED"})
        assert status == 200
        assert _ids(message) == ["f001"]

    @pytest.mark.parametrize("value", ["yes", "1", "maybe"])
    def test_an_invalid_value_is_a_400(self, value):
        status, body = _run_global(_ScriptedIndex([]), {"includeSystem": value})
        assert status == 400
        assert "includeSystem" in json.dumps(body)

    def test_blank_reads_as_absent(self):
        status, message = _run_global(_ScriptedIndex([_row("s", 0, True)]), {"includeSystem": " "})
        assert status == 200
        assert message["Items"] == []


@pytest.mark.unit
class TestPagingThroughASystemDenseWindow:
    def test_every_user_row_is_served_exactly_once_across_pages(self):
        """Ninety system rows interleaved with ten user rows, read 7 rows per query and 3 per page:
        the walk must serve each user row once, in order, and end with no token."""
        rows = []
        for i in range(100):
            rows.append(_row("u" if i % 10 == 4 else "s", i, i % 10 != 4))
        expected = [r["workflowExecutionId"] for r in rows if not r["isSystem"]]
        table = _ScriptedIndex(rows, limit=7)
        served, token, pages = [], None, 0
        while True:
            query = {"pageSize": "3"}
            if token:
                query["startingToken"] = token
            status, message = _run_global(table, query)
            assert status == 200
            served.extend(_ids(message))
            pages += 1
            token = message.get("NextToken")
            if not token:
                break
            assert set(json.loads(base64.b64decode(token))) == set(
                le.GLOBAL_LIST_INDEXES["date"]["keyAttributes"]), "the token shape changed"
            assert pages < 50
        assert served == expected

    def test_a_page_of_only_system_rows_stays_continuable(self):
        rows = [_row("s", i, True) for i in range(6)] + [_row("u", 6, False)]
        table = _ScriptedIndex(rows, limit=3)
        status, message = _run_global(table, {"pageSize": "1"})
        assert status == 200
        assert _ids(message) == ["u006"]


@pytest.mark.unit
class TestTheSystemWorkflowScope:
    QUERY = {"pageSize": "50", "workflowId": "sys-wf", "workflowDatabaseId": "GLOBAL"}

    def _rows(self):
        return [_row("s", 0, True, workflow="sys-wf", db="GLOBAL"),
                _row("s", 1, True, workflow="sys-wf", db="GLOBAL")]

    def test_a_scope_to_one_system_workflow_shows_its_runs_by_default(self):
        status, message = _run_global(_ScriptedIndex(self._rows()), dict(self.QUERY),
                                      workflow_definition={"isSystem": True})
        assert status == 200
        assert _ids(message) == ["s000", "s001"]
        assert message["includeSystem"] is True

    def test_an_explicit_false_still_hides_them(self):
        status, message = _run_global(_ScriptedIndex(self._rows()),
                                      dict(self.QUERY, includeSystem="false"),
                                      workflow_definition={"isSystem": True})
        assert status == 200
        assert message["Items"] == []
        assert message["includeSystem"] is False

    def test_a_non_system_workflow_scope_keeps_the_default(self):
        rows = [_row("s", 0, True, workflow="wf2"), _row("u", 1, False, workflow="wf2")]
        status, message = _run_global(
            _ScriptedIndex(rows), {"pageSize": "50", "workflowId": "wf2", "workflowDatabaseId": "db"},
            workflow_definition={"isSystem": False})
        assert _ids(message) == ["u001"]
        assert message["includeSystem"] is False

    def test_a_workflow_id_without_its_database_is_not_a_single_workflow_scope(self):
        status, message = _run_global(_ScriptedIndex(self._rows()),
                                      {"pageSize": "50", "workflowId": "sys-wf"},
                                      workflow_definition={"isSystem": True})
        assert message["Items"] == []
        assert message["includeSystem"] is False

    def test_the_default_does_not_flip_for_a_workflow_the_caller_cannot_get(self):
        """No existence leak: the echoed default must not reveal that an unreadable workflow is a
        system workflow (its rows are withheld by the per-row workflow GET anyway)."""
        status, message = _run_global(_ScriptedIndex(self._rows()), dict(self.QUERY),
                                      enforcer=_deny_workflow("sys-wf"),
                                      workflow_definition={"isSystem": True})
        assert status == 200
        assert message["Items"] == []
        assert message["includeSystem"] is False

    def test_a_definition_read_failure_falls_back_to_hiding(self):
        le.claims_and_roles = {"tokens": ["u1"]}
        with patch(f"{MOD}.get_workflow_definition", side_effect=RuntimeError("ddb down")):
            assert le._resolve_include_system(None, _allow_all(), "GLOBAL", "sys-wf") is False


@pytest.mark.unit
class TestARestrictedRoleIsUnchanged:
    def test_include_system_does_not_widen_per_row_visibility(self):
        rows = [_row("s", 0, True, workflow="hidden-wf"), _row("u", 1, False, workflow="hidden-wf"),
                _row("v", 2, True)]
        status, message = _run_global(_ScriptedIndex(rows), {"pageSize": "50", "includeSystem": "true"},
                                      enforcer=_deny_workflow("hidden-wf"))
        assert status == 200
        assert _ids(message) == ["v002"]


# ---------------------------------------------------------------------------------------------
_ASSET = {"databaseId": "db", "assetId": "asset-1", "assetName": "asset-1"}


def _run_asset(main_rows, query, workflow_id="", workflow_database_id="", enforcer=None,
               workflow_definition=None):
    """Drive get_executions for one asset whose input rows name `main_rows`' executions."""
    input_rows = [{"workflowExecutionId": r["workflowExecutionId"], "databaseId": "db",
                   "assetId": "asset-1", "inputAssetFileKey": "asset-1/f.glb",
                   "executionStartDate": r["executionStartDate"],
                   "workflowId": r["workflowId"], "workflowDatabaseId": r["workflowDatabaseId"]}
                  for r in main_rows]
    by_id = {r["workflowExecutionId"]: r for r in main_rows}
    inputs_table, cfg_table, main_table = MagicMock(), MagicMock(), MagicMock()
    inputs_table.query.return_value = {"Items": input_rows}
    cfg_table.query.return_value = {"Items": []}

    def _main_query(**kwargs):
        execution_id = kwargs["KeyConditionExpression"].get_expression()["values"][1]
        return {"Items": [dict(by_id[execution_id])] if execution_id in by_id else []}

    main_table.query.side_effect = _main_query
    tables = {le.workflow_execution_inputs_table: inputs_table,
              le.workflow_execution_configuration_table: cfg_table,
              le.workflow_execution_database_v2: main_table}
    le.claims_and_roles = {"tokens": ["u1"]}
    with patch(f"{MOD}.dynamodb") as ddb, \
         patch(f"{MOD}.CasbinEnforcer", return_value=enforcer or _allow_all()), \
         patch(f"{MOD}.get_asset_details", side_effect=lambda d, a: dict(_ASSET)), \
         patch(f"{MOD}.get_execution_input_assets", side_effect=lambda eid: [("db", "asset-1")]), \
         patch(f"{MOD}.get_workflow_execution_configuration_row", return_value={}), \
         patch(f"{MOD}.get_workflow_definition",
               side_effect=lambda d, w: dict(workflow_definition or {})):
        ddb.Table.side_effect = lambda name: tables.get(name, MagicMock())
        response = le.get_executions({}, "db", "asset-1", workflow_database_id, workflow_id, query)
    body = json.loads(response["body"])
    return response["statusCode"], body.get("message", body)


@pytest.mark.unit
class TestTheAssetListHidesSystemRunsByDefault:
    def test_default_hides_system_rows_and_keeps_legacy_rows(self):
        rows = [_row("s", 0, True), _row("u", 1, False), _row("l", 2, None)]
        status, message = _run_asset(rows, {})
        assert status == 200
        assert _ids(message) == ["u001", "l002"]
        assert all(r["isSystem"] is False for r in message["Items"])
        assert message["includeSystem"] is False

    def test_include_system_true_lists_them_with_the_flag(self):
        rows = [_row("s", 0, True), _row("u", 1, False)]
        status, message = _run_asset(rows, {"includeSystem": "true"})
        assert _ids(message) == ["s000", "u001"]
        assert [r["isSystem"] for r in message["Items"]] == [True, False]

    def test_an_invalid_value_is_a_400(self):
        status, _body = _run_asset([], {"includeSystem": "nope"})
        assert status == 400

    def test_the_path_scope_to_a_system_workflow_shows_its_runs(self):
        rows = [_row("s", 0, True, workflow="sys-wf", db="GLOBAL")]
        status, message = _run_asset(rows, {}, workflow_id="sys-wf", workflow_database_id="GLOBAL",
                                     workflow_definition={"isSystem": True})
        assert _ids(message) == ["s000"]
        assert message["includeSystem"] is True

    def test_the_query_scope_to_a_system_workflow_shows_its_runs_unless_false(self):
        rows = [_row("s", 0, True, workflow="sys-wf", db="GLOBAL")]
        query = {"workflowId": "sys-wf", "workflowDatabaseId": "GLOBAL"}
        _s, shown = _run_asset(rows, dict(query), workflow_definition={"isSystem": True})
        _s, hidden = _run_asset(rows, dict(query, includeSystem="false"),
                                workflow_definition={"isSystem": True})
        assert _ids(shown) == ["s000"]
        assert hidden["Items"] == []
