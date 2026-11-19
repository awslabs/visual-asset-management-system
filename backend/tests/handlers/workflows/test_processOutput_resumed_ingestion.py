# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A run whose large outputs went to the copy engine (#1141, #1123) ends like any other run (#316).

Such a run ends from the output step's finalize mode, after its waiting state or when the copy engine
invokes it. The end-of-run order of the ordinary path holds there too:

- while it waits (the ingest hand-off and the await mode) the run releases no lock or slot and
  announces nothing, so it keeps its concurrency locks and its system slot until it ends;
- finalize writes the terminal status, then releases the run's locks (one release through the holder
  index, which also frees a system slot), then publishes `workflow.execution.completed`, once; a second
  finalize (the engine's invoke delivered again) whose terminal write lost announces nothing;
- the deferred file metadata of a landed large output is written as the execution's
  (`lambdaCrossCall.workflowExecutionId`), as on the ingest path;
- a SYSTEM pipeline's metadata-only output (many small metadata files, no file over the async
  threshold) takes the ordinary path: no batch, no wait, recorded at once, every write the run's.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.tests.copyEngineRouting import route_to_engine
from backend.tests.handlers.workflows import test_processOutput_large_output_ingestion as large

po = large.po
base = large.base
METADATA_PREFIX = large.METADATA_PREFIX
ANNOUNCEMENT = {"workflow_execution_id": "E1", "workflow_database_id": "wdb", "workflow_id": "wf1",
                "execution_status": "SUCCEEDED", "started_at": "2026-01-01T00:00:00Z",
                "completed_at": "2026-01-01T01:00:00Z"}
FINALIZE = {"body": {"mode": "finalize", "batchId": "E1", "workflowExecutionId": "E1"}}


def _end_of_run_recorder(order, announcement):
    """Patches for the end of a run, each appending its step to `order`: the terminal write (returns
    `announcement`, None when another writer already ended the run), the lock release and the event."""
    return (
        patch.object(po, "_write_execution_outputs",
                     side_effect=lambda *a, **k: order.append("write") or announcement),
        patch.object(po.el, "release_locks_for_execution",
                     side_effect=lambda *a, **k: order.append("release") or 1),
        patch.object(po, "emit_workflow_execution_completed",
                     side_effect=lambda **k: order.append("emit")),
    )


def _finalize(announcement=ANNOUNCEMENT, outcome=None):
    """Run the finalize mode for real down to the row writes: the state the hand-off saved, the batch
    outcome from the copy engine, the shared recording and the end-of-run steps."""
    order = []
    batch = {"jobId": "batch#E1", "context": {"workflowExecutionId": "E1", "stateBucket": base.RUN_BUCKET,
                                              "stateKey": "k"}}
    s3 = MagicMock()
    s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(large._state()).encode())}
    write, release, emit = _end_of_run_recorder(order, announcement)
    with route_to_engine(po, MagicMock()), \
            patch.object(po.loc, "get_batch", return_value=batch), \
            patch.object(po.loc, "batch_outcomes", return_value=outcome or {
                "big/cloud.e57": {"status": "SUCCEEDED", "destVersionId": "v9"}}), \
            patch.object(po, "s3c", s3), \
            patch.object(po, "process_metadata_file", return_value=large.APPLIED) as metadata, \
            patch.object(po, "_settle_engine_rows"), \
            patch.object(po.eo, "recorded_output_versions", return_value={}), \
            patch.object(po, "_fetch_execution_logs", return_value=("", "")), \
            patch.object(po, "_execution_start_date", return_value=""), \
            patch.object(po, "_refresh_execution_log"), \
            write as write_mock, release as release_mock, emit as emit_mock:
        result = po.lambda_handler(json.loads(json.dumps(FINALIZE)), MagicMock())
    return result, order, write_mock, release_mock, emit_mock, metadata


@pytest.mark.unit
class TestWhileTheRunWaits:
    def test_the_hand_off_releases_nothing_and_announces_nothing(self):
        order = []
        write, release, emit = _end_of_run_recorder(order, ANNOUNCEMENT)
        with write, release as release_mock, emit as emit_mock:
            resp, mocks = large._ingest({"awaitOutputIngestion": True})
        assert resp["outputIngestionPending"] is True
        mocks["record"].assert_not_called()
        release_mock.assert_not_called()
        emit_mock.assert_not_called()

    def test_a_definition_that_does_not_wait_also_keeps_its_locks_until_the_engine_finalizes(self):
        order = []
        write, release, emit = _end_of_run_recorder(order, ANNOUNCEMENT)
        with write, release as release_mock, emit as emit_mock:
            resp, mocks = large._ingest()
        assert resp["outputIngestionPending"] is False
        mocks["finalize"].assert_not_called()
        release_mock.assert_not_called()
        emit_mock.assert_not_called()

    def test_the_await_mode_releases_nothing_and_announces_nothing(self):
        order = []
        write, release, emit = _end_of_run_recorder(order, ANNOUNCEMENT)
        with route_to_engine(po, MagicMock()), \
                patch.object(po.loc, "register_task_token"), \
                patch.object(po.loc, "claim_notification", return_value=None), \
                write, release as release_mock, emit as emit_mock:
            po.lambda_handler({"body": {"mode": "await", "batchId": "E1", "taskToken": "t"}}, MagicMock())
        release_mock.assert_not_called()
        emit_mock.assert_not_called()


