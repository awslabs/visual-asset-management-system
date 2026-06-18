# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import sys
import types
import json
import pytest
from unittest.mock import MagicMock

# executeWorkflow loads these env vars at import time.
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "t-buckets")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "t-assets")
os.environ.setdefault("PIPELINE_STORAGE_TABLE_NAME", "t-pipelines")
os.environ.setdefault("WORKFLOW_STORAGE_TABLE_NAME", "t-workflows")
os.environ.setdefault("WORKFLOW_EXECUTION_STORAGE_TABLE_NAME", "t-exec")
os.environ.setdefault("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "t-exec-v2")
os.environ.setdefault("PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME", "t-pexec")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_FILES_STORAGE_TABLE_NAME", "t-pin-files")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME", "t-pin-md")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME", "t-pin-cfg")
os.environ.setdefault("WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME", "t-wf-inputs")
os.environ.setdefault("WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME", "t-wf-cfg")
os.environ.setdefault("S3_ASSETAUXILIARY_STORAGE_BUCKET", "t-aux")
os.environ.setdefault("METADATA_SERVICE_LAMBDA_FUNCTION_NAME", "t-md-svc")

# The handlers.workflows package __init__ imports get_task_builder from
# common.stepfunctions_builder at import time. The shared test mock package does
# not provide this submodule, so register a lightweight stub before importing the
# handler. The persist tests below do not exercise ASL generation.
if "common.stepfunctions_builder" not in sys.modules:
    _sf_builder_stub = types.ModuleType("common.stepfunctions_builder")
    _sf_builder_stub.get_task_builder = lambda *a, **k: None
    sys.modules["common.stepfunctions_builder"] = _sf_builder_stub

from backend.backend.handlers.workflows import executeWorkflow as ew


@pytest.mark.unit
class TestPersistExecutionRecords:
    def _tables(self):
        """Return a dict of mocked tables keyed by table name, recording put_item calls."""
        puts = {}

        def make_table(name):
            t = MagicMock()
            puts[name] = []
            t.put_item.side_effect = lambda Item, _n=name: puts[_n].append(Item)
            return t

        dynamo = MagicMock()
        dynamo.Table.side_effect = make_table
        return dynamo, puts

    def test_writes_one_row_per_pipeline_plus_first_pipeline_inputs(self):
        dynamo, puts = self._tables()
        pipelines = [
            {"name": "p1", "databaseId": "db", "pipelineType": "standardFile",
             "pipelineExecutionType": "Lambda", "waitForCallback": "Disabled",
             "userProvidedResource": json.dumps({"resourceId": "arn:fn1", "resourceType": "Lambda"})},
            {"name": "p2", "databaseId": "db", "pipelineType": "standardFile",
             "pipelineExecutionType": "SQS", "waitForCallback": "Enabled",
             "userProvidedResource": json.dumps({"resourceId": "https://sqs/q", "resourceType": "SQS"})},
        ]
        result = ew.persist_execution_records(
            dynamo=dynamo,
            execution_id="EXEC1",
            workflow_arn="arn:aws:states:us-east-1:1:stateMachine:vams-wf",
            workflow_execution_arn="arn:aws:states:us-east-1:1:execution:vams-wf:EXEC1",
            database_id="db", asset_id="a1",
            workflow_database_id="wdb", workflow_id="wf",
            input_asset_file_key="folder/x.glb",
            asset_bucket="abkt", aux_bucket="auxbkt",
            triggered_by_user_id="user@x", trigger_type="Manual",
            execution_log_group_arn="arn:lg",
            pipelines=pipelines,
            first_job_name="abcde-p1",
            input_metadata={"VAMS": {}},
            input_configuration="{}",
        )
        # One PipelineExecutions row per pipeline
        assert len(puts["t-pexec"]) == 2
        # endStatePipeline set true on the LAST pipeline only
        end_flags = {row["pipelineId"]: row["endStatePipeline"] for row in puts["t-pexec"]}
        assert end_flags["p1"] == "false" and end_flags["p2"] == "true"
        # chain: p2.from == p1.pipelineExecutionId
        rows = {r["pipelineId"]: r for r in puts["t-pexec"]}
        assert rows["p1"]["from_pipeline_execution_id"] == ""
        assert rows["p2"]["from_pipeline_execution_id"] == rows["p1"]["pipelineExecutionId"]
        # Main V2 row written once, no asset coupling
        assert len(puts["t-exec-v2"]) == 1
        assert "databaseId:assetId" not in puts["t-exec-v2"][0]
        assert puts["t-exec-v2"][0]["executionId"] == "EXEC1"
        # Workflow-level input + config written once each
        assert len(puts["t-wf-inputs"]) == 1
        assert len(puts["t-wf-cfg"]) == 1
        # First-pipeline input rows present (files + config); metadata optional
        assert len(puts["t-pin-files"]) == 1
        assert len(puts["t-pin-cfg"]) == 1
        # The first-pipeline input file is linked to p1's pipelineExecutionId
        assert puts["t-pin-files"][0]["pipelineExecutionId"] == rows["p1"]["pipelineExecutionId"]
        # returns the end-state pipeline-execution id (p2)
        assert result["endStatePipelineExecutionId"] == rows["p2"]["pipelineExecutionId"]

    def test_empty_pipelines_safe(self):
        dynamo, puts = self._tables()
        result = ew.persist_execution_records(
            dynamo=dynamo,
            pipeline_execution_ids=None,
            execution_id="EXEC1",
            workflow_arn="arn:aws:states:us-east-1:1:stateMachine:vams-wf",
            workflow_execution_arn="arn:aws:states:us-east-1:1:execution:vams-wf:EXEC1",
            database_id="db", asset_id="a1",
            workflow_database_id="wdb", workflow_id="wf",
            input_asset_file_key="folder/x.glb",
            asset_bucket="abkt", aux_bucket="auxbkt",
            triggered_by_user_id="user@x", trigger_type="Manual",
            execution_log_group_arn="arn:lg",
            pipelines=[],
            first_job_name="",
            input_metadata={},
            input_configuration="",
        )
        # No end-state id with no pipelines, and no IndexError
        assert result["endStatePipelineExecutionId"] == ""
        # Main + workflow-level inputs/config written once each
        assert len(puts["t-exec-v2"]) == 1
        assert len(puts["t-wf-inputs"]) == 1
        assert len(puts["t-wf-cfg"]) == 1
        # No pipeline-execution or first-pipeline input rows
        assert len(puts.get("t-pexec", [])) == 0
        assert len(puts.get("t-pin-files", [])) == 0

    def test_input_metadata_row_conditional(self):
        pipelines = [
            {"name": "p1", "databaseId": "db", "pipelineType": "standardFile",
             "pipelineExecutionType": "Lambda", "waitForCallback": "Disabled",
             "userProvidedResource": json.dumps({"resourceId": "arn:fn1", "resourceType": "Lambda"})},
            {"name": "p2", "databaseId": "db", "pipelineType": "standardFile",
             "pipelineExecutionType": "SQS", "waitForCallback": "Enabled",
             "userProvidedResource": json.dumps({"resourceId": "https://sqs/q", "resourceType": "SQS"})},
        ]
        common_kwargs = dict(
            execution_id="EXEC1",
            workflow_arn="arn:aws:states:us-east-1:1:stateMachine:vams-wf",
            workflow_execution_arn="arn:aws:states:us-east-1:1:execution:vams-wf:EXEC1",
            database_id="db", asset_id="a1",
            workflow_database_id="wdb", workflow_id="wf",
            input_asset_file_key="folder/x.glb",
            asset_bucket="abkt", aux_bucket="auxbkt",
            triggered_by_user_id="user@x", trigger_type="Manual",
            execution_log_group_arn="arn:lg",
            pipelines=pipelines,
            first_job_name="abcde-p1",
            input_configuration="{}",
        )

        # With metadata present: exactly one t-pin-md row for the FIRST pipeline.
        dynamo, puts = self._tables()
        ew.persist_execution_records(dynamo=dynamo, input_metadata={"VAMS": {"k": "v"}}, **common_kwargs)
        assert len(puts["t-pin-md"]) == 1
        first_pexec_id = puts["t-pin-files"][0]["pipelineExecutionId"]
        assert puts["t-pin-md"][0]["pipelineExecutionId"] == first_pexec_id

        # With empty metadata: ZERO t-pin-md rows.
        dynamo2, puts2 = self._tables()
        ew.persist_execution_records(dynamo=dynamo2, input_metadata={}, **common_kwargs)
        assert len(puts2.get("t-pin-md", [])) == 0


