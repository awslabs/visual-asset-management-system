# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Stage 2 unit tests: shared output-attribution module (executionOutputs), the interim
pipeline-tracking lambda (output version-diff + resolved input manifest), the error-handler
lambda (table reconciliation to FAILED), and the createWorkflow ASL flow (interim states +
error-catch routing + the Stage 2 input envelope)."""

import os
import sys
import types
import json
import pytest
from unittest.mock import MagicMock, patch

# Env vars the Stage 2 lambdas read at import time.
for k, v in {
    "WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME": "t-exec-v2",
    "PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME": "t-pexec",
    "PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME": "t-of",
    "PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME": "t-logs",
    "WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME": "t-wf-inputs",
    "WORKFLOW_EXECUTION_LOG_GROUP_ARN": "arn:aws:logs:us-east-1:1:log-group:vams-wf:*",
}.items():
    os.environ.setdefault(k, v)

if "common.workflows.stepfunctions_builder" not in sys.modules:
    _sf = types.ModuleType("common.workflows.stepfunctions_builder")
    _sf.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _sf

from backend.backend.common.workflows import executionOutputs as eo
from backend.backend.common.workflows import executionRecords as er
from backend.backend.handlers.workflows.sfn import interimPipelineTracking as ipt
from backend.backend.handlers.workflows.sfn import handleExecutionError as heh


# ============================ executionOutputs (pure) ============================

@pytest.mark.unit
class TestOutputAttribution:
    def test_attribute_new_and_changed_versions(self):
        snapshot = {"k/a": "v1", "k/b": "v1"}
        current = [
            {"key": "k/a", "versionId": "v1", "relativePath": "/a"},   # unchanged -> not N's
            {"key": "k/b", "versionId": "v2", "relativePath": "/b"},   # changed -> N's
            {"key": "k/c", "versionId": "v1", "relativePath": "/c"},   # new -> N's
        ]
        produced = eo.attribute_pipeline_outputs(current, snapshot)
        rels = {f["relativePath"] for f in produced}
        assert rels == {"/b", "/c"}

    def test_build_resolved_manifest_shadows_only_overwritten_paths(self):
        # Original inputs: two asset files. Output folder has a new version of pump.e57 only.
        original = [
            {"relativePath": "/test/pump.e57", "bucket": "abkt", "key": "a1/test/pump.e57", "versionId": ""},
            {"relativePath": "/test/scan.las", "bucket": "abkt", "key": "a1/test/scan.las", "versionId": ""},
        ]
        s3 = MagicMock()
        # list_current_output_files is driven by the paginator; stub it directly instead.
        with patch.object(eo, "list_current_output_files", return_value=[
            {"key": "out/files/test/pump.e57", "relativePath": "/test/pump.e57",
             "versionId": "ov9", "fileSize": 1, "contentType": ""},
        ]):
            envelope = eo.build_resolved_manifest(s3, original, "abkt", "out/files/")
        # The manifest is now a grouped envelope; the resolved files are under inputFiles.
        assert envelope["schemaVersion"] == er.MANIFEST_SCHEMA_VERSION
        by_rel = {m["relativePath"]: m for m in envelope["inputFiles"]}
        # pump.e57 shadowed -> points at the OUTPUT file + its version.
        assert by_rel["/test/pump.e57"]["key"] == "out/files/test/pump.e57"
        assert by_rel["/test/pump.e57"]["versionId"] == "ov9"
        # scan.las untouched -> keeps the ORIGINAL asset file.
        assert by_rel["/test/scan.las"]["key"] == "a1/test/scan.las"

    def test_execution_input_prefix_keyed_only_on_execution_id(self):
        # REGRESSION (Fix 1): the input-definition folder must NOT depend on a job name --
        # executeWorkflow and the ASL each draw independent job-name uuids, so a job-name
        # segment would diverge. The prefix is a pure function of the execution id, and the
        # ASL template must produce the identical path when its {} is filled with the exec id.
        from backend.backend.common.workflows import executionRecords as er2
        prefix = er2.execution_input_prefix("EXECABC")
        assert prefix == "pipelines/workflowExecutionInputs/EXECABC/"
        # The createWorkflow ASL template (input_folder_template) filled with the exec name.
        asl_template = "pipelines/workflowExecutionInputs/{}/"
        assert asl_template.format("EXECABC") == prefix
        # Per-pipeline + metadata keys are likewise execution-id-only.
        assert er2.pipeline_input_config_key("EXECABC", 2) == prefix + "pipeline2/config.json"
        assert er2.pipeline_input_manifest_key("EXECABC", 1) == prefix + "pipeline1/manifest.json"
        assert er2.execution_input_metadata_key("EXECABC") == prefix + "metadata.json"

    def test_recorded_output_versions_reconstructs_snapshot(self):
        # Two prior pipelines' recorded output rows form the version baseline.
        def make_table(name):
            t = MagicMock()
            t.query.return_value = {"Items": [
                {"s3Key": "out/files/a", "s3VersionId": "v1"},
            ]}
            return t
        dynamo = MagicMock()
        dynamo.Table.side_effect = make_table
        snap = eo.recorded_output_versions(dynamo, "t-of", ["P1"])
        assert snap == {"out/files/a": "v1"}


# ============================ interim lambda ============================

@pytest.mark.unit
class TestInterimPipelineTracking:
    def _event(self, body):
        return {"body": body}

    def test_logs_prev_outputs_and_writes_next_manifest(self):
        body = {
            "workflowExecutionId": "EXEC1",
            "workflowDatabaseId": "wdb", "workflowId": "wf",
            "bucketAsset": "abkt",
            "outputFilesPrefix": "pipelines/p1/job/output/EXEC1/files/",
            "fromPipelineExecutionId": "P1",
            "priorPipelineExecutionIds": ["P1", "P2"],
            "nextPipelineManifestS3Key": "pipelines/p1/job/input/EXEC1/pipeline2/manifest.json",
            "nextPipelineConfigS3Key": "pipelines/p1/job/input/EXEC1/pipeline2/config.json",
        }
        # P1 produced one new output file; original input is one asset file.
        produced = [{"key": "pipelines/p1/job/output/EXEC1/files/test/x.glb",
                     "relativePath": "/test/x.glb", "versionId": "ov1",
                     "fileSize": 10, "contentType": ""}]
        put_object = MagicMock()
        record_outputs = MagicMock()
        set_status = MagicMock()
        with patch.object(ipt.s3c, "put_object", put_object), \
             patch.object(ipt.eo, "recorded_output_versions", return_value={}), \
             patch.object(ipt.eo, "list_current_output_files", return_value=produced), \
             patch.object(ipt.eo, "record_pipeline_output_files", record_outputs), \
             patch.object(ipt.eo, "set_pipeline_status", set_status), \
             patch.object(ipt, "_get_original_input_entries", return_value=[
                 {"relativePath": "/test/x.glb", "bucket": "abkt", "key": "a1/test/x.glb", "versionId": ""}]):
            result = ipt.lambda_handler(self._event(body), MagicMock())
        # P1's outputs recorded + P1 marked SUCCEEDED.
        record_outputs.assert_called_once()
        assert record_outputs.call_args.args[2] == "P1"  # pipeline_execution_id
        set_status.assert_called_once()
        assert set_status.call_args.args[2] == "P1"
        # Next pipeline manifest written + locations returned.
        put_object.assert_called_once()
        assert put_object.call_args.kwargs["Key"] == body["nextPipelineManifestS3Key"]
        assert result["inputManifestS3Location"].endswith("pipeline2/manifest.json")
        assert result["inputConfigurationS3Location"].endswith("pipeline2/config.json")

    def test_original_input_relativepath_is_stripped_to_asset_relative(self):
        # The stored inputAssetFileKey is the FULL asset-ID-prefixed key; _get_original_input_entries
        # strips each file's OWN asset-root key (the per-row assetRootS3Key, bucket-relative) so
        # relativePath is asset-relative and matches the asset-relative output-files keys for shadow
        # detection. Each file's own aux preview prefix is rebuilt per file.
        inputs_table = MagicMock(query=MagicMock(return_value={"Items": [
            {"inputAssetFileKey": "/a1xyz/test/pump.e57", "databaseId": "db", "assetId": "a1xyz",
             "s3Bucket": "abkt", "assetRootS3Key": "a1xyz/"},
        ]}))
        with patch.object(ipt.dynamodb, "Table", return_value=inputs_table):
            entries = ipt._get_original_input_entries("EXEC1")
        assert entries[0]["relativePath"] == "/test/pump.e57"   # base stripped, asset-relative
        assert entries[0]["key"] == "a1xyz/test/pump.e57"       # full key preserved for S3
        assert entries[0]["bucket"] == "abkt"                   # the file's own bucket
        assert entries[0]["assetRootS3Key"] == "a1xyz/"         # relative asset-root key
        # Per-file aux preview prefix keyed on the FULL asset file key: {databaseId}/{assetFileKey}/preview.
        assert entries[0]["auxPreviewPrefix"] == "db/a1xyz/test/pump.e57/preview"

    def test_shadowing_matches_after_stripping(self):
        # End-to-end shadow check with the full-prefixed stored key: pump.e57 was rewritten by
        # a prior pipeline (present in the output files folder), scan.las was not. Each input row
        # is self-locating (its own s3Bucket + assetRootS3Key).
        inputs_table = MagicMock(query=MagicMock(return_value={"Items": [
            {"inputAssetFileKey": "/a1xyz/test/pump.e57", "databaseId": "db", "assetId": "a1",
             "s3Bucket": "abkt", "assetRootS3Key": "a1xyz/"},
            {"inputAssetFileKey": "/a1xyz/test/scan.las", "databaseId": "db", "assetId": "a1",
             "s3Bucket": "abkt", "assetRootS3Key": "a1xyz/"},
        ]}))
        body = {
            "workflowExecutionId": "EXEC1",
            "workflowExecutionS3InputOutputBucket": "abkt",
            "outputFilesPrefix": "pipelines/wei/EXEC1/files/",
            "nextPipelineManifestS3Key": "pipelines/workflowExecutionInputs/EXEC1/pipeline2/manifest.json",
            "nextPipelineConfigS3Key": "pipelines/workflowExecutionInputs/EXEC1/pipeline2/config.json",
        }
        captured = {}
        put_object = MagicMock(side_effect=lambda **kw: captured.update({kw["Key"]: kw["Body"]}))
        with patch.object(ipt.dynamodb, "Table", return_value=inputs_table), \
             patch.object(ipt.s3c, "put_object", put_object), \
             patch.object(ipt.eo, "list_current_output_files", return_value=[
                 {"key": "pipelines/wei/EXEC1/files/test/pump.e57", "relativePath": "/test/pump.e57",
                  "versionId": "ov5", "fileSize": 1, "contentType": ""}]):
            result = ipt.prepare_next_pipeline(body)
        envelope = json.loads(captured[body["nextPipelineManifestS3Key"]].decode("utf-8"))
        input_files = envelope["inputFiles"]
        by_rel = {m["relativePath"]: m for m in input_files}
        # pump.e57 shadowed -> output version; scan.las untouched -> original asset file.
        assert by_rel["/test/pump.e57"]["key"] == "pipelines/wei/EXEC1/files/test/pump.e57"
        assert by_rel["/test/pump.e57"]["versionId"] == "ov5"
        assert by_rel["/test/scan.las"]["key"] == "a1xyz/test/scan.las"
        # No duplicate entry for the shadowed path (it shadows in place, not appended).
        assert len([m for m in input_files if m["relativePath"] == "/test/pump.e57"]) == 1
        # Shadowed entry keeps the original asset identity for traceability.
        assert by_rel["/test/pump.e57"]["assetId"] == "a1"
        assert result["inputManifestS3Location"].endswith("pipeline2/manifest.json")


# ============================ error handler ============================

@pytest.mark.unit
class TestHandleExecutionError:
    def test_reconciles_failed_execution(self):
        # The error-handler payload carries NO pipeline id (Step Functions can't inject the
        # failing state name into a static payload) -- the handler attributes the failure to
        # the in-flight (non-terminal) pipeline rows it fetches.
        body = {"workflowExecutionId": "EXEC1", "workflowDatabaseId": "wdb", "workflowId": "wf"}
        error_info = {"Error": "States.TaskFailed",
                      "Cause": json.dumps({"errorMessage": "boom"})}
        # One in-flight pipeline (gets a log row) + one already-SUCCEEDED (skipped).
        pipeline_rows = [
            {"pipelineExecutionId": "P1", "workflowExecutionId": "EXEC1", "executionStatus": "RUNNING"},
            {"pipelineExecutionId": "P0", "workflowExecutionId": "EXEC1", "executionStatus": "SUCCEEDED"},
        ]
        mark_terminal = MagicMock()
        finalize = MagicMock()
        logs_table = MagicMock()
        main_table = MagicMock(query=MagicMock(return_value={"Items": [{"executionStatus": "RUNNING"}]}))

        def _table(name):
            return logs_table if name == heh.pipeline_execution_logs_table else main_table

        with patch.object(heh, "_get_pipeline_rows", return_value=pipeline_rows), \
             patch.object(heh.eo, "mark_inflight_pipelines_terminal", mark_terminal), \
             patch.object(heh.eo, "finalize_main_row", finalize), \
             patch.object(heh, "_fetch_execution_log", return_value="full log"), \
             patch.object(heh.dynamodb, "Table", side_effect=_table):
            resp = heh.lambda_handler({"body": body, "errorInfo": error_info}, MagicMock())

        assert resp == {"handled": True}
        # In-flight pipelines marked FAILED.
        mark_terminal.assert_called_once()
        assert mark_terminal.call_args.args[3] == "FAILED"
        # Main row finalized FAILED with the extracted error message + log.
        finalize.assert_called_once()
        fkw = finalize.call_args
        assert "boom" in fkw.kwargs["execution_error"]
        assert fkw.kwargs["execution_log"] == "full log"
        # REGRESSION (Fix 4): a log row is written for the in-flight pipeline (P1) even with NO
        # threaded pipeline id, and NOT for the already-terminal one (P0).
        assert logs_table.put_item.call_count == 1
        assert logs_table.put_item.call_args.kwargs["Item"]["pipelineExecutionId"] == "P1"

    def test_error_handler_never_raises(self):
        # Even if reconciliation blows up, the handler returns (so the SFN reaches Fail).
        with patch.object(heh, "reconcile_failed_execution", side_effect=Exception("kaboom")):
            resp = heh.lambda_handler({"body": {"workflowExecutionId": "E"}, "errorInfo": {}}, MagicMock())
        assert resp == {"handled": True}

    def test_extract_error_message_from_cause_json(self):
        msg = heh._extract_error_message({"Error": "X", "Cause": json.dumps({"errorMessage": "detail"})})
        assert "detail" in msg
