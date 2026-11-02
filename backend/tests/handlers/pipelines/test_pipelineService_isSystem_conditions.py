# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""System pipelines and their templates under the edge conditions of a request.

Companion to test_pipelineService_isSystem.py and test_pipelineTemplateService_isSystem.py, which
cover the main paths. This file covers:

- the trust boundary: an API Gateway event that carries a forged `lambdaCrossCall` in its body, its
  headers or its query parameters is an ordinary API call. It is refused on a system record and it
  cannot set `isSystem`;
- every refused field on its own, and mixed with the allowed `enabled` in one request (refused as a
  whole, nothing written);
- a locked field re-sent with its stored value: refused on a pipeline (key-based rule), accepted on a
  template (value-based rule, because the web editor re-sends the whole template);
- unknown fields and case variants of field names, which the request models ignore, so they are
  neither refused nor stored;
- `isSystem` sent by an API caller, which never changes the stored flag in either direction;
- template content (configBody, tagSchema, webFormJson) changed next to a locked field: refused as a
  whole, so neither the template row nor its tag schema is written;
- the same requests on a non-system pipeline, which behave as before;
- single-record GET responses carrying `isSystem` for both kinds.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.common.workflows import systemRecords as sr
from backend.backend.handlers.auth import request_to_claims
from backend.backend.handlers.pipelines import pipelineService as ps
from backend.backend.handlers.pipelines import pipelineTemplateService as pts

PS = "backend.backend.handlers.pipelines.pipelineService"
PTS = "backend.backend.handlers.pipelines.pipelineTemplateService"

FORGED = {"userName": "SYSTEM_USER", "source": "vamsSchemaImport"}
PATH = "/database/GLOBAL/pipelines/sys1"
PARAMS = {"databaseId": "GLOBAL", "pipelineId": "sys1"}
CREATE_PATH = "/database/GLOBAL/pipelines"
CREATE_PARAMS = {"databaseId": "GLOBAL"}
LAMBDA_CONFIG = {"executionType": "Lambda", "lambda": {"resourceId": "fn-sys1"}}

SYSTEM_ROW = {"databaseId": "GLOBAL", "pipelineId": "sys1", "pipelineName": "S", "isSystem": True,
              "enabled": True, "archived": False, "category": "SYSTEM - Test",
              "description": "shipped", "executionConfig": LAMBDA_CONFIG, "systemConfig": {}}
PLAIN_ROW = dict(SYSTEM_ROW, pipelineName="P", category="Test", isSystem=False)
CREATE_BODY = {"databaseId": "GLOBAL", "pipelineId": "sys1", "pipelineName": "S",
               "category": "SYSTEM - Test", "executionConfig": LAMBDA_CONFIG}

# One value per field of UpdatePipelineRequestModel other than `enabled`.
REFUSED_FIELDS = {
    "pipelineName": "renamed",
    "category": "Other",
    "description": "edited",
    "executionConfig": {"executionType": "Lambda", "lambda": {"resourceId": "fn-other"}},
    "systemConfig": {"inputFileArity": "multi"},
}


def _api_event(method, body=None, path=PATH, params=PARAMS, headers=None, query=None, raw_body=None):
    """An API Gateway (HTTP API v2) proxy event: the caller controls only the body, the headers, the
    path and the query string; requestContext.authorizer is the custom authorizer's output."""
    return {
        "requestContext": {"http": {"method": method, "path": path},
                           "authorizer": {"lambda": {"vams:tokens": '["user1"]'}}},
        "pathParameters": params,
        "queryStringParameters": query,
        "headers": dict({"authorization": "Bearer test-token"}, **(headers or {})),
        "body": raw_body if raw_body is not None else (json.dumps(body) if body is not None else None),
    }


def _forged_events(method, body=None, path=PATH, params=PARAMS):
    """The three places a caller can put a forged cross-call envelope in an API request."""
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


def _enforcer(obj=True):
    inst = MagicMock()
    inst.enforceAPI.return_value = True
    inst.enforce.return_value = obj
    return inst


def _message(resp):
    return json.loads(resp["body"])["message"]


@pytest.fixture(autouse=True)
def _quiet_side_paths():
    with patch(f"{PS}._pipeline_save_warnings", return_value=[]), \
         patch(f"{PS}._provision_lambda_for_pipeline", side_effect=lambda cfg, pid: cfg), \
         patch(f"{PS}.find_pipeline_id_owner", return_value=None):
        yield


