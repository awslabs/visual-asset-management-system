#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Compliance Audit Service handler.

- GET /compliance/audit/{databaseId}/{assetId}  — audit history of an asset
- GET /compliance/audit                          — audit entries across assets, optionally by event type

Audit table (PK entryId; GSI AssetIndex on databaseId:assetId/timestamp; GSI EventTypeIndex on
eventType/timestamp; GSI AuditByDateGSI on the constant allListPartition/timestamp; GSI
DatabaseIndex on databaseId/timestamp). Every listing is one newest-first query on the index that
serves it, or for a caller who may read only some databases one DatabaseIndex query per database
merged newest first (`query_audit`), and pages externally with a Base64 NextToken.
An entry that names an asset is the caller's as the asset's compliance data is (`AssetAccess`): the
evaluation GET on its database, and, for a caller whose policy has asset rules, the asset GET on
the asset's row as stored; the entries of an archived asset are also decided with its stored
database id, `<databaseId>#deleted`. A finished
cascade's entries (`cascade_completed`, `cascade_auto_triggered`) count its archived descendants
and children only where the caller may see them, and a finished cascade's descendants recorded by
id as the cascade read counts each of them: by status where the caller may see it, in
`unauthorizedNodes` where the caller may not read it (its database's compliance, or the asset
itself). An entry written before the ids were recorded counts the descendants in other databases
than the parent's only where the caller may read that database and its assets, the others as
`unauthorizedNodes` (`cascadeAuditSummary.details_for_reader`), on every path that returns them.
"""

import base64
import json

import boto3
from aws_lambda_powertools.utilities.parser import ValidationError
from aws_lambda_powertools.utilities.typing import LambdaContext
from boto3.dynamodb.conditions import Attr, Key
from botocore.config import Config

from common.apiRoutes import API_COMPLIANCE_AUDIT, API_COMPLIANCE_AUDIT_ASSET
from common.compliance import databaseRetirement, knownDatabase, listing
from common.compliance.assetAccess import (
    ACCESS_ARCHIVED_HIDDEN, ACCESS_VISIBLE, AssetAccess, flush_denials,
)
from common.compliance.auditRecord import AUDIT_LIST_PARTITION, PRIVATE_AUDIT_ATTRIBUTES
from common.compliance.cascadeAuditSummary import (
    LIVE_HIDDEN, LIVE_SHOWN, LIVE_UNAUTHORIZED, details_for_reader, live_node_keys,
)
from common.resourceNames import ResourceKeys, get_table_name
from common.validators import validate
from common.compliance.routeAudit import (
    audit_outcome, failure_outcome, request_parameters, response_count,
)
from customLogging.auditLogging import audit_request, deferred_authorization_audit, log_action
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
# One database's entries, newest first (`databaseId`, `timestamp`; D173).
DATABASE_INDEX = "DatabaseIndex"
DATABASE_INDEX_KEY_ATTRIBUTES = ("entryId", "databaseId", "timestamp")
# The most databases a page across databases is merged from, one DatabaseIndex query each (a
# caller who may read more, but not every database, reads the global listing); also the most a
# merged page's token may name, which keeps the token a few kilobytes.
MAX_MERGED_DATABASES = 25
# The pages of database ids a page across databases lists before it treats them as too many to
# merge (each page is 1 MB of database rows).
MAX_DATABASE_SCAN_PAGES = 10
# The database id of the schema-level entries (`schema_deleted`).
SCHEMA_LEVEL_DATABASE_ID = "*"
# The field a merged page's token keeps each database's resume point under.
MERGE_TOKEN_FIELD = "databases"
# Which listing issued a token (`_token_kind`).
TOKEN_EVENT_TYPE = "event"
TOKEN_MERGE = "merge"
TOKEN_DATE = "date"
TOKEN_DATABASE = "database"
# The cache key of the evaluation GET on a database's stored id `<databaseId>#deleted`: the row
# decision of a listing and the archived counts of a finished cascade's entry ask it once per
# database of a request.
ARCHIVE_VISIBILITY_CACHE_KEY = "GET"

try:
    audit_table_name = get_table_name(ResourceKeys.COMPLIANCE_AUDIT_STORAGE_TABLE)
    asset_table_name = get_table_name(ResourceKeys.ASSET_STORAGE_TABLE)
    database_table_name = get_table_name(ResourceKeys.DATABASE_STORAGE_TABLE)
    evaluation_table_name = get_table_name(ResourceKeys.COMPLIANCE_EVALUATION_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed loading resource names")
    raise e

audit_table = dynamodb.Table(audit_table_name)
# Read for the archived status of an asset whose entries a caller may not see (keys only).
asset_table = dynamodb.Table(asset_table_name)
# Read for whether a listing's `databaseId` names a database (keys only).
database_table = dynamodb.Table(database_table_name)
# Read for the retirement record of a deleted database's compliance move only
# (`databaseRetirement`; GetItem on `databaseRetirement#` keys).
evaluation_table = dynamodb.Table(evaluation_table_name)


#######################
# Lambda handler
#######################

def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    # The request's audit context: its action records are written together when it ends.
    with audit_request(event, context):
        return _handle_request(event)


def _handle_request(event) -> APIGatewayProxyResponseV2:
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
            log_action(event, **failure_outcome(403))
            return authorization_error()

        if method == "GET":
            return handle_get_request(event)
        else:
            return validation_error(body={"message": "Method not allowed"}, event=event)

    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        log_action(event, **failure_outcome(400))
        return validation_error(body={"message": validation_error_message(v)}, event=event)
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        log_action(event, **failure_outcome(400))
        return general_error(body={"message": str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        log_action(event, **failure_outcome(500))
        return internal_error(event=event)
    finally:
        # The denials of this request's asset checks (`AssetAccess`).
        flush_denials()


#######################
# Method handlers
#######################

def handle_get_request(event):
    path = event["requestContext"]["http"]["path"]
    path_params = event.get("pathParameters", {}) or {}
    query_params = event.get("queryStringParameters", {}) or {}
    if API_COMPLIANCE_AUDIT_ASSET.matches(path):
        database_id = knownDatabase.decoded_scope(path_params.get("databaseId"))
        response = get_asset_audit(event, database_id, path_params.get("assetId"), query_params)
        log_action(event, "complianceAuditList", **audit_outcome(response),
                   database_id=database_id, asset_id=path_params.get("assetId"),
                   request_parameters=request_parameters(query_params, (
                       "startDate", "endDate", "maxItems", "limit", "startingToken")),
                   item_count=response_count(response, "entries"))
        return response
    if API_COMPLIANCE_AUDIT.matches(path):
        # The listing decides every database it may list once (D173): their refusals are written
        # together at the end of the request, not one CloudWatch call each.
        with deferred_authorization_audit():
            response = query_audit(event, query_params)
        log_action(event, "complianceAuditList", **audit_outcome(response),
                   request_parameters=request_parameters(query_params, (
                       "eventType", "startDate", "endDate", "databaseId", "assetId", "text",
                       "maxItems", "limit", "startingToken")),
                   item_count=response_count(response, "entries"))
        return response
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


def _archive_visibility(casbin_enforcer, archived, readable=None):
    """`asset_visible(database_id, asset_id)` for the caller: whether it may see an archived asset
    a cascade's entry counts (`AssetAccess.archived_asset_visible`: the evaluation GET on the
    stored id `<databaseId>#deleted`, asked once per database of the request and shared with the
    listing's row decision, and the asset GET on the archived row). A cascade's entries count the
    archived descendants and children only where this passes
    (`cascadeAuditSummary.details_for_reader`). `readable` is the request's evaluation GET by
    database id (`_database_readability`), when it has one."""
    decide = readable or (
        lambda stored_id: casbin_enforcer.enforce(_evaluation_object(stored_id), "GET"))

    def asset_visible(database_id, asset_id):
        return bool(casbin_enforcer) and archived.archived_asset_visible(
            database_id, asset_id, decide, cache_key=ARCHIVE_VISIBILITY_CACHE_KEY)
    return asset_visible


def _database_readability(casbin_enforcer):
    """`database_readable(database_id)` for the caller: whether it may read a database's compliance
    records (the evaluation GET on it, an object that names nothing but the database id), asked of
    the engine once per database id of the request (`listing.once_per_key`). The listing decides its
    rows with it, and a finished cascade's entry counts the results of its descendants in another
    database than the parent's only where it passes, and the rest as `unauthorizedNodes`
    (`cascadeAuditSummary.details_for_reader`)."""
    return listing.once_per_key(
        lambda database_id: bool(casbin_enforcer) and casbin_enforcer.enforce(
            _evaluation_object(database_id), "GET"))


def _descendant_readability(readable, access):
    """`database_readable(database_id)` for the descendants a finished cascade's entry counts per
    database (`cascadeAuditSummary.details_for_reader`): the evaluation GET on the database
    (`readable`) and, for a reader whose policy has asset rules, the asset GET on the database's
    assets (`AssetAccess.database_assets_readable`, once per database). The entry records those
    descendants as counts, without their ids, so they are decided per database: a reader refused a
    database's assets gets them counted in `unauthorizedNodes`."""
    return listing.once_per_key(
        lambda database_id: readable(database_id) and access.database_assets_readable(database_id))


