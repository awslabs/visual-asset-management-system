# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared builders and stand-ins for the compliance handler tests.

Import as `from backend.tests.handlers.compliance._harness import ...`. The `sys.modules`
registration this directory depends on lives in `conftest.py`, which pytest loads first.
"""

import importlib.util
import json
import os
from unittest.mock import MagicMock

from common.auth.apiEvent import normalize_event

_BACKEND_SOURCE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend"))

# `to_update_expr` is a MagicMock in the root harness (`common.dynamodb` is mocked). The evaluation
# store unpacks its three-tuple, so a path that writes an asset-state or evaluation row needs the
# real helper. Loaded by path from the real module -- the way tests/conftest.py loads the real
# `query_all_items` -- so the copy cannot drift from what the handlers run against.
_real_dynamodb_spec = importlib.util.spec_from_file_location(
    "_real_common_dynamodb_for_compliance_tests",
    os.path.join(_BACKEND_SOURCE, "common", "dynamodb.py"))
_real_dynamodb = importlib.util.module_from_spec(_real_dynamodb_spec)
_real_dynamodb_spec.loader.exec_module(_real_dynamodb)
REAL_TO_UPDATE_EXPR = _real_dynamodb.to_update_expr

USER = "user1"
DB = "db1"
ASSET = "asset.glb"
SCHEMA = "schema-1"
CASCADE_ID = "5b1e2c3d-0000-4000-8000-000000000001"

# A vams-rules-v1 body carrying one rule of each kind. The metadata rule references a metadata
# schema; the relationship rule wants one parentChild parent; the pipeline rule executes a GLOBAL
# workflow and bounds one measurement.
RULES_SCHEMA_BODY = {
    "schemaFormat": "vams-rules-v1",
    "rules": {
        "owner-present": {
            "ruleType": "metadata",
            "enforcement": "warn",
            "metadataSchemaRef": {"databaseId": DB, "schemaName": "Asset Schema"},
            "checks": [{"name": "req", "validateRequired": True, "validateTypes": True,
                        "additionalRequiredFields": ["owner"]}],
        },
        "has-parent": {
            "ruleType": "relationship",
            "enforcement": "quarantine",
            "checks": [{"name": "parent", "direction": "parents",
                        "relationshipType": "parentChild", "minCount": 1}],
        },
        "residual-bound": {
            "ruleType": "pipeline",
            "enforcement": "quarantine",
            "pipelineRef": {"databaseId": "GLOBAL", "workflowId": "wf-1",
                            "pipelineDatabaseId": "GLOBAL", "pipelineId": "pipe-1"},
            "checks": [{"name": "residual", "outputField": "residual",
                        "tolerance": {"operator": "lte", "value": 1}}],
        },
    },
}

# The same body without the pipeline rule: an evaluation of it completes synchronously.
SYNC_RULES_SCHEMA_BODY = {
    "schemaFormat": "vams-rules-v1",
    "rules": {name: rule for name, rule in RULES_SCHEMA_BODY["rules"].items()
              if rule["ruleType"] != "pipeline"},
}


def rest_event(method, path, path_params=None, query_params=None, body=None):
    """An API Gateway REST (v1) proxy event, the shape the deployed handlers receive.

    `pathParameters` / `queryStringParameters` are sent as `null` when absent -- the REST event's
    "no params" shape (backend/CLAUDE.md Rule 16) -- so a handler that indexes them without
    normalizing crashes here the way it would in production.
    """
    event = {
        "httpMethod": method,
        "path": path,
        "requestContext": {"identity": {"sourceIp": "203.0.113.10"}},
        "headers": {"authorization": "Bearer test-token"},
        "pathParameters": path_params,
        "queryStringParameters": query_params,
    }
    if body is not None:
        event["body"] = body if isinstance(body, str) else json.dumps(body)
    return event


def claims_for(*tokens):
    """A `request_to_claims` stand-in: normalizes the event the way the real one does (Rule 16), then
    returns the given identity. `claims_for()` with no tokens is the empty-token case."""
    def _request_to_claims(event):
        normalize_event(event)
        return {"tokens": list(tokens), "roles": [], "mfaEnabled": False}
    return _request_to_claims


def enforcer(api=True, obj=True):
    """A CasbinEnforcer instance stand-in with fixed Tier-1 (`api`) and Tier-2 (`obj`) answers."""
    instance = MagicMock(name="CasbinEnforcer")
    instance.enforceAPI.return_value = api
    instance.enforce.return_value = obj
    return instance


def body_of(response):
    return json.loads(response["body"])


def schema_row(schema_name=SCHEMA, version=1, body=None, database_id="GLOBAL", is_system=False):
    """A schema-table row as the schema service writes it."""
    return {
        "schemaName": schema_name,
        "internalVersion": version,
        "databaseId": database_id,
        "description": "",
        "schemaBody": json.dumps(body if body is not None else RULES_SCHEMA_BODY),
        "registeredAt": "2026-01-01T00:00:00+00:00",
        "registeredBy": USER,
        "isSystem": is_system,
    }


def put_items(table):
    """Every `Item=` a mock table received through `put_item`, in call order."""
    return [call.kwargs["Item"] for call in table.put_item.call_args_list]


def update_values(table):
    """Every `ExpressionAttributeValues` a mock table received through `update_item`, keyed back to
    attribute names through `ExpressionAttributeNames` when the write aliased them."""
    updates = []
    for call in table.update_item.call_args_list:
        names = call.kwargs.get("ExpressionAttributeNames") or {}
        values = call.kwargs.get("ExpressionAttributeValues") or {}
        expression = call.kwargs.get("UpdateExpression", "")
        resolved = {}
        for clause in expression.replace("SET ", "", 1).split(", "):
            if " = " not in clause:
                continue
            name_token, value_token = clause.split(" = ", 1)
            resolved[names.get(name_token, name_token)] = values.get(value_token)
        updates.append(resolved)
    return updates
