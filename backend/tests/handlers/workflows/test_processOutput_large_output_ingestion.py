# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The output step's three modes for outputs over the async threshold (#1123, fixes #1126, #1127).

- ingest with no large outputs is today's path: no batch, no wait, recorded at once;
- ingest with large outputs hands them to copy jobs, skips their provenance self-copy, defers their
  file-level metadata, records the batch, puts the run in INGESTING_OUTPUTS and does NOT record it
  terminal (the shipped bug #1126 recorded SUCCEEDED here);
- a waiting definition gets outputIngestionPending; one that does not wait is finalized by the copy
  engine, or at once when every copy already finished;
- await registers the task token and reports at once when the copies are done;
- finalize records the outcome: a failed copy names its file and the run ends FAILED, the files
  that landed keep their rows, and deferred file metadata is applied only to files that landed
  (the shipped bug #1127 applied it before the file existed)."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

for _k, _v in {
    "WORKFLOW_STORAGE_TABLE_V2_NAME": "t-wf-v2",
    "WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME": "t-inputs",
    "WORKFLOW_EXECUTION_LOCKS_STORAGE_TABLE_NAME": "t-locks",
    "WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME": "t-config",
    "LARGE_OBJECT_COPY_JOBS_STORAGE_TABLE_NAME": "t-copy-jobs",
}.items():
    os.environ.setdefault(_k, _v)

from backend.tests.copyEngineRouting import route_to_engine  # noqa: E402
from backend.tests.handlers.workflows import test_processOutput_ingestion_outcome as base  # noqa: E402

po = base.po
loc = po.loc
GIB = 1024 ** 3
FILES_PREFIX = base.FILES_PREFIX
METADATA_PREFIX = base.METADATA_PREFIX

SMALL = {"Key": FILES_PREFIX + "small.glb", "Size": 7}
LARGE = {"Key": FILES_PREFIX + "big/cloud.e57", "Size": 3 * GIB}
LISTING = {"Contents": [dict(SMALL), dict(LARGE)]}
MD_LISTING = {"Contents": [
    {"Key": METADATA_PREFIX + "small.glb.metadata.json", "Size": 5},
    {"Key": METADATA_PREFIX + "big/cloud.e57.metadata.json", "Size": 5},
    {"Key": METADATA_PREFIX + "big/cloud.e57.attribute.json", "Size": 5},
]}
APPLIED = [{"metadataKey": "k", "metadataValue": "v"}]


def _ingestion_result(large_ok=True):
    results = [{"relativeKey": "small.glb", "success": True}]
    if large_ok:
        results.append({"relativeKey": "big/cloud.e57", "success": True,
                        "largeFileAsynchronousHandling": True, "ingestionJobId": "E1#abc"})
    else:
        results.append({"relativeKey": "big/cloud.e57", "success": False,
                        "error": loc.REASON_START_FAILED})
    return {"overallSuccess": large_ok, "fileResults": results}


def _listing(bucket, prefix):
    return MD_LISTING if prefix == METADATA_PREFIX else LISTING


def _ingest(event_overrides=None, ingestion=None, claim=None):
    """Run the ingest mode with a file listing (one small, one large output) and file metadata for
    both. Returns (response, mocks)."""
    lookup, bucket, logs = base._asset_patches()
    mocks = {}
    with lookup, bucket, logs, \
            patch.object(po, "verify_get_path_objects", side_effect=_listing), \
            patch.object(po, "create_external_upload_record", return_value="upl-1"), \
            patch.object(po, "_stamp_output_objects", return_value=True) as mocks["stamp"], \
            patch.object(po, "process_external_upload",
                         return_value=ingestion or _ingestion_result()) as mocks["upload"], \
            patch.object(po, "process_metadata_file", return_value=APPLIED) as mocks["metadata"], \
            patch.object(po, "record_execution_outputs") as mocks["record"], \
            route_to_engine(po, MagicMock()), \
            patch.object(po, "_mark_ingesting", return_value=True) as mocks["mark"], \
            patch.object(po.loc, "record_batch") as mocks["batch"], \
            patch.object(po.loc, "claim_notification", return_value=claim) as mocks["claim"], \
            patch.object(po, "_finalize_ingestion") as mocks["finalize"], \
            patch.object(po, "s3c") as mocks["s3"]:
        event = base._event(filesPathKey=FILES_PREFIX, metadataPathKey=METADATA_PREFIX,
                            **(event_overrides or {}))
        resp = po.lambda_handler(event, MagicMock())
    return resp, mocks


@pytest.mark.unit
class TestIngestWithoutLargeOutputsIsUnchanged:
    def test_small_outputs_record_at_once_and_ask_for_no_wait(self):
        listing = {"Contents": [dict(SMALL)]}
        lookup, bucket, logs = base._asset_patches()
        with lookup, bucket, logs, \
                patch.object(po, "verify_get_path_objects", return_value=listing), \
                patch.object(po, "create_external_upload_record", return_value="upl-1"), \
                patch.object(po, "_stamp_output_objects", return_value=True) as stamp, \
                patch.object(po, "process_external_upload",
                             return_value={"overallSuccess": True, "fileResults": [
                                 {"relativeKey": "small.glb", "success": True}]}), \
                patch.object(po, "record_execution_outputs") as record, \
                patch.object(po.loc, "record_batch") as batch:
            resp = po.lambda_handler(base._event(filesPathKey=FILES_PREFIX, awaitOutputIngestion=True),
                                     MagicMock())
        assert resp["statusCode"] == 200
        assert resp["outputIngestionPending"] is False
        assert record.call_args.kwargs["execution_status"] == "SUCCEEDED"
        batch.assert_not_called()
        assert [o["Key"] for o in stamp.call_args.args[0]] == [SMALL["Key"]]

    def test_a_direct_invocation_without_an_execution_keeps_the_queue_path(self):
        lookup, bucket, logs = base._asset_patches()
        with lookup, bucket, logs, \
                patch.object(po, "verify_get_path_objects", return_value=LISTING), \
                patch.object(po, "create_external_upload_record", return_value="upl-1"), \
                patch.object(po, "_stamp_output_objects", return_value=True) as stamp, \
                patch.object(po, "process_external_upload",
                             return_value={"overallSuccess": True}) as upload, \
                patch.object(po, "record_execution_outputs"):
            po.lambda_handler(base._event(filesPathKey=FILES_PREFIX, workflowExecutionId=""),
                              MagicMock())
        assert upload.call_args.kwargs["ingestion_batch_id"] == ""
        # No batch to hand large files to, so every object is stamped as before.
        assert len(stamp.call_args.args[0]) == 2


@pytest.mark.unit
class TestIngestWithLargeOutputsWaits:
    def test_the_run_is_not_recorded_terminal_while_copies_run(self):
        resp, m = _ingest({"awaitOutputIngestion": True})
        m["record"].assert_not_called()  # #1126: no SUCCEEDED before the outputs landed
        assert resp["outputIngestionPending"] is True
        assert resp["batchId"] == "E1"
        m["mark"].assert_called_once()
        assert m["mark"].call_args.args[1:] == (1, 3 * GIB, 0, 0)

    def test_the_large_output_is_not_self_copied_to_stamp_it(self):
        _resp, m = _ingest({"awaitOutputIngestion": True})
        assert [o["Key"] for o in m["stamp"].call_args.args[0]] == [SMALL["Key"]]

    def test_ingestion_is_asked_to_hand_large_files_to_the_batch(self):
        _resp, m = _ingest({"awaitOutputIngestion": True})
        assert m["upload"].call_args.kwargs["ingestion_batch_id"] == "E1"
        assert m["upload"].call_args.kwargs["workflow_database_id"] == "wdb"

    def test_file_metadata_of_the_large_output_is_deferred(self):
        _resp, m = _ingest({"awaitOutputIngestion": True})
        # #1127: only the landed file's metadata is applied now.
        assert [c.args[5] for c in m["metadata"].call_args_list] == ["small.glb"]
        state = json.loads(m["s3"].put_object.call_args.kwargs["Body"])
        assert {(d["filePath"], d["type"]) for d in state["deferredMetadata"]} == {
            ("big/cloud.e57", "metadata"), ("big/cloud.e57", "attribute")}
        assert state["pendingJobs"] == {"big/cloud.e57": "E1#abc"}

    def test_the_finalize_state_is_saved_under_the_execution_input_folder(self):
        _resp, m = _ingest({"awaitOutputIngestion": True,
                            "workflowExecutionS3InputOutputBasePrefix": "vams/"})
        kwargs = m["s3"].put_object.call_args.kwargs
        assert kwargs["Bucket"] == base.RUN_BUCKET
        assert kwargs["Key"] == "vams/pipelines/workflowExecutionInputs/E1/" + po.INGESTION_STATE_FILENAME
        context = m["batch"].call_args.args[3]
        assert context["stateKey"] == kwargs["Key"] and context["legacy"] is False
        assert m["batch"].call_args.args[1:3] == ("E1", ["E1#abc"])

    def test_a_definition_that_does_not_wait_is_left_to_the_copy_engine(self):
        resp, m = _ingest()
        assert resp["outputIngestionPending"] is False
        assert m["batch"].call_args.args[3]["legacy"] is True
        m["record"].assert_not_called()
        m["finalize"].assert_not_called()

    def test_a_definition_that_does_not_wait_finalizes_at_once_when_copies_are_done(self):
        _resp, m = _ingest(claim={"jobId": "batch#E1"})
        m["finalize"].assert_called_once()

    def test_a_copy_that_could_not_start_fails_the_run_without_waiting(self):
        resp, m = _ingest({"awaitOutputIngestion": True}, ingestion=_ingestion_result(large_ok=False))
        assert resp["outputIngestionPending"] is False
        assert m["record"].call_args.kwargs["execution_status"] == "FAILED"
        m["batch"].assert_not_called()

    def test_a_run_ended_meanwhile_cancels_the_copies_it_started(self):
        calls = []
        lookup, bucket, logs = base._asset_patches()
        with lookup, bucket, logs, \
                patch.object(po, "verify_get_path_objects", side_effect=_listing), \
                patch.object(po, "create_external_upload_record", return_value="upl-1"), \
                patch.object(po, "_stamp_output_objects", return_value=True), \
                patch.object(po, "process_external_upload", return_value=_ingestion_result()), \
                patch.object(po, "process_metadata_file", return_value=APPLIED), \
                patch.object(po, "record_execution_outputs") as record, \
                route_to_engine(po, MagicMock(), calls=calls), \
                patch.object(po, "_mark_ingesting", return_value=False), \
                patch.object(po.loc, "record_batch") as batch, \
                patch.object(po.loc, "cancel_execution_copy_jobs", return_value=[]) as cancel, \
                patch.object(po, "s3c"):
            resp = po.lambda_handler(base._event(filesPathKey=FILES_PREFIX, awaitOutputIngestion=True),
                                     MagicMock())
            from backend.tests.copyEngineRouting import engine
            engine_lambda_client = engine.lambda_client
        # The output step asks the copy engine to cancel; the engine stops the copies with its own
        # role and hands the stops to its own reconcile (#1123 M11 round 6): its Lambda client and
        # its own function name.
        # (It first folds in any copies an earlier attempt recorded, also through the engine.)
        assert [op for op, _ in calls] == [po.loc.OUTPUT_STEP_RECORDED_JOBS, po.loc.OUTPUT_STEP_CANCEL]
        assert calls[1][1] == {"workflowExecutionId": base._event()["body"]["workflowExecutionId"]}
        cancel.assert_called_once()
        assert cancel.call_args.args[4] is engine_lambda_client
        assert cancel.call_args.args[5] == "largeObjectCopyEngine"
        batch.assert_not_called()
        record.assert_not_called()
        assert resp["outputIngestionPending"] is False


def _state(**overrides):
    state = {
        "event": {"workflowExecutionId": "E1", "workflowDatabaseId": "wdb", "workflowId": "wf1",
                  "endStatePipelineExecutionId": "P1", "priorPipelineExecutionIds": ["P1"],
                  "databaseId": "db1", "assetId": "asset1.glb", "metadataPathKey": METADATA_PREFIX},
        "bucketName": base.ASSET_BUCKET,
        "sourceBucket": base.RUN_BUCKET,
        "collectedOutputFiles": [
            {"fileType": "file", "relativeFilePath": "small.glb", "s3Key": SMALL["Key"]},
            {"fileType": "file", "relativeFilePath": "big/cloud.e57", "s3Key": LARGE["Key"]},
        ],
        "collectedOutputMetadata": [],
        "collectedOutputResults": [],
        "outputFailures": [],
        "pendingJobs": {"big/cloud.e57": "E1#abc"},
        "deferredMetadata": [
            {"key": METADATA_PREFIX + "big/cloud.e57.metadata.json", "filePath": "big/cloud.e57",
             "type": "metadata"}],
    }
    state.update(overrides)
    return state


def _finalize(outcome, state=None):
    batch = {"jobId": "batch#E1", "context": {"workflowExecutionId": "E1", "stateBucket": base.RUN_BUCKET,
                                              "stateKey": "k"}}
    s3 = MagicMock()
    s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(state or _state()).encode())}
    with route_to_engine(po, MagicMock()), \
            patch.object(po.loc, "get_batch", return_value=batch), \
            patch.object(po.loc, "batch_outcomes", return_value=outcome), \
            patch.object(po, "s3c", s3), \
            patch.object(po, "process_metadata_file", return_value=APPLIED) as metadata, \
            patch.object(po, "_record_and_finish") as finish:
        result = po.lambda_handler({"body": {"mode": "finalize", "batchId": "E1",
                                             "workflowExecutionId": "E1"}}, MagicMock())
    return result, finish.call_args.args, metadata


