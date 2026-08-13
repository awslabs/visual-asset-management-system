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


@pytest.mark.unit
class TestListPageByteBudget:
    """The row cap alone does not bound the list response: a workflow row carries specifiedPipelines,
    systemConfig and the computed aggregate filters, so a page at the row cap can pass the 6 MB Lambda
    synchronous-response limit — which returns a 502 with no body and no NextToken, so the caller
    cannot page past it. The page stops at a byte budget and resumes from the last row it kept."""

    def _fat_item(self, workflow_id, filter_count=400):
        # A strict extension whitelist is normal for a conversion workflow, and it is echoed back in
        # both systemConfig and the computed aggregate.
        allow = [f"*.ext{n:04d}" for n in range(filter_count)]
        return {
            "databaseId": "db1", "workflowId": workflow_id, "workflowName": workflow_id,
            "allListPartition": "workflow", "dateModified": f"2026-01-01T00:00:{int(workflow_id[-1]):02d}Z",
            "systemConfig": {"inputFileFilters": {"allow": allow, "exclude": []}},
            "specifiedPipelines": [{"pipelineId": "pipe1"}],
        }

    def _run(self, items, max_bytes, on_by_date_gsi=False):
        from backend.backend.handlers.workflows import workflowService as ws
        with patch.object(ws, "_enforce_workflow", return_value=True), \
             patch.object(ws, "_execution_count", return_value=1), \
             patch.object(ws, "_trigger_summary", return_value={"triggerCount": 0,
                                                               "triggersEnabledCount": 0}), \
             patch.object(ws, "_batch_pipeline_system_configs", return_value={}), \
             patch.object(ws, "MAX_LIST_PAGE_BYTES", max_bytes):
            return ws._filtered_page({"Items": items}, include_archived=False,
                                     claims_and_roles={"tokens": ["u"]},
                                     on_by_date_gsi=on_by_date_gsi)

    def _page_bytes(self, result):
        return len(json.dumps(result.dict(), default=str).encode("utf-8"))

    def test_page_stops_at_the_byte_budget(self):
        items = [self._fat_item(f"wf-{n}") for n in range(8)]
        # A budget of roughly three items' worth: the page must return fewer than all eight.
        one_item = self._page_bytes(self._run(items[:1], max_bytes=10 * 1024 * 1024))
        result = self._run(items, max_bytes=one_item * 3)
        assert 0 < len(result.Items) < len(items)
        assert self._page_bytes(result) <= one_item * 4

    def test_a_trimmed_page_reports_a_resume_token_for_the_deferred_rows(self):
        from botocore.paginate import TokenDecoder
        items = [self._fat_item(f"wf-{n}") for n in range(8)]
        one_item = self._page_bytes(self._run(items[:1], max_bytes=10 * 1024 * 1024))
        result = self._run(items, max_bytes=one_item * 3, on_by_date_gsi=True)
        assert result.NextToken
        # The token is the paginator's own encoding, so the caller passes it straight back, and it
        # names the LAST ROW KEPT (a GSI continuation carries the index keys plus the table keys).
        decoded = TokenDecoder().decode(result.NextToken)["ExclusiveStartKey"]
        last_kept = result.Items[-1]
        assert decoded["workflowId"] == last_kept.workflowId
        assert decoded["databaseId"] == last_kept.databaseId
        assert set(decoded) == {"databaseId", "workflowId", "allListPartition", "dateModified"}

    def test_a_database_scoped_page_resumes_on_the_table_keys_only(self):
        from botocore.paginate import TokenDecoder
        items = [self._fat_item(f"wf-{n}") for n in range(8)]
        one_item = self._page_bytes(self._run(items[:1], max_bytes=10 * 1024 * 1024))
        result = self._run(items, max_bytes=one_item * 3)
        decoded = TokenDecoder().decode(result.NextToken)["ExclusiveStartKey"]
        assert set(decoded) == {"databaseId", "workflowId"}

    def test_an_untrimmed_page_keeps_the_paginators_own_token(self):
        from backend.backend.handlers.workflows import workflowService as ws
        page = {"Items": [{"databaseId": "db1", "workflowId": "wf-a", "workflowName": "A"}],
                "NextToken": "paginator-token"}
        with patch.object(ws, "_enforce_workflow", return_value=True), \
             patch.object(ws, "_execution_count", return_value=0), \
             patch.object(ws, "_trigger_summary", return_value=None), \
             patch.object(ws, "_batch_pipeline_system_configs", return_value={}):
            result = ws._filtered_page(page, include_archived=False,
                                       claims_and_roles={"tokens": ["u"]})
        assert result.NextToken == "paginator-token"
        assert len(result.Items) == 1

    def test_one_oversized_row_is_still_returned(self):
        # A page that came back empty would read as "no workflows" and the caller could not page past
        # it either, so the first row is always kept whatever it measures.
        result = self._run([self._fat_item("wf-0"), self._fat_item("wf-1")], max_bytes=1)
        assert len(result.Items) == 1