def _live_descendant_outcome(casbin_enforcer, access, readable):
    """`live_outcome(database_id, asset_id)` for the descendants a finished cascade's entry records
    by id (`cascadeAuditSummary.details_for_reader`), the decision the cascade read makes on the same
    descendant (`complianceCascadeService._NodeVisibility.outcome`): `AssetAccess.access` in its
    listing form (the evaluation GET on its database, `readable`, asked once per database id, and,
    for a reader whose policy has asset rules, the asset GET on its row as stored, the rows of a
    page's entries read together, `_expect_descendants`). A descendant the reader may see is
    counted by status, one it may not read in `unauthorizedNodes`, and one archived since that it
    may not see is left out."""
    def live_outcome(database_id, asset_id):
        if not casbin_enforcer:
            return LIVE_UNAUTHORIZED
        decision = access.access(database_id, asset_id, readable,
                                 cache_key=ARCHIVE_VISIBILITY_CACHE_KEY, listing=True)
        if decision == ACCESS_VISIBLE:
            return LIVE_SHOWN
        if decision == ACCESS_ARCHIVED_HIDDEN:
            return LIVE_HIDDEN
        return LIVE_UNAUTHORIZED
    return live_outcome


def _expect_descendants(access, entries):
    """Note the descendants the finished cascades' entries among `entries` record by id, so that
    their rows are read together, 100 to a BatchGetItem, when a decision first needs one (a reader
    whose rules read an asset's own fields only: `AssetAccess.expect`)."""
    access.expect(key for entry in entries for key in live_node_keys(entry))


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


