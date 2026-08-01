# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the workflow V2 -> ASL adapter (common/workflows/workflowAsl). Pure; maps V2
pipeline records' executionConfig into the V1-shaped pipeline dict the shared ASL generator reads."""

import json
from unittest.mock import MagicMock

import pytest

from backend.backend.common.workflows import workflowAsl as wa


@pytest.mark.unit
class TestWorkflowAslAdapter:
    def test_lambda_mapping(self):
        rec = {"pipelineId": "pipe1", "databaseId": "db1",
               "executionConfig": {"executionType": "Lambda", "lambda": {"resourceId": "fn-x"},
                                   "waitForCallback": "Disabled"}}
        d = wa.to_asl_pipeline_dict(rec, "job-pipe1")
        assert d["name"] == "job-pipe1"
        assert d["pipelineExecutionType"] == "Lambda"
        assert d["waitForCallback"] == "Disabled"
        ur = json.loads(d["userProvidedResource"])
        assert ur["resourceType"] == "Lambda" and ur["resourceId"] == "fn-x"

    def test_sqs_mapping(self):
        rec = {"pipelineId": "pipe1", "databaseId": "db1",
               "executionConfig": {"executionType": "SQS", "sqs": {"queueUrl": "https://q"}}}
        d = wa.to_asl_pipeline_dict(rec)
        assert d["name"] == "pipe1"  # falls back to pipelineId when no job name
        ur = json.loads(d["userProvidedResource"])
        assert ur["resourceType"] == "SQS" and ur["resourceId"] == "https://q"

    def test_eventbridge_mapping(self):
        rec = {"pipelineId": "pipe1", "databaseId": "db1",
               "executionConfig": {"executionType": "EventBridge",
                                   "eventBridge": {"busArn": "arn:bus", "source": "vams.x",
                                                   "detailType": "run"}}}
        ur = json.loads(wa.to_asl_pipeline_dict(rec)["userProvidedResource"])
        assert ur["resourceType"] == "EventBridge" and ur["resourceId"] == "arn:bus"
        assert ur["eventSource"] == "vams.x" and ur["eventDetailType"] == "run"

    def test_deadline_mapping(self):
        rec = {"pipelineId": "pipe1", "databaseId": "db1",
               "executionConfig": {"executionType": "DeadlineCloud", "waitForCallback": "Enabled",
                                   "deadlineCloud": {"farmId": "farm-1", "queueId": "queue-1",
                                                     "templateType": "YAML"}}}
        d = wa.to_asl_pipeline_dict(rec)
        assert d["waitForCallback"] == "Enabled"
        ur = json.loads(d["userProvidedResource"])
        assert ur["resourceType"] == "DeadlineCloud"
        assert ur["deadlineFarmId"] == "farm-1" and ur["deadlineQueueId"] == "queue-1"

    def test_deadline_mapping_serializes_decimal_numeric_fields(self):
        # priority / maxRetriesPerTask / maxFailedTasksCount come back from DynamoDB as Decimal;
        # to_asl_pipeline_dict must produce a JSON-serializable userProvidedResource (json.dumps
        # cannot encode Decimal). Coerced to int.
        from decimal import Decimal
        rec = {"pipelineId": "pipe1", "databaseId": "db1",
               "executionConfig": {"executionType": "DeadlineCloud", "waitForCallback": "Enabled",
                                   "deadlineCloud": {"farmId": "farm-1", "queueId": "queue-1",
                                                     "template": "specificationVersion: x",
                                                     "templateType": "YAML",
                                                     "priority": Decimal("50"),
                                                     "maxRetriesPerTask": Decimal("3"),
                                                     "maxFailedTasksCount": Decimal("10")}}}
        d = wa.to_asl_pipeline_dict(rec)  # must not raise
        ur = json.loads(d["userProvidedResource"])
        assert ur["deadlinePriority"] == 50 and isinstance(ur["deadlinePriority"], int)
        assert ur["deadlineMaxRetriesPerTask"] == 3
        assert ur["deadlineMaxFailedTasksCount"] == 10

    def test_to_asl_pipeline_dicts_preserves_order_and_jobnames(self):
        class Ref:
            def __init__(self, jn):
                self.jobName = jn
        ref_records = [
            (Ref("job-a"), {"pipelineId": "a", "databaseId": "db1", "executionConfig": {"executionType": "Lambda"}}),
            (Ref("job-b"), {"pipelineId": "b", "databaseId": "db1", "executionConfig": {"executionType": "SQS"}}),
        ]
        dicts = wa.to_asl_pipeline_dicts(ref_records)
        assert [d["name"] for d in dicts] == ["job-a", "job-b"]
        assert dicts[1]["pipelineExecutionType"] == "SQS"

    def test_state_machine_name_prefixed_and_bounded(self):
        assert wa._generate_state_machine_name("wflow1").startswith("vams-wflow1")
        long_name = wa._generate_state_machine_name("w" * 200)
        assert long_name.startswith("vams-") and len(long_name) == 80

    def test_state_machine_exists_propagates_non_missing_error(self):
        class DoesNotExist(Exception):
            pass
        sf_client = MagicMock()
        sf_client.exceptions.StateMachineDoesNotExist = DoesNotExist
        sf_client.describe_state_machine.side_effect = RuntimeError("ThrottlingException")
        with pytest.raises(RuntimeError):
            wa._state_machine_exists(sf_client, "arn:existing")

    def test_state_machine_exists_false_when_missing(self):
        class DoesNotExist(Exception):
            pass
        sf_client = MagicMock()
        sf_client.exceptions.StateMachineDoesNotExist = DoesNotExist
        sf_client.describe_state_machine.side_effect = DoesNotExist()
        assert wa._state_machine_exists(sf_client, "arn:gone") is False

    def test_deploy_state_machine_empty_refrecords_short_circuits(self):
        # No pipelines to deploy: keep any existing arn and return no job names (no env/boto3 read).
        assert wa.deploy_state_machine("db1", "wflow1", [], existing_arn="") == ("", [])
        assert wa.deploy_state_machine(
            "db1", "wflow1", [], existing_arn="arn:existing") == ("arn:existing", [])