@pytest.mark.unit
class TestTriggerSummaryEnrichment:
    """The workflow list reports how many triggers each workflow has and how many are ENABLED.

    Both numbers are needed: triggerCount alone cannot distinguish "no triggers" from "triggers that
    exist but are all switched off", which is exactly the state an operator is looking for when a
    workflow silently stops firing.
    """

    def _page(self):
        return {"Items": [
            {"databaseId": "db1", "workflowId": "wf-a", "workflowName": "A"},
            {"databaseId": "db1", "workflowId": "wf-b", "workflowName": "B"},
            {"databaseId": "db1", "workflowId": "wf-c", "workflowName": "C"},
        ]}

    # wf-a: one enabled; wf-b: two rows, one enabled; wf-c: none.
    SUMMARIES = {
        "wf-a": {"triggerCount": 1, "triggersEnabledCount": 1},
        "wf-b": {"triggerCount": 2, "triggersEnabledCount": 1},
        "wf-c": {"triggerCount": 0, "triggersEnabledCount": 0},
    }

    def _run(self, has_triggers="", summaries=None):
        from backend.backend.handlers.workflows import workflowService as ws
        table = summaries if summaries is not None else self.SUMMARIES
        with patch.object(ws, "_enforce_workflow", return_value=True), \
             patch.object(ws, "_execution_count", return_value=0), \
             patch.object(ws, "_trigger_summary", side_effect=lambda db, wid: table.get(wid)):
            return ws._filtered_page(self._page(), include_archived=False,
                                     claims_and_roles={"tokens": ["u"]},
                                     has_triggers=has_triggers)

    def test_counts_are_reported_per_workflow(self):
        items = {i.workflowId: i for i in self._run().Items}
        assert (items["wf-a"].triggerCount, items["wf-a"].triggersEnabledCount) == (1, 1)
        # The distinguishing case: triggers exist, but only one of them fires.
        assert (items["wf-b"].triggerCount, items["wf-b"].triggersEnabledCount) == (2, 1)
        assert (items["wf-c"].triggerCount, items["wf-c"].triggersEnabledCount) == (0, 0)

    def test_filter_true_keeps_only_workflows_with_an_enabled_trigger(self):
        assert sorted(i.workflowId for i in self._run("true").Items) == ["wf-a", "wf-b"]

    def test_filter_false_keeps_only_workflows_with_no_enabled_trigger(self):
        assert [i.workflowId for i in self._run("false").Items] == ["wf-c"]

    def test_a_workflow_whose_triggers_are_all_disabled_is_not_counted_as_triggered(self):
        # The load-bearing case for filtering on the ENABLED count rather than the raw count.
        summaries = dict(self.SUMMARIES,
                         **{"wf-b": {"triggerCount": 3, "triggersEnabledCount": 0}})
        assert sorted(i.workflowId for i in self._run("true", summaries).Items) == ["wf-a"]
        assert sorted(i.workflowId for i in self._run("false", summaries).Items) == ["wf-b", "wf-c"]

    def test_no_filter_returns_every_authorized_workflow(self):
        assert len(self._run("").Items) == 3

    def test_an_unreadable_summary_keeps_the_workflow_rather_than_dropping_it(self):
        # The counts are best-effort. Dropping a workflow because its trigger read failed would
        # silently shorten the list; the workflow is kept with null counts instead.
        summaries = dict(self.SUMMARIES, **{"wf-a": None})
        kept = self._run("true", summaries)
        assert "wf-a" in [i.workflowId for i in kept.Items]
        wf_a = next(i for i in kept.Items if i.workflowId == "wf-a")
        assert wf_a.triggerCount is None and wf_a.triggersEnabledCount is None


