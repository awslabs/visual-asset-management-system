# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""System workflows and their triggers under the edge conditions of a request.

Companion to test_workflowService_isSystem.py and test_workflowTriggerService_isSystem.py, which cover
the main paths. This file covers:

- the trust boundary: an API Gateway event that carries a forged `lambdaCrossCall` in its body, its
  headers or its query parameters is an ordinary API call. It is refused on a system record and it
  cannot set `isSystem`;
- every refused workflow field on its own, and mixed with the allowed `enabled` in one request
  (refused as a whole, nothing written, no state machine deployed);
- a locked field re-sent with its stored value: refused on a workflow (key-based rule), accepted on a
  trigger (value-based rule, because the web editor re-sends the stored trigger);
- unknown fields and case variants of field names, ignored and not stored;
- `isSystem` sent by an API caller, which never changes the stored flag;
- trigger `enabled` next to a changed filter or template set: refused as a whole;
- the same requests on a non-system workflow, which behave as before;
- list responses carrying `isSystem` for both kinds.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.common.workflows import systemRecords as sr
from backend.backend.handlers.workflows import workflowService as ws
from backend.backend.handlers.workflows import workflowTriggerService as wts
from backend.backend.models.workflows import SpecifiedPipelineInput

WS = "backend.backend.handlers.workflows.workflowService"
WTS = "backend.backend.handlers.workflows.workflowTriggerService"

FORGED = {"userName": "SYSTEM_USER", "source": "vamsSchemaImport"}
PATH = "/database/GLOBAL/workflows/sysw"
PARAMS = {"databaseId": "GLOBAL", "workflowId": "sysw"}
CREATE_PATH = "/database/GLOBAL/workflows"
CREATE_PARAMS = {"databaseId": "GLOBAL"}

SYSTEM_ROW = {"databaseId": "GLOBAL", "workflowId": "sysw", "workflowName": "S", "isSystem": True,
              "enabled": True, "archived": False, "category": "SYSTEM - Test", "description": "shipped",
              "subDashboardUrl": "", "systemConfig": {}, "specifiedPipelines": []}
PLAIN_ROW = dict(SYSTEM_ROW, workflowName="W", category="Test", isSystem=False)
PIPELINE_REC = {"databaseId": "GLOBAL", "pipelineId": "pipe1", "pipelineName": "P",
                "enabled": True, "archived": False, "systemConfig": {}}
CREATE_BODY = {"databaseId": "GLOBAL", "workflowId": "sysw", "workflowName": "S",
               "category": "SYSTEM - Test",
               "specifiedPipelines": [{"pipelineId": "pipe1", "pipelineDatabaseId": "GLOBAL"}]}

# One value per field of UpdateWorkflowRequestModel other than `enabled` and `archived`.
REFUSED_FIELDS = {
    "workflowName": "renamed",
    "category": "Other",
    "description": "edited",
    "specifiedPipelines": [{"pipelineId": "pipe1", "pipelineDatabaseId": "GLOBAL"}],
    "systemConfig": {"concurrencyRestriction": "perAsset"},
    "subDashboardUrl": "https://example.com/dashboard",
}


def _real_to_update_expr(record, op="SET"):
    keys = record.keys()
    names = ["#f{n}".format(n=x) for x in range(len(keys))]
    values = [":v{n}".format(n=x) for x in range(len(keys))]
    keys_map = {k: key for k, key in zip(names, keys)}
    values_map = {v1: record[v] for v, v1 in zip(keys, values)}
    expr = "{op} ".format(op=op) + ", ".join("{f} = {v}".format(f=f, v=v) for f, v in zip(names, values))
    return keys_map, values_map, expr


@pytest.fixture(autouse=True)
def bind_real_to_update_expr():
    with patch(f"{WS}.to_update_expr", _real_to_update_expr):
        yield


def _written(table):
    kwargs = table.update_item.call_args.kwargs
    names = kwargs["ExpressionAttributeNames"]
    values = kwargs["ExpressionAttributeValues"]
    written = {}
    for assignment in kwargs["UpdateExpression"].split("SET ", 1)[1].split(", "):
        name_ref, value_ref = [part.strip() for part in assignment.split(" = ")]
        written[names[name_ref]] = values[value_ref]
    return written