@pytest.mark.unit
class TestFinalizeEndsTheRunInOrder:
    def test_the_status_is_written_then_the_locks_released_then_the_run_announced_once(self):
        result, order, _write, release, emit, _metadata = _finalize()
        assert result["finalized"] is True
        assert order == ["write", "release", "emit"]
        assert release.call_args.kwargs["workflow_execution_id"] == "E1"
        assert (release.call_args.kwargs["workflow_database_id"],
                release.call_args.kwargs["workflow_id"]) == ("wdb", "wf1")
        assert emit.call_args.kwargs == ANNOUNCEMENT

    def test_a_failed_copy_ends_the_run_failed_in_the_same_order(self):
        result, order, write, _release, _emit, _metadata = _finalize(
            outcome={"big/cloud.e57": {"status": "FAILED", "error": "x"}})
        assert result["failedFiles"] == 1
        assert order == ["write", "release", "emit"]
        assert write.call_args.args[13] == "FAILED"  # execution_status

    def test_a_second_finalize_whose_write_lost_releases_again_and_announces_nothing(self):
        # The copy engine's finalize invoke delivered again, or an overlap with the error path: the
        # terminal guard refuses this write, the release is a holder-conditional no-op, and the run is
        # not announced a second time.
        _result, order, _write, release, emit, _metadata = _finalize(announcement=None)
        assert order == ["write", "release"]
        release.assert_called_once()
        emit.assert_not_called()

    def test_the_locks_are_released_even_when_the_terminal_write_raises(self):
        order = []
        _unused_write, release, emit = _end_of_run_recorder(order, ANNOUNCEMENT)
        with patch.object(po, "_write_execution_outputs", side_effect=RuntimeError("table down")), \
                release as release_mock, emit as emit_mock:
            with pytest.raises(RuntimeError):
                po.record_execution_outputs(
                    dynamo=MagicMock(), workflow_execution_id="E1", end_state_pipeline_execution_id="P1",
                    workflow_database_id="wdb", workflow_id="wf1", bucket_name="b", output_files=[],
                    output_metadata=[], output_results=[], result_log="", execution_log="",
                    log_group_arn="", log_stream_name="", execution_status="SUCCEEDED")
        release_mock.assert_called_once()
        emit_mock.assert_not_called()


@pytest.mark.unit
class TestFinalizeWriteBackIsTheExecutions:
    def test_deferred_metadata_of_a_landed_output_is_written_as_the_execution(self):
        _result, _order, _write, _release, _emit, metadata = _finalize()
        metadata.assert_called_once()
        assert metadata.call_args.args[5] == "big/cloud.e57"
        assert metadata.call_args.kwargs.get("workflow_execution_id") == "E1"

    def test_the_cross_call_carries_the_execution_id(self):
        """process_metadata_file itself puts the id on lambdaCrossCall, which the metadata service
        reads to record the write as the run's."""
        sent = []
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": MagicMock(read=lambda: json.dumps(
            {"type": "metadata", "metadata": [{"metadataKey": "k", "metadataValueType": "string",
                                               "metadataValue": "v"}]}).encode())}
        answer = {"Payload": MagicMock(read=lambda: json.dumps(
            {"statusCode": 200, "body": json.dumps({"message": "ok"})}).encode())}
        with patch.object(po, "s3c", s3), \
                patch.object(po, "_lambda_metadata_service", side_effect=lambda e: sent.append(e) or answer):
            po.process_metadata_file("b", "k.metadata.json", "", "db1", "a1", "big/cloud.e57", "metadata",
                                     None, workflow_execution_id="E1")
        assert sent[0]["lambdaCrossCall"] == {"userName": "SYSTEM_USER", "workflowExecutionId": "E1"}


def _system_metadata_listing(count):
    """A SYSTEM GenAI metadata run's output: per-file metadata and attribute JSON, all small."""
    contents = []
    for i in range(count):
        contents.append({"Key": f"{METADATA_PREFIX}scan/part{i}.glb.metadata.json", "Size": 900})
        contents.append({"Key": f"{METADATA_PREFIX}scan/part{i}.glb.attribute.json", "Size": 300})
    return {"Contents": contents}


@pytest.mark.unit
class TestSystemPipelineMetadataTakesTheOrdinaryPath:
    def test_many_small_metadata_files_are_recorded_at_once_without_a_batch(self):
        listing = _system_metadata_listing(60)
        lookup, bucket, logs = base._asset_patches()
        with lookup, bucket, logs, \
                patch.object(po, "verify_get_path_objects",
                             side_effect=lambda b, prefix: listing if prefix == METADATA_PREFIX else {}), \
                patch.object(po, "process_external_upload") as upload, \
                patch.object(po, "process_metadata_file", return_value=large.APPLIED) as metadata, \
                patch.object(po, "record_execution_outputs", return_value=True) as record, \
                patch.object(po, "_mark_ingesting") as mark, \
                patch.object(po.loc, "record_batch") as batch, \
                patch.object(po, "_engine") as engine_call:
            resp = po.lambda_handler(base._event(metadataPathKey=METADATA_PREFIX, awaitOutputIngestion=True),
                                     MagicMock())
        assert resp["statusCode"] == 200
        assert resp["outputIngestionPending"] is False
        upload.assert_not_called()  # no output file at all, so nothing for the copy engine
        batch.assert_not_called()
        mark.assert_not_called()
        engine_call.assert_not_called()
        record.assert_called_once()
        assert record.call_args.kwargs["execution_status"] == "SUCCEEDED"
        assert metadata.call_count == 120
        assert {c.kwargs.get("workflow_execution_id") for c in metadata.call_args_list} == {"E1"}
