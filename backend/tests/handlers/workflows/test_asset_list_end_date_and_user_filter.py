# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The asset-scoped execution list applies `filterEndDate` and `triggeredByUserId`.

Both parameters reach the asset route (the web sends `filterEndDate` for the Executions tab's
"Custom range..." selection, and the CLI exposes `--filter-end-date`), and the global list applies
both. Accepting a bound without applying it is worse than rejecting it: the rows returned are EXTRA
rather than missing, so the tab shows executions from outside the window the caller asked for while
the date inputs still display the narrow range.

An asset's history is the union of two directions — runs that read the asset (the inputs GSI) and
runs that wrote to it (the configuration table's output-asset GSI) — so an upper bound applied to
only one of them still leaks rows through the other. Both key conditions are asserted here.

These tests drive the REAL handler (`lambda_handler` -> `get_executions`) and inspect the DynamoDB
key conditions it builds plus the rows it returns, rather than asserting a hand-fed projection
echoes what it was given.
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


def _flatten_condition(condition):
    """Flatten a boto3 key condition into [(operator, key_name, (values...))].

    Read through get_expression(): str() on a boto3 condition is an object repr carrying no values,
    so substring-matching one silently matches nothing.
    """
    expression = condition.get_expression()
    operator = expression["operator"]
    if operator in ("AND", "OR"):
        parts = []
        for sub in expression["values"]:
            parts.extend(_flatten_condition(sub))
        return parts
    values = list(expression["values"])
    key_name = getattr(values[0], "name", values[0])
    return [(operator, key_name, tuple(values[1:]))]


def _date_bound(condition):
    """The single executionStartDate bound in a key condition, as (operator, (values...))."""
    bounds = [(op, vals) for op, name, vals in _flatten_condition(condition)
              if name == "executionStartDate"]
    assert len(bounds) == 1, f"expected one executionStartDate bound, got {bounds}"
    return bounds[0]


# Two executions on the same asset, triggered by two different users.
_E1 = {
    "workflowExecutionId": "E1",
    "workflowId": "wf-alpha",
    "workflowDatabaseId": "wdb-one",
    "workflowDatabaseId:workflowId": "wdb-one:wf-alpha",
    "executionStatus": "SUCCEEDED",
    "executionStartDate": "2026-06-16T00:00:00Z",
    "executionStopDate": "2026-06-16T00:05:00Z",
    "triggeredByUserId": "alice@example.com",
}
_E2 = {
    "workflowExecutionId": "E2",
    "workflowId": "wf-beta",
    "workflowDatabaseId": "wdb-two",
    "workflowDatabaseId:workflowId": "wdb-two:wf-beta",
    "executionStatus": "SUCCEEDED",
    "executionStartDate": "2026-06-15T00:00:00Z",
    "executionStopDate": "2026-06-15T00:05:00Z",
    "triggeredByUserId": "bob@example.com",
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

    The handler cannot be imported through ``handlers.workflows`` (its package ``__init__`` imports a
    module the root conftest stubs as a non-package), so it is loaded directly by file path — still
    the real handler.
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
        "real_common_dynamodb_enddate", _module_path("backend", "common", "dynamodb.py"))
    sys.modules["common.dynamodb"].validate_pagination_info = real_ddb.validate_pagination_info

    sys.modules["handlers.auth"].request_to_claims = MagicMock(
        return_value={"tokens": ["u1"], "roles": ["r1"]}
    )
    allow_enforcer = MagicMock()
    allow_enforcer.enforceAPI.return_value = True
    allow_enforcer.enforce.return_value = True
    sys.modules["handlers.authz"].CasbinEnforcer = MagicMock(return_value=allow_enforcer)

    module = _load_by_path(
        "executionService_end_date_filter",
        _module_path("backend", "handlers", "workflows", "executionService.py"),
    )

    module.asset_table = MagicMock()
    module.asset_table.query.return_value = {
        "Items": [{"databaseId": "testdb", "assetId": "x123", "assetName": "a"}]
    }

    inputs_table = MagicMock()
    inputs_table.query.return_value = {
        "Items": [
            {"workflowExecutionId": "E1", "inputAssetFileKey": "/a.glb",
             "databaseId": "testdb", "assetId": "x123",
             "workflowId": "wf-alpha", "workflowDatabaseId": "wdb-one",
             "executionStartDate": "2026-06-16T00:00:00Z"},
            {"workflowExecutionId": "E2", "inputAssetFileKey": "/b.glb",
             "databaseId": "testdb", "assetId": "x123",
             "workflowId": "wf-beta", "workflowDatabaseId": "wdb-two",
             "executionStartDate": "2026-06-15T00:00:00Z"},
        ]
    }
    cfg_table = MagicMock()
    cfg_table.query.return_value = {"Items": []}
    main_table = MagicMock()

    def _main_query(**kwargs):
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
    module._test_inputs_table = inputs_table
    module._test_cfg_table = cfg_table
    return module


def _body(response):
    return json.loads(response["body"])["message"]


def _execution_ids(response):
    return [item["workflowExecutionId"] for item in _body(response)["Items"]]


def _key_condition(table_mock, index_name):
    """The KeyConditionExpression of the (first) query issued against index_name."""
    for call in table_mock.query.call_args_list:
        if call.kwargs.get("IndexName") == index_name:
            return call.kwargs["KeyConditionExpression"]
    raise AssertionError(f"no query issued against {index_name}")


@pytest.mark.unit
class TestAssetListEndDateBound:
    """filterEndDate must bound BOTH directions of an asset's execution history."""

    def test_control_without_end_date_both_directions_are_lower_bounded_only(self):
        """Positive control. Without it, a BETWEEN assertion below could be satisfied by a handler
        that always emits BETWEEN, and the echo assertion by one that always echoes."""
        executionService = _load_handler_authorized()
        response = executionService.lambda_handler(
            _rest_event({"filterStartDate": "2026-06-01T00:00:00Z"}), MagicMock())
        assert response["statusCode"] == 200

        operator, values = _date_bound(_key_condition(
            executionService._test_inputs_table, "WorkflowExecInputsByAssetGSI"))
        assert operator == ">="
        assert values == ("2026-06-01T00:00:00Z",)

        operator, values = _date_bound(_key_condition(
            executionService._test_cfg_table, "WorkflowExecConfigByOutputAssetGSI"))
        assert operator == ">="
        assert values == ("2026-06-01T00:00:00Z",)

        assert "filterEndDate" not in _body(response)

    def test_inputs_gsi_is_bounded_by_the_end_date(self):
        executionService = _load_handler_authorized()
        response = executionService.lambda_handler(
            _rest_event({"filterStartDate": "2026-06-01T00:00:00Z",
                         "filterEndDate": "2026-06-15T23:59:59Z"}), MagicMock())
        assert response["statusCode"] == 200

        operator, values = _date_bound(_key_condition(
            executionService._test_inputs_table, "WorkflowExecInputsByAssetGSI"))
        assert operator == "BETWEEN"
        assert values == ("2026-06-01T00:00:00Z", "2026-06-15T23:59:59Z")

    def test_output_asset_gsi_is_bounded_by_the_end_date(self):
        """The output direction is a separate query on a separate table. Bounding only the inputs
        GSI still returns out-of-range rows for any results-only or generate-from-nothing run, which
        writes no input row at all and is reachable ONLY through this index."""
        executionService = _load_handler_authorized()
        response = executionService.lambda_handler(
            _rest_event({"filterStartDate": "2026-06-01T00:00:00Z",
                         "filterEndDate": "2026-06-15T23:59:59Z"}), MagicMock())
        assert response["statusCode"] == 200

        operator, values = _date_bound(_key_condition(
            executionService._test_cfg_table, "WorkflowExecConfigByOutputAssetGSI"))
        assert operator == "BETWEEN"
        assert values == ("2026-06-01T00:00:00Z", "2026-06-15T23:59:59Z")

    def test_end_date_is_echoed_back_so_the_response_is_self_describing(self):
        """The global list echoes the applied bounds; the asset list must too, or a client cannot
        tell that the window it asked for was the window served."""
        executionService = _load_handler_authorized()
        response = executionService.lambda_handler(
            _rest_event({"filterEndDate": "2026-06-15T23:59:59Z"}), MagicMock())
        assert response["statusCode"] == 200
        body = _body(response)
        assert body["filterEndDate"] == "2026-06-15T23:59:59Z"
        assert body["filterStartDate"]

    def test_end_date_is_canonicalized_before_it_is_applied(self):
        """The bound is a lexicographic sort-key compare, so the applied value must be the
        normalized form rather than whatever spelling the caller sent."""
        executionService = _load_handler_authorized()
        response = executionService.lambda_handler(
            _rest_event({"filterEndDate": "  2026-06-15T23:59:59Z  "}), MagicMock())
        assert response["statusCode"] == 200
        assert _body(response)["filterEndDate"] == "2026-06-15T23:59:59Z"

    def test_malformed_end_date_is_a_400(self):
        """A value that is not a timestamp would otherwise become a lexicographic bound that
        silently widens or empties the window."""
        executionService = _load_handler_authorized()
        response = executionService.lambda_handler(
            _rest_event({"filterEndDate": "not-a-date"}), MagicMock())
        assert response["statusCode"] == 400


@pytest.mark.unit
class TestAssetListTriggeredByUserFilter:
    """triggeredByUserId is accepted on the asset route and must narrow the list."""

    def test_control_no_user_filter_lists_both_executions(self):
        executionService = _load_handler_authorized()
        response = executionService.lambda_handler(_rest_event(None), MagicMock())
        assert response["statusCode"] == 200
        assert sorted(_execution_ids(response)) == ["E1", "E2"]

    def test_user_filter_narrows_the_list(self):
        executionService = _load_handler_authorized()
        response = executionService.lambda_handler(
            _rest_event({"triggeredByUserId": "bob@example.com"}), MagicMock())
        assert response["statusCode"] == 200
        assert _execution_ids(response) == ["E2"]

    def test_unmatched_user_filter_returns_no_rows(self):
        executionService = _load_handler_authorized()
        response = executionService.lambda_handler(
            _rest_event({"triggeredByUserId": "carol@example.com"}), MagicMock())
        assert response["statusCode"] == 200
        assert _execution_ids(response) == []

    def test_empty_user_filter_is_ignored(self):
        """An empty string must mean "no filter" rather than filtering to rows with no triggering
        user — the web omits unset filters, but a hand-built URL can send "".
        """
        executionService = _load_handler_authorized()
        response = executionService.lambda_handler(
            _rest_event({"triggeredByUserId": ""}), MagicMock())
        assert response["statusCode"] == 200
        assert sorted(_execution_ids(response)) == ["E1", "E2"]