@pytest.mark.unit
class TestFinalize:
    def test_a_landed_large_output_keeps_its_row_and_gets_its_metadata(self):
        result, (event, bucket, files, metadata_rows, _results, failures), metadata = _finalize(
            {"big/cloud.e57": {"status": "SUCCEEDED", "destVersionId": "v9"}})
        assert result["failedFiles"] == 0
        assert failures == []
        assert {f["relativeFilePath"] for f in files} == {"small.glb", "big/cloud.e57"}
        big = next(f for f in files if f["relativeFilePath"] == "big/cloud.e57")
        assert big["ingestionStatus"] == "SUCCEEDED"
        assert metadata.call_args.args[5] == "big/cloud.e57"
        assert {r["targetFilePath"] for r in metadata_rows} == {"/big/cloud.e57"}
        assert event["workflowExecutionId"] == "E1" and bucket == base.ASSET_BUCKET

    def test_a_failed_copy_names_the_file_and_drops_its_row(self):
        _result, (_e, _b, files, metadata_rows, _r, failures), metadata = _finalize(
            {"big/cloud.e57": {"status": "FAILED", "error": loc.REASON_SOURCE_CHANGED}})
        assert [f["relativeFilePath"] for f in files] == ["small.glb"]
        assert failures == [f"Ingesting output file 'big/cloud.e57' into the asset failed: "
                            f"{loc.REASON_SOURCE_CHANGED}"]
        assert po._terminal_status(failures)[0] == "FAILED"
        metadata.assert_not_called()  # no metadata for a file that never landed
        assert metadata_rows == []

    def test_a_copy_that_never_reported_is_a_failure(self):
        _result, (_e, _b, _f, _m, _r, failures), _md = _finalize({})
        assert failures == [f"Ingesting output file 'big/cloud.e57' into the asset failed: "
                            f"{loc.REASON_NOT_REPORTED}"]

    def test_phase_one_failures_are_kept_ahead_of_ingestion_failures(self):
        state = _state(outputFailures=["PipelineReportedFailure: boom"])
        _result, (_e, _b, _f, _m, _r, failures), _md = _finalize(
            {"big/cloud.e57": {"status": "FAILED", "error": "x"}}, state)
        assert failures[0] == "PipelineReportedFailure: boom"

    def test_a_missing_batch_raises_for_the_error_handler(self):
        with route_to_engine(po, MagicMock()), \
                patch.object(po.loc, "get_batch", return_value={}):
            with pytest.raises(Exception):
                po.lambda_handler({"body": {"mode": "finalize", "batchId": "E1"}}, MagicMock())


