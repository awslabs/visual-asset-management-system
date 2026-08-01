# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the workflow V2 CRUD handler (workflowServiceV2). REST v1 event shape;
CasbinEnforcer + request_to_claims + tables + pipeline resolution are patched. IDs are >=3 chars so
the shared ID validator passes under both the real and mock validators (test-isolation safe)."""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.handlers.workflows.workflowService import lambda_handler

MOD = "backend.backend.handlers.workflows.workflowService"


def _event(method, path, path_params=None, body=None, query=None):
    return {
        "requestContext": {"http": {"method": method, "path": path}},
        "pathParameters": path_params,
        "queryStringParameters": query,
        "headers": {"authorization": "Bearer test-token"},
        "body": json.dumps(body) if body is not None else None,
    }


def _enforcer(api=True, obj=True):
    inst = MagicMock()
    inst.enforceAPI.return_value = api
    inst.enforce.return_value = obj
    return inst


PIPELINE_REC = {"databaseId": "db1", "pipelineId": "pipe1", "pipelineName": "P",
                "enabled": True, "archived": False, "systemConfig": {}}


@pytest.mark.unit
class TestWorkflowServiceV2:
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_api_denied(self, mock_enforcer, mock_claims):
        mock_claims.return_value = {"tokens": ["t"]}
        mock_enforcer.return_value = _enforcer(api=False)
        resp = lambda_handler(_event("GET", "/workflows"), MagicMock())
        assert resp["statusCode"] == 403

    @patch(f"{MOD}._execution_count", return_value=0)
    @patch(f"{MOD}.dynamodb")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_global_list_queries_by_date_gsi(self, mock_enforcer, mock_claims, mock_dynamodb, mock_count):
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        paginator = MagicMock()
        paginator.paginate.return_value.build_full_result.return_value = {
            "Items": [{"databaseId": "db1", "workflowId": "wflow1", "enabled": True, "archived": False}]
        }
        mock_dynamodb.meta.client.get_paginator.return_value = paginator
        event = _event("GET", "/workflows", query={"maxItems": "100", "pageSize": "100", "startingToken": None})
        resp = lambda_handler(event, MagicMock())
        assert resp["statusCode"] == 200
        assert json.loads(resp["body"])["message"]["Items"][0]["workflowId"] == "wflow1"
        # Global list queries the by-date GSI newest-first, not a table scan.
        mock_dynamodb.meta.client.get_paginator.assert_called_with("query")
        paginate_kwargs = paginator.paginate.call_args.kwargs
        assert paginate_kwargs["IndexName"] == "WorkflowsByDateGSI"
        assert paginate_kwargs["ScanIndexForward"] is False

    @patch(f"{MOD}.find_workflow_id_owner")
    @patch(f"{MOD}.workflowAsl")
    @patch(f"{MOD}._get_pipeline_record")
    @patch(f"{MOD}._workflow_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_create_rejected_when_id_used_by_another_database(
            self, mock_enforcer, mock_claims, mock_table, mock_get_pipe, mock_asl, mock_owner):
        """Workflow ids are unique across every database (GLOBAL included), matching pipelines."""
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        mock_get_pipe.return_value = PIPELINE_REC
        mock_asl.ASL_SCHEMA_VERSION = 1
        table = MagicMock()
        table.get_item.return_value = {}          # free within this database
        mock_table.return_value = table
        mock_owner.return_value = "db-other"      # taken by another database
        body = {"databaseId": "db1", "workflowId": "wf1", "workflowName": "My WF",
                "specifiedPipelines": [{"pipelineId": "pipe1"}]}
        resp = lambda_handler(
            _event("POST", "/database/db1/workflows", {"databaseId": "db1"}, body), MagicMock())
        assert resp["statusCode"] == 400
        # Assert the specific rejection, not just any 400, so the test cannot pass for another reason.
        assert "already in use by another database" in resp["body"]
        table.put_item.assert_not_called()
        # The owning database is never disclosed to the caller.
        assert "db-other" not in resp["body"]

    @patch(f"{MOD}.workflowAsl")
    @patch(f"{MOD}._get_pipeline_record")
    @patch(f"{MOD}._workflow_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_create_workflow_success(self, mock_enforcer, mock_claims, mock_table,
                                     mock_get_pipe, mock_asl):
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        mock_get_pipe.return_value = PIPELINE_REC
        mock_asl.ASL_SCHEMA_VERSION = 1
        mock_asl.deploy_state_machine.return_value = ("", [])
        table = MagicMock()
        table.get_item.return_value = {}  # no existing workflow
        mock_table.return_value = table
        body = {"databaseId": "db1", "workflowName": "My WF", "category": "conv",
                "specifiedPipelines": [{"pipelineId": "pipe1"}]}
        resp = lambda_handler(
            _event("POST", "/database/db1/workflows", {"databaseId": "db1"}, body), MagicMock())
        assert resp["statusCode"] == 200
        saved = table.put_item.call_args.kwargs["Item"]
        assert saved["databaseId"] == "db1" and saved["workflowName"] == "My WF"
        assert saved["enabled"] is True and saved["archived"] is False
        assert len(saved["workflowId"]) == 32  # generated GUID
        assert saved["specifiedPipelines"][0]["pipelineDatabaseId:pipelineId"] == "db1:pipe1"
        data = json.loads(resp["body"])["message"]
        assert "warnings" in data

    @patch(f"{MOD}._get_pipeline_record")
    @patch(f"{MOD}._workflow_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_create_global_workflow_rejects_db_pipeline(self, mock_enforcer, mock_claims,
                                                        mock_table, mock_get_pipe):
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        _t = MagicMock()
        _t.get_item.return_value = {}
        mock_table.return_value = _t
        body = {"databaseId": "GLOBAL", "workflowName": "W",
                "specifiedPipelines": [{"pipelineId": "pipe1", "pipelineDatabaseId": "db1"}]}
        resp = lambda_handler(
            _event("POST", "/database/GLOBAL/workflows", {"databaseId": "GLOBAL"}, body), MagicMock())
        assert resp["statusCode"] == 400
        assert "GLOBAL" in json.loads(resp["body"])["message"]

    @patch(f"{MOD}._get_pipeline_record")
    @patch(f"{MOD}._workflow_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_create_rejects_missing_pipeline(self, mock_enforcer, mock_claims, mock_table, mock_get_pipe):
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        mock_get_pipe.return_value = None  # pipeline not found
        _t = MagicMock()
        _t.get_item.return_value = {}
        mock_table.return_value = _t
        body = {"databaseId": "db1", "workflowName": "W", "specifiedPipelines": [{"pipelineId": "pipe1"}]}
        resp = lambda_handler(
            _event("POST", "/database/db1/workflows", {"databaseId": "db1"}, body), MagicMock())
        assert resp["statusCode"] == 404

    @patch(f"{MOD}._get_pipeline_record")
    @patch(f"{MOD}._workflow_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_create_rejects_archived_pipeline(self, mock_enforcer, mock_claims, mock_table, mock_get_pipe):
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        archived = dict(PIPELINE_REC, archived=True)
        mock_get_pipe.return_value = archived
        _t = MagicMock()
        _t.get_item.return_value = {}
        mock_table.return_value = _t
        body = {"databaseId": "db1", "workflowName": "W", "specifiedPipelines": [{"pipelineId": "pipe1"}]}
        resp = lambda_handler(
            _event("POST", "/database/db1/workflows", {"databaseId": "db1"}, body), MagicMock())
        assert resp["statusCode"] == 400
        assert "archived" in json.loads(resp["body"])["message"]

    @patch(f"{MOD}.workflowAsl")
    @patch(f"{MOD}._get_pipeline_record")
    @patch(f"{MOD}._workflow_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_create_workflow_denied_does_not_probe(self, mock_enforcer, mock_claims, mock_table,
                                                   mock_get_pipe, mock_asl):
        # The workflow Tier-2 POST auth runs BEFORE both the existence probe AND referenced-pipeline
        # resolution, so a workflow-denied caller cannot use either as an existence oracle.
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer(api=True, obj=False)  # workflow POST denied
        mock_asl.deploy_state_machine.return_value = ("", [])
        table = MagicMock()
        mock_table.return_value = table
        body = {"databaseId": "db1", "workflowId": "wflow1", "workflowName": "W",
                "specifiedPipelines": [{"pipelineId": "pipe1"}]}
        resp = lambda_handler(
            _event("POST", "/database/db1/workflows", {"databaseId": "db1"}, body), MagicMock())
        assert resp["statusCode"] == 403
        table.get_item.assert_not_called()  # no workflow existence probe before auth
        mock_get_pipe.assert_not_called()   # no pipeline probe before workflow auth (no info leak)

    @patch(f"{MOD}.workflowAsl")
    @patch(f"{MOD}.ev")
    @patch(f"{MOD}._get_pipeline_record")
    @patch(f"{MOD}._workflow_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_create_blocks_on_save_errors(self, mock_enforcer, mock_claims, mock_table,
                                          mock_get_pipe, mock_ev, mock_asl):
        # A hard error from validate_workflow_save blocks the save (does not silently 200).
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        mock_get_pipe.return_value = PIPELINE_REC
        mock_ev.validate_workflow_save.return_value = (["some hard error"], [])
        mock_asl.ASL_SCHEMA_VERSION = 1
        _t = MagicMock()
        _t.get_item.return_value = {}
        mock_table.return_value = _t
        body = {"databaseId": "db1", "workflowName": "W", "specifiedPipelines": [{"pipelineId": "pipe1"}]}
        resp = lambda_handler(
            _event("POST", "/database/db1/workflows", {"databaseId": "db1"}, body), MagicMock())
        assert resp["statusCode"] == 400
        assert "saveErrors" in json.loads(resp["body"])["message"]
        _t.put_item.assert_not_called()  # not persisted when a hard error blocks

    @patch(f"{MOD}.get_workflow_triggers")
    @patch(f"{MOD}._workflow_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_get_single_workflow_with_triggers(self, mock_enforcer, mock_claims, mock_table, mock_triggers):
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        table = MagicMock()
        table.get_item.return_value = {"Item": {"databaseId": "db1", "workflowId": "wflow1",
                                                "workflowName": "W", "enabled": True, "archived": False}}
        mock_table.return_value = table
        mock_triggers.return_value = [{"triggerType": "fileUpload", "triggerConfig": {}, "enabled": True}]
        resp = lambda_handler(
            _event("GET", "/database/db1/workflows/wflow1", {"databaseId": "db1", "workflowId": "wflow1"}),
            MagicMock())
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])["message"]
        assert data["workflowId"] == "wflow1"
        assert data["triggers"][0]["triggerType"] == "fileUpload"

    @patch(f"{MOD}._workflow_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_get_archived_hidden_by_default(self, mock_enforcer, mock_claims, mock_table):
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        table = MagicMock()
        table.get_item.return_value = {"Item": {"databaseId": "db1", "workflowId": "wflow1", "archived": True}}
        mock_table.return_value = table
        resp = lambda_handler(
            _event("GET", "/database/db1/workflows/wflow1", {"databaseId": "db1", "workflowId": "wflow1"}),
            MagicMock())
        assert resp["statusCode"] == 404

    @patch(f"{MOD}._resolve_snapshot_pipeline_records")
    @patch(f"{MOD}._workflow_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_update_enable_disable(self, mock_enforcer, mock_claims, mock_table, mock_snapshot):
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        table = MagicMock()
        table.get_item.return_value = {"Item": {"databaseId": "db1", "workflowId": "wflow1",
                                                "enabled": True, "systemConfig": {}}}
        mock_table.return_value = table
        mock_snapshot.return_value = []
        resp = lambda_handler(
            _event("PUT", "/database/db1/workflows/wflow1", {"databaseId": "db1", "workflowId": "wflow1"},
                   {"enabled": False}), MagicMock())
        assert resp["statusCode"] == 200
        assert table.put_item.call_args.kwargs["Item"]["enabled"] is False

    @patch(f"{MOD}._resolve_snapshot_pipeline_records")
    @patch(f"{MOD}._workflow_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_update_reenforces_on_the_mutated_object(self, mock_enforcer, mock_claims, mock_table,
                                                     mock_snapshot):
        # category/workflowName are Casbin constraint fields, so the PUT is enforced on the mutated
        # object too: a role allowed on the stored category but not the requested one is denied and
        # nothing is written.
        mock_claims.return_value = {"tokens": ["user1"]}
        inst = MagicMock()
        inst.enforceAPI.return_value = True
        # Allow the stored object (category "sandbox"), deny the mutated one ("production").
        inst.enforce.side_effect = lambda obj, action: obj.get("category") != "production"
        mock_enforcer.return_value = inst
        table = MagicMock()
        table.get_item.return_value = {"Item": {"databaseId": "db1", "workflowId": "wflow1",
                                                "category": "sandbox", "systemConfig": {}}}
        mock_table.return_value = table
        mock_snapshot.return_value = []
        resp = lambda_handler(
            _event("PUT", "/database/db1/workflows/wflow1", {"databaseId": "db1", "workflowId": "wflow1"},
                   {"category": "production"}), MagicMock())
        assert resp["statusCode"] == 403
        table.put_item.assert_not_called()

    @patch(f"{MOD}.ev")
    @patch(f"{MOD}._resolve_snapshot_pipeline_records")
    @patch(f"{MOD}._workflow_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_update_without_pipelines_downgrades_save_errors(self, mock_enforcer, mock_claims,
                                                             mock_table, mock_snapshot, mock_ev):
        # A metadata-only edit (here: disable) is not blocked by a save error against the stored
        # pipeline snapshot (e.g. a referenced pipeline archived later) — the condition is surfaced as
        # a warning so the workflow stays editable.
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        mock_ev.validate_workflow_save.return_value = (["pipeline 'pipe1' is archived"], [])
        table = MagicMock()
        table.get_item.return_value = {"Item": {"databaseId": "db1", "workflowId": "wflow1",
                                                "enabled": True, "systemConfig": {}}}
        mock_table.return_value = table
        mock_snapshot.return_value = [dict(PIPELINE_REC, archived=True)]
        resp = lambda_handler(
            _event("PUT", "/database/db1/workflows/wflow1", {"databaseId": "db1", "workflowId": "wflow1"},
                   {"enabled": False}), MagicMock())
        assert resp["statusCode"] == 200
        assert table.put_item.call_args.kwargs["Item"]["enabled"] is False
        assert "pipeline 'pipe1' is archived" in json.loads(resp["body"])["message"]["warnings"]

    @patch(f"{MOD}.workflowAsl")
    @patch(f"{MOD}.ev")
    @patch(f"{MOD}._get_pipeline_record")
    @patch(f"{MOD}._workflow_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_update_with_pipelines_still_blocks_on_save_errors(self, mock_enforcer, mock_claims,
                                                               mock_table, mock_get_pipe, mock_ev,
                                                               mock_asl):
        # When the caller supplies specifiedPipelines, a save error remains a hard 400.
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        mock_get_pipe.return_value = PIPELINE_REC
        mock_ev.validate_workflow_save.return_value = (["some hard error"], [])
        table = MagicMock()
        table.get_item.return_value = {"Item": {"databaseId": "db1", "workflowId": "wflow1",
                                                "enabled": True, "systemConfig": {}}}
        mock_table.return_value = table
        resp = lambda_handler(
            _event("PUT", "/database/db1/workflows/wflow1", {"databaseId": "db1", "workflowId": "wflow1"},
                   {"specifiedPipelines": [{"pipelineId": "pipe1"}]}), MagicMock())
        assert resp["statusCode"] == 400
        assert "saveErrors" in json.loads(resp["body"])["message"]
        table.put_item.assert_not_called()

    @patch(f"{MOD}._workflow_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_archive_workflow(self, mock_enforcer, mock_claims, mock_table):
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        table = MagicMock()
        table.get_item.return_value = {"Item": {"databaseId": "db1", "workflowId": "wflow1", "enabled": True}}
        mock_table.return_value = table
        resp = lambda_handler(
            _event("DELETE", "/database/db1/workflows/wflow1", {"databaseId": "db1", "workflowId": "wflow1"}),
            MagicMock())
        assert resp["statusCode"] == 200
        saved = table.put_item.call_args.kwargs["Item"]
        assert saved["archived"] is True and saved["enabled"] is False

    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_create_empty_pipelines_rejected(self, mock_enforcer, mock_claims):
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        body = {"databaseId": "db1", "workflowName": "W", "specifiedPipelines": []}
        resp = lambda_handler(
            _event("POST", "/database/db1/workflows", {"databaseId": "db1"}, body), MagicMock())
        assert resp["statusCode"] == 400


@pytest.mark.unit
class TestExecutionCountEnrichment:
    """The workflow list enriches each authorized item with an executionCount (bounded COUNT query
    on the by-workflow GSI). _execution_count is patched so no real DynamoDB is needed."""

    def test_filtered_page_sets_execution_count(self):
        from backend.backend.handlers.workflows import workflowService as ws
        page = {"Items": [
            {"databaseId": "db1", "workflowId": "wf-a", "workflowName": "A"},
            {"databaseId": "db1", "workflowId": "wf-b", "workflowName": "B", "archived": True},
        ]}
        with patch.object(ws, "_enforce_workflow", return_value=True), \
             patch.object(ws, "_execution_count", side_effect=lambda db, wid: {"wf-a": 7}.get(wid, 0)):
            result = ws._filtered_page(page, include_archived=False, claims_and_roles={"tokens": ["u"]})
        # Archived hidden by default; the visible workflow carries its execution count.
        assert len(result.Items) == 1
        assert result.Items[0].workflowId == "wf-a"
        assert result.Items[0].executionCount == 7

    def test_pagination_config_clamps_caller_supplied_sizes(self):
        # A caller-supplied maxItems cannot make one request accumulate the whole table (each
        # accumulated row costs a COUNT query); both sizes clamp to MAX_LIST_PAGE_ITEMS.
        from backend.backend.handlers.workflows import workflowService as ws
        cfg = ws._pagination_config({"maxItems": "50000", "pageSize": "50000",
                                     "startingToken": None})
        assert cfg["MaxItems"] == ws.MAX_LIST_PAGE_ITEMS
        assert cfg["PageSize"] == ws.MAX_LIST_PAGE_ITEMS
        # A request under the ceiling is passed through untouched.
        cfg = ws._pagination_config({"maxItems": "25", "pageSize": "10", "startingToken": "tok"})
        assert cfg == {"MaxItems": 25, "PageSize": 10, "StartingToken": "tok"}

    def test_execution_count_returns_none_on_error(self):
        # Best-effort: a count query failure must not break the listing — returns None.
        from backend.backend.handlers.workflows import workflowService as ws
        with patch.object(ws.dynamodb, "Table", side_effect=Exception("boom")):
            assert ws._execution_count("db1", "wf-a") is None

    def test_execution_count_bounds_to_recent_window(self):
        # The COUNT query is bounded to the recent window: its KeyConditionExpression combines the
        # workflow composite PK with an executionStartDate >= cutoff range (so the count reflects only
        # recent executions and the query stays bounded).
        from backend.backend.handlers.workflows import workflowService as ws
        table = MagicMock()
        table.query.return_value = {"Count": 3}
        with patch.object(ws.dynamodb, "Table", return_value=table):
            assert ws._execution_count("db1", "wf-a") == 3
        kwargs = table.query.call_args.kwargs
        assert kwargs["IndexName"] == ws.WORKFLOW_EXECUTIONS_BY_WORKFLOW_GSI
        assert kwargs["Select"] == "COUNT"
        # The key condition is an AND of the PK equality and the executionStartDate lower bound.
        expr = kwargs["KeyConditionExpression"]
        rendered = expr.get_expression() if hasattr(expr, "get_expression") else {}
        assert rendered.get("operator") == "AND"