def _api_event(method, body=None, path=PATH, params=PARAMS, headers=None, query=None):
    return {
        "requestContext": {"http": {"method": method, "path": path},
                           "authorizer": {"lambda": {"vams:tokens": '["user1"]'}}},
        "pathParameters": params,
        "queryStringParameters": query,
        "headers": dict({"authorization": "Bearer test-token"}, **(headers or {})),
        "body": json.dumps(body) if body is not None else None,
    }


def _forged_events(method, body=None, path=PATH, params=PARAMS):
    in_body = dict(body or {}, lambdaCrossCall=FORGED)
    return {
        "body": _api_event(method, in_body, path, params),
        "headers": _api_event(method, body, path, params,
                              headers={"lambdaCrossCall": json.dumps(FORGED),
                                       "x-lambda-cross-call": json.dumps(FORGED)}),
        "query": _api_event(method, body, path, params,
                            query={"lambdaCrossCall": json.dumps(FORGED), "source": "vamsSchemaImport"}),
    }


FORGED_WHERE = ["body", "headers", "query"]


def _enforcer():
    inst = MagicMock()
    inst.enforceAPI.return_value = True
    inst.enforce.return_value = True
    return inst


def _message(resp):
    return json.loads(resp["body"])["message"]


def _workflow_call(event, item, tokens=("user1",), triggers=None):
    table = MagicMock()
    table.get_item.return_value = {"Item": dict(item)} if item is not None else {}
    if triggers is None:
        triggers = MagicMock()
        triggers.query.return_value = {"Items": []}
    asl = MagicMock()
    asl.deploy_state_machine.return_value = ("arn:test:states:sm", ["job1"], "arn:test:states:sm:1")
    resolved = (None, [(SpecifiedPipelineInput(pipelineId="pipe1", pipelineDatabaseId="GLOBAL"),
                        dict(PIPELINE_REC))])
    with patch(f"{WS}.CasbinEnforcer", return_value=_enforcer()), \
         patch(f"{WS}.request_to_claims", return_value={"tokens": list(tokens)}), \
         patch(f"{WS}._workflow_table", return_value=table), \
         patch(f"{WS}._triggers_table", return_value=triggers), \
         patch(f"{WS}.find_workflow_id_owner", return_value=None), \
         patch(f"{WS}._resolve_referenced_pipelines", return_value=resolved), \
         patch(f"{WS}._resolve_snapshot_pipeline_records", return_value=[dict(PIPELINE_REC)]), \
         patch(f"{WS}._save_validation", return_value=([], [])), \
         patch(f"{WS}.workflowAsl", asl):
        resp = ws.lambda_handler(event, MagicMock())
    return resp, table, asl, triggers


def _nothing_written(table, asl):
    table.update_item.assert_not_called()
    table.put_item.assert_not_called()
    asl.deploy_state_machine.assert_not_called()


# ---------------------------------------------------------------------------------------------------
# Trust boundary
# ---------------------------------------------------------------------------------------------------

