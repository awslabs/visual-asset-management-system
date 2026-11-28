# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The fileUpload dispatcher skips a trigger whose workflow cannot run.

A trigger row outlives its workflow: archiving or disabling a workflow leaves its rows in the triggers
table, and the matcher reads only the trigger row. Every matching upload then costs a synchronous
executeWorkflow invoke that ends 400 ("Workflow is archived" / "is disabled") -- one WARNING line, no
execution, on every upload. The dispatcher already reads the workflow row for systemConfig, so the same
read decides whether to invoke at all.

The read-failure arm keeps today's behaviour on purpose: when the row cannot be read the dispatcher does
not know, and the execute handler stays the authority.
"""

import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("WORKFLOW_TRIGGERS_STORAGE_TABLE_NAME", "t-triggers")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "t-assets")
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "t-buckets")
os.environ.setdefault("EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME", "t-execv2")

if "common.workflows.stepfunctions_builder" not in sys.modules:
    _stub = types.ModuleType("common.workflows.stepfunctions_builder")
    _stub.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _stub

from backend.backend.handlers.workflows.sfn import workflowTriggerDispatch as wd  # noqa: E402

DMOD = "backend.backend.handlers.workflows.sfn.workflowTriggerDispatch"

TRIGGER = {"triggerType": "fileUpload", "triggerBaseType": "fileUpload",
           "workflowDatabaseId": "GLOBAL", "workflowId": "wfG", "enabled": True,
           "triggerConfig": {"inputFileFilters": {"allow": [".glb"]}, "defaultTemplateIds": {}}}

RESOLVED = ("db1", "a1", "/model.glb", "", "")


def _dispatch(get_item):
    """Dispatch one upload against TRIGGER with the workflow-row read answering as ``get_item`` -- a
    response dict, or an exception instance to raise. Returns (launched, invoke mock, get_item mock)."""
    wd._workflow_row_cache.clear()
    read = ({"side_effect": get_item} if isinstance(get_item, BaseException)
            else {"return_value": get_item})
    with patch(f"{DMOD}._resolve_asset_relative_key", return_value=RESOLVED), \
         patch.object(wd.workflow_storage_table_v2, "get_item", **read) as m_get, \
         patch(f"{DMOD}._invoke_execute", return_value=True) as m_invoke:
        launched = wd._dispatch_uploaded_file("b1", "a1/model.glb", [TRIGGER])
    return launched, m_invoke, m_get


@pytest.mark.unit
class TestInactiveWorkflowsAreSkipped:
    def test_a_live_workflow_is_launched(self):
        """Positive control: every skip below is only meaningful if this arm invokes."""
        launched, m_invoke, _ = _dispatch({"Item": {"enabled": True, "archived": False, "systemConfig": {}}})
        assert launched == 1
        m_invoke.assert_called_once()

    def test_an_archived_workflow_is_skipped(self):
        launched, m_invoke, _ = _dispatch({"Item": {"archived": True, "enabled": False, "systemConfig": {}}})
        assert launched == 0
        m_invoke.assert_not_called()

    def test_a_disabled_workflow_is_skipped(self):
        launched, m_invoke, _ = _dispatch({"Item": {"archived": False, "enabled": False, "systemConfig": {}}})
        assert launched == 0
        m_invoke.assert_not_called()

    def test_a_row_without_an_enabled_flag_is_treated_as_enabled(self):
        launched, _m_invoke, _ = _dispatch({"Item": {"systemConfig": {"inputFileArity": "one"}}})
        assert launched == 1

    def test_a_missing_workflow_row_is_skipped(self):
        launched, m_invoke, _ = _dispatch({})
        assert launched == 0
        m_invoke.assert_not_called()

    def test_an_unreadable_row_keeps_the_launch(self):
        launched, m_invoke, _ = _dispatch(RuntimeError("throttled"))
        assert launched == 1
        m_invoke.assert_called_once()

    def test_the_gate_reuses_the_systemconfig_read(self):
        """One get_item serves the arity, the chaining flag and the gate for a workflow."""
        _launched, _m_invoke, m_get = _dispatch(
            {"Item": {"enabled": True, "systemConfig": {"inputFileArity": "one"}}})
        assert m_get.call_count == 1

    def test_the_row_memo_is_cleared_per_invocation(self):
        wd._workflow_row_cache[("GLOBAL", "stale")] = {"archived": True}
        with patch(f"{DMOD}._list_fileupload_triggers", return_value=[]):
            wd.lambda_handler({"Records": []}, MagicMock())
        assert wd._workflow_row_cache == {}

    def test_system_config_still_resolves_from_the_row(self):
        wd._workflow_row_cache.clear()
        row = {"Item": {"systemConfig": {"inputFileArity": "none", "allowWorkflowTriggerChaining": True}}}
        with patch.object(wd.workflow_storage_table_v2, "get_item", return_value=row):
            assert wd._workflow_input_file_arity("GLOBAL", "wf1") == "none"
            assert wd._workflow_allows_trigger_chaining("GLOBAL", "wf1") is True

    def test_system_config_of_an_unreadable_row_is_empty(self):
        wd._workflow_row_cache.clear()
        with patch.object(wd.workflow_storage_table_v2, "get_item", side_effect=RuntimeError("throttled")):
            assert wd._workflow_system_config("GLOBAL", "wf1") == {}
