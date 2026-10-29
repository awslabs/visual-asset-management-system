# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A stored execution log that holds the launching request's client details is returned with them
masked, from the asset's executions list and from the logs endpoint (truncated mode).

The stored executionLog is the run's Step Functions log text. An execution launched with the whole
API Gateway request context carries it in every state's input, so the stored text holds the identity
block (source IP, user agent) and the authorizer's API key id. Both read paths return it through
common.logRedaction.redact_log_text, and keep every field they return.

The asset listing runs against moto, as in test_asset_execution_list_full_page_walk.py, whose table
definitions it reuses."""

import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

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
from backend.backend.common.logRedaction import REDACTED  # noqa: E402

MOD = "backend.backend.handlers.workflows.executionService"
DB, ASSET = "db", "A"
EXEC_ID = "e" + "0" * 30 + "1"
SOURCE_IP = "203.0.113.7"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) curl/8.4.0"
API_KEY_ID = "11111111-2222-3333-4444-555555555555"
USER_ARN = "arn:aws:iam::000000000000:user/launcher"


def _launch_request_context():
    """A REST (v1) request context as the launch received it, normalize_event's http block included."""
    return {
        "accountId": "000000000000", "requestId": "req-1", "stage": "api",
        "identity": {"sourceIp": SOURCE_IP, "userAgent": USER_AGENT, "userArn": USER_ARN,
                     "cognitoIdentityId": None, "user": None, "caller": None},
        "authorizer": {"principalId": "alice", "vams:tokens": '["alice"]',
                       "vams:apiKeyId": API_KEY_ID},
        "http": {"path": "/workflows/wf-db/wf/execute", "method": "POST", "sourceIp": SOURCE_IP},
    }


def _stored_execution_log():
    """Two Step Functions log lines: the execution input, and a Lambda task's parameters, each carrying
    the request context as escaped JSON."""
    execution_input = {"workflowExecutionId": EXEC_ID, "executingUserName": "alice",
                       "executingRequestContext": _launch_request_context()}
    task_parameters = {"FunctionName": "processOutput",
                       "Payload": {"body": {"executingUserName": "alice",
                                            "executingRequestContext": _launch_request_context()}}}
    return "\n".join([
        json.dumps({"id": "1", "type": "ExecutionStarted",
                    "details": {"input": json.dumps(execution_input)}}),
        json.dumps({"id": "9", "type": "TaskScheduled",
                    "details": {"parameters": json.dumps(task_parameters)}}),
    ])


def _assert_masked(log_text):
    """Every client detail is masked, and the log keeps its structure and every other value."""
    for value in (SOURCE_IP, USER_AGENT, API_KEY_ID, USER_ARN):
        assert value not in log_text
    started, scheduled = [json.loads(line) for line in log_text.split("\n")]
    contexts = [
        json.loads(started["details"]["input"])["executingRequestContext"],
        json.loads(scheduled["details"]["parameters"])["Payload"]["body"]["executingRequestContext"],
    ]
    for context in contexts:
        assert context["identity"] == {"sourceIp": REDACTED, "userAgent": REDACTED,
                                       "userArn": REDACTED, "cognitoIdentityId": None,
                                       "user": None, "caller": None}
        assert context["http"]["sourceIp"] == REDACTED
        assert context["http"]["path"] == "/workflows/wf-db/wf/execute"
        assert context["authorizer"] == {"principalId": "alice", "vams:tokens": '["alice"]',
                                         "vams:apiKeyId": REDACTED}
        assert context["requestId"] == "req-1"
    assert json.loads(started["details"]["input"])["executingUserName"] == "alice"


@pytest.mark.unit
class TestTheAssetExecutionsListMasksTheStoredLog:
    def _list(self):
        import boto3
        from moto import mock_aws
        from backend.tests.handlers.workflows import test_asset_execution_list_full_page_walk as walk
        date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        with mock_aws():
            ddb = boto3.resource("dynamodb", region_name="us-east-1")
            walk._create_tables(ddb)
            ddb.Table(le.workflow_execution_inputs_table).put_item(Item={
                "workflowExecutionId": EXEC_ID, "databaseId:assetId": f"{DB}:{ASSET}",
                "databaseId:assetId:inputAssetFileKey": f"{DB}:{ASSET}:/f.glb",
                "databaseId": DB, "assetId": ASSET, "inputAssetFileKey": "/f.glb",
                "workflowId": "wf", "workflowDatabaseId": "wf-db", "executionStartDate": date})
            ddb.Table(le.workflow_execution_database_v2).put_item(Item={
                "workflowExecutionId": EXEC_ID, "workflowDatabaseId:workflowId": "wf-db:wf",
                "workflowDatabaseId": "wf-db", "workflowId": "wf",
                "workflow_execution_arn": "arn:ex", "executionStatus": "SUCCEEDED",
                "executionStartDate": date, "executionStopDate": date,
                "lastSfnSyncCheckDate": date, "triggerType": "Manual",
                "triggeredByUserId": "alice", "executionGroupId": "",
                "executionError": "", "executionLog": _stored_execution_log()})
            le.claims_and_roles = {"tokens": ["u1"]}
            enforcer = MagicMock()
            enforcer.enforce.return_value = True
            with patch.object(le, "dynamodb", ddb), \
                    patch(f"{MOD}.CasbinEnforcer", return_value=enforcer), \
                    patch(f"{MOD}.get_asset_details",
                          side_effect=lambda d, a: {"databaseId": d, "assetId": a}), \
                    patch(f"{MOD}._execution_access_check", return_value=(True, "")), \
                    patch(f"{MOD}.sfn"):
                response = le.get_executions({}, DB, ASSET, "", "", {})
        assert response["statusCode"] == 200, response
        items = json.loads(response["body"])["message"]["Items"]
        assert [item["workflowExecutionId"] for item in items] == [EXEC_ID]
        return items[0]

    def test_the_listed_row_returns_the_log_with_the_client_details_masked(self):
        _assert_masked(self._list()["executionLog"])

    def test_the_listed_row_keeps_its_fields(self):
        row = self._list()
        assert {"workflowExecutionId", "workflowId", "workflowDatabaseId", "executionStatus",
                "triggerType", "triggeredByUserId", "startDate", "stopDate",
                "executionStartDate", "executionStopDate", "inputAssetFileKey", "databaseId",
                "assetId", "executionError", "executionLog", "outputTypes"} <= set(row)
        assert row["triggeredByUserId"] == "alice"


@pytest.mark.unit
class TestTheLogsEndpointMasksTheStoredLog:
    def _logs(self):
        le.claims_and_roles = {"tokens": ["u1"]}
        main = {"workflowExecutionId": EXEC_ID, "workflowId": "wf", "workflowDatabaseId": "wf-db",
                "executionLogGroupArn": "arn:aws:logs:us-east-1:000000000000:log-group:/g:*",
                "executionLog": _stored_execution_log(), "executionError": ""}
        with patch(f"{MOD}.get_execution_main_row", return_value=main), \
                patch(f"{MOD}.authorize_execution_access", return_value=(True, "")):
            response = le.get_execution_logs({}, EXEC_ID, {})
        assert response["statusCode"] == 200, response
        return json.loads(response["body"])["message"]

    def test_truncated_mode_returns_the_stored_log_with_the_client_details_masked(self):
        message = self._logs()
        assert message["logsSource"] == "stored"
        _assert_masked(message["executionLog"])

    def test_truncated_mode_keeps_its_fields(self):
        assert {"mode", "executionLog", "executionError", "logsSource"} <= set(self._logs())
