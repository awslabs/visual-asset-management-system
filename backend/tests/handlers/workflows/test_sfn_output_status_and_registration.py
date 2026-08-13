# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""End-state output recording, failure status, and sub-process registration contracts:

- output-metadata rows carry the real applied key/value for asset-level, file-level, and
  attribute outputs (one row per key);
- an output-file row's s3Bucket is the bucket its s3Key/s3VersionId were listed in;
- preview detection is case-insensitive;
- a write-back or listing failure records the execution FAILED with an error summary;
- the ingestion cross-call propagates the launching user's MFA state;
- the error handler bounds the stored executionError so the FAILED finalization fits the item;
- a redelivered registration event does not duplicate registered locators.
"""

import json
import os
import sys
import types
import pytest
from unittest.mock import MagicMock, patch

# models.assetsV3 fails to import under Python 3.13 due to a pre-existing, unrelated Pydantic v1
# regex incompatibility. The handler only needs AssetUploadTableModel inside
# create_external_upload_record, which is not exercised here. Stub it before importing the handler.
if "models.assetsV3" not in sys.modules:
    _assetsv3_stub = types.ModuleType("models.assetsV3")
    _assetsv3_stub.AssetUploadTableModel = MagicMock()
    sys.modules["models.assetsV3"] = _assetsv3_stub

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
    "PIPELINE_EXECUTION_INPUT_FILES_STORAGE_TABLE_NAME": "t-pin-files",
    "PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME": "t-pin-md",
    "PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME": "t-pin-cfg",
    "WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME": "t-wf-inputs",
    "WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME": "t-wf-cfg",
}.items():
    os.environ.setdefault(_k, _v)

if "common.workflows.stepfunctions_builder" not in sys.modules:
    _sf_builder_stub = types.ModuleType("common.workflows.stepfunctions_builder")
    _sf_builder_stub.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _sf_builder_stub

from backend.backend.handlers.workflows.sfn import processWorkflowExecutionOutput as po
from backend.backend.handlers.workflows.sfn import handleExecutionError as heh
from backend.backend.handlers.workflows.sfn import registerPipelineExecution as reg

RUN_BUCKET = "run-io-bucket"
ASSET_BUCKET = "output-asset-bucket"
METADATA_PREFIX = "pipelines/p1/JOB/output/E1/metadata/"
PREVIEW_PREFIX = "pipelines/p1/JOB/output/E1/previews/"
FILES_PREFIX = "pipelines/p1/JOB/output/E1/files/"


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
                     return_value={"databaseId": "db1", "assetId": "asset1.glb", "bucketId": "b1"}),
        patch.object(po, "get_default_bucket_details",
                     return_value={"bucketId": "b1", "bucketName": ASSET_BUCKET,
                                   "baseAssetsPrefix": "assets/"}),
        patch.object(po, "_fetch_execution_logs", return_value=("", "")),
    )


@pytest.mark.unit
class TestAppliedMetadataRows:
    """One output-metadata row per applied key, for asset-level, file-level, and attribute files."""

    _METADATA_JSON = json.dumps({
        "type": "metadata", "updateType": "update",
        "metadata": [{"metadataKey": "color", "metadataValue": "red"},
                     {"metadataKey": "count", "metadataValue": "3"}],
    })
    _ATTRIBUTE_JSON = json.dumps({
        "type": "attribute", "updateType": "update",
        "metadata": [{"metadataKey": "volume", "metadataValue": "12.5"}],
    })

    def _run(self):
        listing = {"Contents": [
            {"Key": METADATA_PREFIX + "asset.metadata.json", "Size": 5},
            {"Key": METADATA_PREFIX + "models/part.glb.metadata.json", "Size": 5},
            {"Key": METADATA_PREFIX + "models/part.glb.attribute.json", "Size": 5},
        ]}

        def _get_object(Bucket, Key):
            payload = self._ATTRIBUTE_JSON if Key.endswith(".attribute.json") else self._METADATA_JSON
            return {"Body": MagicMock(read=lambda: payload.encode("utf-8"))}

        def _invoke(**kwargs):
            return {"Payload": MagicMock(read=lambda: json.dumps(
                {"statusCode": 200, "body": json.dumps({"message": "ok"})}).encode())}

        lookup, bucket, logs = _asset_patches()
        with lookup, bucket, logs, \
                patch.object(po, "verify_get_path_objects", return_value=listing), \
                patch.object(po.s3c, "get_object", side_effect=_get_object), \
                patch.object(po.s3c, "head_object", return_value={"VersionId": "v1"}), \
                patch.object(po.client, "invoke", side_effect=_invoke), \
                patch.object(po, "record_execution_outputs") as m_record:
            resp = po.lambda_handler(_event(metadataPathKey=METADATA_PREFIX), MagicMock())
        assert resp["statusCode"] == 200
        return m_record.call_args.kwargs["output_metadata"]

    def test_one_row_per_applied_key_across_all_targets(self):
        rows = self._run()
        # asset-level (2 keys) + file-level metadata (2 keys) + file-level attribute (1 key)
        assert len(rows) == 5
        pairs = {(r["targetFilePath"], r["metadataKey"], r["metadataValue"]) for r in rows}
        assert ("/", "color", "red") in pairs
        assert ("/", "count", "3") in pairs
        assert ("/models/part.glb", "color", "red") in pairs
        assert ("/models/part.glb", "volume", "12.5") in pairs

    def test_no_placeholder_empty_keys_recorded(self):
        rows = self._run()
        assert all(r["metadataKey"] for r in rows)
        assert all(r["sourceMetadataFileRelativePath"] for r in rows)

    def test_unparseable_metadata_file_records_no_rows(self):
        listing = {"Contents": [{"Key": METADATA_PREFIX + "asset.metadata.json", "Size": 5}]}
        lookup, bucket, logs = _asset_patches()
        with lookup, bucket, logs, \
                patch.object(po, "verify_get_path_objects", return_value=listing), \
                patch.object(po.s3c, "get_object",
                             return_value={"Body": MagicMock(read=lambda: b"not json")}), \
                patch.object(po, "record_execution_outputs") as m_record:
            po.lambda_handler(_event(metadataPathKey=METADATA_PREFIX), MagicMock())
        assert m_record.call_args.kwargs["output_metadata"] == []


@pytest.mark.unit
class TestOutputFileBucketPairing:
    """s3Bucket must be the bucket the recorded s3Key/s3VersionId were listed in."""

    def test_descriptor_carries_the_listed_bucket(self):
        listing = {"Contents": [{"Key": FILES_PREFIX + "model.glb", "Size": 7}]}
        with patch.object(po.s3c, "head_object", return_value={"VersionId": "v9"}):
            descriptors = po._collect_output_descriptors(listing, "file", FILES_PREFIX, RUN_BUCKET)
        assert descriptors[0]["s3Bucket"] == RUN_BUCKET
        assert descriptors[0]["s3Key"] == FILES_PREFIX + "model.glb"

    def test_recorded_row_prefers_the_descriptor_bucket_over_the_asset_bucket(self):
        puts = []
        table = MagicMock()
        table.put_item.side_effect = lambda Item: puts.append(Item)
        dynamo = MagicMock(Table=MagicMock(return_value=table))
        po.record_execution_outputs(
            dynamo=dynamo, workflow_execution_id="E1", end_state_pipeline_execution_id="P1",
            workflow_database_id="wdb", workflow_id="wf1", bucket_name=ASSET_BUCKET,
            output_files=[{"fileType": "file", "relativeFilePath": "model.glb",
                           "s3Bucket": RUN_BUCKET, "s3Key": FILES_PREFIX + "model.glb",
                           "fileSize": 7, "contentType": "", "s3VersionId": "v9"}],
            output_metadata=[], output_results=[], result_log="", execution_log="",
            log_group_arn="", log_stream_name="", execution_status="SUCCEEDED")
        row = next(p for p in puts if p.get("fileType") == "file")
        assert row["s3Bucket"] == RUN_BUCKET and row["s3VersionId"] == "v9"

    def test_falls_back_to_bucket_name_when_descriptor_has_none(self):
        puts = []
        table = MagicMock()
        table.put_item.side_effect = lambda Item: puts.append(Item)
        dynamo = MagicMock(Table=MagicMock(return_value=table))
        po.record_execution_outputs(
            dynamo=dynamo, workflow_execution_id="E1", end_state_pipeline_execution_id="P1",
            workflow_database_id="wdb", workflow_id="wf1", bucket_name=ASSET_BUCKET,
            output_files=[{"fileType": "file", "relativeFilePath": "m.glb", "s3Key": "k/m.glb"}],
            output_metadata=[], output_results=[], result_log="", execution_log="",
            log_group_arn="", log_stream_name="", execution_status="SUCCEEDED")
        row = next(p for p in puts if p.get("fileType") == "file")
        assert row["s3Bucket"] == ASSET_BUCKET


@pytest.mark.unit
class TestPreviewExtensionMatching:
    """A preview written with an uppercase extension is still recognized and ingested."""

    def _run(self, preview_key, extra_keys=()):
        listing = {"Contents": [{"Key": preview_key, "Size": 3}]
                   + [{"Key": k, "Size": 3} for k in extra_keys]}
        lookup, bucket, logs = _asset_patches()
        with lookup, bucket, logs, \
                patch.object(po, "verify_get_path_objects", return_value=listing), \
                patch.object(po.s3c, "head_object", return_value={"VersionId": "v1"}), \
                patch.object(po, "create_external_upload_record", return_value="upl-1"), \
                patch.object(po, "update_s3_object_metadata", return_value=True), \
                patch.object(po, "process_external_upload", return_value={"ok": True}) as m_upload, \
                patch.object(po, "record_execution_outputs") as m_record:
            resp = po.lambda_handler(_event(previewPathKey=PREVIEW_PREFIX), MagicMock())
        assert resp["statusCode"] == 200
        return m_upload, m_record

    def test_uppercase_extension_preview_is_ingested(self):
        m_upload, m_record = self._run(PREVIEW_PREFIX + "thumbnail.PNG")
        assert m_upload.call_count == 1
        assert m_upload.call_args.args[3] == "assetPreview"
        assert m_record.call_args.kwargs["execution_status"] == "SUCCEEDED"

    def test_non_image_preview_records_failed(self):
        m_upload, m_record = self._run(PREVIEW_PREFIX + "notes.txt")
        m_upload.assert_not_called()
        assert m_record.call_args.kwargs["execution_status"] == "FAILED"
        assert m_record.call_args.kwargs["execution_error"]

    def test_only_the_ingested_preview_is_recorded_at_its_basename(self):
        # The ingested preview lands under its basename, and the objects that were not ingested
        # (a second image, a non-image) get no preview row.
        m_upload, m_record = self._run(
            PREVIEW_PREFIX + "render/large.png",
            extra_keys=(PREVIEW_PREFIX + "render/small.png", PREVIEW_PREFIX + "log.txt"))
        rows = [f for f in m_record.call_args.kwargs["output_files"] if f["fileType"] == "preview"]
        assert len(rows) == 1
        assert rows[0]["relativeFilePath"] == "large.png"
        assert rows[0]["s3Key"] == PREVIEW_PREFIX + "render/large.png"
        assert m_upload.call_args.args[4] == [PREVIEW_PREFIX + "render/large.png"]

    def test_no_preview_row_when_nothing_is_ingested(self):
        _m_upload, m_record = self._run(PREVIEW_PREFIX + "notes.txt")
        assert [f for f in m_record.call_args.kwargs["output_files"]
                if f["fileType"] == "preview"] == []


@pytest.mark.unit
class TestFailureStatusRecording:
    """A failed write-back or listing records the execution FAILED, not SUCCEEDED."""

    def test_asset_file_ingestion_failure_records_failed(self):
        listing = {"Contents": [{"Key": FILES_PREFIX + "model.glb", "Size": 7}]}
        lookup, bucket, logs = _asset_patches()
        with lookup, bucket, logs, \
                patch.object(po, "verify_get_path_objects", return_value=listing), \
                patch.object(po.s3c, "head_object", return_value={"VersionId": "v1"}), \
                patch.object(po, "create_external_upload_record", return_value="upl-1"), \
                patch.object(po, "update_s3_object_metadata", return_value=True), \
                patch.object(po, "process_external_upload", return_value=None), \
                patch.object(po, "record_execution_outputs") as m_record:
            resp = po.lambda_handler(_event(filesPathKey=FILES_PREFIX), MagicMock())
        assert resp["statusCode"] == 200
        kw = m_record.call_args.kwargs
        assert kw["execution_status"] == "FAILED"
        assert "write-back" in kw["execution_error"]

    def test_listing_failure_records_failed(self):
        lookup, bucket, logs = _asset_patches()
        with lookup, bucket, logs, \
                patch.object(po, "verify_get_path_objects", side_effect=Exception("denied")), \
                patch.object(po, "record_execution_outputs") as m_record:
            po.lambda_handler(_event(filesPathKey=FILES_PREFIX), MagicMock())
        assert m_record.call_args.kwargs["execution_status"] == "FAILED"

    def test_clean_run_still_records_succeeded_with_no_error(self):
        listing = {"Contents": [{"Key": FILES_PREFIX + "model.glb", "Size": 7}]}
        lookup, bucket, logs = _asset_patches()
        with lookup, bucket, logs, \
                patch.object(po, "verify_get_path_objects", return_value=listing), \
                patch.object(po.s3c, "head_object", return_value={"VersionId": "v1"}), \
                patch.object(po, "create_external_upload_record", return_value="upl-1"), \
                patch.object(po, "update_s3_object_metadata", return_value=True), \
                patch.object(po, "process_external_upload", return_value={"ok": True}), \
                patch.object(po, "record_execution_outputs") as m_record:
            po.lambda_handler(_event(filesPathKey=FILES_PREFIX), MagicMock())
        kw = m_record.call_args.kwargs
        assert kw["execution_status"] == "SUCCEEDED" and kw["execution_error"] == ""

    def test_metadata_write_back_rejection_records_failed(self):
        listing = {"Contents": [{"Key": METADATA_PREFIX + "asset.metadata.json", "Size": 5}]}
        payload = json.dumps({"type": "metadata",
                              "metadata": [{"metadataKey": "color", "metadataValue": "red"}]})
        rejected = {"Payload": MagicMock(read=lambda: json.dumps(
            {"statusCode": 403, "body": json.dumps({"message": "denied"})}).encode())}
        lookup, bucket, logs = _asset_patches()
        with lookup, bucket, logs, \
                patch.object(po, "verify_get_path_objects", return_value=listing), \
                patch.object(po.s3c, "get_object",
                             return_value={"Body": MagicMock(read=lambda: payload.encode())}), \
                patch.object(po.client, "invoke", return_value=rejected), \
                patch.object(po, "record_execution_outputs") as m_record:
            po.lambda_handler(_event(metadataPathKey=METADATA_PREFIX), MagicMock())
        kw = m_record.call_args.kwargs
        assert kw["execution_status"] == "FAILED"
        assert kw["execution_error"] == po.METADATA_WRITE_BACK_FAILURE
        assert kw["output_metadata"] == []

    def test_unparseable_metadata_file_records_failed(self):
        listing = {"Contents": [{"Key": METADATA_PREFIX + "asset.metadata.json", "Size": 5}]}
        lookup, bucket, logs = _asset_patches()
        with lookup, bucket, logs, \
                patch.object(po, "verify_get_path_objects", return_value=listing), \
                patch.object(po.s3c, "get_object",
                             return_value={"Body": MagicMock(read=lambda: b"not json")}), \
                patch.object(po, "record_execution_outputs") as m_record:
            po.lambda_handler(_event(metadataPathKey=METADATA_PREFIX), MagicMock())
        assert m_record.call_args.kwargs["execution_status"] == "FAILED"

    def test_metadata_file_with_no_keys_still_succeeds(self):
        listing = {"Contents": [{"Key": METADATA_PREFIX + "asset.metadata.json", "Size": 5}]}
        payload = json.dumps({"type": "metadata", "metadata": []})
        applied = {"Payload": MagicMock(read=lambda: json.dumps(
            {"statusCode": 200, "body": json.dumps({"message": "ok"})}).encode())}
        lookup, bucket, logs = _asset_patches()
        with lookup, bucket, logs, \
                patch.object(po, "verify_get_path_objects", return_value=listing), \
                patch.object(po.s3c, "get_object",
                             return_value={"Body": MagicMock(read=lambda: payload.encode())}), \
                patch.object(po.client, "invoke", return_value=applied), \
                patch.object(po, "record_execution_outputs") as m_record:
            po.lambda_handler(_event(metadataPathKey=METADATA_PREFIX), MagicMock())
        kw = m_record.call_args.kwargs
        assert kw["execution_status"] == "SUCCEEDED" and kw["output_metadata"] == []

    def test_failed_status_writes_execution_error_on_the_main_row(self):
        updates = {}
        po.workflow_execution_database_v2 = "t-exec-v2"

        def _make_table(name):
            t = MagicMock()
            updates.setdefault(name, [])
            t.update_item.side_effect = lambda **kw: updates[name].append(kw)
            return t

        dynamo = MagicMock(Table=MagicMock(side_effect=_make_table))
        po.record_execution_outputs(
            dynamo=dynamo, workflow_execution_id="E1", end_state_pipeline_execution_id="P1",
            workflow_database_id="wdb", workflow_id="wf1", bucket_name=ASSET_BUCKET,
            output_files=[], output_metadata=[], output_results=[], result_log="",
            execution_log="", log_group_arn="", log_stream_name="",
            execution_status="FAILED", execution_error="The asset file write-back failed.")
        main_update = updates["t-exec-v2"][0]
        assert "executionError" in main_update["UpdateExpression"]
        assert main_update["ExpressionAttributeValues"][":er"] == "The asset file write-back failed."


@pytest.mark.unit
class TestWriteBackCrossCallMfa:
    """The ingestion cross-call carries the launching end user's MFA state."""

    def _captured_cross_call(self, mfa_enabled):
        captured = {}

        def _invoke(FunctionName, InvocationType, Payload):
            captured["payload"] = json.loads(Payload.decode("utf-8"))
            return {"Payload": MagicMock(read=lambda: json.dumps(
                {"statusCode": 200, "body": json.dumps({"ok": True})}).encode())}

        with patch.object(po.client, "invoke", side_effect=_invoke):
            po.process_external_upload(
                "upl-1", "asset1", "db1", "assetFile", ["a/model.glb"], "a/", {"http": {}},
                change_user_id="alice", mfa_enabled=mfa_enabled)
        return captured["payload"]["lambdaCrossCall"]

    def test_non_mfa_user_propagates_false(self):
        assert self._captured_cross_call(False) == {"userName": "alice", "mfaEnabled": False}

    def test_mfa_user_propagates_true(self):
        assert self._captured_cross_call(True)["mfaEnabled"] is True

    def test_system_call_leaves_mfa_unset(self):
        assert "mfaEnabled" not in self._captured_cross_call(None)

    def test_handler_threads_the_launching_users_mfa_state(self):
        listing = {"Contents": [{"Key": FILES_PREFIX + "model.glb", "Size": 7}]}
        enforcer = MagicMock()
        enforcer.enforce.return_value = True
        lookup, bucket, logs = _asset_patches()
        with lookup, bucket, logs, \
                patch.object(po, "request_to_claims",
                             return_value={"tokens": ["alice"], "roles": [], "mfaEnabled": False}), \
                patch.object(po, "CasbinEnforcer", return_value=enforcer), \
                patch.object(po, "verify_get_path_objects", return_value=listing), \
                patch.object(po.s3c, "head_object", return_value={"VersionId": "v1"}), \
                patch.object(po, "create_external_upload_record", return_value="upl-1"), \
                patch.object(po, "update_s3_object_metadata", return_value=True), \
                patch.object(po, "process_external_upload", return_value={"ok": True}) as m_upload, \
                patch.object(po, "record_execution_outputs"):
            po.lambda_handler(
                _event(filesPathKey=FILES_PREFIX, executingUserName="alice"), MagicMock())
        assert m_upload.call_args.kwargs["mfa_enabled"] is False


