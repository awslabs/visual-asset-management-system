# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Re-run reconstruction of the metadata-source asset selection from the workflow-execution
configuration row.

The row carries `metadataSourceAssets` next to a `metadataSourceAssetsTruncated` flag written by the
same record builder. A flagged list is a SHORTER list than the one the original run used, so a re-run
must refuse rather than replay it as if complete; an unflagged list (flag False, or absent on a row
written before the flag existed) must reach the rebuilt body intact and in order.
"""

import os
import pytest
from unittest.mock import MagicMock, patch

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

EXECUTION_ID = "e1000000000000000000000000000001"
MAIN_ITEM = {"workflowId": "wf", "workflowDatabaseId": "db"}
INPUT_ROW = {"databaseId": "db", "assetId": "a1", "inputAssetFileKey": "/x.glb", "assetRootS3Key": ""}
PIPELINE_CFG_ROW = {"templateId": "t1"}
SOURCES = [{"databaseId": "db", "assetId": "m1"}, {"databaseId": "db2", "assetId": "m2"}]


def _config_row(**fields):
    row = {"outputAssetId": "a1", "outputDatabaseId": "db"}
    row.update(fields)
    return row


def _reconstruct(config_row):
    """Run the reconstruction against one input row and one pipeline row, returning the body together
    with the two table stubs so a caller can prove the function really reached them. Both stubs are
    patched on the module object whose namespace the function executes in."""
    query_all = MagicMock(side_effect=[[INPUT_ROW], [PIPELINE_CFG_ROW]])
    pipeline_rows = MagicMock(return_value=[{"pipelineExecutionId": "pe1", "pipelineId": "p1"}])
    with patch(f"{MOD}._query_all", query_all), \
         patch(f"{MOD}.get_pipeline_execution_rows", pipeline_rows):
        body = le._reconstruct_execute_request(EXECUTION_ID, MAIN_ITEM, config_row)
    return body, query_all, pipeline_rows


def _refusal(config_row):
    """Run the reconstruction expecting the metadata-source refusal; returns the raised error. Its
    message text is what attributes the failure to THIS guard rather than to a sibling refusal or a
    stub."""
    query_all = MagicMock(side_effect=[[INPUT_ROW], [PIPELINE_CFG_ROW]])
    pipeline_rows = MagicMock(return_value=[{"pipelineExecutionId": "pe1", "pipelineId": "p1"}])
    with patch(f"{MOD}._query_all", query_all), \
         patch(f"{MOD}.get_pipeline_execution_rows", pipeline_rows), \
         pytest.raises(le.VAMSGeneralErrorResponse) as excinfo:
        le._reconstruct_execute_request(EXECUTION_ID, MAIN_ITEM, config_row)
    # The guard sits on the configuration row, after the input and pipeline rows are read; reaching
    # both stubs proves the refusal came from the reconstruction itself and not a stub misfire.
    assert query_all.call_count >= 2
    assert pipeline_rows.call_count >= 1
    return excinfo.value


@pytest.mark.unit
class TestRerunMetadataSourceAssetsTruncated:
    def test_truncated_list_refuses_rerun(self):
        error = _refusal(_config_row(metadataSourceAssets=SOURCES, metadataSourceAssetsTruncated=True))
        assert "metadata source asset" in str(error)
        # The handler maps this error class to a client error, so the caller sees a refusal rather than
        # a server fault.
        assert error.status_code == 400

    def test_truncated_list_trimmed_to_empty_still_refuses_rerun(self):
        # The worst case: trimmed to nothing. A guard placed after the list's truthiness test would
        # replay this as "no metadata sources" and launch a divergent run silently.
        assert "metadata source asset" in str(
            _refusal(_config_row(metadataSourceAssets=[], metadataSourceAssetsTruncated=True)))

    def test_truncated_list_refuses_even_when_pipeline_names_a_template(self):
        # Unlike the override guard, a templateId on the pipeline row is not an escape hatch: the
        # selection is the caller's own and cannot be re-resolved from a template.
        message = str(_refusal(_config_row(metadataSourceAssets=SOURCES, metadataSourceAssetsTruncated=True)))
        assert "metadata source asset" in message
        assert "template tag" not in message
        assert "custom configuration" not in message

    def test_untruncated_list_replays_intact(self):
        # Positive control, flag present and False: every source reaches the body, in stored order.
        body, query_all, pipeline_rows = _reconstruct(
            _config_row(metadataSourceAssets=SOURCES, metadataSourceAssetsTruncated=False))
        assert body["metadataSourceAssets"] == SOURCES
        # Both readers were reached: the body was reconstructed, not short-circuited.
        assert query_all.call_count >= 2
        assert pipeline_rows.call_count >= 1

    def test_absent_truncation_flag_replays_intact(self):
        # Positive control for rows written before the flag existed: the key is absent, not False, so
        # the guard must not brick an ordinary re-run.
        body, query_all, pipeline_rows = _reconstruct(_config_row(metadataSourceAssets=SOURCES))
        assert body["metadataSourceAssets"] == SOURCES
        # Both readers were reached: the body was reconstructed, not short-circuited.
        assert query_all.call_count >= 2
        assert pipeline_rows.call_count >= 1

    def test_untruncated_empty_list_replays_as_no_sources(self):
        # Negative control for the placement arm above: an EMPTY list with the flag False is a run
        # that really had no metadata sources, and replays as such rather than refusing.
        body, _query_all, _pipeline_rows = _reconstruct(
            _config_row(metadataSourceAssets=[], metadataSourceAssetsTruncated=False))
        assert body["metadataSourceAssets"] == []
