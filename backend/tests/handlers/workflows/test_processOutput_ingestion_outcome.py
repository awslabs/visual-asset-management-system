# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""processWorkflowExecutionOutput write-back correctness:

- the file-ingestion API answers 200 whenever at least ONE file landed, so the per-file
  outcome (overallSuccess / fileResults) decides the recorded status and which output rows exist;
- a failed provenance stamp fails the write-back instead of ingesting an unstamped object;
- file-level metadata/attributes target the file's EXTENDED placement, matching the file write;
- the completion status writes are conditioned on the rows not already being terminal, so an
  in-flight task cannot resurrect an aborted execution;
- each listed output object is HEADed once and stamped in a bounded pool.
"""

import os
import sys
import types
import json
import pytest
from unittest.mock import MagicMock, patch

for _k, _v in {
    "S3_ASSET_BUCKETS_STORAGE_TABLE_NAME": "t-buckets",
    "METADATA_SERVICE_LAMBDA_FUNCTION_NAME": "t-md-svc",
    "FILE_UPLOAD_LAMBDA_FUNCTION_NAME": "t-upload",
    "ASSET_STORAGE_TABLE_NAME": "t-assets",
    "ASSET_UPLOAD_TABLE_NAME": "t-asset-upload",
    "DATABASE_STORAGE_TABLE_NAME": "t-db",
    "WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME": "t-exec-v2",
    "PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME": "t-pexec",
    "PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME": "t-of",
    "PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE_NAME": "t-om",
    "PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE_NAME": "t-or",
    "PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME": "t-logs",
}.items():
    os.environ.setdefault(_k, _v)

# The handlers.workflows package __init__ imports get_task_builder from
# common.workflows.stepfunctions_builder at import time; the shared test mock package does not
# provide that submodule. These tests do not exercise ASL generation.
if "common.workflows.stepfunctions_builder" not in sys.modules:
    _sf_builder_stub = types.ModuleType("common.workflows.stepfunctions_builder")
    _sf_builder_stub.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _sf_builder_stub

from backend.backend.handlers.workflows.sfn import processWorkflowExecutionOutput as po

RUN_BUCKET = "run-io-bucket"
ASSET_BUCKET = "output-asset-bucket"
FILES_PREFIX = "pipelines/p1/JOB/output/E1/files/"
PREVIEW_PREFIX = "pipelines/p1/JOB/output/E1/preview/"
METADATA_PREFIX = "pipelines/p1/JOB/output/E1/metadata/"


def _event(**overrides):
    body = {
        "outputAssetId": "asset1.glb",
        "outputDatabaseId": "db1",
        "workflowExecutionId": "E1",
        "endStatePipelineExecutionId": "P1",
        "workflowDatabaseId": "wdb",
        "workflowId": "wf1",
        "workflowExecutionS3InputOutputBucket": RUN_BUCKET,
        "executingUserName": "SYSTEM_USER",
        "executingRequestContext": {"http": {}},
    }
    body.update(overrides)
    return {"body": body}


def _asset_patches():
    return (
        patch.object(po, "lookup_existing_asset",
                     return_value={"databaseId": "db1", "assetId": "asset1.glb",
                                   "bucketId": "b1"}),
        patch.object(po, "get_default_bucket_details",
                     return_value={"bucketId": "b1", "bucketName": ASSET_BUCKET,
                                   "baseAssetsPrefix": "assets/"}),
        patch.object(po, "_fetch_execution_logs", return_value=("", "")),
    )


def _ingestion_body(**body):
    return {"Payload": MagicMock(read=lambda: json.dumps(
        {"statusCode": 200, "body": json.dumps(body)}).encode("utf-8"))}


@pytest.mark.unit
class TestPartialIngestionIsAFailure:
    """A 200 with overallSuccess False means only SOME files landed; the run is not SUCCEEDED and
    no output row is written for a file that never landed."""

    LISTING = {"Contents": [{"Key": FILES_PREFIX + "good.glb", "Size": 7},
                            {"Key": FILES_PREFIX + "bad.glb", "Size": 7}]}

    def _run(self, upload_result):
        lookup, bucket, logs = _asset_patches()
        with lookup, bucket, logs, \
                patch.object(po, "verify_get_path_objects", return_value=self.LISTING), \
                patch.object(po, "create_external_upload_record", return_value="upl-1"), \
                patch.object(po, "_stamp_output_objects", return_value=True), \
                patch.object(po, "process_external_upload", return_value=upload_result), \
                patch.object(po, "record_execution_outputs") as m_record:
            resp = po.lambda_handler(_event(filesPathKey=FILES_PREFIX), MagicMock())
        assert resp["statusCode"] == 200
        return m_record.call_args.kwargs

    def test_partial_success_records_failed(self):
        kw = self._run({
            "overallSuccess": False,
            "fileResults": [{"relativeKey": "good.glb", "success": True},
                            {"relativeKey": "bad.glb", "success": False, "error": "rejected"}],
        })
        assert kw["execution_status"] == "FAILED"
        assert kw["execution_error"] == "The asset file write-back failed."

    def test_only_the_files_that_landed_get_output_rows(self):
        kw = self._run({
            "overallSuccess": False,
            "fileResults": [{"relativeKey": "good.glb", "success": True},
                            {"relativeKey": "bad.glb", "success": False, "error": "rejected"}],
        })
        rows = [f["relativeFilePath"] for f in kw["output_files"] if f["fileType"] == "file"]
        assert rows == ["good.glb"]

    def test_all_succeeded_still_records_every_row(self):
        kw = self._run({
            "overallSuccess": True,
            "fileResults": [{"relativeKey": "good.glb", "success": True},
                            {"relativeKey": "bad.glb", "success": True}],
        })
        assert kw["execution_status"] == "SUCCEEDED"
        rows = {f["relativeFilePath"] for f in kw["output_files"] if f["fileType"] == "file"}
        assert rows == {"good.glb", "bad.glb"}

    def test_a_body_without_per_file_results_keeps_every_row(self):
        # An older/other ingestion response shape carries no fileResults; nothing to narrow by.
        kw = self._run({"message": "Upload completed successfully"})
        assert kw["execution_status"] == "SUCCEEDED"
        assert len([f for f in kw["output_files"] if f["fileType"] == "file"]) == 2

    def test_preview_partial_failure_records_failed_with_no_row(self):
        listing = {"Contents": [{"Key": PREVIEW_PREFIX + "thumb.png", "Size": 3}]}
        lookup, bucket, logs = _asset_patches()
        with lookup, bucket, logs, \
                patch.object(po, "verify_get_path_objects", return_value=listing), \
                patch.object(po, "create_external_upload_record", return_value="upl-1"), \
                patch.object(po, "_stamp_output_objects", return_value=True), \
                patch.object(po, "process_external_upload", return_value={
                    "overallSuccess": False,
                    "fileResults": [{"relativeKey": "thumb.png", "success": False}]}), \
                patch.object(po, "record_execution_outputs") as m_record:
            po.lambda_handler(_event(previewPathKey=PREVIEW_PREFIX), MagicMock())
        kw = m_record.call_args.kwargs
        assert kw["execution_status"] == "FAILED"
        assert [f for f in kw["output_files"] if f["fileType"] == "preview"] == []


@pytest.mark.unit
class TestIngestionOutcomeReader:
    def test_no_result_is_a_failure(self):
        assert po._ingestion_outcome(None) == (False, None)

    def test_missing_flag_defaults_to_success(self):
        assert po._ingestion_outcome({"message": "ok"}) == (True, None)

    def test_successful_keys_are_collected(self):
        ok, keys = po._ingestion_outcome({
            "overallSuccess": False,
            "fileResults": [{"relativeKey": "a", "success": True},
                            {"relativeKey": "b", "success": False}]})
        assert ok is False and keys == {"a"}


@pytest.mark.unit
class TestStampFailureFailsTheWriteBack:
    """An object ingested without its asset/upload provenance stamp has no traceable origin, so a
    failed stamp is a write-back failure and ingestion is never invoked."""

    LISTING = {"Contents": [{"Key": FILES_PREFIX + "model.glb", "Size": 7}]}

    def test_failed_stamp_blocks_ingestion_and_records_failed(self):
        lookup, bucket, logs = _asset_patches()
        with lookup, bucket, logs, \
                patch.object(po, "verify_get_path_objects", return_value=self.LISTING), \
                patch.object(po, "create_external_upload_record", return_value="upl-1"), \
                patch.object(po, "update_s3_object_metadata", return_value=False), \
                patch.object(po, "process_external_upload") as m_upload, \
                patch.object(po, "record_execution_outputs") as m_record:
            po.lambda_handler(_event(filesPathKey=FILES_PREFIX), MagicMock())
        m_upload.assert_not_called()
        kw = m_record.call_args.kwargs
        assert kw["execution_status"] == "FAILED"
        assert [f for f in kw["output_files"] if f["fileType"] == "file"] == []

    def test_failed_preview_stamp_blocks_ingestion(self):
        listing = {"Contents": [{"Key": PREVIEW_PREFIX + "thumb.png", "Size": 3}]}
        lookup, bucket, logs = _asset_patches()
        with lookup, bucket, logs, \
                patch.object(po, "verify_get_path_objects", return_value=listing), \
                patch.object(po, "create_external_upload_record", return_value="upl-1"), \
                patch.object(po, "update_s3_object_metadata", return_value=False), \
                patch.object(po, "process_external_upload") as m_upload, \
                patch.object(po, "record_execution_outputs") as m_record:
            po.lambda_handler(_event(previewPathKey=PREVIEW_PREFIX), MagicMock())
        m_upload.assert_not_called()
        assert m_record.call_args.kwargs["execution_status"] == "FAILED"

    def test_stamp_helper_reports_a_single_failure(self):
        objects = [{"Key": "a"}, {"Key": "b"}, {"Key": "c"}]
        with patch.object(po, "update_s3_object_metadata",
                          side_effect=[True, False, True]) as m_stamp:
            assert po._stamp_output_objects(objects, "a1", "db1", "u1", RUN_BUCKET) is False
        assert m_stamp.call_count == 3

    def test_stamp_helper_reuses_the_listing_head(self):
        objects = [{"Key": "a", "ContentType": "model/gltf-binary", "Metadata": {"x": "1"}}]
        with patch.object(po, "s3c") as m_s3c, patch.object(po, "s3r") as m_s3r:
            assert po._stamp_output_objects(objects, "a1", "db1", "u1", RUN_BUCKET) is True
        m_s3c.head_object.assert_not_called()
        extra = m_s3r.Object.return_value.copy.call_args.kwargs["ExtraArgs"]
        assert extra["ContentType"] == "model/gltf-binary"
        assert extra["Metadata"]["x"] == "1"


@pytest.mark.unit
class TestFileMetadataTargetsTheExtendedPath:
    """Three shipped workflows set an output path extension, so file-level metadata must target the
    extended path the file write used or the metadata service rejects it as a missing file."""

    def test_derived_path_carries_the_extension(self):
        assert po.extract_file_path_from_metadata_filename(
            METADATA_PREFIX + "models/part.glb.metadata.json", METADATA_PREFIX,
            file_base_execution_path_extension="/exec-2026/") == "models/exec-2026/part.glb"

    def test_attribute_files_use_the_same_placement(self):
        assert po.extract_file_path_from_metadata_filename(
            METADATA_PREFIX + "part.glb.attribute.json", METADATA_PREFIX,
            file_base_execution_path_extension="/exec-2026/") == "exec-2026/part.glb"

    def test_default_extension_leaves_the_path_unchanged(self):
        assert po.extract_file_path_from_metadata_filename(
            METADATA_PREFIX + "models/part.glb.metadata.json", METADATA_PREFIX) \
            == "models/part.glb"

    def _run(self, extension):
        listing = {"Contents": [
            {"Key": METADATA_PREFIX + "models/part.glb.metadata.json", "Size": 5},
            {"Key": METADATA_PREFIX + "models/part.glb.attribute.json", "Size": 5},
        ]}
        lookup, bucket, logs = _asset_patches()
        with lookup, bucket, logs, \
                patch.object(po, "verify_get_path_objects", return_value=listing), \
                patch.object(po, "process_metadata_file",
                             return_value=[{"metadataKey": "color", "metadataValue": "red"}]) as m_md, \
                patch.object(po, "record_execution_outputs") as m_record:
            po.lambda_handler(
                _event(metadataPathKey=METADATA_PREFIX,
                       outputFileBaseExecutionPathExtension=extension), MagicMock())
        return m_md, m_record.call_args.kwargs

    def test_handler_sends_the_extended_path_to_the_metadata_service(self):
        m_md, _kw = self._run("/exec-2026/")
        # positional arg 5 is file_path on both the metadata and the attribute call
        assert {call.args[5] for call in m_md.call_args_list} == {"models/exec-2026/part.glb"}

    def test_recorded_target_path_matches_the_write(self):
        _m_md, kw = self._run("/exec-2026/")
        assert {row["targetFilePath"] for row in kw["output_metadata"]} \
            == {"/models/exec-2026/part.glb"}

    def test_no_extension_keeps_the_plain_path(self):
        m_md, kw = self._run("/")
        assert {call.args[5] for call in m_md.call_args_list} == {"models/part.glb"}
        assert {row["targetFilePath"] for row in kw["output_metadata"]} == {"/models/part.glb"}


@pytest.mark.unit
class TestCompletionWritesAreGuarded:
    """An abort stamps the rows terminal and stops the state machine, but the in-flight
    process-output task still runs; its completion write must not resurrect the run."""

    def _record(self, main_side_effect=None):
        updates = {}
        po.workflow_execution_database_v2 = "t-exec-v2"

        def _make_table(name):
            t = MagicMock()
            updates.setdefault(name, [])

            def _update(**kw):
                updates[name].append(kw)
                if name == "t-exec-v2" and main_side_effect:
                    raise main_side_effect
            t.update_item.side_effect = _update
            return t

        dynamo = MagicMock(Table=MagicMock(side_effect=_make_table))
        po.record_execution_outputs(
            dynamo=dynamo, workflow_execution_id="E1", end_state_pipeline_execution_id="P1",
            workflow_database_id="wdb", workflow_id="wf1", bucket_name=ASSET_BUCKET,
            output_files=[], output_metadata=[], output_results=[], result_log="",
            execution_log="", log_group_arn="", log_stream_name="",
            execution_status="SUCCEEDED")
        return updates

    def test_both_status_writes_carry_the_terminal_guard(self):
        updates = self._record()
        for name in ("t-pexec", "t-exec-v2"):
            condition = updates[name][0]["ConditionExpression"]
            assert "attribute_not_exists(executionStatus)" in condition
            assert "NOT executionStatus IN" in condition

    def test_every_terminal_status_is_excluded(self):
        updates = self._record()
        values = updates["t-exec-v2"][0]["ExpressionAttributeValues"]
        guarded = {v for k, v in values.items() if k.startswith(":term")}
        assert guarded == set(po.eo.TERMINAL_STATUSES)

    def test_an_already_terminal_row_is_left_alone(self):
        from botocore.exceptions import ClientError
        rejected = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException", "Message": "no"}}, "UpdateItem")
        # No exception escapes: the abort already recorded the terminal state.
        assert self._record(main_side_effect=rejected)["t-exec-v2"]

    def test_a_real_write_failure_still_surfaces(self):
        from botocore.exceptions import ClientError
        throttled = ClientError(
            {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "slow"}},
            "UpdateItem")
        with pytest.raises(ClientError):
            self._record(main_side_effect=throttled)


@pytest.mark.unit
class TestOutputObjectsAreHeadedOnce:
    """A thousand-file output must fit the lambda timeout: one HEAD per object for the whole run
    (shared by the MIME check, the recorded descriptors and the provenance stamp) and one copy."""

    def test_listing_heads_each_object_once_and_annotates_it(self):
        objects = [{"Key": FILES_PREFIX + f"f{i}.glb", "Size": 1} for i in range(5)]
        with patch.object(po, "list_all_objects", return_value=objects), \
                patch.object(po, "s3c") as m_s3c:
            m_s3c.head_object.return_value = {"ContentType": "model/gltf-binary",
                                              "VersionId": "v1", "Metadata": {"m": "1"}}
            listing = po.verify_get_path_objects(RUN_BUCKET, FILES_PREFIX)
        assert m_s3c.head_object.call_count == 5
        assert all(o["VersionId"] == "v1" for o in listing["Contents"])

    def test_descriptors_do_not_re_head_an_annotated_listing(self):
        listing = {"Contents": [{"Key": FILES_PREFIX + "m.glb", "Size": 1,
                                 "ContentType": "model/gltf-binary", "VersionId": "v7",
                                 "Metadata": {}}]}
        with patch.object(po, "s3c") as m_s3c:
            descs = po._collect_output_descriptors(listing, "file", FILES_PREFIX, RUN_BUCKET)
        m_s3c.head_object.assert_not_called()
        assert descs[0]["s3VersionId"] == "v7"
        assert descs[0]["contentType"] == "model/gltf-binary"

    def test_a_full_run_costs_one_head_and_one_copy_per_file(self):
        objects = [{"Key": FILES_PREFIX + f"f{i}.glb", "Size": 1} for i in range(10)]
        lookup, bucket, logs = _asset_patches()
        with lookup, bucket, logs, \
                patch.object(po, "list_all_objects", return_value=objects), \
                patch.object(po, "create_external_upload_record", return_value="upl-1"), \
                patch.object(po, "_lambda_file_ingestion",
                             return_value=_ingestion_body(overallSuccess=True)), \
                patch.object(po, "record_execution_outputs"), \
                patch.object(po, "s3c") as m_s3c, patch.object(po, "s3r") as m_s3r:
            m_s3c.head_object.return_value = {"ContentType": "model/gltf-binary",
                                              "VersionId": "v1", "Metadata": {}}
            po.lambda_handler(_event(filesPathKey=FILES_PREFIX), MagicMock())
        assert m_s3c.head_object.call_count == 10
        assert m_s3r.Object.return_value.copy.call_count == 10

    def test_the_s3_client_carries_a_retry_config(self):
        # A transient 5xx on one of thousands of calls must be retried, not fail the whole run.
        assert po.s3c.meta.config.retries["mode"] == "adaptive"
        assert po.s3c.meta.config.max_pool_connections == po.MAX_PARALLEL_S3_WORKERS