@pytest.mark.unit
class TestErrorMessageTruncation:
    """executionError shares the main row's text budget with executionLog, so it is bounded."""

    def test_large_cause_is_trimmed_under_the_error_budget(self):
        message = heh._extract_error_message(
            {"Error": "States.TaskFailed", "Cause": "A" * 300000})
        assert len(message.encode("utf-8")) <= heh.MAX_ERROR_FIELD_BYTES

    def test_non_dict_error_info_is_trimmed_too(self):
        assert len(heh._extract_error_message("B" * 200000).encode("utf-8")) \
            <= heh.MAX_ERROR_FIELD_BYTES

    def test_error_plus_log_budgets_fit_the_item_text_limit(self):
        from backend.backend.common.workflows import executionRecords as er
        assert heh.MAX_ERROR_FIELD_BYTES + heh.MAX_ERROR_LOG_FIELD_BYTES <= er.MAX_LOG_FIELD_BYTES

    def test_small_message_is_unchanged(self):
        assert heh._extract_error_message(
            {"Error": "X", "Cause": json.dumps({"errorMessage": "detail"})}) == "X: detail"


@pytest.mark.unit
class TestRegistrationIdempotency:
    """EventBridge delivers at-least-once; a redelivered registration must not duplicate."""

    _SM_ARN = "arn:aws:states:us-east-1:123456789012:stateMachine:sm"
    _EX_ARN = "arn:aws:states:us-east-1:123456789012:execution:sm:ex"
    _LG_ARN = "arn:aws:logs:us-east-1:123456789012:log-group:/aws/lg:*"

    def _detail(self):
        return {"pipelineExecutionId": "P1",
                "subExecution": {"stateMachineArn": self._SM_ARN, "executionArn": self._EX_ARN},
                "logs": [{"logGroupArn": self._LG_ARN, "logGroupName": "lg",
                          "logStreamName": "s1"}]}

    def test_redelivery_of_the_same_detail_writes_once(self):
        stored_subs, stored_logs = [], []
        row = {"pipelineExecutionId": "P1", "workflowExecutionId": "E1",
               "registeredSubExecutions": stored_subs, "registeredLogs": stored_logs}

        def _update_item(**kw):
            stored_subs.extend(kw["ExpressionAttributeValues"][":s"])
            stored_logs.extend(kw["ExpressionAttributeValues"][":l"])

        table = MagicMock(query=MagicMock(return_value={"Items": [row]}),
                          update_item=MagicMock(side_effect=_update_item))
        with patch.object(reg.dynamodb, "Table", return_value=table):
            reg.register(self._detail())
            reg.register(self._detail())
        assert table.update_item.call_count == 1
        assert len(stored_subs) == 1 and len(stored_logs) == 1

    def test_a_new_locator_still_appends(self):
        row = {"pipelineExecutionId": "P1", "workflowExecutionId": "E1",
               "registeredSubExecutions": [{"resourceType": "stepFunctionsExecution",
                                            "stateMachineArn": self._SM_ARN,
                                            "executionArn": self._EX_ARN}],
               "registeredLogs": []}
        new_ex = "arn:aws:states:us-east-1:123456789012:execution:sm:other"
        table = MagicMock(query=MagicMock(return_value={"Items": [row]}), update_item=MagicMock())
        with patch.object(reg.dynamodb, "Table", return_value=table):
            reg.register({"pipelineExecutionId": "P1",
                          "subExecution": {"stateMachineArn": self._SM_ARN,
                                           "executionArn": new_ex}})
        subs = table.update_item.call_args.kwargs["ExpressionAttributeValues"][":s"]
        assert subs == [{"resourceType": "stepFunctionsExecution",
                         "stateMachineArn": self._SM_ARN, "executionArn": new_ex}]
