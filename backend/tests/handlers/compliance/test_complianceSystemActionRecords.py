# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The compliance handlers that serve no API route record their system actions (D199).

The audit action registry lists every action a handler that serves no API route records
(`common/auditActions.py`, `CORE_AUDIT_SYSTEM_ACTIONS`), and every Lambda entrypoint under
`handlers/` opens the request audit context (`@audited_handler`). The compliance trigger, the
workflow callback, the cascade executor and the job Lambda change compliance data without an API
request: an automatic evaluation, the removal of a permanently deleted asset's record, a cascade
opened for approval, a pipeline rule's result, a cascade run and its evaluations, a database binding
change applied and a deleted database's data moved. Each now writes its action record, in the
invocation's audit context, with the ids it changed.
"""

import ast
import inspect
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend.tests.handlers.compliance.test_complianceCascadeExecutor import (
    CASCADE_ID, EXECUTING, Graph, _patched,
)
from handlers.compliance import complianceCascadeExecutor as executor
from handlers.compliance import complianceEvaluationStore as store
from handlers.compliance import complianceJobs as jobs
from handlers.compliance import complianceTrigger as trigger
from handlers.compliance import complianceWorkflowCallback as callback

AUDIT_EVENT = {"Records": [{"messageId": "m-1"}]}
NON_ROUTE_HANDLERS = (trigger, callback, executor, jobs)
DB, ASSET = "db1", "a1"
APPLY_ID = "5b1e2c3d-0000-4000-8000-0000000d1990"
RUN_ID = "5b1e2c3d-0000-4000-8000-0000000d1991"


@contextmanager
def _records(module):
    """The (event, action, fields) of each log_action call `module` makes, inside an audit context
    whose event is AUDIT_EVENT."""
    calls = []
    with patch.object(module, "current_audit_context", return_value=SimpleNamespace(event=AUDIT_EVENT)), \
            patch.object(module, "log_action",
                         side_effect=lambda event, action=None, **fields: calls.append(
                             (event, action, fields))):
        yield calls


def _actions(calls):
    return [action for _event, action, _fields in calls]


@pytest.mark.unit
class TestTheEntrypointsOpenTheAuditContext:

    @pytest.mark.parametrize("module", NON_ROUTE_HANDLERS, ids=lambda m: m.__name__.rsplit(".", 1)[-1])
    def test_the_entrypoint_is_an_audited_handler(self, module):
        # Read from the source: the test harness's audit mock leaves the decorator a no-op.
        tree = ast.parse(inspect.getsource(module))
        entrypoint, = [node for node in tree.body
                       if isinstance(node, ast.FunctionDef) and node.name == "lambda_handler"]
        assert [getattr(d, "id", None) for d in entrypoint.decorator_list] == ["audited_handler"]


@pytest.mark.unit
class TestTheTrigger:

    def test_an_automatic_evaluation_records_its_evaluation(self):
        result = {"evaluationId": "eval-d199", "verdict": "non_compliant",
                  "complianceState": "quarantined"}
        with _records(trigger) as calls, \
                patch.object(store, "run_evaluation", return_value=result), \
                patch.object(trigger, "check_and_trigger_cascade"):
            trigger.trigger_evaluation(DB, ASSET, "schema-1")
        assert calls == [(AUDIT_EVENT, "complianceAutoEvaluate", {
            "outcome": "success", "reason": None, "database_id": DB, "asset_id": ASSET,
            "evaluationId": "eval-d199", "schemaName": "schema-1",
            "changes": {"complianceState": "quarantined"},
            "details": {"verdict": "non_compliant", "trigger": "assetChange"}})]

    def test_an_evaluation_that_could_not_run_is_recorded_failed(self):
        with _records(trigger) as calls, \
                patch.object(store, "run_evaluation", return_value={"error": "schema missing"}), \
                patch.object(trigger, "check_and_trigger_cascade") as cascade:
            trigger.trigger_evaluation(DB, ASSET, "schema-1")
        (_event, action, fields), = calls
        assert (action, fields["outcome"], fields["reason"], fields["changes"]) == (
            "complianceAutoEvaluate", "failed", "internal", None)
        cascade.assert_not_called()

    def test_the_record_of_a_deleted_asset_removed_is_recorded(self):
        table = MagicMock(name="asset_state_table")
        table.delete_item.return_value = {"Attributes": {
            "databaseId": DB, "assetId": ASSET, "schemaSource": "database",
            "complianceState": "compliant", "schemaName": "schema-1"}}
        with _records(trigger) as calls, patch.object(trigger, "asset_state_table", table), \
                patch.object(store, "write_audit"):
            assert trigger._delete_record_of_deleted_asset(DB, ASSET, "sweep") is True
        (event, action, fields), = calls
        assert (event, action, fields["database_id"], fields["asset_id"]) == (
            AUDIT_EVENT, "complianceRecordRemove", DB, ASSET)
        assert fields["details"]["detectedBy"] == "sweep"
        assert fields["previous"] == {"complianceState": "compliant", "schemaName": "schema-1"}

    def test_no_record_is_written_when_there_was_no_record_to_remove(self):
        table = MagicMock(name="asset_state_table")
        table.delete_item.return_value = {}
        with _records(trigger) as calls, patch.object(trigger, "asset_state_table", table):
            assert trigger._delete_record_of_deleted_asset(DB, ASSET) is False
        assert calls == []

    def test_a_cascade_opened_for_approval_names_its_cascade(self):
        opened = []
        with _records(trigger) as calls, \
                patch.object(store, "get_child_links", return_value=[{"toAssetDatabaseId": DB,
                                                                      "toAssetId": "child"}]), \
                patch.object(store, "is_cascade_link", return_value=True), \
                patch.object(trigger, "pending_cascade_exists", return_value=False), \
                patch.object(trigger, "opened_details", return_value={"childCount": 1}), \
                patch.object(trigger, "_cascade_parent_name", return_value="Parent"), \
                patch.object(trigger, "_open_cascade_claiming_parent",
                             side_effect=lambda item: opened.append(item) or True), \
                patch.object(store, "write_audit"), \
                patch("handlers.compliance.complianceNotifications.notify_cascade_pending"):
            trigger.check_and_trigger_cascade(DB, ASSET)
        (event, action, fields), = calls
        assert (event, action) == (AUDIT_EVENT, "complianceCascadeAutoCreate")
        assert fields["cascadeId"] == opened[0]["cascadeId"]
        assert (fields["database_id"], fields["asset_id"]) == (DB, ASSET)

    def test_no_cascade_record_when_one_is_already_pending(self):
        with _records(trigger) as calls, \
                patch.object(store, "get_child_links", return_value=[{"toAssetId": "child"}]), \
                patch.object(store, "is_cascade_link", return_value=True), \
                patch.object(trigger, "pending_cascade_exists", return_value=True):
            trigger.check_and_trigger_cascade(DB, ASSET)
        assert calls == []


@pytest.mark.unit
class TestTheWorkflowCallback:

    EVALUATION = {"evaluationId": "eval-cb", "databaseId": DB, "assetId": ASSET,
                  "status": "pending_pipeline", "pipelineRulesPending": "[]"}

    def _process(self, status, outcome, removed=None):
        with _records(callback) as calls, \
                patch.object(store, "resolve_pipeline_execution",
                             return_value=(dict(self.EVALUATION), "rule-1")), \
                patch.object(store, "asset_removal_reason", return_value=removed), \
                patch.object(store, "close_evaluation_of_removed_asset"), \
                patch.object(callback, "read_execution_failure_reason", return_value="boom"), \
                patch.object(callback, "read_compliance_output", return_value=None), \
                patch.object(store, "complete_pipeline_rule", return_value=outcome):
            callback.process_completed_execution("exec-1", status, None, None)
        return calls

    def test_a_finalizing_rule_result_records_the_state_it_set(self):
        calls = self._process("FAILED", {"evaluationId": "eval-cb", "finalized": True,
                                         "verdict": "non_compliant", "complianceState": "quarantined"})
        (event, action, fields), = calls
        assert (event, action, fields["evaluationId"], fields["executionId"]) == (
            AUDIT_EVENT, "compliancePipelineRuleComplete", "eval-cb", "exec-1")
        assert fields["changes"] == {"complianceState": "quarantined"}
        assert fields["details"]["executionStatus"] == "FAILED"
        # The failure reason is read for the evaluation, never written to the action record.
        assert "boom" not in repr(fields)

    def test_a_rule_result_that_does_not_finalize_records_no_state(self):
        (_event, _action, fields), = self._process(
            "SUCCEEDED", {"evaluationId": "eval-cb", "finalized": False})
        assert fields["changes"] is None and fields["details"]["finalized"] is False

    def test_an_evaluation_closed_for_a_removed_asset_is_recorded(self):
        (_event, action, fields), = self._process("SUCCEEDED", {}, removed="deleted")
        assert action == "compliancePipelineRuleComplete" and fields["details"]["closed"] == "deleted"


@pytest.mark.unit
class TestTheCascadeExecutor:

    def _run(self, cascade_item):
        cascade_table, patches = _patched(Graph([("SRC", "B"), ("SRC", "C")]), cascade_item)
        started = [p.start() for p in patches]
        try:
            with _records(executor) as calls, \
                    patch("handlers.compliance.complianceNotifications.notify_cascade_completed"):
                result = executor.execute_cascade(CASCADE_ID)
        finally:
            for p in reversed(patches):
                p.stop()
        return result, calls, started

    def test_each_evaluation_and_the_run_are_recorded_for_the_approver(self):
        cascade = dict(EXECUTING, triggeredByAssetId="SRC", approvedBy="approver-1")
        result, calls, _ = self._run(cascade)
        assert result["status"] == "completed"
        assert _actions(calls) == ["complianceAutoEvaluate", "complianceAutoEvaluate",
                                   "complianceCascadeRun"]
        evaluations = [fields for _e, action, fields in calls if action == "complianceAutoEvaluate"]
        assert sorted(fields["evaluationId"] for fields in evaluations) == ["eval-B", "eval-C"]
        assert len(evaluations) == 2
        assert all(fields["details"]["cascadeId"] == CASCADE_ID
                   and fields["on_behalf_of"] == "approver-1" for fields in evaluations)
        (_event, _action, run), = [call for call in calls if call[1] == "complianceCascadeRun"]
        assert (run["cascadeId"], run["item_count"], run.get("outcome", "success")) == (
            CASCADE_ID, 2, "success")

    def test_a_run_that_raises_is_recorded_failed(self):
        with _records(executor) as calls, \
                patch.object(executor, "execute_cascade", side_effect=RuntimeError("boom")), \
                patch.object(executor, "abort_cascade"):
            result = executor.lambda_handler({"cascadeId": CASCADE_ID}, MagicMock())
        assert result["status"] == "aborted"
        (_event, action, fields), = calls
        assert (action, fields["outcome"], fields["cascadeId"]) == (
            "complianceCascadeRun", "failed", CASCADE_ID)


@pytest.mark.unit
class TestTheJobs:

    def test_a_binding_change_run_is_recorded_with_its_apply(self):
        with _records(jobs) as calls, patch.object(jobs.binding_apply, "run", return_value=True):
            assert jobs.run_binding_apply(DB, APPLY_ID, RUN_ID, None) is True
        (event, action, fields), = calls
        assert (event, action, fields["database_id"]) == (AUDIT_EVENT, "complianceBindingApply", DB)
        assert fields["details"] == {"applyId": APPLY_ID, "runId": RUN_ID, "done": True}

    def test_a_binding_change_run_that_raises_is_recorded_failed(self):
        with _records(jobs) as calls, \
                patch.object(jobs.binding_apply, "run", side_effect=RuntimeError("boom")):
            assert jobs.run_binding_apply(DB, APPLY_ID, RUN_ID, None) is False
        (_event, action, fields), = calls
        assert (action, fields["outcome"]) == ("complianceBindingApply", "failed")

    def test_a_deleted_databases_move_is_recorded(self):
        with _records(jobs) as calls, patch.object(jobs, "retirement_tables"), \
                patch.object(jobs.databaseRetirement, "begin_run",
                             return_value=(jobs.databaseRetirement.RUN_GO, {})), \
                patch.object(jobs.databaseRetirement, "run", return_value=True):
            assert jobs.run_database_move(DB, None) is True
        (_event, action, fields), = calls
        assert (action, fields["database_id"], fields["details"]["done"]) == (
            "complianceDatabaseRetire", DB, True)
        assert fields["details"]["movedTo"] == f"{DB}#deleted"
