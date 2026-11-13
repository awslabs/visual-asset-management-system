# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The launch copies the workflow row's stored isSystem onto the execution main row (#1121).

The execution lists hide system runs by reading this flag off the main row, so a launch that dropped it
would show a system run (fail-visible) and one that set it from anything but the workflow row could
hide a user run. Only an exact boolean True on the workflow row marks the run."""

import os
import sys
import types

import pytest
from unittest.mock import MagicMock, patch

# executeWorkflow resolves these at import (mirrors test_execute_per_input_file_version_lock.py).
for _name, _value in [
    ("ASSET_STORAGE_TABLE_NAME", "t-assets"),
    ("WORKFLOW_STORAGE_TABLE_V2_NAME", "t-wf-v2"),
    ("PIPELINE_STORAGE_TABLE_V2_NAME", "t-pipe-v2"),
    ("PIPELINE_TEMPLATES_STORAGE_TABLE_NAME", "t-templates"),
    ("PIPELINE_TEMPLATE_TAG_SCHEMA_STORAGE_TABLE_NAME", "t-tagschema"),
    ("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "t-buckets"),
    ("S3_ASSETAUXILIARY_STORAGE_BUCKET", "t-aux"),
    ("METADATA_SERVICE_LAMBDA_FUNCTION_NAME", "t-md-svc"),
    ("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "t-exec-v2"),
    ("PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME", "t-pexec"),
    ("PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME", "t-pin-md"),
    ("PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME", "t-pin-cfg"),
    ("WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME", "t-wf-inputs"),
    ("WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME", "t-wf-cfg"),
    ("WORKFLOW_EXECUTION_LOCKS_STORAGE_TABLE_NAME", "t-wf-locks"),
]:
    os.environ.setdefault(_name, _value)

# handlers.workflows package __init__ imports get_task_builder at import time; the shared mock package
# does not provide it, so register a lightweight stub before importing the handler.
if "common.workflows.stepfunctions_builder" not in sys.modules:
    _stub = types.ModuleType("common.workflows.stepfunctions_builder")
    _stub.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _stub

from backend.backend.handlers.workflows import executeWorkflow as ew  # noqa: E402


class _Stop(Exception):
    """Raised by the first put_item: the main row is the first write, nothing after it matters here."""


def _main_row_for(workflow):
    written = []

    def _put_item(Item):
        written.append(Item)
        raise _Stop()

    table = MagicMock()
    table.put_item.side_effect = _put_item
    with patch.object(ew.dynamodb, "Table", return_value=table):
        with pytest.raises(_Stop):
            ew._persist_execution_records(
                execution_id="E1", workflow_arn="arn:sm", workflow_execution_arn="arn:ex",
                workflow=workflow, pipeline_records=[], resolved_configs=[], selected_inputs=[],
                asset_records={}, pipeline_execution_ids=[], first_job_name="", run_bucket="b",
                metadata_envelope={}, output_database_id="", output_asset_id="",
                output_extension="", trigger_type_stored="Manual", executing_user="user@x",
                input_locations={}, execution_group_id="")
    assert written and written[0]["workflowExecutionId"] == "E1"
    return written[0]


@pytest.mark.unit
class TestTheLaunchStampsTheWorkflowFlag:
    @pytest.mark.parametrize("stored, expected", [
        (True, True),
        (False, False),
        ("absent", False),   # a workflow row written before the flag existed
        ("true", False),     # never a truthy non-boolean
        (1, False),
    ])
    def test_the_main_row_carries_the_workflow_rows_flag(self, stored, expected):
        workflow = {"workflowId": "wf", "databaseId": "db"}
        if stored != "absent":
            workflow["isSystem"] = stored
        assert _main_row_for(workflow)["isSystem"] is expected
