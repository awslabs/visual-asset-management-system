# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""System workflows: how `isSystem` crosses from the importer into the row, and what the row then
refuses (spec 4.1-4.2). Three arms per write path for the crossing; PUT accepts only `enabled`; DELETE
and the PUT restore are refused; the importer's marked cross-call is exempt; Tier-2 runs first.

Update and archive write a targeted SET through to_update_expr, which the root conftest replaces with
a MagicMock, so the real expression builder is bound in and the written attributes are read back from
the expression — the same approach as test_workflowService.py.

The archive route also deletes the workflow's trigger rows after its own write. The DELETE refusal is
asserted to answer before either — the trigger table is neither queried nor written — and the importer's
teardown is the positive control that, past the guard, the deletion is reached."""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.common.workflows import systemRecords as sr
from backend.backend.handlers.workflows.workflowService import lambda_handler
from backend.backend.models.workflows import SpecifiedPipelineInput

MOD = "backend.backend.handlers.workflows.workflowService"

IMPORT_CALL = {"userName": "SYSTEM_USER", "source": "vamsSchemaImport"}
UNMARKED_CALL = {"userName": "SYSTEM_USER"}
PATH = "/database/GLOBAL/workflows/sysw"
PARAMS = {"databaseId": "GLOBAL", "workflowId": "sysw"}

SYSTEM_ROW = {"databaseId": "GLOBAL", "workflowId": "sysw", "workflowName": "S", "isSystem": True,
              "enabled": True, "archived": False, "systemConfig": {}, "specifiedPipelines": []}
PLAIN_ROW = {"databaseId": "GLOBAL", "workflowId": "sysw", "workflowName": "W", "enabled": True,
             "archived": False, "systemConfig": {}, "specifiedPipelines": []}
PIPELINE_REC = {"databaseId": "GLOBAL", "pipelineId": "pipe1", "pipelineName": "P",
                "enabled": True, "archived": False, "systemConfig": {}}
CREATE_BODY = {"databaseId": "GLOBAL", "workflowId": "sysw", "workflowName": "S",
               "category": "SYSTEM - Preview",
               "specifiedPipelines": [{"pipelineId": "pipe1", "pipelineDatabaseId": "GLOBAL"}],
               "isSystem": True}


def _real_to_update_expr(record, op="SET"):
    keys = record.keys()
    keys_attr_names = ["#f{n}".format(n=x) for x in range(len(keys))]
    values_attr_names = [":v{n}".format(n=x) for x in range(len(keys))]
    keys_map = {k: key for k, key in zip(keys_attr_names, keys)}
    values_map = {v1: record[v] for v, v1 in zip(keys, values_attr_names)}
    expr = "{op} ".format(op=op) + ", ".join(
        "{f} = {v}".format(f=f, v=v) for f, v in zip(keys_attr_names, values_attr_names))
    return keys_map, values_map, expr


@pytest.fixture(autouse=True)
def bind_real_to_update_expr():
    with patch(f"{MOD}.to_update_expr", _real_to_update_expr):
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


def _message(resp):
    return json.loads(resp["body"])["message"]


def _call(event, item, obj=True, tokens=("user1",), triggers=None):
    table = MagicMock()
    table.get_item.return_value = {"Item": dict(item)} if item is not None else {}
    # The archive route deletes the workflow's trigger rows after its own write; the triggers table
    # answers an empty partition unless a test hands in its own mock to observe those calls.
    if triggers is None:
        triggers = MagicMock()
        triggers.query.return_value = {"Items": []}
    asl = MagicMock()
    asl.deploy_state_machine.return_value = ("arn:test:states:sm", ["job1"])
    resolved = (None, [(SpecifiedPipelineInput(pipelineId="pipe1", pipelineDatabaseId="GLOBAL"),
                        dict(PIPELINE_REC))])
    with patch(f"{MOD}.CasbinEnforcer", return_value=_enforcer(obj=obj)), \
         patch(f"{MOD}.request_to_claims", return_value={"tokens": list(tokens)}), \
         patch(f"{MOD}._workflow_table", return_value=table), \
         patch(f"{MOD}._triggers_table", return_value=triggers), \
         patch(f"{MOD}.find_workflow_id_owner", return_value=None), \
         patch(f"{MOD}._resolve_referenced_pipelines", return_value=resolved), \
         patch(f"{MOD}._resolve_snapshot_pipeline_records", return_value=[dict(PIPELINE_REC)]), \
         patch(f"{MOD}._save_validation", return_value=([], [])), \
         patch(f"{MOD}.workflowAsl", asl):
        resp = lambda_handler(event, MagicMock())
    return resp, table


@pytest.mark.unit
class TestCrossingOnCreate:
    def test_an_api_caller_cannot_mint_a_system_workflow(self):
        resp, table = _call(_event("POST", CREATE_BODY, path="/database/GLOBAL/workflows",
                                   params={"databaseId": "GLOBAL"}), None)
        assert resp["statusCode"] == 200, resp["body"]
        assert table.put_item.call_args.kwargs["Item"]["isSystem"] is False
        assert _message(resp)["isSystem"] is False

    def test_the_importer_stores_true(self):
        resp, table = _call(_event("POST", CREATE_BODY, IMPORT_CALL, path="/database/GLOBAL/workflows",
                                   params={"databaseId": "GLOBAL"}), None, tokens=("SYSTEM_USER",))
        assert resp["statusCode"] == 200, resp["body"]
        assert table.put_item.call_args.kwargs["Item"]["isSystem"] is True
        assert _message(resp)["isSystem"] is True

    def test_system_user_without_the_marker_stores_false(self):
        resp, table = _call(_event("POST", CREATE_BODY, UNMARKED_CALL, path="/database/GLOBAL/workflows",
                                   params={"databaseId": "GLOBAL"}), None, tokens=("SYSTEM_USER",))
        assert resp["statusCode"] == 200, resp["body"]
        assert table.put_item.call_args.kwargs["Item"]["isSystem"] is False


@pytest.mark.unit
class TestCrossingOnUpdate:
    def test_the_importer_promotes_a_pre_existing_row(self):
        body = {"workflowName": "S", "enabled": True, "archived": False, "isSystem": True}
        resp, table = _call(_event("PUT", body, IMPORT_CALL), PLAIN_ROW, tokens=("SYSTEM_USER",))
        assert resp["statusCode"] == 200, resp["body"]
        assert _written(table)["isSystem"] is True
        assert _message(resp)["isSystem"] is True

    def test_an_api_caller_sending_isSystem_is_ignored(self):
        resp, table = _call(_event("PUT", {"enabled": False, "isSystem": True}), PLAIN_ROW)
        assert resp["statusCode"] == 200, resp["body"]
        written = _written(table)
        assert written["enabled"] is False and "isSystem" not in written

    def test_system_user_without_the_marker_cannot_promote(self):
        resp, table = _call(_event("PUT", {"enabled": True, "isSystem": True}, UNMARKED_CALL), PLAIN_ROW,
                            tokens=("SYSTEM_USER",))
        assert resp["statusCode"] == 200, resp["body"]
        assert "isSystem" not in _written(table)


@pytest.mark.unit
class TestSystemWorkflowGuards:
    def test_enabled_alone_is_accepted(self):
        resp, table = _call(_event("PUT", {"enabled": False}), SYSTEM_ROW)
        assert resp["statusCode"] == 200, resp["body"]
        assert _written(table)["enabled"] is False
        assert _message(resp)["isSystem"] is True

    @pytest.mark.parametrize("body", [
        {"description": "edited"},
        {"enabled": True, "workflowName": "renamed"},
        {"enabled": True, "archived": False},
        {"archived": False},
        {"specifiedPipelines": [{"pipelineId": "pipe1", "pipelineDatabaseId": "GLOBAL"}]},
    ])
    def test_any_other_field_is_refused_with_the_readonly_message(self, body):
        resp, table = _call(_event("PUT", body), SYSTEM_ROW)
        assert resp["statusCode"] == 400
        assert _message(resp) == sr.SYSTEM_WORKFLOW_READONLY_MESSAGE
        table.update_item.assert_not_called()

    def test_tier_2_still_runs_before_the_system_rule(self):
        resp, table = _call(_event("PUT", {"description": "edited"}), SYSTEM_ROW, obj=False)
        assert resp["statusCode"] == 403
        table.update_item.assert_not_called()

    def test_delete_is_refused(self):
        resp, table = _call(_event("DELETE"), SYSTEM_ROW)
        assert resp["statusCode"] == 400
        assert _message(resp) == sr.SYSTEM_WORKFLOW_ARCHIVE_MESSAGE
        table.update_item.assert_not_called()

    def test_delete_is_refused_before_any_trigger_row_is_touched(self):
        # The archive route deletes the workflow's trigger rows after its own write; the system rule
        # answers before either, so the stored triggers are neither read nor deleted.
        triggers = MagicMock()
        triggers.query.return_value = {"Items": [{"triggerType": "fileUpload"}]}
        resp, table = _call(_event("DELETE"), SYSTEM_ROW, triggers=triggers)
        assert resp["statusCode"] == 400
        assert _message(resp) == sr.SYSTEM_WORKFLOW_ARCHIVE_MESSAGE
        table.update_item.assert_not_called()
        triggers.query.assert_not_called()
        triggers.delete_item.assert_not_called()

    def test_a_non_system_row_is_untouched_by_the_rule(self):
        resp, table = _call(_event("PUT", {"description": "edited"}), PLAIN_ROW)
        assert resp["statusCode"] == 200, resp["body"]
        assert _written(table)["description"] == "edited"
        resp, table = _call(_event("DELETE"), PLAIN_ROW)
        assert resp["statusCode"] == 200
        assert _written(table)["archived"] is True


@pytest.mark.unit
class TestTheImporterIsExempt:
    def test_the_deploy_wins_update_body_is_accepted(self):
        body = {"workflowName": "S", "category": "SYSTEM - Preview", "description": "shipped",
                "specifiedPipelines": [{"pipelineId": "pipe1", "pipelineDatabaseId": "GLOBAL"}],
                "subDashboardUrl": "", "enabled": True, "systemConfig": {}, "archived": False,
                "isSystem": True}
        disabled = dict(SYSTEM_ROW, enabled=False, archived=True)
        resp, table = _call(_event("PUT", body, IMPORT_CALL), disabled, tokens=("SYSTEM_USER",))
        assert resp["statusCode"] == 200, resp["body"]
        written = _written(table)
        assert written["enabled"] is True and written["archived"] is False
        assert written["description"] == "shipped" and written["isSystem"] is True

    def test_the_teardown_archive_is_accepted_and_reaches_the_trigger_rows(self):
        triggers = MagicMock()
        triggers.query.return_value = {"Items": [{"triggerType": "fileUpload"}]}
        resp, table = _call(_event("DELETE", cross_call=IMPORT_CALL), SYSTEM_ROW,
                            tokens=("SYSTEM_USER",), triggers=triggers)
        assert resp["statusCode"] == 200, resp["body"]
        written = _written(table)
        assert written["archived"] is True and written["enabled"] is False
        # The positive control for the refusal above: past the guard, the archive deletes the triggers.
        triggers.delete_item.assert_called_once()
