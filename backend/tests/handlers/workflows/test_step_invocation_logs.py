# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The per-step SECONDARY log: the log of the resource a step INVOKED.

The primary per-step log is whatever the pipeline's own sub-process registered. This secondary one is
the invocation the top-level state machine made — for a Lambda step (every use-case pipeline's
vamsExecute) that log holds the reason a launch failed, and nothing pointed at it before, so it was
unreachable from the execution view.

Derived from what the execute path already records (pipelineExecutionType + pipelineResourceArn), so it
needs no registration by the pipeline. Execution types with no reachable invocation log return "" —
an empty section labelled "no log" is worse than no section.
"""

import os

import pytest

# executionService resolves its table names at import time; seeded with the same values the sibling
# executionService tests use so the shared process-wide env stays consistent regardless of import order.
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "t-assets")
os.environ.setdefault("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "t-exec-v2")
os.environ.setdefault("WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME", "t-wf-inputs")
os.environ.setdefault("PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME", "t-pexec")
os.environ.setdefault("WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME", "t-wf-cfg")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_FILES_STORAGE_TABLE_NAME", "t-pin-files")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME", "t-pin-md")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME", "t-pin-cfg")
# Output/log table names are shared with processWorkflowExecutionOutput's tests; use the
# same values so the shared process-wide env stays consistent regardless of import order.
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME", "t-of")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE_NAME", "t-om")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE_NAME", "t-or")
os.environ.setdefault("PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME", "t-logs")
os.environ.setdefault("WORKFLOW_STORAGE_TABLE_NAME", "t-workflows")
os.environ.setdefault("PIPELINE_STORAGE_TABLE_NAME", "t-pipelines")

from backend.backend.handlers.workflows.executionService import step_invocation_log_group_arn

REFERENCE = ("arn:aws:logs:us-west-2:123456789012:log-group:"
             "/aws/vendedlogs/vamsPipelineWorkflowsabc:*")


@pytest.mark.unit
class TestStepInvocationLogGroupArn:
    def test_derives_a_lambda_log_group_from_a_function_arn(self):
        arn = step_invocation_log_group_arn({
            "pipelineExecutionType": "Lambda",
            "pipelineResourceArn": "arn:aws:lambda:us-west-2:123456789012:function:vams-vamsExecuteX",
        })
        assert arn == ("arn:aws:logs:us-west-2:123456789012:log-group:"
                       "/aws/lambda/vams-vamsExecuteX:*")

    def test_derives_from_a_bare_function_name_using_the_reference_arn(self):
        # pipelineResourceArn often holds just the function NAME (that is what the vamsSchema
        # resource_overrides inject), so the account/partition/region come from the execution's own
        # log-group ARN instead — always same-account and same-partition.
        arn = step_invocation_log_group_arn(
            {"pipelineExecutionType": "Lambda", "pipelineResourceArn": "vams-vamsExecuteY"},
            REFERENCE)
        assert arn == ("arn:aws:logs:us-west-2:123456789012:log-group:"
                       "/aws/lambda/vams-vamsExecuteY:*")

    def test_uses_the_partition_from_the_arn_rather_than_assuming_commercial(self):
        # Nothing is hard-coded to 'aws', so this holds in GovCloud and ISO partitions.
        arn = step_invocation_log_group_arn({
            "pipelineExecutionType": "Lambda",
            "pipelineResourceArn":
                "arn:aws-us-gov:lambda:us-gov-west-1:123456789012:function:vams-fn",
        })
        assert arn.startswith("arn:aws-us-gov:logs:us-gov-west-1:123456789012:log-group:")

    def test_a_qualified_function_arn_drops_the_version_alias(self):
        # A :$LATEST or :1 suffix is not part of the log-group name.
        arn = step_invocation_log_group_arn({
            "pipelineExecutionType": "Lambda",
            "pipelineResourceArn":
                "arn:aws:lambda:us-west-2:123456789012:function:vams-fn:$LATEST",
        })
        assert arn.endswith("/aws/lambda/vams-fn:*")

    @pytest.mark.parametrize("execution_type,resource", [
        # A queue has no invocation log; the CONSUMER's log is a separate resource VAMS does not own.
        ("SQS", "https://sqs.us-west-2.amazonaws.com/123456789012/q"),
        # A bus does not log deliveries by default.
        ("EventBridge", "arn:aws:events:us-west-2:123456789012:event-bus/b"),
        # Deadline Cloud session logs are reachable through the job, not a derivable CloudWatch group.
        ("DeadlineCloud", ""),
    ])
    def test_unsupported_execution_types_yield_no_log(self, execution_type, resource):
        assert step_invocation_log_group_arn(
            {"pipelineExecutionType": execution_type, "pipelineResourceArn": resource},
            REFERENCE) == ""

    def test_missing_resource_yields_no_log(self):
        assert step_invocation_log_group_arn(
            {"pipelineExecutionType": "Lambda", "pipelineResourceArn": ""}, REFERENCE) == ""

    def test_no_derivable_account_yields_no_log_rather_than_a_broken_arn(self):
        # Without an account id the ARN would be malformed; returning "" keeps the caller from issuing
        # a guaranteed-failing CloudWatch read.
        assert step_invocation_log_group_arn(
            {"pipelineExecutionType": "Lambda", "pipelineResourceArn": "bare-name"}, "") == ""

    def test_absent_execution_type_defaults_to_lambda(self):
        # Rows written before the field existed are Lambda steps.
        arn = step_invocation_log_group_arn(
            {"pipelineResourceArn": "vams-legacy-fn"}, REFERENCE)
        assert arn.endswith("/aws/lambda/vams-legacy-fn:*")

    @pytest.mark.parametrize("row", [None, {}])
    def test_tolerates_an_empty_row(self, row):
        assert step_invocation_log_group_arn(row, REFERENCE) == ""
