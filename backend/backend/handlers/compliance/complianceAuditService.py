#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Compliance Audit Service handler.

- GET /compliance/audit/{databaseId}/{assetId}  — audit history of an asset
- GET /compliance/audit                          — audit entries across assets, optionally by event type

Audit table (PK entryId; GSI AssetIndex on databaseId:assetId/timestamp; GSI EventTypeIndex on
eventType/timestamp; GSI AuditByDateGSI on the constant allListPartition/timestamp). Every listing
is one newest-first query on the index that serves it and pages externally with a Base64 NextToken.
The entries of an archived asset are decided with its stored database id, `<databaseId>#deleted`
(`ArchivedAssets`), so they follow the engine's archived-record rule as the asset does.
"""

import base64
import json

import boto3
from aws_lambda_powertools.utilities.parser import ValidationError
from aws_lambda_powertools.utilities.typing import LambdaContext
from boto3.dynamodb.conditions import Attr, Key
from botocore.config import Config

from common.apiRoutes import API_COMPLIANCE_AUDIT, API_COMPLIANCE_AUDIT_ASSET
from common.compliance import listing
from common.compliance.archivedAssets import ArchivedAssets
from common.compliance.auditRecord import AUDIT_LIST_PARTITION
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
DEFAULT_AUDIT_PAGE_SIZE = 100
MAX_AUDIT_PAGE_SIZE = listing.MAX_LISTING_PAGE_SIZE
INVALID_PAGINATION_TOKEN_MESSAGE = "Invalid pagination token"
# The attributes a LastEvaluatedKey of each listing's index carries (the table key plus the index
# keys); a token missing one belongs to another listing and never reaches DynamoDB.
ASSET_INDEX_KEY_ATTRIBUTES = ("entryId", "databaseId:assetId", "timestamp")
EVENT_TYPE_INDEX_KEY_ATTRIBUTES = ("entryId", "eventType", "timestamp")
DATE_INDEX_KEY_ATTRIBUTES = ("entryId", "allListPartition", "timestamp")

try:
    audit_table_name = get_table_name(ResourceKeys.COMPLIANCE_AUDIT_STORAGE_TABLE)
    asset_table_name = get_table_name(ResourceKeys.ASSET_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed loading resource names")
    raise e

audit_table = dynamodb.Table(audit_table_name)
# Read for the archived status of an asset whose entries a caller may not see (keys only).
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

def _evaluation_object(database_id):
    """The compliance evaluation object of a database, as the list filter and the single-resource
    check enforce it. An audit entry with no databaseId maps to the empty-database object, so such
    entries are listed only for a caller allowed to GET that object."""
    return {
        "object__type": COMPLIANCE_EVALUATION_OBJECT_TYPE,
        "databaseId": database_id or "",
        "complianceState": "",
    }


def _enforce(database_id, action):
    """Tier-2 check on a compliance evaluation object. Fails closed on an empty token list."""
    if len(claims_and_roles["tokens"]) == 0:
        return False
    return CasbinEnforcer(claims_and_roles).enforce(_evaluation_object(database_id), action)


#######################
# Paging helpers
#######################

def _page_arguments(event, params, key_attributes=None):
    """(page_size, exclusive_start_key) from `limit` / `maxItems` and `startingToken`, or a
    validation response when they are malformed. A token must decode to a non-empty JSON object;
    with `key_attributes`, to one carrying exactly those string attributes (`_index_key`)."""
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
            exclusive_start_key = json.loads(base64.b64decode(starting_token, validate=True).decode("utf-8"))
        except (ValueError, TypeError):
            exclusive_start_key = None
        if not isinstance(exclusive_start_key, dict) or not exclusive_start_key:
            return None, None, validation_error(
                body={"message": INVALID_PAGINATION_TOKEN_MESSAGE}, event=event)
        if key_attributes is not None:
            exclusive_start_key = _index_key(exclusive_start_key, key_attributes)
            if exclusive_start_key is None:
                return None, None, validation_error(
                    body={"message": INVALID_PAGINATION_TOKEN_MESSAGE}, event=event)
    return page_size, exclusive_start_key, None


def _index_key(decoded, key_attributes):
    """The index key a decoded token carries, or None when it is not an object holding a string
    value for every one of `key_attributes`."""
    if not isinstance(decoded, dict):
        return None
    if any(not isinstance(decoded.get(name), str) for name in key_attributes):
        return None
    return {name: decoded[name] for name in key_attributes}


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

    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error()
    if not ArchivedAssets(asset_table).decide(
            database_id, asset_id, lambda stored_id: _enforce(stored_id, "GET")):
        return authorization_error()

    page_size, exclusive_start_key, error = _page_arguments(event, params, ASSET_INDEX_KEY_ATTRIBUTES)
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


def _audit_filter(database_id, text):
    """The FilterExpression of the listing's `databaseId` and `text` filters, or None. An entry
    written before `searchText` existed is matched on its asset id and actor."""
    conditions = []
    if database_id:
        conditions.append(Attr("databaseId").eq(database_id))
    if text:
        conditions.append(Attr("searchText").contains(text) | Attr("assetId").contains(text)
                          | Attr("actor").contains(text))
    if not conditions:
        return None
    expression = conditions[0]
    for condition in conditions[1:]:
        expression = expression & condition
    return expression


def query_audit(event, params):
    """Audit entries across assets, newest first, filtered and paged on the server.

    Query parameters: `eventType`, `databaseId`, `assetId` (with `databaseId`), `startDate` /
    `endDate` (ISO-8601), `text` (case-insensitive contains on the asset name at the time of the
    event, the asset id or the actor), `maxItems` (1..200) and `startingToken`.

    Access path — a key condition on one index, the other filters a FilterExpression:
    - `databaseId` + `assetId`: the asset's AssetIndex partition;
    - `eventType`: that EventTypeIndex partition; the token names it and carries its key;
    - otherwise AuditByDateGSI, whose partition is the constant every audit row carries.
    The time window narrows each index's `timestamp` sort key. The page is filled across at most
    `listing.DEFAULT_READ_BUDGET` reads with the entries the caller may GET by databaseId
    (`listing.query_page`); `NextToken` resumes after the last entry returned.
    """
    event_type = params.get("eventType") or None
    start_date = params.get("startDate")
    end_date = params.get("endDate")
    database_id = params.get("databaseId") or None
    asset_id = params.get("assetId") or None
    text = listing.normalized_text(params.get("text")) or None
    (valid, message) = validate({
        "eventType": {"value": event_type, "validator": "STRING_256", "optional": True},
        "databaseId": {"value": database_id, "validator": "ID", "allowGlobalKeyword": True,
                       "optional": True},
        "assetId": {"value": asset_id, "validator": "ASSET_ID", "optional": True},
        "text": {"value": text, "validator": "STRING_256", "optional": True},
    })
    if not valid:
        return validation_error(body={"message": message}, event=event)
    if asset_id and not database_id:
        return validation_error(body={"message": "assetId requires databaseId"}, event=event)
    (valid, message) = _validate_dates(start_date, end_date)
    if not valid:
        return validation_error(body={"message": message}, event=event)
    page_size = listing.page_size_of(params.get("maxItems", params.get("limit")),
                                     DEFAULT_AUDIT_PAGE_SIZE)
    if page_size is None:
        return validation_error(body={"message": "limit must be an integer"}, event=event)

    filter_database = database_id
    if asset_id:
        index_name, keys = "AssetIndex", ASSET_INDEX_KEY_ATTRIBUTES
        key_condition = Key("databaseId:assetId").eq(f"{database_id}:{asset_id}")
        filter_database = None
        if event_type:
            extra = Attr("eventType").eq(event_type)
        else:
            extra = None
    elif event_type:
        index_name, keys = "EventTypeIndex", EVENT_TYPE_INDEX_KEY_ATTRIBUTES
        key_condition = Key("eventType").eq(event_type)
        extra = None
    else:
        index_name, keys = "AuditByDateGSI", DATE_INDEX_KEY_ATTRIBUTES
        key_condition = Key("allListPartition").eq(AUDIT_LIST_PARTITION)
        extra = None

    start_key = None
    starting_token = params.get("startingToken")
    if starting_token:
        if event_type and not asset_id:
            # A token for the event-type listing is {"partition": <eventType>, "key": <key> | null}.
            # One naming another partition, or carrying a key that is not this index's, never
            # reaches DynamoDB.
            raw = None
            try:
                raw = json.loads(base64.b64decode(starting_token, validate=True).decode("utf-8"))
            except (ValueError, TypeError):
                raw = None
            if not isinstance(raw, dict) or raw.get("partition") != event_type:
                logger.info("Audit pagination token rejected: unknown partition")
                return validation_error(body={"message": INVALID_PAGINATION_TOKEN_MESSAGE},
                                        event=event)
            if raw.get("key") is not None:
                start_key = _index_key(raw.get("key"), keys)
                if start_key is None:
                    logger.info("Audit pagination token rejected: malformed key")
                    return validation_error(body={"message": INVALID_PAGINATION_TOKEN_MESSAGE},
                                            event=event)
        else:
            start_key = listing.decode_token(starting_token, keys)
            if start_key is None:
                return validation_error(body={"message": INVALID_PAGINATION_TOKEN_MESSAGE},
                                        event=event)

    query_kwargs = {
        "IndexName": index_name,
        "KeyConditionExpression": _timestamp_condition(key_condition, start_date, end_date),
        "ScanIndexForward": False,
    }
    expression = _audit_filter(filter_database, text)
    if extra is not None:
        expression = extra if expression is None else expression & extra
    if expression is not None:
        query_kwargs["FilterExpression"] = expression

    casbin_enforcer = CasbinEnforcer(claims_and_roles) if len(claims_and_roles["tokens"]) > 0 else None
    # The entries of an archived asset are listed only for a caller allowed on its stored database
    # id; its archived status is read once per database, and only when that decides anything.
    archived = ArchivedAssets(asset_table)

    def may_get(item):
        # List filtering appends only when enforce() passes, so empty tokens yield an empty list.
        return bool(casbin_enforcer) and archived.decide(
            item.get("databaseId"), item.get("assetId"),
            lambda stored_id: casbin_enforcer.enforce(_evaluation_object(stored_id), "GET"),
            cache_key="GET", listing=True)

    entries, next_key, _ = listing.query_page(
        audit_table, query_kwargs, page_size, keys, start_key=start_key,
        filtered=expression is not None, accept=may_get)
    for entry in entries:
        entry.pop("searchText", None)
    result = {"entries": entries}
    if next_key:
        result["NextToken"] = _encode_token(
            {"partition": event_type, "key": next_key} if event_type and not asset_id else next_key)
    return success(body=result)
