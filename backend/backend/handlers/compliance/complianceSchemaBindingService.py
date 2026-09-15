#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Compliance Schema Binding Service handler.

- GET    /compliance/bind/{databaseId}            — database binding and asset overrides
- PUT    /compliance/bind/{databaseId}            — bind a schema to a database
- DELETE /compliance/bind/{databaseId}            — remove the database binding
- PUT    /compliance/bind/{databaseId}/{assetId}  — bind a schema to an asset (override)
- DELETE /compliance/bind/{databaseId}/{assetId}  — remove the asset override

A database binding lives on the database row (`complianceSchemaName`, `complianceAutoEval`,
`complianceSchemaUpdatedAt`); asset state rows carry `schemaName` + `schemaSource`
("database" or "asset"). An asset-level binding always overrides the database binding: a
database rebinding touches only rows with schemaSource "database", and removing an asset
override falls the asset back to the database schema (or removes its row when there is none).
"""

import json
from datetime import datetime, timezone

import boto3
from aws_lambda_powertools.utilities.parser import ValidationError, parse
from aws_lambda_powertools.utilities.typing import LambdaContext
from boto3.dynamodb.conditions import Key
from botocore.config import Config

from common.apiRoutes import API_COMPLIANCE_BIND_ASSET, API_COMPLIANCE_BIND_DATABASE
from common.dynamodb import query_all_items
from common.resourceNames import ResourceKeys, get_table_name
from common.validators import validate
from customLogging.logger import safeLogger
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from handlers.compliance import complianceEvaluationStore as store
from models.common import (
    APIGatewayProxyResponseV2,
    VAMSGeneralErrorResponse,
    authorization_error,
    general_error,
    internal_error,
    success,
    validation_error,
    validation_error_message,
)
from models.compliance import GLOBAL_DATABASE_ID, BindSchemaRequestModel

retry_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})
dynamodb = boto3.resource("dynamodb", config=retry_config)
logger = safeLogger(service_name="ComplianceSchemaBindingService")

claims_and_roles = {}

COMPLIANCE_SCHEMA_OBJECT_TYPE = "complianceSchema"
SCHEMA_SOURCE_DATABASE = "database"
SCHEMA_SOURCE_ASSET = "asset"
STATE_PENDING_EVALUATION = "pending_evaluation"

try:
    asset_state_table_name = get_table_name(ResourceKeys.COMPLIANCE_ASSET_STATE_STORAGE_TABLE)
    schema_table_name = get_table_name(ResourceKeys.COMPLIANCE_SCHEMA_STORAGE_TABLE)
    database_table_name = get_table_name(ResourceKeys.DATABASE_STORAGE_TABLE)
    asset_table_name = get_table_name(ResourceKeys.ASSET_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed loading resource names")
    raise e

asset_state_table = dynamodb.Table(asset_state_table_name)
schema_table = dynamodb.Table(schema_table_name)
database_table = dynamodb.Table(database_table_name)
asset_table = dynamodb.Table(asset_table_name)


#######################
# Lambda handler
#######################

def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    global claims_and_roles
    claims_and_roles = request_to_claims(event)

    try:
        method = event["requestContext"]["http"]["method"]

        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

        if not method_allowed_on_api:
            return authorization_error()

        if method == "GET":
            return handle_get_request(event)
        elif method == "PUT":
            return handle_put_request(event)
        elif method == "DELETE":
            return handle_delete_request(event)
        else:
            return validation_error(body={"message": "Method not allowed"}, event=event)

    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={"message": validation_error_message(v)}, event=event)
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={"message": str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error(event=event)


#######################
# Method handlers
#######################

def handle_get_request(event):
    path = event["requestContext"]["http"]["path"]
    path_params = event.get("pathParameters", {}) or {}
    if API_COMPLIANCE_BIND_DATABASE.matches(path):
        return get_bindings(event, path_params.get("databaseId"))
    return validation_error(body={"message": "Method not allowed"}, event=event)


def handle_put_request(event):
    path = event["requestContext"]["http"]["path"]
    path_params = event.get("pathParameters", {}) or {}
    if API_COMPLIANCE_BIND_ASSET.matches(path):
        request = parse(_parse_body(event), model=BindSchemaRequestModel)
        return bind_schema_to_asset(event, path_params.get("databaseId"),
                                    path_params.get("assetId"), request)
    if API_COMPLIANCE_BIND_DATABASE.matches(path):
        request = parse(_parse_body(event), model=BindSchemaRequestModel)
        return bind_schema_to_database(event, path_params.get("databaseId"), request)
    return validation_error(body={"message": "Method not allowed"}, event=event)


def handle_delete_request(event):
    path = event["requestContext"]["http"]["path"]
    path_params = event.get("pathParameters", {}) or {}
    if API_COMPLIANCE_BIND_ASSET.matches(path):
        return unbind_schema_from_asset(event, path_params.get("databaseId"),
                                        path_params.get("assetId"))
    if API_COMPLIANCE_BIND_DATABASE.matches(path):
        return unbind_schema_from_database(event, path_params.get("databaseId"))
    return validation_error(body={"message": "Method not allowed"}, event=event)


def _parse_body(event):
    raw = event.get("body") or "{}"
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise VAMSGeneralErrorResponse("Invalid JSON in request body")


#######################
# Authorization helpers
#######################

def _enforce(schema_name, action):
    """Tier-2 check on a schema object. Fails closed on an empty token list."""
    if len(claims_and_roles["tokens"]) == 0:
        return False
    return CasbinEnforcer(claims_and_roles).enforce({
        "object__type": COMPLIANCE_SCHEMA_OBJECT_TYPE,
        "complianceSchemaName": schema_name or "",
    }, action)


def _validate_database(database_id):
    return validate({
        "databaseId": {"value": database_id, "validator": "ID", "allowGlobalKeyword": True},
    })


def _validate_database_and_asset(database_id, asset_id):
    return validate({
        "databaseId": {"value": database_id, "validator": "ID", "allowGlobalKeyword": True},
        "assetId": {"value": asset_id, "validator": "ASSET_ID"},
    })


#######################
# Business logic
#######################

def bind_schema_to_database(event, database_id, request: BindSchemaRequestModel):
    """Bind a schema to a database; every asset without an asset-level override becomes
    pending_evaluation under it."""
    (valid, message) = _validate_database(database_id)
    if not valid:
        return validation_error(body={"message": message}, event=event)

    if not _enforce(request.schemaName, "PUT"):
        return authorization_error()

    visible, exists = _schema_visibility(request.schemaName, database_id)
    if not exists:
        return general_error(body={"message": "Schema not found"}, event=event)
    if not visible:
        return general_error(body={
            "message": "Schema is not available for this database. Only GLOBAL or "
                       "database-scoped schemas can be assigned.",
        }, event=event)

    db_item = database_table.get_item(Key={"databaseId": database_id}).get("Item")
    if not db_item:
        return general_error(body={"message": "Database not found"}, event=event)

    now = datetime.now(timezone.utc).isoformat()
    actor = claims_and_roles["tokens"][0]
    old_schema = db_item.get("complianceSchemaName")
    auto_eval = True if request.complianceAutoEval is None else bool(request.complianceAutoEval)

    database_table.update_item(
        Key={"databaseId": database_id},
        UpdateExpression=(
            "SET complianceSchemaName = :schema,"
            " complianceSchemaUpdatedAt = :now,"
            " complianceAutoEval = :autoEval"
        ),
        ExpressionAttributeValues={
            ":schema": request.schemaName,
            ":now": now,
            ":autoEval": auto_eval,
        },
    )

    affected = mark_database_assets_pending(database_id, request.schemaName, now)

    store.write_audit(
        database_id, "*",
        event_type="schema_bound_to_database",
        actor=actor,
        schema_name=request.schemaName,
        details={"previousSchema": old_schema, "affectedAssets": affected},
    )

    return success(body={
        "message": f"Schema '{request.schemaName}' bound to database '{database_id}'",
        "schemaName": request.schemaName,
        "databaseId": database_id,
        "assetsPendingEvaluation": affected,
    })


def unbind_schema_from_database(event, database_id):
    """Remove the database binding and the asset rows that inherited it."""
    (valid, message) = _validate_database(database_id)
    if not valid:
        return validation_error(body={"message": message}, event=event)

    db_item = database_table.get_item(Key={"databaseId": database_id}).get("Item")
    if not db_item:
        if not _enforce("", "DELETE"):
            return authorization_error()
        return general_error(body={"message": "Database not found"}, event=event)

    old_schema = db_item.get("complianceSchemaName")
    if not _enforce(old_schema or "", "DELETE"):
        return authorization_error()

    if not old_schema:
        return success(body={
            "message": "Database has no schema binding to remove",
            "databaseId": database_id,
        })

    actor = claims_and_roles["tokens"][0]
    database_table.update_item(
        Key={"databaseId": database_id},
        UpdateExpression=(
            "REMOVE complianceSchemaName, complianceSchemaUpdatedAt, complianceAutoEval"
        ),
    )

    removed = _remove_database_compliance_records(database_id)

    store.write_audit(
        database_id, "*",
        event_type="schema_unbound_from_database",
        actor=actor,
        schema_name=old_schema,
        details={"removedComplianceRecords": removed},
    )

    return success(body={
        "message": f"Schema binding removed from database '{database_id}'",
        "databaseId": database_id,
        "previousSchema": old_schema,
        "removedComplianceRecords": removed,
    })


def bind_schema_to_asset(event, database_id, asset_id, request: BindSchemaRequestModel):
    """Bind a schema directly to an asset, overriding the database binding."""
    (valid, message) = _validate_database_and_asset(database_id, asset_id)
    if not valid:
        return validation_error(body={"message": message}, event=event)

    if not _enforce(request.schemaName, "PUT"):
        return authorization_error()

    visible, exists = _schema_visibility(request.schemaName, database_id)
    if not exists:
        return general_error(body={"message": "Schema not found"}, event=event)
    if not visible:
        return general_error(body={
            "message": "Schema is not available for this database. Only GLOBAL or "
                       "database-scoped schemas can be assigned.",
        }, event=event)

    if not store.get_asset_item(database_id, asset_id):
        return general_error(body={"message": "Asset not found"}, event=event)

    now = datetime.now(timezone.utc).isoformat()
    actor = claims_and_roles["tokens"][0]

    existing = store.get_compliance_record(database_id, asset_id) or {}
    store.update_asset_state(database_id, asset_id, {
        "schemaName": request.schemaName,
        "schemaSource": SCHEMA_SOURCE_ASSET,
        "complianceState": STATE_PENDING_EVALUATION,
        "updatedAt": now,
    })

    store.write_audit(
        database_id, asset_id,
        event_type="schema_bound_to_asset",
        actor=actor,
        schema_name=request.schemaName,
        details={
            "previousSchema": existing.get("schemaName"),
            "previousSource": existing.get("schemaSource"),
        },
    )

    return success(body={
        "message": f"Schema '{request.schemaName}' bound to asset",
        "databaseId": database_id,
        "assetId": asset_id,
        "schemaName": request.schemaName,
        "schemaSource": SCHEMA_SOURCE_ASSET,
    })


def unbind_schema_from_asset(event, database_id, asset_id):
    """Remove an asset's schema override, falling back to the database binding."""
    (valid, message) = _validate_database_and_asset(database_id, asset_id)
    if not valid:
        return validation_error(body={"message": message}, event=event)

    existing = store.get_compliance_record(database_id, asset_id)
    current_schema = (existing or {}).get("schemaName", "")
    if not _enforce(current_schema, "DELETE"):
        return authorization_error()

    if not existing or existing.get("schemaSource") != SCHEMA_SOURCE_ASSET:
        return success(body={
            "message": "Asset has no explicit schema override to remove",
            "databaseId": database_id,
            "assetId": asset_id,
        })

    now = datetime.now(timezone.utc).isoformat()
    actor = claims_and_roles["tokens"][0]

    db_item = database_table.get_item(Key={"databaseId": database_id}).get("Item") or {}
    db_schema = db_item.get("complianceSchemaName")

    if db_schema:
        store.update_asset_state(database_id, asset_id, {
            "schemaName": db_schema,
            "schemaSource": SCHEMA_SOURCE_DATABASE,
            "complianceState": STATE_PENDING_EVALUATION,
            "updatedAt": now,
        })
    else:
        asset_state_table.delete_item(Key={"databaseId": database_id, "assetId": asset_id})

    store.write_audit(
        database_id, asset_id,
        event_type="schema_unbound_from_asset",
        actor=actor,
        schema_name=current_schema,
        details={"fallbackSchema": db_schema},
    )

    return success(body={
        "message": "Asset schema override removed",
        "databaseId": database_id,
        "assetId": asset_id,
        "fallbackSchema": db_schema,
        "schemaSource": SCHEMA_SOURCE_DATABASE if db_schema else None,
    })


