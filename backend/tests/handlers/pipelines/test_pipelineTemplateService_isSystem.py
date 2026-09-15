# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Templates of a system pipeline (spec 4.1): none may be added or deleted; an update may change only
configBody, tagSchema and webFormJson, judged by VALUE because the web form sends the whole body; the
tag-schema route stays open; the importer is exempt. Refusals are 400s carrying the message the CLI,
MCP and live suite quote."""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.common.workflows import systemRecords as sr
from backend.backend.handlers.pipelines.pipelineTemplateService import lambda_handler

MOD = "backend.backend.handlers.pipelines.pipelineTemplateService"

IMPORT_CALL = {"userName": "SYSTEM_USER", "source": "vamsSchemaImport"}
SYSTEM_PIPELINE = {"databaseId": "db1", "pipelineId": "sys1", "pipelineName": "S", "isSystem": True,
                   "executionConfig": {"executionType": "Lambda"}}
PLAIN_PIPELINE = {"databaseId": "db1", "pipelineId": "sys1", "pipelineName": "P",
                  "executionConfig": {"executionType": "Lambda"}}
BASE = "/database/db1/pipelines/sys1/templates"
PARAMS = {"databaseId": "db1", "pipelineId": "sys1"}
TPARAMS = {"databaseId": "db1", "pipelineId": "sys1", "templateId": "sys-default"}
DEFAULT_BUCKET = {"bucketId": "b-id", "bucketName": "b", "baseAssetsPrefix": ""}

STORED_ROW = {
    "pipelineDatabaseId:pipelineId": "db1:sys1", "templateId": "sys-default",
    "pipelineDatabaseId": "db1", "pipelineId": "sys1", "templateName": "Default",
    "description": "shipped", "configFormat": "json", "allowCustomEdit": True,
    "inputInstructions": "", "bodyStorage": "inline", "configBody": '{"views": 8}',
    "webFormJson": "", "overrides": {}, "isDefault": True,
    "dateCreated": "2026-01-01T00:00:00Z", "dateModified": "2026-01-01T00:00:00Z",
}

# What the web form sends on an edit of the shipped template: every field, only the body changed.
WEB_BODY = {"templateName": "Default", "description": "shipped", "configFormat": "json",
            "configBody": '{"views": 4}', "inputInstructions": "", "allowCustomEdit": True,
            "isDefault": True, "overrides": {}}


def _event(method, path, params, body=None, cross_call=None):
    event = {
        "requestContext": {"http": {"method": method, "path": path}},
        "pathParameters": params,
        "queryStringParameters": None,
        "headers": {"authorization": "Bearer test-token"},
        "body": json.dumps(body) if body is not None else None,
    }
    if cross_call is not None:
        event["lambdaCrossCall"] = dict(cross_call)
    return event


def _enforcer():
    inst = MagicMock()
    inst.enforceAPI.return_value = True
    inst.enforce.return_value = True
    return inst


def _message(resp):
    return json.loads(resp["body"])["message"]


def _call(event, pipeline, template_row=STORED_ROW):
    templates = MagicMock()
    tag_table = MagicMock()
    tag_table.query.return_value = {"Items": []}
    triggers = MagicMock()
    triggers.query.return_value = {"Items": []}
    with patch(f"{MOD}.CasbinEnforcer", return_value=_enforcer()), \
         patch(f"{MOD}.request_to_claims", return_value={"tokens": ["user1"]}), \
         patch(f"{MOD}._enforce_parent_pipeline", return_value=(True, dict(pipeline))), \
         patch(f"{MOD}._get_template_row", return_value=dict(template_row) if template_row else None), \
         patch(f"{MOD}._rehydrate_template",
               return_value={"configBody": STORED_ROW["configBody"], "webFormJson": ""}), \
         patch(f"{MOD}._default_bucket", return_value=DEFAULT_BUCKET), \
         patch(f"{MOD}._templates_table", return_value=templates), \
         patch(f"{MOD}._tag_schema_table", return_value=tag_table), \
         patch(f"{MOD}._triggers_table", return_value=triggers):
        resp = lambda_handler(event, MagicMock())
    return resp, templates, tag_table


@pytest.mark.unit
class TestAddAndDeleteAreRefused:
    def test_post_is_refused(self):
        resp, templates, _ = _call(_event("POST", BASE, PARAMS, {"templateName": "extra"}),
                                   SYSTEM_PIPELINE, template_row=None)
        assert resp["statusCode"] == 400
        assert _message(resp) == sr.SYSTEM_TEMPLATE_LOCKED_MESSAGE
        templates.put_item.assert_not_called()

    def test_delete_is_refused(self):
        resp, templates, _ = _call(_event("DELETE", BASE + "/sys-default", TPARAMS), SYSTEM_PIPELINE)
        assert resp["statusCode"] == 400
        assert _message(resp) == sr.SYSTEM_TEMPLATE_LOCKED_MESSAGE
        templates.delete_item.assert_not_called()

    def test_a_non_system_pipeline_still_adds_and_deletes(self):
        resp, templates, _ = _call(_event("POST", BASE, PARAMS, {"templateName": "extra"}),
                                   PLAIN_PIPELINE, template_row=None)
        assert resp["statusCode"] == 200, resp["body"]
        templates.put_item.assert_called_once()
        resp, templates, _ = _call(_event("DELETE", BASE + "/sys-default", TPARAMS), PLAIN_PIPELINE)
        assert resp["statusCode"] == 200, resp["body"]
        templates.delete_item.assert_called_once()


@pytest.mark.unit
class TestUpdateIsValueBased:
    def test_the_full_web_body_with_only_the_body_changed_is_accepted(self):
        resp, templates, _ = _call(_event("PUT", BASE + "/sys-default", TPARAMS, WEB_BODY), SYSTEM_PIPELINE)
        assert resp["statusCode"] == 200, resp["body"]
        saved = templates.put_item.call_args.kwargs["Item"]
        assert saved["configBody"] == '{"views": 4}' and saved["templateName"] == "Default"

    def test_body_and_tag_schema_alone_are_accepted(self):
        # An integer tag's placeholder stands for the whole JSON value, so it is unquoted.
        body = {"configBody": '{"views": {{VIEWS}}}',
                "tagSchema": [{"tagKey": "VIEWS", "type": "integer", "default": 8}]}
        resp, templates, tag_table = _call(_event("PUT", BASE + "/sys-default", TPARAMS, body), SYSTEM_PIPELINE)
        assert resp["statusCode"] == 200, resp["body"]
        templates.put_item.assert_called_once()
        tag_table.put_item.assert_called_once()

    @pytest.mark.parametrize("field,value", [
        ("templateName", "renamed"),
        ("description", "edited"),
        ("isDefault", False),
        ("allowCustomEdit", False),
        ("overrides", {"inputFileArity": "multi"}),
        ("inputInstructions", "do this"),
    ])
    def test_a_changed_locked_field_is_refused_naming_it(self, field, value):
        body = dict(WEB_BODY, **{field: value})
        resp, templates, _ = _call(_event("PUT", BASE + "/sys-default", TPARAMS, body), SYSTEM_PIPELINE)
        assert resp["statusCode"] == 400
        assert _message(resp) == sr.system_template_locked_field_message(field)
        assert f'"{field}"' in _message(resp)
        templates.put_item.assert_not_called()

    def test_a_non_system_pipeline_renames_freely(self):
        body = dict(WEB_BODY, templateName="renamed")
        resp, templates, _ = _call(_event("PUT", BASE + "/sys-default", TPARAMS, body), PLAIN_PIPELINE)
        assert resp["statusCode"] == 200, resp["body"]
        assert templates.put_item.call_args.kwargs["Item"]["templateName"] == "renamed"

    def test_a_missing_template_is_still_a_404(self):
        resp, _, _ = _call(_event("PUT", BASE + "/nope", dict(TPARAMS, templateId="nope"), WEB_BODY),
                           SYSTEM_PIPELINE, template_row=None)
        assert resp["statusCode"] == 404


@pytest.mark.unit
class TestTagSchemaRouteStaysOpen:
    def test_put_tag_schema_on_a_system_pipeline_is_accepted(self):
        body = {"fields": [{"tagKey": "VIEWS", "type": "integer", "default": 8}]}
        resp, _, tag_table = _call(_event("PUT", BASE + "/sys-default/tagSchema", TPARAMS, body),
                                   SYSTEM_PIPELINE)
        assert resp["statusCode"] == 200, resp["body"]
        tag_table.put_item.assert_called_once()


@pytest.mark.unit
class TestTheImporterIsExempt:
    def test_the_importer_creates_a_template(self):
        body = {"templateId": "sys-default", "templateName": "Default", "configBody": '{"views": 8}'}
        resp, templates, _ = _call(_event("POST", BASE, PARAMS, body, IMPORT_CALL), SYSTEM_PIPELINE,
                                   template_row=None)
        assert resp["statusCode"] == 200, resp["body"]
        templates.put_item.assert_called_once()

    def test_the_importer_rewrites_locked_fields(self):
        body = dict(WEB_BODY, templateName="Default v2", description="reshipped")
        resp, templates, _ = _call(_event("PUT", BASE + "/sys-default", TPARAMS, body, IMPORT_CALL),
                                   SYSTEM_PIPELINE)
        assert resp["statusCode"] == 200, resp["body"]
        assert templates.put_item.call_args.kwargs["Item"]["templateName"] == "Default v2"

    def test_the_importer_deletes(self):
        resp, templates, _ = _call(_event("DELETE", BASE + "/sys-default", TPARAMS, cross_call=IMPORT_CALL),
                                   SYSTEM_PIPELINE)
        assert resp["statusCode"] == 200, resp["body"]
        templates.delete_item.assert_called_once()
