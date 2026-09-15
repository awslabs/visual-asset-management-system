# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""System pipelines: how `isSystem` crosses from the importer into the row, and what the row then
refuses.

Crossing (spec 4.2), three arms per write path: an API caller sending isSystem stores False; the
importer's marked cross-call stores True on create and on update of a pre-existing non-system row;
a SYSTEM_USER cross-call WITHOUT the marker stores False. Guards (spec 4.1): PUT accepts only
`enabled`; DELETE and the archived-row restore are refused; the importer is exempt from all three;
Tier-2 still runs first. Every refusal is a 400 carrying the exact message, so the CLI and MCP relay
it verbatim.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.common.workflows import systemRecords as sr
from backend.backend.handlers.pipelines.pipelineService import lambda_handler

MOD = "backend.backend.handlers.pipelines.pipelineService"

IMPORT_CALL = {"userName": "SYSTEM_USER", "source": "vamsSchemaImport"}
UNMARKED_CALL = {"userName": "SYSTEM_USER"}
PATH = "/database/GLOBAL/pipelines/sys1"
PARAMS = {"databaseId": "GLOBAL", "pipelineId": "sys1"}
LAMBDA_CONFIG = {"executionType": "Lambda", "lambda": {"resourceId": "fn-sys1"}}

SYSTEM_ROW = {"databaseId": "GLOBAL", "pipelineId": "sys1", "pipelineName": "S", "isSystem": True,
              "enabled": True, "archived": False, "category": "SYSTEM - Preview",
              "executionConfig": LAMBDA_CONFIG, "systemConfig": {}}
PLAIN_ROW = {"databaseId": "GLOBAL", "pipelineId": "sys1", "pipelineName": "P", "enabled": True,
             "archived": False, "executionConfig": LAMBDA_CONFIG, "systemConfig": {}}


def _event(method, body=None, cross_call=None, path=PATH, params=PARAMS):
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


def _enforcer(obj=True):
    inst = MagicMock()
    inst.enforceAPI.return_value = True
    inst.enforce.return_value = obj
    return inst


def _table(item):
    table = MagicMock()
    table.get_item.return_value = {"Item": dict(item)} if item is not None else {}
    return table


def _message(resp):
    return json.loads(resp["body"])["message"]


@pytest.fixture(autouse=True)
def _quiet_side_paths():
    """The save-warning and Lambda-provisioning helpers read other tables / call Lambda; neither is
    under test here, so both are pinned to their pass-through results."""
    with patch(f"{MOD}._pipeline_save_warnings", return_value=[]), \
         patch(f"{MOD}._provision_lambda_for_pipeline", side_effect=lambda cfg, pid: cfg), \
         patch(f"{MOD}.find_pipeline_id_owner", return_value=None):
        yield


def _call(event, item, obj=True, tokens=("user1",)):
    table = _table(item)
    with patch(f"{MOD}.CasbinEnforcer", return_value=_enforcer(obj=obj)), \
         patch(f"{MOD}.request_to_claims", return_value={"tokens": list(tokens)}), \
         patch(f"{MOD}._pipeline_table", return_value=table):
        resp = lambda_handler(event, MagicMock())
    return resp, table


CREATE_BODY = {"databaseId": "GLOBAL", "pipelineId": "sys1", "pipelineName": "S",
               "category": "SYSTEM - Preview", "executionConfig": LAMBDA_CONFIG, "isSystem": True}


@pytest.mark.unit
class TestCrossingOnCreate:
    def test_an_api_caller_cannot_mint_a_system_pipeline(self):
        resp, table = _call(_event("POST", CREATE_BODY, path="/database/GLOBAL/pipelines",
                                   params={"databaseId": "GLOBAL"}), None)
        assert resp["statusCode"] == 200, resp["body"]
        assert table.put_item.call_args.kwargs["Item"]["isSystem"] is False
        assert _message(resp)["isSystem"] is False

    def test_the_importer_stores_true(self):
        resp, table = _call(_event("POST", CREATE_BODY, IMPORT_CALL, path="/database/GLOBAL/pipelines",
                                   params={"databaseId": "GLOBAL"}), None, tokens=("SYSTEM_USER",))
        assert resp["statusCode"] == 200, resp["body"]
        assert table.put_item.call_args.kwargs["Item"]["isSystem"] is True
        assert _message(resp)["isSystem"] is True

    def test_system_user_without_the_marker_stores_false(self):
        resp, table = _call(_event("POST", CREATE_BODY, UNMARKED_CALL, path="/database/GLOBAL/pipelines",
                                   params={"databaseId": "GLOBAL"}), None, tokens=("SYSTEM_USER",))
        assert resp["statusCode"] == 200, resp["body"]
        assert table.put_item.call_args.kwargs["Item"]["isSystem"] is False

    def test_the_importer_omitting_the_key_stores_false(self):
        body = {k: v for k, v in CREATE_BODY.items() if k != "isSystem"}
        resp, table = _call(_event("POST", body, IMPORT_CALL, path="/database/GLOBAL/pipelines",
                                   params={"databaseId": "GLOBAL"}), None, tokens=("SYSTEM_USER",))
        assert resp["statusCode"] == 200, resp["body"]
        assert table.put_item.call_args.kwargs["Item"]["isSystem"] is False


