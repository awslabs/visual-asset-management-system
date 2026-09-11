# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Regression test for the REST API (v1) null-query-string event shape in listExecutions.

The workflow-executions GET route is normally called with no query string. REST API (v1)
sends ``queryStringParameters`` as an explicit ``null`` in that case. The handler forwards
those params into ``get_executions``, which builds the DynamoDB paginator config with
``int(query_params['maxItems'])`` — so if the event is not normalized first (null coerced
to ``{}`` and the pagination defaults filled in), the authorized path crashes with
``TypeError: 'NoneType' object is not subscriptable`` -> 500.

This test drives the AUTHORIZED path (the only path that reaches the crash) and asserts a
200. Removing the ``normalize_event(event)`` call from the handler makes it return 500, so
this test genuinely guards the fix (verified by mutation).
"""

import os
import sys
import importlib.util
import pytest
from unittest.mock import MagicMock


def _rest_event_no_query():
    # REST API (v1) proxy event for GET .../workflows/executions with no query string:
    # queryStringParameters arrives as an explicit null.
    return {
        "httpMethod": "GET",
        "path": "/database/test/assets/x123/workflows/executions",
        "resource": "/database/{databaseId}/assets/{assetId}/workflows/executions",
        "pathParameters": {"databaseId": "test", "assetId": "x123"},
        "queryStringParameters": None,
        "requestContext": {
            "identity": {"sourceIp": "203.0.113.7"},
            "authorizer": {"claims": {"sub": "u1", "email": "u@example.com"}},
        },
        "headers": {"Authorization": "Bearer test-token"},
    }


def _module_path(*parts):
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", *parts))


def _load_by_path(module_name, file_path):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_list_executions_authorized():
    """Load the real listExecutions handler under the mock harness, wired for the
    authorized path.

    The handler cannot be imported through ``handlers.workflows`` (the package
    ``__init__.py`` imports ``common.stepfunctions_builder``, which the root conftest stubs
    ``common`` as a non-package — the same reason every other workflows handler test is
    skipped). Loading the module file directly by path bypasses the package ``__init__``
    while still exercising the real handler. We then fill the harness gaps the handler
    needs:

    * ``handlers.auth``/``handlers.authz`` are bare ``MockModule`` instances — give them a
      ``request_to_claims`` that returns a token and a ``CasbinEnforcer`` that allows, so the
      request reaches ``get_executions`` (where the null-query crash lives).
    * The mock ``common.dynamodb`` omits ``validate_pagination_info`` — bind the REAL one so
      the pagination defaulting is exercised, not stubbed away.
    """
    os.environ.setdefault("WORKFLOW_EXECUTION_STORAGE_TABLE_NAME", "wf-exec-table")
    os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "assetStorageTable")
    # executionService (the renamed listExecutions) resolves its full V2 execution
    # table set at import; the env-var override path keeps resolution offline.
    for env, val in {
        "WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME": "t-exec-v2",
        "WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME": "t-wf-inputs",
        "WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME": "t-wf-cfg",
        "PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME": "t-pexec",
        "PIPELINE_EXECUTION_INPUT_FILES_STORAGE_TABLE_NAME": "t-pin-files",
        "PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME": "t-pin-md",
        "PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME": "t-pin-cfg",
        "PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME": "t-of",
        "PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE_NAME": "t-om",
        "PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE_NAME": "t-or",
        "PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME": "t-logs",
        "WORKFLOW_STORAGE_TABLE_NAME": "t-workflows",
        "PIPELINE_STORAGE_TABLE_NAME": "t-pipelines",
        "ASSET_FILE_VERSION_HISTORY_STORAGE_TABLE_NAME": "t-afvh",
    }.items():
        os.environ.setdefault(env, val)

    real_ddb = _load_by_path("real_common_dynamodb", _module_path("backend", "common", "dynamodb.py"))
    sys.modules["common.dynamodb"].validate_pagination_info = real_ddb.validate_pagination_info

    sys.modules["handlers.auth"].request_to_claims = MagicMock(
        return_value={"tokens": ["u1"], "roles": ["r1"]}
    )
    allow_enforcer = MagicMock()
    allow_enforcer.enforceAPI.return_value = True
    allow_enforcer.enforce.return_value = True
    sys.modules["handlers.authz"].CasbinEnforcer = MagicMock(return_value=allow_enforcer)

    module = _load_by_path(
        "executionService_under_test",
        _module_path("backend", "handlers", "workflows", "executionService.py"),
    )

    # Stub the AWS resources the authorized path touches so it stays offline.
    module.asset_table = MagicMock()
    module.asset_table.query.return_value = {
        "Items": [{"databaseId": "test", "assetId": "x123", "assetName": "a"}]
    }
    module.dynamodb = MagicMock()
    module.dynamodb.meta.client.get_paginator.return_value.paginate.return_value.build_full_result.return_value = {
        "Items": []
    }
    # V2 listing reads the inputs GSI + main table via dynamodb.Table(...); the MagicMock
    # above returns empty Items so the authorized path completes with an empty page.
    return module


@pytest.mark.unit
class TestListExecutionsRestEventShape:
    """listExecutions must tolerate the REST v1 null pathParameters/queryStringParameters."""

    def test_null_query_string_does_not_500(self):
        executionService = _load_list_executions_authorized()

        response = executionService.lambda_handler(_rest_event_no_query(), MagicMock())

        # The authorized path reaches get_executions and builds the paginator config from
        # the (now-normalized) query params. Without normalize_event it 500s on int(None).
        assert response["statusCode"] != 500
        assert response["statusCode"] == 200