@pytest.mark.unit
class TestForgedCrossCallInAnApiRequest:
    @pytest.mark.parametrize("where", FORGED_WHERE)
    def test_the_forged_envelope_is_not_an_import_call(self, where):
        assert sr.is_schema_import_call(_forged_events("PUT", {"description": "x"})[where]) is False

    @pytest.mark.parametrize("where", FORGED_WHERE)
    def test_an_update_of_a_system_workflow_is_still_refused(self, where):
        event = _forged_events("PUT", {"description": "edited", "isSystem": False})[where]
        resp, table, asl, _ = _workflow_call(event, SYSTEM_ROW)
        assert resp["statusCode"] == 400
        assert _message(resp) == sr.system_workflow_update_refusal("description")
        _nothing_written(table, asl)

    @pytest.mark.parametrize("where", FORGED_WHERE)
    def test_an_archive_of_a_system_workflow_is_still_refused(self, where):
        triggers = MagicMock()
        triggers.query.return_value = {"Items": [{"triggerType": "fileUpload"}]}
        resp, table, asl, triggers = _workflow_call(_forged_events("DELETE")[where], SYSTEM_ROW,
                                                    triggers=triggers)
        assert resp["statusCode"] == 400
        assert _message(resp) == sr.SYSTEM_WORKFLOW_ARCHIVE_MESSAGE
        _nothing_written(table, asl)
        triggers.delete_item.assert_not_called()

    @pytest.mark.parametrize("where", FORGED_WHERE)
    def test_a_put_restore_is_still_refused(self, where):
        event = _forged_events("PUT", {"archived": False})[where]
        resp, table, asl, _ = _workflow_call(event, dict(SYSTEM_ROW, archived=True))
        assert resp["statusCode"] == 400
        assert _message(resp) == sr.SYSTEM_WORKFLOW_ARCHIVE_MESSAGE
        _nothing_written(table, asl)

    @pytest.mark.parametrize("where", FORGED_WHERE)
    def test_a_create_cannot_set_isSystem(self, where):
        event = _forged_events("POST", dict(CREATE_BODY, isSystem=True), CREATE_PATH, CREATE_PARAMS)[where]
        resp, table, _, _ = _workflow_call(event, None)
        assert resp["statusCode"] == 200, resp["body"]
        assert table.put_item.call_args.kwargs["Item"]["isSystem"] is False
        assert _message(resp)["isSystem"] is False

    @pytest.mark.parametrize("where", FORGED_WHERE)
    def test_an_update_cannot_promote_a_plain_workflow(self, where):
        event = _forged_events("PUT", {"enabled": True, "isSystem": True})[where]
        resp, table, _, _ = _workflow_call(event, PLAIN_ROW)
        assert resp["statusCode"] == 200, resp["body"]
        assert "isSystem" not in _written(table)

    @pytest.mark.parametrize("where", FORGED_WHERE)
    def test_the_forged_envelope_cannot_reach_an_internal_action(self, where):
        # The internal actions need a top-level action key, a SYSTEM_USER cross-call and no
        # requestContext; a forged body/header/query supplies none of them, so the request routes as
        # an ordinary GET and never redeploys.
        event = _forged_events("GET")[where]
        assert ws._is_internal_action_event(event) is False


# ---------------------------------------------------------------------------------------------------
# Workflow PUT conditions
# ---------------------------------------------------------------------------------------------------

@pytest.mark.unit
class TestWorkflowPutConditions:
    @pytest.mark.parametrize("field", sorted(REFUSED_FIELDS))
    def test_each_refused_field_alone_is_refused_naming_it(self, field):
        resp, table, asl, _ = _workflow_call(_api_event("PUT", {field: REFUSED_FIELDS[field]}), SYSTEM_ROW)
        assert resp["statusCode"] == 400
        assert _message(resp) == sr.system_workflow_update_refusal(field)
        _nothing_written(table, asl)

    @pytest.mark.parametrize("field", sorted(REFUSED_FIELDS))
    def test_each_refused_field_next_to_enabled_refuses_the_whole_request(self, field):
        body = {"enabled": False, field: REFUSED_FIELDS[field]}
        resp, table, asl, _ = _workflow_call(_api_event("PUT", body), SYSTEM_ROW)
        assert resp["statusCode"] == 400
        assert _message(resp) == sr.system_workflow_update_refusal(field)
        _nothing_written(table, asl)

    @pytest.mark.parametrize("field", ["workflowName", "category", "description"])
    def test_a_locked_field_resent_with_its_stored_value_is_refused(self, field):
        resp, table, asl, _ = _workflow_call(_api_event("PUT", {field: SYSTEM_ROW[field]}), SYSTEM_ROW)
        assert resp["statusCode"] == 400
        assert _message(resp) == sr.system_workflow_update_refusal(field)
        _nothing_written(table, asl)

    def test_an_unknown_field_next_to_enabled_is_ignored_and_not_stored(self):
        resp, table, _, _ = _workflow_call(_api_event("PUT", {"enabled": False, "owner": "me"}), SYSTEM_ROW)
        assert resp["statusCode"] == 200, resp["body"]
        written = _written(table)
        assert written["enabled"] is False and "owner" not in written

    @pytest.mark.parametrize("variant", ["Description", "WORKFLOWNAME", "specifiedpipelines"])
    def test_a_case_variant_of_a_locked_field_is_ignored_and_not_stored(self, variant):
        resp, table, _, _ = _workflow_call(_api_event("PUT", {"enabled": False, variant: "x"}), SYSTEM_ROW)
        assert resp["statusCode"] == 200, resp["body"]
        written = _written(table)
        assert variant not in written
        assert set(written) <= {"enabled", "dateModified", "modifiedBy"}

    @pytest.mark.parametrize("variant", ["Enabled", "ENABLED"])
    def test_a_case_variant_of_enabled_alone_supplies_nothing(self, variant):
        resp, table, asl, _ = _workflow_call(_api_event("PUT", {variant: False}), SYSTEM_ROW)
        assert resp["statusCode"] == 400
        _nothing_written(table, asl)

    @pytest.mark.parametrize("value", [False, "false", 0])
    def test_an_api_caller_cannot_clear_the_flag(self, value):
        resp, table, _, _ = _workflow_call(_api_event("PUT", {"enabled": False, "isSystem": value}), SYSTEM_ROW)
        assert resp["statusCode"] == 200, resp["body"]
        assert "isSystem" not in _written(table)
        assert _message(resp)["isSystem"] is True

    @pytest.mark.parametrize("enabled", [True, False])
    def test_enabled_is_accepted_both_ways_and_only_it_is_written(self, enabled):
        resp, table, _, _ = _workflow_call(_api_event("PUT", {"enabled": enabled}),
                                           dict(SYSTEM_ROW, enabled=not enabled))
        assert resp["statusCode"] == 200, resp["body"]
        written = _written(table)
        assert written["enabled"] is enabled
        for key in REFUSED_FIELDS:
            assert key not in written, key


