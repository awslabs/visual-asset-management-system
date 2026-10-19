# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The workflow state machine version an execution started on, as the execution-details view reports it.

A launch starts on a published Step Functions state machine version and records its ARN on the main
execution row (workflowVersionArn); the workflow record holds the version its current deployment
publishes. The detail view reports the run's version number (workflowVersion) and whether it is still
the current one (workflowVersionCurrent), derived from those two rows alone. The ARNs stay internal,
like every other ARN the view omits."""

import json
import os

import pytest
from unittest.mock import MagicMock, patch

# executionService resolves these at import (mirrors test_executionService_wb53.py).
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

ACCOUNT = "123456789012"
MACHINE = f"arn:aws:states:us-east-1:{ACCOUNT}:stateMachine:vams-wf-abc"
V3 = f"{MACHINE}:3"
V4 = f"{MACHINE}:4"


def _assemble(main_extra=None, workflow_def=None):
    """Assemble the details view for a run with no steps, every read stubbed. Returns the details, the
    workflow-definition read, and the DynamoDB and Step Functions clients, so a test can show the
    version fields cost no read beyond the workflow definition the view already makes."""
    main_item = {"workflowId": "wf", "workflowDatabaseId": "db", **(main_extra or {})}
    workflow_read = MagicMock(return_value=workflow_def if workflow_def is not None else {})
    with patch(f"{MOD}.get_workflow_definition", workflow_read), \
         patch(f"{MOD}.get_pipeline_definition", return_value={}), \
         patch(f"{MOD}.get_pipeline_definitions", return_value={}), \
         patch(f"{MOD}._query_all", return_value=[]), \
         patch(f"{MOD}.get_pipeline_execution_rows", return_value=[]), \
         patch(f"{MOD}._query_capped", return_value=([], False)), \
         patch(f"{MOD}.get_produced_file_versions", return_value={}), \
         patch(f"{MOD}.dynamodb") as dynamodb, \
         patch(f"{MOD}.sfn") as sfn:
        details = le.assemble_execution_details("E1", main_item, config_row={})
    return details, workflow_read, dynamodb, sfn


@pytest.mark.unit
class TestWorkflowVersionFields:
    def test_a_run_on_the_current_version_reports_its_number_as_current(self):
        details, _read, _ddb, _sfn = _assemble({"workflowVersionArn": V3},
                                               {"workflowVersionArn": V3})
        assert details["workflowVersion"] == 3
        assert details["workflowVersionCurrent"] is True

    def test_a_run_on_an_earlier_version_reports_it_superseded(self):
        # The workflow was redeployed (version 4) after the run started on version 3.
        details, _read, _ddb, _sfn = _assemble({"workflowVersionArn": V3},
                                               {"workflowVersionArn": V4})
        assert details["workflowVersion"] == 3
        assert details["workflowVersionCurrent"] is False

    def test_a_run_started_before_versions_were_recorded_reports_neither(self):
        # Its row carries no workflowVersionArn: it started on the unqualified state machine ARN.
        details, _read, _ddb, _sfn = _assemble({}, {"workflowVersionArn": V4})
        assert details["workflowVersion"] is None
        assert details["workflowVersionCurrent"] is None

    def test_a_workflow_with_no_recorded_version_leaves_current_unknown(self):
        details, _read, _ddb, _sfn = _assemble({"workflowVersionArn": V3}, {})
        assert details["workflowVersion"] == 3
        assert details["workflowVersionCurrent"] is None

    @pytest.mark.parametrize("stored", [
        MACHINE,                                    # unqualified state machine ARN
        f"{MACHINE}:prod",                          # alias, not a version
        f"{MACHINE}:3x",
        f"{MACHINE}:",
        f"{MACHINE}:\u00b3",                        # a non-ASCII digit
        f"arn:aws:states:us-east-1:{ACCOUNT}:execution:vams-wf-abc:3",
        "not-an-arn",
        "",
        3,
        {"S": V3},
    ])
    def test_a_stored_value_that_is_not_a_version_arn_reports_no_number(self, stored):
        details, _read, _ddb, _sfn = _assemble({"workflowVersionArn": stored},
                                               {"workflowVersionArn": V4})
        assert details["workflowVersion"] is None

    def test_a_malformed_stored_value_is_still_compared_without_failing(self):
        details, _read, _ddb, _sfn = _assemble({"workflowVersionArn": f"{MACHINE}:prod"},
                                               {"workflowVersionArn": V4})
        assert details["workflowVersion"] is None
        assert details["workflowVersionCurrent"] is False

    def test_the_fields_cost_no_read_beyond_the_workflow_definition(self):
        _details, workflow_read, dynamodb, sfn = _assemble({"workflowVersionArn": V3},
                                                           {"workflowVersionArn": V4})
        workflow_read.assert_called_once_with("db", "wf")
        assert dynamodb.mock_calls == []
        assert sfn.mock_calls == []

    def test_the_version_arns_are_not_returned(self):
        details, _read, _ddb, _sfn = _assemble({"workflowVersionArn": V3},
                                               {"workflowVersionArn": V4})
        rendered = json.dumps(details)
        assert "workflowVersionArn" not in details
        assert "arn:" not in rendered and ACCOUNT not in rendered
