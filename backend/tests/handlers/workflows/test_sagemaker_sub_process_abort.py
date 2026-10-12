# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Abortable SageMaker sub-processes, and the signal for the ones VAMS cannot stop.

Two registered resource types join the abortable set: `sageMakerTrainingJob` (locator `trainingJobArn`;
StopTrainingJob and DescribeTrainingJob take the NAME, parsed from the ARN) and
`sageMakerPipelineExecution` (locator `pipelineExecutionArn`). Both the abort API
(`executionService._abort_registered_sub_process`) and the failure path
(`executionOutputs.stop_registered_sub_process`, reached from `handleExecutionError`) stop them through
the shared helper, so the two cannot diverge on how a job is stopped or which stop errors are the
normal already-finished race.

Any other registered type — an external bridge's own compute, say — is displayed but left running when
its execution is aborted. The detail view says so per sub-process (`abortable: false`) and the abort
response lists each one under `notAbortable` beside the free-text `warnings`, so a caller can name what
was left running instead of inferring it from a sentence.
"""

import contextlib
import json
import os

import pytest
from unittest.mock import MagicMock, patch

import botocore.exceptions

# Env vars the two lambdas read at import time (the legacy break-glass path the suites use).
for _k, _v in {
    "ASSET_STORAGE_TABLE_NAME": "t-assets",
    "WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME": "t-exec-v2",
    "WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME": "t-wf-inputs",
    "PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME": "t-pexec",
    "WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME": "t-wf-cfg",
    "PIPELINE_EXECUTION_INPUT_FILES_STORAGE_TABLE_NAME": "t-pin-files",
    "PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME": "t-pin-md",
    "PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME": "t-pin-cfg",
    "PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME": "t-of",
    "PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE_NAME": "t-om",
    "PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE_NAME": "t-or",
    "PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME": "t-logs",
    "WORKFLOW_STORAGE_TABLE_NAME": "t-workflows",
    "PIPELINE_STORAGE_TABLE_NAME": "t-pipelines",
    "EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME": "t-execv2",
    "WORKFLOW_EXECUTION_LOG_GROUP_ARN": "arn:aws:logs:us-east-1:1:log-group:vams-wf:*",
}.items():
    os.environ.setdefault(_k, _v)

from backend.backend.common.workflows import executionOutputs as eo  # noqa: E402
from backend.backend.common.workflows import subExecutionStages as ses  # noqa: E402
from backend.backend.handlers.workflows import executionService as es  # noqa: E402
from backend.backend.handlers.workflows.sfn import handleExecutionError as heh  # noqa: E402

ACCOUNT = "123456789012"
TRAINING_JOB_NAME = "mock-bridge-train-0001"
TRAINING_JOB_ARN = f"arn:aws:sagemaker:us-east-1:{ACCOUNT}:training-job/{TRAINING_JOB_NAME}"
PIPELINE_EXECUTION_ARN = (f"arn:aws:sagemaker:us-east-1:{ACCOUNT}:pipeline/mock-bridge/execution/"
                          "a1b2c3d4e5f6")

TRAINING_SUB = {"resourceType": eo.RESOURCE_TYPE_SAGEMAKER_TRAINING_JOB,
                "trainingJobArn": TRAINING_JOB_ARN, "label": "Mock training", "stageName": "Train"}
PIPELINE_SUB = {"resourceType": eo.RESOURCE_TYPE_SAGEMAKER_PIPELINE_EXECUTION,
                "pipelineExecutionArn": PIPELINE_EXECUTION_ARN, "stageName": "Pipeline"}
SLURM_SUB = {"resourceType": "slurmJob", "jobId": "slurm-00001", "label": "Mock Slurm job",
             "stageName": "MockTrain"}


def _client_error(code, message=None, operation="StopTrainingJob"):
    return botocore.exceptions.ClientError(
        {"Error": {"Code": code, "Message": message or code}}, operation)


# ---------------------------------------------------------------------------
# The shared stop helper (failure path)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestTheSharedStopHelperStopsSageMaker:
    def test_a_training_job_is_stopped_by_the_name_parsed_from_its_arn(self):
        """StopTrainingJob takes the NAME: a call carrying the ARN raises ValidationException and
        stops nothing, so the parsed name is what is asserted."""
        sagemaker = MagicMock()
        assert eo.stop_registered_sub_process(dict(TRAINING_SUB), sagemaker_client=sagemaker) == ""
        sagemaker.stop_training_job.assert_called_once_with(TrainingJobName=TRAINING_JOB_NAME)

    def test_a_pipeline_execution_is_stopped_by_its_arn(self):
        sagemaker = MagicMock()
        assert eo.stop_registered_sub_process(dict(PIPELINE_SUB), sagemaker_client=sagemaker) == ""
        sagemaker.stop_pipeline_execution.assert_called_once_with(
            PipelineExecutionArn=PIPELINE_EXECUTION_ARN)

    def test_an_arn_that_is_not_a_training_job_is_named_not_stopped(self):
        sagemaker = MagicMock()
        bad = dict(TRAINING_SUB, trainingJobArn=f"arn:aws:sagemaker:us-east-1:{ACCOUNT}:model/x")
        message = eo.stop_registered_sub_process(bad, sagemaker_client=sagemaker)
        assert "not a training-job ARN" in message and "model/x" in message
        sagemaker.stop_training_job.assert_not_called()

    def test_an_entry_with_no_locator_is_silently_skipped(self):
        sagemaker = MagicMock()
        assert eo.stop_registered_sub_process(
            {"resourceType": eo.RESOURCE_TYPE_SAGEMAKER_TRAINING_JOB}, sagemaker_client=sagemaker) == ""
        assert eo.stop_registered_sub_process(
            {"resourceType": eo.RESOURCE_TYPE_SAGEMAKER_PIPELINE_EXECUTION},
            sagemaker_client=sagemaker) == ""
        sagemaker.stop_training_job.assert_not_called()
        sagemaker.stop_pipeline_execution.assert_not_called()

    def test_a_deployment_with_no_sagemaker_client_names_the_job_as_left_running(self):
        message = eo.stop_registered_sub_process(dict(TRAINING_SUB), sagemaker_client=None)
        assert TRAINING_JOB_NAME in message and "may still be running" in message
        message = eo.stop_registered_sub_process(dict(PIPELINE_SUB), sagemaker_client=None)
        assert PIPELINE_EXECUTION_ARN in message and "may still be running" in message

    @pytest.mark.parametrize("error", [
        _client_error("ResourceNotFound"),
        _client_error("ValidationException",
                      f"The request was rejected because the training job {TRAINING_JOB_NAME} is not "
                      "in progress."),
        _client_error("ValidationException", "Training job is already in status Stopping."),
        _client_error("ConflictException", "Pipeline execution is not in a stoppable state",
                      "StopPipelineExecution"),
    ])
    def test_a_job_that_already_finished_or_is_stopping_is_not_reported(self, error):
        sagemaker = MagicMock(stop_training_job=MagicMock(side_effect=error),
                              stop_pipeline_execution=MagicMock(side_effect=error))
        assert eo.stop_registered_sub_process(dict(TRAINING_SUB), sagemaker_client=sagemaker) == ""
        assert eo.stop_registered_sub_process(dict(PIPELINE_SUB), sagemaker_client=sagemaker) == ""

    @pytest.mark.parametrize("error", [
        _client_error("AccessDeniedException"),
        _client_error("ValidationException", "Requested resource not found: some other wording"),
        _client_error("ThrottlingException"),
        RuntimeError("boom"),
    ])
    def test_a_real_stop_failure_names_the_job_and_its_cause(self, error):
        sagemaker = MagicMock(stop_training_job=MagicMock(side_effect=error),
                              stop_pipeline_execution=MagicMock(side_effect=error))
        message = eo.stop_registered_sub_process(dict(TRAINING_SUB), sagemaker_client=sagemaker)
        assert TRAINING_JOB_NAME in message and "may still be running" in message
        assert (eo._error_code(error) or "boom") in message
        message = eo.stop_registered_sub_process(dict(PIPELINE_SUB), sagemaker_client=sagemaker)
        assert PIPELINE_EXECUTION_ARN in message and "may still be running" in message

    def test_the_abortable_set_names_exactly_the_five_types(self):
        assert eo.ABORTABLE_RESOURCE_TYPES == frozenset({
            "stepFunctionsExecution", "batchJob", "deadlineCloudJob",
            "sageMakerTrainingJob", "sageMakerPipelineExecution"})
        # The abort handler re-exports the shared set rather than a list that could drift.
        assert es.ABORTABLE_RESOURCE_TYPES == eo.ABORTABLE_RESOURCE_TYPES

    def test_an_unknown_type_is_still_reported_as_left_running(self):
        message = eo.stop_registered_sub_process(dict(SLURM_SUB), sagemaker_client=MagicMock())
        assert "slurmJob" in message and "slurm-00001" in message and "may still be running" in message


@pytest.mark.unit
class TestTheErrorHandlerThreadsTheSageMakerClient:
    def test_the_handler_hands_its_sagemaker_client_to_the_stop_path(self):
        sagemaker = MagicMock()
        mark_terminal = MagicMock(return_value=[])
        main_table = MagicMock(query=MagicMock(return_value={"Items": []}))
        rows = [{"pipelineExecutionId": "P1", "workflowExecutionId": "EXEC1",
                 "executionStatus": "RUNNING", "registeredSubExecutions": [dict(TRAINING_SUB)]}]
        body = {"workflowExecutionId": "EXEC1", "workflowDatabaseId": "wdb", "workflowId": "wf"}
        error_info = {"Error": "States.Timeout", "Cause": json.dumps({"errorMessage": "timed out"})}

        with patch.object(heh, "_get_pipeline_rows", return_value=rows), \
             patch.object(heh, "sagemaker_client", sagemaker), \
             patch.object(heh.eo, "mark_inflight_pipelines_terminal", mark_terminal), \
             patch.object(heh.eo, "finalize_main_row", MagicMock()), \
             patch.object(heh, "_fetch_execution_log", return_value=""), \
             patch.object(heh.dynamodb, "Table", return_value=main_table):
            heh.lambda_handler({"body": body, "errorInfo": error_info}, MagicMock())

        mark_terminal.assert_called_once()
        assert mark_terminal.call_args.kwargs["sagemaker_client"] is sagemaker

    def test_the_shared_helper_walks_rows_with_the_client(self):
        sagemaker = MagicMock()
        rows = [
            {"executionStatus": "RUNNING", "registeredSubExecutions": [dict(TRAINING_SUB)]},
            {"executionStatus": "FAILED", "registeredSubExecutions": [dict(PIPELINE_SUB)]},
        ]
        assert eo.stop_registered_sub_processes(rows, sagemaker_client=sagemaker) == []
        sagemaker.stop_training_job.assert_called_once_with(TrainingJobName=TRAINING_JOB_NAME)
        # The finished row's sub-process is left alone.
        sagemaker.stop_pipeline_execution.assert_not_called()

    def test_the_positional_contract_of_mark_inflight_pipelines_terminal_is_unchanged(self):
        import inspect
        names = list(inspect.signature(eo.mark_inflight_pipelines_terminal).parameters)
        assert names[:5] == ["dynamo", "pipeline_executions_table", "pipeline_rows", "status",
                             "stop_date"]
        assert "sagemaker_client" in names


# ---------------------------------------------------------------------------
# The abort API
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestTheAbortPathStopsSageMaker:
    def test_a_training_job_is_stopped_through_the_shared_helper_with_the_abort_client(self):
        sagemaker = MagicMock()
        with patch.object(es, "sagemaker_client", sagemaker):
            assert es._abort_registered_sub_process(dict(TRAINING_SUB)) == ""
        sagemaker.stop_training_job.assert_called_once_with(TrainingJobName=TRAINING_JOB_NAME)

    def test_a_pipeline_execution_is_stopped(self):
        sagemaker = MagicMock()
        with patch.object(es, "sagemaker_client", sagemaker):
            assert es._abort_registered_sub_process(dict(PIPELINE_SUB)) == ""
        sagemaker.stop_pipeline_execution.assert_called_once_with(
            PipelineExecutionArn=PIPELINE_EXECUTION_ARN)

    def test_a_real_failure_is_a_warning_and_an_absent_client_is_named(self):
        sagemaker = MagicMock(stop_training_job=MagicMock(
            side_effect=_client_error("AccessDeniedException")))
        with patch.object(es, "sagemaker_client", sagemaker):
            warning = es._abort_registered_sub_process(dict(TRAINING_SUB))
        assert "AccessDeniedException" in warning and TRAINING_JOB_NAME in warning
        with patch.object(es, "sagemaker_client", None):
            warning = es._abort_registered_sub_process(dict(TRAINING_SUB))
        assert "not available in this deployment" in warning

    def test_an_abortable_type_has_no_not_abortable_entry(self):
        for sub in (TRAINING_SUB, PIPELINE_SUB,
                    {"resourceType": "stepFunctionsExecution", "executionArn": "arn:x"},
                    {"resourceType": "batchJob", "jobId": "j"},
                    {"resourceType": "deadlineCloudJob", "jobId": "j", "farmId": "f", "queueId": "q"},
                    {"executionArn": "arn:x"}):
            assert es._not_abortable_entry(dict(sub)) is None

    def test_an_unknown_type_yields_a_warning_and_a_structured_entry(self):
        warning = es._abort_registered_sub_process(dict(SLURM_SUB))
        assert "slurmJob" in warning and "slurm-00001" in warning
        assert "VAMS cannot stop this resource type" in warning
        assert es._not_abortable_entry(dict(SLURM_SUB)) == {
            "resourceType": "slurmJob", "label": "Mock Slurm job", "stageName": "MockTrain",
            "locator": "slurm-00001"}

    def test_an_unknown_type_with_only_a_generic_arn_or_nothing_still_has_a_locator(self):
        assert es._not_abortable_entry({"resourceType": "ecsTask", "arn": "arn:x"})["locator"] == "arn:x"
        assert es._not_abortable_entry({"resourceType": "ecsTask"})["locator"] == "ecsTask"
        assert es._not_abortable_entry({"resourceType": "ecsTask"})["label"] == ""


def _abort(rows, stopped_subs=None):
    """Run abort_execution over `rows` with every AWS call stubbed. Returns the parsed response body.
    `stopped_subs` collects the sub-processes the stop dispatcher saw."""
    seen = stopped_subs if stopped_subs is not None else []

    def _dispatch(sub):
        seen.append(dict(sub))
        return _REAL_ABORT(sub)

    main_item = {"workflowExecutionId": "EXEC1", "workflow_execution_arn": "arn:x",
                 "executionStatus": "RUNNING"}
    with contextlib.ExitStack() as patches:
        for context in (
            patch.object(es, "get_execution_main_row", return_value=main_item),
            patch.object(es, "authorize_abort", return_value=(True, "")),
            patch.object(es, "_stop_sfn_execution", MagicMock()),
            patch.object(es, "get_pipeline_execution_rows", return_value=rows),
            patch.object(es.eo, "set_pipeline_status", MagicMock()),
            patch.object(es, "_persist_reconciled_main_row", MagicMock()),
            patch.object(es, "log_actions", MagicMock()),
            patch.object(es.dynamodb, "Table", MagicMock()),
            patch.object(es, "sagemaker_client", MagicMock()),
            patch.object(es, "_abort_registered_sub_process", side_effect=_dispatch),
        ):
            patches.enter_context(context)
        response = es.abort_execution({"requestContext": {}}, "EXEC1")
    assert response["statusCode"] == 200
    return json.loads(response["body"])


_REAL_ABORT = es._abort_registered_sub_process


def _row(*subs, status="RUNNING", pipeline_execution_id="P1"):
    return {"pipelineExecutionId": pipeline_execution_id, "workflowExecutionId": "EXEC1",
            "executionStatus": status, "registeredSubExecutions": [dict(s) for s in subs]}


@pytest.mark.unit
class TestTheAbortResponseListsWhatVamsCannotStop:
    def test_an_unknown_type_is_listed_under_not_abortable_beside_the_warning(self):
        body = _abort([_row(SLURM_SUB)])
        assert body["message"] == "Execution aborted"
        assert body["notAbortable"] == [{
            "resourceType": "slurmJob", "label": "Mock Slurm job", "stageName": "MockTrain",
            "locator": "slurm-00001"}]
        assert any("slurm-00001" in w for w in body["warnings"])

    def test_the_same_sub_process_seen_on_both_passes_is_listed_once(self):
        """Both passes read the same row; the second pass is deduplicated on the sub-process identity
        for stop calls, and the list is deduplicated on the locator so a repeat registration of the
        same job is also one entry."""
        body = _abort([_row(SLURM_SUB, dict(SLURM_SUB, label="again"))])
        assert len(body["notAbortable"]) == 1

    def test_a_clean_abort_of_abortable_types_carries_neither_list(self):
        body = _abort([_row(TRAINING_SUB, PIPELINE_SUB)])
        assert body == {"message": "Execution aborted"}

    def test_a_finished_row_contributes_nothing(self):
        body = _abort([_row(SLURM_SUB, status="SUCCEEDED")])
        assert body == {"message": "Execution aborted"}


# ---------------------------------------------------------------------------
# The detail view
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestTheSummaryCarriesAbortableAndSageMakerStatus:
    def setup_method(self):
        es._sagemaker_describe_cache.clear()

    def test_every_summary_says_whether_vams_can_stop_it(self):
        for sub, abortable in ((TRAINING_SUB, True), (PIPELINE_SUB, True), (SLURM_SUB, False),
                               ({"resourceType": "batchJob", "jobId": "j"}, True),
                               ({"resourceType": "ecsTask", "taskArn": "arn:x"}, False)):
            with patch.object(es, "sagemaker_client", MagicMock()), \
                 patch.object(es, "_describe_batch_job_cached", return_value=(None, "")):
                summary, _ = es._sub_execution_summary(dict(sub))
            assert summary["abortable"] is abortable, sub

    def test_an_unknown_type_keeps_its_label_and_stage_and_is_unknown(self):
        summary, warnings = es._sub_execution_summary(dict(SLURM_SUB))
        assert summary["resourceType"] == "slurmJob"
        assert summary["label"] == "Mock Slurm job" and summary["stageName"] == "MockTrain"
        assert summary["status"] == ses.STATUS_UNKNOWN and summary["abortable"] is False
        assert warnings == []

    @pytest.mark.parametrize("raw,mapped", [
        ("InProgress", "RUNNING"), ("Stopping", "RUNNING"), ("Completed", "SUCCEEDED"),
        ("Failed", "FAILED"), ("Stopped", "ABORTED"), ("Bogus", "UNKNOWN")])
    def test_a_training_job_is_described_by_name_and_its_status_mapped(self, raw, mapped):
        sagemaker = MagicMock(describe_training_job=MagicMock(return_value={
            "TrainingJobName": TRAINING_JOB_NAME, "TrainingJobStatus": raw,
            "TrainingStartTime": 1700000000000, "TrainingEndTime": 1700000600000,
            "FailureReason": "token=abc", "HyperParameters": {"secret": "x"}}))
        with patch.object(es, "sagemaker_client", sagemaker):
            summary, warnings = es._sub_execution_summary(dict(TRAINING_SUB))
        sagemaker.describe_training_job.assert_called_once_with(TrainingJobName=TRAINING_JOB_NAME)
        assert summary["status"] == mapped and warnings == []
        assert summary["resourceName"] == TRAINING_JOB_NAME
        assert summary["sageMaker"] == {"trainingJobName": TRAINING_JOB_NAME,
                                        "trainingJobArn": TRAINING_JOB_ARN}
        assert summary["startDate"].startswith("2023-11-14")
        # Hyperparameters never reach the summary or the memo.
        assert "HyperParameters" not in json.dumps(summary)
        assert "HyperParameters" not in json.dumps(es._sagemaker_describe_cache)

    @pytest.mark.parametrize("raw,mapped", [
        ("Executing", "RUNNING"), ("Stopping", "RUNNING"), ("Succeeded", "SUCCEEDED"),
        ("Failed", "FAILED"), ("Stopped", "ABORTED")])
    def test_a_pipeline_execution_is_described_and_its_status_mapped(self, raw, mapped):
        sagemaker = MagicMock(describe_pipeline_execution=MagicMock(return_value={
            "PipelineExecutionDisplayName": "nightly", "PipelineExecutionStatus": raw,
            "CreationTime": 1700000000000, "LastModifiedTime": 1700000600000,
            "PipelineParameters": [{"Name": "secret", "Value": "x"}]}))
        with patch.object(es, "sagemaker_client", sagemaker):
            summary, warnings = es._sub_execution_summary(dict(PIPELINE_SUB))
        sagemaker.describe_pipeline_execution.assert_called_once_with(
            PipelineExecutionArn=PIPELINE_EXECUTION_ARN)
        assert summary["status"] == mapped and warnings == []
        assert summary["resourceName"] == "nightly"
        assert summary["sageMaker"] == {"pipelineExecutionArn": PIPELINE_EXECUTION_ARN}
        assert (summary["stopDate"] != "") is (mapped in ("SUCCEEDED", "FAILED", "ABORTED"))
        assert "PipelineParameters" not in json.dumps(es._sagemaker_describe_cache)

    def test_a_describe_failure_is_a_warning_not_an_error(self):
        sagemaker = MagicMock(describe_training_job=MagicMock(
            side_effect=_client_error("AccessDeniedException", operation="DescribeTrainingJob")))
        with patch.object(es, "sagemaker_client", sagemaker):
            summary, warnings = es._sub_execution_summary(dict(TRAINING_SUB))
            # Memoised: the second summary in the same request does not ask again.
            es._sub_execution_summary(dict(TRAINING_SUB))
        assert summary["status"] == ses.STATUS_UNKNOWN
        assert warnings == [f"SageMaker training job status unavailable for {TRAINING_JOB_NAME}: "
                            "AccessDeniedException"]
        sagemaker.describe_training_job.assert_called_once()

    def test_an_absent_client_is_a_named_warning(self):
        with patch.object(es, "sagemaker_client", None):
            summary, warnings = es._sub_execution_summary(dict(PIPELINE_SUB))
        assert summary["status"] == ses.STATUS_UNKNOWN
        assert warnings == ["SageMaker pipeline execution status unavailable for a1b2c3d4e5f6: "
                            "SageMaker client unavailable"]

    def test_the_training_job_name_parser(self):
        assert ses.sagemaker_training_job_name_from_arn(TRAINING_JOB_ARN) == TRAINING_JOB_NAME
        assert ses.sagemaker_training_job_name_from_arn("") == ""
        assert ses.sagemaker_training_job_name_from_arn(
            f"arn:aws:sagemaker:us-east-1:{ACCOUNT}:model/x") == ""
        assert ses.sagemaker_training_job_name_from_arn(
            f"arn:aws:sagemaker:us-east-1:{ACCOUNT}:training-job/") == ""