def entry_answer(entry):
    """An audit row as the listings return it: a copy without the attributes kept for the listing
    only (`auditRecord.PRIVATE_AUDIT_ATTRIBUTES`: the date index's partition and what the `text`
    filter matches), which stay stored."""
    return {name: value for name, value in entry.items() if name not in PRIVATE_AUDIT_ATTRIBUTES}


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
    (valid, message) = knownDatabase.validate_scope(database_id)
    if valid:
        (valid, message) = validate({"assetId": {"value": asset_id, "validator": "ASSET_ID"}})
    if not valid:
        return validation_error(body={"message": message}, event=event)
    start_date = params.get("startDate")
    end_date = params.get("endDate")
    (valid, message) = _validate_dates(start_date, end_date)
    if not valid:
        return validation_error(body={"message": message}, event=event)

    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error()
    casbin_enforcer = CasbinEnforcer(claims_and_roles)
    # An archived row of an earlier, deleted database with the same id is not the asset's.
    archived = AssetAccess(asset_table, casbin_enforcer,
                           databaseRetirement.Presenter(evaluation_table))
    if not archived.decide(
            database_id, asset_id, lambda stored_id: _enforce(stored_id, "GET")):
        return authorization_error()

    page_size, exclusive_start_key, error = _page_arguments(event, params, ASSET_INDEX_KEY_ATTRIBUTES)
    if error:
        return error
    # Every entry still under the live id of a deleted database whose move is running is the
    # deleted database's (`databaseRetirement`): none is the asset's of the live id.
    if databaseRetirement.move_in_progress(evaluation_table, database_id):
        return success(body={"entries": []})

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
    items = response.get("Items", [])
    evaluation_read = _database_readability(casbin_enforcer)
    visible = _archive_visibility(casbin_enforcer, archived)
    readable = _descendant_readability(evaluation_read, archived)
    _expect_descendants(archived, items)
    live = _live_descendant_outcome(casbin_enforcer, archived, evaluation_read)
    result = {"entries": [entry_answer(details_for_reader(entry, visible, readable, live))
                          for entry in items]}
    if "LastEvaluatedKey" in response:
        result["NextToken"] = _encode_token(response["LastEvaluatedKey"])
    return success(body=result)