@pytest.mark.unit
class TestTriggerSummaryQuery:
    """_trigger_summary reads the triggers table for one workflow."""

    def _summary(self, rows):
        from backend.backend.handlers.workflows import workflowService as ws
        table = MagicMock()
        table.query.return_value = {"Items": rows}
        with patch.object(ws, "_triggers_table", return_value=table):
            return ws._trigger_summary("db1", "wf1")

    def test_counts_enabled_and_total(self):
        assert self._summary([{"enabled": True}, {"enabled": False}, {"enabled": True}]) == {
            "triggerCount": 3, "triggersEnabledCount": 2}

    def test_a_row_without_the_flag_counts_as_enabled(self):
        # Matches get_workflow_triggers and the dispatch default, so an older row is not reported as
        # disabled (which would read as "this trigger will not fire" when it will).
        assert self._summary([{}])["triggersEnabledCount"] == 1

    def test_no_triggers(self):
        assert self._summary([]) == {"triggerCount": 0, "triggersEnabledCount": 0}

    def test_a_query_failure_is_best_effort(self):
        from backend.backend.handlers.workflows import workflowService as ws
        table = MagicMock()
        table.query.side_effect = Exception("throttled")
        with patch.object(ws, "_triggers_table", return_value=table):
            assert ws._trigger_summary("db1", "wf1") is None


@pytest.mark.unit
class TestHasTriggersQueryParam:
    """The hasTriggers list filter is normalized (or rejected) at the handler boundary.

    An unrecognized value must not fall through as "no filter": the caller would receive a full,
    unfiltered list while believing it was filtered.
    """

    def _event(self, value):
        return {
            "requestContext": {"http": {"method": "GET", "path": "/workflows"}, "authorizer": {}},
            "pathParameters": None,
            "queryStringParameters": {"hasTriggers": value} if value is not None else None,
            "headers": {"authorization": "Bearer t"},
        }

    def _call(self, value):
        from backend.backend.handlers.workflows import workflowService as ws
        captured = {}

        def _fake_all(query_params, include_archived, claims_and_roles):
            captured["hasTriggers"] = query_params.get("hasTriggers", "")
            from backend.backend.models.workflows import GetWorkflowsResponseModel
            return GetWorkflowsResponseModel(Items=[])

        with patch.object(ws, "request_to_claims", return_value={"tokens": ["u1"]}), \
             patch.object(ws, "CasbinEnforcer") as m_enf, \
             patch.object(ws, "get_all_workflows", side_effect=_fake_all):
            m_enf.return_value.enforceAPI.return_value = True
            resp = ws.lambda_handler(self._event(value), MagicMock())
        return resp, captured

    @pytest.mark.parametrize("raw,expected", [
        ("true", "true"), ("TRUE", "true"), ("1", "true"), ("yes", "true"),
        ("false", "false"), ("False", "false"), ("0", "false"), ("no", "false"),
    ])
    def test_accepted_values_normalize(self, raw, expected):
        resp, captured = self._call(raw)
        assert resp["statusCode"] == 200
        assert captured["hasTriggers"] == expected

    def test_absent_means_no_filter(self):
        resp, captured = self._call(None)
        assert resp["statusCode"] == 200
        assert captured["hasTriggers"] == ""

    @pytest.mark.parametrize("bad", ["maybe", "enabled", "2"])
    def test_an_unrecognized_value_is_rejected_not_ignored(self, bad):
        resp, _ = self._call(bad)
        assert resp["statusCode"] == 400