EARLIER_JOB = {"jobId": "E1#aaa", "batchId": "E1", "relativeKey": "big/early.e57", "status": "SUCCEEDED",
               "counted": True, "sourceBucket": base.RUN_BUCKET, "sourceKey": FILES_PREFIX + "big/early.e57",
               "sourceSize": 2 * GIB, "sourceVersionId": "sv1"}


@pytest.mark.unit
class TestRetriedOutputStep:
    """[in-dev D5] a retried output step no longer lists a file whose copy already finished (the copy
    deleted its staged source). Its job is folded back in, so the batch waits for every copy and
    finalize records every file."""

    def test_an_earlier_attempt_s_finished_job_is_folded_into_the_batch(self):
        prior = _state(collectedOutputFiles=[{"fileType": "file", "relativeFilePath": "big/early.e57",
                                               "s3Key": EARLIER_JOB["sourceKey"], "contentType": "x/e57"}])
        with route_to_engine(po, MagicMock()), \
                patch.object(po.loc, "recorded_batch_jobs",
                             return_value={"E1#aaa": EARLIER_JOB, "E1#abc": {
                                 "jobId": "E1#abc", "batchId": "E1", "relativeKey": "big/cloud.e57",
                                 "status": "RUNNING", "sourceSize": 3 * GIB}}), \
                patch.object(po, "_read_ingestion_state", return_value=prior):
            pending, files, pending_bytes, done, done_bytes = po._merge_earlier_attempts(
                "E1", base.RUN_BUCKET, "k", {"big/cloud.e57": "E1#abc"},
                [{"fileType": "file", "relativeFilePath": "big/cloud.e57"}], 3 * GIB)
        assert pending == {"big/cloud.e57": "E1#abc", "big/early.e57": "E1#aaa"}
        assert {f["relativeFilePath"] for f in files} == {"big/cloud.e57", "big/early.e57"}
        assert next(f for f in files if f["relativeFilePath"] == "big/early.e57")["contentType"] == "x/e57"
        assert pending_bytes == 5 * GIB
        assert (done, done_bytes) == (1, 2 * GIB)

    def test_the_batch_records_every_attempt_s_jobs_and_the_progress_counts_them(self):
        with patch.object(po.loc, "recorded_batch_jobs", return_value={"E1#aaa": EARLIER_JOB}), \
                patch.object(po, "_read_ingestion_state", return_value={}):
            _resp, m = _ingest({"awaitOutputIngestion": True, "outputStepAttempt": 1})
        assert sorted(m["batch"].call_args.args[2]) == ["E1#aaa", "E1#abc"]
        assert m["mark"].call_args.args[1:] == (2, 5 * GIB, 1, 2 * GIB)

    def test_a_retry_whose_copies_all_finished_still_waits_and_finalizes(self):
        listing = {"Contents": [dict(SMALL)]}
        lookup, bucket, logs = base._asset_patches()
        with lookup, bucket, logs, \
                patch.object(po, "verify_get_path_objects", return_value=listing), \
                patch.object(po, "create_external_upload_record", return_value="upl-1"), \
                patch.object(po, "_stamp_output_objects", return_value=True), \
                patch.object(po, "process_external_upload", return_value={
                    "overallSuccess": True, "fileResults": [{"relativeKey": "small.glb", "success": True}]}), \
                patch.object(po, "record_execution_outputs") as record, \
                route_to_engine(po, MagicMock()), \
                patch.object(po.loc, "recorded_batch_jobs", return_value={"E1#aaa": EARLIER_JOB}), \
                patch.object(po, "_read_ingestion_state", return_value={}), \
                patch.object(po, "_mark_ingesting", return_value=True), \
                patch.object(po.loc, "record_batch") as batch, \
                patch.object(po, "s3c"):
            resp = po.lambda_handler(base._event(filesPathKey=FILES_PREFIX, awaitOutputIngestion=True,
                                                 outputStepAttempt=1), MagicMock())
        record.assert_not_called()  # not recorded SUCCEEDED without the copied file's row
        assert resp["outputIngestionPending"] is True
        assert batch.call_args.args[2] == ["E1#aaa"]

    def test_a_first_attempt_does_not_look_for_earlier_jobs(self):
        with patch.object(po.loc, "recorded_batch_jobs") as recorded:
            assert po._earlier_attempt_started_copies(
                {"workflowExecutionId": "E1", "awaitOutputIngestion": True, "outputStepAttempt": 0}) is False
        recorded.assert_not_called()

    def test_finalize_records_a_copied_file_no_attempt_described(self):
        _result, (_e, _b, files, _m, _r, failures), _metadata = _finalize(
            {"big/cloud.e57": {"status": "SUCCEEDED"}, "big/early.e57": EARLIER_JOB})
        assert failures == []
        early = next(f for f in files if f["relativeFilePath"] == "big/early.e57")
        assert early["s3Key"] == EARLIER_JOB["sourceKey"]
        assert early["s3VersionId"] == "sv1"
        assert early["ingestionStatus"] == "SUCCEEDED"

    def test_finalize_fails_the_run_for_a_recorded_job_that_failed(self):
        failed = {**EARLIER_JOB, "status": "FAILED", "error": loc.REASON_COPY_FAILED}
        _result, (_e, _b, files, _m, _r, failures), _metadata = _finalize(
            {"big/cloud.e57": {"status": "SUCCEEDED"}, "big/early.e57": failed})
        assert "big/early.e57" not in {f["relativeFilePath"] for f in files}
        assert failures == [f"Ingesting output file 'big/early.e57' into the asset failed: "
                            f"{loc.REASON_COPY_FAILED}"]


