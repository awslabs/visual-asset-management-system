# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""systemWorkflowLauncher: one execute-workflow cross-call per launch message.

The launcher invokes executeWorkflowV2 exactly once (no retry: the call is not idempotent), as
SYSTEM_USER, with `triggerType: "systemReindex"` and `executionGroupId: vec-{runId}-{chunk}`, using the
system workflow's fileUpload trigger defaults so a reindex run selects the template an upload run would.
A 400 (the per-file-version lock or a validation refusal) is dropped; a throttle, a 5xx or an invocation
fault is reported so SQS redelivers and, after three attempts, dead-letters the message.
"""

import io
import json
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

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
        assert kwargs["FunctionName"] == "executeWorkflowV2-test" and kwargs["InvocationType"] == "RequestResponse"
        event = json.loads(kwargs["Payload"])
        assert event["lambdaCrossCall"] == {"userName": "SYSTEM_USER"}
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

    @pytest.mark.parametrize("status", [429, 500, 503])
    def test_non_200_non_400_is_reported_for_redelivery(self, launcher, status):
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
