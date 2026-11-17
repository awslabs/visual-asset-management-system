# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The completion contract of a run whose large outputs are copied by the copy engine, now that the
workflow output step does its copy work THROUGH the engine (#1123, owner decision 2026-11-10: the
output step holds no grant on the copy jobs table or the copy executions; it invokes the engine).

Driven end to end with the real output step (await and finalize modes), the real engine handler and
the real `common.largeObjectCopy` against moto's DynamoDB and S3. Only the Step Functions client is a
mock, so each SendTaskSuccess (the waiting state's answer) is observed.

Pinned here:
- the waiting state's task token is answered only once EVERY copy job of the run is terminal, and
  exactly once, whichever of the token registration and the last copy comes first;
- a copy that failed (or was refused) makes finalize name the file in the run's error, never an
  early or clean success;
- a token an earlier deployment's output step registered (a run already waiting when this deploys)
  is still answered by the engine when the copies finish;
- a refused or failed engine call fails the output step (it raises), so the run cannot be reported
  done on a call that did not happen;
- the engine call: AccessDenied while the permission deploys is retried within a bound, anything
  else is not, and the engine accepts only the output step operations.
The 48 h maximum wait is the AwaitOutputIngestion state's timeout, unchanged by this routing:
tests/common/workflows/test_workflowAsl_output_ingestion.py pins it and its States.Timeout catch.
"""

import json
import os
from unittest.mock import MagicMock, patch

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

for _k, _v in {
    "WORKFLOW_STORAGE_TABLE_V2_NAME": "t-wf-v2",
    "WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME": "t-inputs",
    "WORKFLOW_EXECUTION_LOCKS_STORAGE_TABLE_NAME": "t-locks",
    "WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME": "t-config",
    "LARGE_OBJECT_COPY_JOBS_STORAGE_TABLE_NAME": "t-copy-jobs",
}.items():
    os.environ.setdefault(_k, _v)

from backend.tests.handlers.workflows import test_processOutput_ingestion_outcome as base  # noqa: E402
from backend.tests.copyEngineRouting import engine, route_to_engine  # noqa: E402

po = base.po
loc = engine.loc
REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
EXECUTION = "E1"
FILES = ("big-a.bin", "big-b.bin")


def _job(rel):
    job = loc.build_job(
        kind=loc.KIND_WORKFLOW_OUTPUT,
        source={"bucket": "run-bucket", "key": f"pipelines/p/j/output/{EXECUTION}/files/{rel}", "size": 2 * loc.GIB},
        dest={"bucket": "asset-bucket", "key": f"a1/{rel}"},
        metadata={}, content_headers={}, database_id="db1", asset_id="a1", upload_id="u1",
        upload_type="assetFile", relative_key=rel, change_user_id="u1", scope_id=EXECUTION,
        workflow_execution_id=EXECUTION, workflow_id="wf1", workflow_database_id="wdb",
        batch_id=EXECUTION)
    execution_arn = "arn:aws:states:" + REGION + ":1:execution:LargeObjectCopy-x:" + rel
    loc.start_copy_job(engine.jobs_table, MagicMock(**{"start_execution.return_value": {
        "executionArn": execution_arn}}),
        "arn:aws:states:r:1:stateMachine:LargeObjectCopy-x", job)
    return job


@pytest.fixture
def run():
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name=REGION)
        jobs = ddb.create_table(
            TableName=engine.jobs_table_name,
            KeySchema=[{"AttributeName": "jobId", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "jobId", "AttributeType": "S"},
                                  {"AttributeName": "workflowExecutionId", "AttributeType": "S"}],
            GlobalSecondaryIndexes=[{
                "IndexName": loc.WORKFLOW_EXECUTION_ID_INDEX,
                "KeySchema": [{"AttributeName": "workflowExecutionId", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"}}],
            BillingMode="PAY_PER_REQUEST")
        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(Bucket="run-bucket")
        state_key = f"pipelines/state/{EXECUTION}/outputIngestionState.json"
        s3.put_object(Bucket="run-bucket", Key=state_key, Body=json.dumps({
            "event": {"workflowExecutionId": EXECUTION, "workflowDatabaseId": "wdb", "workflowId": "wf1"},
            "collectedOutputFiles": [{"fileType": "file", "relativeFilePath": rel} for rel in FILES],
            "collectedOutputMetadata": [], "collectedOutputResults": [], "outputFailures": [],
            "pendingJobs": {}, "deferredMetadata": [], "sourceBucket": "run-bucket"}).encode())
        sfn = MagicMock()
        calls = []
        with route_to_engine(po, jobs, sfn_client=sfn, calls=calls), \
                patch.object(engine, "dynamodb_client", boto3.client("dynamodb", region_name=REGION)), \
                patch.object(engine, "_record_progress", lambda job, status: None), \
                patch.object(po, "s3c", s3):
            jobs_by_file = {rel: _job(rel) for rel in FILES}
            context = {"workflowExecutionId": EXECUTION, "workflowDatabaseId": "wdb",
                       "workflowId": "wf1", "stateBucket": "run-bucket", "stateKey": state_key,
                       "legacy": False}
            po._engine(loc.OUTPUT_STEP_RECORD_BATCH, batchId=EXECUTION,
                       jobIds=[j["jobId"] for j in jobs_by_file.values()], context=context, claim=False)
            yield {"jobs": jobs, "sfn": sfn, "calls": calls, "job": jobs_by_file}


def _finish(run, rel, status, error=None):
    job = run["job"][rel]
    loc.finalize_job(run["jobs"], job["jobId"], status, {"error": error} if error else {})
    engine.notify(job, status)


def _answers(run):
    return run["sfn"].send_task_success.call_args_list


class TestTheRunWaitsForEveryCopy:
    def test_the_token_is_answered_only_after_the_last_copy_ends(self, run):
        po._await_ingestion({"batchId": EXECUTION, "taskToken": "tok-1"})
        assert _answers(run) == []
        _finish(run, FILES[0], loc.STATUS_SUCCEEDED)
        # One copy still running: the run keeps waiting.
        assert _answers(run) == []
        _finish(run, FILES[1], loc.STATUS_SUCCEEDED)
        assert len(_answers(run)) == 1
        assert _answers(run)[0].kwargs["taskToken"] == "tok-1"
        # A repeated notify does not answer again.
        engine.notify(run["job"][FILES[1]], loc.STATUS_SUCCEEDED)
        assert len(_answers(run)) == 1

    def test_copies_that_ended_before_the_wait_registered_are_answered_at_registration(self, run):
        _finish(run, FILES[0], loc.STATUS_SUCCEEDED)
        _finish(run, FILES[1], loc.STATUS_FAILED, error="Copy failed")
        assert _answers(run) == []
        po._await_ingestion({"batchId": EXECUTION, "taskToken": "tok-2"})
        assert [c.kwargs["taskToken"] for c in _answers(run)] == ["tok-2"]
        # A retried await (same token) does not answer twice.
        po._await_ingestion({"batchId": EXECUTION, "taskToken": "tok-2"})
        assert len(_answers(run)) == 1

    def test_the_output_step_itself_makes_no_copy_call_of_its_own(self, run):
        po._await_ingestion({"batchId": EXECUTION, "taskToken": "tok-3"})
        assert run["calls"], "the output step made no engine call"
        assert run["calls"][-1][0] == loc.OUTPUT_STEP_REGISTER_TOKEN
        assert run["calls"][-1][1] == {"batchId": EXECUTION, "taskToken": "tok-3"}
        assert not hasattr(po, "copy_jobs_table")
        assert not hasattr(po, "sfn_client")


class TestAFailedCopyFailsTheRun:
    def test_finalize_names_the_failed_file_and_keeps_the_landed_one(self, run):
        _finish(run, FILES[0], loc.STATUS_SUCCEEDED)
        _finish(run, FILES[1], loc.STATUS_FAILED, error="The source changed during the copy")
        po._await_ingestion({"batchId": EXECUTION, "taskToken": "tok-4"})
        with patch.object(po, "_record_and_finish") as finish:
            answer = po._finalize_ingestion({"batchId": EXECUTION, "workflowExecutionId": EXECUTION})
        assert answer == {"finalized": True, "failedFiles": 1}
        output_files, failures = finish.call_args.args[2], finish.call_args.args[5]
        assert failures == ["Ingesting output file 'big-b.bin' into the asset failed: "
                            "The source changed during the copy"]
        # The landed file is recorded; the failed one is left out of the output rows.
        assert [d["relativeFilePath"] for d in output_files] == ["big-a.bin"]
        assert output_files[0]["ingestionStatus"] == loc.STATUS_SUCCEEDED

    def test_a_copy_that_never_reported_is_a_failure(self, run):
        _finish(run, FILES[0], loc.STATUS_SUCCEEDED)
        with patch.object(po, "_record_and_finish") as finish:
            po._finalize_ingestion({"batchId": EXECUTION, "workflowExecutionId": EXECUTION})
        assert finish.call_args.args[5] == [
            f"Ingesting output file 'big-b.bin' into the asset failed: {loc.REASON_NOT_REPORTED}"]

    def test_a_refused_copy_is_a_failure_too(self, run):
        _finish(run, FILES[0], loc.STATUS_SUCCEEDED)
        _finish(run, FILES[1], loc.STATUS_FAILED, error=loc.REASON_CANCELLED)
        answer = po._engine(loc.OUTPUT_STEP_BATCH_OUTCOMES, batchId=EXECUTION, workflowExecutionId=EXECUTION)
        statuses = {rel: row["status"] for rel, row in answer["outcomes"].items()}
        assert statuses == {FILES[0]: loc.STATUS_SUCCEEDED, FILES[1]: loc.STATUS_FAILED}


class TestUpgradeSafety:
    def test_a_token_an_earlier_deployment_registered_is_still_answered(self, run):
        # The previous head's output step stored the token on the batch row itself.
        loc.register_task_token(run["jobs"], EXECUTION, "tok-old")
        _finish(run, FILES[0], loc.STATUS_SUCCEEDED)
        assert _answers(run) == []
        _finish(run, FILES[1], loc.STATUS_SUCCEEDED)
        assert [c.kwargs["taskToken"] for c in _answers(run)] == ["tok-old"]

    def test_a_batch_an_earlier_deployment_recorded_is_finalized_by_the_new_code(self, run):
        _finish(run, FILES[0], loc.STATUS_SUCCEEDED)
        _finish(run, FILES[1], loc.STATUS_SUCCEEDED)
        answer = po._engine(loc.OUTPUT_STEP_BATCH_OUTCOMES, batchId=EXECUTION, workflowExecutionId=EXECUTION)
        assert answer["batch"]["context"]["stateKey"].endswith("outputIngestionState.json")
        assert answer["batch"]["terminalCount"] == 2 and answer["batch"]["totalCount"] == 2


class TestAFailedEngineCallFailsTheStep:
    def test_await_raises_when_the_engine_call_fails(self):
        with patch.object(po, "_engine", side_effect=loc.EngineCallFailed("refused")):
            with pytest.raises(loc.EngineCallFailed):
                po._await_ingestion({"batchId": EXECUTION, "taskToken": "t"})

    def test_finalize_raises_when_the_engine_call_fails(self):
        with patch.object(po, "_engine", side_effect=loc.EngineCallFailed("refused")):
            with pytest.raises(loc.EngineCallFailed):
                po._finalize_ingestion({"batchId": EXECUTION, "workflowExecutionId": EXECUTION})


def _invoke_answer(payload, function_error=None):
    response = {"Payload": MagicMock(read=MagicMock(return_value=json.dumps(payload).encode()))}
    if function_error:
        response["FunctionError"] = function_error
    return response


def _denied():
    return ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "Invoke")


class TestCallEngine:
    def test_the_answer_is_returned(self):
        client = MagicMock(**{"invoke.return_value": _invoke_answer({"claimed": True})})
        assert loc.call_engine(client, "fn", loc.OUTPUT_STEP_RECORD_BATCH, batchId="E1") == {"claimed": True}
        sent = json.loads(client.invoke.call_args.kwargs["Payload"])
        assert sent == {"action": "outputStep", "operation": "recordBatch", "batchId": "E1"}
        assert client.invoke.call_args.kwargs["InvocationType"] == "RequestResponse"

    def test_an_engine_error_raises(self):
        client = MagicMock(**{"invoke.return_value": _invoke_answer({"errorMessage": "x"}, "Unhandled")})
        with pytest.raises(loc.EngineCallFailed):
            loc.call_engine(client, "fn", loc.OUTPUT_STEP_BATCH_OUTCOMES, batchId="E1")

    def test_access_denied_while_the_permission_deploys_is_retried(self):
        client = MagicMock()
        client.invoke.side_effect = [_denied(), _denied(), _invoke_answer({"registered": True})]
        sleeps = []
        assert loc.call_engine(client, "fn", loc.OUTPUT_STEP_REGISTER_TOKEN, sleep=sleeps.append,
                               batchId="E1", taskToken="t") == {"registered": True}
        assert sleeps == list(loc.ENGINE_CALL_DEPLOY_DELAYS[:2])

    def test_access_denied_past_the_bound_raises(self):
        client = MagicMock(**{"invoke.side_effect": _denied()})
        sleeps = []
        with pytest.raises(loc.EngineCallFailed):
            loc.call_engine(client, "fn", loc.OUTPUT_STEP_CANCEL, sleep=sleeps.append,
                            workflowExecutionId="E1")
        assert sum(sleeps) <= loc.ENGINE_CALL_DEPLOY_WAIT_SECONDS

    def test_access_denied_is_not_retried_past_the_invocation_time(self):
        client = MagicMock(**{"invoke.side_effect": _denied()})
        with pytest.raises(loc.EngineCallFailed):
            loc.call_engine(client, "fn", loc.OUTPUT_STEP_CANCEL, sleep=lambda s: None,
                            remaining_ms=lambda: 10_000, workflowExecutionId="E1")
        assert client.invoke.call_count == 1

    def test_another_error_is_not_retried(self):
        client = MagicMock(**{"invoke.side_effect": ClientError(
            {"Error": {"Code": "ResourceNotFoundException"}}, "Invoke")})
        with pytest.raises(loc.EngineCallFailed):
            loc.call_engine(client, "fn", loc.OUTPUT_STEP_CANCEL, sleep=lambda s: None,
                            workflowExecutionId="E1")
        assert client.invoke.call_count == 1

    def test_no_engine_raises_after_the_bounded_wait(self):
        client = MagicMock()
        sleeps = []
        with pytest.raises(loc.EngineCallFailed):
            loc.call_engine(client, "", loc.OUTPUT_STEP_CANCEL, sleep=sleeps.append,
                            workflowExecutionId="E1")
        client.invoke.assert_not_called()
        assert 0 < sum(sleeps) <= loc.ENGINE_CALL_DEPLOY_WAIT_SECONDS

    def test_an_engine_name_published_during_the_deploy_is_picked_up(self):
        names = iter(["", "", "engine-fn"])
        client = MagicMock(**{"invoke.return_value": _invoke_answer({"jobs": {}})})
        assert loc.call_engine(client, lambda: next(names), loc.OUTPUT_STEP_RECORDED_JOBS,
                               sleep=lambda s: None, workflowExecutionId="E1") == {"jobs": {}}
        assert client.invoke.call_args.kwargs["FunctionName"] == "engine-fn"

    def test_an_unknown_operation_is_refused_before_the_call(self):
        client = MagicMock()
        with pytest.raises(ValueError):
            loc.call_engine(client, "fn", "deleteEverything")
        client.invoke.assert_not_called()


class TestTheEngineAcceptsOnlyOutputStepOperations:
    def test_an_unknown_operation_raises(self):
        with pytest.raises(ValueError):
            engine.lambda_handler({"action": "outputStep", "operation": "putItem"}, None)

    @pytest.mark.parametrize("operation,fields", [
        ("recordedJobs", {}),
        ("recordBatch", {}),
        ("registerTaskToken", {"batchId": "E1"}),
        ("batchOutcomes", {"batchId": "E1"}),
        ("cancel", {"workflowExecutionId": ""}),
    ])
    def test_a_missing_id_raises(self, operation, fields):
        with pytest.raises(ValueError):
            engine.lambda_handler({"action": "outputStep", "operation": operation, **fields}, None)