def _pipeline_call(event, item, tokens=("user1",)):
    """Run the pipeline service with Casbin allowing and the caller resolved to `tokens`. The guard
    under test reads the event itself (is_schema_import_call), not the resolved claims; the real
    claims resolver is checked against the forged events separately below."""
    table = MagicMock()
    table.get_item.return_value = {"Item": dict(item)} if item is not None else {}
    with patch(f"{PS}.CasbinEnforcer", return_value=_enforcer()), \
         patch(f"{PS}.request_to_claims", return_value={"tokens": list(tokens)}), \
         patch(f"{PS}._pipeline_table", return_value=table):
        resp = ps.lambda_handler(event, MagicMock())
    return resp, table


# ---------------------------------------------------------------------------------------------------
# Trust boundary
# ---------------------------------------------------------------------------------------------------

@pytest.mark.unit
class TestForgedCrossCallInAnApiRequest:
    @pytest.mark.parametrize("where", FORGED_WHERE)
    def test_the_forged_envelope_is_not_an_import_call(self, where):
        event = _forged_events("PUT", {"description": "edited"})[where]
        assert sr.is_schema_import_call(event) is False

    @pytest.mark.parametrize("where", FORGED_WHERE)
    def test_an_update_of_a_system_pipeline_is_still_refused(self, where):
        event = _forged_events("PUT", {"description": "edited", "isSystem": False})[where]
        resp, table = _pipeline_call(event, SYSTEM_ROW)
        assert resp["statusCode"] == 400
        assert _message(resp) == sr.system_pipeline_update_refusal("description")
        table.put_item.assert_not_called()

    @pytest.mark.parametrize("where", FORGED_WHERE)
    def test_an_archive_of_a_system_pipeline_is_still_refused(self, where):
        resp, table = _pipeline_call(_forged_events("DELETE")[where], SYSTEM_ROW)
        assert resp["statusCode"] == 400
        assert _message(resp) == sr.SYSTEM_PIPELINE_ARCHIVE_MESSAGE
        table.put_item.assert_not_called()

    @pytest.mark.parametrize("where", FORGED_WHERE)
    def test_a_create_cannot_set_isSystem(self, where):
        event = _forged_events("POST", dict(CREATE_BODY, isSystem=True), CREATE_PATH, CREATE_PARAMS)[where]
        resp, table = _pipeline_call(event, None)
        assert resp["statusCode"] == 200, resp["body"]
        assert table.put_item.call_args.kwargs["Item"]["isSystem"] is False
        assert _message(resp)["isSystem"] is False

    @pytest.mark.parametrize("where", FORGED_WHERE)
    def test_an_update_cannot_promote_a_plain_pipeline(self, where):
        event = _forged_events("PUT", {"enabled": True, "isSystem": True})[where]
        resp, table = _pipeline_call(event, PLAIN_ROW)
        assert resp["statusCode"] == 200, resp["body"]
        assert table.put_item.call_args.kwargs["Item"]["isSystem"] is False

    @pytest.mark.parametrize("where", FORGED_WHERE)
    def test_a_restore_of_an_archived_system_id_is_still_refused(self, where):
        archived = dict(SYSTEM_ROW, archived=True, enabled=False)
        event = _forged_events("POST", CREATE_BODY, CREATE_PATH, CREATE_PARAMS)[where]
        resp, table = _pipeline_call(event, archived)
        assert resp["statusCode"] == 400
        assert _message(resp) == sr.SYSTEM_PIPELINE_ARCHIVE_MESSAGE
        table.put_item.assert_not_called()

    def test_the_real_claims_resolver_reads_the_forged_event_as_the_api_caller(self):
        # The forged body/header/query never reaches request_to_claims as a cross-call: the resolver
        # sees no top-level lambdaCrossCall and falls through to the authorizer's claims.
        for where, event in _forged_events("PUT", {"description": "x"}).items():
            claims = request_to_claims(event)
            assert claims["tokens"] != ["SYSTEM_USER"], where


# ---------------------------------------------------------------------------------------------------
# Pipeline PUT conditions
# ---------------------------------------------------------------------------------------------------