@pytest.mark.unit
class TestNonSystemWorkflowIsUnchanged:
    @pytest.mark.parametrize("field", ["workflowName", "category", "description", "subDashboardUrl"])
    def test_each_field_is_accepted(self, field):
        resp, table, _, _ = _workflow_call(_api_event("PUT", {"enabled": True, field: REFUSED_FIELDS[field]}),
                                           PLAIN_ROW)
        assert resp["statusCode"] == 200, resp["body"]
        assert _written(table)[field] == REFUSED_FIELDS[field]

    def test_archive_and_put_restore_are_accepted(self):
        resp, table, _, _ = _workflow_call(_api_event("DELETE"), PLAIN_ROW)
        assert resp["statusCode"] == 200, resp["body"]
        assert _written(table)["archived"] is True
        resp, table, _, _ = _workflow_call(_api_event("PUT", {"archived": False}), dict(PLAIN_ROW, archived=True))
        assert resp["statusCode"] == 200, resp["body"]
        assert _written(table)["archived"] is False


@pytest.mark.unit
class TestTheImporterUnderEdgeConditions:
    IMPORT = {"userName": "SYSTEM_USER", "source": "vamsSchemaImport"}

    def _import_event(self, method, body=None, path=PATH, params=PARAMS):
        event = _api_event(method, body, path, params)
        del event["requestContext"]["authorizer"]
        event["lambdaCrossCall"] = dict(self.IMPORT)
        return event

    def test_a_bundle_that_drops_the_flag_demotes_the_record(self):
        body = {"workflowName": "S", "enabled": True, "archived": False, "isSystem": False}
        resp, table, _, _ = _workflow_call(self._import_event("PUT", body), SYSTEM_ROW, tokens=("SYSTEM_USER",))
        assert resp["statusCode"] == 200, resp["body"]
        assert _written(table)["isSystem"] is False

    @pytest.mark.parametrize("value", ["true", "false", 1])
    def test_a_non_boolean_from_a_direct_invoke_leaves_the_flag_alone(self, value):
        body = {"workflowName": "S", "enabled": True, "isSystem": value}
        resp, table, _, _ = _workflow_call(self._import_event("PUT", body), SYSTEM_ROW, tokens=("SYSTEM_USER",))
        assert resp["statusCode"] == 200, resp["body"]
        assert "isSystem" not in _written(table)

    @pytest.mark.parametrize("value", ["true", 1])
    def test_a_non_boolean_on_create_stores_false(self, value):
        resp, table, _, _ = _workflow_call(
            self._import_event("POST", dict(CREATE_BODY, isSystem=value), CREATE_PATH, CREATE_PARAMS),
            None, tokens=("SYSTEM_USER",))
        assert resp["statusCode"] == 200, resp["body"]
        assert table.put_item.call_args.kwargs["Item"]["isSystem"] is False


