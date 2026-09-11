# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Two races on an execution record, and the Deadline Cloud arm of the abort.

S2-BACKEND-105 — the read paths reconcile a still-running execution's status by READING the row,
polling Step Functions, and writing back. The write named every reconcilable attribute and took their
values from the read-time snapshot, with no condition. The end-state lambda finishes the execution
from INSIDE the state machine, so describe_execution legitimately still reports RUNNING while it
writes — and the reconcile then pushed the pre-completion snapshot back over it, reverting the status
and blanking the executionLog/executionError the completing writer captured once, at completion.

S2-BACKEND-044 — abort read the pipeline rows once, stopped what it found, marked the rows ABORTED,
and only then stopped the parent state machine. Registration arrives asynchronously (the pipeline
emits an EventBridge event that registerPipelineExecution writes), so a job submitted just before the
abort could land on the row afterwards — and a row already stamped ABORTED is no longer a candidate
for this API, leaving the job running with no in-product remedy.

S2-BACKEND-045 — a registered Deadline Cloud job matched no abortable resource type, so it fell to the
"not yet abortable" arm. Its task runs through createJob.waitForTaskToken, so Step Functions owns the
token and not the job: stopping the execution abandons the token and leaves the farm rendering.
"""

import json
import os
from datetime import datetime, timedelta, timezone

import pytest
from unittest.mock import MagicMock, patch

os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "t-assets")
os.environ.setdefault("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "t-exec-v2")
os.environ.setdefault("WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME", "t-wf-inputs")
os.environ.setdefault("PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME", "t-pexec")
os.environ.setdefault("WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME", "t-wf-cfg")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_FILES_STORAGE_TABLE_NAME", "t-pin-files")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME", "t-pin-md")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME", "t-pin-cfg")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME", "t-of")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE_NAME", "t-om")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE_NAME", "t-or")
os.environ.setdefault("PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME", "t-logs")
os.environ.setdefault("WORKFLOW_STORAGE_TABLE_NAME", "t-workflows")
os.environ.setdefault("PIPELINE_STORAGE_TABLE_NAME", "t-pipelines")
os.environ.setdefault("EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME", "t-execv2")

from backend.backend.handlers.workflows import executionService as le  # noqa: E402

MOD = "backend.backend.handlers.workflows.executionService"

DB, ASSET = "db", "A"
EXEC_ID = "e1000000000000000000000000000001"
COMPOSITE = "wf-db:wf"


def _recent_iso(minutes_ago=5):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _running_main_row(**overrides):
    """A main row as launch writes it: every reconcilable attribute present, log and error empty."""
    row = {
        "workflowExecutionId": EXEC_ID,
        "workflowDatabaseId:workflowId": COMPOSITE,
        "workflowDatabaseId": "wf-db", "workflowId": "wf",
        "workflow_execution_arn": "arn:ex",
        "executionStatus": "RUNNING",
        "executionStartDate": _recent_iso(30),
        "executionStopDate": "",
        "executionError": "",
        "executionLog": "",
        "lastSfnSyncCheckDate": "2000-01-01T00:00:00Z",
    }
    row.update(overrides)
    return row


def _input_row():
    return {
        "workflowExecutionId": EXEC_ID,
        "databaseId:assetId": f"{DB}:{ASSET}",
        "databaseId:assetId:inputAssetFileKey": f"{DB}:{ASSET}:/f.glb",
        "databaseId": DB, "assetId": ASSET, "inputAssetFileKey": "/f.glb",
        "workflowId": "wf", "workflowDatabaseId": "wf-db",
        "executionStartDate": _recent_iso(30),
    }


@pytest.mark.unit
class TestTheListReconcileWritesOnlyWhatThePollProduced:
    """S2-BACKEND-105, first half: the write named attributes the poll never touched."""

    def _reconcile(self, describe_response):
        persist = MagicMock()
        main_row = _running_main_row()
        le.build_execution_items(
            input_items=[_input_row()],
            fetch_main_row=lambda eid: main_row,
            describe_execution=lambda arn: describe_response,
            persist_main_row=persist,
            workflow_id_filter="", workflow_database_id="",
            fetch_execution_log_and_error=lambda *a: ("err text", "log text"))
        persist.assert_called_once()
        return persist.call_args.args[0]

    def test_a_still_running_poll_writes_only_the_sync_stamp(self):
        written = self._reconcile({"status": "RUNNING", "startDate": None, "stopDate": None})
        assert "lastSfnSyncCheckDate" in written
        # The attributes the poll did not produce are ABSENT, so their read-time values cannot be
        # written back over a writer that finished the execution in the meantime.
        for attr in ("executionLog", "executionError", "executionStopDate", "executionStatus"):
            assert attr not in written, f"{attr} was written from the read-time snapshot: {written}"

    def test_the_write_still_names_the_row_it_reconciles(self):
        # Control: narrowing the payload must not lose the row identity the update keys on.
        written = self._reconcile({"status": "RUNNING", "startDate": None, "stopDate": None})
        assert written["workflowExecutionId"] == EXEC_ID
        assert written["workflowDatabaseId:workflowId"] == COMPOSITE

    def test_a_terminal_poll_still_writes_the_status_dates_and_log(self):
        # Control: the reconcile that DID observe a completion must still record all of it, otherwise
        # the test above is satisfied by a reconcile that writes nothing at all.
        stop = datetime(2026, 6, 16, 0, 5, 0)
        written = self._reconcile({"status": "ABORTED", "startDate": stop, "stopDate": stop})
        assert written["executionStatus"] == "ABORTED"
        assert written["executionStopDate"] == "2026-06-16T00:05:00Z"
        assert written["executionLog"] == "log text"
        assert written["executionError"] == "err text"


@pytest.mark.unit
class TestTheReconcileWriteIsGuardedWhenItCarriesATerminalStatus:
    """S2-BACKEND-105, second half: the guard, asserted through the real closure `get_executions`
    builds rather than a re-implementation of it."""

    def _list(self, describe_response):
        inputs_table, main_table, cfg_table = MagicMock(), MagicMock(), MagicMock()
        inputs_table.query.return_value = {"Items": [_input_row()]}
        main_table.query.return_value = {"Items": [_running_main_row()]}
        cfg_table.query.return_value = {"Items": []}

        def _table(name):
            return {le.workflow_execution_inputs_table: inputs_table,
                    le.workflow_execution_database_v2: main_table,
                    le.workflow_execution_configuration_table: cfg_table}.get(name, MagicMock())

        le.claims_and_roles = {"tokens": ["u1"]}
        enforcer = MagicMock()
        enforcer.enforce.return_value = True
        with patch(f"{MOD}.dynamodb") as ddb, \
                patch(f"{MOD}.CasbinEnforcer", return_value=enforcer), \
                patch(f"{MOD}.get_asset_details",
                      side_effect=lambda d, a: {"databaseId": d, "assetId": a}), \
                patch(f"{MOD}._execution_access_check", return_value=(True, "")), \
                patch(f"{MOD}._fetch_execution_logs", return_value="live log"), \
                patch(f"{MOD}.sfn") as sfn:
            ddb.Table.side_effect = _table
            sfn.describe_execution.return_value = describe_response
            resp = le.get_executions({}, DB, ASSET, "", "", {})
        assert resp["statusCode"] == 200
        assert main_table.update_item.called, "the reconcile did not write at all"
        return main_table.update_item.call_args.kwargs

    def test_a_terminal_reconcile_refuses_to_overwrite_a_terminal_row(self):
        stop = datetime(2026, 6, 16, 0, 5, 0)
        kwargs = self._list({"status": "SUCCEEDED", "startDate": stop, "stopDate": stop})
        condition = kwargs.get("ConditionExpression", "")
        assert "attribute_not_exists(executionStatus)" in condition
        assert "NOT executionStatus IN (" in condition
        assert set(le.TERMINAL_STATUSES).issubset(set(kwargs["ExpressionAttributeValues"].values()))

    def test_a_sync_only_reconcile_writes_nothing_a_terminal_writer_owns(self):
        # The still-running poll needs no guard because it touches no attribute a completing writer
        # owns — asserted rather than assumed, since an unguarded write of any of them is the defect.
        kwargs = self._list({"status": "RUNNING", "startDate": None, "stopDate": None})
        assert set(kwargs["ExpressionAttributeNames"].values()) == {"lastSfnSyncCheckDate"}


@pytest.mark.unit
class TestTheDetailsReconcileIsGuardedToo:
    """S2-BACKEND-105: the details path writes a 3-attribute tuple with the same defect — its
    executionStopDate comes from the read, where it is the empty string."""

    def test_the_still_running_branch_neither_writes_a_stop_date_nor_writes_unguarded(self):
        table = MagicMock()
        main_row = _running_main_row()
        with patch.object(le.dynamodb, "Table", return_value=table), \
                patch(f"{MOD}.sfn") as sfn:
            sfn.describe_execution.return_value = {"status": "RUNNING"}
            le._reconcile_main_status(EXEC_ID, main_row)
        kwargs = table.update_item.call_args.kwargs
        assert "executionStopDate" not in set(kwargs["ExpressionAttributeNames"].values()), (
            "the empty read-time stop date must not be written back")
        condition = kwargs.get("ConditionExpression", "")
        assert "NOT executionStatus IN (" in condition

    def test_an_observed_completion_is_still_recorded(self):
        # Control: the guard and the narrowed payload must not stop the details path recording a
        # completion Step Functions really reported.
        table = MagicMock()
        main_row = _running_main_row()
        with patch.object(le.dynamodb, "Table", return_value=table), \
                patch(f"{MOD}.sfn") as sfn:
            sfn.describe_execution.return_value = {
                "status": "SUCCEEDED", "stopDate": datetime(2026, 6, 16, 0, 5, 0)}
            le._reconcile_main_status(EXEC_ID, main_row)
        kwargs = table.update_item.call_args.kwargs
        assert set(kwargs["ExpressionAttributeNames"].values()) == {
            "executionStatus", "executionStopDate", "lastSfnSyncCheckDate"}
        assert "2026-06-16T00:05:00Z" in kwargs["ExpressionAttributeValues"].values()


def _moto_tables(ddb):
    """The three tables the asset listing reads, with the two indexes it queries."""
    ddb.create_table(
        TableName=le.workflow_execution_database_v2,
        KeySchema=[{"AttributeName": "workflowExecutionId", "KeyType": "HASH"},
                   {"AttributeName": "workflowDatabaseId:workflowId", "KeyType": "RANGE"}],
        AttributeDefinitions=[
            {"AttributeName": "workflowExecutionId", "AttributeType": "S"},
            {"AttributeName": "workflowDatabaseId:workflowId", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST")
    ddb.create_table(
        TableName=le.workflow_execution_inputs_table,
        KeySchema=[{"AttributeName": "workflowExecutionId", "KeyType": "HASH"},
                   {"AttributeName": "databaseId:assetId:inputAssetFileKey", "KeyType": "RANGE"}],
        AttributeDefinitions=[
            {"AttributeName": "workflowExecutionId", "AttributeType": "S"},
            {"AttributeName": "databaseId:assetId:inputAssetFileKey", "AttributeType": "S"},
            {"AttributeName": "databaseId:assetId", "AttributeType": "S"},
            {"AttributeName": "executionStartDate", "AttributeType": "S"}],
        GlobalSecondaryIndexes=[{
            "IndexName": "WorkflowExecInputsByAssetGSI",
            "KeySchema": [{"AttributeName": "databaseId:assetId", "KeyType": "HASH"},
                          {"AttributeName": "executionStartDate", "KeyType": "RANGE"}],
            "Projection": {"ProjectionType": "ALL"}}],
        BillingMode="PAY_PER_REQUEST")
    ddb.create_table(
        TableName=le.workflow_execution_configuration_table,
        KeySchema=[{"AttributeName": "workflowExecutionId", "KeyType": "HASH"},
                   {"AttributeName": "recordType", "KeyType": "RANGE"}],
        AttributeDefinitions=[
            {"AttributeName": "workflowExecutionId", "AttributeType": "S"},
            {"AttributeName": "recordType", "AttributeType": "S"},
            {"AttributeName": "outputDatabaseId:outputAssetId", "AttributeType": "S"},
            {"AttributeName": "executionStartDate", "AttributeType": "S"}],
        GlobalSecondaryIndexes=[{
            "IndexName": "WorkflowExecConfigByOutputAssetGSI",
            "KeySchema": [{"AttributeName": "outputDatabaseId:outputAssetId", "KeyType": "HASH"},
                          {"AttributeName": "executionStartDate", "KeyType": "RANGE"}],
            "Projection": {"ProjectionType": "ALL"}}],
        BillingMode="PAY_PER_REQUEST")
    return ddb.Table(le.workflow_execution_database_v2)


@pytest.mark.unit
class TestTheCompletionRaceAgainstDynamoDB:
    """S2-BACKEND-105 end to end, against a real DynamoDB expression parser rather than a pattern
    match on the condition string."""

    KEY = {"workflowExecutionId": EXEC_ID, "workflowDatabaseId:workflowId": COMPOSITE}

    @pytest.mark.aws
    def test_a_completed_execution_keeps_its_status_and_its_captured_log(self):
        """The finding's own scenario: a list-path poll overlapping the end-state write.

        describe_execution is where the overlap is injected — the end-state lambda runs as a state
        INSIDE the machine, so it finishes the row while Step Functions still reports RUNNING.
        """
        import boto3
        from moto import mock_aws
        with mock_aws():
            ddb = boto3.resource("dynamodb", region_name="us-east-1")
            main_table = _moto_tables(ddb)
            main_table.put_item(Item=_running_main_row())
            ddb.Table(le.workflow_execution_inputs_table).put_item(Item=_input_row())

            def _describe_and_finish(**kwargs):
                # The competing writer, mid-request: terminal status, stop date, captured log.
                main_table.update_item(
                    Key=self.KEY,
                    UpdateExpression=("SET executionStatus = :s, executionStopDate = :d, "
                                      "executionLog = :l"),
                    ExpressionAttributeValues={":s": "SUCCEEDED", ":d": _recent_iso(1),
                                               ":l": "captured at completion"})
                return {"status": "RUNNING"}

            le.claims_and_roles = {"tokens": ["u1"]}
            enforcer = MagicMock()
            enforcer.enforce.return_value = True
            with patch.object(le, "dynamodb", ddb), \
                    patch(f"{MOD}.CasbinEnforcer", return_value=enforcer), \
                    patch(f"{MOD}.get_asset_details",
                          side_effect=lambda d, a: {"databaseId": d, "assetId": a}), \
                    patch(f"{MOD}._execution_access_check", return_value=(True, "")), \
                    patch(f"{MOD}.sfn") as sfn:
                sfn.describe_execution.side_effect = _describe_and_finish
                resp = le.get_executions({}, DB, ASSET, "", "", {})
            assert resp["statusCode"] == 200
            item = main_table.get_item(Key=self.KEY)["Item"]
            assert item["executionStatus"] == "SUCCEEDED", "the finished run was reverted to RUNNING"
            assert item["executionLog"] == "captured at completion", "the captured log was blanked"
            assert item["executionStopDate"], "the stop date was blanked"

    @pytest.mark.aws
    def test_an_uncontested_poll_still_records_what_it_observed(self):
        """Control: without a competing writer the reconcile must still write, or the test above is
        satisfied by a reconcile that no longer reconciles anything."""
        import boto3
        from moto import mock_aws
        with mock_aws():
            ddb = boto3.resource("dynamodb", region_name="us-east-1")
            main_table = _moto_tables(ddb)
            main_table.put_item(Item=_running_main_row())
            ddb.Table(le.workflow_execution_inputs_table).put_item(Item=_input_row())

            le.claims_and_roles = {"tokens": ["u1"]}
            enforcer = MagicMock()
            enforcer.enforce.return_value = True
            with patch.object(le, "dynamodb", ddb), \
                    patch(f"{MOD}.CasbinEnforcer", return_value=enforcer), \
                    patch(f"{MOD}.get_asset_details",
                          side_effect=lambda d, a: {"databaseId": d, "assetId": a}), \
                    patch(f"{MOD}._execution_access_check", return_value=(True, "")), \
                    patch(f"{MOD}._fetch_execution_logs", return_value="polled log"), \
                    patch(f"{MOD}.sfn") as sfn:
                sfn.describe_execution.return_value = {
                    "status": "TIMED_OUT", "stopDate": datetime(2026, 6, 16, 0, 9, 0),
                    "error": "States.Timeout", "cause": "no heartbeat"}
                resp = le.get_executions({}, DB, ASSET, "", "", {})
            assert resp["statusCode"] == 200
            item = main_table.get_item(Key=self.KEY)["Item"]
            assert item["executionStatus"] == "TIMED_OUT"
            assert item["executionStopDate"] == "2026-06-16T00:09:00Z"
            assert item["executionLog"] == "polled log"


def _pipeline_row(subs, status="RUNNING", pipeline_id="P1"):
    return {"pipelineExecutionId": pipeline_id, "workflowExecutionId": EXEC_ID,
            "executionStatus": status, "registeredSubExecutions": subs,
            "executionStopDate": ""}


@pytest.mark.unit
class TestAbortClosesTheLateRegistrationWindow:
    """S2-BACKEND-044: the rows were read once, before the parent was stopped, and never re-read."""

    def _abort(self, row_reads, sub_spy=None):
        order = []
        main_table, pexec_table = MagicMock(), MagicMock()

        def _table(name):
            return pexec_table if name == le.pipeline_executions_table else main_table

        def _rows(execution_id):
            order.append("read")
            return row_reads.pop(0) if row_reads else []

        with patch(f"{MOD}.get_execution_main_row",
                   return_value=_running_main_row(executionStopDate="")), \
                patch(f"{MOD}.authorize_abort", return_value=(True, "")), \
                patch(f"{MOD}.get_pipeline_execution_rows", side_effect=_rows), \
                patch(f"{MOD}._stop_sfn_execution",
                      side_effect=lambda arn: order.append("stop-parent")), \
                patch(f"{MOD}._abort_registered_sub_process",
                      side_effect=(sub_spy or (lambda sub: ""))), \
                patch(f"{MOD}._persist_reconciled_main_row"), \
                patch(f"{MOD}.log_actions"), \
                patch.object(le.dynamodb, "Table", side_effect=_table), \
                patch.object(le.eo, "set_pipeline_status"):
            resp = le.abort_execution({}, EXEC_ID)
        return resp, order

    def test_the_parent_is_stopped_before_the_pipeline_rows_are_read(self):
        # A running parent is what schedules the next task, so reading the rows while it still runs
        # leaves a window in which a task starts and registers work this request has already passed.
        resp, order = self._abort([[], []])
        assert resp["statusCode"] == 200
        assert order.index("stop-parent") < order.index("read"), order

    def test_a_sub_process_registered_after_the_first_read_is_still_stopped(self):
        late = {"resourceType": "batchJob", "jobId": "job-late"}
        stopped = []

        def _spy(sub):
            stopped.append(sub.get("jobId") or sub.get("executionArn") or "")
            return ""

        # First read: nothing registered yet (the EventBridge registration has not landed). Second
        # read: the job is there, on a row this request has meanwhile stamped ABORTED.
        resp, _order = self._abort(
            [[_pipeline_row([])], [_pipeline_row([late], status="ABORTED")]], sub_spy=_spy)
        assert resp["statusCode"] == 200
        assert "job-late" in stopped, (
            "a job registered inside the abort window is unstoppable through the API afterwards")

    def test_a_sub_process_seen_on_the_first_read_is_still_stopped(self):
        # Control: the second pass must not have replaced the first one.
        early = {"resourceType": "batchJob", "jobId": "job-early"}
        stopped = []
        resp, _order = self._abort(
            [[_pipeline_row([early])], [_pipeline_row([early], status="ABORTED")]],
            sub_spy=lambda sub: stopped.append(sub.get("jobId")) or "")
        assert resp["statusCode"] == 200
        assert "job-early" in stopped

    def test_a_warning_from_the_second_pass_reaches_the_caller(self):
        # The abort answers 200; the only signal that something was left running is this list, so a
        # sub-process the second pass could not stop has to appear in it.
        late = {"resourceType": "ecsTask", "taskArn": "arn:task:late"}
        resp, _order = self._abort(
            [[_pipeline_row([])], [_pipeline_row([late], status="ABORTED")]],
            sub_spy=lambda sub: "could not be aborted: arn:task:late")
        body = json.loads(resp["body"])
        assert any("arn:task:late" in w for w in body.get("warnings", [])), body


@pytest.mark.unit
class TestADeadlineCloudJobIsCancellable:
    """S2-BACKEND-045: the registered farm job matched no abortable type, so abort reported nothing
    and cancelled nothing."""

    SUB = {"resourceType": "deadlineCloudJob", "farmId": "farm-1", "queueId": "queue-1",
           "jobId": "job-1"}

    def test_the_abort_cancels_the_farm_job(self):
        client = MagicMock()
        with patch.object(le, "deadline_client", client):
            warning = le._abort_registered_sub_process(dict(self.SUB))
        assert warning == "", warning
        kwargs = client.update_job.call_args.kwargs
        assert kwargs == {"farmId": "farm-1", "queueId": "queue-1", "jobId": "job-1",
                          "targetTaskRunStatus": "CANCELED"}

    def test_a_job_that_already_finished_is_not_reported_as_left_running(self):
        # Cancelling a finished job is rejected by Deadline Cloud, and an abort racing a job that just
        # completed is normal — reporting it would tell the caller work was left running when none was.
        import botocore.exceptions
        client = MagicMock()
        client.update_job.side_effect = botocore.exceptions.ClientError(
            {"Error": {"Code": "ConflictException", "Message": "already terminal"}}, "UpdateJob")
        with patch.object(le, "deadline_client", client):
            assert le._abort_registered_sub_process(dict(self.SUB)) == ""

    def test_a_real_cancel_failure_is_reported_with_the_job_id(self):
        # The grant this needs (deadline:UpdateJob) lives in the CDK layer, so a missing permission is
        # the realistic failure — and it must surface rather than be swallowed into a clean abort.
        import botocore.exceptions
        client = MagicMock()
        client.update_job.side_effect = botocore.exceptions.ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "UpdateJob")
        with patch.object(le, "deadline_client", client):
            warning = le._abort_registered_sub_process(dict(self.SUB))
        assert "job-1" in warning and "AccessDenied" in warning

    def test_a_deployment_without_the_service_says_the_job_was_left_running(self):
        with patch.object(le, "deadline_client", None):
            warning = le._abort_registered_sub_process(dict(self.SUB))
        assert "job-1" in warning

    def test_an_incomplete_registration_is_named_rather_than_cancelled(self):
        # A locator missing its farm or queue cannot address a job; guessing would cancel nothing (or
        # something else). So no call is made - but the job WAS registered, so it is named: staying
        # silent reported a clean abort while a farm job may still have been running on the farm, which
        # is the outcome this return value exists to prevent.
        client = MagicMock()
        with patch.object(le, "deadline_client", client):
            warning = le._abort_registered_sub_process(
                {"resourceType": "deadlineCloudJob", "jobId": "job-1"})
        assert "job-1" in warning and "may still be running" in warning
        client.update_job.assert_not_called()

    def test_an_unknown_resource_type_still_reports_that_it_was_left_running(self):
        # Control: adding the Deadline arm must not swallow the types that genuinely have no stop API.
        client = MagicMock()
        with patch.object(le, "deadline_client", client):
            warning = le._abort_registered_sub_process(
                {"resourceType": "ecsTask", "taskArn": "arn:task:1"})
        assert "arn:task:1" in warning
        client.update_job.assert_not_called()