@pytest.mark.unit
class TestPipelinePutConditions:
    @pytest.mark.parametrize("field", sorted(REFUSED_FIELDS))
    def test_each_refused_field_alone_is_refused_naming_it(self, field):
        resp, table = _pipeline_call(_api_event("PUT", {field: REFUSED_FIELDS[field]}), SYSTEM_ROW)
        assert resp["statusCode"] == 400
        assert _message(resp) == sr.system_pipeline_update_refusal(field)
        table.put_item.assert_not_called()

    @pytest.mark.parametrize("field", sorted(REFUSED_FIELDS))
    def test_each_refused_field_next_to_enabled_refuses_the_whole_request(self, field):
        body = {"enabled": False, field: REFUSED_FIELDS[field]}
        resp, table = _pipeline_call(_api_event("PUT", body), SYSTEM_ROW)
        assert resp["statusCode"] == 400
        assert _message(resp) == sr.system_pipeline_update_refusal(field)
        # Nothing is written: the allowed half of the request is not applied either.
        table.put_item.assert_not_called()

    @pytest.mark.parametrize("field", ["pipelineName", "category", "description"])
    def test_a_locked_field_resent_with_its_stored_value_is_refused(self, field):
        # Key-based rule (documented): any field other than `enabled` is refused, equal or not.
        resp, table = _pipeline_call(_api_event("PUT", {field: SYSTEM_ROW[field]}), SYSTEM_ROW)
        assert resp["statusCode"] == 400
        assert _message(resp) == sr.system_pipeline_update_refusal(field)
        table.put_item.assert_not_called()

    def test_the_first_refused_field_in_name_order_is_named(self):
        body = {"pipelineName": "x", "description": "y", "category": "z"}
        resp, _ = _pipeline_call(_api_event("PUT", body), SYSTEM_ROW)
        assert _message(resp) == sr.system_pipeline_update_refusal("category")

    def test_an_unknown_field_next_to_enabled_is_ignored_and_not_stored(self):
        resp, table = _pipeline_call(_api_event("PUT", {"enabled": False, "owner": "me", "x": 1}), SYSTEM_ROW)
        assert resp["statusCode"] == 200, resp["body"]
        saved = table.put_item.call_args.kwargs["Item"]
        assert saved["enabled"] is False and saved["isSystem"] is True
        assert "owner" not in saved and "x" not in saved

    @pytest.mark.parametrize("variant", ["Description", "DESCRIPTION", "pipelinename", "SystemConfig"])
    def test_a_case_variant_of_a_locked_field_is_ignored_and_not_stored(self, variant):
        resp, table = _pipeline_call(_api_event("PUT", {"enabled": False, variant: "x"}), SYSTEM_ROW)
        assert resp["statusCode"] == 200, resp["body"]
        saved = table.put_item.call_args.kwargs["Item"]
        assert variant not in saved
        assert saved["description"] == "shipped" and saved["pipelineName"] == "S"

    @pytest.mark.parametrize("variant", ["Enabled", "ENABLED"])
    def test_a_case_variant_of_enabled_alone_supplies_nothing(self, variant):
        resp, table = _pipeline_call(_api_event("PUT", {variant: False}), SYSTEM_ROW)
        assert resp["statusCode"] == 400
        table.put_item.assert_not_called()

    @pytest.mark.parametrize("value", [False, "false", 0, None])
    def test_an_api_caller_cannot_clear_the_flag(self, value):
        resp, table = _pipeline_call(_api_event("PUT", {"enabled": False, "isSystem": value}), SYSTEM_ROW)
        assert resp["statusCode"] == 200, resp["body"]
        assert table.put_item.call_args.kwargs["Item"]["isSystem"] is True
        assert _message(resp)["isSystem"] is True

    def test_isSystem_alone_from_an_api_caller_supplies_nothing(self):
        resp, table = _pipeline_call(_api_event("PUT", {"isSystem": False}), SYSTEM_ROW)
        assert resp["statusCode"] == 400
        table.put_item.assert_not_called()

    @pytest.mark.parametrize("enabled", [True, False])
    def test_enabled_is_accepted_both_ways_and_nothing_else_changes(self, enabled):
        row = dict(SYSTEM_ROW, enabled=not enabled)
        resp, table = _pipeline_call(_api_event("PUT", {"enabled": enabled}), row)
        assert resp["statusCode"] == 200, resp["body"]
        saved = table.put_item.call_args.kwargs["Item"]
        assert saved["enabled"] is enabled
        for key in ("pipelineName", "category", "description", "executionConfig", "isSystem", "archived"):
            assert saved[key] == SYSTEM_ROW[key], key

    def test_a_non_json_object_body_is_a_400_and_writes_nothing(self):
        resp, table = _pipeline_call(_api_event("PUT", raw_body="[]"), SYSTEM_ROW)
        assert resp["statusCode"] == 400
        table.put_item.assert_not_called()