@pytest.mark.unit
class TestCrossingOnUpdate:
    def test_the_importer_promotes_a_pre_existing_row(self):
        body = {"pipelineName": "S", "enabled": True, "archived": False, "isSystem": True}
        resp, table = _call(_event("PUT", body, IMPORT_CALL), PLAIN_ROW, tokens=("SYSTEM_USER",))
        assert resp["statusCode"] == 200, resp["body"]
        assert table.put_item.call_args.kwargs["Item"]["isSystem"] is True
        assert _message(resp)["isSystem"] is True

    def test_an_api_caller_sending_isSystem_is_ignored(self):
        resp, table = _call(_event("PUT", {"enabled": False, "isSystem": True}), PLAIN_ROW)
        assert resp["statusCode"] == 200, resp["body"]
        saved = table.put_item.call_args.kwargs["Item"]
        assert saved["enabled"] is False
        assert saved.get("isSystem", False) is False

    def test_system_user_without_the_marker_cannot_promote(self):
        resp, table = _call(_event("PUT", {"enabled": True, "isSystem": True}, UNMARKED_CALL), PLAIN_ROW,
                            tokens=("SYSTEM_USER",))
        assert resp["statusCode"] == 200, resp["body"]
        assert table.put_item.call_args.kwargs["Item"].get("isSystem", False) is False

    def test_the_importer_leaves_the_flag_alone_when_it_omits_the_key(self):
        resp, table = _call(_event("PUT", {"enabled": True}, IMPORT_CALL), SYSTEM_ROW, tokens=("SYSTEM_USER",))
        assert resp["statusCode"] == 200, resp["body"]
        assert table.put_item.call_args.kwargs["Item"]["isSystem"] is True


@pytest.mark.unit
class TestSystemPipelineGuards:
    def test_enabled_alone_is_accepted_and_keeps_the_flag(self):
        resp, table = _call(_event("PUT", {"enabled": False}), SYSTEM_ROW)
        assert resp["statusCode"] == 200, resp["body"]
        saved = table.put_item.call_args.kwargs["Item"]
        assert saved["enabled"] is False and saved["isSystem"] is True

    @pytest.mark.parametrize("body", [
        {"description": "edited"},
        {"enabled": True, "pipelineName": "renamed"},
        {"enabled": True, "archived": False},
        {"archived": False},
        {"systemConfig": {"inputFileArity": "multi"}},
    ])
    def test_any_other_field_is_refused_with_the_readonly_message(self, body):
        resp, table = _call(_event("PUT", body), SYSTEM_ROW)
        assert resp["statusCode"] == 400
        assert _message(resp) == sr.SYSTEM_PIPELINE_READONLY_MESSAGE
        table.put_item.assert_not_called()

    def test_tier_2_still_runs_before_the_system_rule(self):
        resp, table = _call(_event("PUT", {"description": "edited"}), SYSTEM_ROW, obj=False)
        assert resp["statusCode"] == 403
        table.put_item.assert_not_called()

    def test_delete_is_refused(self):
        resp, table = _call(_event("DELETE"), SYSTEM_ROW)
        assert resp["statusCode"] == 400
        assert _message(resp) == sr.SYSTEM_PIPELINE_ARCHIVE_MESSAGE
        table.put_item.assert_not_called()

    def test_post_restore_of_an_archived_system_id_is_refused(self):
        archived = dict(SYSTEM_ROW, archived=True, enabled=False)
        body = {k: v for k, v in CREATE_BODY.items() if k != "isSystem"}
        resp, table = _call(_event("POST", body, path="/database/GLOBAL/pipelines",
                                   params={"databaseId": "GLOBAL"}), archived)
        assert resp["statusCode"] == 400
        assert _message(resp) == sr.SYSTEM_PIPELINE_ARCHIVE_MESSAGE
        table.put_item.assert_not_called()

    def test_a_non_system_row_is_untouched_by_the_rule(self):
        resp, table = _call(_event("PUT", {"description": "edited"}), PLAIN_ROW)
        assert resp["statusCode"] == 200, resp["body"]
        assert table.put_item.call_args.kwargs["Item"]["description"] == "edited"
        resp, table = _call(_event("DELETE"), PLAIN_ROW)
        assert resp["statusCode"] == 200


@pytest.mark.unit
class TestTheImporterIsExempt:
    def test_the_deploy_wins_update_body_is_accepted(self):
        body = {"pipelineName": "S", "category": "SYSTEM - Preview", "description": "shipped",
                "executionConfig": LAMBDA_CONFIG, "systemConfig": {"requireTemplate": False},
                "enabled": True, "archived": False, "isSystem": True}
        disabled = dict(SYSTEM_ROW, enabled=False)
        resp, table = _call(_event("PUT", body, IMPORT_CALL), disabled, tokens=("SYSTEM_USER",))
        assert resp["statusCode"] == 200, resp["body"]
        saved = table.put_item.call_args.kwargs["Item"]
        assert saved["enabled"] is True and saved["description"] == "shipped" and saved["isSystem"] is True

    def test_the_teardown_archive_is_accepted(self):
        resp, table = _call(_event("DELETE", cross_call=IMPORT_CALL), SYSTEM_ROW, tokens=("SYSTEM_USER",))
        assert resp["statusCode"] == 200, resp["body"]
        saved = table.put_item.call_args.kwargs["Item"]
        assert saved["archived"] is True and saved["isSystem"] is True

    def test_the_importer_restores_an_archived_system_row_in_place(self):
        archived = dict(SYSTEM_ROW, archived=True, enabled=False, dateCreated="2024-01-01T00:00:00Z",
                        createdBy="SYSTEM_USER")
        resp, table = _call(_event("POST", CREATE_BODY, IMPORT_CALL, path="/database/GLOBAL/pipelines",
                                   params={"databaseId": "GLOBAL"}), archived, tokens=("SYSTEM_USER",))
        assert resp["statusCode"] == 200, resp["body"]
        saved = table.put_item.call_args.kwargs["Item"]
        assert saved["isSystem"] is True and saved["archived"] is False
        assert saved["dateCreated"] == "2024-01-01T00:00:00Z"
