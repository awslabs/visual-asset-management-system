# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A conditional workflow redeploy keeps the state machine ARN it recorded.

`redeploy_workflow` writes through `_write_workflow_updates(..., condition=Attr("deploymentRevision")
.eq(read_revision))`. boto3 renders that condition with `:v0` and merges it into the update's values;
while `to_update_expr` also named its first value `:v0`, the condition's value (the old revision, a
Number) replaced the first SET value, `workflow_arn`. The next redeploy then failed in
`describe_state_machine`, marked the workflow `deploymentStale`, and launches were refused.

These tests run against a moto table so boto3's real condition rendering is in the call, and bind
the shipped `to_update_expr` (tests/conftest.py replaces `common.dynamodb` with a MagicMock).
"""

import importlib.util
import json
import os
from unittest.mock import patch

import pytest
from boto3.dynamodb.conditions import Attr

from backend.backend.common.workflows import workflowAsl as wa
from backend.backend.common.workflows import workflowRecords as wr
from backend.backend.handlers.workflows import workflowService as ws

MOD = "backend.backend.handlers.workflows.workflowService"
REGION = "us-east-1"
TABLE = "t-workflows-redeploy"

_DYNAMODB_COMMON = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "common", "dynamodb.py"))
_spec = importlib.util.spec_from_file_location(
    "real_common_dynamodb_for_workflow_redeploy", _DYNAMODB_COMMON)
_real_ddb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_real_ddb)


def _pipeline(**execution):
    config = {"executionType": "Lambda", "waitForCallback": "Enabled", "taskTimeout": "3600",
              "lambda": {"resourceId": "vams-fn"}}
    config.update(execution)
    return {"databaseId": "db1", "pipelineId": "pipe1", "pipelineName": "P", "enabled": True,
            "archived": False, "systemConfig": {}, "executionConfig": config}


def _ref():
    return wr.build_specified_pipeline_ref("db1", "pipe1", "", "")


@pytest.fixture
def aws():
    """A moto workflow table (with the by-date GSI the refresh walk queries) and a deployed state
    machine, with the handler bound to both and to the shipped to_update_expr."""
    import boto3
    from moto import mock_aws
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name=REGION)
        resource.create_table(
            TableName=TABLE,
            KeySchema=[{"AttributeName": "databaseId", "KeyType": "HASH"},
                       {"AttributeName": "workflowId", "KeyType": "RANGE"}],
            AttributeDefinitions=[
                {"AttributeName": "databaseId", "AttributeType": "S"},
                {"AttributeName": "workflowId", "AttributeType": "S"},
                {"AttributeName": "allListPartition", "AttributeType": "S"},
                {"AttributeName": "dateModified", "AttributeType": "S"}],
            GlobalSecondaryIndexes=[{
                "IndexName": "WorkflowsByDateGSI",
                "KeySchema": [{"AttributeName": "allListPartition", "KeyType": "HASH"},
                              {"AttributeName": "dateModified", "KeyType": "RANGE"}],
                "Projection": {"ProjectionType": "ALL"}}],
            BillingMode="PAY_PER_REQUEST")
        table = resource.Table(TABLE)
        sfn = boto3.client("stepfunctions", region_name=REGION)
        arn = sfn.create_state_machine(
            name="vams-wf1", definition=json.dumps({"StartAt": "S", "States": {"S": {"Type": "Pass",
                                                                                   "End": True}}}),
            roleArn="arn:aws:iam::123456789012:role/vams-workflow")["stateMachineArn"]
        with patch(f"{MOD}._workflow_table", return_value=table), \
                patch(f"{MOD}.to_update_expr", _real_ddb.to_update_expr), \
                patch(f"{MOD}.log_actions"):
            yield table, sfn, arn


def _store_workflow(table, arn, deployed_from):
    table.put_item(Item={
        "databaseId": "db1", "workflowId": "wf1", "workflowName": "wf1", "enabled": True,
        "archived": False, "systemConfig": {}, "specifiedPipelines": [_ref()],
        "allListPartition": wr.ALL_WORKFLOWS_LIST_PARTITION, "dateModified": "2026-10-01T00:00:00Z",
        "workflow_arn": arn, "workflowVersionArn": f"{arn}:3", "jobNames": ["abcde-pipe1"],
        "pipelineFingerprints": wa.workflow_pipeline_fingerprints([deployed_from]),
        "deploymentRevision": 3,
    })


def _stored(table):
    return table.get_item(Key={"databaseId": "db1", "workflowId": "wf1"})["Item"]


def _deploy_against(sfn):
    """A deploy_state_machine stand-in whose first call on a recorded ARN is the real one:
    describe_state_machine, where botocore validates the ARN's type before the request is sent.
    Records the existing_arn of every call."""
    calls = []

    def _deploy(database_id, workflow_id, ref_records, existing_arn="", job_names=None):
        calls.append(existing_arn)
        sfn.describe_state_machine(stateMachineArn=existing_arn)
        return existing_arn, job_names or ["abcde-pipe1"], f"{existing_arn}:{3 + len(calls)}"

    return _deploy, calls


@pytest.mark.aws
class TestConditionalWriteKeepsTheArn:
    def test_the_first_set_value_survives_a_revision_condition(self, aws):
        table, _sfn, arn = aws
        _store_workflow(table, arn, _pipeline())
        new_arn = arn + "-new"

        written = ws._write_workflow_updates("db1", "wf1", {
            "workflow_arn": new_arn,
            "workflowVersionArn": f"{new_arn}:4",
            "deploymentRevision": 4,
        }, condition=Attr("deploymentRevision").eq(3))

        assert written is True
        stored = _stored(table)
        assert stored["workflow_arn"] == new_arn
        assert isinstance(stored["workflow_arn"], str)
        assert stored["workflowVersionArn"] == f"{new_arn}:4"
        assert stored["deploymentRevision"] == 4

    def test_a_stale_revision_still_writes_nothing(self, aws):
        """Control: the revision condition is evaluated, not dropped."""
        table, _sfn, arn = aws
        _store_workflow(table, arn, _pipeline())

        written = ws._write_workflow_updates("db1", "wf1", {
            "workflow_arn": arn + "-new", "deploymentRevision": 4,
        }, condition=Attr("deploymentRevision").eq(2))

        assert written is False
        stored = _stored(table)
        assert stored["workflow_arn"] == arn
        assert stored["deploymentRevision"] == 3


@pytest.mark.aws
class TestSuccessiveRedeploys:
    def test_a_second_pipeline_change_redeploys_on_the_recorded_arn(self, aws):
        table, sfn, arn = aws
        _store_workflow(table, arn, _pipeline())
        live = {"record": _pipeline(taskTimeout="7200")}
        deploy, calls = _deploy_against(sfn)

        with patch(f"{MOD}._get_pipeline_record", side_effect=lambda _db, _pid, **_kw: live["record"]), \
                patch(f"{MOD}.workflowAsl.deploy_state_machine", side_effect=deploy):
            first = ws.redeploy_workflow("db1", "wf1", reason="first change")
            live["record"] = _pipeline(taskTimeout="10800")
            second = ws.redeploy_workflow("db1", "wf1", reason="second change")

        assert first["outcome"] == "redeployed"
        assert second["outcome"] == "redeployed", second
        assert calls == [arn, arn]
        stored = _stored(table)
        assert stored["workflow_arn"] == arn
        assert isinstance(stored["workflow_arn"], str)
        assert stored["workflowVersionArn"] == f"{arn}:5"
        assert stored["deploymentRevision"] == 5
        assert stored["deploymentStale"] is False
        assert stored["pipelineFingerprints"] == wa.workflow_pipeline_fingerprints([live["record"]])

    def test_a_pipeline_refresh_then_a_launch_time_check_both_redeploy(self, aws):
        """The two internal entry points that write conditionally: the pipeline-change refresh (also
        reached by a pipeline unarchive and the deploy-time global-pipeline import) and the
        launch-time check."""
        table, sfn, arn = aws
        _store_workflow(table, arn, _pipeline())
        live = {"record": _pipeline(taskTimeout="7200")}
        deploy, calls = _deploy_against(sfn)

        with patch(f"{MOD}._get_pipeline_record", side_effect=lambda _db, _pid, **_kw: live["record"]), \
                patch(f"{MOD}.workflowAsl.deploy_state_machine", side_effect=deploy):
            refresh = ws.handle_internal_action(wr.build_internal_action_event(
                wr.INTERNAL_ACTION_REFRESH_PIPELINE_REFERENCES,
                {"pipelineDatabaseId": "db1", "pipelineId": "pipe1"}))
            live["record"] = _pipeline(taskTimeout="10800")
            launch = ws.handle_internal_action(wr.build_internal_action_event(
                wr.INTERNAL_ACTION_REDEPLOY_WORKFLOW,
                {"workflowDatabaseId": "db1", "workflowId": "wf1"}))

        assert json.loads(refresh["body"])["message"]["outcomes"] == {"redeployed": 1}
        assert json.loads(launch["body"])["message"]["outcome"] == "redeployed"
        assert calls == [arn, arn]
        stored = _stored(table)
        assert stored["workflow_arn"] == arn
        assert stored["deploymentRevision"] == 5
        assert stored["deploymentStale"] is False