@pytest.mark.unit
class TestNonSystemPipelineIsUnchanged:
    @pytest.mark.parametrize("field", sorted(REFUSED_FIELDS))
    def test_each_field_is_accepted(self, field):
        resp, table = _pipeline_call(_api_event("PUT", {"enabled": True, field: REFUSED_FIELDS[field]}),
                                     PLAIN_ROW)
        assert resp["statusCode"] == 200, resp["body"]
        assert table.put_item.call_args.kwargs["Item"]["isSystem"] is False

    def test_archive_and_put_restore_are_accepted(self):
        resp, table = _pipeline_call(_api_event("DELETE"), PLAIN_ROW)
        assert resp["statusCode"] == 200, resp["body"]
        assert table.put_item.call_args.kwargs["Item"]["archived"] is True
        resp, table = _pipeline_call(_api_event("PUT", {"archived": False}), dict(PLAIN_ROW, archived=True))
        assert resp["statusCode"] == 200, resp["body"]
        assert table.put_item.call_args.kwargs["Item"]["archived"] is False

    def test_post_restore_of_an_archived_plain_id_is_accepted(self):
        archived = dict(PLAIN_ROW, archived=True, enabled=False)
        resp, table = _pipeline_call(_api_event("POST", CREATE_BODY, CREATE_PATH, CREATE_PARAMS), archived)
        assert resp["statusCode"] == 200, resp["body"]
        assert table.put_item.call_args.kwargs["Item"]["archived"] is False


@pytest.mark.unit
class TestTheImporterUnderEdgeConditions:
    IMPORT = {"userName": "SYSTEM_USER", "source": "vamsSchemaImport"}

    def _import_event(self, method, body=None, path=PATH, params=PARAMS):
        event = _api_event(method, body, path, params)
        del event["requestContext"]["authorizer"]
        event["lambdaCrossCall"] = dict(self.IMPORT)
        return event

    def test_a_bundle_that_drops_the_flag_demotes_the_record(self):
        # The bundle is authoritative: a re-registration with isSystem false makes the row editable.
        body = {"pipelineName": "S", "enabled": True, "archived": False, "isSystem": False}
        resp, table = _pipeline_call(self._import_event("PUT", body), SYSTEM_ROW, tokens=("SYSTEM_USER",))
        assert resp["statusCode"] == 200, resp["body"]
        assert table.put_item.call_args.kwargs["Item"]["isSystem"] is False

    @pytest.mark.parametrize("value", ["true", "false", 1])
    def test_a_non_boolean_from_a_direct_invoke_leaves_the_flag_alone(self, value):
        body = {"pipelineName": "S", "enabled": True, "isSystem": value}
        for row, expected in ((SYSTEM_ROW, True), (PLAIN_ROW, False)):
            resp, table = _pipeline_call(self._import_event("PUT", body), row, tokens=("SYSTEM_USER",))
            assert resp["statusCode"] == 200, resp["body"]
            assert table.put_item.call_args.kwargs["Item"]["isSystem"] is expected

    @pytest.mark.parametrize("value", ["true", 1])
    def test_a_non_boolean_on_create_stores_false(self, value):
        resp, table = _pipeline_call(
            self._import_event("POST", dict(CREATE_BODY, isSystem=value), CREATE_PATH, CREATE_PARAMS),
            None, tokens=("SYSTEM_USER",))
        assert resp["statusCode"] == 200, resp["body"]
        assert table.put_item.call_args.kwargs["Item"]["isSystem"] is False


