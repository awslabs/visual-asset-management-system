# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Concurrency tests for the workflow V2 CRUD handler's update and archive writes.

Update and archive both read the workflow row, mutate it in memory, and only then write — update
across a state-machine deployment that takes seconds. These tests stand a fake table in for the
workflow table that APPLIES writes to a stored row, so a second writer can land mid-request and the
test can observe which attributes survived.
"""

import json
from unittest.mock import MagicMock, patch

import botocore.exceptions
import pytest

from backend.backend.handlers.workflows.workflowService import lambda_handler

MOD = "backend.backend.handlers.workflows.workflowService"

PIPELINE_REC = {"databaseId": "db1", "pipelineId": "pipe1", "pipelineName": "P",
                "enabled": True, "archived": False, "systemConfig": {}}


def _event(method, path, path_params=None, body=None):
    return {
        "requestContext": {"http": {"method": method, "path": path}},
        "pathParameters": path_params,
        "queryStringParameters": None,
        "headers": {"authorization": "Bearer test-token"},
        "body": json.dumps(body) if body is not None else None,
    }


def _enforcer():
    inst = MagicMock()
    inst.enforceAPI.return_value = True
    inst.enforce.return_value = True
    return inst


def _real_to_update_expr(record, op="SET"):
    """The real `common.dynamodb.to_update_expr`.

    The handler binds `to_update_expr` at import time and `tests/conftest.py` registers
    `sys.modules['common.dynamodb']` as a `MagicMock`, so the bound name is a mock whose call yields
    nothing to unpack into three values. Patching the real logic in is what makes the write the
    handler issues the same one it issues in the deployed handler.
    """
    keys = record.keys()
    keys_attr_names = ["#f{n}".format(n=x) for x in range(len(keys))]
    values_attr_names = [":v{n}".format(n=x) for x in range(len(keys))]
    keys_map = {k: key for k, key in zip(keys_attr_names, keys)}
    values_map = {v1: record[v] for v, v1 in zip(keys, values_attr_names)}
    expr = "{op} ".format(op=op) + ", ".join(
        "{f} = {v}".format(f=f, v=v)
        for f, v in zip(keys_attr_names, values_attr_names))
    return keys_map, values_map, expr


@pytest.fixture(autouse=True)
def bind_real_to_update_expr():
    with patch(f"{MOD}.to_update_expr", _real_to_update_expr):
        yield


def _stored_row():
    return {
        "databaseId": "db1",
        "workflowId": "wflow1",
        "workflowName": "Original Name",
        "category": "sandbox",
        "description": "d",
        "enabled": True,
        "archived": False,
        "systemConfig": {},
        "specifiedPipelines": [{"pipelineDatabaseId": "db1", "pipelineId": "old-pipe",
                                "jobName": "", "defaultTemplateId": ""}],
        "jobNames": ["old-job-uuid"],
        "workflow_arn": "arn:aws:states:us-east-1:111122223333:stateMachine:old",
        "dateModified": "2026-01-01T00:00:00Z",
    }


def _conditional_check_failed(operation):
    return botocore.exceptions.ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException",
                   "Message": "The conditional request failed"}},
        operation)


class FakeWorkflowTable:
    """Applies writes to a single stored row so a test can observe what survived a concurrent write.

    `after_read` fires once the read has taken its snapshot, which is where a second writer's change
    lands: the handler is then holding a stale copy of the row for the rest of the request.
    """

    def __init__(self, row, after_read=None):
        self.row = row
        self.after_read = after_read
        self.put_items = []

    def get_item(self, Key):
        snapshot = dict(self.row) if self.row else None
        if self.after_read:
            self.after_read(self.row)
        return {"Item": snapshot} if snapshot else {}

    def update_item(self, Key, UpdateExpression, ExpressionAttributeNames,
                    ExpressionAttributeValues, ConditionExpression=None):
        # ConditionExpression is the existence guard; the row is the only item this stub holds.
        if not self.row:
            raise _conditional_check_failed("UpdateItem")
        for assignment in UpdateExpression.split("SET ", 1)[1].split(", "):
            name_ref, value_ref = [part.strip() for part in assignment.split(" = ")]
            self.row[ExpressionAttributeNames[name_ref]] = ExpressionAttributeValues[value_ref]

    def put_item(self, Item):
        self.put_items.append(Item)
        self.row.clear()
        self.row.update(Item)


def _written(table):
    """The attributes the last update_item call SET, as {attributeName: value}."""
    kwargs = table.update_item.call_args.kwargs
    names = kwargs["ExpressionAttributeNames"]
    values = kwargs["ExpressionAttributeValues"]
    written = {}
    for assignment in kwargs["UpdateExpression"].split("SET ", 1)[1].split(", "):
        name_ref, value_ref = [part.strip() for part in assignment.split(" = ")]
        written[names[name_ref]] = values[value_ref]
    return written


@pytest.mark.unit
class TestConcurrentWorkflowWrites:
    @patch(f"{MOD}.workflowAsl")
    @patch(f"{MOD}.ev")
    @patch(f"{MOD}._get_pipeline_record")
    @patch(f"{MOD}._workflow_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_an_archive_that_lands_during_the_deploy_is_not_erased_by_the_update(
            self, mock_enforcer, mock_claims, mock_table, mock_get_pipe, mock_ev, mock_asl):
        # deploy_state_machine takes seconds; a concurrent DELETE archives the row inside that window.
        # The update must not carry archived=False back from the snapshot it read before the deploy.
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        mock_get_pipe.return_value = PIPELINE_REC
        mock_ev.validate_workflow_save.return_value = ([], [])
        table = FakeWorkflowTable(_stored_row())
        mock_table.return_value = table

        def _deploy_then_a_concurrent_archive(*_args, **_kwargs):
            table.row["archived"] = True
            table.row["enabled"] = False
            table.row["modifiedBy"] = "other-user"
            return "arn:aws:states:us-east-1:111122223333:stateMachine:new", ["new-job-uuid"]

        mock_asl.deploy_state_machine.side_effect = _deploy_then_a_concurrent_archive

        resp = lambda_handler(
            _event("PUT", "/database/db1/workflows/wflow1",
                   {"databaseId": "db1", "workflowId": "wflow1"},
                   {"specifiedPipelines": [{"pipelineId": "pipe1"}]}), MagicMock())

        assert resp["statusCode"] == 200
        assert table.row["archived"] is True
        assert table.row["enabled"] is False
        # The update's own change still landed, and its redeployed job names went with it.
        assert [ref["pipelineId"] for ref in table.row["specifiedPipelines"]] == ["pipe1"]
        assert table.row["jobNames"] == ["new-job-uuid"]

    @patch(f"{MOD}._resolve_snapshot_pipeline_records", return_value=[])
    @patch(f"{MOD}._workflow_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_an_update_writes_only_the_attributes_the_request_changed(
            self, mock_enforcer, mock_claims, mock_table, mock_snapshot):
        # A description-only edit must not rewrite archived / enabled / specifiedPipelines / jobNames
        # from the snapshot it read — those are what a concurrent writer owns.
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        table = MagicMock()
        table.get_item.return_value = {"Item": _stored_row()}
        mock_table.return_value = table

        resp = lambda_handler(
            _event("PUT", "/database/db1/workflows/wflow1",
                   {"databaseId": "db1", "workflowId": "wflow1"},
                   {"description": "edited"}), MagicMock())

        assert resp["statusCode"] == 200
        assert set(_written(table)) == {"description", "dateModified", "modifiedBy"}
        table.put_item.assert_not_called()

    @patch(f"{MOD}._workflow_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_an_archive_does_not_revert_a_rename_that_landed_after_its_read(
            self, mock_enforcer, mock_claims, mock_table):
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()

        def _concurrent_rename(row):
            row["workflowName"] = "Renamed By Someone Else"

        table = FakeWorkflowTable(_stored_row(), after_read=_concurrent_rename)
        mock_table.return_value = table

        resp = lambda_handler(
            _event("DELETE", "/database/db1/workflows/wflow1",
                   {"databaseId": "db1", "workflowId": "wflow1"}), MagicMock())

        assert resp["statusCode"] == 200
        assert table.row["archived"] is True and table.row["enabled"] is False
        assert table.row["workflowName"] == "Renamed By Someone Else"

    @patch(f"{MOD}._workflow_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_a_row_removed_after_the_read_is_reported_missing_not_recreated(
            self, mock_enforcer, mock_claims, mock_table):
        # A targeted update_item creates the item when it is absent, so the write carries an existence
        # condition: the row is reported gone rather than resurrected from a stale snapshot.
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        table = FakeWorkflowTable(_stored_row(), after_read=lambda row: row.clear())
        mock_table.return_value = table

        resp = lambda_handler(
            _event("DELETE", "/database/db1/workflows/wflow1",
                   {"databaseId": "db1", "workflowId": "wflow1"}), MagicMock())

        assert resp["statusCode"] == 404
        assert table.row == {}
        assert table.put_items == []

    # ---- positive controls: the ordinary single-writer paths still work ----

    @patch(f"{MOD}._resolve_snapshot_pipeline_records", return_value=[])
    @patch(f"{MOD}._workflow_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_an_uncontended_update_persists_every_supplied_field(
            self, mock_enforcer, mock_claims, mock_table, mock_snapshot):
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        table = FakeWorkflowTable(_stored_row())
        mock_table.return_value = table

        resp = lambda_handler(
            _event("PUT", "/database/db1/workflows/wflow1",
                   {"databaseId": "db1", "workflowId": "wflow1"},
                   {"workflowName": "Renamed", "description": "edited", "enabled": False,
                    "subDashboardUrl": "https://example.com/d"}), MagicMock())

        assert resp["statusCode"] == 200
        assert table.row["workflowName"] == "Renamed"
        assert table.row["description"] == "edited"
        assert table.row["enabled"] is False
        assert table.row["subDashboardUrl"] == "https://example.com/d"
        assert table.row["modifiedBy"] == "user1"
        assert table.row["dateModified"] != "2026-01-01T00:00:00Z"
        # Untouched attributes are still there — a targeted write must not drop the rest of the row.
        assert table.row["jobNames"] == ["old-job-uuid"]
        assert table.row["archived"] is False
        body = json.loads(resp["body"])["message"]
        assert body["workflowName"] == "Renamed" and body["enabled"] is False

    @patch(f"{MOD}._workflow_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_an_uncontended_archive_marks_the_row_archived_and_disabled(
            self, mock_enforcer, mock_claims, mock_table):
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        table = FakeWorkflowTable(_stored_row())
        mock_table.return_value = table

        resp = lambda_handler(
            _event("DELETE", "/database/db1/workflows/wflow1",
                   {"databaseId": "db1", "workflowId": "wflow1"}), MagicMock())

        assert resp["statusCode"] == 200
        assert json.loads(resp["body"])["message"] == "Workflow archived"
        assert table.row["archived"] is True and table.row["enabled"] is False
        assert table.row["modifiedBy"] == "user1"
        # Everything the archive did not touch survives.
        assert table.row["workflowName"] == "Original Name"
        assert table.row["specifiedPipelines"][0]["pipelineId"] == "old-pipe"

    @patch(f"{MOD}._resolve_snapshot_pipeline_records", return_value=[])
    @patch(f"{MOD}._workflow_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_an_unarchive_still_clears_archived_and_re_enables(
            self, mock_enforcer, mock_claims, mock_table, mock_snapshot):
        # The CLI/MCP unarchive is a PUT carrying archived=False + enabled=True; both must be written
        # even though a targeted update writes only what the request supplies.
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        row = _stored_row()
        row["archived"] = True
        row["enabled"] = False
        table = FakeWorkflowTable(row)
        mock_table.return_value = table

        resp = lambda_handler(
            _event("PUT", "/database/db1/workflows/wflow1",
                   {"databaseId": "db1", "workflowId": "wflow1"},
                   {"archived": False, "enabled": True}), MagicMock())

        assert resp["statusCode"] == 200
        assert table.row["archived"] is False and table.row["enabled"] is True

    @patch(f"{MOD}.workflowAsl")
    @patch(f"{MOD}.ev")
    @patch(f"{MOD}._get_pipeline_record")
    @patch(f"{MOD}._workflow_table")
    @patch(f"{MOD}.request_to_claims")
    @patch(f"{MOD}.CasbinEnforcer")
    def test_a_pipeline_set_update_still_persists_the_redeployed_state_machine(
            self, mock_enforcer, mock_claims, mock_table, mock_get_pipe, mock_ev, mock_asl):
        mock_claims.return_value = {"tokens": ["user1"]}
        mock_enforcer.return_value = _enforcer()
        mock_get_pipe.return_value = PIPELINE_REC
        mock_ev.validate_workflow_save.return_value = ([], [])
        mock_asl.deploy_state_machine.return_value = (
            "arn:aws:states:us-east-1:111122223333:stateMachine:new", ["new-job-uuid"])
        table = FakeWorkflowTable(_stored_row())
        mock_table.return_value = table

        resp = lambda_handler(
            _event("PUT", "/database/db1/workflows/wflow1",
                   {"databaseId": "db1", "workflowId": "wflow1"},
                   {"specifiedPipelines": [{"pipelineId": "pipe1"}]}), MagicMock())

        assert resp["statusCode"] == 200
        assert table.row["workflow_arn"].endswith(":new")
        assert table.row["jobNames"] == ["new-job-uuid"]
        assert [ref["pipelineId"] for ref in table.row["specifiedPipelines"]] == ["pipe1"]
