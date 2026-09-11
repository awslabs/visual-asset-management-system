# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The asset-scoped execution list accepts the workflow filter as QUERY parameters.

The asset route already supported a workflow filter, but only in a form a browser cannot send:
``workflowId`` came from the ``.../executions/{workflowId}`` path variant and its companion
``workflowDatabaseId`` was read from a GET request BODY. fetch/XHR cannot send a GET body, so the web
client could supply at most half of a composite key that is only unique as a pair.

These tests drive the REAL handler end to end (``lambda_handler`` -> ``get_executions`` -> the filter)
with two executions of DIFFERENT workflows on the same asset, so they exercise the filter rather than
asserting that a hand-fed projection echoes what it was given.

Two properties are pinned deliberately:

* ``workflowId`` alone narrows the list. The path form compares against the joined
  ``workflowDatabaseId:workflowId`` key, which treats a missing database as ``""`` and so yields
  ``":wf1"`` — matching nothing and returning an empty list with no hint the filter caused it. The
  query form matches per field instead.
* A malformed id is a 400, not an empty 200. Without validation an unmatchable value is compared
  as-is, and the caller cannot distinguish a typo from "this asset never ran that workflow".
"""

import os
import sys
import json
import importlib.util
import pytest
from unittest.mock import MagicMock


def _module_path(*parts):
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", *parts))


def _load_by_path(module_name, file_path):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Two executions on the same asset, from two different workflows in two different databases.
_E1 = {
    "workflowExecutionId": "E1",
    "workflowId": "wf-alpha",
    "workflowDatabaseId": "wdb-one",
    "workflowDatabaseId:workflowId": "wdb-one:wf-alpha",
    "executionStatus": "SUCCEEDED",
    "executionStartDate": "2099-06-16T00:00:00Z",
    "executionStopDate": "2099-06-16T00:05:00Z",
}
_E2 = {
    "workflowExecutionId": "E2",
    "workflowId": "wf-beta",
    "workflowDatabaseId": "wdb-two",
    "workflowDatabaseId:workflowId": "wdb-two:wf-beta",
    "executionStatus": "SUCCEEDED",
    "executionStartDate": "2099-06-15T00:00:00Z",
    "executionStopDate": "2099-06-15T00:05:00Z",
}
_MAIN_ROWS = {"E1": _E1, "E2": _E2}


def _rest_event(query_params):
    """REST (v1) proxy event for GET .../workflows/executions with a query string."""
    return {
        "httpMethod": "GET",
        "path": "/database/testdb/assets/x123/workflows/executions",
        "resource": "/database/{databaseId}/assets/{assetId}/workflows/executions",
        "pathParameters": {"databaseId": "testdb", "assetId": "x123"},
        "queryStringParameters": query_params,
        "requestContext": {
            "identity": {"sourceIp": "203.0.113.7"},
            "authorizer": {"claims": {"sub": "u1", "email": "u@example.com"}},
        },
        "headers": {"Authorization": "Bearer test-token"},
    }


def _load_handler_authorized():
    """Load the real executionService under the mock harness, wired for the authorized path.

    Mirrors test_listExecutions_rest_event_shape's loader: the handler cannot be imported through
    ``handlers.workflows`` (its package ``__init__`` imports a module the root conftest stubs as a
    non-package), so it is loaded directly by file path — still the real handler.
    """
    for env, val in {
        "WORKFLOW_EXECUTION_STORAGE_TABLE_NAME": "wf-exec-table",
        "ASSET_STORAGE_TABLE_NAME": "assetStorageTable",
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

    # The mock common.dynamodb omits validate_pagination_info; bind the REAL one so pagination
    # defaulting is exercised rather than stubbed away.
    real_ddb = _load_by_path(
        "real_common_dynamodb_wfq", _module_path("backend", "common", "dynamodb.py"))
    sys.modules["common.dynamodb"].validate_pagination_info = real_ddb.validate_pagination_info

    sys.modules["handlers.auth"].request_to_claims = MagicMock(
        return_value={"tokens": ["u1"], "roles": ["r1"]}
    )
    allow_enforcer = MagicMock()
    allow_enforcer.enforceAPI.return_value = True
    allow_enforcer.enforce.return_value = True
    sys.modules["handlers.authz"].CasbinEnforcer = MagicMock(return_value=allow_enforcer)

    module = _load_by_path(
        "executionService_wf_query_filter",
        _module_path("backend", "handlers", "workflows", "executionService.py"),
    )

    module.asset_table = MagicMock()
    module.asset_table.query.return_value = {
        "Items": [{"databaseId": "testdb", "assetId": "x123", "assetName": "a"}]
    }

    # The listing reads three tables through dynamodb.Table(name): the inputs GSI (one row per input
    # file), the configuration table's output-asset GSI, and the V2 main table (per execution).
    inputs_table = MagicMock()
    inputs_table.query.return_value = {
        "Items": [
            {"workflowExecutionId": "E1", "inputAssetFileKey": "/a.glb",
             "databaseId": "testdb", "assetId": "x123",
             "workflowId": "wf-alpha", "workflowDatabaseId": "wdb-one",
             "executionStartDate": "2099-06-16T00:00:00Z"},
            {"workflowExecutionId": "E2", "inputAssetFileKey": "/b.glb",
             "databaseId": "testdb", "assetId": "x123",
             "workflowId": "wf-beta", "workflowDatabaseId": "wdb-two",
             "executionStartDate": "2099-06-15T00:00:00Z"},
        ]
    }
    cfg_table = MagicMock()
    cfg_table.query.return_value = {"Items": []}
    main_table = MagicMock()

    def _main_query(**kwargs):
        # The handler queries by workflowExecutionId equality; recover which id from the condition
        # so one stub serves every per-execution read. Read via get_expression() — str() on a boto3
        # condition is an object repr ("<...Equals object at 0x...>") carrying no values, so
        # substring-matching it silently matches nothing.
        condition = kwargs.get("KeyConditionExpression")
        requested_id = condition.get_expression()["values"][1]
        row = _MAIN_ROWS.get(requested_id)
        return {"Items": [row]} if row else {"Items": []}

    main_table.query.side_effect = _main_query

    def _table(name):
        if name == os.environ["WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME"]:
            return inputs_table
        if name == os.environ["WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME"]:
            return cfg_table
        return main_table

    module.dynamodb = MagicMock()
    module.dynamodb.Table.side_effect = _table
    # Terminal executions with no ARN: _describe returns None, so no Step Functions call is made.
    module.sfn = MagicMock()
    return module


def _execution_ids(response):
    body = json.loads(response["body"])
    return [item["workflowExecutionId"] for item in body["message"]["Items"]]


@pytest.mark.unit
class TestAssetListWorkflowQueryFilter:
    """The workflow filter must be reachable as query parameters, and must be validated."""

    def test_control_no_filter_lists_both_executions(self):
        """Positive control. Without it, every assertion below is satisfiable by a listing that
        returns nothing for an unrelated reason."""
        executionService = _load_handler_authorized()
        response = executionService.lambda_handler(
            _rest_event(None), MagicMock())
        assert response["statusCode"] == 200
        assert sorted(_execution_ids(response)) == ["E1", "E2"]

    def test_workflow_id_alone_narrows_the_list(self):
        """The property the composite-key form cannot provide: a database-less workflowId still
        filters. Comparing against the joined key would build ":wf-alpha" and match neither row."""
        executionService = _load_handler_authorized()
        response = executionService.lambda_handler(
            _rest_event({"workflowId": "wf-alpha"}), MagicMock())
        assert response["statusCode"] == 200
        assert _execution_ids(response) == ["E1"]

    def test_both_halves_of_the_composite_narrow_the_list(self):
        executionService = _load_handler_authorized()
        response = executionService.lambda_handler(
            _rest_event({"workflowId": "wf-beta", "workflowDatabaseId": "wdb-two"}), MagicMock())
        assert response["statusCode"] == 200
        assert _execution_ids(response) == ["E2"]

    def test_mismatched_halves_match_nothing(self):
        """Both filters are AND-ed per field: a real workflow id paired with the wrong database
        matches no row, rather than the id alone winning."""
        executionService = _load_handler_authorized()
        response = executionService.lambda_handler(
            _rest_event({"workflowId": "wf-alpha", "workflowDatabaseId": "wdb-two"}), MagicMock())
        assert response["statusCode"] == 200
        assert _execution_ids(response) == []

    def test_workflow_database_id_alone_narrows_the_list(self):
        executionService = _load_handler_authorized()
        response = executionService.lambda_handler(
            _rest_event({"workflowDatabaseId": "wdb-one"}), MagicMock())
        assert response["statusCode"] == 200
        assert _execution_ids(response) == ["E1"]

    @pytest.mark.parametrize("bad_value", ["a", "has space", "semi;colon", "x" * 64])
    def test_malformed_workflow_id_is_a_400_not_an_empty_200(self, bad_value):
        """An unvalidated value would be compared as-is and yield an empty 200 — indistinguishable
        from a genuine empty result, so a typo looks like history."""
        executionService = _load_handler_authorized()
        response = executionService.lambda_handler(
            _rest_event({"workflowId": bad_value}), MagicMock())
        assert response["statusCode"] == 400

    @pytest.mark.parametrize("bad_value", ["a", "has space", "semi;colon"])
    def test_malformed_workflow_database_id_is_a_400(self, bad_value):
        executionService = _load_handler_authorized()
        response = executionService.lambda_handler(
            _rest_event({"workflowDatabaseId": bad_value}), MagicMock())
        assert response["statusCode"] == 400

    def test_global_keyword_is_accepted_as_a_workflow_database(self):
        """GLOBAL is a real database id for shared workflows, so it must pass validation. It is not
        one of the seeded rows, so the list is legitimately empty — the point is the 200."""
        executionService = _load_handler_authorized()
        response = executionService.lambda_handler(
            _rest_event({"workflowDatabaseId": "GLOBAL"}), MagicMock())
        assert response["statusCode"] == 200
        assert _execution_ids(response) == []

    def test_empty_filter_values_are_ignored(self):
        """An empty string must behave as "no filter" rather than filtering to rows whose workflow
        id is empty — the web sends omitted filters as absent, but a hand-built URL can send "".
        """
        executionService = _load_handler_authorized()
        response = executionService.lambda_handler(
            _rest_event({"workflowId": "", "workflowDatabaseId": ""}), MagicMock())
        assert response["statusCode"] == 200
        assert sorted(_execution_ids(response)) == ["E1", "E2"]