@pytest.mark.unit
class TestLaunchWorkflow:
    def _tables(self):
        """Return a dict of mocked tables keyed by table name, recording put_item calls."""
        puts = {}

        def make_table(name):
            t = MagicMock()
            puts[name] = []
            t.put_item.side_effect = lambda Item, _n=name: puts[_n].append(Item)
            return t

        dynamo = MagicMock()
        dynamo.Table.side_effect = make_table
        return dynamo, puts

    def test_launch_workflow_threads_sfn_input(self):
        from unittest.mock import patch

        pipelines = [
            {"name": "p1", "databaseId": "db", "pipelineType": "standardFile",
             "pipelineExecutionType": "Lambda", "waitForCallback": "Disabled",
             "userProvidedResource": json.dumps({"resourceId": "arn:fn1", "resourceType": "Lambda"})},
            {"name": "p2", "databaseId": "db", "pipelineType": "standardFile",
             "pipelineExecutionType": "SQS", "waitForCallback": "Enabled",
             "userProvidedResource": json.dumps({"resourceId": "https://sqs/q", "resourceType": "SQS"})},
        ]

        dynamo, _puts = self._tables()
        start_exec = MagicMock(return_value={
            "executionArn": "arn:aws:states:us-east-1:1:execution:vams-wf:NAME"
        })

        with patch.object(ew.sfn_client, "start_execution", start_exec), \
                patch.object(ew, "dynamodb", dynamo):
            execution_id = ew.launchWorkflow(
                "abkt", "a1/", "folder/x.glb",
                "arn:aws:states:us-east-1:1:stateMachine:vams-wf",
                "db", "a1", "wdb", "wf",
                "user@x", {"requestContext": {}},
                pipelines, {"VAMS": {}}, "Manual",
            )

        # start_execution called with name == returned executionId (the ASL contract)
        assert start_exec.call_count == 1
        call_kwargs = start_exec.call_args.kwargs
        assert call_kwargs["name"] == execution_id
        # The threaded SFN input carries the execution id + a non-empty end-state id
        sfn_input = json.loads(call_kwargs["input"])
        assert sfn_input["workflowExecutionId"] == execution_id
        assert sfn_input["endStatePipelineExecutionId"]
