# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The listing reconcile losing the completion race, against a real DynamoDB condition evaluator.

S2-BACKEND-105 — the read-path reconcile reads the row, polls Step Functions, and writes back. Two
things had to be true for it to stop reverting a finished execution: the write names only what the poll
produced, and a write that carries a completion-owned attribute is conditioned on the row not already
being terminal. test_reconcile_and_abort_races.py covers the narrowed payload end to end (its poll
reports RUNNING, so nothing owned by a completing writer is written at all) and the condition string
per call site. The remaining gap is the CONDITION firing for real: a poll that observes a terminal
status while the end-state lambda has already written a different one, where the write must be refused
and the refusal must not surface to the caller.

executionService resolves its table names at import (mirrors test_executionService_wb53.py)."""

import json
import os
from datetime import datetime, timedelta, timezone

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

from backend.backend.handlers.workflows import executionService as le  # noqa: E402

MOD = "backend.backend.handlers.workflows.executionService"

DB, ASSET = "db", "A"
EXEC_ID = "e1000000000000000000000000000001"
COMPOSITE = "wf-db:wf"
KEY = {"workflowExecutionId": EXEC_ID, "workflowDatabaseId:workflowId": COMPOSITE}


def _iso(minutes_ago):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _running_main_row():
    """A main row as launch writes it: every reconcilable attribute present, log and error empty, and
    a sync stamp old enough that the listing re-polls Step Functions."""
    return {"workflowExecutionId": EXEC_ID, "workflowDatabaseId:workflowId": COMPOSITE,
            "workflowDatabaseId": "wf-db", "workflowId": "wf",
            "workflow_execution_arn": "arn:ex",
            "executionStatus": "RUNNING", "executionStartDate": _iso(30),
            "executionStopDate": "", "executionError": "", "executionLog": "",
            "lastSfnSyncCheckDate": "2000-01-01T00:00:00Z"}


def _input_row():
    return {"workflowExecutionId": EXEC_ID, "databaseId:assetId": f"{DB}:{ASSET}",
            "databaseId:assetId:inputAssetFileKey": f"{DB}:{ASSET}:/f.glb",
            "databaseId": DB, "assetId": ASSET, "inputAssetFileKey": "/f.glb",
            "workflowId": "wf", "workflowDatabaseId": "wf-db",
            "executionStartDate": _iso(30)}


def _create_tables(ddb):
    """The three tables the asset listing reads, with the two indexes it queries."""
    ddb.create_table(
        TableName=le.workflow_execution_database_v2,
        KeySchema=[{"AttributeName": "workflowExecutionId", "KeyType": "HASH"},
                   {"AttributeName": "workflowDatabaseId:workflowId", "KeyType": "RANGE"}],
        AttributeDefinitions=[
            {"AttributeName": "workflowExecutionId", "AttributeType": "S"},
            {"AttributeName": "workflowDatabaseId:workflowId", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST")
    ddb.create_table(
        TableName=le.workflow_execution_inputs_table,
        KeySchema=[{"AttributeName": "workflowExecutionId", "KeyType": "HASH"},
                   {"AttributeName": "databaseId:assetId:inputAssetFileKey", "KeyType": "RANGE"}],
        AttributeDefinitions=[
            {"AttributeName": "workflowExecutionId", "AttributeType": "S"},
            {"AttributeName": "databaseId:assetId:inputAssetFileKey", "AttributeType": "S"},
            {"AttributeName": "databaseId:assetId", "AttributeType": "S"},
            {"AttributeName": "executionStartDate", "AttributeType": "S"}],
        GlobalSecondaryIndexes=[{
            "IndexName": "WorkflowExecInputsByAssetGSI",
            "KeySchema": [{"AttributeName": "databaseId:assetId", "KeyType": "HASH"},
                          {"AttributeName": "executionStartDate", "KeyType": "RANGE"}],
            "Projection": {"ProjectionType": "ALL"}}],
        BillingMode="PAY_PER_REQUEST")
    ddb.create_table(
        TableName=le.workflow_execution_configuration_table,
        KeySchema=[{"AttributeName": "workflowExecutionId", "KeyType": "HASH"},
                   {"AttributeName": "recordType", "KeyType": "RANGE"}],
        AttributeDefinitions=[
            {"AttributeName": "workflowExecutionId", "AttributeType": "S"},
            {"AttributeName": "recordType", "AttributeType": "S"},
            {"AttributeName": "outputDatabaseId:outputAssetId", "AttributeType": "S"},
            {"AttributeName": "executionStartDate", "AttributeType": "S"}],
        GlobalSecondaryIndexes=[{
            "IndexName": "WorkflowExecConfigByOutputAssetGSI",
            "KeySchema": [{"AttributeName": "outputDatabaseId:outputAssetId", "KeyType": "HASH"},
                          {"AttributeName": "executionStartDate", "KeyType": "RANGE"}],
            "Projection": {"ProjectionType": "ALL"}}],
        BillingMode="PAY_PER_REQUEST")


def _list_with_describe(ddb, describe):
    le.claims_and_roles = {"tokens": ["u1"]}
    enforcer = MagicMock()
    enforcer.enforce.return_value = True
    with patch.object(le, "dynamodb", ddb), \
            patch(f"{MOD}.CasbinEnforcer", return_value=enforcer), \
            patch(f"{MOD}.get_asset_details",
                  side_effect=lambda d, a: {"databaseId": d, "assetId": a}), \
            patch(f"{MOD}._execution_access_check", return_value=(True, "")), \
            patch(f"{MOD}._fetch_execution_logs", return_value="polled log"), \
            patch(f"{MOD}.sfn") as sfn:
        sfn.describe_execution.side_effect = describe
        return le.get_executions({}, DB, ASSET, "", "", {})


@pytest.mark.unit
class TestTheGuardRefusesToRegressATerminalRow:
    """A poll that observes a terminal status is a completion-owned write, so it must lose to the
    writer that finished the execution first — and losing must read as an ordinary listing."""

    @pytest.mark.aws
    def test_a_terminal_reconcile_does_not_overwrite_the_completing_writers_row(self):
        import boto3
        from moto import mock_aws
        with mock_aws():
            ddb = boto3.resource("dynamodb", region_name="us-east-1")
            _create_tables(ddb)
            main_table = ddb.Table(le.workflow_execution_database_v2)
            main_table.put_item(Item=_running_main_row())
            ddb.Table(le.workflow_execution_inputs_table).put_item(Item=_input_row())

            def _describe_and_finish(**kwargs):
                # The end-state lambda runs as a state INSIDE the machine, so it finishes the row while
                # this request is mid-flight. It captured the log synchronously, at completion.
                main_table.update_item(
                    Key=KEY,
                    UpdateExpression=("SET executionStatus = :s, executionStopDate = :d, "
                                      "executionLog = :l"),
                    ExpressionAttributeValues={":s": "SUCCEEDED", ":d": _iso(1),
                                               ":l": "captured at completion"})
                # This poll then observes a DIFFERENT terminal status, so the reconcile builds a
                # payload of attributes the completing writer owns and the guard has to refuse it.
                return {"status": "ABORTED", "stopDate": datetime(2026, 6, 16, 0, 5, 0)}

            response = _list_with_describe(ddb, _describe_and_finish)
            assert response["statusCode"] == 200, response
            item = main_table.get_item(Key=KEY)["Item"]
            assert item["executionStatus"] == "SUCCEEDED", "the finished run was regressed to ABORTED"
            assert item["executionLog"] == "captured at completion", "the captured log was replaced"
            assert item["executionStopDate"] != "2026-06-16T00:05:00Z", (
                "the poll's stop date replaced the completing writer's")

    @pytest.mark.aws
    def test_losing_the_race_is_not_reported_as_a_failed_listing(self):
        # The refusal is the expected outcome for the second writer, so it is logged and swallowed. A
        # ConditionalCheckFailed reaching the handler's except would turn an ordinary board refresh
        # into a 500.
        import boto3
        from moto import mock_aws
        with mock_aws():
            ddb = boto3.resource("dynamodb", region_name="us-east-1")
            _create_tables(ddb)
            main_table = ddb.Table(le.workflow_execution_database_v2)
            main_table.put_item(Item=_running_main_row())
            ddb.Table(le.workflow_execution_inputs_table).put_item(Item=_input_row())

            def _describe_and_finish(**kwargs):
                main_table.update_item(
                    Key=KEY, UpdateExpression="SET executionStatus = :s, executionStopDate = :d",
                    ExpressionAttributeValues={":s": "FAILED", ":d": _iso(1)})
                return {"status": "SUCCEEDED", "stopDate": datetime(2026, 6, 16, 0, 5, 0)}

            response = _list_with_describe(ddb, _describe_and_finish)
        assert response["statusCode"] == 200, response
        body = json.loads(response["body"])["message"]
        assert body["Items"], "the row must still be listed"

    @pytest.mark.aws
    def test_an_uncontested_terminal_poll_is_still_recorded(self):
        # The control: the guard must only block a row another writer already finished. A poll that is
        # the FIRST to observe the completion — an execution cancelled directly in Step Functions,
        # outside VAMS — has to be written, log and all.
        import boto3
        from moto import mock_aws
        with mock_aws():
            ddb = boto3.resource("dynamodb", region_name="us-east-1")
            _create_tables(ddb)
            main_table = ddb.Table(le.workflow_execution_database_v2)
            main_table.put_item(Item=_running_main_row())
            ddb.Table(le.workflow_execution_inputs_table).put_item(Item=_input_row())

            response = _list_with_describe(ddb, lambda **kwargs: {
                "status": "ABORTED", "stopDate": datetime(2026, 6, 16, 0, 5, 0),
                "error": "Abort", "cause": "stopped in the console"})
            assert response["statusCode"] == 200, response
            item = main_table.get_item(Key=KEY)["Item"]
            assert item["executionStatus"] == "ABORTED"
            assert item["executionStopDate"] == "2026-06-16T00:05:00Z"
            assert item["executionLog"] == "polled log"
            assert item["executionError"]
