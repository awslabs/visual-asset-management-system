"""FMM Audit Service handler.

Provides audit log query endpoints:
- GET /compliance/audit/{databaseId}/{assetId} — Asset audit history
- GET /compliance/audit — Query audit log with filters
"""

import json
import os

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
logger = safeLogger(service_name="FMMAuditService")

claims_and_roles = {}

try:
    audit_table_name = os.environ["FMM_AUDIT_STORAGE_TABLE_NAME"]
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

audit_table = dynamodb.Table(audit_table_name)


def lambda_handler(event, context):
    global claims_and_roles
    response = STANDARD_JSON_RESPONSE

    try:
        http_method = event["requestContext"]["http"]["method"]
        path_params = event.get("pathParameters", {}) or {}
        query_params = event.get("queryStringParameters", {}) or {}
        database_id = path_params.get("databaseId")
        asset_id = path_params.get("assetId")

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

        if http_method == "GET" and database_id and asset_id:
            response = get_asset_audit(database_id, asset_id, query_params)
        elif http_method == "GET":
            response = query_audit(query_params)
        else:
            response["statusCode"] = 405
            response["body"] = json.dumps({"message": "Method not allowed"})

    except Exception as e:
        logger.exception("Unhandled error in FMM Audit Service")
        response = internal_error(body={"message": str(e)})

    return response


def get_asset_audit(database_id, asset_id, params):
    """Get audit history for a specific asset.

    Supports optional startDate/endDate query params (ISO 8601).
    """
    limit = int(params.get("limit", "50"))
    start_date = params.get("startDate")
    end_date = params.get("endDate")
    asset_key = f"{database_id}:{asset_id}"

    key_condition = Key("databaseId:assetId").eq(asset_key)
    if start_date and end_date:
        key_condition = key_condition & Key("timestamp").between(
            start_date, end_date
        )
    elif start_date:
        key_condition = key_condition & Key("timestamp").gte(start_date)
    elif end_date:
        key_condition = key_condition & Key("timestamp").lte(end_date)

    response = audit_table.query(
        IndexName="AssetIndex",
        KeyConditionExpression=key_condition,
        ScanIndexForward=False,
        Limit=limit,
    )

    return success(body={"entries": response.get("Items", [])})


def query_audit(params):
    """Query audit log with optional filters.

    Supports eventType, startDate, endDate, and limit query params.
    """
    event_type = params.get("eventType")
    limit = int(params.get("limit", "50"))
    start_date = params.get("startDate")
    end_date = params.get("endDate")

    if event_type:
        key_condition = Key("eventType").eq(event_type)
        if start_date and end_date:
            key_condition = key_condition & Key("timestamp").between(
                start_date, end_date
            )
        elif start_date:
            key_condition = key_condition & Key("timestamp").gte(start_date)
        elif end_date:
            key_condition = key_condition & Key("timestamp").lte(end_date)

        response = audit_table.query(
            IndexName="EventTypeIndex",
            KeyConditionExpression=key_condition,
            ScanIndexForward=False,
            Limit=limit,
        )
    else:
        response = audit_table.scan(Limit=limit)

    return success(body={"entries": response.get("Items", [])})