@pytest.mark.unit
class TestListCarriesIsSystem:
    def test_workflow_list_items_carry_the_flag_for_both_kinds(self):
        rows = [dict(SYSTEM_ROW), {k: v for k, v in PLAIN_ROW.items() if k != "isSystem"}]
        paginator = MagicMock()
        paginator.paginate.return_value.build_full_result.return_value = {"Items": rows}
        dynamodb = MagicMock()
        dynamodb.meta.client.get_paginator.return_value = paginator
        with patch(f"{WS}.dynamodb", dynamodb), \
             patch(f"{WS}.CasbinEnforcer", return_value=_enforcer()), \
             patch(f"{WS}.request_to_claims", return_value={"tokens": ["user1"]}), \
             patch(f"{WS}._batch_pipeline_system_configs", return_value={}):
            resp = ws.lambda_handler(_api_event("GET", path="/workflows", params=None,
                                                query={"maxItems": "100", "pageSize": "100"}), MagicMock())
        assert resp["statusCode"] == 200, resp["body"]
        items = _message(resp)["Items"]
        assert [(i["workflowId"], i["isSystem"]) for i in items] == [("sysw", True), ("sysw", False)]


# ---------------------------------------------------------------------------------------------------
# Trigger conditions
# ---------------------------------------------------------------------------------------------------

TBASE = "/database/GLOBAL/workflows/sysw/triggers"
TPARAMS = {"databaseId": "GLOBAL", "workflowId": "sysw", "triggerType": "fileUpload"}
SPECIFIED = {"pipelineDatabaseId": "GLOBAL", "pipelineId": "sysp",
             "pipelineDatabaseId:pipelineId": "GLOBAL:sysp", "jobName": "", "defaultTemplateId": ""}
SYSTEM_WF = {"databaseId": "GLOBAL", "workflowId": "sysw", "workflowName": "S", "isSystem": True,
             "systemConfig": {"concurrencyRestriction": "none"}, "specifiedPipelines": [SPECIFIED]}
PLAIN_WF = dict(SYSTEM_WF, isSystem=False)
STORED_CONFIG = {"inputFileFilters": {"allow": ["*.glb", "*.stl"], "exclude": ["*.tmp"]},
                 "defaultTemplateIds": {"GLOBAL:sysp": "sys-default"}}
STORED_TRIGGER = {"workflowDatabaseId:workflowId": "GLOBAL:sysw", "triggerType": "fileUpload",
                  "triggerBaseType": "fileUpload", "triggerId": "", "workflowDatabaseId": "GLOBAL",
                  "workflowId": "sysw", "triggerConfig": STORED_CONFIG, "enabled": True,
                  "dateCreated": "2026-01-01T00:00:00Z", "dateModified": "2026-01-01T00:00:00Z"}
TRIGGER_CHANGES = {
    "inputFileFilters": [
        {"allow": ["*.png"], "exclude": ["*.tmp"]},
        {"allow": ["*.glb", "*.stl"], "exclude": []},
        {"allow": ["*.glb", "*.stl", "*.obj"], "exclude": ["*.tmp"]},
        {"allow": [], "exclude": ["*.tmp"]},
    ],
    "defaultTemplateIds": [
        {"GLOBAL:sysp": "other"},
        {},
    ],
}


def _trigger_event(method, body=None, params=TPARAMS, **kwargs):
    return _api_event(method, body, f"{TBASE}/{params['triggerType']}", params, **kwargs)


def _trigger_call(event, workflow=SYSTEM_WF, stored=STORED_TRIGGER):
    table = MagicMock()
    pipelines = MagicMock()
    pipelines.get_item.return_value = {"Item": {"databaseId": "GLOBAL", "pipelineId": "sysp",
                                                "pipelineName": "P", "systemConfig": {},
                                                "executionConfig": {"executionType": "Lambda"}}}
    templates = MagicMock()
    templates.query.return_value = {"Items": []}
    with patch(f"{WTS}.CasbinEnforcer", return_value=_enforcer()), \
         patch(f"{WTS}.request_to_claims", return_value={"tokens": ["user1"]}), \
         patch(f"{WTS}._enforce_parent_workflow", return_value=(True, dict(workflow))), \
         patch(f"{WTS}.get_trigger", return_value=dict(stored) if stored else None), \
         patch(f"{WTS}._same_type_triggers", return_value=[]), \
         patch(f"{WTS}._triggers_table", return_value=table), \
         patch(f"{WTS}._pipelines_table", return_value=pipelines), \
         patch(f"{WTS}._templates_table", return_value=templates), \
         patch(f"{WTS}._load_template_tag_schema_fields", return_value=None):
        resp = wts.lambda_handler(event, MagicMock())
    return resp, table


def _trigger_change_cases():
    for field, values in TRIGGER_CHANGES.items():
        for index, value in enumerate(values):
            yield pytest.param(field, value, id=f"{field}-{index}")


