# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""`isSystem` is written by the record builders (default False) and read back on every pipeline and
workflow response, list and single alike, with a row that predates the attribute reading False.

The list and single-record arms go through lambda_handler rather than the response builder alone:
the field has to reach the wire, and the two list paths measure and serialize each item themselves.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.common.workflows import pipelineRecords as pr
from backend.backend.common.workflows import workflowRecords as wr
from backend.backend.models.pipelines import PipelineRecordV2, PipelineResponseModel
from backend.backend.models.workflows import WorkflowRecordV2, WorkflowResponseModel
from backend.backend.handlers.pipelines import pipelineService as ps
from backend.backend.handlers.workflows import workflowService as ws

PS = "backend.backend.handlers.pipelines.pipelineService"
WS = "backend.backend.handlers.workflows.workflowService"


def _event(method, path, path_params=None, query=None):
    return {
        "requestContext": {"http": {"method": method, "path": path}},
        "pathParameters": path_params,
        "queryStringParameters": query,
        "headers": {"authorization": "Bearer test-token"},
        "body": None,
    }


def _enforcer():
    inst = MagicMock()
    inst.enforceAPI.return_value = True
    inst.enforce.return_value = True
    return inst


@pytest.mark.unit
class TestRecordBuilders:
    def test_pipeline_record_defaults_to_not_system(self):
        rec = pr.build_pipeline_record(
            database_id="db1", pipeline_id="p1", pipeline_name="P", category="c", description="",
            execution_config=pr.build_pipeline_execution_config(),
            system_config=pr.build_pipeline_system_config())
        assert rec["isSystem"] is False

    def test_pipeline_record_accepts_is_system(self):
        rec = pr.build_pipeline_record(
            database_id="GLOBAL", pipeline_id="p1", pipeline_name="P", category="SYSTEM - Preview",
            description="", execution_config=None, system_config=None, is_system=True)
        assert rec["isSystem"] is True

    def test_workflow_record_defaults_to_not_system(self):
        rec = wr.build_workflow_record(
            database_id="db1", workflow_id="w1", workflow_name="W", category="c", description="",
            specified_pipelines=[], system_config=wr.build_workflow_system_config())
        assert rec["isSystem"] is False

    def test_workflow_record_accepts_is_system(self):
        rec = wr.build_workflow_record(
            database_id="GLOBAL", workflow_id="w1", workflow_name="W", category="SYSTEM - Preview",
            description="", specified_pipelines=[], system_config=None, is_system=True)
        assert rec["isSystem"] is True


@pytest.mark.unit
class TestModels:
    def test_record_and_response_models_default_to_false(self):
        assert PipelineRecordV2(databaseId="d", pipelineId="p").isSystem is False
        assert PipelineResponseModel(databaseId="d", pipelineId="p").isSystem is False
        assert WorkflowRecordV2(databaseId="d", workflowId="w").isSystem is False
        assert WorkflowResponseModel(databaseId="d", workflowId="w").isSystem is False

    def test_response_models_carry_true(self):
        assert PipelineResponseModel(databaseId="d", pipelineId="p", isSystem=True).dict()["isSystem"] is True
        assert WorkflowResponseModel(databaseId="d", workflowId="w", isSystem=True).dict()["isSystem"] is True


@pytest.mark.unit
class TestResponseBuilders:
    def test_pipeline_item_to_response_reads_the_stored_flag(self):
        assert ps._item_to_response({"databaseId": "d", "pipelineId": "p"}).isSystem is False
        assert ps._item_to_response({"databaseId": "d", "pipelineId": "p", "isSystem": True}).isSystem is True

    def test_workflow_item_to_response_reads_the_stored_flag(self):
        assert ws._item_to_response({"databaseId": "d", "workflowId": "w"}).isSystem is False
        assert ws._item_to_response({"databaseId": "d", "workflowId": "w", "isSystem": True}).isSystem is True


@pytest.mark.unit
class TestWire:
    @patch(f"{PS}._template_count", return_value=0)
    @patch(f"{PS}.dynamodb")
    @patch(f"{PS}.request_to_claims")
    @patch(f"{PS}.CasbinEnforcer")
    def test_pipeline_list_items_carry_isSystem(self, mock_enforcer, mock_claims, mock_dynamodb, _count):
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        paginator = MagicMock()
        paginator.paginate.return_value.build_full_result.return_value = {"Items": [
            {"databaseId": "GLOBAL", "pipelineId": "sys1", "isSystem": True, "enabled": True},
            {"databaseId": "db1", "pipelineId": "mine", "enabled": True},
        ]}
        mock_dynamodb.meta.client.get_paginator.return_value = paginator
        resp = ps.lambda_handler(_event("GET", "/pipelines", query={
            "maxItems": "100", "pageSize": "100", "startingToken": None}), MagicMock())
        assert resp["statusCode"] == 200
        items = json.loads(resp["body"])["message"]["Items"]
        assert [(i["pipelineId"], i["isSystem"]) for i in items] == [("sys1", True), ("mine", False)]

    @patch(f"{WS}._batch_pipeline_system_configs", return_value={})
    @patch(f"{WS}.get_workflow_triggers", return_value=[])
    @patch(f"{WS}.get_workflow_item")
    @patch(f"{WS}.request_to_claims")
    @patch(f"{WS}.CasbinEnforcer")
    def test_single_workflow_carries_isSystem(self, mock_enforcer, mock_claims, mock_item, _trig, _cfg):
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        params = {"databaseId": "GLOBAL", "workflowId": "sys1"}
        mock_item.return_value = {"databaseId": "GLOBAL", "workflowId": "sys1", "isSystem": True,
                                  "systemConfig": {}, "specifiedPipelines": []}
        resp = ws.lambda_handler(_event("GET", "/database/GLOBAL/workflows/sys1", params), MagicMock())
        assert resp["statusCode"] == 200
        assert json.loads(resp["body"])["message"]["isSystem"] is True

        mock_item.return_value = {"databaseId": "GLOBAL", "workflowId": "sys1",
                                  "systemConfig": {}, "specifiedPipelines": []}
        resp = ws.lambda_handler(_event("GET", "/database/GLOBAL/workflows/sys1", params), MagicMock())
        assert json.loads(resp["body"])["message"]["isSystem"] is False