def get_bindings(event, database_id):
    """The database binding plus every asset-level override in the database."""
    (valid, message) = _validate_database(database_id)
    if not valid:
        return validation_error(body={"message": message}, event=event)

    db_item = database_table.get_item(Key={"databaseId": database_id}).get("Item")
    if not db_item:
        if not _enforce("", "GET"):
            return authorization_error()
        return general_error(body={"message": "Database not found"}, event=event)

    db_schema = db_item.get("complianceSchemaName")
    if not _enforce(db_schema or "", "GET"):
        return authorization_error()

    asset_overrides = []
    for item in _database_asset_state_rows(database_id):
        if item.get("schemaSource") == SCHEMA_SOURCE_ASSET:
            asset_overrides.append({
                "assetId": item["assetId"],
                "schemaName": item.get("schemaName"),
                "complianceState": item.get("complianceState"),
            })

    return success(body={
        "databaseId": database_id,
        "databaseSchema": db_schema,
        "complianceAutoEval": bool(db_item.get("complianceAutoEval", False)),
        "assetOverrides": asset_overrides,
        "assetOverrideCount": len(asset_overrides),
    })


#######################
# Table helpers
#######################

def _database_asset_state_rows(database_id):
    """Every asset-state row in a database, paged to exhaustion."""
    return query_all_items(
        asset_state_table, KeyConditionExpression=Key("databaseId").eq(database_id))


