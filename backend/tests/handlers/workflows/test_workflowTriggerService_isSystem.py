# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Triggers of a system workflow (spec 4.1): `enabled` may be switched, the stored inputFileFilters and
defaultTemplateIds may not change, no trigger may be added or deleted, and the importer is exempt.

The rule reads the RAW body — the request model turns an absent field into {} — and compares after the
same normalisation the store applies, so the two clients that exist both pass: the web editor re-sends
the stored trigger with `enabled` flipped, and the CLI sends {"enabled": false} alone. The stored
triggerConfig is what gets written back, so a locked save can never blank the bundle's filters."""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.common.workflows import systemRecords as sr
from backend.backend.handlers.workflows.workflowTriggerService import lambda_handler

MOD = "backend.backend.handlers.workflows.workflowTriggerService"

IMPORT_CALL = {"userName": "SYSTEM_USER", "source": "vamsSchemaImport"}
BASE = "/database/GLOBAL/workflows/sysw/triggers"
TPARAMS = {"databaseId": "GLOBAL", "workflowId": "sysw", "triggerType": "fileUpload"}
EXTRA_PARAMS = {"databaseId": "GLOBAL", "workflowId": "sysw", "triggerType": "fileUpload#extra"}

SPECIFIED = {"pipelineDatabaseId": "GLOBAL", "pipelineId": "sysp",
             "pipelineDatabaseId:pipelineId": "GLOBAL:sysp", "jobName": "", "defaultTemplateId": ""}
SYSTEM_WF = {"databaseId": "GLOBAL", "workflowId": "sysw", "workflowName": "S", "isSystem": True,
             "systemConfig": {"concurrencyRestriction": "none"}, "specifiedPipelines": [SPECIFIED]}
PLAIN_WF = {"databaseId": "GLOBAL", "workflowId": "sysw", "workflowName": "W",
            "systemConfig": {"concurrencyRestriction": "none"}, "specifiedPipelines": [SPECIFIED]}
PIPELINE_ITEM = {"databaseId": "GLOBAL", "pipelineId": "sysp", "pipelineName": "P",
                 "systemConfig": {}, "executionConfig": {"executionType": "Lambda"}}

STORED_CONFIG = {"inputFileFilters": {"allow": ["*.glb", "*.stl", "*.obj"], "exclude": []},
                 "defaultTemplateIds": {"GLOBAL:sysp": "sys-default"}}
STORED_ROW = {"workflowDatabaseId:workflowId": "GLOBAL:sysw", "triggerType": "fileUpload",
              "triggerBaseType": "fileUpload", "triggerId": "", "workflowDatabaseId": "GLOBAL",
              "workflowId": "sysw", "triggerConfig": STORED_CONFIG, "enabled": True,
              "dateCreated": "2026-01-01T00:00:00Z", "dateModified": "2026-01-01T00:00:00Z"}


