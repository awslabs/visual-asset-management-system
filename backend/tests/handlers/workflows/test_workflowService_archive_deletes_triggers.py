# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Archiving a workflow deletes its trigger rows.

`archive_workflow` sets archived/enabled on the workflow row; the trigger rows live in their own table,
keyed by workflow, and the upload dispatcher reads them without consulting the workflow row, so a row that
outlives its workflow keeps matching uploads and each match costs a launch the execute handler refuses.
The archive therefore deletes the workflow's triggers -- AFTER its own write succeeds, so a refused or
failed archive leaves the triggers exactly as they were -- and audits each deletion the way the trigger
service's DELETE route does.
"""

import importlib.util
import os
import pathlib
from unittest.mock import MagicMock, patch

import botocore.exceptions
import pytest

from backend.backend.handlers.workflows import workflowService as ws
from backend.tests.pagingStub import Pager

MOD = "backend.backend.handlers.workflows.workflowService"

# The root conftest replaces `common.dynamodb` with a MagicMock whose to_update_expr yields nothing to
# unpack into three values; the real helper is bound in so the archive write builds a real expression.
_real_ddb_spec = importlib.util.spec_from_file_location(
    "_real_common_dynamodb_for_archive_triggers_test",
    os.fspath(pathlib.Path(ws.__file__).parents[2] / "common" / "dynamodb.py"))
_real_ddb = importlib.util.module_from_spec(_real_ddb_spec)
_real_ddb_spec.loader.exec_module(_real_ddb)

COMPOSITE = "db1:wflow1"
ROWS = [
    {"workflowDatabaseId:workflowId": COMPOSITE, "triggerType": "fileUpload"},
    {"workflowDatabaseId:workflowId": COMPOSITE, "triggerType": "fileUpload#nightly"},
]
WF = {"databaseId": "db1", "workflowId": "wflow1", "workflowName": "W", "enabled": True}


def _event():
    return {"requestContext": {"http": {"method": "DELETE", "path": "/database/db1/workflows/wflow1"}},
            "pathParameters": {"databaseId": "db1", "workflowId": "wflow1"},
            "queryStringParameters": None, "headers": {"authorization": "Bearer t"}, "body": None}


def _enforcer():
    inst = MagicMock()
    inst.enforceAPI.return_value = True
    inst.enforce.return_value = True
    return inst


def _single_page():
    return Pager({"Items": list(ROWS)}, name="triggers")


def _archive(workflow_item, trigger_query, delete_errors=None, update_error=None):
    """Run the DELETE route. ``trigger_query`` answers the triggers table's query; ``delete_errors``
    maps a triggerType to the exception its delete raises; ``update_error`` is raised by the archive
    write. Returns (response, workflow table, triggers table, audit mock, ordered write log)."""
    order = []
    workflow_table = MagicMock()
    workflow_table.get_item.return_value = {"Item": workflow_item} if workflow_item else {}

    def _update(**_kwargs):
        order.append("archive")
        if update_error:
            raise update_error
        return {}

    workflow_table.update_item.side_effect = _update
    triggers_table = MagicMock()
    triggers_table.query.side_effect = trigger_query

    def _delete(**kwargs):
        trigger_type = kwargs["Key"]["triggerType"]
        order.append(("delete", trigger_type))
        if delete_errors and trigger_type in delete_errors:
            raise delete_errors[trigger_type]
        return {}

    triggers_table.delete_item.side_effect = _delete
    audit = MagicMock()
    with patch(f"{MOD}.to_update_expr", _real_ddb.to_update_expr), \
         patch(f"{MOD}._workflow_table", return_value=workflow_table), \
         patch(f"{MOD}._triggers_table", return_value=triggers_table), \
         patch(f"{MOD}.log_actions", audit), \
         patch(f"{MOD}.request_to_claims", return_value={"tokens": ["user1"]}), \
         patch(f"{MOD}.CasbinEnforcer", return_value=_enforcer()):
        resp = ws.lambda_handler(_event(), MagicMock())
    return resp, workflow_table, triggers_table, audit, order


def _deleted(order):
    return [key for step in order if isinstance(step, tuple) for _name, key in [step]]


def _audits(audit, secondary_type):
    return [c for c in audit.call_args_list if c.args[1] == secondary_type]


@pytest.mark.unit
class TestArchiveDeletesTriggers:
    def test_every_trigger_row_of_the_workflow_is_deleted(self):
        resp, _wf, triggers, _audit, order = _archive(WF, _single_page())
        assert resp["statusCode"] == 200
        assert _deleted(order) == ["fileUpload", "fileUpload#nightly"]
        assert {c.kwargs["Key"]["workflowDatabaseId:workflowId"]
                for c in triggers.delete_item.call_args_list} == {COMPOSITE}

    def test_trigger_rows_are_deleted_only_after_the_archive_write(self):
        _resp, _wf, _triggers, _audit, order = _archive(WF, _single_page())
        assert order[0] == "archive"
        assert all(isinstance(step, tuple) for step in order[1:]) and len(order) == 3

    def test_each_deletion_is_audited_like_the_trigger_service_route(self):
        _resp, _wf, _triggers, audit, _order = _archive(WF, _single_page())
        trigger_audits = _audits(audit, "workflowTriggerDelete")
        assert [c.args[2]["triggerType"] for c in trigger_audits] == ["fileUpload", "fileUpload#nightly"]
        for c in trigger_audits:
            assert c.args[2]["databaseId"] == "db1" and c.args[2]["workflowId"] == "wflow1"
            assert c.args[2]["operation"] == "delete"
        archive_audits = _audits(audit, "workflowArchive")
        assert len(archive_audits) == 1
        assert archive_audits[0].args[2]["triggersDeleted"] == 2

    def test_the_trigger_partition_is_read_to_exhaustion(self):
        pager = Pager({"Items": [ROWS[0]], "LastEvaluatedKey": {"triggerType": "fileUpload"}},
                      {"Items": [ROWS[1]]}, name="triggers")
        _resp, _wf, _triggers, _audit, order = _archive(WF, pager)
        pager.assert_paged_to_exhaustion()
        assert _deleted(order) == ["fileUpload", "fileUpload#nightly"]

    def test_a_missing_workflow_deletes_no_triggers(self):
        resp, _wf, triggers, _audit, _order = _archive(None, _single_page())
        assert resp["statusCode"] == 404
        triggers.query.assert_not_called()
        triggers.delete_item.assert_not_called()

    def test_an_archive_write_that_finds_no_row_deletes_no_triggers(self):
        gone = botocore.exceptions.ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
        resp, _wf, triggers, _audit, _order = _archive(WF, _single_page(), update_error=gone)
        assert resp["statusCode"] == 404
        triggers.delete_item.assert_not_called()

    def test_a_failed_trigger_delete_does_not_fail_the_archive(self):
        resp, _wf, _triggers, audit, order = _archive(
            WF, _single_page(), delete_errors={"fileUpload": RuntimeError("throttled")})
        assert resp["statusCode"] == 200
        assert _deleted(order) == ["fileUpload", "fileUpload#nightly"]
        assert [c.args[2]["triggerType"] for c in _audits(audit, "workflowTriggerDelete")] == ["fileUpload#nightly"]
        assert _audits(audit, "workflowArchive")[0].args[2]["triggersDeleted"] == 1

    def test_a_failed_trigger_listing_does_not_fail_the_archive(self):
        """The archive has already landed when the partition is read, so a throttled or failing query
        is logged and counted as zero deletions rather than turning the DELETE into a 500."""
        resp, _wf, triggers, audit, order = _archive(WF, RuntimeError("throttled"))
        assert resp["statusCode"] == 200
        assert order == ["archive"]
        triggers.delete_item.assert_not_called()
        assert _audits(audit, "workflowTriggerDelete") == []
        assert _audits(audit, "workflowArchive")[0].args[2]["triggersDeleted"] == 0

    def test_the_archive_write_still_sets_archived_and_disabled(self):
        _resp, wf, _triggers, _audit, _order = _archive(WF, _single_page())
        kwargs = wf.update_item.call_args.kwargs
        names, values = kwargs["ExpressionAttributeNames"], kwargs["ExpressionAttributeValues"]
        written = {}
        for assignment in kwargs["UpdateExpression"].split("SET ", 1)[1].split(", "):
            name_ref, value_ref = [part.strip() for part in assignment.split(" = ")]
            written[names[name_ref]] = values[value_ref]
        assert written["archived"] is True and written["enabled"] is False
