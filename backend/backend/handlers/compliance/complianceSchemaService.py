#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Compliance Schema Service handler.

- GET    /compliance/schemas               — list schemas (latest version of each)
- POST   /compliance/schemas               — register a schema (a new version when the name exists)
- GET    /compliance/schemas/{schemaName}  — get a schema (latest version)
- PUT    /compliance/schemas/{schemaName}  — update a schema (writes a new version)
- DELETE /compliance/schemas/{schemaName}  — delete an unbound schema (every version)

Schema table (PK schemaName, SK internalVersion; GSI DatabaseIdIndex on databaseId/schemaName).
Every schema record the GET routes return carries a top-level `schemaFormat` (`vams-rules-v1` |
`legacy`) derived from the stored body.
"""

import json
import uuid
from datetime import datetime, timezone

import boto3
from aws_lambda_powertools.utilities.parser import ValidationError, parse
from aws_lambda_powertools.utilities.typing import LambdaContext
from boto3.dynamodb.conditions import Attr, Key
from botocore.config import Config

from common.apiRoutes import API_COMPLIANCE_SCHEMA_BY_NAME, API_COMPLIANCE_SCHEMAS
from common.compliance import evaluationEngine as engine
from common.dynamodb import query_all_items, query_has_match
from common.resourceNames import ResourceKeys, get_table_name
from common.validators import validate
from customLogging.logger import safeLogger
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
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
from models.compliance import (
    GLOBAL_DATABASE_ID,
    RULE_TYPE_MODELS,
    CreateSchemaRequestModel,
    UpdateSchemaRequestModel,
    VamsRulesV1Schema,
)

retry_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})
dynamodb = boto3.resource("dynamodb", config=retry_config)
logger = safeLogger(service_name="ComplianceSchemaService")

claims_and_roles = {}

COMPLIANCE_SCHEMA_OBJECT_TYPE = "complianceSchema"
# The system actor: the only identity that registers or modifies system schemas.
SYSTEM_USER = "SYSTEM_USER"

try:
    schema_table_name = get_table_name(ResourceKeys.COMPLIANCE_SCHEMA_STORAGE_TABLE)
    asset_state_table_name = get_table_name(ResourceKeys.COMPLIANCE_ASSET_STATE_STORAGE_TABLE)
    audit_table_name = get_table_name(ResourceKeys.COMPLIANCE_AUDIT_STORAGE_TABLE)
    database_table_name = get_table_name(ResourceKeys.DATABASE_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed loading resource names")
    raise e

schema_table = dynamodb.Table(schema_table_name)
asset_state_table = dynamodb.Table(asset_state_table_name)
audit_table = dynamodb.Table(audit_table_name)
database_table = dynamodb.Table(database_table_name)


#######################
# Schema body validation
#######################

# The one client-visible message for a body that is not a vams-rules-v1 document; the specifics
# (missing format marker, missing rules, model errors) are logged.
NOT_VAMS_RULES_MESSAGE = "schemaBody must be a vams-rules-v1 document"


def validate_schema_body(schema_body):
    """Validate a schema body. Only the `vams-rules-v1` format (`schemaFormat: "vams-rules-v1"` plus
    a `rules` object of pipeline, metadata and relationship rules) is accepted.

    Returns (True, None) when valid, (False, error_message) otherwise. A body that does not declare
    the format is refused with NOT_VAMS_RULES_MESSAGE; a declared body whose rules fail their models
    is refused with the model's caller-safe message.
    """
    if not isinstance(schema_body, dict):
        logger.info("Schema body rejected: not a JSON object")
        return False, NOT_VAMS_RULES_MESSAGE
    if schema_body.get("schemaFormat") != engine.VAMS_RULES_V1:
        logger.info(f"Schema body rejected: schemaFormat is not {engine.VAMS_RULES_V1}")
        return False, NOT_VAMS_RULES_MESSAGE
    return _validate_vams_rules_v1(schema_body)


def _validate_vams_rules_v1(schema_body):
    """Validate a vams-rules-v1 body through its Pydantic models.

    The message returned names fields by their published names only (via
    `validation_error_message`) and locates a failing rule by its position, never by the
    caller-supplied rule name; the full pydantic error is logged.
    """
    try:
        schema = VamsRulesV1Schema(**schema_body)
    except ValidationError as v:
        logger.warning(f"vams-rules-v1 body rejected: {v}")
        return False, validation_error_message(v)
    except (ValueError, TypeError) as e:
        logger.warning(f"vams-rules-v1 body rejected: {e}")
        return False, NOT_VAMS_RULES_MESSAGE

    for position, (rule_name, rule_def) in enumerate(schema.rules.items()):
        try:
            RULE_TYPE_MODELS[rule_def["ruleType"]](**rule_def)
        except ValidationError as v:
            logger.warning(f"vams-rules-v1 rule '{rule_name}' at position {position} rejected: {v}")
            return False, f"rules[{position}]: {validation_error_message(v)}"
        except (ValueError, TypeError) as e:
            logger.warning(f"vams-rules-v1 rule '{rule_name}' at position {position} rejected: {e}")
            return False, f"rules[{position}]: rule definition is not valid"
    return True, None


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
        elif method == "POST":
            return handle_post_request(event)
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
    query_params = event.get("queryStringParameters", {}) or {}

    if API_COMPLIANCE_SCHEMA_BY_NAME.matches(path):
        return get_schema(event, path_params.get("schemaName"))
    if API_COMPLIANCE_SCHEMAS.matches(path):
        return list_schemas(event, query_params.get("databaseId"))
    return validation_error(body={"message": "Method not allowed"}, event=event)


def handle_post_request(event):
    path = event["requestContext"]["http"]["path"]
    if API_COMPLIANCE_SCHEMAS.matches(path):
        body = _parse_body(event)
        return register_schema(event, parse(body, model=CreateSchemaRequestModel))
    return validation_error(body={"message": "Method not allowed"}, event=event)


def handle_put_request(event):
    path = event["requestContext"]["http"]["path"]
    path_params = event.get("pathParameters", {}) or {}
    if API_COMPLIANCE_SCHEMA_BY_NAME.matches(path):
        body = _parse_body(event)
        return update_schema(event, path_params.get("schemaName"),
                             parse(body, model=UpdateSchemaRequestModel))
    return validation_error(body={"message": "Method not allowed"}, event=event)


def handle_delete_request(event):
    path = event["requestContext"]["http"]["path"]
    path_params = event.get("pathParameters", {}) or {}
    if API_COMPLIANCE_SCHEMA_BY_NAME.matches(path):
        return delete_schema(event, path_params.get("schemaName"))
    return validation_error(body={"message": "Method not allowed"}, event=event)


def _parse_body(event):
    """The JSON request body as a dict; a body that is not JSON raises a validation error."""
    raw = event.get("body") or "{}"
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise VAMSGeneralErrorResponse("Invalid JSON in request body")


#######################
# Business logic
#######################

def normalize_schema_item(item):
    """A schema row in its API response shape.

    `schemaFormat` is derived from the stored body: `vams-rules-v1` when the body declares that
    format, `legacy` for a row written before the format existed (a JSON-Schema body). A legacy
    schema cannot be evaluated, updated or bound — it is listed so an operator can find and delete
    it.
    """
    schema_body = item.get("schemaBody", "{}")
    if isinstance(schema_body, str):
        try:
            schema_body = json.loads(schema_body)
        except (json.JSONDecodeError, TypeError):
            pass
    return {
        "schemaName": item.get("schemaName"),
        "databaseId": item.get("databaseId", GLOBAL_DATABASE_ID),
        "description": item.get("description", ""),
        "schemaFormat": engine.schema_format(schema_body),
        "schemaBody": schema_body,
        "version": int(item.get("internalVersion", 1)),
        "createdAt": item.get("registeredAt"),
        "isSystem": bool(item.get("isSystem", False)),
    }


def _schema_object(schema_name):
    """The Tier-2 authorization object for a schema."""
    return {
        "object__type": COMPLIANCE_SCHEMA_OBJECT_TYPE,
        "complianceSchemaName": schema_name or "",
    }


def _enforce(schema_name, action):
    """Tier-2 check on a schema. Fails closed on an empty token list."""
    if len(claims_and_roles["tokens"]) == 0:
        return False
    return CasbinEnforcer(claims_and_roles).enforce(_schema_object(schema_name), action)


def list_schemas(event, database_id_filter=None):
    """List the latest version of every schema, filtered to those the caller may GET.

    With `databaseId`, only GLOBAL schemas and those scoped to that database are listed
    (DatabaseIdIndex GSI); without it, every partition of the GSI is read.
    """
    if database_id_filter:
        (valid, message) = validate({
            "databaseId": {
                "value": database_id_filter, "validator": "ID", "allowGlobalKeyword": True,
            },
        })
        if not valid:
            return validation_error(body={"message": message}, event=event)
        database_ids = [GLOBAL_DATABASE_ID]
        if database_id_filter != GLOBAL_DATABASE_ID:
            database_ids.append(database_id_filter)
        items = _query_schemas_for_databases(database_ids)
    else:
        items = _query_all_schemas()

    schemas_by_name = {}
    for item in items:
        name = item["schemaName"]
        version = int(item.get("internalVersion", 1))
        if name not in schemas_by_name or version > schemas_by_name[name]["version"]:
            schemas_by_name[name] = normalize_schema_item(item)

    casbin_enforcer = CasbinEnforcer(claims_and_roles) if len(claims_and_roles["tokens"]) > 0 else None
    allowed = []
    for schema in schemas_by_name.values():
        # List filtering appends only when enforce() passes, so empty tokens yield an empty list.
        if casbin_enforcer and casbin_enforcer.enforce(_schema_object(schema["schemaName"]), "GET"):
            allowed.append(schema)

    return success(body={"schemas": allowed})


def _query_schemas_for_databases(database_ids):
    """Every schema row whose databaseId is one of `database_ids` (DatabaseIdIndex, paged)."""
    items = []
    for database_id in database_ids:
        items.extend(query_all_items(
            schema_table,
            IndexName="DatabaseIdIndex",
            KeyConditionExpression=Key("databaseId").eq(database_id),
        ))
    return items


def _query_all_schemas():
    """Every schema row. The schema table has no constant-partition index, so the complete
    unfiltered listing is a scan paged to exhaustion (schemas are administrator-defined and few)."""
    return _scan_all_items(schema_table)


def _scan_all_items(table, **scan_kwargs):
    """Scan a table to exhaustion, following LastEvaluatedKey by presence."""
    items = []
    while True:
        response = table.scan(**scan_kwargs)
        items.extend(response.get("Items", []))
        if "LastEvaluatedKey" not in response:
            return items
        scan_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def get_schema(event, schema_name):
    """The latest version of one schema."""
    (valid, message) = validate({"schemaName": {"value": schema_name, "validator": "ID"}})
    if not valid:
        return validation_error(body={"message": message}, event=event)

    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error()
    if not _enforce(schema_name, "GET"):
        return authorization_error()

    item = _latest_schema_item(schema_name)
    if not item:
        return general_error(body={"message": "Schema not found"}, event=event)
    return success(body=normalize_schema_item(item))


def _latest_schema_item(schema_name):
    """The highest internalVersion row of a schema, or None."""
    response = schema_table.query(
        KeyConditionExpression=Key("schemaName").eq(schema_name),
        ScanIndexForward=False,
        Limit=1,
    )
    items = response.get("Items", [])
    return items[0] if items else None


def register_schema(event, request: CreateSchemaRequestModel):
    """Register a schema. A name that already exists gains a new version."""
    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error()
    if not _enforce(request.schemaName, "POST"):
        return authorization_error()

    if request.databaseId != GLOBAL_DATABASE_ID and not _database_exists(request.databaseId):
        return general_error(body={"message": "Database not found"}, event=event)

    valid, err = validate_schema_body(request.schemaBody)
    if not valid:
        return _schema_body_rejected(event, err)

    # Only the system actor registers system schemas (which no other caller can later modify).
    is_system = bool(request.isSystem) and claims_and_roles["tokens"][0] == SYSTEM_USER
    return _write_schema_version(event, request.schemaName, request.databaseId,
                                 request.description or "", request.schemaBody, is_system)


def _schema_body_rejected(event, err):
    """The 400 for a schema body `validate_schema_body` refused: the generic format message as is,
    a model message prefixed as an invalid schema."""
    logger.info(f"Schema body rejected: {err}")
    message = err if err == NOT_VAMS_RULES_MESSAGE else f"Invalid schema: {err}"
    return validation_error(body={"message": message}, event=event)


def _write_schema_version(event, schema_name, database_id, description, schema_body, is_system):
    """Write the next internalVersion row of a schema."""
    current = _latest_schema_item(schema_name)
    next_version = int(current["internalVersion"]) + 1 if current else 1
    now = datetime.now(timezone.utc).isoformat()

    schema_table.put_item(Item={
        "schemaName": schema_name,
        "internalVersion": next_version,
        "databaseId": database_id,
        "description": description,
        "schemaBody": json.dumps(schema_body),
        "registeredAt": now,
        "registeredBy": claims_and_roles["tokens"][0],
        "isSystem": is_system,
    })

    logger.info(f"Registered schema '{schema_name}' v{next_version}")
    return success(body={
        "message": f"Schema '{schema_name}' registered as v{next_version}",
        "schemaName": schema_name,
        "databaseId": database_id,
        "internalVersion": next_version,
    })


def update_schema(event, schema_name, request: UpdateSchemaRequestModel):
    """Update a schema by writing a new version. A system schema is only updated by SYSTEM_USER."""
    (valid, message) = validate({"schemaName": {"value": schema_name, "validator": "ID"}})
    if not valid:
        return validation_error(body={"message": message}, event=event)

    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error()
    if not _enforce(schema_name, "PUT"):
        return authorization_error()

    current = _latest_schema_item(schema_name)
    if not current:
        return general_error(body={"message": "Schema not found"}, event=event)

    if current.get("isSystem") and claims_and_roles["tokens"][0] != SYSTEM_USER:
        return general_error(body={"message": "System schemas cannot be modified"}, event=event)

    database_id = request.databaseId or current.get("databaseId", GLOBAL_DATABASE_ID)
    if database_id != GLOBAL_DATABASE_ID and not _database_exists(database_id):
        return general_error(body={"message": "Database not found"}, event=event)

    if request.schemaBody is not None:
        schema_body = request.schemaBody
    else:
        schema_body = normalize_schema_item(current)["schemaBody"]
    valid, err = validate_schema_body(schema_body)
    if not valid:
        return _schema_body_rejected(event, err)

    description = request.description if request.description is not None else current.get("description", "")
    return _write_schema_version(event, schema_name, database_id, description, schema_body,
                                 bool(current.get("isSystem", False)))


def delete_schema(event, schema_name):
    """Delete every version of a schema that no database or asset is bound to."""
    (valid, message) = validate({"schemaName": {"value": schema_name, "validator": "ID"}})
    if not valid:
        return validation_error(body={"message": message}, event=event)

    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error()
    if not _enforce(schema_name, "DELETE"):
        return authorization_error()

    rows = query_all_items(
        schema_table,
        KeyConditionExpression=Key("schemaName").eq(schema_name),
        ProjectionExpression="schemaName, internalVersion",
    )
    if not rows:
        return general_error(body={"message": "Schema not found"}, event=event)

    current = _latest_schema_item(schema_name) or {}
    if current.get("isSystem") and claims_and_roles["tokens"][0] != SYSTEM_USER:
        return general_error(body={"message": "System schemas cannot be deleted"}, event=event)

    if _schema_is_bound(schema_name):
        logger.info(f"Schema '{schema_name}' is still bound; delete refused")
        return general_error(
            body={"message": "Schema is bound to a database or asset and cannot be deleted"},
            event=event)

    with schema_table.batch_writer() as batch:
        for row in rows:
            batch.delete_item(Key={
                "schemaName": row["schemaName"], "internalVersion": row["internalVersion"],
            })

    audit_table.put_item(Item={
        "entryId": str(uuid.uuid4()),
        "databaseId:assetId": "*:*",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "eventType": "schema_deleted",
        "databaseId": "*",
        "assetId": "*",
        "actor": claims_and_roles["tokens"][0],
        "schemaName": schema_name,
        "details": json.dumps({"versionsDeleted": len(rows)}),
    })

    logger.info(f"Deleted schema '{schema_name}' ({len(rows)} versions)")
    return success(body={
        "message": "Schema deleted",
        "schemaName": schema_name,
        "versionsDeleted": len(rows),
    })


def _schema_is_bound(schema_name):
    """Whether any asset-state row (SchemaNameIndex) or any database row references the schema.

    A database binding is the `complianceSchemaName` attribute on the database row, which the
    database table carries no index on, so that half is a scan paged to exhaustion and filtered
    to the attribute (empty `Items` with a `LastEvaluatedKey` means "keep paging", not "unbound").
    """
    if query_has_match(
        asset_state_table,
        IndexName="SchemaNameIndex",
        KeyConditionExpression=Key("schemaName").eq(schema_name),
    ):
        return True
    scan_kwargs = {
        "FilterExpression": Attr("complianceSchemaName").eq(schema_name),
        "ProjectionExpression": "databaseId",
    }
    while True:
        response = database_table.scan(**scan_kwargs)
        if response.get("Items"):
            return True
        if "LastEvaluatedKey" not in response:
            return False
        scan_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def _database_exists(database_id):
    """Whether a database row exists."""
    return "Item" in database_table.get_item(Key={"databaseId": database_id})
