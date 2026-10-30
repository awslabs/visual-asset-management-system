# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared builders and stand-ins for the compliance handler tests.

Import as `from backend.tests.handlers.compliance._harness import ...`. The `sys.modules`
registration this directory depends on lives in `conftest.py`, which pytest loads first.
"""

import base64
import contextlib
import importlib.util
import json
import os
import types
from unittest.mock import MagicMock, NonCallableMock, patch

from botocore.exceptions import ClientError

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


@contextlib.contextmanager
def real_constants(*module_names):
    """The real `common.constants` (the permission field matrix the policy builder reads), for a
    test that runs the production enforcer.

    The root harness installs a bare mock of the module; `tests/handlers/auth/conftest.py` installs
    the real one, but only when the auth tests are collected, so a test that leans on it passes in
    the full run and fails alone. This installs the real module when the mock is in place (and
    restores the mock afterwards), and in each of `module_names` points every name it bound from
    the mock at import (`from common.constants import ...`) at the real value until the context
    ends; those modules read the names at call time. Yields the module.
    """
    import importlib
    import sys

    def is_real(module):
        return isinstance(module, types.ModuleType) and isinstance(
            getattr(module, "PERMISSION_CONSTRAINT_FIELDS", None), dict)

    previous = sys.modules.get("common.constants")
    installed = not is_real(previous)
    if installed:
        spec = importlib.util.spec_from_file_location(
            "common.constants", os.path.join(_BACKEND_SOURCE, "common", "constants.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules["common.constants"] = module
        spec.loader.exec_module(module)
    constants = sys.modules["common.constants"]
    public = [name for name in vars(constants) if not name.startswith("_")]
    patches = []
    for name in module_names:
        loaded = sys.modules.get(name)
        if loaded is None:
            continue
        # A name the module took from the mock `common.constants` at import is a mock: point it at
        # the real value for the duration (the modules read these names at call time).
        for attribute in public:
            if isinstance(vars(loaded).get(attribute), NonCallableMock):
                patches.append(patch.object(loaded, attribute, vars(constants)[attribute]))
    for active in patches:
        active.start()
    try:
        yield constants
    finally:
        for active in reversed(patches):
            active.stop()
        if installed:
            if previous is None:
                sys.modules.pop("common.constants", None)
            else:
                sys.modules["common.constants"] = previous

USER = "user1"
DB = "db1"
ASSET = "asset.glb"
SCHEMA = "schema-1"
CASCADE_ID = "5b1e2c3d-0000-4000-8000-000000000001"

# The compliance asset-state table as the CDK defines it (storageBuilder-nestedStack.ts): PK
# databaseId / SK assetId, GSI SchemaNameIndex (schemaName, complianceState) and GSI
# ComplianceStateIndex (complianceState, databaseId) and GSI DatabaseAssetNameIndex (databaseId,
# assetNameSort), all projecting ALL. `create_table(**...)`
# kwargs for a moto-backed DynamoDB resource; the name is the one the root harness seeds.
COMPLIANCE_ASSET_STATE_TABLE_DEFINITION = {
    "TableName": os.environ.get("COMPLIANCE_ASSET_STATE_STORAGE_TABLE_NAME", "t-compliance-asset-state"),
    "KeySchema": [
        {"AttributeName": "databaseId", "KeyType": "HASH"},
        {"AttributeName": "assetId", "KeyType": "RANGE"},
    ],
    "AttributeDefinitions": [
        {"AttributeName": "databaseId", "AttributeType": "S"},
        {"AttributeName": "assetId", "AttributeType": "S"},
        {"AttributeName": "schemaName", "AttributeType": "S"},
        {"AttributeName": "complianceState", "AttributeType": "S"},
        {"AttributeName": "assetNameSort", "AttributeType": "S"},
    ],
    "GlobalSecondaryIndexes": [
        {
            "IndexName": "SchemaNameIndex",
            "KeySchema": [
                {"AttributeName": "schemaName", "KeyType": "HASH"},
                {"AttributeName": "complianceState", "KeyType": "RANGE"},
            ],
            "Projection": {"ProjectionType": "ALL"},
        },
        {
            "IndexName": "ComplianceStateIndex",
            "KeySchema": [
                {"AttributeName": "complianceState", "KeyType": "HASH"},
                {"AttributeName": "databaseId", "KeyType": "RANGE"},
            ],
            "Projection": {"ProjectionType": "ALL"},
        },
        {
            "IndexName": "DatabaseAssetNameIndex",
            "KeySchema": [
                {"AttributeName": "databaseId", "KeyType": "HASH"},
                {"AttributeName": "assetNameSort", "KeyType": "RANGE"},
            ],
            "Projection": {"ProjectionType": "ALL"},
        },
    ],
    "BillingMode": "PAY_PER_REQUEST",
}

# A vams-rules-v1 body carrying one rule of each kind. The metadata rule references a metadata
# schema; the relationship rule wants one parentChild parent; the pipeline rule executes a GLOBAL
# workflow and bounds one measurement.
RULES_SCHEMA_BODY = {
    "schemaFormat": "vams-rules-v1",
    "rules": {
        "owner-present": {
            "ruleType": "metadata",
            "enforcement": "warn",
            # No databaseId: looked up in the evaluated asset's database (DB), then GLOBAL.
            "metadataSchemaRef": {"schemaName": "Asset Schema"},
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


def mirror_engine(instance, real=None):
    """Complete a CasbinEnforcer stand-in for the archived-asset decisions (`ArchivedAssets`): the
    engine call they make (`service_object.enforce`, no audit record each) and the policy question
    `policy_object_fields`.

    With `real` (a production CasbinEnforcer the stand-in's `enforce` delegates to), both are the
    real enforcer's. Without, the engine answers as the stand-in's `enforce` does, whatever it is
    later set to, without recording a call on it, and the policy reads no asset field but the
    database id (a stand-in decides on the database id at most). Returns the stand-in."""
    if real is not None:
        instance.service_object = real.service_object
        instance.claims_and_roles = real.claims_and_roles
        instance.policy_object_fields.side_effect = real.policy_object_fields
        return instance

    def engine(obj, action):
        effect = instance.enforce.side_effect
        return effect(obj, action) if effect is not None else instance.enforce.return_value
    instance.service_object.enforce.side_effect = engine
    instance.policy_object_fields.return_value = frozenset()
    instance.claims_and_roles = {"tokens": [USER], "roles": [], "mfaEnabled": False}
    return instance


def enforcer(api=True, obj=True):
    """A CasbinEnforcer instance stand-in with fixed Tier-1 (`api`) and Tier-2 (`obj`) answers."""
    instance = MagicMock(name="CasbinEnforcer")
    instance.enforceAPI.return_value = api
    instance.enforce.return_value = obj
    return mirror_engine(instance)


def enforcer_for_object_types(*allowed_object_types):
    """A CasbinEnforcer stand-in that passes Tier-1 and grants Tier-2 only to objects whose
    `object__type` is one of `allowed_object_types` -- the shape that proves a second object is
    enforced independently of the first."""
    instance = enforcer()
    instance.enforce.side_effect = (
        lambda obj, action: obj.get("object__type") in allowed_object_types)
    return instance


ARCHIVED_SUFFIX = "#deleted"


def live_only_asset_table(live_item=None, archived=()):
    """An asset-table stand-in for the services' archived-asset reads (`common.compliance.
    archivedAssets`): a `get_item` of a live key answers `live_item` (None: no row), of an archived
    key `<databaseId>#deleted` a row only for an asset id in `archived`, and a `query` of an archived
    partition the `archived` ids. With the defaults no asset is archived."""
    table = MagicMock(name="asset_table")

    def get_item(Key, **_kwargs):
        if Key["databaseId"].endswith(ARCHIVED_SUFFIX):
            return {"Item": {"assetId": Key["assetId"]}} if Key["assetId"] in archived else {}
        return {"Item": dict(live_item)} if live_item is not None else {}

    table.get_item.side_effect = get_item
    table.query.side_effect = lambda **_kwargs: {"Items": [{"assetId": a} for a in archived]}
    return table


def database_table_with(*database_ids):
    """A database-table stand-in for the existence check of the database-level compliance reads
    (`common.compliance.knownDatabase`): a `get_item` answers a row for each id in `database_ids`
    (a deleted database's row id `<databaseId>#deleted` included when listed so) and none for any
    other id."""
    table = MagicMock(name="database_table")
    table.get_item.side_effect = lambda Key, **_kwargs: (
        {"Item": {"databaseId": Key["databaseId"]}} if Key["databaseId"] in database_ids else {})
    return table


def stored_id_checks(live_object, action):
    """The two Tier-2 checks an asset-scoped compliance check makes when the live one passes: the
    object with the live database id, then with the stored id `<databaseId>#deleted`
    (`ArchivedAssets.decide`)."""
    archived_object = dict(live_object, databaseId=f"{live_object['databaseId']}{ARCHIVED_SUFFIX}")
    return [(live_object, action), (archived_object, action)]


def encode_token(value):
    """A pagination token as the handlers emit one: Base64 of the JSON value."""
    return base64.b64encode(json.dumps(value).encode("utf-8")).decode("utf-8")


def decode_token(token):
    return json.loads(base64.b64decode(token))


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


def _ownership_clause_holds(row, clause, names, values):
    """One clause of a ConditionExpression the evaluation store writes the asset-state row under:
    `attribute_not_exists(#n)`, `#n < :v` (strings compare as DynamoDB compares them, by code
    point) or `#n = :v`."""
    clause = clause.strip()
    if clause.startswith("attribute_not_exists("):
        return names[clause[len("attribute_not_exists("):-1]] not in row
    for operator in (" < ", " = "):
        if operator in clause:
            name_token, value_token = clause.split(operator, 1)
            attribute, value = names[name_token], values[value_token]
            if attribute not in row:
                return False
            return row[attribute] < value if operator == " < " else row[attribute] == value
    raise AssertionError(f"Unsupported condition clause: {clause}")


class FakeAssetStateTable:
    """An in-memory asset-state table (PK databaseId, SK assetId) that HONORS the ownership
    condition the evaluation store writes under, the way DynamoDB does: `update_item` applies its
    SET only when one OR-clause of the ConditionExpression holds, else raises
    ConditionalCheckFailedException; `get_item` returns the row as last written, whatever
    `ConsistentRead` says. `mock` is the MagicMock the store is patched with, so the harness helpers
    (`update_values`) still read its call history, and `calls` lists `(operation, ConsistentRead)`
    for every read and `("update_item", None)` for every write, in order."""

    def __init__(self, *rows):
        self.rows = {(row["databaseId"], row["assetId"]): dict(row) for row in rows}
        self.calls = []
        self.mock = MagicMock(name="asset_state_table")
        self.mock.get_item.side_effect = self._get_item
        self.mock.update_item.side_effect = self._update_item
        self.mock.put_item.side_effect = self._put_item

    def row(self, database_id, asset_id):
        return self.rows.get((database_id, asset_id))

    def _get_item(self, Key, ConsistentRead=False, **_):
        self.calls.append(("get_item", ConsistentRead))
        row = self.rows.get((Key["databaseId"], Key["assetId"]))
        return {"Item": dict(row)} if row is not None else {}

    def _put_item(self, Item, **_):
        self.rows[(Item["databaseId"], Item["assetId"])] = dict(Item)
        return {}

    def _update_item(self, Key, UpdateExpression, ExpressionAttributeNames,
                     ExpressionAttributeValues, ConditionExpression=None, **_):
        self.calls.append(("update_item", None))
        key = (Key["databaseId"], Key["assetId"])
        row = dict(self.rows.get(key) or {})
        if ConditionExpression is not None and ConditionExpression.startswith("attribute_exists(#db) AND "):
            # The name refresh (`store.refresh_asset_state_name`): only an existing row whose name
            # differs is written.
            name = ExpressionAttributeValues[":name"]
            if not row or (row.get("assetName") == name and "assetNameSort" in row):
                raise ClientError(
                    {"Error": {"Code": "ConditionalCheckFailedException", "Message": "x"}},
                    "UpdateItem")
            ConditionExpression = None
        if ConditionExpression is not None and not any(
                _ownership_clause_holds(row, clause, ExpressionAttributeNames,
                                        ExpressionAttributeValues)
                for clause in ConditionExpression.split(" OR ")):
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException", "Message": "x"}},
                "UpdateItem")
        for clause in UpdateExpression.replace("SET ", "", 1).split(", "):
            name_token, value_token = clause.split(" = ", 1)
            row[ExpressionAttributeNames[name_token]] = ExpressionAttributeValues[value_token]
        row.update({"databaseId": key[0], "assetId": key[1]})
        self.rows[key] = row
        return {}