@pytest.mark.unit
class TestTriggerConditions:
    @pytest.mark.parametrize("field,value", list(_trigger_change_cases()))
    def test_each_changed_locked_field_is_refused_naming_it(self, field, value):
        resp, table = _trigger_call(_trigger_event("PUT", {field: value}))
        assert resp["statusCode"] == 400
        assert _message(resp) == sr.system_trigger_locked_field_message(field)
        table.put_item.assert_not_called()

    @pytest.mark.parametrize("field,value", list(_trigger_change_cases()))
    def test_enabled_next_to_a_changed_locked_field_refuses_the_whole_request(self, field, value):
        resp, table = _trigger_call(_trigger_event("PUT", {"enabled": False, field: value}))
        assert resp["statusCode"] == 400
        table.put_item.assert_not_called()

    @pytest.mark.parametrize("enabled", [True, False])
    def test_the_stored_trigger_resent_with_enabled_flipped_is_accepted(self, enabled):
        body = {"enabled": enabled, **STORED_CONFIG}
        resp, table = _trigger_call(_trigger_event("PUT", body), stored=dict(STORED_TRIGGER, enabled=not enabled))
        assert resp["statusCode"] == 200, resp["body"]
        saved = table.put_item.call_args.kwargs["Item"]
        assert saved["enabled"] is enabled and saved["triggerConfig"] == STORED_CONFIG

    @pytest.mark.parametrize("body", [
        {"enabled": False, "owner": "me"},
        {"enabled": False, "InputFileFilters": {"allow": ["*.png"]}},
        {"enabled": False, "DEFAULTTEMPLATEIDS": {"GLOBAL:sysp": "other"}},
        {"enabled": False, "isSystem": False},
        {"enabled": False, "inputFileFilters": None, "defaultTemplateIds": None},
    ], ids=["unknown", "case-filters", "case-templates", "isSystem", "nulls"])
    def test_ignored_keys_keep_the_stored_config(self, body):
        resp, table = _trigger_call(_trigger_event("PUT", body))
        assert resp["statusCode"] == 200, resp["body"]
        saved = table.put_item.call_args.kwargs["Item"]
        assert saved["enabled"] is False and saved["triggerConfig"] == STORED_CONFIG
        assert "isSystem" not in saved and "owner" not in saved

    @pytest.mark.parametrize("where", FORGED_WHERE)
    def test_a_forged_cross_call_cannot_change_filters(self, where):
        event = _forged_events("PUT", {"inputFileFilters": {"allow": ["*"]}}, f"{TBASE}/fileUpload", TPARAMS)[where]
        resp, table = _trigger_call(event)
        assert resp["statusCode"] == 400
        assert _message(resp) == sr.system_trigger_locked_field_message("inputFileFilters")
        table.put_item.assert_not_called()

    @pytest.mark.parametrize("where", FORGED_WHERE)
    def test_a_forged_cross_call_cannot_add_or_delete(self, where):
        extra = dict(TPARAMS, triggerType="fileUpload#extra")
        add = _forged_events("PUT", {"inputFileFilters": {"allow": ["*"]}}, f"{TBASE}/fileUpload#extra", extra)[where]
        resp, table = _trigger_call(add, stored=None)
        assert resp["statusCode"] == 400
        assert _message(resp) == sr.SYSTEM_TRIGGER_LOCKED_MESSAGE
        table.put_item.assert_not_called()
        delete = _forged_events("DELETE", None, f"{TBASE}/fileUpload", TPARAMS)[where]
        resp, table = _trigger_call(delete)
        assert resp["statusCode"] == 400
        assert _message(resp) == sr.SYSTEM_TRIGGER_LOCKED_MESSAGE
        table.delete_item.assert_not_called()

    def test_a_non_json_object_body_is_a_400_and_writes_nothing(self):
        event = _trigger_event("PUT")
        event["body"] = '["enabled"]'
        resp, table = _trigger_call(event)
        assert resp["statusCode"] == 400
        table.put_item.assert_not_called()

    @pytest.mark.parametrize("field,value", list(_trigger_change_cases()))
    def test_a_non_system_workflow_accepts_every_change(self, field, value):
        resp, table = _trigger_call(_trigger_event("PUT", {field: value}), workflow=PLAIN_WF)
        assert resp["statusCode"] == 200, resp["body"]
        table.put_item.assert_called_once()
