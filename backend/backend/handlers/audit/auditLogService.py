# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Audit log search service handler for VAMS API.

Searches the audit log groups with CloudWatch Logs Insights. Core VAMS: no feature switch; access is
decided only by authorization (Tier 1 route, Tier 2 per requested log group as an ``auditLog`` object).

Routes (``common/apiRoutes.py`` ``AUDIT_ROUTES``):
  * ``GET /audit/log-groups``                    the groups the caller may search, and the filter options
  * ``POST /audit/queries``                      start a search (202, a handle)
  * ``GET /audit/queries/{queryId}``             long-poll the status and read one page of rows
  * ``DELETE /audit/queries/{queryId}``          stop a running search
  * ``GET /audit/queries/{queryId}/export``      one export chunk (CSV or NDJSON)

The caller never sends query text. The query is built from enumerated, validated filters by
``common/auditSearch/queryBuilder``; Logs Insights only narrows, and every row is parsed, redacted and
checked again against the filters here (``recordMatcher``). A row that fails that check is not shown,
and no count of such rows is ever returned (a count would let a caller probe values the viewer hides).

A handle is a VAMS-generated UUID that belongs to the user who started the search. An unknown, foreign,
expired or no-longer-authorized handle is the same ``404 Query not found``. An unknown log group and a
log group the caller may not search are the same generic ``403``.
"""

import dataclasses
import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config
from botocore.exceptions import ClientError
from aws_lambda_powertools.utilities.parser import ValidationError
from aws_lambda_powertools.utilities.typing import LambdaContext

from common.auditActions import AUDIT_ROUTE_ACTIONS, AUDIT_SECONDARY_ACTIONS, AUDIT_SYSTEM_ACTIONS
from common.auditLogGroups import AUDIT_LOG_GROUPS, AuditLogGroup, authorization_object, resource_key
from common.auditSearch import containsFilter, queryBuilder
from common.auditSearch.containsFilter import MatchBudget
from common.auditSearch.exportEncoding import encode_chunk
from common.auditSearch.parsers import parse_record
from common.auditSearch.queryBuilder import AuditSearchValidationError, SearchFilters, SearchLimits
from common.auditSearch.recordMatcher import recheck
from common.auditSearch.rowModel import OUTCOMES, dedupe_rows, time_ms
from common.resourceNames import ResourceKeys, get_log_group_name, get_table_name
from customLogging.auditLogging import audited_handler, log_action
from customLogging.logger import safeLogger
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from models.auditLogs import (
    DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, ExportAuditQueryRequestModel, GetAuditQueryRequestModel,
    StartAuditQueryRequestModel, StopAuditQueryRequestModel, validate_query_id,
)
from models.common import (
    APIGatewayProxyResponseV2, VAMSGeneralErrorResponse, authorization_error, general_error,
    internal_error, success, validation_error, validation_error_message,
)

retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

dynamodb = boto3.resource('dynamodb', config=retry_config)
logs_client = boto3.client('logs', config=retry_config)
logger = safeLogger(service_name="AuditLogService")

# Global variable for claims and roles
claims_and_roles = {}

try:
    query_table_name = get_table_name(ResourceKeys.AUDIT_LOG_QUERIES_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed resolving resource names")
    raise e

query_table = dynamodb.Table(query_table_name)

#######################
# Constants
#######################

# The permission-check marker of the group list (Tier 2 denials of hidden groups are written as probe
# records, in one batch, so a page load does not write one unmarked denial per hidden group).
PROBE_ENDPOINT_LOG_GROUPS = "GET /audit/log-groups"
PROBE_ENDPOINT_START = "POST /audit/queries"

# Configuration (environment variables set from app.auditLogging.search by the CDK builder). The
# defaults apply when a variable is absent, so an upgrade with no configuration behaves as documented.
# The ceilings mirror infra/config/config.ts and common/auditSearch (CEILING_*).
ENV_MAX_WINDOW_DAYS = "AUDIT_SEARCH_MAX_QUERY_WINDOW_DAYS"
ENV_MAX_ROWS = "AUDIT_SEARCH_MAX_ROWS"
ENV_MAX_CONCURRENT = "AUDIT_SEARCH_MAX_CONCURRENT_QUERIES"
ENV_MAX_CONCURRENT_PER_USER = "AUDIT_SEARCH_MAX_CONCURRENT_QUERIES_PER_USER"
ENV_DEADLINE_SECONDS = "AUDIT_SEARCH_QUERY_DEADLINE_SECONDS"
ENV_LONG_POLL_SECONDS = "AUDIT_SEARCH_LONG_POLL_WAIT_SECONDS"
ENV_MAX_CONTAINS_TERM_LENGTH = "AUDIT_SEARCH_MAX_CONTAINS_TERM_LENGTH"
ENV_MAX_CONTAINS_TERMS = "AUDIT_SEARCH_MAX_CONTAINS_TERMS"
ENV_HANDLE_RETENTION_MINUTES = "AUDIT_SEARCH_HANDLE_RETENTION_MINUTES"

DEFAULT_MAX_CONCURRENT = 5
CEILING_MAX_CONCURRENT = 20
DEFAULT_MAX_CONCURRENT_PER_USER = 2
DEFAULT_DEADLINE_SECONDS = 300
CEILING_DEADLINE_SECONDS = 900
DEFAULT_LONG_POLL_SECONDS = 20
CEILING_LONG_POLL_SECONDS = 25

# The poll rhythm inside one long-poll, and the headroom kept for the response and the audit write.
POLL_INTERVAL_SECONDS = 1.5
LAMBDA_HEADROOM_SECONDS = 3.0
# The share of the remaining Lambda time the server-side recheck may use before it returns what it has.
RECHECK_HEADROOM_SECONDS = 4.0

# A handle lives for ``handleRetentionMinutes`` after its last activity (start, poll, page, export or
# stop), by DynamoDB TTL on ``expiresAt``. TTL deletion is lazy, so a row past its ``expiresAt`` reads as
# not found and its contains terms are never used. A running search keeps its row at least until its deadline.
DEFAULT_HANDLE_RETENTION_MINUTES = 60
MIN_HANDLE_RETENTION_MINUTES = 5
CEILING_HANDLE_RETENTION_MINUTES = 1440

# The attribute that holds the contains terms of a search (KMS-encrypted table). Kept apart from the
# normalized filters so it can be removed on its own once the search is stopped or its export is done.
TERMS_ATTRIBUTE = "containsTerms"

# One page of a results read stays under this many serialized bytes (the Lambda response limit is 6 MB).
PAGE_BYTE_BUDGET = 5 * 1024 * 1024
# One export chunk is at most this many bytes of the JSON response body.
EXPORT_CHUNK_BYTES = 4 * 1024 * 1024

OPEN_INDEX = "OpenQueriesIndex"
OPEN_KEY_VALUE = "open"

STATUS_RUNNING = "Running"
STATUS_COMPLETE = "Complete"
STATUS_FAILED = "Failed"
STATUS_CANCELLED = "Cancelled"
STATUS_TIMEOUT = "Timeout"
_OPEN_STATUSES = frozenset({"Scheduled", "Running"})
_CW_STATUS = {"Scheduled": STATUS_RUNNING, "Running": STATUS_RUNNING, "Complete": STATUS_COMPLETE,
              "Failed": STATUS_FAILED, "Cancelled": STATUS_CANCELLED, "Timeout": STATUS_TIMEOUT}

WARNING_RECHECK_BUDGET = "recheckTimeBudget"
WARNING_CANDIDATE_LIMIT = "candidateLimitReached"

MESSAGE_NOT_FOUND = "Query not found"
MESSAGE_INVALID = "Invalid audit search request"
MESSAGE_BUSY = "Too many audit searches are running. Try again later."

# The generic reasons the viewer's own records carry.
REASON_FORBIDDEN = "forbidden"
REASON_VALIDATION = "validation"
REASON_BUSY = "throttled"


class AuditSearchDenied(Exception):
    """The caller may not run this search (raised so no success path runs)."""


class AuditSearchNotFound(Exception):
    """The handle is unknown, foreign, expired or no longer authorized: one generic 404."""


class AuditSearchBusy(Exception):
    """A concurrency cap is reached."""


#######################
# Configuration
#######################

def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = int(str(raw).strip())
    except ValueError:
        return default
    return min(max(value, minimum), maximum)


def search_limits() -> SearchLimits:
    """The query limits of this deployment (defaults when the configuration is silent)."""
    return SearchLimits(
        max_window_days=_env_int(ENV_MAX_WINDOW_DAYS, queryBuilder.DEFAULT_MAX_WINDOW_DAYS, 1,
                                 queryBuilder.CEILING_MAX_WINDOW_DAYS),
        max_rows=_env_int(ENV_MAX_ROWS, queryBuilder.DEFAULT_MAX_ROWS, 1, queryBuilder.CEILING_MAX_ROWS),
        max_contains_terms=_env_int(ENV_MAX_CONTAINS_TERMS, containsFilter.DEFAULT_MAX_TERMS, 1,
                                    containsFilter.CEILING_MAX_TERMS),
        max_contains_term_length=_env_int(ENV_MAX_CONTAINS_TERM_LENGTH, containsFilter.DEFAULT_MAX_TERM_LENGTH,
                                          containsFilter.MIN_TERM_LENGTH, containsFilter.CEILING_MAX_TERM_LENGTH),
    ).clamped()


def concurrency_caps() -> Tuple[int, int]:
    """(VAMS-wide, per user) open searches. The per-user cap never exceeds the VAMS-wide one."""
    wide = _env_int(ENV_MAX_CONCURRENT, DEFAULT_MAX_CONCURRENT, 1, CEILING_MAX_CONCURRENT)
    per_user = _env_int(ENV_MAX_CONCURRENT_PER_USER, DEFAULT_MAX_CONCURRENT_PER_USER, 1, wide)
    return wide, min(per_user, wide)


def deadline_seconds() -> int:
    return _env_int(ENV_DEADLINE_SECONDS, DEFAULT_DEADLINE_SECONDS, 10, CEILING_DEADLINE_SECONDS)


def long_poll_seconds() -> int:
    return _env_int(ENV_LONG_POLL_SECONDS, DEFAULT_LONG_POLL_SECONDS, 0, CEILING_LONG_POLL_SECONDS)


def handle_retention_seconds() -> int:
    """How long a handle (and the contains terms in it) lives after its last activity."""
    return 60 * _env_int(ENV_HANDLE_RETENTION_MINUTES, DEFAULT_HANDLE_RETENTION_MINUTES,
                         MIN_HANDLE_RETENTION_MINUTES, CEILING_HANDLE_RETENTION_MINUTES)


def _expiry_after_activity(item: Dict[str, Any], now: int) -> int:
    """The new ``expiresAt`` after activity: retention from now, never before the deadline of a running search."""
    expiry = now + handle_retention_seconds()
    if item.get("status", STATUS_RUNNING) == STATUS_RUNNING:
        expiry = max(expiry, int(item.get("deadlineAt", 0)))
    return expiry


#######################
# Groups and authorization
#######################

def deployed_groups() -> Dict[str, Tuple[AuditLogGroup, str]]:
    """The registered groups whose log group name resolves, by key: (descriptor, log group name).

    A group that is not deployed (its name does not resolve) is omitted and cannot be searched
    (fail closed). The registry is the only list of groups; nothing here names one.
    """
    deployed: Dict[str, Tuple[AuditLogGroup, str]] = {}
    for group in AUDIT_LOG_GROUPS:
        try:
            name = get_log_group_name(resource_key(group))
        except Exception:
            continue
        if name:
            deployed[group.key] = (group, name)
    return deployed


def _group_allowed(enforcer: Any, group_key: str) -> bool:
    """Tier 2: the caller may search this group (``auditLog`` object, GET)."""
    return bool(enforcer.enforce(authorization_object(group_key), "GET"))


def _tier1(event: Dict[str, Any]) -> Optional[CasbinEnforcer]:
    """The enforcer when the caller may use this route, else None (pre-set false; empty tokens deny)."""
    if len(claims_and_roles.get("tokens", [])) > 0:
        enforcer = CasbinEnforcer(claims_and_roles)
        if enforcer.enforceAPI(event):
            return enforcer
    return None


def _user_id() -> str:
    return claims_and_roles["tokens"][0]


#######################
# Handle table
#######################

def _now() -> int:
    return int(time.time())


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_handle(query_id: str) -> Optional[Dict[str, Any]]:
    if not validate_query_id(query_id):
        return None
    item = query_table.get_item(Key={"queryId": query_id}, ConsistentRead=True).get("Item")
    if not item:
        return None
    if int(item.get("expiresAt", 0)) and int(item["expiresAt"]) < _now():
        return None  # past its TTL, not yet deleted by DynamoDB: its terms are never read
    if int(item.get("containsCount", 0)) and not item.get(TERMS_ATTRIBUTE) \
            and item.get("status", STATUS_RUNNING) in (STATUS_RUNNING, STATUS_COMPLETE):
        return None  # a search with contains terms whose terms were removed is done: no page can be rechecked
    return item


def _touch_handle(item: Dict[str, Any]) -> None:
    """Activity on a handle moves its expiry (one conditional write; the row must still exist)."""
    expiry = _expiry_after_activity(item, _now())
    try:
        query_table.update_item(
            Key={"queryId": item["queryId"]}, UpdateExpression="SET expiresAt = :e",
            ConditionExpression="attribute_exists(queryId)", ExpressionAttributeValues={":e": expiry})
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            raise AuditSearchNotFound()
        raise
    item["expiresAt"] = expiry


def _remove_terms(query_id: str) -> None:
    """Delete the contains terms of a handle (the row stays, with its status, for the retention window)."""
    try:
        query_table.update_item(Key={"queryId": query_id}, UpdateExpression=f"REMOVE {TERMS_ATTRIBUTE}",
                                ConditionExpression="attribute_exists(queryId)")
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise


def _own_handle(query_id: str, enforcer: CasbinEnforcer) -> Dict[str, Any]:
    """The caller's own handle, or :class:`AuditSearchNotFound`.

    Unknown, expired, foreign, and a handle whose groups the caller may no longer search are one
    outcome. Ownership compares the normalized user id of ``request_to_claims``.
    """
    item = _load_handle(query_id)
    if item is None or item.get("userId") != _user_id():
        raise AuditSearchNotFound()
    deployed = deployed_groups()
    for key in item.get("logGroupKeys", []):
        if key not in deployed or not _group_allowed(enforcer, key):
            raise AuditSearchNotFound()
    return item


def _close_handle(query_id: str, status: str, row_count: Optional[int] = None,
                  bytes_scanned: Optional[int] = None) -> None:
    """Record a final status and free the handle's concurrency slot (the sparse open index)."""
    names = {"#s": "status"}
    now_expiry = _now() + handle_retention_seconds()
    values: Dict[str, Any] = {":s": status}
    expression = "SET #s = :s"
    if row_count is not None:
        expression += ", rowCount = :r"
        values[":r"] = int(row_count)
    if bytes_scanned is not None:
        expression += ", bytesScanned = :b"
        values[":b"] = int(bytes_scanned)
    values[":e"] = now_expiry
    expression += ", expiresAt = :e"
    remove = "openKey, pollLeaseUntil"
    if status != STATUS_COMPLETE:
        remove += f", {TERMS_ATTRIBUTE}"  # no rows will ever be rechecked for a search that did not complete
    expression += f" REMOVE {remove}"
    response = query_table.update_item(Key={"queryId": query_id}, UpdateExpression=expression,
                                       ExpressionAttributeNames=names, ExpressionAttributeValues=values,
                                       ReturnValues="ALL_OLD")
    _release_slots(response.get("Attributes") if isinstance(response, dict) else None)