def _audit_filter(database_id=None, text=None, event_type=None):
    """The FilterExpression of the listing's `databaseId`, `text` and `eventType` filters, or None.
    An entry written before `searchText` existed is matched on its asset id and actor. A filter never
    names a key of the index it is sent with: `databaseId` only on an index not keyed on it, and
    `eventType` only off EventTypeIndex."""
    conditions = []
    if database_id:
        conditions.append(Attr("databaseId").eq(database_id))
    if text:
        conditions.append(Attr("searchText").contains(text) | Attr("assetId").contains(text)
                          | Attr("actor").contains(text))
    if event_type:
        conditions.append(Attr("eventType").eq(event_type))
    if not conditions:
        return None
    expression = conditions[0]
    for condition in conditions[1:]:
        expression = expression & condition
    return expression


def _token_value(starting_token):
    """The JSON object a token decodes to, or None when it is not Base64 JSON of a non-empty object."""
    try:
        decoded = json.loads(base64.b64decode(starting_token, validate=True).decode("utf-8"))
    except (ValueError, TypeError):
        return None
    return decoded if isinstance(decoded, dict) and decoded else None


def _token_kind(token):
    """Which listing issued a decoded token: `event` (EventTypeIndex, `{"partition", "key"}`), `merge`
    (the databases a reader may read, `{"databases": ...}`), `date` (an AuditByDateGSI key) or
    `database` (a DatabaseIndex key). A token always continues on the index that issued it, so a
    token issued before an upgrade, or while DatabaseIndex was being built, stays valid."""
    if "partition" in token:
        return TOKEN_EVENT_TYPE
    if MERGE_TOKEN_FIELD in token:
        return TOKEN_MERGE
    if "allListPartition" in token:
        return TOKEN_DATE
    return TOKEN_DATABASE


def _merge_token(positions):
    """The token of a merged page: each database's resume point as `[timestamp, entryId]`, or null
    for a database none of whose entries were taken yet."""
    return _encode_token({MERGE_TOKEN_FIELD: {
        partition: None if key is None else [key["timestamp"], key["entryId"]]
        for partition, key in positions.items()}})


def _merge_positions(token):
    """`[(databaseId, start key | None)]` of a merged page's token, in database id order, or None
    when it is malformed or names more than MAX_MERGED_DATABASES databases."""
    named = token.get(MERGE_TOKEN_FIELD)
    if not isinstance(named, dict) or not named or len(named) > MAX_MERGED_DATABASES:
        return None
    positions = []
    for partition in sorted(named):
        value = named[partition]
        if partition != SCHEMA_LEVEL_DATABASE_ID and not knownDatabase.validate_scope(partition)[0]:
            return None
        if value is None:
            positions.append((partition, None))
            continue
        if (not isinstance(value, list) or len(value) != 2
                or not all(isinstance(part, str) and part for part in value)):
            return None
        positions.append((partition, {"entryId": value[1], "databaseId": partition,
                                      "timestamp": value[0]}))
    return positions


