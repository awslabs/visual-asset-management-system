# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The {{jobName}} template tag renders a value unique to each execution and to each step within it.

The workflow record's jobNames are generated once, when the workflow is deployed, so they are the
same on every run. The tag renders `{stepJobName}-{pipelineExecutionId}` instead: the pipeline
execution id is minted per step per run, which separates two runs of one workflow AND two steps that
carry the same stored job name.

Both producers are covered: the execute handler renders step 1 (its output path extension, its
config body, and the extension it writes into the manifest), and the interim lambda renders every
later step's config body. The run-bucket output prefix keeps the STORED job name, because the
deployed state machine bakes that name into the prefixes its later states list.
"""

import json
import os
import re

import pytest
from unittest.mock import MagicMock, patch

# executeWorkflow and interimPipelineTracking resolve these at import.
for _k, _v in {
    "ASSET_STORAGE_TABLE_NAME": "t-assets",
    "WORKFLOW_STORAGE_TABLE_V2_NAME": "t-wf-v2",
    "PIPELINE_STORAGE_TABLE_V2_NAME": "t-pipe-v2",
    "PIPELINE_TEMPLATES_STORAGE_TABLE_NAME": "t-templates",
    "PIPELINE_TEMPLATE_TAG_SCHEMA_STORAGE_TABLE_NAME": "t-tagschema",
    "S3_ASSET_BUCKETS_STORAGE_TABLE_NAME": "t-buckets",
    "S3_ASSETAUXILIARY_STORAGE_BUCKET": "t-aux",
    "METADATA_SERVICE_LAMBDA_FUNCTION_NAME": "t-md-svc",
    "WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME": "t-exec-v2",
    "PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME": "t-pexec",
    "PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME": "t-pin-md",
    "PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME": "t-pin-cfg",
    "PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME": "t-of",
    "WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME": "t-wf-inputs",
    "WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME": "t-wf-cfg",
}.items():
    os.environ.setdefault(_k, _v)

from backend.backend.handlers.workflows import executeWorkflow as ewv2  # noqa: E402
from backend.backend.handlers.workflows.sfn import interimPipelineTracking as ipt  # noqa: E402

# Taken from the handlers' own module attributes so the helper under test is the one they call.
er = ewv2.er

MOD = "backend.backend.handlers.workflows.executeWorkflow"

# One path segment of the id character set: what a stored job name and a GUID are made of.
_SINGLE_SEGMENT = re.compile(r"^[-_A-Za-z0-9]+$")

_CONFIG_BODY = json.dumps({"job": "{{jobName}}", "out": "{{outputFileBaseExecutionPathExtension}}"})


def _pipeline(pipeline_id, job_name):
    return {
        "pipelineId": pipeline_id, "databaseId": "GLOBAL", "_jobName": job_name,
        "systemConfig": {"inputFileArity": "one"},
        "executionConfig": {"executionType": "Lambda", "waitForCallback": "Disabled"},
    }


def _workflow(job_names):
    return {
        "workflowId": "wf1", "databaseId": "wfdb",
        "workflow_arn": "arn:aws:states:us-east-1:1:stateMachine:wf1",
        "jobNames": list(job_names), "systemConfig": {"metadataInputs": {}},
    }


def _launch(workflow, pipelines, output_extension="/{{jobName}}/"):
    """Launch one execution and return what it wrote: executionId, the SFN input, step 1's manifest,
    and step 1's rendered config body (parsed)."""
    selected_inputs = [{"databaseId": "db1", "assetId": "a1",
                        "relativeFileKey": "/model.glb", "versionId": ""}]
    asset_records = {("db1", "a1"): {"databaseId": "db1", "assetId": "a1",
                                     "assetLocation": {"Key": "a1/"}, "bucketId": "b1"}}
    resolved = {f"GLOBAL:{p['pipelineId']}": {"renderedConfig": _CONFIG_BODY, "configFormat": "json"}
                for p in pipelines}
    with patch(f"{MOD}.s3c") as s3, \
            patch(f"{MOD}.sfn_client") as sfn, \
            patch(f"{MOD}._asset_bucket_details", return_value={"bucketName": "asset-bucket"}), \
            patch(f"{MOD}._persist_execution_records"):
        sfn.start_execution.return_value = {"executionArn": "arn:exec"}
        execution_id = ewv2._launch_workflow(
            workflow, pipelines, resolved, selected_inputs, asset_records,
            None, "db1", "a1", "run-bucket", {}, "Manual", "", "user@example.com", "",
            output_extension=output_extension)
        puts = {c.kwargs["Key"]: c.kwargs["Body"] for c in s3.put_object.call_args_list}
        sent = json.loads(sfn.start_execution.call_args.kwargs["input"])
    manifest = json.loads(puts[er.pipeline_input_manifest_key(execution_id, 1)].decode("utf-8"))
    config = json.loads(puts[er.pipeline_input_config_key(execution_id, 1)].decode("utf-8"))
    return execution_id, sent, manifest, config


