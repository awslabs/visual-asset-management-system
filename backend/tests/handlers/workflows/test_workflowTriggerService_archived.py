# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A trigger cannot be set on an archived workflow.

Archiving deletes a workflow's triggers and the trigger dispatcher skips an archived workflow, so a
trigger stored on one would never fire. The PUT is refused with a 400 and nothing is written; a
DELETE on the same workflow still works, and the schema importer, which restores a built-in workflow
before it re-asserts its triggers, is not refused."""

from unittest.mock import patch

import pytest

from backend.backend.handlers.workflows import workflowTriggerService as svc
from tests.handlers.workflows.test_workflowTriggerService_isSystem import (
    IMPORT_CALL,
    PLAIN_WF,
    STORED_CONFIG,
    TPARAMS,
    _call,
    _event,
    _message,
)

ARCHIVED_WF = dict(PLAIN_WF, archived=True)
BODY = {"enabled": True, "inputFileFilters": STORED_CONFIG["inputFileFilters"]}


@pytest.mark.unit
class TestArchivedWorkflowTriggers:
    def test_setting_a_trigger_is_refused(self):
        resp, table = _call(_event("PUT", TPARAMS, BODY), ARCHIVED_WF)

        assert resp["statusCode"] == 400
        assert _message(resp) == svc.ARCHIVED_WORKFLOW_TRIGGER_MESSAGE
        table.put_item.assert_not_called()

    def test_the_refusal_does_not_echo_the_request(self):
        resp, _ = _call(_event("PUT", TPARAMS, BODY), ARCHIVED_WF)

        assert "sysw" not in _message(resp)
        assert "GLOBAL" not in _message(resp)

    def test_a_workflow_that_is_not_archived_still_takes_a_trigger(self):
        resp, table = _call(_event("PUT", TPARAMS, BODY), dict(PLAIN_WF, archived=False))

        assert resp["statusCode"] == 200, resp["body"]
        table.put_item.assert_called_once()

    def test_removing_a_trigger_stays_allowed(self):
        with patch.object(svc, "delete_trigger", return_value={"statusCode": 200, "body": "{}"}) as delete:
            resp, _ = _call(_event("DELETE", TPARAMS), ARCHIVED_WF)

        assert resp["statusCode"] == 200
        delete.assert_called_once()

    def test_the_schema_importer_is_not_refused(self):
        event = _event("PUT", TPARAMS, BODY, cross_call=IMPORT_CALL)

        resp, table = _call(event, ARCHIVED_WF)

        assert resp["statusCode"] == 200, resp["body"]
        table.put_item.assert_called_once()