def _stop_cloudwatch_query(cwl_query_id: str) -> None:
    try:
        logs_client.stop_query(queryId=cwl_query_id)
    except ClientError:
        pass  # already finished or unknown: nothing to stop
    except Exception:
        logger.exception("Stopping a CloudWatch Logs query failed")


def _open_handles() -> List[Dict[str, Any]]:
    """Every handle that still holds a concurrency slot (the sparse index lists open handles only)."""
    items: List[Dict[str, Any]] = []
    kwargs: Dict[str, Any] = {"IndexName": OPEN_INDEX,
                              "KeyConditionExpression": Key("openKey").eq(OPEN_KEY_VALUE)}
    while True:
        response = query_table.query(**kwargs)
        items.extend(response.get("Items", []))
        if "LastEvaluatedKey" not in response:
            return items
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def _running_cloudwatch_queries() -> Optional[set]:
    """The ids of the Logs Insights queries running in the account, or None when that cannot be read."""
    try:
        ids = set()
        kwargs: Dict[str, Any] = {"status": "Running", "maxResults": 1000}
        while True:
            response = logs_client.describe_queries(**kwargs)
            ids.update(query.get("queryId") for query in response.get("queries", []))
            token = response.get("nextToken")
            if not token:
                return ids
            kwargs["nextToken"] = token
    except Exception:
        logger.warning("Could not list the running Logs Insights queries")
        return None


