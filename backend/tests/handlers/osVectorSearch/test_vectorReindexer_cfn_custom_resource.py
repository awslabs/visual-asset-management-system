# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""vectorReindexer as a CloudFormation custom resource (`app.vectorSearch.reindexOnCdkDeploy`).

The handler takes the same two invocation shapes as the OpenSearch `crReindexer`: a direct payload, and
a custom-resource request relayed by the CDK provider framework. On Create/Update the resource properties
map onto the direct payload and the run starts; the response is what `cr.Provider` relays, so the handler
never posts to `ResponseURL` itself. A run that outlives one invocation is reported as started with its
run id, a rejected payload or a failed first invocation raises (which the framework reports as FAILED),
and Delete is a no-op because the vector table is retained.
"""

import json
from unittest.mock import MagicMock

import pytest

from tests.handlers.osVectorSearch.vectorsearch_support import FakeVectorStore, load_handler

K1 = {"databaseId:assetId": {"S": "db1:a1"}, "fileVersionKey": {"S": "/a.glb#v1"}}


def _context(remaining_ms=600000):
    context = MagicMock()
    context.get_remaining_time_in_millis.return_value = remaining_ms
    return context


def _cfn_event(request_type="Create", properties=None, physical_id=None):
    event = {
        "RequestType": request_type,
        "ResponseURL": "https://cloudformation-custom-resource-response.example/presigned",
        "StackId": "arn:aws:cloudformation:us-east-1:123456789012:stack/vams/guid",
        "RequestId": "req-1",
        "LogicalResourceId": "VectorReindexTrigger",
        "ResourceType": "AWS::CloudFormation::CustomResource",
        "ResourceProperties": {"ServiceToken": "arn:aws:lambda:us-east-1:123456789012:function:provider",
                               "Operation": "both", "Timestamp": "1700000000000",
                               **(properties or {})},
    }
    if physical_id:
        event["PhysicalResourceId"] = physical_id
    return event


def _deleted(store):
    """Every key the fake store was asked to delete, across all delete_keys calls."""
    return [key for call in store.calls if call[0] == "delete_keys" for key in call[1]]


@pytest.fixture
def reindexer():
    m = load_handler("vectorReindexer")
    m.vector_store = FakeVectorStore(scan_pages=[(None, ([K1], None))])
    m.lambda_client = MagicMock()
    m.sqs_client = MagicMock()
    m.sqs_client.send_message_batch.return_value = {"Successful": [], "Failed": []}
    m.workflow_storage_table_v2 = MagicMock()
    m.workflow_storage_table_v2.get_item.return_value = {"Item": {
        "databaseId": "GLOBAL", "workflowId": "system-genai-metadata",
        "specifiedPipelines": [], "systemConfig": {"inputFileFilters": {"allow": ["*.glb"], "exclude": []}}}}
    m.pipeline_storage_table_v2 = MagicMock()
    m.enumerate_latest_live_files = MagicMock(return_value=([], None))
    return m


@pytest.mark.unit
class TestEventShapeDetection:
    def test_a_direct_payload_is_not_a_cfn_event(self, reindexer):
        assert reindexer.is_cfn_event({"operation": "both"}) is False

    def test_both_keys_are_required(self, reindexer):
        # `RequestType` alone would also match a direct payload that happened to carry it; the
        # presigned ResponseURL is what only CloudFormation supplies.
        assert reindexer.is_cfn_event({"RequestType": "Create"}) is False
        assert reindexer.is_cfn_event(_cfn_event()) is True

    def test_the_direct_shape_still_dispatches_to_the_direct_path(self, reindexer):
        result = reindexer.lambda_handler({"operation": "clear", "dryRun": True}, _context())
        assert result["statusCode"] == 200
        assert json.loads(result["body"])["dryRun"] is True


@pytest.mark.unit
class TestPropertyMapping:
    def test_defaults_match_the_construct(self, reindexer):
        payload = reindexer.cfn_payload_from_properties({"ServiceToken": "t", "Timestamp": "1"})
        assert payload == {"operation": "both"}

    def test_dry_run_and_database_id_are_forwarded(self, reindexer):
        payload = reindexer.cfn_payload_from_properties(
            {"Operation": "enqueue", "DryRun": "true", "DatabaseId": "db1"})
        assert payload == {"operation": "enqueue", "dryRun": True, "databaseId": "db1"}

    def test_an_unknown_property_reaches_validation_instead_of_being_dropped(self, reindexer):
        payload = reindexer.cfn_payload_from_properties({"Operation": "both", "ClearIndexes": "true"})
        params, error = reindexer.validate_payload(payload)
        assert params is None and "ClearIndexes" in error

    def test_a_malformed_dry_run_is_rejected_by_validation(self, reindexer):
        payload = reindexer.cfn_payload_from_properties({"DryRun": "maybe"})
        params, error = reindexer.validate_payload(payload)
        assert params is None and "dryRun" in error


@pytest.mark.unit
class TestCreateAndUpdate:
    def test_create_runs_both_and_reports_completed_when_the_run_finished(self, reindexer):
        response = reindexer.lambda_handler(_cfn_event("Create"), _context())
        assert _deleted(reindexer.vector_store) == [K1]
        assert response["Data"]["Message"] == "Vector reindex completed"
        assert response["Data"]["Continued"] == "false"
        assert response["Data"]["Deleted"] == "1"
        assert response["Data"]["Phase"] == "done"
        assert response["Data"]["ReindexRunId"]
        reindexer.lambda_client.invoke.assert_not_called()

    def test_update_re_fires_the_run_and_keeps_the_physical_id(self, reindexer):
        response = reindexer.lambda_handler(_cfn_event("Update", physical_id="vams-vector-reindex-trigger"),
                                            _context())
        assert response["PhysicalResourceId"] == "vams-vector-reindex-trigger"
        assert _deleted(reindexer.vector_store) == [K1]

    def test_a_run_that_hands_off_to_a_continuation_is_reported_as_started(self, reindexer):
        # The deploy must not wait on Bedrock re-analyzing every file: once the first invocation has
        # queued its continuation the resource is complete, and the run id lets an operator follow it.
        reindexer.enumerate_latest_live_files = MagicMock(return_value=([], "prefix-a/a5/z"))
        response = reindexer.lambda_handler(_cfn_event("Create"), _context())
        assert response["Data"]["Message"] == "Vector reindex started"
        assert response["Data"]["Continued"] == "true"
        assert response["Data"]["Phase"] == "enqueue"
        continuation = json.loads(reindexer.lambda_client.invoke.call_args.kwargs["Payload"])
        assert continuation["continuation"]["runId"] == response["Data"]["ReindexRunId"]

    def test_the_response_carries_no_response_url_post(self, reindexer):
        # The provider framework relays the returned dict; posting to ResponseURL as well would race it.
        assert not hasattr(reindexer, "send_cfn_response")
        assert not hasattr(reindexer, "http")

    def test_a_rejected_payload_raises_so_the_resource_fails(self, reindexer):
        with pytest.raises(ValueError, match="rejected"):
            reindexer.lambda_handler(_cfn_event("Create", {"Operation": "rebuild"}), _context())
        assert _deleted(reindexer.vector_store) == []

    def test_a_failed_first_invocation_raises_so_the_resource_fails(self, reindexer):
        reindexer.workflow_storage_table_v2.get_item.return_value = {}
        with pytest.raises(RuntimeError, match="failed in phase enqueue"):
            reindexer.lambda_handler(_cfn_event("Create"), _context())


@pytest.mark.unit
class TestDelete:
    def test_delete_touches_nothing_and_succeeds(self, reindexer):
        response = reindexer.lambda_handler(_cfn_event("Delete", physical_id="vams-vector-reindex-trigger"),
                                            _context())
        assert response["PhysicalResourceId"] == "vams-vector-reindex-trigger"
        assert _deleted(reindexer.vector_store) == []
        assert reindexer.enumerate_latest_live_files.call_count == 0
        reindexer.lambda_client.invoke.assert_not_called()
