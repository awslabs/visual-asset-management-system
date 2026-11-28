# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The pipeline and template reads record the request and the count; the tag schema set records the
fields it wrote. A denied or missing read records nothing (the writes keep their existing records)."""

import json
from unittest.mock import MagicMock

import pytest

import backend.backend.handlers.pipelines.pipelineService as pipeline_svc
import backend.backend.handlers.pipelines.pipelineTemplateService as template_svc

CLAIMS = {"tokens": ["alice"], "roles": ["admin"], "mfaEnabled": False}


def _event(method, path, path_params=None, body=None, query=None):
    return {"requestContext": {"http": {"method": method, "path": path}}, "pathParameters": path_params,
            "queryStringParameters": query, "body": json.dumps(body) if body is not None else None}


def _wire(monkeypatch, module, api=True, obj=True):
    recorder = MagicMock()
    enforcer = MagicMock()
    enforcer.return_value.enforceAPI.return_value = api
    enforcer.return_value.enforce.return_value = obj
    monkeypatch.setattr(module, "log_action", recorder)
    monkeypatch.setattr(module, "request_to_claims", lambda event: CLAIMS)
    monkeypatch.setattr(module, "CasbinEnforcer", enforcer)
    return recorder


@pytest.mark.unit
class TestPipelineReadRecords:
    def test_a_database_list_records_the_request_and_the_count(self, monkeypatch):
        recorder = _wire(monkeypatch, pipeline_svc)
        listing = pipeline_svc.GetPipelinesResponseModel(Items=[])
        monkeypatch.setattr(pipeline_svc, "get_database_pipelines", lambda *args: listing)
        event = _event("GET", "/database/db1/pipelines", {"databaseId": "db1"}, query={"maxItems": "5"})
        assert pipeline_svc.lambda_handler(event, MagicMock())["statusCode"] == 200
        recorder.assert_called_once()
        assert recorder.call_args.args == (event, "pipelineList")
        assert recorder.call_args.kwargs["database_id"] == "db1" and recorder.call_args.kwargs["item_count"] == 0

    def test_a_denied_list_records_nothing(self, monkeypatch):
        recorder = _wire(monkeypatch, pipeline_svc, api=False)
        assert pipeline_svc.lambda_handler(_event("GET", "/pipelines"), MagicMock())["statusCode"] == 403
        recorder.assert_not_called()

    def test_a_get_of_a_missing_pipeline_records_nothing(self, monkeypatch):
        recorder = _wire(monkeypatch, pipeline_svc)
        monkeypatch.setattr(pipeline_svc, "get_pipeline_item", lambda *args: None)
        event = _event("GET", "/database/db1/pipelines/pipe-01", {"databaseId": "db1", "pipelineId": "pipe-01"})
        assert pipeline_svc.lambda_handler(event, MagicMock())["statusCode"] == 404
        recorder.assert_not_called()


@pytest.mark.unit
class TestTemplateRecords:
    @pytest.fixture
    def wired(self, monkeypatch):
        recorder = _wire(monkeypatch, template_svc)
        monkeypatch.setattr(template_svc, "_validate_ids", lambda params: None, raising=False)
        pipeline = {"databaseId": "db1", "pipelineId": "pipe-01"}
        monkeypatch.setattr(template_svc, "_enforce_parent_pipeline",
                            lambda *args, **kwargs: (True, pipeline), raising=False)
        monkeypatch.setattr(template_svc, "_get_template_row", lambda *args: {"templateId": "tpl-01"})
        return recorder

    def test_a_tag_schema_set_records_the_fields_written(self, wired, monkeypatch):
        monkeypatch.setattr(template_svc, "_set_tag_schema_on_template", lambda *args: None)
        monkeypatch.setattr(template_svc, "SetTagSchemaRequestModel",
                            lambda **body: MagicMock(fields=[MagicMock(json=lambda: '{"name": "lod"}')]))
        path = "/database/db1/pipelines/pipe-01/templates/tpl-01/tagSchema"
        event = _event("PUT", path, {"databaseId": "db1", "pipelineId": "pipe-01", "templateId": "tpl-01"},
                       body={"fields": [{"name": "lod"}]})
        assert template_svc.lambda_handler(event, MagicMock())["statusCode"] == 200
        assert wired.call_args.args == (event, "pipelineTemplateTagSchemaUpdate")
        assert wired.call_args.kwargs["changes"] == {"fields": [{"name": "lod"}]}

    def test_a_template_list_records_the_request_and_the_count(self, wired, monkeypatch):
        monkeypatch.setattr(template_svc, "list_templates",
                            lambda *args: template_svc.GetTemplatesResponseModel(Items=[]))
        event = _event("GET", "/database/db1/pipelines/pipe-01/templates",
                       {"databaseId": "db1", "pipelineId": "pipe-01"}, query={"maxItems": "3"})
        assert template_svc.lambda_handler(event, MagicMock())["statusCode"] == 200
        wired.assert_called_once()
        assert wired.call_args.args == (event, "pipelineTemplateList")
        assert wired.call_args.kwargs["item_count"] == 0
        assert wired.call_args.kwargs["request_parameters"] == {"maxItems": "3"}

    def test_a_template_get_of_a_missing_template_records_nothing(self, wired, monkeypatch):
        monkeypatch.setattr(template_svc, "_get_template_row", lambda *args: None)
        event = _event("GET", "/database/db1/pipelines/pipe-01/templates/tpl-01",
                       {"databaseId": "db1", "pipelineId": "pipe-01", "templateId": "tpl-01"})
        assert template_svc.lambda_handler(event, MagicMock())["statusCode"] == 404
        wired.assert_not_called()
