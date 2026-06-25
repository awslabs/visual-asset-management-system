# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the createPipeline handler.

Rewritten for the modernized function-based handler. The previous version of this
file targeted a removed `CreatePipeline` class (createLambdaPipeline / upload_Pipeline /
from_env); the handler is now module-level functions (`lambda_handler`,
`upload_pipeline`) using Pydantic request models. These tests exercise the current API.
"""

import json
import pytest
from unittest.mock import patch, MagicMock

from backend.backend.handlers.pipelines.createPipeline import lambda_handler

# Valid request body matching CreatePipelineRequestModel (Lambda execution, user-provided)
VALID_BODY = {
    "waitForCallback": "Disabled",
    "pipelineExecutionType": "Lambda",
    "pipelineType": "standardFile",
    "databaseId": "default",
    "description": "demo pipeline",
    "pipelineId": "demo",
    "assetType": ".stl",
    "outputType": ".stl",
    "lambdaName": "my-existing-lambda",
    "updateAssociatedWorkflows": False,
}


def _make_event(body=None):
    return {
        "requestContext": {
            "http": {
                "method": "POST",
                "path": "/pipelines",
            },
        },
        "body": json.dumps(body if body is not None else VALID_BODY),
        "isBase64Encoded": False,
    }


@patch("backend.backend.handlers.pipelines.createPipeline.request_to_claims")
@patch("backend.backend.handlers.pipelines.createPipeline.CasbinEnforcer")
def test_lambda_handler_not_authorized_api(mock_enforcer, mock_request_to_claims):
    """Tier-1 API denial returns 403 Not Authorized."""
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    enforcer_instance = MagicMock()
    enforcer_instance.enforceAPI.return_value = False
    mock_enforcer.return_value = enforcer_instance

    response = lambda_handler(_make_event(), None)

    assert response["statusCode"] == 403
    assert json.loads(response["body"])["message"] == "Not Authorized"


def test_lambda_handler_missing_body():
    """Missing request body returns a 400 validation error."""
    event = _make_event()
    del event["body"]

    response = lambda_handler(event, None)

    assert response["statusCode"] == 400
    assert "body is required" in json.loads(response["body"])["message"].lower()


@patch("backend.backend.handlers.pipelines.createPipeline.request_to_claims")
def test_lambda_handler_invalid_json(mock_request_to_claims):
    """Malformed JSON body returns a 400 validation error."""
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    event = _make_event()
    event["body"] = "{not-valid-json"

    response = lambda_handler(event, None)

    assert response["statusCode"] == 400
    assert "json" in json.loads(response["body"])["message"].lower()


@patch("backend.backend.handlers.pipelines.createPipeline.request_to_claims")
def test_lambda_handler_invalid_model_missing_required_field(mock_request_to_claims):
    """A body missing a required field fails Pydantic validation with 400."""
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    bad_body = {k: v for k, v in VALID_BODY.items() if k != "pipelineId"}

    response = lambda_handler(_make_event(bad_body), None)

    assert response["statusCode"] == 400


@patch("backend.backend.handlers.pipelines.createPipeline.to_update_expr")
@patch("backend.backend.handlers.pipelines.createPipeline.update_pipeline_workflows")
@patch("backend.backend.handlers.pipelines.createPipeline.dynamodb")
@patch("backend.backend.handlers.pipelines.createPipeline.request_to_claims")
@patch("backend.backend.handlers.pipelines.createPipeline.CasbinEnforcer")
def test_lambda_handler_success(
    mock_enforcer, mock_request_to_claims, mock_dynamodb, mock_update_workflows, mock_to_update_expr
):
    """Authorized request with a valid body creates the pipeline and returns 200."""
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}

    enforcer_instance = MagicMock()
    enforcer_instance.enforceAPI.return_value = True  # Tier 1 allow
    enforcer_instance.enforce.return_value = True      # Tier 2 allow
    mock_enforcer.return_value = enforcer_instance

    # common.dynamodb.to_update_expr is MagicMock'd globally by tests/conftest.py; give it
    # a real (names, values, expression) tuple so the update_item call can unpack it.
    mock_to_update_expr.return_value = ({"#f0": "enabled"}, {":v0": True}, "SET #f0 = :v0")

    # Both the database-existence check and the pipeline-existence check call
    # dynamodb.Table(...).get_item(...). Returning a database row satisfies the
    # existence check; the same shape as an existing-pipeline row is harmless here
    # because the execution type matches (Lambda == Lambda), so no immutability error.
    table = MagicMock()
    table.get_item.return_value = {
        "Item": {"databaseId": "default", "pipelineExecutionType": "Lambda"}
    }
    # The cross-database uniqueness check scans for the pipelineId. Only the
    # request's own database owns it here, so there is no conflict.
    table.scan.return_value = {
        "Items": [{"databaseId": "default", "pipelineId": "demo"}]
    }
    mock_dynamodb.Table.return_value = table

    response = lambda_handler(_make_event(), None)

    assert response["statusCode"] == 200
    assert json.loads(response["body"])["message"] == "Succeeded"
    # The pipeline row must have been written
    assert table.update_item.call_count == 1
    assert table.update_item.call_args[1]["Key"] == {
        "databaseId": "default",
        "pipelineId": "demo",
    }


@patch("backend.backend.handlers.pipelines.createPipeline.dynamodb")
@patch("backend.backend.handlers.pipelines.createPipeline.request_to_claims")
@patch("backend.backend.handlers.pipelines.createPipeline.CasbinEnforcer")
def test_lambda_handler_object_level_denied(
    mock_enforcer, mock_request_to_claims, mock_dynamodb
):
    """Tier-1 allow but Tier-2 (object-level) deny returns 403."""
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}

    enforcer_instance = MagicMock()
    enforcer_instance.enforceAPI.return_value = True   # Tier 1 allow
    enforcer_instance.enforce.return_value = False     # Tier 2 deny
    mock_enforcer.return_value = enforcer_instance

    response = lambda_handler(_make_event(), None)

    assert response["statusCode"] == 403
    assert json.loads(response["body"])["message"] == "Not Authorized"


@patch("backend.backend.handlers.pipelines.createPipeline.dynamodb")
@patch("backend.backend.handlers.pipelines.createPipeline.request_to_claims")
@patch("backend.backend.handlers.pipelines.createPipeline.CasbinEnforcer")
def test_lambda_handler_cross_database_id_conflict(
    mock_enforcer, mock_request_to_claims, mock_dynamodb
):
    """A pipelineId already owned by a different database is rejected with 400."""
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}

    enforcer_instance = MagicMock()
    enforcer_instance.enforceAPI.return_value = True   # Tier 1 allow
    enforcer_instance.enforce.return_value = True      # Tier 2 allow
    mock_enforcer.return_value = enforcer_instance

    table = MagicMock()
    table.get_item.return_value = {"Item": {"databaseId": "default"}}
    # Same pipelineId already exists under a DIFFERENT database -> conflict
    table.scan.return_value = {
        "Items": [{"databaseId": "other-db", "pipelineId": "demo"}]
    }
    mock_dynamodb.Table.return_value = table

    response = lambda_handler(_make_event(), None)

    assert response["statusCode"] == 400
    message = json.loads(response["body"])["message"]
    assert "already in use" in message
    # The generic message must NOT leak the conflicting database id or the input id
    assert "other-db" not in message
    assert "demo" not in message
    # The pipeline row must NOT have been written
    assert table.update_item.call_count == 0


@patch("backend.backend.handlers.pipelines.createPipeline.to_update_expr")
@patch("backend.backend.handlers.pipelines.createPipeline.update_pipeline_workflows")
@patch("backend.backend.handlers.pipelines.createPipeline.dynamodb")
@patch("backend.backend.handlers.pipelines.createPipeline.request_to_claims")
@patch("backend.backend.handlers.pipelines.createPipeline.CasbinEnforcer")
def test_lambda_handler_deleted_id_not_a_conflict(
    mock_enforcer, mock_request_to_claims, mock_dynamodb, mock_update_workflows, mock_to_update_expr
):
    """A pipelineId that exists only in a soft-deleted partition is reusable."""
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}

    enforcer_instance = MagicMock()
    enforcer_instance.enforceAPI.return_value = True
    enforcer_instance.enforce.return_value = True
    mock_enforcer.return_value = enforcer_instance

    mock_to_update_expr.return_value = ({"#f0": "enabled"}, {":v0": True}, "SET #f0 = :v0")

    table = MagicMock()
    table.get_item.return_value = {
        "Item": {"databaseId": "default", "pipelineExecutionType": "Lambda"}
    }
    # The only other record holding this pipelineId is soft-deleted -> not a conflict
    table.scan.return_value = {
        "Items": [{"databaseId": "other-db#deleted", "pipelineId": "demo"}]
    }
    mock_dynamodb.Table.return_value = table

    response = lambda_handler(_make_event(), None)

    assert response["statusCode"] == 200
    assert table.update_item.call_count == 1