def _database_ids():
    """Every database row's id (live, and a deleted database's `<databaseId>#deleted`), or None when
    there are more than MAX_DATABASE_SCAN_PAGES pages of them. The database table is small and is
    scanned for its key alone, paged."""
    database_ids = []
    scan_kwargs = {"ProjectionExpression": "databaseId"}
    for _page in range(MAX_DATABASE_SCAN_PAGES):
        response = database_table.scan(**scan_kwargs)
        database_ids.extend(item["databaseId"] for item in response.get("Items", [])
                            if isinstance(item.get("databaseId"), str) and item["databaseId"])
        if "LastEvaluatedKey" not in response:
            return database_ids
        scan_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
    return None


def _audit_partitions(presenter):
    """Every `databaseId` an audit entry of a database can be stored under, as DatabaseIndex
    partitions, in id order: each database row's id (a live database, and a deleted one's history
    `<databaseId>#deleted`), the history of a database re-created since it was deleted (its
    retirement record's `deletedAt`), a live id whose deleted generation is still moving (its rows
    still carry the live id), and `*`, the schema-level entries. None when the databases are too many
    to list (`_database_ids`). Reads: the database table's keys and the retirement records of the
    database ids (`Presenter.prime`, one BatchGetItem per 100 ids)."""
    database_ids = _database_ids()
    if database_ids is None:
        return None
    base_ids = sorted({databaseRetirement.base_id(database_id) for database_id in database_ids})
    presenter.prime(base_ids)
    partitions = set(database_ids) | {SCHEMA_LEVEL_DATABASE_ID}
    for base in base_ids:
        if presenter.moving(base):
            partitions.add(base)
        if presenter.deleted_at(base):
            partitions.add(databaseRetirement.deleted_id(base))
    return sorted(partitions)


def _presented_id(presenter, partition):
    """The database id a partition's entries are decided on: `<databaseId>#deleted` for a live id
    whose deleted generation is still moving (`databaseRetirement.Presenter`), else the id itself."""
    return databaseRetirement.deleted_id(partition) if presenter.moving(partition) else partition


def _readable_partitions(presenter, readable):
    """`(partitions, every)`: the DatabaseIndex partitions the caller may read (the evaluation GET on
    each one's id, `readable`, asked once per id and shared with the rows' own decision), and whether
    that is every partition. `(None, True)` when the partitions are too many to list."""
    partitions = _audit_partitions(presenter)
    if partitions is None:
        return None, True
    allowed = [partition for partition in partitions
               if readable(_presented_id(presenter, partition))]
    return allowed, len(allowed) == len(partitions)


