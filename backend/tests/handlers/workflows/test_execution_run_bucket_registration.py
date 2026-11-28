# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The execution detail names the run bucket's registration (#1891).

A pipeline-execution row records the run bucket's bucketId and the area VAMS owns in it beside the
bucket name it hands the pipeline, and `renderedConfigLocation` adds `bucketId` and `relativeKey`
(additive: `bucket` and `key` keep their values). A row recorded before the registration was captured
gets the two fields it had before and no others.
"""

import os

import pytest

for _name, _value in (
    ("ASSET_STORAGE_TABLE_NAME", "t-assets"),
    ("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "t-exec-v2"),
    ("WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME", "t-wf-inputs"),
    ("PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME", "t-pexec"),
    ("WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME", "t-wf-cfg"),
    ("PIPELINE_EXECUTION_INPUT_FILES_STORAGE_TABLE_NAME", "t-pin-files"),
    ("PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME", "t-pin-md"),
    ("PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME", "t-pin-cfg"),
    ("PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME", "t-of"),
    ("PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE_NAME", "t-om"),
    ("PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE_NAME", "t-or"),
    ("PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME", "t-logs"),
    ("WORKFLOW_STORAGE_TABLE_NAME", "t-workflows"),
    ("PIPELINE_STORAGE_TABLE_NAME", "t-pipelines"),
    ("EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME", "t-execv2"),
):
    os.environ.setdefault(_name, _value)

from backend.backend.common.workflows import executionRecords as er  # noqa: E402
from backend.backend.handlers.workflows import executionService as es  # noqa: E402

_EXECUTE_WORKFLOW = os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend", "handlers",
                                 "workflows", "executeWorkflow.py")
KEY = "vams/executions/e1/input/1/config.json"


def _record(**kwargs):
    return er.build_pipeline_execution_record(
        pipeline_execution_id="pe1", workflow_execution_id="e1", pipeline_database_id="GLOBAL",
        pipeline_id="p1", end_state_pipeline=True, s3_asset_bucket="customer-bucket",
        s3_aux_bucket="aux", output_prefixes={}, input_metadata_file_prefix="",
        input_config_file_prefix=KEY, aux_temp_prefix="", aux_preview_prefix="",
        pipeline_execution_type="Lambda", wait_for_callback="Disabled", pipeline_resource_arn="", **kwargs)


@pytest.mark.unit
class TestRunBucketRegistration:
    def test_the_row_records_the_registration_and_its_prefix(self):
        row = _record(s3_asset_bucket_id="reg-1", s3_asset_bucket_base_prefix="vams/")
        assert (row["S3AssetPipelineBucketId"], row["S3AssetPipelineBucketBasePrefix"]) == ("reg-1", "vams/")
        assert row["S3AssetPipelineBucket"] == "customer-bucket"

    def test_a_row_without_the_registration_has_no_fields_for_it(self):
        row = _record()
        assert "S3AssetPipelineBucketId" not in row and "S3AssetPipelineBucketBasePrefix" not in row

    def test_the_location_adds_the_registration_and_the_relative_key(self):
        location = es.rendered_config_location(
            {"S3AssetPipelineBucket": "customer-bucket", "S3AssetPipelineBucketId": "reg-1",
             "S3AssetPipelineBucketBasePrefix": "vams/"}, KEY)
        assert location == {"bucket": "customer-bucket", "key": KEY, "bucketId": "reg-1",
                            "relativeKey": "executions/e1/input/1/config.json"}

    def test_a_location_at_the_bucket_root_keeps_the_key_as_the_relative_key(self):
        location = es.rendered_config_location(
            {"S3AssetPipelineBucket": "vams-bucket", "S3AssetPipelineBucketId": "reg-1"}, "executions/e1/c.json")
        assert location["relativeKey"] == "executions/e1/c.json"

    def test_a_run_recorded_before_the_registration_keeps_its_two_fields(self):
        location = es.rendered_config_location({"S3AssetPipelineBucket": "vams-bucket"}, KEY)
        assert location == {"bucket": "vams-bucket", "key": KEY}

    def test_the_launch_records_the_run_bucket_registration(self):
        with open(os.path.abspath(_EXECUTE_WORKFLOW), encoding="utf-8") as f:
            source = f.read()
        assert 'run_bucket_id = run_bucket_row.get("bucketId")' in source
        assert "s3_asset_bucket_id=run_bucket_id, s3_asset_bucket_base_prefix=run_prefix" in source
