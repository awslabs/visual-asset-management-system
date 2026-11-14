# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""systemWorkflowLauncher: one execute-workflow cross-call per launch message.

The launcher invokes executeWorkflowV2 once per message (botocore retries off: the call is not
idempotent; the launcher itself repeats only a connection failure, see TestConnectionRetry), as
SYSTEM_USER, with `triggerType: "systemReindex"` and `executionGroupId: vec-{runId}-{chunk}`, using the
system workflow's fileUpload trigger defaults so a reindex run selects the template an upload run would.
A 400 (the per-file-version lock or a validation refusal) is dropped; a throttle, a 5xx or an invocation
fault is reported so SQS redelivers and, after three attempts, dead-letters the message.
"""

import io
import json
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import (
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
    ResponseStreamingError,
)

from tests.handlers.osVectorSearch.vectorsearch_support import load_handler

MESSAGE = {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/part.glb", "versionId": "",
           "reindexRunId": "abc123def456", "chunk": 3}
TRIGGER_ROW = {
    "workflowDatabaseId:workflowId": "GLOBAL:system-genai-metadata", "triggerType": "fileUpload",
    "triggerBaseType": "fileUpload", "enabled": True,
    "triggerConfig": {"inputFileFilters": {"allow": ["*.glb"], "exclude": []},
                      "defaultTemplateIds": {"GLOBAL:system-genai-metadata": "system-genai-metadata-default"}},
}


def _invoke_response(status_code, function_error=None):
    response = {"StatusCode": 200,
                "Payload": io.BytesIO(json.dumps({"statusCode": status_code, "body": "{}"}).encode("utf-8"))}
    if function_error:
        response["FunctionError"] = function_error
    return response


def _sqs(message_id, body):
    return {"messageId": message_id, "eventSource": "aws:sqs",
            "body": body if isinstance(body, str) else json.dumps(body)}


def _identifiers(response):
    assert "batchItemFailures" in response
    return [e["itemIdentifier"] for e in response["batchItemFailures"]]


@pytest.fixture
def launcher():
    m = load_handler("systemWorkflowLauncher")
    m.lambda_client = MagicMock()
    m.lambda_client.invoke.return_value = _invoke_response(200)
    m.workflow_triggers_table = MagicMock()
    m.workflow_triggers_table.query.return_value = {"Items": [TRIGGER_ROW]}
    m._trigger_row_cache.clear()
    return m


@pytest.mark.unit
class TestEnvironment:
    def test_the_workflow_database_id_has_no_default_and_fails_the_load(self, monkeypatch):
        # The workflow identity is deployment configuration; a silent literal would launch against a
        # workflow the deployment never registered.
        monkeypatch.delenv("GENAI_METADATA_WORKFLOW_DATABASE_ID")
        with pytest.raises(KeyError, match="GENAI_METADATA_WORKFLOW_DATABASE_ID"):
            load_handler("systemWorkflowLauncher")

    def test_the_workflow_database_id_is_read_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("GENAI_METADATA_WORKFLOW_DATABASE_ID", "SYSTEMDB")
        m = load_handler("systemWorkflowLauncher")
        assert m.system_workflow_database_id == "SYSTEMDB"


@pytest.mark.unit
class TestLaunchBody:
    def test_body_is_the_upload_trigger_body_with_reindex_identity(self, launcher):
        body = launcher.build_launch_body(dict(MESSAGE))
        assert body == {
            "inputFiles": [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/part.glb", "versionId": ""}],
            "outputAssetId": "a1", "outputDatabaseId": "db1",
            "pipelineExecutionParameters": {"system-genai-metadata": {"templateId": "system-genai-metadata-default"}},
            "triggerType": "systemReindex", "executionGroupId": "vec-abc123def456-3",
        }

    def test_trigger_row_is_read_by_composite_key_and_cached_per_container(self, launcher):
        launcher.build_launch_body(dict(MESSAGE))
        launcher.build_launch_body({**MESSAGE, "assetId": "a2"})
        launcher.workflow_triggers_table.query.assert_called_once()
        condition = launcher.workflow_triggers_table.query.call_args.kwargs["KeyConditionExpression"]
        # An `And` condition's `_values` are two ConditionBase objects whose repr carries no operand
        # (`str(condition._values)` never contains the composite key); read the leaf operands instead:
        # `Key(name).eq(value)._values == (Key(name), value)`.
        assert condition._values[0]._values[1] == "GLOBAL:system-genai-metadata"
        assert condition._values[1]._values[1] == "fileUpload"

    def test_missing_trigger_row_launches_template_less(self, launcher):
        launcher.workflow_triggers_table.query.return_value = {"Items": []}
        body = launcher.build_launch_body(dict(MESSAGE))
        assert body["pipelineExecutionParameters"] == {} and body["triggerType"] == "systemReindex"


@pytest.mark.unit
class TestInvoke:
    def test_invokes_execute_workflow_once_as_system_user(self, launcher):
        response = launcher.lambda_handler({"Records": [_sqs("m1", MESSAGE)]}, MagicMock())
        assert _identifiers(response) == []
        launcher.lambda_client.invoke.assert_called_once()
        kwargs = launcher.lambda_client.invoke.call_args.kwargs
        # The module read its target at import; another suite may have set the variable first.
        assert kwargs["FunctionName"] == launcher.execute_workflow_v2_function
        assert kwargs["InvocationType"] == "RequestResponse"
        event = json.loads(kwargs["Payload"])
        assert event["lambdaCrossCall"]["userName"] == "SYSTEM_USER"
        slots = event["lambdaCrossCall"]["reindexSlots"]
        assert slots["count"] == launcher.reindex_slot_count
        assert event["requestContext"]["http"] == {"method": "POST", "path": "/workflows/GLOBAL/system-genai-metadata/execute"}
        assert event["pathParameters"] == {"workflowDatabaseId": "GLOBAL", "workflowId": "system-genai-metadata"}
        assert event["queryStringParameters"] == {}
        assert json.loads(event["body"])["triggerType"] == "systemReindex"

    def test_the_invoke_client_delivers_exactly_one_attempt(self, launcher):
        assert launcher.invoke_config.retries == {"total_max_attempts": 1}
        assert launcher.invoke_config.read_timeout == 900

    def test_400_is_dropped_not_redriven(self, launcher):
        launcher.lambda_client.invoke.return_value = _invoke_response(400)
        response = launcher.lambda_handler({"Records": [_sqs("m1", MESSAGE)]}, MagicMock())
        assert _identifiers(response) == [] and response["statusCode"] == 200

    @pytest.mark.parametrize("status", [409, 500, 503])
    def test_non_200_non_400_is_reported_for_redelivery(self, launcher, status):
        """A 429 is no longer among these: it means every reindex slot is in use, which is a wait."""
        launcher.lambda_client.invoke.return_value = _invoke_response(status)
        response = launcher.lambda_handler({"Records": [_sqs("m1", MESSAGE)]}, MagicMock())
        assert _identifiers(response) == ["m1"]

    def test_function_error_is_reported_for_redelivery(self, launcher):
        launcher.lambda_client.invoke.return_value = _invoke_response(200, function_error="Unhandled")
        assert _identifiers(launcher.lambda_handler({"Records": [_sqs("m1", MESSAGE)]}, MagicMock())) == ["m1"]

    def test_throttled_invoke_is_reported_for_redelivery(self, launcher):
        launcher.lambda_client.invoke.side_effect = ClientError(
            {"Error": {"Code": "TooManyRequestsException", "Message": "Rate Exceeded."}}, "Invoke")
        assert _identifiers(launcher.lambda_handler({"Records": [_sqs("m1", MESSAGE)]}, MagicMock())) == ["m1"]

    def test_only_the_failed_message_of_a_batch_is_reported(self, launcher):
        launcher.lambda_client.invoke.side_effect = [_invoke_response(200), _invoke_response(500)]
        response = launcher.lambda_handler(
            {"Records": [_sqs("m1", MESSAGE), _sqs("m2", {**MESSAGE, "assetId": "a2"})]}, MagicMock())
        assert _identifiers(response) == ["m2"]

    @pytest.mark.parametrize("body", ["not json", {"databaseId": "db1"}])
    def test_malformed_message_is_reported_so_it_dead_letters(self, launcher, body):
        response = launcher.lambda_handler({"Records": [_sqs("m1", body)]}, MagicMock())
        assert _identifiers(response) == ["m1"]
        launcher.lambda_client.invoke.assert_not_called()


ENDPOINT = "https://lambda.us-east-1.amazonaws.com/2015-03-31/functions/execute/invocations"


def _closed():
    return ConnectionClosedError(endpoint_url=ENDPOINT, error="RemoteDisconnected", request=None)


def _launched(response):
    return json.loads(response["body"])["launched"]


@pytest.mark.unit
class TestConnectionRetry:
    """D108: a launch over an idle pooled connection the network had closed failed with "Connection was
    closed before we received a valid response", executeWorkflow never saw the call, and the message
    waited out the queue's visibility timeout (three in a row dead-letter it). A connection failure is
    repeated right away inside the launch; anything that answered, or may still be running, is not."""

    @pytest.mark.parametrize("error", [
        _closed(),
        EndpointConnectionError(endpoint_url=ENDPOINT, error="Name or service not known"),
        ConnectTimeoutError(endpoint_url=ENDPOINT, error="timed out"),
    ], ids=["closed", "endpoint", "connect-timeout"])
    def test_a_connection_failure_is_repeated_and_launches_once(self, launcher, error):
        launcher.lambda_client.invoke.side_effect = [error, _invoke_response(200)]
        response = launcher.lambda_handler({"Records": [_sqs("m1", MESSAGE)]}, MagicMock())
        assert _identifiers(response) == [] and _launched(response) == 1
        assert launcher.lambda_client.invoke.call_count == 2
        first, second = (c.kwargs for c in launcher.lambda_client.invoke.call_args_list)
        # The same launch is sent again: same target, same body, same slot candidates.
        assert first == second

    def test_a_second_closed_connection_is_repeated_too(self, launcher):
        launcher.lambda_client.invoke.side_effect = [_closed(), _closed(), _invoke_response(200)]
        response = launcher.lambda_handler({"Records": [_sqs("m1", MESSAGE)]}, MagicMock())
        assert _identifiers(response) == [] and _launched(response) == 1
        assert launcher.lambda_client.invoke.call_count == 3

    def test_repeated_connection_failures_give_up_and_leave_the_message_to_sqs(self, launcher):
        launcher.sqs_client = MagicMock()
        launcher.lambda_client.invoke.side_effect = _closed()
        response = launcher.lambda_handler({"Records": [_sqs("m1", MESSAGE)]}, MagicMock())
        assert _identifiers(response) == ["m1"] and _launched(response) == 0
        assert launcher.lambda_client.invoke.call_count == 3
        launcher.sqs_client.send_message.assert_not_called()

    def test_a_function_error_is_not_repeated(self, launcher):
        launcher.lambda_client.invoke.return_value = _invoke_response(200, function_error="Unhandled")
        assert _identifiers(launcher.lambda_handler({"Records": [_sqs("m1", MESSAGE)]}, MagicMock())) == ["m1"]
        launcher.lambda_client.invoke.assert_called_once()

    @pytest.mark.parametrize("status", [400, 500])
    def test_an_http_answer_from_execute_workflow_is_not_repeated(self, launcher, status):
        launcher.lambda_client.invoke.return_value = _invoke_response(status)
        launcher.lambda_handler({"Records": [_sqs("m1", MESSAGE)]}, MagicMock())
        launcher.lambda_client.invoke.assert_called_once()

    def test_a_lambda_service_error_is_not_repeated(self, launcher):
        launcher.lambda_client.invoke.side_effect = ClientError(
            {"Error": {"Code": "TooManyRequestsException", "Message": "Rate Exceeded."}}, "Invoke")
        assert _identifiers(launcher.lambda_handler({"Records": [_sqs("m1", MESSAGE)]}, MagicMock())) == ["m1"]
        launcher.lambda_client.invoke.assert_called_once()

    @pytest.mark.parametrize("error", [
        ReadTimeoutError(endpoint_url=ENDPOINT, error="Read timed out"),
        ResponseStreamingError(error="IncompleteRead"),
    ], ids=["read-timeout", "response-stream"])
    def test_a_failure_after_the_call_was_delivered_is_not_repeated(self, launcher, error):
        # A read timeout comes after the 900 s executeWorkflow can run; a broken response stream after it
        # answered. Either way the call reached the function, so it is left to SQS.
        launcher.lambda_client.invoke.side_effect = error
        assert _identifiers(launcher.lambda_handler({"Records": [_sqs("m1", MESSAGE)]}, MagicMock())) == ["m1"]
        launcher.lambda_client.invoke.assert_called_once()