def reconcile_open_handles() -> List[Dict[str, Any]]:
    """Close the open handles that are past their deadline or that CloudWatch no longer runs, and
    return the ones still open. Runs on start, with no scheduler.

    A handle past ``deadlineAt`` is stopped and recorded as ``Timeout``. A handle CloudWatch no longer
    lists as running frees its slot at once (its final status is read on the next poll).
    """
    now = _now()
    running = _running_cloudwatch_queries()
    still_open = []
    for item in _open_handles():
        if int(item.get("deadlineAt", 0)) <= now:
            _stop_cloudwatch_query(item.get("cwlQueryId", ""))
            _close_handle(item["queryId"], STATUS_TIMEOUT)
            continue
        age = now - int(item.get("createdAtEpoch", now))
        if running is not None and item.get("cwlQueryId") not in running and age > 15:
            # Finished (or gone) in CloudWatch; the status is read from CloudWatch by the next poll.
            response = query_table.update_item(Key={"queryId": item["queryId"]}, UpdateExpression="REMOVE openKey",
                                               ReturnValues="ALL_OLD")
            _release_slots(response.get("Attributes") if isinstance(response, dict) else None)
            continue
        still_open.append(item)
    return still_open


def _check_caps(open_handles: List[Dict[str, Any]]) -> None:
    """The first, cheap cap check against the open handles listed by the (eventually consistent) index.
    It also counts handles started before the slot counters existed. The check that holds under
    concurrent starts is :func:`_reserve_slots`."""
    wide, per_user = concurrency_caps()
    if len(open_handles) >= wide:
        raise AuditSearchBusy()
    if sum(1 for item in open_handles if item.get("userId") == _user_id()) >= per_user:
        raise AuditSearchBusy()