@pytest.mark.unit
class TestSingleGetCarriesIsSystem:
    @pytest.mark.parametrize("row,expected", [(SYSTEM_ROW, True), (PLAIN_ROW, False),
                                              ({k: v for k, v in PLAIN_ROW.items() if k != "isSystem"}, False)])
    def test_get_returns_the_stored_flag(self, row, expected):
        with patch(f"{PS}.get_pipeline_templates", return_value=[]):
            resp, _ = _pipeline_call(_api_event("GET"), row)
        assert resp["statusCode"] == 200, resp["body"]
        assert _message(resp)["isSystem"] is expected


# ---------------------------------------------------------------------------------------------------
# Template conditions
# ---------------------------------------------------------------------------------------------------

TBASE = "/database/GLOBAL/pipelines/sys1/templates"
TPARAMS = {"databaseId": "GLOBAL", "pipelineId": "sys1", "templateId": "sys-default"}
STORED_TEMPLATE = {
    "pipelineDatabaseId:pipelineId": "GLOBAL:sys1", "templateId": "sys-default",
    "pipelineDatabaseId": "GLOBAL", "pipelineId": "sys1", "templateName": "Default",
    "description": "shipped", "configFormat": "json", "allowCustomEdit": True,
    "inputInstructions": "", "bodyStorage": "inline", "configBody": '{"views": 8}',
    "webFormJson": "", "overrides": {}, "isDefault": True,
    "dateCreated": "2026-01-01T00:00:00Z", "dateModified": "2026-01-01T00:00:00Z",
}
LOCKED_TEMPLATE_CHANGES = {
    "templateName": "renamed",
    "description": "edited",
    "configFormat": "yaml",
    "allowCustomEdit": False,
    "inputInstructions": "do this",
    "overrides": {"inputFileArity": "multi"},
    "isDefault": False,
}
CONTENT_EDITS = {
    "configBody": {"configBody": '{"views": 4}'},
    "tagSchema": {"tagSchema": [{"tagKey": "VIEWS", "type": "integer", "default": 8}]},
    "webFormJson": {"webFormJson": '[{"name": "views"}]'},
}


def _template_call(event, pipeline=None, template_row=STORED_TEMPLATE):
    pipeline = pipeline or SYSTEM_ROW
    templates = MagicMock()
    tag_table = MagicMock()
    tag_table.query.return_value = {"Items": []}
    triggers = MagicMock()
    triggers.query.return_value = {"Items": []}
    with patch(f"{PTS}.CasbinEnforcer", return_value=_enforcer()), \
         patch(f"{PTS}.request_to_claims", return_value={"tokens": ["user1"]}), \
         patch(f"{PTS}._enforce_parent_pipeline", return_value=(True, dict(pipeline))), \
         patch(f"{PTS}._get_template_row", return_value=dict(template_row) if template_row else None), \
         patch(f"{PTS}._rehydrate_template",
               return_value={"configBody": STORED_TEMPLATE["configBody"], "webFormJson": ""}), \
         patch(f"{PTS}._default_bucket",
               return_value={"bucketId": "b-id", "bucketName": "b", "baseAssetsPrefix": ""}), \
         patch(f"{PTS}._templates_table", return_value=templates), \
         patch(f"{PTS}._tag_schema_table", return_value=tag_table), \
         patch(f"{PTS}._triggers_table", return_value=triggers):
        resp = pts.lambda_handler(event, MagicMock())
    return resp, templates, tag_table


def _tpl_event(method, body=None, template_id="sys-default", **kwargs):
    path = f"{TBASE}/{template_id}" if template_id else TBASE
    params = dict(TPARAMS, templateId=template_id) if template_id else {"databaseId": "GLOBAL",
                                                                           "pipelineId": "sys1"}
    return _api_event(method, body, path, params, **kwargs)