def _render_later_step(next_job_name, next_pipeline_execution_id):
    """Render a later step's config body in the interim lambda and return it (parsed). The body
    carries the two fields the deployed state machine threads for the next step: its stored job
    name and its pipeline execution id."""
    body = {
        "workflowExecutionId": "EXEC1", "workflowId": "wf1", "workflowDatabaseId": "wfdb",
        "executingUserName": "user@example.com",
        "nextPipelineId": "p2", "nextPipelineDatabaseId": "GLOBAL",
        "nextPipelineJobName": next_job_name,
        "nextPipelineExecutionId": next_pipeline_execution_id,
    }
    manifest = {"inputFiles": [], "outputTarget": {"fileBaseExecutionPathExtension": "/"}}
    cfg_table = MagicMock()
    cfg_table.get_item.return_value = {"Item": {"configFormat": "json"}}
    put_object = MagicMock()
    with patch.object(ipt.s3c, "get_object", MagicMock(return_value={
                "Body": MagicMock(read=lambda: _CONFIG_BODY.encode("utf-8"))})), \
            patch.object(ipt.s3c, "put_object", put_object), \
            patch.object(ipt.dynamodb, "Table", MagicMock(return_value=cfg_table)):
        ipt._render_next_pipeline_config(body, manifest, "run-bucket", "in/EXEC1/pipeline2/config.json")
    return json.loads(put_object.call_args.kwargs["Body"].decode("utf-8"))


@pytest.mark.unit
class TestRunJobNameValue:

    def test_the_value_is_the_stored_step_job_name_and_the_pipeline_execution_id(self):
        assert er.run_job_name("fb522-convert", "0123abcd") == "fb522-convert-0123abcd"

    def test_the_longest_stored_job_name_still_fits_with_the_whole_id(self):
        """A pipeline id or ref job name takes up to 63 characters, so a stored job name runs to 69.
        Trimming the id instead of the stored name would cost the value its uniqueness."""
        stored = "abcde-" + "p" * 63
        pipeline_execution_id = er.new_guid()
        value = er.run_job_name(stored, pipeline_execution_id)
        assert len(value) <= er.RUN_JOB_NAME_MAX_LENGTH
        assert value.endswith("-" + pipeline_execution_id)
        assert value.startswith("abcde-ppp")

    def test_the_value_is_a_single_path_segment(self):
        value = er.run_job_name("abcde-convert_step", er.new_guid())
        assert _SINGLE_SEGMENT.match(value), value


@pytest.mark.unit
class TestStepOneRendersPerRun:

    def test_two_executions_of_one_workflow_render_different_values(self):
        workflow, pipelines = _workflow(["fb522-p1"]), [_pipeline("p1", "p1")]
        _id_a, sent_a, _manifest_a, config_a = _launch(workflow, pipelines)
        _id_b, sent_b, _manifest_b, config_b = _launch(workflow, pipelines)
        assert config_a["job"] != config_b["job"]
        assert (sent_a["outputFileBaseExecutionPathExtension"]
                != sent_b["outputFileBaseExecutionPathExtension"])

    def test_the_value_is_the_stored_name_and_this_steps_pipeline_execution_id(self):
        workflow, pipelines = _workflow(["fb522-p1"]), [_pipeline("p1", "p1")]
        _execution_id, sent, _manifest, config = _launch(workflow, pipelines)
        assert config["job"] == f"fb522-p1-{sent['pipelineExecutionIds'][0]}"

    def test_one_step_renders_the_same_value_in_the_extension_the_manifest_and_the_config(self):
        workflow, pipelines = _workflow(["fb522-p1"]), [_pipeline("p1", "p1")]
        _execution_id, sent, manifest, config = _launch(workflow, pipelines)
        job = config["job"]
        assert _SINGLE_SEGMENT.match(job), job
        assert sent["outputFileBaseExecutionPathExtension"] == f"/{job}/"
        assert manifest["outputTarget"]["fileBaseExecutionPathExtension"] == f"/{job}/"
        assert config["out"] == f"/{job}/"

    def test_the_run_bucket_output_prefix_keeps_the_stored_job_name(self):
        """The deployed state machine lists pipelines/<pipelineName>/<stored job name>/output/<id>/,
        so the manifest must hand the pipeline that prefix and not one built from the tag value."""
        workflow, pipelines = _workflow(["fb522-p1"]), [_pipeline("p1", "p1")]
        execution_id, _sent, manifest, config = _launch(workflow, pipelines)
        assert manifest["outputs"]["files"] == f"pipelines/p1/fb522-p1/output/{execution_id}/files/"
        assert config["job"] not in manifest["outputs"]["files"]


@pytest.mark.unit
class TestLaterStepsRenderPerRun:

    def test_two_executions_render_different_values_for_the_same_step(self):
        config_a = _render_later_step("fb522-p2", er.new_guid())
        config_b = _render_later_step("fb522-p2", er.new_guid())
        assert config_a["job"] != config_b["job"]

    def test_the_value_is_the_stored_name_and_the_next_steps_pipeline_execution_id(self):
        assert _render_later_step("fb522-p2", "PEXEC2")["job"] == "fb522-p2-PEXEC2"

    def test_two_steps_sharing_a_stored_job_name_render_different_values(self):
        """Two refs with the same job name are generated within one deploy and usually draw the same
        uuid fragment, so their stored job names are identical. The run's step 1 and step 2 must
        still render distinct values: step 1 from the launch, step 2 from the interim lambda with
        the fields the state machine threads for it."""
        workflow = _workflow(["fb522-convert", "fb522-convert"])
        pipelines = [_pipeline("p1", "convert"), _pipeline("p2", "convert")]
        _execution_id, sent, _manifest, step_one = _launch(workflow, pipelines)
        step_two = _render_later_step(workflow["jobNames"][1], sent["pipelineExecutionIds"][1])
        assert step_one["job"] != step_two["job"]
        assert step_one["job"].startswith("fb522-convert-")
        assert step_two["job"].startswith("fb522-convert-")