# Slot counters. A start takes one slot in the VAMS-wide counter and one in the caller's counter with a
# conditional update (``count < cap``), so two starts racing for the last slot cannot both get it: the
# write is atomic in DynamoDB, unlike a count of the open handles followed by a put. A close gives the
# slots back. The counters live in the handle table under keys that are not handle ids (a handle id is a
# UUID, so no request can name them), carry no ``openKey`` (they are not in the open index) and expire:
# ``expiresAt`` is the latest deadline of the handles that hold a slot, so a counter that drifted up (a
# crash between reserving and storing a handle) heals itself once those handles are past their deadline.
SLOT_ALL_KEY = "slots#all"
SLOT_USER_KEY_PREFIX = "slots#user#"
SLOT_COUNT = "slotCount"
SLOTS_COUNTED = "slotsCounted"
SLOT_EXPIRY_GRACE_SECONDS = 60


def _slot_keys(user_id: str) -> Tuple[str, str]:
    return SLOT_ALL_KEY, SLOT_USER_KEY_PREFIX + user_id


def _take_slot(key: str, cap: int, expires_at: int) -> bool:
    """Take one slot of a counter: reset it first when it has expired (full or not), else add one if it
    is below ``cap``. The conditional writes are atomic. False when it is full and not expired."""
    now = _now()
    attempts = (
        # An expired counter belongs to handles that are all past their deadline (or to a start that died
        # between reserving and storing a handle): start over, whether or not the counter is full.
        ("SET #c = :one, expiresAt = :e", "attribute_exists(#c) AND expiresAt < :now",
         {":one": 1, ":now": now, ":e": expires_at}),
        ("SET #c = if_not_exists(#c, :zero) + :one, expiresAt = :e",
         "attribute_not_exists(#c) OR #c < :cap",
         {":zero": 0, ":one": 1, ":cap": cap, ":e": expires_at}),
    )
    for expression, condition, values in attempts:
        try:
            query_table.update_item(
                Key={"queryId": key}, UpdateExpression=expression, ConditionExpression=condition,
                ExpressionAttributeNames={"#c": SLOT_COUNT}, ExpressionAttributeValues=values)
            return True
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise
    return False


def _give_slot_back(key: str) -> None:
    try:
        query_table.update_item(
            Key={"queryId": key}, UpdateExpression="SET #c = #c - :one",
            ConditionExpression="attribute_exists(#c) AND #c > :zero",
            ExpressionAttributeNames={"#c": SLOT_COUNT}, ExpressionAttributeValues={":one": 1, ":zero": 0})
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            logger.warning("Giving back an audit search slot failed")
    except Exception:
        logger.warning("Giving back an audit search slot failed")


def _reserve_slots(user_id: str) -> None:
    """Take one slot of the VAMS-wide cap and one of the caller's cap, atomically per counter.
    Raises ``AuditSearchBusy`` (and keeps nothing) when either is full."""
    wide, per_user = concurrency_caps()
    expires_at = _now() + deadline_seconds() + SLOT_EXPIRY_GRACE_SECONDS
    all_key, user_key = _slot_keys(user_id)
    if not _take_slot(all_key, wide, expires_at):
        raise AuditSearchBusy()
    if not _take_slot(user_key, per_user, expires_at):
        _give_slot_back(all_key)
        raise AuditSearchBusy()


def _give_back_user_slots(user_id: str) -> None:
    for key in _slot_keys(user_id):
        _give_slot_back(key)


def _release_slots(old_item: Optional[Dict[str, Any]]) -> None:
    """Give the slots of a handle back when this update is the one that closed it (the old item was
    open and counted), so two closers of one handle give them back once."""
    if not old_item or not old_item.get(SLOTS_COUNTED) or old_item.get("openKey") != OPEN_KEY_VALUE:
        return
    _give_back_user_slots(str(old_item.get("userId", "")))


#######################
# Results
#######################

def _result_fields(result: List[Dict[str, str]]) -> Dict[str, str]:
    return {entry.get("field"): entry.get("value") for entry in result if isinstance(entry, dict)}


def _group_by_log_name(group_keys: List[str], deployed: Dict[str, Tuple[AuditLogGroup, str]]
                       ) -> Dict[str, AuditLogGroup]:
    return {deployed[key][1]: deployed[key][0] for key in group_keys if key in deployed}


def _log_name(log_field: Optional[str]) -> str:
    """The log group name of an Insights ``@log`` value (``<account>:<name>``)."""
    if not isinstance(log_field, str):
        return ""
    return log_field.split(":", 1)[1] if ":" in log_field else log_field


def filters_from_item(item: Dict[str, Any]) -> SearchFilters:
    """The validated filters a handle was started with (stored normalized; stored values are trusted
    only as far as ``SearchFilters`` goes: they were validated before they were stored)."""
    stored = json.loads(item["filters"])
    stored["contains"] = list(item.get(TERMS_ATTRIBUTE) or [])  # the terms live in their own attribute
    return SearchFilters(
        group_keys=tuple(stored["groupKeys"]), start_time=int(stored["startTime"]), end_time=int(stored["endTime"]),
        actions=tuple(stored.get("actions", [])), entity_types=tuple(stored.get("entityTypes", [])),
        entity_ids=tuple(stored.get("entityIds", [])), users=tuple(stored.get("users", [])),
        api_key_ids=tuple(stored.get("apiKeyIds", [])), database_ids=tuple(stored.get("databaseIds", [])),
        asset_ids=tuple(stored.get("assetIds", [])), outcomes=tuple(stored.get("outcomes", [])),
        request_ids=tuple(stored.get("requestIds", [])), contains=tuple(stored.get("contains", [])),
        include_probes=bool(stored.get("includeProbes", True)), sort=stored.get("sort", "desc"))