@pytest.mark.unit
class TestAwait:
    def test_await_registers_the_token_and_reports_when_copies_are_done(self):
        with route_to_engine(po, MagicMock()), \
                patch.object(po.loc, "register_task_token") as register, \
                patch.object(po.loc, "claim_notification", return_value={"jobId": "batch#E1",
                                                                         "taskToken": "t"}), \
                patch.object(po.loc, "deliver_batch_report") as deliver:
            po.lambda_handler({"body": {"mode": "await", "batchId": "E1", "taskToken": "t"}}, MagicMock())
        assert register.call_args.args[1:] == ("E1", "t")
        deliver.assert_called_once()

    def test_await_with_copies_still_running_only_registers(self):
        with route_to_engine(po, MagicMock()), \
                patch.object(po.loc, "register_task_token"), \
                patch.object(po.loc, "claim_notification", return_value=None), \
                patch.object(po.loc, "deliver_batch_report") as deliver:
            po.lambda_handler({"body": {"mode": "await", "batchId": "E1", "taskToken": "t"}}, MagicMock())
        deliver.assert_not_called()

    def test_await_without_a_token_raises(self):
        with pytest.raises(Exception):
            po.lambda_handler({"body": {"mode": "await", "batchId": "E1"}}, MagicMock())


@pytest.mark.unit
class TestMainRowPhase:
    def test_the_phase_is_written_under_the_terminal_guard(self):
        table = MagicMock()
        with patch.object(po.dynamodb, "Table", return_value=table):
            assert po._mark_ingesting({"workflowExecutionId": "E1", "workflowDatabaseId": "wdb",
                                       "workflowId": "wf1"}, 2, 10) is True
        kwargs = table.update_item.call_args.kwargs
        assert "NOT executionStatus IN" in kwargs["ConditionExpression"]
        assert kwargs["ExpressionAttributeValues"][":p"] == "INGESTING_OUTPUTS"
        assert kwargs["ExpressionAttributeValues"][":oi"]["filesTotal"] == 2

    def test_the_completion_write_leaves_the_phase(self):
        updates = []
        table = MagicMock()
        table.update_item.side_effect = lambda **kw: updates.append(kw) or {}
        dynamo = MagicMock(Table=MagicMock(return_value=table))
        po.workflow_execution_database_v2 = "t-exec-v2"
        with patch.object(po.el, "release_locks_for_execution"):
            po.record_execution_outputs(
                dynamo=dynamo, workflow_execution_id="E1", end_state_pipeline_execution_id="P1",
                workflow_database_id="wdb", workflow_id="wf1", bucket_name="b", output_files=[],
                output_metadata=[], output_results=[], result_log="", execution_log="",
                log_group_arn="", log_stream_name="", execution_status="SUCCEEDED")
        main = [u for u in updates if "executionStopDate" in u.get("UpdateExpression", "")]
        assert main and main[-1]["UpdateExpression"].endswith("REMOVE executionPhase, outputIngestion")


