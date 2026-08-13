# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import sys
import types
import pytest
from unittest.mock import MagicMock

# models.assetsV3 fails to import under Python 3.13 due to a pre-existing,
# unrelated Pydantic v1 regex incompatibility. The handler only needs
# AssetUploadTableModel inside create_external_upload_record, which is not
# exercised by these unit tests. Stub the module before importing the handler.
if "models.assetsV3" not in sys.modules:
    _assetsv3_stub = types.ModuleType("models.assetsV3")
    _assetsv3_stub.AssetUploadTableModel = MagicMock()
    sys.modules["models.assetsV3"] = _assetsv3_stub

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "t-buckets")
os.environ.setdefault("METADATA_SERVICE_LAMBDA_FUNCTION_NAME", "t-md")
os.environ.setdefault("FILE_UPLOAD_LAMBDA_FUNCTION_NAME", "t-fu")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "t-assets")
os.environ.setdefault("ASSET_UPLOAD_TABLE_NAME", "t-upload")
os.environ.setdefault("DATABASE_STORAGE_TABLE_NAME", "t-db")
# Unconditional (not setdefault): the root conftest seeds a default for this var so other handlers
# can import, but this module pins its own name and asserts on it, so it must override before the
# processWorkflowExecutionOutput handler resolves its tables at import below.
os.environ["WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME"] = "t-exec-v2"
os.environ.setdefault("PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME", "t-pexec")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME", "t-of")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE_NAME", "t-om")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE_NAME", "t-or")
os.environ.setdefault("PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME", "t-logs")

# The handlers.workflows package __init__ imports get_task_builder from
# common.workflows.stepfunctions_builder at import time. The shared test mock package does
# not provide this submodule, so register a lightweight stub before importing the
# handler. These tests do not exercise ASL generation.
if "common.workflows.stepfunctions_builder" not in sys.modules:
    _sf_builder_stub = types.ModuleType("common.workflows.stepfunctions_builder")
    _sf_builder_stub.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _sf_builder_stub

from backend.backend.handlers.workflows.sfn import processWorkflowExecutionOutput as po


@pytest.mark.unit
class TestRecordExecutionOutputs:
    def _dynamo(self):
        puts, updates = {}, {}

        def make_table(name):
            t = MagicMock()
            puts[name] = []
            updates[name] = []
            t.put_item.side_effect = lambda Item, _n=name: puts[_n].append(Item)
            t.update_item.side_effect = lambda _n=name, **kw: updates[_n].append(kw)
            return t

        dynamo = MagicMock()
        dynamo.Table.side_effect = make_table
        return dynamo, puts, updates

    def test_writes_output_files_and_completion_status(self):
        # Pin the handler's resolved main-table name regardless of global import order (the root
        # conftest may have seeded a different default before this module's env set took effect).
        po.workflow_execution_database_v2 = "t-exec-v2"
        dynamo, puts, updates = self._dynamo()
        po.record_execution_outputs(
            dynamo=dynamo,
            workflow_execution_id="E1",
            end_state_pipeline_execution_id="P9",
            workflow_database_id="wdb", workflow_id="wf",
            bucket_name="abkt",
            output_files=[{"fileType": "file", "relativeFilePath": "x.glb",
                           "s3Key": "k/x.glb", "fileSize": 10, "contentType": "model/gltf-binary",
                           "s3VersionId": "v1"}],
            output_metadata=[{"targetFilePath": "/x.glb", "metadataKey": "c", "metadataValue": "red",
                              "sourceMetadataFileRelativePath": "x.glb.metadata.json"}],
            output_results=[{"relativeFilePath": "/x.glb.result.json",
                             "resultsContent": '{"score": 0.9}', "s3Key": "k/results/x.glb.result.json"}],
            result_log="done", execution_log="full execution log text",
            log_group_arn="arn:lg", log_stream_name="s",
            execution_status="SUCCEEDED",
        )
        assert len(puts["t-of"]) == 1 and puts["t-of"][0]["fileType:relativeFilePath"] == "file:x.glb"
        assert len(puts["t-om"]) == 1
        assert len(puts["t-or"]) == 1 and puts["t-or"][0]["relativeFilePath"] == "/x.glb.result.json"
        assert puts["t-or"][0]["resultsContent"] == '{"score": 0.9}'
        assert len(puts["t-logs"]) == 1 and puts["t-logs"][0]["resultLog"] == "done"
        # completion status updates on both the end-state pipeline-exec row and the main row
        assert len(updates["t-pexec"]) == 1
        assert len(updates["t-exec-v2"]) == 1
        # The main-row update captures the full execution log on completion (success path),
        # and the per-pipeline logs row carries it as well.
        main_update = updates["t-exec-v2"][0]
        assert main_update["ExpressionAttributeValues"][":lg"] == "full execution log text"
        assert "executionLog" in main_update["UpdateExpression"]
        assert puts["t-logs"][0]["errorLog"] == "full execution log text"

    def test_no_op_when_no_end_state_id(self):
        dynamo, puts, updates = self._dynamo()
        po.record_execution_outputs(
            dynamo=dynamo, workflow_execution_id="", end_state_pipeline_execution_id="",
            workflow_database_id="", workflow_id="", bucket_name="b",
            output_files=[], output_metadata=[], output_results=[], result_log="", execution_log="",
            log_group_arn="", log_stream_name="", execution_status="SUCCEEDED",
        )
        # Nothing written when there is no execution context (non-workflow/direct invoke)
        assert all(len(v) == 0 for v in puts.values())


@pytest.mark.unit
class TestResultsOnly:
    """Results-only executions (outputLocationType 'none'): no output asset, no file/metadata
    ingestion — only results text + logs + completion status recorded against the execution."""

    def test_lambda_handler_results_only_skips_asset_ingestion(self):
        from unittest.mock import patch
        event = {"body": {
            "outputLocationType": "none",
            "outputAssetId": "", "outputDatabaseId": "",
            "workflowExecutionId": "E1", "endStatePipelineExecutionId": "P1",
            "workflowDatabaseId": "GLOBAL", "workflowId": "wf1",
            "workflowExecutionS3InputOutputBucket": "run-bucket",
            "resultsPathKey": "pipelines/wf1/E1/results/",
            "executingUserName": "SYSTEM_USER", "executingRequestContext": {},
        }}
        with patch.object(po, "lookup_existing_asset") as m_lookup, \
             patch.object(po, "verify_get_path_objects",
                          return_value={"Contents": [{"Key": "pipelines/wf1/E1/results/out.json"}]}), \
             patch.object(po, "s3c") as m_s3, \
             patch.object(po, "_fetch_execution_logs", return_value=("log text", "stream")), \
             patch.object(po, "record_execution_outputs") as m_record:
            m_s3.get_object.return_value = {
                "Body": MagicMock(read=lambda: b'{"answer": "hello from the LLM"}')}
            resp = po.lambda_handler(event, MagicMock())
        assert resp["statusCode"] == 200
        # No output asset was looked up or authorized.
        m_lookup.assert_not_called()
        # record_execution_outputs called with empty files/metadata + the collected results + empty bucket.
        assert m_record.call_count == 1
        kw = m_record.call_args.kwargs
        assert kw["output_files"] == [] and kw["output_metadata"] == []
        assert kw["bucket_name"] == ""
        assert kw["output_results"][0]["resultsContent"] == '{"answer": "hello from the LLM"}'
        assert kw["output_results"][0]["relativeFilePath"] == "/out.json"
        assert kw["execution_status"] == "SUCCEEDED"