def filters_to_json(filters: SearchFilters) -> str:
    return json.dumps({
        "groupKeys": list(filters.group_keys), "startTime": filters.start_time, "endTime": filters.end_time,
        "actions": list(filters.actions), "entityTypes": list(filters.entity_types),
        "entityIds": list(filters.entity_ids), "users": list(filters.users),
        "apiKeyIds": list(filters.api_key_ids), "databaseIds": list(filters.database_ids),
        "assetIds": list(filters.asset_ids), "outcomes": list(filters.outcomes),
        "requestIds": list(filters.request_ids),
        "includeProbes": filters.include_probes, "sort": filters.sort,
    }, separators=(",", ":"))


def _candidate_limit_reached(parsed: List[Any], kept: List[Any], item: Dict[str, Any], filters: SearchFilters,
                             limit: int, budget: MatchBudget, raw_count: int) -> bool:
    """Whether the search may have missed rows because Logs Insights cut its candidates at the row limit.

    The Insights limit applies to the raw candidates before the recheck, and the filters that are applied
    only on parsed rows (outcome, entity type, probe handling) cannot narrow it. The flag is derived from
    what the caller may see only, never from the raw result: it is set when the number of candidates that
    satisfy every filter the caller can see, except those three, reaches the limit. The raw result also
    holds records that matched on content the caller cannot see (changes, previous values, request echo,
    JSON keys), so its length would tell a caller that such records exist (a one-bit probe).
    The flag can stay unset when records the caller cannot see took the slots; it never tells that they
    exist. ``raw_count`` only decides whether the second pass can matter (it cannot exceed the raw
    count): the answer does not depend on it.
    """
    if not limit or item.get("prefilterComplete", True) or raw_count < limit:
        return False
    if len(kept) >= limit:
        return True
    relaxed = dataclasses.replace(filters, outcomes=(), entity_types=(), include_probes=True)
    return len(recheck(parsed, relaxed, budget)) >= limit


def build_rows(results: List[List[Dict[str, str]]], item: Dict[str, Any], filters: SearchFilters,
               context: Optional[LambdaContext]) -> Tuple[List[Any], Dict[str, Any]]:
    """Parse, dedupe and recheck the Insights candidates of a handle.

    Returns (rows, facts). ``facts`` holds ``duplicatesRemoved``, ``recheckBudgetExceeded`` and
    ``candidateLimitReached``. It never holds a count of the rows the recheck dropped.
    """
    deployed = deployed_groups()
    by_name = _group_by_log_name(list(filters.group_keys), deployed)
    parsed = []
    for result in results:
        fields = _result_fields(result)
        group = by_name.get(_log_name(fields.get("@log")))
        if group is None:
            continue  # a row outside the searched groups is never shown
        try:
            length = int(fields.get("msgLen")) if fields.get("msgLen") is not None else None
        except (TypeError, ValueError):
            length = None
        parsed.append(parse_record(fields.get("msg"), group, fields.get("@timestamp"), length))

    remaining = (context.get_remaining_time_in_millis() / 1000.0) if context is not None else 30.0
    budget = MatchBudget(deadline=time.monotonic() + max(1.0, remaining - RECHECK_HEADROOM_SECONDS))
    # Rows are rechecked first and deduplicated after, so ``duplicatesRemoved`` counts only rows the
    # caller may see: a duplicate pair that the recheck drops must not show in any figure.
    kept = recheck(parsed, filters, budget)
    rows, duplicates = dedupe_rows(kept)
    limit = int(item.get("queryLimit", 0) or 0)
    facts = {
        "duplicatesRemoved": duplicates,
        "recheckBudgetExceeded": budget.exceeded,
        "candidateLimitReached": _candidate_limit_reached(parsed, kept, item, filters, limit, budget, len(results)),
    }
    return rows, facts


def _row_time(row: Any) -> int:
    """The row's instant in epoch milliseconds (rows with no readable time sort first)."""
    epoch_ms = time_ms(row.timestamp)
    return epoch_ms if epoch_ms is not None else -1


def sort_rows(rows: List[Any], order: str) -> List[Any]:
    """Rows in time order by instant, not by text: legacy and contract v1 records write the time in
    different formats. The sort is stable, so equal instants keep their arrival order."""
    return sorted(rows, key=_row_time, reverse=(order == "desc"))


def _json_size(value: Any) -> int:
    return len(json.dumps(value, separators=(",", ":"), default=str).encode("utf-8"))


def _statistics(response: Dict[str, Any]) -> Dict[str, Any]:
    """The scan statistics of a response. Only figures that do not depend on the search filters are
    returned (records and bytes scanned in the searched groups and window). CloudWatch's
    ``recordsMatched`` counts records that matched the Insights prefilter on the raw message, before the
    server recheck, so ``recordsMatched - rowCount`` would be the number of records whose hidden or
    unsearched content matched a term: it is never returned, here or anywhere else."""
    stats = response.get("statistics") or {}
    return {
        "recordsScanned": int(stats.get("recordsScanned", 0) or 0),
        "bytesScanned": int(stats.get("bytesScanned", 0) or 0),
    }


#######################
# Business logic
#######################

def _contains_digest(filters: SearchFilters) -> Optional[str]:
    if not filters.contains:
        return None
    return hashlib.sha256("\n".join(filters.contains).encode("utf-8")).hexdigest()


def _start_changes(filters: SearchFilters) -> Dict[str, Any]:
    """What a start records: the normalized filters; ``contains`` as count, length and SHA-256 only."""
    summary = queryBuilder.filters_summary(filters)
    digest = _contains_digest(filters)
    if digest:
        summary["containsSha256"] = digest
    return summary


def authorized_group_keys(enforcer: CasbinEnforcer, probe_endpoint: str) -> Tuple[List[str], Dict[str, Tuple[AuditLogGroup, str]]]:
    """The deployed groups the caller may search.

    The caller did not ask for the groups it may not search, so those denials are not written one per
    group: one summary denial (the hidden group keys, sorted) is written as a probe-marked record, so a
    call writes at most one probe record however many groups are hidden.
    """
    # Imported here: the batch helper lives with the permission-check endpoints of the auth routes
    # handler, which this handler needs only for this one probe-marked path.
    from handlers.auth.routes import _DenialAuditBatch
    deployed = deployed_groups()
    batch = _DenialAuditBatch(claims_and_roles, enforcer, probe_endpoint)
    allowed: List[str] = []
    hidden: List[str] = []
    for key in deployed:
        if enforcer.service_object.enforce(authorization_object(key), "GET"):
            allowed.append(key)
        else:
            hidden.append(key)
    if hidden:
        batch.add_denial(authorization_object(",".join(sorted(hidden))), "GET")
    batch.flush()
    return allowed, deployed


