# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The end-state lambda releases an execution's perInputFileVersion locks once the main row is terminal.

Both end-state paths (asset write-back and results-only) reach record_execution_outputs, so the release
lives there. It derives the keys from the workflow's stored restriction and the execution's input rows,
touches nothing for the other restrictions, and never fails the recording that precedes it."""

import os
import sys
import types

import pytest
from botocore.exceptions import ClientError
from unittest.mock import MagicMock

# models.assetsV3 fails to import under Python 3.13 (pre-existing pydantic v1 regex incompatibility);
# the handler needs it only inside create_external_upload_record, which these tests never reach.
if "models.assetsV3" not in sys.modules:
    _assetsv3_stub = types.ModuleType("models.assetsV3")
    _assetsv3_stub.AssetUploadTableModel = MagicMock()
    sys.modules["models.assetsV3"] = _assetsv3_stub

for _name, _value in [
    ("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "t-buckets"),
    ("METADATA_SERVICE_LAMBDA_FUNCTION_NAME", "t-md"),
    ("FILE_UPLOAD_LAMBDA_FUNCTION_NAME", "t-fu"),
    ("ASSET_STORAGE_TABLE_NAME", "t-assets"),
    ("ASSET_UPLOAD_TABLE_NAME", "t-upload"),
    ("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "t-exec-v2"),
    ("PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME", "t-pexec"),
    ("PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME", "t-of"),
    ("PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE_NAME", "t-om"),
    ("PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE_NAME", "t-or"),
    ("PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME", "t-logs"),
    ("WORKFLOW_STORAGE_TABLE_V2_NAME", "t-wf-v2"),
    ("WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME", "t-wf-inputs"),
    ("WORKFLOW_EXECUTION_LOCKS_STORAGE_TABLE_NAME", "t-wf-locks"),
]:
    os.environ.setdefault(_name, _value)

if "common.workflows.stepfunctions_builder" not in sys.modules:
    _sf_builder_stub = types.ModuleType("common.workflows.stepfunctions_builder")
    _sf_builder_stub.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _sf_builder_stub

from backend.backend.handlers.workflows.sfn import processWorkflowExecutionOutput as po  # noqa: E402

el = po.el

WF_DB, WF_ID, EXEC = "wdb", "wf", "E1"
ROWS = [
    {"workflowExecutionId": EXEC, "databaseId": "db", "assetId": "a1",
     "inputAssetFileKey": "/a1/f.glb", "versionId": "v1"},
    {"workflowExecutionId": EXEC, "databaseId": "db", "assetId": "a1",
     "inputAssetFileKey": "/a1/g.glb", "versionId": "v2"},
]
KEYS = [el.build_lock_key(WF_DB, WF_ID, "db", "a1", "/a1/f.glb", "v1"),
        el.build_lock_key(WF_DB, WF_ID, "db", "a1", "/a1/g.glb", "v2")]


def _dynamo(restriction="perInputFileVersion", workflow_get_error=None):
    """Table router: the workflow row, the input rows, a lock table spy, and MagicMocks elsewhere."""
    workflow_table = MagicMock()
    if workflow_get_error is not None:
        workflow_table.get_item.side_effect = workflow_get_error
    else:
        workflow_table.get_item.return_value = {"Item": {
            "databaseId": WF_DB, "workflowId": WF_ID,
            "systemConfig": {"concurrencyRestriction": restriction}}}
    inputs_table = MagicMock()
    inputs_table.query.return_value = {"Items": list(ROWS)}
    locks_table = MagicMock()
    main_table = MagicMock()
    tables = {po.workflow_table_name: workflow_table, po.workflow_execution_inputs_table: inputs_table,
              po.workflow_execution_locks_table: locks_table, po.workflow_execution_database_v2: main_table}
    dynamo = MagicMock()
    dynamo.Table.side_effect = lambda name: tables.get(name, MagicMock())
    return dynamo, workflow_table, inputs_table, locks_table, main_table


def _record(dynamo, status="SUCCEEDED"):
    po.record_execution_outputs(
        dynamo=dynamo, workflow_execution_id=EXEC, end_state_pipeline_execution_id="P9",
        workflow_database_id=WF_DB, workflow_id=WF_ID, bucket_name="b",
        output_files=[], output_metadata=[], output_results=[],
        result_log="done", execution_log="", log_group_arn="", log_stream_name="",
        execution_status=status)


def _released_keys(locks_table):
    return [c.kwargs["Key"]["lockKey"] for c in locks_table.delete_item.call_args_list]


@pytest.mark.unit
class TestEndStateReleasesLocks:
    @pytest.mark.parametrize("status", ["SUCCEEDED", "FAILED"])
    def test_every_input_row_version_is_released_after_the_terminal_write(self, status):
        dynamo, _w, _i, locks_table, main_table = _dynamo()
        _record(dynamo, status)
        assert _released_keys(locks_table) == KEYS
        releases = locks_table.delete_item.call_args_list
        assert releases, "no lock was released"
        assert {(c.kwargs["Key"]["lockKey"], c.kwargs["ExpressionAttributeValues"][":e"]) for c in releases} == {
            (key, EXEC) for key in KEYS}
        assert {"workflowExecutionId" in c.kwargs["ConditionExpression"] for c in releases} == {True}
        # The terminal status write happened, and before the release.
        main_table.update_item.assert_called_once()
        assert dynamo.mock_calls.index(("Table", (po.workflow_execution_database_v2,), {})) < \
            dynamo.mock_calls.index(("Table", (po.workflow_execution_locks_table,), {}))

    def test_another_restriction_reads_no_input_rows_and_deletes_nothing(self):
        for restriction in ("none", "perAsset", "perInputFile"):
            dynamo, _w, inputs_table, locks_table, _m = _dynamo(restriction)
            _record(dynamo)
            inputs_table.query.assert_not_called()
            locks_table.delete_item.assert_not_called()

    def test_a_failing_release_never_fails_the_recording(self):
        dynamo, _w, _i, locks_table, main_table = _dynamo(workflow_get_error=ClientError(
            {"Error": {"Code": "InternalServerError", "Message": "boom"}}, "GetItem"))
        _record(dynamo)  # must not raise
        main_table.update_item.assert_called_once()
        locks_table.delete_item.assert_not_called()

    def test_no_execution_context_records_nothing_and_releases_nothing(self):
        dynamo, _w, _i, locks_table, _m = _dynamo()
        po.record_execution_outputs(
            dynamo=dynamo, workflow_execution_id="", end_state_pipeline_execution_id="",
            workflow_database_id=WF_DB, workflow_id=WF_ID, bucket_name="b",
            output_files=[], output_metadata=[], output_results=[], result_log="", execution_log="",
            log_group_arn="", log_stream_name="", execution_status="SUCCEEDED")
        dynamo.Table.assert_not_called()
        locks_table.delete_item.assert_not_called()


@pytest.mark.unit
class TestBothEndStatePathsReachTheReleaseSite:
    def test_the_results_only_path_records_through_the_same_function(self):
        # _process_results_only is the second end-state path; it reaches record_execution_outputs,
        # which is the one release site, so the results-only case needs no second hook.
        recorded = MagicMock()
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(po, "record_execution_outputs", recorded)
            mp.setattr(po, "_fetch_execution_logs", lambda *a, **k: ("", ""))
            mp.setattr(po, "verify_get_path_objects", lambda *a, **k: {})
            po._process_results_only({
                "workflowExecutionId": EXEC, "endStatePipelineExecutionId": "P9",
                "workflowDatabaseId": WF_DB, "workflowId": WF_ID, "outputLocationType": "none",
                "workflowExecutionS3InputOutputBucket": "run", "workflowExecutionS3InputOutputBasePrefix": "",
                "filesPathKey": "pipelines/p/j/output/E1/files/",
                "metadataPathKey": "pipelines/p/j/output/E1/metadata/",
                "previewPathKey": "pipelines/p/j/output/E1/previews/",
                "resultsPathKey": "pipelines/p/j/output/E1/results/"})
        recorded.assert_called_once()
        assert recorded.call_args.kwargs["workflow_execution_id"] == EXEC
        assert recorded.call_args.kwargs["workflow_database_id"] == WF_DB
        assert recorded.call_args.kwargs["workflow_id"] == WF_ID