def _event(method, params, body=None, cross_call=None):
    event = {
        "requestContext": {"http": {"method": method, "path": f"{BASE}/{params['triggerType']}"}},
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


def _call(event, workflow, stored=STORED_ROW):
    table = MagicMock()
    pipelines = MagicMock()
    pipelines.get_item.return_value = {"Item": dict(PIPELINE_ITEM)}
    templates = MagicMock()
    templates.query.return_value = {"Items": []}
    with patch(f"{MOD}.CasbinEnforcer", return_value=_enforcer()), \
         patch(f"{MOD}.request_to_claims", return_value={"tokens": ["user1"]}), \
         patch(f"{MOD}._enforce_parent_workflow", return_value=(True, dict(workflow))), \
         patch(f"{MOD}.get_trigger", return_value=dict(stored) if stored else None), \
         patch(f"{MOD}._same_type_triggers", return_value=[]), \
         patch(f"{MOD}._triggers_table", return_value=table), \
         patch(f"{MOD}._pipelines_table", return_value=pipelines), \
         patch(f"{MOD}._templates_table", return_value=templates), \
         patch(f"{MOD}._load_template_tag_schema_fields", return_value=None):
        resp = lambda_handler(event, MagicMock())
    return resp, table


@pytest.mark.unit
class TestEnabledIsFree:
    def test_the_cli_shape_switches_the_trigger_off_and_keeps_the_stored_config(self):
        resp, table = _call(_event("PUT", TPARAMS, {"enabled": False}), SYSTEM_WF)
        assert resp["statusCode"] == 200, resp["body"]
        saved = table.put_item.call_args.kwargs["Item"]
        assert saved["enabled"] is False
        assert saved["triggerConfig"] == STORED_CONFIG
        assert saved["dateCreated"] == "2026-01-01T00:00:00Z"
        assert _message(resp)["triggerConfig"] == STORED_CONFIG

    def test_the_web_shape_resends_the_stored_trigger_with_enabled_flipped(self):
        body = {"triggerType": "fileUpload", "enabled": False,
                "inputFileFilters": STORED_CONFIG["inputFileFilters"],
                "defaultTemplateIds": STORED_CONFIG["defaultTemplateIds"]}
        resp, table = _call(_event("PUT", TPARAMS, body), SYSTEM_WF)
        assert resp["statusCode"] == 200, resp["body"]
        saved = table.put_item.call_args.kwargs["Item"]
        assert saved["enabled"] is False and saved["triggerConfig"] == STORED_CONFIG

    def test_a_reordered_allow_list_without_exclude_is_still_the_stored_trigger(self):
        body = {"enabled": True, "inputFileFilters": {"allow": ["*.obj", "*.glb", "*.stl"]}}
        resp, table = _call(_event("PUT", TPARAMS, body), SYSTEM_WF)
        assert resp["statusCode"] == 200, resp["body"]
        assert table.put_item.call_args.kwargs["Item"]["triggerConfig"] == STORED_CONFIG


@pytest.mark.unit
class TestLockedFieldsAreRefused:
    def test_changed_filters_are_refused_naming_the_field(self):
        body = {"enabled": True, "inputFileFilters": {"allow": ["*.png"], "exclude": []}}
        resp, table = _call(_event("PUT", TPARAMS, body), SYSTEM_WF)
        assert resp["statusCode"] == 400
        assert _message(resp) == sr.system_trigger_locked_field_message("inputFileFilters")
        assert "inputFileFilters" in _message(resp)
        table.put_item.assert_not_called()

    def test_changed_default_templates_are_refused_naming_the_field(self):
        body = {"inputFileFilters": STORED_CONFIG["inputFileFilters"],
                "defaultTemplateIds": {"GLOBAL:sysp": "other"}}
        resp, table = _call(_event("PUT", TPARAMS, body), SYSTEM_WF)
        assert resp["statusCode"] == 400
        assert _message(resp) == sr.system_trigger_locked_field_message("defaultTemplateIds")
        table.put_item.assert_not_called()

    def test_a_key_with_no_stored_trigger_is_refused(self):
        body = {"inputFileFilters": {"allow": ["*.png"], "exclude": []}, "enabled": True}
        resp, table = _call(_event("PUT", EXTRA_PARAMS, body), SYSTEM_WF, stored=None)
        assert resp["statusCode"] == 400
        assert _message(resp) == sr.SYSTEM_TRIGGER_LOCKED_MESSAGE
        table.put_item.assert_not_called()

    def test_delete_is_refused(self):
        resp, table = _call(_event("DELETE", TPARAMS), SYSTEM_WF)
        assert resp["statusCode"] == 400
        assert _message(resp) == sr.SYSTEM_TRIGGER_LOCKED_MESSAGE
        table.delete_item.assert_not_called()

    def test_a_non_system_workflow_is_untouched_by_the_rule(self):
        body = {"inputFileFilters": {"allow": ["*.png"], "exclude": []}, "enabled": True}
        resp, table = _call(_event("PUT", TPARAMS, body), PLAIN_WF)
        assert resp["statusCode"] == 200, resp["body"]
        assert table.put_item.call_args.kwargs["Item"]["triggerConfig"]["inputFileFilters"]["allow"] == ["*.png"]
        resp, table = _call(_event("DELETE", TPARAMS), PLAIN_WF)
        assert resp["statusCode"] == 200, resp["body"]
        table.delete_item.assert_called_once()


@pytest.mark.unit
class TestTheImporterIsExempt:
    def test_the_importer_rewrites_the_filters_and_the_flag(self):
        body = {"inputFileFilters": {"allow": ["*.glb", "*.usd"], "exclude": []},
                "defaultTemplateIds": {"GLOBAL:sysp": "sys-default"}, "enabled": True}
        resp, table = _call(_event("PUT", TPARAMS, body, IMPORT_CALL), SYSTEM_WF)
        assert resp["statusCode"] == 200, resp["body"]
        saved = table.put_item.call_args.kwargs["Item"]
        assert saved["triggerConfig"]["inputFileFilters"]["allow"] == ["*.glb", "*.usd"]
        assert saved["enabled"] is True

    def test_the_importer_adds_a_trigger_under_a_new_key(self):
        body = {"inputFileFilters": {"allow": ["*.png"], "exclude": []}, "enabled": False}
        resp, table = _call(_event("PUT", EXTRA_PARAMS, body, IMPORT_CALL), SYSTEM_WF, stored=None)
        assert resp["statusCode"] == 200, resp["body"]
        assert table.put_item.call_args.kwargs["Item"]["triggerType"] == "fileUpload#extra"

    def test_the_importer_deletes(self):
        resp, table = _call(_event("DELETE", TPARAMS, cross_call=IMPORT_CALL), SYSTEM_WF)
        assert resp["statusCode"] == 200, resp["body"]
        table.delete_item.assert_called_once()
