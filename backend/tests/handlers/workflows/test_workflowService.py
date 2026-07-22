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

    def test_execution_count_returns_none_on_error(self):
        # Best-effort: a count query failure must not break the listing — returns None.
        from backend.backend.handlers.workflows import workflowService as ws
        with patch.object(ws.dynamodb, "Table", side_effect=Exception("boom")):
            assert ws._execution_count("db1", "wf-a") is None