def start_query(event: Dict[str, Any], enforcer: CasbinEnforcer, raw_filters: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and authorize a search, start it, store its handle. Returns the response body."""
    limits = search_limits()
    deployed = deployed_groups()
    requested = raw_filters.get("logGroups") if isinstance(raw_filters, dict) else None
    if isinstance(requested, str):
        requested = [requested]

    if requested:
        # Explicit groups: each one must be deployed and allowed. One generic refusal either way; the
        # first refusal ends the check, so a request does not write one denial per group.
        if not isinstance(requested, list) or len(requested) > queryBuilder.MAX_VALUES_PER_FILTER:
            raise AuditSearchValidationError("logGroups:type")
        for key in requested:
            if not isinstance(key, str) or key not in deployed or not _group_allowed(enforcer, key):
                raise AuditSearchDenied()
        allowed_keys = list(requested)
    else:
        allowed_keys, deployed = authorized_group_keys(enforcer, PROBE_ENDPOINT_START)
        if not allowed_keys:
            raise AuditSearchDenied()

    try:
        filters = queryBuilder.validate_filters(raw_filters, allowed_keys, limits)
    except AuditSearchValidationError as error:
        if error.code in ("groupUnknown", "groupNotAllowed", "groupNone"):
            raise AuditSearchDenied()
        raise

    plan = queryBuilder.build_query(filters, limits)

    user_id = _user_id()
    _check_caps(reconcile_open_handles())
    _reserve_slots(user_id)
    return _start_reserved(event, filters, plan, deployed, user_id)


def _start_reserved(event: Dict[str, Any], filters: SearchFilters, plan: Any,
                    deployed: Dict[str, Tuple[AuditLogGroup, str]], user_id: str) -> Dict[str, Any]:
    """Start the Logs Insights query and store the handle. The caller holds this user's slots; they are
    given back here when no handle is stored (the handle's close gives them back otherwise)."""
    log_group_names = [deployed[key][1] for key in filters.group_keys]
    try:
        started = logs_client.start_query(
            logGroupNames=log_group_names, startTime=plan.start_time, endTime=plan.end_time,
            queryString=plan.query_string, limit=plan.limit)
    except BaseException:
        _give_back_user_slots(user_id)
        raise
    cwl_query_id = started["queryId"]

    query_id = str(uuid.uuid4())
    now = _now()
    item = {
        "queryId": query_id, "userId": user_id, SLOTS_COUNTED: True, "cwlQueryId": cwl_query_id,
        "logGroupKeys": list(filters.group_keys), "filters": filters_to_json(filters),
        "containsLength": sum(len(term) for term in filters.contains), "containsCount": len(filters.contains),
        "startTime": plan.start_time, "endTime": plan.end_time, "queryLimit": plan.limit,
        "prefilterComplete": plan.prefilter_complete, "warnings": list(plan.warnings),
        "status": STATUS_RUNNING, "openKey": OPEN_KEY_VALUE, "createdAt": _iso(now), "createdAtEpoch": now,
        "deadlineAt": now + deadline_seconds(), "expiresAt": now + deadline_seconds() + handle_retention_seconds(),
    }
    if filters.contains:
        item[TERMS_ATTRIBUTE] = list(filters.contains)
    try:
        query_table.put_item(Item=item, ConditionExpression="attribute_not_exists(queryId)")
    except Exception:
        _stop_cloudwatch_query(cwl_query_id)
        _give_back_user_slots(user_id)
        raise
    log_action(event, "auditQueryStart", entity_id=query_id, changes=_start_changes(filters),
               details={"queryLimit": plan.limit})
    return {"queryId": query_id, "status": STATUS_RUNNING, "warnings": list(plan.warnings)}


def _acquire_lease(query_id: str, until: int) -> bool:
    """One waiting poller per handle (conditional update of ``pollLeaseUntil``)."""
    try:
        query_table.update_item(
            Key={"queryId": query_id}, UpdateExpression="SET pollLeaseUntil = :until",
            ConditionExpression="attribute_exists(queryId) AND (attribute_not_exists(pollLeaseUntil) "
                                "OR pollLeaseUntil < :now)",
            ExpressionAttributeValues={":until": until, ":now": _now()})
        return True
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return False
        raise


def _release_lease(query_id: str) -> None:
    try:
        query_table.update_item(Key={"queryId": query_id}, UpdateExpression="REMOVE pollLeaseUntil",
                                ConditionExpression="attribute_exists(queryId)")
    except Exception:
        pass  # the lease expires by itself


def poll_results(item: Dict[str, Any], wait: int, context: Optional[LambdaContext]) -> Dict[str, Any]:
    """The current CloudWatch response of a handle, waiting up to ``wait`` seconds for a final status.

    Marks the handle ``Timeout`` and stops its query when it is past ``deadlineAt``. When the status is
    final the handle is closed (slot freed). Returns the ``get_query_results`` response with the
    VAMS status under ``vamsStatus``.
    """
    query_id = item["queryId"]
    cwl_query_id = item["cwlQueryId"]
    status = item.get("status", STATUS_RUNNING)
    if status not in (STATUS_RUNNING,):
        # A closed handle: the status is final, the results of a Complete one are read again.
        if status == STATUS_COMPLETE:
            response = logs_client.get_query_results(queryId=cwl_query_id)
            response["vamsStatus"] = STATUS_COMPLETE
            return response
        return {"results": [], "statistics": {}, "vamsStatus": status}

    now = _now()
    if int(item.get("deadlineAt", 0)) <= now:
        _stop_cloudwatch_query(cwl_query_id)
        _close_handle(query_id, STATUS_TIMEOUT)
        return {"results": [], "statistics": {}, "vamsStatus": STATUS_TIMEOUT}

    remaining = (context.get_remaining_time_in_millis() / 1000.0) if context is not None else 30.0
    wait = max(0, min(int(wait), long_poll_seconds(), int(remaining - LAMBDA_HEADROOM_SECONDS)))
    leased = wait > 0 and _acquire_lease(query_id, now + wait + 5)
    if not leased:
        wait = 0
    started = time.monotonic()
    try:
        while True:
            response = logs_client.get_query_results(queryId=cwl_query_id)
            vams_status = _CW_STATUS.get(response.get("status"), STATUS_FAILED)
            if vams_status != STATUS_RUNNING:
                break
            if time.monotonic() - started + POLL_INTERVAL_SECONDS > wait:
                break
            time.sleep(POLL_INTERVAL_SECONDS)
    finally:
        if leased:
            _release_lease(query_id)
    response["vamsStatus"] = vams_status
    if vams_status != STATUS_RUNNING:
        stats = _statistics(response)
        _close_handle(query_id, vams_status, row_count=len(response.get("results", [])),
                      bytes_scanned=stats["bytesScanned"])
    return response


def _page_body(item: Dict[str, Any], response: Dict[str, Any], rows_page: List[Dict[str, Any]],
               next_token: Optional[str], total: Optional[int], facts: Dict[str, Any],
               warnings: List[str]) -> Dict[str, Any]:
    statistics = _statistics(response)
    if total is not None:
        statistics["rowCount"] = total
        statistics["duplicatesRemoved"] = facts.get("duplicatesRemoved", 0)
    if facts.get("candidateLimitReached"):
        statistics["candidateLimitReached"] = True
    body: Dict[str, Any] = {
        "queryId": item["queryId"], "status": response["vamsStatus"], "statistics": statistics,
        "Items": rows_page, "warnings": warnings,
    }
    if next_token is not None:
        body["NextToken"] = next_token
    return body


def get_query(event: Dict[str, Any], context: Optional[LambdaContext], enforcer: CasbinEnforcer,
              request: GetAuditQueryRequestModel) -> Dict[str, Any]:
    """Long-poll a handle and return its status, statistics and (when complete) one page of rows."""
    item = _own_handle(request.queryId, enforcer)
    _touch_handle(item)
    response = poll_results(item, request.wait or 0, context)
    status = response["vamsStatus"]
    warnings = list(item.get("warnings", []))
    if status != STATUS_COMPLETE:
        log_action(event, "auditQueryStatus", entity_id=item["queryId"], details={"status": status})
        return _page_body(item, response, [], None, None, {}, warnings)

    filters = filters_from_item(item)
    rows, facts = build_rows(response.get("results", []), item, filters, context)
    if facts["recheckBudgetExceeded"]:
        warnings.append(WARNING_RECHECK_BUDGET)
    rows = sort_rows(rows, request.sort or filters.sort)
    offset = int(request.startingToken) if request.startingToken else 0
    page_size = request.pageSize or DEFAULT_PAGE_SIZE
    page: List[Dict[str, Any]] = []
    used = 0
    index = offset
    while index < len(rows) and len(page) < min(page_size, MAX_PAGE_SIZE):
        row = rows[index].to_dict()
        size = _json_size(row)
        if page and used + size > PAGE_BYTE_BUDGET:
            break
        page.append(row)
        used += size
        index += 1
    next_token = str(index) if index < len(rows) else None
    body = _page_body(item, response, page, next_token, len(rows), facts, warnings)
    if page:
        log_action(event, "auditQueryResults", entity_id=item["queryId"], item_count=len(page), bytes=used,
                   details={"bytesScanned": body["statistics"]["bytesScanned"], "status": status})
    else:
        log_action(event, "auditQueryStatus", entity_id=item["queryId"], details={"status": status})
    return body


def stop_query(event: Dict[str, Any], enforcer: CasbinEnforcer, query_id: str) -> Dict[str, Any]:
    item = _own_handle(query_id, enforcer)
    status = item.get("status", STATUS_RUNNING)
    if status == STATUS_RUNNING:
        _stop_cloudwatch_query(item.get("cwlQueryId", ""))
        _close_handle(query_id, STATUS_CANCELLED)
        status = STATUS_CANCELLED
    else:
        _remove_terms(query_id)  # a stopped or finished search needs no terms
    log_action(event, "auditQueryStop", entity_id=query_id)
    return {"queryId": query_id, "status": status}


def export_query(event: Dict[str, Any], context: Optional[LambdaContext], enforcer: CasbinEnforcer,
                 request: ExportAuditQueryRequestModel) -> Dict[str, Any]:
    """One export chunk of a completed search (at most ``EXPORT_CHUNK_BYTES`` of response body)."""
    item = _own_handle(request.queryId, enforcer)
    _touch_handle(item)
    if item.get("status") not in (STATUS_RUNNING, STATUS_COMPLETE):
        raise VAMSGeneralErrorResponse("The search has no results to export")
    response = poll_results(item, 0, context)
    if response["vamsStatus"] != STATUS_COMPLETE:
        raise VAMSGeneralErrorResponse("The search has not finished")
    filters = filters_from_item(item)
    rows, facts = build_rows(response.get("results", []), item, filters, context)
    warnings = list(item.get("warnings", []))
    if facts["recheckBudgetExceeded"]:
        warnings.append(WARNING_RECHECK_BUDGET)
    rows = sort_rows(rows, filters.sort)
    offset = int(request.startingToken) if request.startingToken else 0
    remaining = [row.to_dict() for row in rows[offset:]]

    # Take rows until the encoded chunk (as it travels inside the JSON body) would pass the limit.
    count = len(remaining)
    chunk_rows = remaining
    while True:
        chunk_rows = remaining[:count]
        encoded = encode_chunk(chunk_rows, request.format, first_chunk=(offset == 0))
        text = encoded.decode("utf-8")
        body = {"queryId": item["queryId"], "format": request.format, "content": text,
                "rowCount": len(chunk_rows), "warnings": warnings}
        size = _json_size(body)
        if size <= EXPORT_CHUNK_BYTES or count <= 1:
            break
        count = max(1, int(count * EXPORT_CHUNK_BYTES / size * 0.9))
    next_offset = offset + len(chunk_rows)
    if next_offset < len(rows):
        body["NextToken"] = str(next_offset)
    else:
        _remove_terms(item["queryId"])  # the export is complete: nothing is rechecked again
    log_action(event, "auditQueryExport", entity_id=item["queryId"], item_count=len(chunk_rows), bytes=size,
               details={"format": request.format})
    return body


def _action_options(allowed_keys: List[str]) -> List[Dict[str, Any]]:
    """The actions the caller can filter by, from the merged registry: those written to a group the
    caller may search. Add-on and fork actions appear without a code change."""
    specs = list(AUDIT_ROUTE_ACTIONS.values()) + list(AUDIT_SECONDARY_ACTIONS) + [
        system.spec for system in AUDIT_SYSTEM_ACTIONS]
    seen: Dict[str, Dict[str, Any]] = {}
    for audit_spec in specs:
        if audit_spec.group in allowed_keys and audit_spec.action not in seen:
            seen[audit_spec.action] = {"name": audit_spec.action, "kind": audit_spec.kind,
                                       "entityType": audit_spec.entity_type, "logGroup": audit_spec.group}
    return [seen[name] for name in sorted(seen)]


def list_log_groups(event: Dict[str, Any], enforcer: CasbinEnforcer) -> Dict[str, Any]:
    allowed, deployed = authorized_group_keys(enforcer, PROBE_ENDPOINT_LOG_GROUPS)
    groups = []
    for key in allowed:
        descriptor = deployed[key][0]
        groups.append({"key": descriptor.key, "label": descriptor.label, "prefix": descriptor.prefix,
                       "description": descriptor.description, "sensitivity": list(descriptor.sensitivity)})
    actions = _action_options(allowed)
    entity_types = sorted({option["entityType"] for option in actions if option.get("entityType")})
    limits = search_limits()
    wide, per_user = concurrency_caps()
    body = {
        "logGroups": groups, "actions": actions, "entityTypes": entity_types, "outcomes": list(OUTCOMES),
        "limits": {
            "maxQueryWindowDays": limits.max_window_days, "maxRows": limits.max_rows,
            "maxConcurrentQueries": wide, "maxConcurrentQueriesPerUser": per_user,
            "queryDeadlineSeconds": deadline_seconds(), "longPollWaitSeconds": long_poll_seconds(),
            "maxContainsTerms": limits.max_contains_terms,
            "maxContainsTermLength": limits.max_contains_term_length,
            "pageSize": DEFAULT_PAGE_SIZE, "maxPageSize": MAX_PAGE_SIZE,
        },
    }
    log_action(event, "auditLogGroupsList", item_count=len(groups))
    return body


#######################
# Request handlers
#######################

def _path_query_id(event: Dict[str, Any]) -> str:
    return (event.get("pathParameters") or {}).get("queryId") or ""


def _not_found(event: Dict[str, Any]) -> APIGatewayProxyResponseV2:
    return general_error(status_code=404, body={"message": MESSAGE_NOT_FOUND}, event=event)


def handle_get_request(event: Dict[str, Any], context: Optional[LambdaContext], enforcer: CasbinEnforcer,
                       path: str) -> APIGatewayProxyResponseV2:
    query_parameters = event.get("queryStringParameters") or {}
    try:
        if path.endswith("/log-groups"):
            return success(body=list_log_groups(event, enforcer))
        request_values = {**query_parameters, "queryId": _path_query_id(event)}
        if path.endswith("/export"):
            request = ExportAuditQueryRequestModel(**{k: v for k, v in request_values.items()
                                                      if k in ("queryId", "format", "startingToken")})
            if not validate_query_id(request.queryId):
                return _not_found(event)
            return success(body=export_query(event, context, enforcer, request))
        request = GetAuditQueryRequestModel(**{k: v for k, v in request_values.items()
                                               if k in ("queryId", "wait", "pageSize", "startingToken", "sort")})
        if not validate_query_id(request.queryId):
            return _not_found(event)
        return success(body=get_query(event, context, enforcer, request))
    except AuditSearchNotFound:
        return _not_found(event)


def handle_post_request(event: Dict[str, Any], enforcer: CasbinEnforcer) -> APIGatewayProxyResponseV2:
    body = event.get("body")
    if not body:
        return validation_error(body={"message": "Request body is required"}, event=event)
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except ValueError:
            return validation_error(body={"message": MESSAGE_INVALID}, event=event)
    try:
        model = StartAuditQueryRequestModel(filters=body)
        return success(status_code=202, body=start_query(event, enforcer, model.filters))
    except AuditSearchDenied:
        log_action(event, "auditQueryStart", outcome="denied", reason=REASON_FORBIDDEN)
        return authorization_error(event=event)
    except AuditSearchBusy:
        log_action(event, "auditQueryStart", outcome="failed", reason=REASON_BUSY)
        return validation_error(body={"message": MESSAGE_BUSY}, event=event)
    except AuditSearchValidationError as error:
        # The message names no value and no group; the code goes to the function log only.
        logger.info(f"Audit search refused: {error.code}")
        log_action(event, "auditQueryStart", outcome="failed", reason=REASON_VALIDATION)
        return validation_error(body={"message": MESSAGE_INVALID}, event=event)


def handle_delete_request(event: Dict[str, Any], enforcer: CasbinEnforcer) -> APIGatewayProxyResponseV2:
    try:
        request = StopAuditQueryRequestModel(queryId=_path_query_id(event))
        if not validate_query_id(request.queryId):
            return _not_found(event)
        return success(body=stop_query(event, enforcer, request.queryId))
    except AuditSearchNotFound:
        return _not_found(event)


@audited_handler
def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for the audit log search APIs."""
    global claims_and_roles
    claims_and_roles = request_to_claims(event)

    try:
        method = event['requestContext']['http']['method']
        path = event['requestContext']['http']['path']

        # Tier 1: the route. Pre-set false; an empty token list denies.
        enforcer = _tier1(event)
        if enforcer is None:
            return authorization_error()

        if method == 'GET':
            return handle_get_request(event, context, enforcer, path)
        elif method == 'POST':
            return handle_post_request(event, enforcer)
        elif method == 'DELETE':
            return handle_delete_request(event, enforcer)
        return validation_error(body={'message': "Method not allowed"}, event=event)

    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': validation_error_message(v)}, event=event)
    except VAMSGeneralErrorResponse as v:
        return general_error(body={'message': str(v)}, event=event)
    except Exception:
        # Counts and handle ids only; never the event, the filters or any row content.
        logger.exception("Internal error in the audit log service")
        return internal_error(event=event)