@pytest.mark.unit
class TestAuditRecords:
    """Audit contract: one workflowOutputIngest and one workflowExecutionEnd per run, written for the
    entrypoint's own event. A run whose large outputs were handed to copy jobs records both when it is
    finalized, never at the hand-off."""

    def _actions(self, recorder):
        return [c.args[1] for c in recorder.call_args_list]

    def test_the_hand_off_records_nothing(self):
        with patch.object(po, "log_action") as recorder:
            _ingest({"awaitOutputIngestion": True})
        assert self._actions(recorder) == []

    def test_a_run_without_large_outputs_records_its_ingest_and_end_at_once(self):
        listing = {"Contents": [dict(SMALL)]}
        lookup, bucket, logs = base._asset_patches()
        with lookup, bucket, logs, patch.object(po, "log_action") as recorder, \
                patch.object(po, "verify_get_path_objects", return_value=listing), \
                patch.object(po, "create_external_upload_record", return_value="upl-1"), \
                patch.object(po, "_stamp_output_objects", return_value=True), \
                patch.object(po, "process_external_upload",
                             return_value={"overallSuccess": True, "fileResults": [
                                 {"relativeKey": "small.glb", "success": True}]}), \
                patch.object(po, "record_execution_outputs"):
            event = base._event(filesPathKey=FILES_PREFIX, awaitOutputIngestion=True)
            po.lambda_handler(event, MagicMock())
        assert self._actions(recorder) == ["workflowOutputIngest", "workflowExecutionEnd"]
        # Both records carry the entrypoint's own event (a list, so zero calls cannot pass).
        assert [c.args[0] is event for c in recorder.call_args_list] == [True, True]

    def test_finalize_records_the_ingest_and_the_end_for_its_own_event(self):
        batch = {"jobId": "batch#E1", "context": {"workflowExecutionId": "E1", "stateBucket": base.RUN_BUCKET,
                                                  "stateKey": "k"}}
        state = _state()
        state["event"]["executingUserName"] = "alice"
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(state).encode())}
        event = {"body": {"mode": "finalize", "batchId": "E1", "workflowExecutionId": "E1"}}
        with route_to_engine(po, MagicMock()), patch.object(po, "log_action") as recorder, \
                patch.object(po.loc, "get_batch", return_value=batch), \
                patch.object(po.loc, "batch_outcomes",
                             return_value={"big/cloud.e57": {"status": "FAILED", "error": "x"}}), \
                patch.object(po, "s3c", s3), \
                patch.object(po, "process_metadata_file", return_value=APPLIED), \
                patch.object(po, "record_execution_outputs"), \
                patch.object(po, "_fetch_execution_logs", return_value=("", "")), \
                patch.object(po, "_execution_start_date", return_value=""), \
                patch.object(po, "_refresh_execution_log"):
            po.lambda_handler(event, MagicMock())
        assert self._actions(recorder) == ["workflowOutputIngest", "workflowExecutionEnd"]
        ingest, end = recorder.call_args_list
        assert ingest.args[0] is event and end.args[0] is event
        assert ingest.kwargs["outcome"] == "failed" and ingest.kwargs["file_count"] == 1
        assert end.kwargs["on_behalf_of"] == "alice" and end.kwargs["executionId"] == "E1"
