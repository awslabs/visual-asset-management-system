# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The fixed section of the execution-details view is bounded like every other part of it.

`pipelines` and `inputConfigurations` are charged against the response byte ceiling before the
collections divide what is left, but the charge is driven by each step's inline rendered configuration
body — capped in the low hundreds of KB per step, over a workflow that may carry a hundred steps. A run
whose steps carry large bodies must therefore return bounded configuration rather than a response over
the AWS Lambda synchronous-response limit, which returns no body at all; every step must still be
reported, a shortened body must be flagged, and renderedConfigLocation must survive as the caller's
route to the body the step actually ran with."""

import json
import os

import pytest
from unittest.mock import patch

# executionService resolves these at import (mirrors test_details_per_pipeline_metadata.py).
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "t-assets")
os.environ.setdefault("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "t-exec-v2")
os.environ.setdefault("WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME", "t-wf-inputs")
os.environ.setdefault("PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME", "t-pexec")
os.environ.setdefault("WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME", "t-wf-cfg")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_FILES_STORAGE_TABLE_NAME", "t-pin-files")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME", "t-pin-md")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME", "t-pin-cfg")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME", "t-of")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE_NAME", "t-om")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE_NAME", "t-or")
os.environ.setdefault("PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME", "t-logs")
os.environ.setdefault("WORKFLOW_STORAGE_TABLE_NAME", "t-workflows")
os.environ.setdefault("PIPELINE_STORAGE_TABLE_NAME", "t-pipelines")
os.environ.setdefault("EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME", "t-execv2")

from backend.backend.handlers.workflows import executionService as le

MOD = "backend.backend.handlers.workflows.executionService"

# The Lambda synchronous-response limit. A response over it fails the whole request with a 502 and no
# body, so it is the failure the bound exists to prevent.
LAMBDA_LIMIT = 6 * 1024 * 1024

CONFIG_S3_KEY = "executions/E1/input/0/config.json"


def _assemble(steps, config_kb, escape_heavy=False):
    """Assemble the details view for `steps` steps, each recording a rendered configuration body of
    `config_kb` KB. escape_heavy fills the body with characters JSON escapes, which is how a body's
    serialized size exceeds its character count."""
    prows = [{"pipelineExecutionId": f"pe{i}", "pipelineId": f"p{i}",
              "pipelineDatabaseId": "db", "S3AssetPipelineBucket": "bkt"} for i in range(steps)]
    body = ('"\\' if escape_heavy else "y") * (config_kb * 1024)

    def _all(table_name, key_condition):
        if table_name == le.pipeline_execution_input_configuration_table:
            return [{"inputConfiguration": body, "inputConfigurationTruncated": False,
                     "inputConfigurationFileS3Key": CONFIG_S3_KEY}]
        return []

    with patch(f"{MOD}.get_workflow_definition", return_value={}), \
         patch(f"{MOD}.get_pipeline_definition", return_value={}), \
         patch(f"{MOD}.get_pipeline_definitions", return_value={}), \
         patch(f"{MOD}._query_all", side_effect=_all), \
         patch(f"{MOD}.get_pipeline_execution_rows", return_value=prows), \
         patch(f"{MOD}._query_capped", return_value=([], False)), \
         patch(f"{MOD}.get_produced_file_versions", return_value={}):
        return le.assemble_execution_details(
            "E1", {"workflowId": "wf", "workflowDatabaseId": "db"}, config_row={})


def _response_bytes(details):
    return len(json.dumps(details, default=str).encode("utf-8"))


@pytest.mark.unit
class TestFixedSectionBound:
    def test_a_ten_step_run_with_large_configs_stays_under_the_lambda_limit(self):
        # 10 steps x 380 KB (the per-step inline cap) is over 3.8 MB of configuration on its own, with
        # no collection row in the response at all: the fixed section must yield.
        details = _assemble(10, 380)
        size = _response_bytes(details)
        assert size < LAMBDA_LIMIT, f"assembled response was {size} bytes"
        assert size <= le.DETAIL_RESPONSE_BYTE_CEILING, f"assembled response was {size} bytes"

    def test_a_hundred_step_run_with_large_configs_stays_under_the_lambda_limit(self):
        # The workflow step cap (models.workflows.MAX_SPECIFIED_PIPELINES), the worst case the view has
        # to serve.
        details = _assemble(100, 380)
        size = _response_bytes(details)
        assert size < LAMBDA_LIMIT, f"assembled response was {size} bytes"

    def test_escape_heavy_configs_stay_under_the_limit_too(self):
        # A body of quotes and backslashes doubles when serialized, so a share derived from character
        # counts alone is not a bound.
        details = _assemble(10, 380, escape_heavy=True)
        size = _response_bytes(details)
        assert size < LAMBDA_LIMIT, f"assembled response was {size} bytes"

    def test_every_step_is_still_reported(self):
        # A step's identity is what the view exists to report; only its body yields.
        details = _assemble(10, 380)
        assert [p["pipelineId"] for p in details["pipelines"]] == [f"p{i}" for i in range(10)]

    def test_a_bounded_fixed_section_is_named_in_truncated_collections(self):
        details = _assemble(10, 380)
        assert "pipelines" in details["truncatedCollections"]
        assert "inputConfigurations" in details["truncatedCollections"]

    def test_a_shortened_step_reports_the_truncation_flag(self):
        details = _assemble(10, 380)
        shortened = [p for p in details["pipelines"] if p["renderedConfigTruncated"]]
        assert shortened, "a bounded configuration body must be flagged on the step"

    def test_a_shortened_step_keeps_its_rendered_config_location(self):
        # The pointer to the fully rendered object is the caller's only route to what the step ran with
        # once the inline copy is bounded.
        details = _assemble(10, 380)
        for pipeline in details["pipelines"]:
            if pipeline["renderedConfigTruncated"]:
                location = pipeline.get("renderedConfigLocation") or {}
                assert location.get("key") == CONFIG_S3_KEY
                assert location.get("bucket") == "bkt"

    def test_the_flat_list_does_not_echo_the_body_the_step_entry_carries(self):
        # The same body counted in both sections charges the response twice for one fact.
        details = _assemble(3, 380)
        assert details["inputConfigurations"]
        for entry in details["inputConfigurations"]:
            assert "inputConfiguration" not in entry
            assert entry["pipelineId"]
            assert "inputConfigurationTruncated" in entry

    def test_a_small_config_run_is_reported_in_full_and_flagged_nothing(self):
        # The bound is a ceiling, not a policy: a run well inside it keeps every inline body intact.
        details = _assemble(3, 4)
        assert details["truncatedCollections"] == []
        for pipeline in details["pipelines"]:
            assert pipeline["renderedConfigTruncated"] is False
            assert len(pipeline["renderedConfig"]) == 4 * 1024
