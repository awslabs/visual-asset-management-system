#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Compliance Audit Service handler.

- GET /compliance/audit/{databaseId}/{assetId}  — audit history of an asset
- GET /compliance/audit                          — audit entries filtered by event type

Audit table (PK entryId; GSI AssetIndex on databaseId:assetId/timestamp; GSI EventTypeIndex on
eventType/timestamp). Both listings page externally with a Base64 NextToken.
"""

import base64
import json

import boto3
from aws_lambda_powertools.utilities.parser import ValidationError
from aws_lambda_powertools.utilities.typing import LambdaContext
from boto3.dynamodb.conditions import Key
from botocore.config import Config

from common.apiRoutes import API_COMPLIANCE_AUDIT, API_COMPLIANCE_AUDIT_ASSET
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

retry_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})
dynamodb = boto3.resource("dynamodb", config=retry_config)
logger = safeLogger(service_name="ComplianceAuditService")

claims_and_roles = {}

COMPLIANCE_EVALUATION_OBJECT_TYPE = "complianceEvaluation"

# Page bounds for audit listings (newest first).
DEFAULT_AUDIT_PAGE_SIZE = 50
MAX_AUDIT_PAGE_SIZE = 500

# Every event type the compliance handlers write; the unfiltered listing reads the
# EventTypeIndex partition of each in turn.
AUDIT_EVENT_TYPES = (
    "compliance_check",
    "quarantine_released",
    "exception_granted",
    "schema_bound_to_database",
    "schema_unbound_from_database",
    "schema_bound_to_asset",
    "schema_unbound_from_asset",
    "schema_deleted",
    "cascade_triggered",
    "cascade_auto_triggered",
    "cascade_approved",
    "cascade_rejected",
    "cascade_completed",
)

try:
    audit_table_name = get_table_name(ResourceKeys.COMPLIANCE_AUDIT_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed loading resource names")
    raise e

audit_table = dynamodb.Table(audit_table_name)


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
    if API_COMPLIANCE_AUDIT_ASSET.matches(path):
        return get_asset_audit(event, path_params.get("databaseId"), path_params.get("assetId"),
                               query_params)
    if API_COMPLIANCE_AUDIT.matches(path):
        return query_audit(event, query_params)
    return validation_error(body={"message": "Method not allowed"}, event=event)


#######################
# Authorization helpers
#######################

def _enforce(database_id, action):
    """Tier-2 check on a compliance evaluation object. Fails closed on an empty token list."""
    if len(claims_and_roles["tokens"]) == 0:
        return False
    return CasbinEnforcer(claims_and_roles).enforce({
        "object__type": COMPLIANCE_EVALUATION_OBJECT_TYPE,
        "databaseId": database_id or "",
    }, action)


#######################
# Paging helpers
#######################

def _page_arguments(event, params):
    """(page_size, exclusive_start_key) from `limit` / `maxItems` and `startingToken`, or a
    validation response when they are malformed."""
    raw_size = params.get("maxItems", params.get("limit", str(DEFAULT_AUDIT_PAGE_SIZE)))
    try:
        page_size = int(raw_size)
    except (TypeError, ValueError):
        return None, None, validation_error(body={"message": "limit must be an integer"}, event=event)
    page_size = max(1, min(page_size, MAX_AUDIT_PAGE_SIZE))

    exclusive_start_key = None
    starting_token = params.get("startingToken")
    if starting_token:
        try:
            exclusive_start_key = json.loads(base64.b64decode(starting_token).decode("utf-8"))
        except (ValueError, TypeError):
            return None, None, validation_error(body={"message": "Invalid pagination token"}, event=event)
    return page_size, exclusive_start_key, None


def _timestamp_condition(key_condition, start_date, end_date):
    """Narrow a GSI key condition to a timestamp window when bounds are supplied."""
    if start_date and end_date:
        return key_condition & Key("timestamp").between(start_date, end_date)
    if start_date:
        return key_condition & Key("timestamp").gte(start_date)
    if end_date:
        return key_condition & Key("timestamp").lte(end_date)
    return key_condition


def _encode_token(last_evaluated_key):
    return base64.b64encode(json.dumps(last_evaluated_key, default=str).encode("utf-8")).decode("utf-8")


def _validate_dates(start_date, end_date):
    return validate({
        "startDate": {"value": start_date, "validator": "STRING_256", "optional": True},
        "endDate": {"value": end_date, "validator": "STRING_256", "optional": True},
    })


#######################
# Business logic
#######################

def get_asset_audit(event, database_id, asset_id, params):
    """Audit entries of one asset, newest first (AssetIndex GSI), externally paged; optional
    `startDate` / `endDate` (ISO-8601) narrow the window."""
    (valid, message) = validate({
        "databaseId": {"value": database_id, "validator": "ID", "allowGlobalKeyword": True},
        "assetId": {"value": asset_id, "validator": "ASSET_ID"},
    })
    if not valid:
        return validation_error(body={"message": message}, event=event)
    start_date = params.get("startDate")
    end_date = params.get("endDate")
    (valid, message) = _validate_dates(start_date, end_date)
    if not valid:
        return validation_error(body={"message": message}, event=event)

    if not _enforce(database_id, "GET"):
        return authorization_error()

    page_size, exclusive_start_key, error = _page_arguments(event, params)
    if error:
        return error

    query_kwargs = {
        "IndexName": "AssetIndex",
        "KeyConditionExpression": _timestamp_condition(
            Key("databaseId:assetId").eq(f"{database_id}:{asset_id}"), start_date, end_date),
        "ScanIndexForward": False,
        "Limit": page_size,
    }
    if exclusive_start_key:
        query_kwargs["ExclusiveStartKey"] = exclusive_start_key

    response = audit_table.query(**query_kwargs)
    result = {"entries": response.get("Items", [])}
    if "LastEvaluatedKey" in response:
        result["NextToken"] = _encode_token(response["LastEvaluatedKey"])
    return success(body=result)


def query_audit(event, params):
    """Audit entries across assets, newest first, externally paged.

    With `eventType`, one EventTypeIndex partition is paged. Without it, the partitions of
    every known event type are read in turn (the token carries the partition being paged), each
    filtered to entries the caller may GET by databaseId.
    """
    event_type = params.get("eventType")
    start_date = params.get("startDate")
    end_date = params.get("endDate")
    (valid, message) = validate({
        "eventType": {"value": event_type, "validator": "STRING_256", "optional": True},
    })
    if not valid:
        return validation_error(body={"message": message}, event=event)
    (valid, message) = _validate_dates(start_date, end_date)
    if not valid:
        return validation_error(body={"message": message}, event=event)

    page_size, exclusive_start_key, error = _page_arguments(event, params)
    if error:
        return error

    casbin_enforcer = CasbinEnforcer(claims_and_roles) if len(claims_and_roles["tokens"]) > 0 else None

    if event_type:
        partitions = [event_type]
    else:
        partitions = list(AUDIT_EVENT_TYPES)

    # A token for the multi-partition walk is {"partition": <eventType>, "key": <LastEvaluatedKey>}.
    start_partition = None
    if exclusive_start_key and isinstance(exclusive_start_key, dict) and "partition" in exclusive_start_key:
        start_partition = exclusive_start_key.get("partition")
        exclusive_start_key = exclusive_start_key.get("key")
        if start_partition in partitions:
            partitions = partitions[partitions.index(start_partition):]

    entries = []
    next_token = None
    for index, partition in enumerate(partitions):
        query_kwargs = {
            "IndexName": "EventTypeIndex",
            "KeyConditionExpression": _timestamp_condition(
                Key("eventType").eq(partition), start_date, end_date),
            "ScanIndexForward": False,
            "Limit": page_size - len(entries),
        }
        if exclusive_start_key and index == 0:
            query_kwargs["ExclusiveStartKey"] = exclusive_start_key
        response = audit_table.query(**query_kwargs)
        for item in response.get("Items", []):
            # List filtering appends only when enforce() passes, so empty tokens yield an empty list.
            if casbin_enforcer and casbin_enforcer.enforce({
                "object__type": COMPLIANCE_EVALUATION_OBJECT_TYPE,
                "databaseId": item.get("databaseId", ""),
            }, "GET"):
                entries.append(item)
        if "LastEvaluatedKey" in response:
            next_token = _encode_token({"partition": partition, "key": response["LastEvaluatedKey"]})
            break
        if len(entries) >= page_size:
            if index + 1 < len(partitions):
                next_token = _encode_token({"partition": partitions[index + 1], "key": None})
            break

    result = {"entries": entries}
    if next_token:
        result["NextToken"] = next_token
    return success(body=result)