@pytest.mark.unit
class TestTemplateConditions:
    @pytest.mark.parametrize("field", sorted(LOCKED_TEMPLATE_CHANGES))
    def test_each_locked_field_alone_is_refused_naming_it(self, field):
        resp, templates, tag_table = _template_call(_tpl_event("PUT", {field: LOCKED_TEMPLATE_CHANGES[field]}))
        assert resp["statusCode"] == 400
        assert _message(resp) == sr.system_template_locked_field_message(field)
        templates.put_item.assert_not_called()
        tag_table.put_item.assert_not_called()

    @pytest.mark.parametrize("content", sorted(CONTENT_EDITS))
    @pytest.mark.parametrize("field", sorted(LOCKED_TEMPLATE_CHANGES))
    def test_content_next_to_a_changed_locked_field_refuses_the_whole_request(self, content, field):
        body = dict(CONTENT_EDITS[content], **{field: LOCKED_TEMPLATE_CHANGES[field]})
        resp, templates, tag_table = _template_call(_tpl_event("PUT", body))
        assert resp["statusCode"] == 400
        assert _message(resp) == sr.system_template_locked_field_message(field)
        templates.put_item.assert_not_called()
        tag_table.put_item.assert_not_called()

    @pytest.mark.parametrize("content", sorted(CONTENT_EDITS))
    def test_each_content_field_alone_is_accepted(self, content):
        resp, templates, _ = _template_call(_tpl_event("PUT", CONTENT_EDITS[content]))
        assert resp["statusCode"] == 200, resp["body"]
        templates.put_item.assert_called_once()

    @pytest.mark.parametrize("field", sorted(LOCKED_TEMPLATE_CHANGES))
    def test_a_locked_field_resent_with_its_stored_value_is_accepted(self, field):
        # Value-based rule (documented): the web editor sends the whole template on every save.
        body = {field: STORED_TEMPLATE[field], "configBody": '{"views": 2}'}
        resp, templates, _ = _template_call(_tpl_event("PUT", body))
        assert resp["statusCode"] == 200, resp["body"]
        assert templates.put_item.call_args.kwargs["Item"]["configBody"] == '{"views": 2}'

    @pytest.mark.parametrize("variant", ["TemplateName", "DESCRIPTION", "isdefault", "IsSystem"])
    def test_a_case_variant_or_unknown_field_is_ignored_and_not_stored(self, variant):
        body = {"configBody": '{"views": 2}', variant: "x"}
        resp, templates, _ = _template_call(_tpl_event("PUT", body))
        assert resp["statusCode"] == 200, resp["body"]
        saved = templates.put_item.call_args.kwargs["Item"]
        assert variant not in saved
        assert saved["templateName"] == "Default" and saved["description"] == "shipped"

    def test_isSystem_in_a_template_body_is_not_stored(self):
        resp, templates, _ = _template_call(_tpl_event("PUT", {"configBody": "{}", "isSystem": False}))
        assert resp["statusCode"] == 200, resp["body"]
        assert "isSystem" not in templates.put_item.call_args.kwargs["Item"]

    @pytest.mark.parametrize("where", FORGED_WHERE)
    def test_a_forged_cross_call_cannot_change_a_locked_field(self, where):
        event = _forged_events("PUT", {"templateName": "renamed"}, f"{TBASE}/sys-default", TPARAMS)[where]
        resp, templates, _ = _template_call(event)
        assert resp["statusCode"] == 400
        assert _message(resp) == sr.system_template_locked_field_message("templateName")
        templates.put_item.assert_not_called()

    @pytest.mark.parametrize("where", FORGED_WHERE)
    def test_a_forged_cross_call_cannot_add_or_delete(self, where):
        params = {"databaseId": "GLOBAL", "pipelineId": "sys1"}
        add = _forged_events("POST", {"templateName": "extra"}, TBASE, params)[where]
        resp, templates, _ = _template_call(add, template_row=None)
        assert resp["statusCode"] == 400
        assert _message(resp) == sr.SYSTEM_TEMPLATE_LOCKED_MESSAGE
        templates.put_item.assert_not_called()
        delete = _forged_events("DELETE", None, f"{TBASE}/sys-default", TPARAMS)[where]
        resp, templates, _ = _template_call(delete)
        assert resp["statusCode"] == 400
        assert _message(resp) == sr.SYSTEM_TEMPLATE_LOCKED_MESSAGE
        templates.delete_item.assert_not_called()

    @pytest.mark.parametrize("field", sorted(LOCKED_TEMPLATE_CHANGES))
    def test_a_non_system_pipeline_accepts_every_field(self, field):
        resp, templates, _ = _template_call(_tpl_event("PUT", {field: LOCKED_TEMPLATE_CHANGES[field]}),
                                            pipeline=PLAIN_ROW)
        assert resp["statusCode"] == 200, resp["body"]
        templates.put_item.assert_called_once()