def mark_database_assets_pending(database_id, schema_name, now):
    """Create or refresh the asset-state row of every asset in the database that has no
    asset-level override, marking it pending_evaluation under `schema_name`. Returns the count."""
    existing_items = {
        item["assetId"]: item for item in _database_asset_state_rows(database_id)
    }
    asset_rows = query_all_items(
        asset_table,
        KeyConditionExpression=Key("databaseId").eq(database_id),
        ProjectionExpression="assetId",
    )

    affected = 0
    with asset_state_table.batch_writer() as batch:
        for row in asset_rows:
            asset_id = row.get("assetId")
            if not asset_id:
                continue
            existing = existing_items.get(asset_id)
            if existing and existing.get("schemaSource") == SCHEMA_SOURCE_ASSET:
                continue
            batch.put_item(Item={
                "databaseId": database_id,
                "assetId": asset_id,
                "schemaName": schema_name,
                "schemaSource": SCHEMA_SOURCE_DATABASE,
                "complianceState": STATE_PENDING_EVALUATION,
                "updatedAt": now,
            })
            affected += 1
    return affected


def _remove_database_compliance_records(database_id):
    """Delete the asset-state rows that inherited the database binding (schemaSource
    "database"); asset overrides stay. Returns the count removed."""
    removed = 0
    with asset_state_table.batch_writer() as batch:
        for item in _database_asset_state_rows(database_id):
            if item.get("schemaSource") == SCHEMA_SOURCE_ASSET:
                continue
            batch.delete_item(Key={"databaseId": database_id, "assetId": item["assetId"]})
            removed += 1
    return removed


def _schema_visibility(schema_name, database_id):
    """(visible, exists): whether the schema's latest version exists, and whether it is GLOBAL
    or scoped to `database_id`."""
    response = schema_table.query(
        KeyConditionExpression=Key("schemaName").eq(schema_name),
        ScanIndexForward=False,
        Limit=1,
    )
    items = response.get("Items", [])
    if not items:
        return False, False
    schema_db_id = items[0].get("databaseId", GLOBAL_DATABASE_ID)
    return schema_db_id in (GLOBAL_DATABASE_ID, database_id), True
