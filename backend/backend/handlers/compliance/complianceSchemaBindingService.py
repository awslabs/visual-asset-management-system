#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Compliance Schema Binding Service handler.

Manages compliance schema bindings to databases and assets:
- PUT /compliance/bind/{databaseId} — Bind schema to database
- DELETE /compliance/bind/{databaseId} — Remove database binding
- PUT /compliance/bind/{databaseId}/{assetId} — Bind schema to asset (override)
- DELETE /compliance/bind/{databaseId}/{assetId} — Remove asset override (fall back to database)
- GET /compliance/bind/{databaseId} — Get database binding and asset overrides

Schema precedence:
- Asset-level bindings always override database-level bindings.
- When a database schema changes, only assets with schemaSource="database" are
  marked pending_evaluation. Assets with schemaSource="asset" are untouched.
- Removing an asset-level binding reverts the asset to the database schema and
  marks it pending_evaluation.
"""

import json
import os
import uuid
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config

from common.constants import STANDARD_JSON_RESPONSE
from customLogging.logger import safeLogger
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from models.common import (
    APIGatewayProxyResponseV2,
    authorization_error,
    internal_error,
    success,
    validation_error,
)

retry_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})
dynamodb = boto3.resource("dynamodb", config=retry_config)
logger = safeLogger(service_name="ComplianceSchemaBindingService")

claims_and_roles = {}

try:
    compliance_table_name = os.environ["COMPLIANCE_ASSET_STATE_STORAGE_TABLE_NAME"]
    schema_table_name = os.environ["COMPLIANCE_SCHEMA_STORAGE_TABLE_NAME"]
    database_table_name = os.environ["DATABASE_STORAGE_TABLE_NAME"]
    asset_table_name = os.environ["ASSET_STORAGE_TABLE_NAME"]
    audit_table_name = os.environ["COMPLIANCE_AUDIT_STORAGE_TABLE_NAME"]
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

compliance_table = dynamodb.Table(compliance_table_name)
schema_table = dynamodb.Table(schema_table_name)
database_table = dynamodb.Table(database_table_name)
asset_table = dynamodb.Table(asset_table_name)
audit_table = dynamodb.Table(audit_table_name)


def lambda_handler(event, context):
    global claims_and_roles
    response = STANDARD_JSON_RESPONSE

    try:
        claims_and_roles = request_to_claims(event)
        if "statusCode" in claims_and_roles:
            return claims_and_roles

        http_method = event["requestContext"]["http"]["method"]
        path = event["requestContext"]["http"]["path"]
        path_params = event.get("pathParameters", {}) or {}
        database_id = path_params.get("databaseId")
        asset_id = path_params.get("assetId")

        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

        if not method_allowed_on_api:
            return authorization_error()

        if http_method == "PUT" and asset_id:
            body = json.loads(event.get("body", "{}"))
            response = bind_schema_to_asset(database_id, asset_id, body)
        elif http_method == "PUT":
            body = json.loads(event.get("body", "{}"))
            response = bind_schema_to_database(database_id, body)
        elif http_method == "DELETE" and asset_id:
            response = unbind_schema_from_asset(database_id, asset_id)
        elif http_method == "DELETE":
            response = unbind_schema_from_database(database_id)
        elif http_method == "GET":
            response = get_bindings(database_id)
        else:
            response["statusCode"] = 405
            response["body"] = json.dumps({"message": "Method not allowed"})

    except Exception as e:
        logger.exception("Unhandled error in Compliance Schema Binding Service")
        response = internal_error(body={"message": str(e)})

    return response


def bind_schema_to_database(database_id, body):
    """Bind a compliance schema to a database.

    All existing assets with schemaSource="database" are marked pending_evaluation.
    Assets with schemaSource="asset" are untouched.
    """
    if not database_id:
        return validation_error(body={"message": "databaseId is required"})

    schema_name = body.get("schemaName")
    if not schema_name:
        return validation_error(body={"message": "schemaName is required"})

    obj = {
        "object__type": "complianceSchema",
        "complianceSchemaName": schema_name,
    }
    if claims_and_roles.get("tokens"):
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if not casbin_enforcer.enforce(obj, "PUT"):
            return authorization_error()

    if not schema_visible_for_database(schema_name, database_id):
        if not schema_exists(schema_name):
            return validation_error(
                body={"message": f"Schema '{schema_name}' not found"}
            )
        return validation_error(
            body={
                "message": (
                    "Schema is not available for this database. "
                    "Only GLOBAL or database-scoped schemas can be assigned."
                )
            }
        )

    db_response = database_table.get_item(Key={"databaseId": database_id})
    if "Item" not in db_response:
        return validation_error(
            body={"message": f"Database '{database_id}' not found"}
        )

    now = datetime.now(timezone.utc).isoformat()
    actor = claims_and_roles.get("sub", "system")
    old_schema = db_response["Item"].get("complianceSchemaName")

    auto_eval = body.get("complianceAutoEval", True)

    database_table.update_item(
        Key={"databaseId": database_id},
        UpdateExpression=(
            "SET complianceSchemaName = :schema,"
            " complianceSchemaUpdatedAt = :now,"
            " complianceAutoEval = :autoEval"
        ),
        ExpressionAttributeValues={
            ":schema": schema_name,
            ":now": now,
            ":autoEval": auto_eval,
        },
    )

    affected = mark_database_assets_pending(database_id, schema_name, now)

    write_audit(
        database_id=database_id,
        asset_id="*",
        event_type="schema_bound_to_database",
        actor=actor,
        schema_name=schema_name,
        details={
            "previousSchema": old_schema,
            "affectedAssets": affected,
        },
    )

    return success(body={
        "message": f"Schema '{schema_name}' bound to database '{database_id}'",
        "schemaName": schema_name,
        "databaseId": database_id,
        "assetsPendingEvaluation": affected,
    })


def unbind_schema_from_database(database_id):
    """Remove the compliance schema binding from a database."""
    if not database_id:
        return validation_error(body={"message": "databaseId is required"})

    obj = {
        "object__type": "complianceSchema",
        "complianceSchemaName": "",
    }
    if claims_and_roles.get("tokens"):
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if not casbin_enforcer.enforce(obj, "DELETE"):
            return authorization_error()

    db_response = database_table.get_item(Key={"databaseId": database_id})
    if "Item" not in db_response:
        return validation_error(
            body={"message": f"Database '{database_id}' not found"}
        )

    old_schema = db_response["Item"].get("complianceSchemaName")
    if not old_schema:
        return success(body={
            "message": "Database has no schema binding to remove",
            "databaseId": database_id,
        })

    now = datetime.now(timezone.utc).isoformat()
    actor = claims_and_roles.get("sub", "system")

    database_table.update_item(
        Key={"databaseId": database_id},
        UpdateExpression=(
            "REMOVE complianceSchemaName, complianceSchemaUpdatedAt,"
            " complianceAutoEval"
        ),
    )

    removed = _remove_database_compliance_records(database_id)

    write_audit(
        database_id=database_id,
        asset_id="*",
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


def bind_schema_to_asset(database_id, asset_id, body):
    """Bind a compliance schema directly to an asset (overrides database binding)."""
    if not database_id or not asset_id:
        return validation_error(
            body={"message": "databaseId and assetId are required"}
        )

    schema_name = body.get("schemaName")
    if not schema_name:
        return validation_error(body={"message": "schemaName is required"})

    obj = {
        "object__type": "complianceSchema",
        "complianceSchemaName": schema_name,
    }
    if claims_and_roles.get("tokens"):
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if not casbin_enforcer.enforce(obj, "PUT"):
            return authorization_error()

    if not schema_visible_for_database(schema_name, database_id):
        if not schema_exists(schema_name):
            return validation_error(
                body={"message": f"Schema '{schema_name}' not found"}
            )
        return validation_error(
            body={
                "message": (
                    "Schema is not available for this database. "
                    "Only GLOBAL or database-scoped schemas can be assigned."
                )
            }
        )

    now = datetime.now(timezone.utc).isoformat()
    actor = claims_and_roles.get("sub", "system")

    existing = compliance_table.get_item(
        Key={"databaseId": database_id, "assetId": asset_id}
    ).get("Item")

    old_schema = existing.get("schemaName") if existing else None
    old_source = existing.get("schemaSource") if existing else None

    compliance_table.update_item(
        Key={"databaseId": database_id, "assetId": asset_id},
        UpdateExpression=(
            "SET schemaName = :schema, "
            "schemaSource = :source, "
            "complianceState = :state, "
            "updatedAt = :now"
        ),
        ExpressionAttributeValues={
            ":schema": schema_name,
            ":source": "asset",
            ":state": "pending_evaluation",
            ":now": now,
        },
    )

    write_audit(
        database_id=database_id,
        asset_id=asset_id,
        event_type="schema_bound_to_asset",
        actor=actor,
        schema_name=schema_name,
        details={
            "previousSchema": old_schema,
            "previousSource": old_source,
        },
    )

    return success(body={
        "message": f"Schema '{schema_name}' bound to asset",
        "databaseId": database_id,
        "assetId": asset_id,
        "schemaName": schema_name,
        "schemaSource": "asset",
    })


def unbind_schema_from_asset(database_id, asset_id):
    """Remove asset-level schema override, falling back to database binding."""
    if not database_id or not asset_id:
        return validation_error(
            body={"message": "databaseId and assetId are required"}
        )

    obj = {
        "object__type": "complianceSchema",
        "complianceSchemaName": "",
    }
    if claims_and_roles.get("tokens"):
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if not casbin_enforcer.enforce(obj, "DELETE"):
            return authorization_error()

    existing = compliance_table.get_item(
        Key={"databaseId": database_id, "assetId": asset_id}
    ).get("Item")

    if not existing or existing.get("schemaSource") != "asset":
        return success(body={
            "message": "Asset has no explicit schema override to remove",
            "databaseId": database_id,
            "assetId": asset_id,
        })

    now = datetime.now(timezone.utc).isoformat()
    actor = claims_and_roles.get("sub", "system")
    old_schema = existing.get("schemaName")

    db_response = database_table.get_item(Key={"databaseId": database_id})
    db_schema = db_response.get("Item", {}).get("complianceSchemaName")

    if db_schema:
        compliance_table.update_item(
            Key={"databaseId": database_id, "assetId": asset_id},
            UpdateExpression=(
                "SET schemaName = :schema, "
                "schemaSource = :source, "
                "complianceState = :state, "
                "updatedAt = :now"
            ),
            ExpressionAttributeValues={
                ":schema": db_schema,
                ":source": "database",
                ":state": "pending_evaluation",
                ":now": now,
            },
        )
    else:
        compliance_table.delete_item(
            Key={"databaseId": database_id, "assetId": asset_id}
        )

    write_audit(
        database_id=database_id,
        asset_id=asset_id,
        event_type="schema_unbound_from_asset",
        actor=actor,
        schema_name=old_schema,
        details={"fallbackSchema": db_schema},
    )

    return success(body={
        "message": "Asset schema override removed",
        "databaseId": database_id,
        "assetId": asset_id,
        "fallbackSchema": db_schema,
        "schemaSource": "database" if db_schema else None,
    })


def get_bindings(database_id):
    """Get the schema binding for a database and any asset overrides."""
    if not database_id:
        return validation_error(body={"message": "databaseId is required"})

    obj = {
        "object__type": "complianceSchema",
        "complianceSchemaName": "",
    }
    if claims_and_roles.get("tokens"):
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if not casbin_enforcer.enforce(obj, "GET"):
            return authorization_error()

    db_response = database_table.get_item(Key={"databaseId": database_id})
    db_item = db_response.get("Item")
    if not db_item:
        return validation_error(
            body={"message": f"Database '{database_id}' not found"}
        )

    db_schema = db_item.get("complianceSchemaName")
    auto_eval = db_item.get("complianceAutoEval", False)

    asset_overrides = []
    response = compliance_table.query(
        KeyConditionExpression=Key("databaseId").eq(database_id),
    )
    for item in response.get("Items", []):
        if item.get("schemaSource") == "asset":
            asset_overrides.append({
                "assetId": item["assetId"],
                "schemaName": item.get("schemaName"),
                "complianceState": item.get("complianceState"),
            })

    return success(body={
        "databaseId": database_id,
        "databaseSchema": db_schema,
        "complianceAutoEval": auto_eval,
        "assetOverrides": asset_overrides,
        "assetOverrideCount": len(asset_overrides),
    })


def mark_database_assets_pending(database_id, schema_name, now):
    """Create or update compliance records for all assets in the database.

    - Assets already tracked with schemaSource="asset" are left untouched.
    - Assets already tracked with schemaSource="database" are updated.
    - Assets not yet in the compliance table get a new record created.

    Returns the count of affected assets.
    """
    existing_compliance = compliance_table.query(
        KeyConditionExpression=Key("databaseId").eq(database_id),
    )
    existing_items = {
        item["assetId"]: item for item in existing_compliance.get("Items", [])
    }

    all_asset_ids = []
    query_kwargs = {
        "KeyConditionExpression": Key("databaseId").eq(database_id),
        "ProjectionExpression": "assetId",
    }
    while True:
        response = asset_table.query(**query_kwargs)
        all_asset_ids.extend(item["assetId"] for item in response.get("Items", []))
        if "LastEvaluatedKey" not in response:
            break
        query_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]

    affected = 0
    for asset_id in all_asset_ids:
        existing = existing_items.get(asset_id)

        if existing and existing.get("schemaSource") == "asset":
            continue

        compliance_table.put_item(
            Item={
                "databaseId": database_id,
                "assetId": asset_id,
                "schemaName": schema_name,
                "schemaSource": "database",
                "complianceState": "pending_evaluation",
                "updatedAt": now,
            }
        )
        affected += 1

    return affected


def _remove_database_compliance_records(database_id):
    """Remove compliance records with schemaSource='database' for a given database.

    Records with schemaSource='asset' (explicit overrides) are left untouched.
    Returns the count of removed records.
    """
    response = compliance_table.query(
        KeyConditionExpression=Key("databaseId").eq(database_id),
    )
    items = response.get("Items", [])

    removed = 0
    for item in items:
        if item.get("schemaSource") == "asset":
            continue
        compliance_table.delete_item(
            Key={"databaseId": database_id, "assetId": item["assetId"]}
        )
        removed += 1

    return removed


GLOBAL_DATABASE_ID = "GLOBAL"


def schema_exists(schema_name):
    """Check if a schema exists in the schema registry."""
    response = schema_table.query(
        KeyConditionExpression=Key("schemaName").eq(schema_name),
        Limit=1,
    )
    return len(response.get("Items", [])) > 0


def schema_visible_for_database(schema_name, database_id):
    """Check if a schema is visible to a database.

    A schema is visible if it is GLOBAL or scoped to the specific database.
    """
    response = schema_table.query(
        KeyConditionExpression=Key("schemaName").eq(schema_name),
        ScanIndexForward=False,
        Limit=1,
    )
    items = response.get("Items", [])
    if not items:
        return False
    schema_db_id = items[0].get("databaseId", GLOBAL_DATABASE_ID)
    return schema_db_id in (GLOBAL_DATABASE_ID, database_id)


def write_audit(database_id, asset_id, event_type, actor, schema_name=None,
                details=None):
    """Write an audit log entry."""
    now = datetime.now(timezone.utc).isoformat()
    audit_table.put_item(
        Item={
            "entryId": str(uuid.uuid4()),
            "databaseId:assetId": f"{database_id}:{asset_id}",
            "timestamp": now,
            "eventType": event_type,
            "databaseId": database_id,
            "assetId": asset_id,
            "actor": actor,
            "schemaName": schema_name,
            "details": json.dumps(details or {}),
        }
    )
