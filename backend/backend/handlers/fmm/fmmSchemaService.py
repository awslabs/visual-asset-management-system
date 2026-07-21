"""FMM Schema Service handler.

Manages compliance schema CRUD operations:
- GET /compliance/schemas — List all schemas
- GET /compliance/schemas/{schemaName} — Get schema by name
- POST /compliance/schemas — Register a new schema
- PUT /compliance/schemas/{schemaName} — Update a schema
"""

import json
import os
import uuid
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config

from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
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
from models.fmm import VamsRulesV1Schema

retry_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})
dynamodb = boto3.resource("dynamodb", config=retry_config)
logger = safeLogger(service_name="FMMSchemaService")

VALID_JSON_SCHEMA_TYPES = {"string", "number", "integer", "boolean", "array", "object", "null"}


def validate_schema_body(schema_body):
    """Validate schema_body. Supports two formats:

    1. vams-rules-v1: Structured rules format with pipeline, metadata,
       and relationship rule types.
    2. Legacy JSON Schema (draft-07 subset): Freeform schema validation.

    Returns (True, None) if valid, (False, error_message) if invalid.
    """
    if not isinstance(schema_body, dict):
        return False, "schemaBody must be a JSON object"

    if schema_body.get("schemaFormat") == "vams-rules-v1":
        return _validate_vams_rules_v1(schema_body)

    return _validate_json_schema(schema_body)


def _validate_vams_rules_v1(schema_body):
    """Validate a vams-rules-v1 schema body using Pydantic models."""
    try:
        schema = VamsRulesV1Schema(**schema_body)
        schema.parse_rules()
        return True, None
    except (ValueError, TypeError) as e:
        return False, str(e)


def _validate_json_schema(schema_body):
    """Validate a legacy JSON Schema (draft-07 subset)."""
    schema_type = schema_body.get("type")
    if schema_type is not None:
        if isinstance(schema_type, list):
            for t in schema_type:
                if t not in VALID_JSON_SCHEMA_TYPES:
                    return False, f"Invalid type '{t}' in type array"
        elif schema_type not in VALID_JSON_SCHEMA_TYPES:
            return False, f"Invalid type '{schema_type}'"

    properties = schema_body.get("properties")
    if properties is not None:
        if not isinstance(properties, dict):
            return False, "'properties' must be an object"
        for prop_name, prop_def in properties.items():
            if not isinstance(prop_def, dict):
                return False, f"Property '{prop_name}' definition must be an object"
            prop_type = prop_def.get("type")
            if prop_type is not None:
                if isinstance(prop_type, list):
                    for t in prop_type:
                        if t not in VALID_JSON_SCHEMA_TYPES:
                            return False, f"Property '{prop_name}' has invalid type '{t}'"
                elif prop_type not in VALID_JSON_SCHEMA_TYPES:
                    return False, f"Property '{prop_name}' has invalid type '{prop_type}'"
            if "enum" in prop_def and not isinstance(prop_def["enum"], list):
                return False, f"Property '{prop_name}' enum must be an array"
            if "minimum" in prop_def and not isinstance(prop_def["minimum"], (int, float)):
                return False, f"Property '{prop_name}' minimum must be a number"
            if "maximum" in prop_def and not isinstance(prop_def["maximum"], (int, float)):
                return False, f"Property '{prop_name}' maximum must be a number"

    required = schema_body.get("required")
    if required is not None:
        if not isinstance(required, list):
            return False, "'required' must be an array"
        for item in required:
            if not isinstance(item, str):
                return False, "'required' array must contain only strings"
        if properties is not None:
            for req_field in required:
                if req_field not in properties:
                    return False, f"Required field '{req_field}' not defined in properties"

    if "items" in schema_body:
        items = schema_body["items"]
        if isinstance(items, dict):
            valid, err = _validate_json_schema(items)
            if not valid:
                return False, f"In 'items': {err}"

    additional = schema_body.get("additionalProperties")
    if additional is not None and not isinstance(additional, (bool, dict)):
        return False, "'additionalProperties' must be a boolean or object"

    return True, None

claims_and_roles = {}

try:
    schema_table_name = os.environ["FMM_SCHEMA_STORAGE_TABLE_NAME"]
    compliance_table_name = os.environ["FMM_ASSET_COMPLIANCE_STORAGE_TABLE_NAME"]
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

schema_table = dynamodb.Table(schema_table_name)
compliance_table = dynamodb.Table(compliance_table_name)


def lambda_handler(event, context):
    global claims_and_roles
    response = STANDARD_JSON_RESPONSE

    try:
        http_method = event["requestContext"]["http"]["method"]
        path_params = event.get("pathParameters", {}) or {}
        schema_name = path_params.get("schemaName")

        claims_and_roles = request_to_claims(event)
        if "statusCode" in claims_and_roles:
            return claims_and_roles

        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

        if not method_allowed_on_api:
            return authorization_error()

        if http_method == "GET" and schema_name:
            response = get_schema(schema_name)
        elif http_method == "GET":
            response = list_schemas()
        elif http_method == "POST":
            body = json.loads(event.get("body", "{}"))
            response = register_schema(body)
        elif http_method == "PUT" and schema_name:
            body = json.loads(event.get("body", "{}"))
            response = update_schema(schema_name, body)
        else:
            response["statusCode"] = 405
            response["body"] = json.dumps({"message": "Method not allowed"})

    except Exception as e:
        logger.exception("Unhandled error in FMM Schema Service")
        response = internal_error(body={"message": str(e)})

    return response


