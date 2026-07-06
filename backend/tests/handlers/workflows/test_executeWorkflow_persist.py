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
# common.workflows.stepfunctions_builder at import time. The shared test mock package does
# not provide this submodule, so register a lightweight stub before importing the
# handler. The persist tests below do not exercise ASL generation.
if "common.workflows.stepfunctions_builder" not in sys.modules:
    _sf_builder_stub = types.ModuleType("common.workflows.stepfunctions_builder")
    _sf_builder_stub.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _sf_builder_stub

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
            input_config_keys=["k1", "k2"],
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
        assert puts["t-exec-v2"][0]["workflowExecutionId"] == "EXEC1"
        # Workflow-level input + config written once each
        assert len(puts["t-wf-inputs"]) == 1
        assert len(puts["t-wf-cfg"]) == 1
        # Stage 2: input asset FILES are tracked at the workflow level, NOT per-pipeline.
        assert len(puts.get("t-pin-files", [])) == 0
        # Each pipeline gets its OWN input configuration row (how it executes itself).
        assert len(puts["t-pin-cfg"]) == 2
        cfg_keys = {r["inputConfigurationFileS3Key"] for r in puts["t-pin-cfg"]}
        assert cfg_keys == {"k1", "k2"}
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

        # With metadata present: exactly one t-pin-md row, recorded under the FIRST
        # pipeline's execution id (the workflow-level input metadata row).
        dynamo, puts = self._tables()
        ew.persist_execution_records(dynamo=dynamo, input_metadata={"VAMS": {"k": "v"}}, **common_kwargs)
        assert len(puts["t-pin-md"]) == 1
        # The first pipeline-execution row is the first t-pexec put; metadata is keyed to it.
        first_pexec_id = puts["t-pexec"][0]["pipelineExecutionId"]
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
        # launchWorkflow now writes input-definition files to the asset bucket via s3c.
        put_object = MagicMock()

        with patch.object(ew.sfn_client, "start_execution", start_exec), \
                patch.object(ew.s3c, "put_object", put_object), \
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
        # Stage 2: SFN input carries the pre-generated per-pipeline ids.
        assert len(sfn_input["pipelineExecutionIds"]) == 2
        # The SFN input is lean: it does NOT carry the input-definition file keys nor the
        # input asset identity/metadata (the ASL recomputes the manifest/config S3 keys, and
        # the pipelines resolve identity + metadata from the manifest). Those convenience
        # top-level copies were removed. inputAssetLocationKey is likewise dropped: each input
        # file is self-locating in the manifest (per-file assetFilesS3Root).
        for dead_key in ("inputMetadataFileS3Key", "firstPipelineConfigS3Key",
                         "firstPipelineManifestS3Key", "inputConfigKeys",
                         "assetId", "databaseId", "inputMetadata", "inputAssetLocationKey"):
            assert dead_key not in sfn_input
        # The primary input file key IS still threaded (some pipelines build aux paths from it).
        assert sfn_input["inputAssetFileKey"] == "folder/x.glb"
        # The output target identity IS threaded (where outputs land).
        assert sfn_input["outputAssetId"] and sfn_input["outputDatabaseId"]
        # Input-definition files are still written to the asset bucket: metadata + 2 configs + manifest.
        assert put_object.call_count == 4
        put_buckets = {c.kwargs["Bucket"] for c in put_object.call_args_list}
        assert put_buckets == {"abkt"}

    def _written(self, put_object, suffix):
        """Return the JSON body of the put_object call whose Key ends with `suffix`."""
        for c in put_object.call_args_list:
            if c.kwargs["Key"].endswith(suffix):
                return json.loads(c.kwargs["Body"].decode("utf-8"))
        return None

    def _run_launch(self, pipelines, stored_job_names=None):
        from unittest.mock import patch
        dynamo, _puts = self._tables()
        start_exec = MagicMock(return_value={
            "executionArn": "arn:aws:states:us-east-1:1:execution:vams-wf:NAME"})
        put_object = MagicMock()
        with patch.object(ew.sfn_client, "start_execution", start_exec), \
                patch.object(ew.s3c, "put_object", put_object), \
                patch.object(ew, "dynamodb", dynamo):
            execution_id = ew.launchWorkflow(
                "abkt", "a1/", "folder/x.glb",
                "arn:aws:states:us-east-1:1:stateMachine:vams-wf",
                "db", "a1", "wdb", "wf",
                "user@x", {"requestContext": {}},
                pipelines, {"VAMS": {"k": "v"}}, "Manual",
                storedJobNames=stored_job_names,
            )
        return execution_id, put_object

    def test_metadata_file_is_schema_versioned_envelope(self):
        # The shared metadata file is wrapped in the stamped envelope (Finding #2), preserving
        # the original metadata payload verbatim under 'metadata'.
        from backend.backend.common.workflows import executionRecords as er
        pipelines = [{"name": "p1", "databaseId": "db", "pipelineType": "standardFile",
                      "pipelineExecutionType": "Lambda", "waitForCallback": "Disabled",
                      "userProvidedResource": json.dumps({"resourceId": "arn:fn1", "resourceType": "Lambda"})}]
        _eid, put_object = self._run_launch(pipelines)
        md = self._written(put_object, "metadata.json")
        assert md["schemaVersion"] == er.METADATA_SCHEMA_VERSION
        assert md["metadata"] == {"VAMS": {"k": "v"}}

    def test_manifest_aux_prefix_has_single_slash(self):
        # The manifest aux prefixes must not contain a double slash after the bucket
        # (Finding #3): the raw file key, not a leading-slash-normalized key, is used.
        pipelines = [{"name": "p1", "databaseId": "db", "pipelineType": "standardFile",
                      "pipelineExecutionType": "Lambda", "waitForCallback": "Disabled",
                      "userProvidedResource": json.dumps({"resourceId": "arn:fn1", "resourceType": "Lambda"})}]
        _eid, put_object = self._run_launch(pipelines)
        manifest = self._written(put_object, "manifest.json")
        # The aux bucket resolves via ResourceKeys.ASSET_AUXILIARY_BUCKET; the root conftest's
        # S3_ASSET_AUXILIARY_BUCKET override (highest precedence) supplies the name.
        aux_bucket = os.environ["S3_ASSET_AUXILIARY_BUCKET"]
        assert manifest["auxTempPrefix"] == f"s3://{aux_bucket}/folder/x.glb/pipelines/p1/"
        assert manifest["auxPreviewPrefix"] == f"s3://{aux_bucket}/folder/x.glb/pipelines/p1/"
        assert "//" not in manifest["auxTempPrefix"].split("s3://", 1)[1]

    def test_manifest_outputs_use_stored_job_name(self):
        # Pipeline 1's manifest outputs use the ASL-stored job name so they resolve to the
        # SAME S3 folder the ASL hands the container (Finding #1).
        pipelines = [{"name": "p1", "databaseId": "db", "pipelineType": "standardFile",
                      "pipelineExecutionType": "Lambda", "waitForCallback": "Disabled",
                      "userProvidedResource": json.dumps({"resourceId": "arn:fn1", "resourceType": "Lambda"})}]
        eid, put_object = self._run_launch(pipelines, stored_job_names=["zz999-p1"])
        manifest = self._written(put_object, "manifest.json")
        # The stored job name segment appears in every output URI.
        assert f"s3://abkt/pipelines/p1/zz999-p1/output/{eid}/files/" == manifest["outputs"]["files"]
        assert "/zz999-p1/" in manifest["outputs"]["previews"]
        assert "/zz999-p1/" in manifest["outputs"]["metadata"]
        assert "/zz999-p1/" in manifest["outputs"]["results"]