def query_audit(event, params):
    """Audit entries across assets, newest first, filtered and paged on the server.

    Query parameters: `eventType`, `databaseId`, `assetId` (with `databaseId`), `startDate` /
    `endDate` (ISO-8601), `text` (case-insensitive contains on the asset name at the time of the
    event, the asset id or the actor), `maxItems` (1..200) and `startingToken`.

    Access path — a key condition on one index, the other filters a FilterExpression:
    - `databaseId` + `assetId`: the asset's AssetIndex partition;
    - `databaseId`: that database's DatabaseIndex partition (`<databaseId>#deleted` for a deleted
      database's history), so other databases' entries are never read;
    - neither, for a caller allowed on every database: `eventType`'s EventTypeIndex partition (the
      token names it and carries its key), otherwise AuditByDateGSI, whose partition is the
      constant every audit row carries;
    - neither, for a caller who may read only some databases (at most MAX_MERGED_DATABASES): the
      DatabaseIndex partition of each of them, merged newest first (`listing.merge_query_page`; the
      token holds each database's resume point), so the databases it may not read take no place on
      a page. A caller who may read none gets an empty page without a token, and reads nothing;
      one who may read more than MAX_MERGED_DATABASES but not every database, or who is allowed on
      the empty-database object (an entry that names no database is in no DatabaseIndex
      partition), reads the global path.
    The partitions are each database row's id, the history of a re-created database and `*`
    (`_audit_partitions`). A token continues on the index that issued it. While DynamoDB still builds
    DatabaseIndex after an update, a first page reads the global path instead.
    The time window narrows each index's `timestamp` sort key. Each page is filled across at most
    `listing.DEFAULT_READ_BUDGET` reads (a merged page: one per database, then
    `DEFAULT_READ_BUDGET - 1` more) with the entries the caller may GET by databaseId
    (`listing.query_page`), the engine asked once per database id of the request; `NextToken`
    resumes after the last entry returned, or after the last entry read when the budget ended the
    page first. So a page is short or empty while `NextToken` is present only when the `text` or
    `eventType` filter, or the caller's asset rules, refuse most of the entries read, or on the
    global path for a reader who may not read some databases.
    Access to a `databaseId` (live or `<databaseId>#deleted`), and with `assetId` to the asset, is
    decided before any of it is read (D175): a caller who may read nothing there gets one empty
    page with no token, the same answer as for an id that names no database, and the request reads
    nothing of it. A `databaseId` that names no database, live or deleted, answers `400` "Database
    not found" to a caller allowed on that id (`knownDatabase.database_known`).
    """
    event_type = params.get("eventType") or None
    start_date = params.get("startDate")
    end_date = params.get("endDate")
    database_id = knownDatabase.decoded_scope(params.get("databaseId")) or None
    asset_id = params.get("assetId") or None
    text = listing.normalized_text(params.get("text")) or None
    (valid, message) = knownDatabase.validate_scope(database_id, optional=True)
    if not valid:
        return validation_error(body={"message": message}, event=event)
    (valid, message) = validate({
        "eventType": {"value": event_type, "validator": "STRING_256", "optional": True},
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
    starting_token = params.get("startingToken") or None
    token = _token_value(starting_token) if starting_token else None
    if starting_token and token is None:
        return validation_error(body={"message": INVALID_PAGINATION_TOKEN_MESSAGE}, event=event)

    casbin_enforcer = CasbinEnforcer(claims_and_roles) if len(claims_and_roles["tokens"]) > 0 else None
    # The evaluation GET names nothing but the database id, so the engine is asked it once per id of
    # the request (live, and `<databaseId>#deleted` for an archived asset), not once per entry: a
    # reader who may read few databases would otherwise have every other database's entries refused
    # one by one, each refusal writing an authorization audit record.
    readable = _database_readability(casbin_enforcer)
    presenter = databaseRetirement.Presenter(evaluation_table)
    # The entries of an archived asset are listed only for a caller allowed on its stored database
    # id; its archived status is read once per database, and only when that decides anything. An
    # archived row of an earlier, deleted database with the same id is told apart with the page's
    # retirement records.
    archived = AssetAccess(asset_table, casbin_enforcer, presenter)
    # Access to the named database is decided before any of it is read (D175): every entry under a
    # `databaseId` is decided first on that id (`AssetAccess.access`), so a caller refused on it
    # may read none of them. It gets one empty page with no token, the answer for an id that names
    # no database, and nothing is read: no audit entry, no database row, no retirement record.
    if database_id and not readable(database_id):
        return success(body={"entries": []})
    # A database filter naming no database is not an empty page (`knownDatabase`), for a caller
    # allowed on that id. A deleted database still names one, so its history stays readable.
    if database_id and not knownDatabase.database_known(database_table, database_id,
                                                        evaluation_table):
        return knownDatabase.database_not_found(event)
    # While a deleted database's compliance data moves under `<databaseId>#deleted`, every entry
    # still under the live id is the deleted database's: the live id lists none of them, and a
    # listing across databases shows them under `<databaseId>#deleted` (`databaseRetirement`).
    if database_id and databaseRetirement.move_in_progress(evaluation_table, database_id):
        return success(body={"entries": []})
    # Every entry of an asset's history gets the same decision, the one on the asset (its database
    # id and asset id, its row as stored): a caller refused on the asset gets the same empty page
    # before its history is read. The asset's row is read with at most one GetItem per key.
    if asset_id and not archived.decide(database_id, asset_id, readable,
                                        cache_key=ARCHIVE_VISIBILITY_CACHE_KEY):
        return success(body={"entries": []})

    def may_get(item):
        # List filtering appends only when enforce() passes, so empty tokens yield an empty list.
        item.update(presenter.present(item))
        return bool(casbin_enforcer) and archived.decide(
            item.get("databaseId") or "", item.get("assetId"), readable,
            cache_key=ARCHIVE_VISIBILITY_CACHE_KEY, listing=True)

    page = _AuditPage(page_size, start_date, end_date, text, event_type, accept=may_get,
                      # Each read's retirement records are read together, before any entry is
                      # presented (D133).
                      prepare=presenter.prepare(then=archived.expect_items()))
    answer = _route_page(page, token, starting_token, database_id, asset_id, event_type,
                         presenter, readable if casbin_enforcer else None)
    if answer is None:
        return validation_error(body={"message": INVALID_PAGINATION_TOKEN_MESSAGE}, event=event)
    entries, next_token = answer
    visible = _archive_visibility(casbin_enforcer, archived, readable)
    descendants = _descendant_readability(readable, archived)
    _expect_descendants(archived, entries)
    live = _live_descendant_outcome(casbin_enforcer, archived, readable)
    entries = [entry_answer(details_for_reader(entry, visible, descendants, live))
               for entry in entries]
    result = {"entries": entries}
    if next_token:
        result["NextToken"] = next_token
    return success(body=result)


class _AuditPage:
    """One audit listing request's page: its size, filters and row decision, read on one index
    (`single`) or merged from several databases' DatabaseIndex partitions (`merged`)."""

    def __init__(self, page_size, start_date, end_date, text, event_type, accept, prepare):
        self.page_size = page_size
        self.start_date, self.end_date = start_date, end_date
        self.text, self.event_type = text, event_type
        self.accept, self.prepare = accept, prepare

    def single(self, index_name, keys, key_condition, expression, start_key):
        """`(entries, next_key)` of one newest-first query on `index_name` (`listing.query_page`)."""
        query_kwargs = {
            "IndexName": index_name,
            "KeyConditionExpression": _timestamp_condition(key_condition, self.start_date,
                                                           self.end_date),
            "ScanIndexForward": False,
        }
        if expression is not None:
            query_kwargs["FilterExpression"] = expression
        entries, next_key, _ = listing.query_page(
            audit_table, query_kwargs, self.page_size, keys, start_key=start_key,
            filtered=expression is not None, accept=self.accept, prepare=self.prepare)
        return entries, next_key

    def merged(self, partitions):
        """`(entries, positions)` of the DatabaseIndex partitions `[(databaseId, start key | None)]`,
        merged newest first (`listing.merge_query_page`)."""
        expression = _audit_filter(text=self.text, event_type=self.event_type)

        def query_kwargs_for(partition):
            query_kwargs = {
                "IndexName": DATABASE_INDEX,
                "KeyConditionExpression": _timestamp_condition(
                    Key("databaseId").eq(partition), self.start_date, self.end_date),
                "ScanIndexForward": False,
            }
            if expression is not None:
                query_kwargs["FilterExpression"] = expression
            return query_kwargs

        entries, positions, _ = listing.merge_query_page(
            audit_table, partitions, query_kwargs_for, self.page_size,
            DATABASE_INDEX_KEY_ATTRIBUTES, "timestamp", filtered=expression is not None,
            accept=self.accept, prepare=self.prepare)
        return entries, positions


def _route_page(page, token, starting_token, database_id, asset_id, event_type, presenter,
                readable):
    """`(entries, NextToken | None)` of the request on the index that serves it (`query_audit`), or
    None when `startingToken` is not a token of that listing (a token naming another partition or
    another index's key never reaches DynamoDB)."""
    kind = _token_kind(token) if token else None
    if asset_id:
        start_key = None
        if starting_token:
            start_key = listing.decode_token(starting_token, ASSET_INDEX_KEY_ATTRIBUTES)
            if start_key is None:
                return None
        entries, next_key = page.single(
            "AssetIndex", ASSET_INDEX_KEY_ATTRIBUTES,
            Key("databaseId:assetId").eq(f"{database_id}:{asset_id}"),
            _audit_filter(text=page.text, event_type=event_type), start_key)
        return entries, _encode_token(next_key) if next_key else None

    if kind in (TOKEN_EVENT_TYPE, TOKEN_DATE):
        return _global_page(page, token, kind, database_id, event_type)
    if database_id:
        if kind == TOKEN_MERGE:
            return None
        start_key = None
        if kind == TOKEN_DATABASE:
            start_key = _index_key(token, DATABASE_INDEX_KEY_ATTRIBUTES)
            if start_key is None or start_key["databaseId"] != database_id:
                logger.info("Audit pagination token rejected: another database's key")
                return None
        try:
            entries, next_key = page.single(
                DATABASE_INDEX, DATABASE_INDEX_KEY_ATTRIBUTES, Key("databaseId").eq(database_id),
                _audit_filter(text=page.text, event_type=event_type), start_key)
        except Exception as error:
            if start_key is not None or not databaseRetirement.wait_index_not_ready(error):
                raise
            logger.info("DatabaseIndex is not readable yet; the database's entries are read from "
                        "the date-ordered trail")
            return _global_page(page, None, None, database_id, event_type)
        return entries, _encode_token(next_key) if next_key else None

    if kind == TOKEN_DATABASE:
        return None
    if kind == TOKEN_MERGE:
        positions = _merge_positions(token)
        if positions is None:
            return None
        # Every database the token names is decided again: one the caller may no longer read is
        # left out.
        positions = [(partition, start) for partition, start in positions
                     if readable is not None and readable(_presented_id(presenter, partition))]
        if not positions:
            return [], None
        entries, left = page.merged(positions)
        return entries, _merge_token(left) if left else None

    if readable is None:
        return [], None
    partitions, every = _readable_partitions(presenter, readable)
    # An entry that names no database is in no DatabaseIndex partition: a caller allowed on the
    # empty-database object reads the global listing, where it is listed for it.
    if every or len(partitions) > MAX_MERGED_DATABASES or readable(""):
        return _global_page(page, None, None, None, event_type)
    if not partitions:
        return [], None
    try:
        entries, left = page.merged([(partition, None) for partition in partitions])
    except Exception as error:
        if not databaseRetirement.wait_index_not_ready(error):
            raise
        logger.info("DatabaseIndex is not readable yet; the audit trail is read date-ordered")
        return _global_page(page, None, None, None, event_type)
    return entries, _merge_token(left) if left else None


def _global_page(page, token, kind, database_id, event_type):
    """`(entries, NextToken | None)` on EventTypeIndex (with `eventType`) or AuditByDateGSI, with
    `databaseId` as a filter when given (a token issued on these indexes, or a first page while
    DatabaseIndex is still being built), or None for a token of another listing."""
    if event_type:
        start_key = None
        if token is not None:
            # A token for the event-type listing is {"partition": <eventType>, "key": <key> | null}.
            # One naming another partition, or carrying a key that is not this index's, never
            # reaches DynamoDB.
            if kind != TOKEN_EVENT_TYPE or token.get("partition") != event_type:
                logger.info("Audit pagination token rejected: unknown partition")
                return None
            if token.get("key") is not None:
                start_key = _index_key(token.get("key"), EVENT_TYPE_INDEX_KEY_ATTRIBUTES)
                if start_key is None:
                    logger.info("Audit pagination token rejected: malformed key")
                    return None
        entries, next_key = page.single(
            "EventTypeIndex", EVENT_TYPE_INDEX_KEY_ATTRIBUTES, Key("eventType").eq(event_type),
            _audit_filter(database_id=database_id, text=page.text), start_key)
        if not next_key:
            return entries, None
        return entries, _encode_token({"partition": event_type, "key": next_key})
    start_key = None
    if token is not None:
        start_key = _index_key(token, DATE_INDEX_KEY_ATTRIBUTES) if kind == TOKEN_DATE else None
        if start_key is None:
            return None
    entries, next_key = page.single(
        "AuditByDateGSI", DATE_INDEX_KEY_ATTRIBUTES,
        Key("allListPartition").eq(AUDIT_LIST_PARTITION),
        _audit_filter(database_id=database_id, text=page.text), start_key)
    return entries, _encode_token(next_key) if next_key else None