def normalize_schema_item(item):
    """Normalize a DynamoDB schema item for API response."""
    schema_body = item.get("schemaBody", "{}")
    if isinstance(schema_body, str):
        try:
            schema_body = json.loads(schema_body)
        except (json.JSONDecodeError, TypeError):
            pass
    return {
        "schemaName": item.get("schemaName"),
        "description": item.get("description", ""),
        "schemaBody": schema_body,
        "version": int(item.get("internalVersion", 1)),
        "createdAt": item.get("registeredAt"),
    }


def list_schemas():
    """List all schemas (latest version of each)."""
    try:
        response = schema_table.scan()
        items = response.get("Items", [])

        casbin_enforcer = CasbinEnforcer(claims_and_roles) if claims_and_roles.get("tokens") else None

        schemas_by_name = {}
        for item in items:
            name = item["schemaName"]
            version = int(item.get("internalVersion", 1))
            if name not in schemas_by_name or version > schemas_by_name[name]["version"]:
                schemas_by_name[name] = normalize_schema_item(item)

        filtered = []
        for schema in schemas_by_name.values():
            obj = {
                "object__type": "complianceSchema",
                "complianceSchemaName": schema.get("schemaName", ""),
            }
            if casbin_enforcer and not casbin_enforcer.enforce(obj, "GET"):
                continue
            filtered.append(schema)

        return success(body={"schemas": filtered})
    except Exception as e:
        logger.exception("Error listing schemas")
        return internal_error(body={"message": str(e)})


def get_schema(schema_name):
    """Get a schema by name (latest version or specific version)."""
    try:
        response = schema_table.query(
            KeyConditionExpression=Key("schemaName").eq(schema_name),
            ScanIndexForward=False,
            Limit=1,
        )
        items = response.get("Items", [])
        if not items:
            return {
                "statusCode": 404,
                "body": json.dumps({"message": f"Schema '{schema_name}' not found"}),
                "headers": {"Content-Type": "application/json"},
            }

        obj = {
            "object__type": "complianceSchema",
            "complianceSchemaName": schema_name,
        }
        if claims_and_roles.get("tokens"):
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not casbin_enforcer.enforce(obj, "GET"):
                return authorization_error()

        return success(body=normalize_schema_item(items[0]))
    except Exception as e:
        logger.exception("Error getting schema")
        return internal_error(body={"message": str(e)})


def register_schema(body):
    """Register a new compliance schema."""
    schema_name = body.get("schemaName") or body.get("name")
    if not schema_name:
        return validation_error(body={"message": "Missing required field: schemaName"})

    obj = {
        "object__type": "complianceSchema",
        "complianceSchemaName": schema_name,
    }
    if claims_and_roles.get("tokens"):
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if not casbin_enforcer.enforce(obj, "POST"):
            return authorization_error()

    schema_body = body.get("schemaBody") or body.get("rules")
    if not schema_body:
        return validation_error(body={"message": "Missing required field: schemaBody"})

    if isinstance(schema_body, str):
        try:
            schema_body = json.loads(schema_body)
        except (json.JSONDecodeError, TypeError):
            return validation_error(body={"message": "schemaBody must be valid JSON"})

    valid, err = validate_schema_body(schema_body)
    if not valid:
        return validation_error(body={"message": f"Invalid schema: {err}"})

    now = datetime.now(timezone.utc).isoformat()

    existing = schema_table.query(
        KeyConditionExpression=Key("schemaName").eq(schema_name),
        ScanIndexForward=False,
        Limit=1,
    )
    existing_items = existing.get("Items", [])
    next_version = 1
    if existing_items:
        next_version = int(existing_items[0]["internalVersion"]) + 1

    item = {
        "schemaName": schema_name,
        "internalVersion": next_version,
        "description": body.get("description", ""),
        "schemaBody": json.dumps(schema_body) if isinstance(schema_body, dict) else schema_body,
        "registeredAt": now,
        "registeredBy": claims_and_roles.get("sub", "system"),
        "isSystem": body.get("isSystem", False),
    }

    schema_table.put_item(Item=item)

    logger.info(f"Registered schema '{schema_name}' v{next_version}")
    return success(body={
        "message": f"Schema '{schema_name}' registered as v{next_version}",
        "schemaName": schema_name,
        "internalVersion": next_version,
    })


def update_schema(schema_name, body):
    """Update a schema (creates a new version).

    System schemas (isSystem=True) cannot be modified unless the caller
    registered them (i.e., is the system actor).
    """
    obj = {
        "object__type": "complianceSchema",
        "complianceSchemaName": schema_name,
    }
    if claims_and_roles.get("tokens"):
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if not casbin_enforcer.enforce(obj, "PUT"):
            return authorization_error()

    existing = schema_table.query(
        KeyConditionExpression=Key("schemaName").eq(schema_name),
        ScanIndexForward=False,
        Limit=1,
    )
    existing_items = existing.get("Items", [])
    if not existing_items:
        return validation_error(body={"message": f"Schema '{schema_name}' not found"})

    current = existing_items[0]
    if current.get("isSystem") and current.get("registeredBy") == "system":
        caller = claims_and_roles.get("sub", "")
        if caller != "system":
            return validation_error(
                body={"message": f"Schema '{schema_name}' is a system schema and cannot be modified"}
            )

    body["schemaName"] = schema_name
    return register_schema(body)
