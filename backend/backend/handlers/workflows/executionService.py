#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import base64
import json
import os
import time
import boto3
import botocore
from datetime import datetime, timedelta, timezone
from weakref import WeakKeyDictionary
from boto3.dynamodb.conditions import Key, Attr
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from common.validators import validate, normalize_iso8601_utc
from common.resourceNames import get_table_name, ResourceKeys
from common.auth.apiEvent import normalize_event
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger
from customLogging.auditLogging import log_actions
from common.dynamodb import validate_pagination_info
from common.logRedaction import redact_log_text, redact_log_events
from common.workflows import executionRecords as er
from common.workflows import executionOutputs as eo
from common.apiRoutes import (
    API_WORKFLOW_EXECUTION_DETAILS,
    API_WORKFLOW_EXECUTION_DETAILS_METADATA,
    API_WORKFLOW_EXECUTION_LOGS,
    API_WORKFLOW_EXECUTION_RERUN,
    API_WORKFLOW_EXECUTION_PERMANENT,
    API_WORKFLOW_EXECUTIONS_GLOBAL,
)
from models.common import (
    APIGatewayProxyResponseV2,
    commonHeaders,
    internal_error,
    success,
    validation_error,
    authorization_error,
    general_error,
    VAMSGeneralErrorResponse,
    _json_default
)
from models.executions import (
    DetailMetadataPageRequestModel,
    ListExecutionsRequestModel,
    RerunExecutionRequestModel,
    PermanentDeleteRequestModel,
)

logger = safeLogger(service="ExecutionService")

# Claims/roles for the current request (set per-invocation in lambda_handler).
claims_and_roles = {}

# Partition-key suffix an archived asset's row is moved under (assetService.archive_asset). Archiving
# is reversible, so an archived asset is still authorized on its own row rather than on its database.
ARCHIVED_DATABASE_SUFFIX = "#deleted"

# Per-request memo of asset rows keyed by (databaseId, assetId), reset at each invocation. The global
# execution list authorizes every row against its input/output assets; many executions reference the
# same few assets, so caching collapses the repeated get_asset_details reads within one list request.
_asset_details_cache = {}

# Memo of Casbin decisions, held per ENFORCER rather than in one module-level dict. A list request
# evaluates the same rule over the same few entities once per ROW, so the memo collapses that to one
# evaluation per distinct entity.
#
# Scoping it to the enforcer is what makes the memo structurally incapable of crossing identities. An
# enforcer is constructed per request — one per page on the list paths, one per call on the
# single-execution paths — and carries the caller's policy snapshot, so a decision can only ever be
# reused by the very enforcer that computed it. A plain module-level dict is instead only as safe as
# every present and future caller remembering to clear it: one missed clear answers one caller's
# request with another caller's decisions, which on an authorization path is a fail-open.
#
# Weak keys let an entry go when its enforcer is collected. Keying on id(enforcer) would NOT be
# equivalent: CPython reuses id() values once an object is freed, so a new enforcer with different
# permissions could inherit a freed one's cached allows — an invisible fail-open rather than a
# test-visible one.
_authz_decision_cache = WeakKeyDictionary()

# Memo of workflow definition rows read for authorization, keyed by (databaseId, workflowId) and held
# per ENFORCER for the same reason the decision memo is: the row supplies the ABAC-visible workflow
# attributes, and an enforcer is built per request, so a row cannot decide a later request's check.
_workflow_definition_cache = WeakKeyDictionary()


def _claims_identity_key(claims):
    """The caller identity a Casbin decision depends on, as a hashable value.

    CasbinEnforcer resolves policy from the user id (`tokens[0]`) and `mfaEnabled` — MFA-gated roles are
    only active for an MFA session — and reads the roles themselves from DynamoDB. Both inputs are part
    of the key so an entry can never be shared across users or across MFA states, independently of the
    per-enforcer scoping above. `tokens` is sorted so an equal identity always yields an equal key."""
    claims = claims or {}
    return (tuple(sorted(claims.get("tokens", []) or [])), bool(claims.get("mfaEnabled", False)))


def _get_asset_details_cached(database_id, asset_id):
    """get_asset_details with per-request memoization (asset rows are stable within one request)."""
    key = (database_id, asset_id)
    if key not in _asset_details_cache:
        _asset_details_cache[key] = get_asset_details(database_id, asset_id)
    return _asset_details_cache[key]


def _enforce_cached(casbin_enforcer, obj, action):
    """`casbin_enforcer.enforce(obj, action)` behind this enforcer's decision memo.

    The caller identity leads the key, then the entity: workflowId sits alongside assetId because a
    workflow object carries no assetId, so a key without it would give two workflows in the same
    database one shared decision. Everything an ABAC rule can read about an entity is either one of
    these ids or an attribute of the single row the entity resolves to within this request (an asset
    resolves once through _asset_details_cache; a workflow or database object is built from its ids
    alone), so two objects sharing a key are the same object and the memo answers with the decision the
    enforcer would have computed — the rule enforced per row is unchanged."""
    memo = _authz_decision_cache.get(casbin_enforcer)
    if memo is None:
        memo = {}
        _authz_decision_cache[casbin_enforcer] = memo
    key = (_claims_identity_key(claims_and_roles),
           obj.get("object__type", ""), obj.get("databaseId", ""),
           obj.get("assetId", ""), obj.get("workflowId", ""), action)
    if key not in memo:
        memo[key] = casbin_enforcer.enforce(obj, action)
    return memo[key]


def _clean_validation_message(v):
    """Extract the human-readable message a request model's @root_validator raised,
    so client-facing validation errors stay identical to the prior handler text."""
    try:
        errors = v.errors()
        if errors and errors[0].get('msg'):
            return errors[0]['msg']
    except Exception:
        pass
    return str(v)

sfn = boto3.client('stepfunctions')
logs_client = boto3.client('logs')
# Used only to terminate a registered Batch job on abort (_terminate_batch_job_reporting).
batch_client = boto3.client('batch')
dynamodb = boto3.resource('dynamodb')

try:
    asset_storage_table_name = get_table_name(ResourceKeys.ASSET_STORAGE_TABLE)
    workflow_execution_database_v2 = get_table_name(ResourceKeys.WORKFLOW_EXECUTIONS_STORAGE_TABLE_V2)
    workflow_execution_inputs_table = get_table_name(ResourceKeys.WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE)
    pipeline_executions_table = get_table_name(ResourceKeys.PIPELINE_EXECUTIONS_STORAGE_TABLE)
    # Detail-assembly tables (read-only): per-pipeline input/output records, logs, and the
    # workflow/pipeline definition tables used to cross-fetch human-readable names/descriptions.
    workflow_execution_configuration_table = get_table_name(ResourceKeys.WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE)
    pipeline_execution_input_files_table = get_table_name(ResourceKeys.PIPELINE_EXECUTION_INPUT_FILES_STORAGE_TABLE)
    pipeline_execution_input_metadata_table = get_table_name(ResourceKeys.PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE)
    pipeline_execution_input_configuration_table = get_table_name(ResourceKeys.PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE)
    pipeline_execution_output_files_table = get_table_name(ResourceKeys.PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE)
    pipeline_execution_output_metadata_table = get_table_name(ResourceKeys.PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE)
    pipeline_execution_output_results_table = get_table_name(ResourceKeys.PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE)
    pipeline_execution_logs_table = get_table_name(ResourceKeys.PIPELINE_EXECUTION_LOGS_STORAGE_TABLE)
    workflow_database = get_table_name(ResourceKeys.WORKFLOW_STORAGE_TABLE_V2)
    pipeline_database = get_table_name(ResourceKeys.PIPELINE_STORAGE_TABLE_V2)
    # Index of 'executions that wrote to this asset', written at launch; removed here alongside the
    # execution's other rows on permanent delete.
    # Re-run delegates to the asset-less V2 execute handler (invoked as a lambda cross-call so the
    # caller's identity + a reconstructed execute body drive a fresh execution).
    execute_workflow_v2_function = os.environ.get("EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME", "")
    # Asset file version-history table, used to enrich asset-output files with the
    # authoritative S3 version each execution produced. Best-effort: unresolvable ->
    # the enrichment is skipped and outputs surface the relative path only.
    try:
        asset_file_version_history_table_name = get_table_name(
            ResourceKeys.ASSET_FILE_VERSION_HISTORY_STORAGE_TABLE)
    except Exception:
        asset_file_version_history_table_name = None
except Exception as e:
    logger.exception("Failed resolving resource names")
    raise e

lambda_client = boto3.client('lambda')

asset_table = dynamodb.Table(asset_storage_table_name)
asset_file_version_history_table = (
    dynamodb.Table(asset_file_version_history_table_name)
    if asset_file_version_history_table_name else None
)

# Upper bound on the number of distinct executions inspected per asset listing.
# Caps the DynamoDB main-row fetches + Step Functions describe_execution fan-out;
# older executions beyond this are surfaced via the NextToken continuation.
MAX_EXECUTIONS_INSPECTED = 200

# Default listing window: executions whose START date is on or after this many days before now.
# The caller can override the lower bound with an explicit `filterStartDate` query parameter.
DEFAULT_EXECUTION_LOOKBACK_DAYS = 90

# Upper bound on the global-list DynamoDB scan page size, so a single request cannot drive the
# per-candidate authorization fan-out (input/output asset reads + Casbin enforce) over an
# unbounded page. Excess is paged via NextToken.
MAX_GLOBAL_LIST_PAGE_SIZE = 100

# Keys per BatchGetItem request. 100 is the DynamoDB hard limit for a single BatchGetItem; a larger
# key set is split across sequential requests.
BATCH_GET_CHUNK_SIZE = 100

# Attempts spent re-requesting a BatchGetItem's UnprocessedKeys (throttling / a response that hit the
# 16 MB size limit) before the remainder falls back to per-item reads.
BATCH_GET_MAX_RETRIES = 3

# Base seconds for the exponential backoff between UnprocessedKeys retries.
BATCH_GET_RETRY_BACKOFF_SECONDS = 0.05

# Upper bound on the DISTINCT assets one global-list page resolves for authorization. The page size is
# already capped, but a page's cost is the number of assets its executions reference, not the number of
# rows: 100 executions over 100 assets each is 10,000 entity resolutions and the Casbin evaluations that
# follow. Beyond this bound the page stops resolving NEW assets, and a row whose assets are not all
# resolved is withheld rather than admitted on a weaker check — the listed rows are exactly the ones
# that passed the full rule. The bound is reported in the response `warnings`, so a shortened page is
# visible to the caller and continues from the same NextToken.
MAX_AUTHZ_ENTITIES_RESOLVED_PER_PAGE = 500

# Per-page distinct-entity budget. Empty (disarmed) for the single-execution paths — details, logs,
# abort and permanent delete each authorize ONE execution, so their cost is the run's own asset count
# and bounding it would deny access to a legitimately wide run. Armed only around a list page, where the
# cost is the whole page's.
_authz_entity_budget = {}


def _arm_authz_entity_budget(limit=MAX_AUTHZ_ENTITIES_RESOLVED_PER_PAGE):
    """Start a fresh per-page entity-resolution budget."""
    _authz_entity_budget.clear()
    _authz_entity_budget.update({"limit": limit, "exceeded": False})


def _disarm_authz_entity_budget():
    """Remove the budget, so the single-execution authorization paths are unbounded."""
    _authz_entity_budget.clear()


def _authz_entity_budget_exceeded():
    """True when the armed budget stopped a row from resolving its assets."""
    return bool(_authz_entity_budget.get("exceeded"))


def _authz_entities_within_budget(pairs):
    """True when resolving `pairs` keeps the armed page budget; always True when disarmed.

    Counted against the assets ALREADY resolved this request (`_asset_details_cache`), so a page over a
    few shared assets is never bounded however many rows reference them — only the breadth of distinct
    assets is. Flags the budget as exceeded when it refuses, so the caller reports the bound."""
    if not _authz_entity_budget:
        return True
    new = {pair for pair in pairs if all(pair) and pair not in _asset_details_cache}
    if len(_asset_details_cache) + len(new) > _authz_entity_budget["limit"]:
        _authz_entity_budget["exceeded"] = True
        return False
    return True


def _resolve_date_filter(query_params, param_name):
    """Validate and canonicalize a caller-supplied listing date bound, or None when absent.

    The value becomes a DynamoDB sort-key bound on executionStartDate, which is a lexicographic
    string compare rather than a date compare — an unvalidated value silently widens the window
    ('0' matches all history) or empties it ('9999' matches nothing) instead of erroring. Raises
    VAMSGeneralErrorResponse so the caller gets a 400."""
    raw = (query_params or {}).get(param_name)
    if raw is None or str(raw).strip() == "":
        return None
    candidate = str(raw).strip()
    (valid, message) = validate({param_name: {'value': candidate, 'validator': 'ISO8601_UTC'}})
    if not valid:
        logger.info(f"Rejected {param_name}: {message}")
        raise VAMSGeneralErrorResponse(
            f"{param_name} must be a UTC timestamp of the form YYYY-MM-DDTHH:MM:SSZ.")
    return normalize_iso8601_utc(candidate)


def _resolve_filter_start_date(query_params):
    """ISO-8601 lower bound on executionStartDate for the listing. Uses the caller's
    `filterStartDate` query parameter when provided; otherwise defaults to 90 days before now.
    Always returns a non-empty ISO-8601 string (the effective filter applied)."""
    supplied = _resolve_date_filter(query_params, 'filterStartDate')
    if supplied:
        return supplied
    cutoff = datetime.now(timezone.utc) - timedelta(days=DEFAULT_EXECUTION_LOOKBACK_DAYS)
    return cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

# Minimum seconds between Step Functions describe_execution polls for the same
# execution. A still-running execution is only re-polled if its stored
# executionStopDate is empty AND its lastSfnSyncCheckDate is older than this. This
# leans on what the table already holds (and on the end-state process-output lambda
# writing the stop date) to cut down on direct SFN calls, while still polling often
# enough to catch executions cancelled/aborted directly outside VAMS.
SFN_SYNC_MIN_INTERVAL_SECONDS = 30

# Step Functions terminal statuses that are NOT a successful completion. When a poll
# observes one of these, the listing also pulls error info + recent logs into the row.
NON_SUCCESS_TERMINAL_STATUSES = ("FAILED", "ABORTED", "TIMED_OUT")

# All Step Functions / VAMS terminal statuses. A pipeline or workflow row already in one
# of these is finished and is left untouched by an abort (only in-flight rows are aborted).
TERMINAL_STATUSES = ("SUCCEEDED", "FAILED", "ABORTED", "TIMED_OUT")

# Status written to the main row and to each still-running pipeline row by an abort.
ABORTED_STATUS = "ABORTED"

# Main-row attributes the listing's lazy reconcile can change (build_execution_items).
LIST_RECONCILED_MAIN_ROW_ATTRIBUTES = (
    "executionStatus", "executionStartDate", "executionStopDate", "lastSfnSyncCheckDate",
    "executionLog", "executionError",
)

# Main-row attributes the details view's lazy reconcile can change (_reconcile_main_status).
DETAIL_RECONCILED_MAIN_ROW_ATTRIBUTES = (
    "executionStatus", "executionStopDate", "lastSfnSyncCheckDate",
)

# Main-row attributes an abort writes.
ABORT_MAIN_ROW_ATTRIBUTES = (
    "executionStatus", "executionStopDate", "lastSfnSyncCheckDate",
)


def _persist_reconciled_main_row(table, main_item, attributes):
    """Write only the named attributes of a main execution row. The reconcile happens on read paths
    while the end-state lambda may be writing the same row, so a whole-item put would replace its
    attributes with the pre-completion snapshot the read started from."""
    reconciled = {attr: main_item[attr] for attr in attributes if attr in main_item}
    if not reconciled:
        return
    names = {f"#a{i}": attr for i, attr in enumerate(reconciled)}
    values = {f":v{i}": main_item[attr] for i, attr in enumerate(reconciled)}
    expr = "SET " + ", ".join(f"{n} = {v}" for n, v in zip(names, values))
    table.update_item(
        Key={"workflowExecutionId": main_item.get("workflowExecutionId", ""),
             "workflowDatabaseId:workflowId": main_item.get("workflowDatabaseId:workflowId", "")},
        UpdateExpression=expr,
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values)


def _fetch_execution_logs(log_group_arn, execution_id, limit_events=50):
    """Best-effort fetch of recent CloudWatch log events for ONE workflow execution.

    Returns the full recent execution log (not just errors). All workflow state machines
    log to a single shared group; events carry the execution name (== executionId) when
    includeExecutionData is enabled, so the read is scoped to this execution via a
    filterPattern. Returns the joined message text; '' on any failure or when logging is
    not configured (logs are non-critical)."""
    if not log_group_arn or not execution_id:
        return ""
    parts = log_group_arn.split(":log-group:")
    if len(parts) < 2:
        return ""
    log_group_name = parts[1]
    if log_group_name.endswith(":*"):
        log_group_name = log_group_name[:-2]
    try:
        resp = logs_client.filter_log_events(
            logGroupName=log_group_name,
            filterPattern=f'"{execution_id}"',
            limit=limit_events,
        )
        text = "\n".join(e.get('message', '') for e in resp.get('events', []))
        # Redact any inline credentials before the log text is stored or returned.
        text = redact_log_text(text)
        # Keep the stored log within DynamoDB item limits.
        text, _ = er.truncate_text(text, limit=er.MAX_LOG_FIELD_BYTES)
        return text
    except Exception as e:
        logger.info(f"Could not fetch CloudWatch logs (non-critical): {e}")
        return ""


def _decode_starting_token(starting_token):
    """Decode a base64 pagination token back into an ExclusiveStartKey, or None when it cannot be
    decoded (the caller returns a validation error rather than silently restarting at page 1)."""
    try:
        decoded = json.loads(base64.b64decode(starting_token).decode('utf-8'))
    except Exception as e:
        logger.exception(f"Invalid startingToken: {e}")
        return None
    return decoded if isinstance(decoded, dict) and decoded else None


def _split_asset_list_token(decoded):
    """Read an asset-list continuation into (inputs cursor, output cursor, drained, servedThrough).

    The asset listing walks two independent queries under one shared budget, so a continuation names
    a cursor for EACH direction plus whether the input side is drained — a cap reached through the
    output query has to be resumable on its own, and the output direction has to stay reachable once
    the input side is exhausted. A token naming none of these is the single-cursor input form.

    `servedThrough` is the oldest executionStartDate any earlier page already returned. Both GSIs are
    walked newest-first, so it is the high-water mark that lets the output query skip executions that
    were already served through the INPUT direction — the two queries are deduped only within one
    request, so without it a dual-role execution (an input for this asset AND its output target) is
    returned again on the page that first reaches the output side."""
    if not any(k in decoded for k in ('inputsKey', 'outputKey', 'inputsDone', 'servedThrough')):
        return decoded, None, False, '', ''
    inputs_key = decoded.get('inputsKey')
    output_key = decoded.get('outputKey')
    served_through = decoded.get('servedThrough') or ''
    served_through_id = decoded.get('servedThroughId') or ''
    return (inputs_key if isinstance(inputs_key, dict) and inputs_key else None,
            output_key if isinstance(output_key, dict) and output_key else None,
            bool(decoded.get('inputsDone')),
            served_through if isinstance(served_through, str) else '',
            served_through_id if isinstance(served_through_id, str) else '')


def _asset_list_input_row_key(input_item):
    """The ExclusiveStartKey that resumes WorkflowExecInputsByAssetGSI after this input row.

    A GSI continuation names both the index's own keys and the base table's, so a synthesized one
    carries all four. Returns None when the row is missing any of them, so a malformed row yields no
    cursor rather than one that resumes from the wrong place."""
    key = {
        'databaseId:assetId': input_item.get('databaseId:assetId'),
        'executionStartDate': input_item.get('executionStartDate'),
        'workflowExecutionId': input_item.get('workflowExecutionId'),
        'databaseId:assetId:inputAssetFileKey': input_item.get(
            'databaseId:assetId:inputAssetFileKey'),
    }
    return key if all(key.values()) else None


def _asset_list_output_row_key(cfg_item):
    """The ExclusiveStartKey that resumes WorkflowExecConfigByOutputAssetGSI after this row.

    Same four-key shape as the inputs cursor, over the configuration table's own keys."""
    key = {
        'outputDatabaseId:outputAssetId': cfg_item.get('outputDatabaseId:outputAssetId'),
        'executionStartDate': cfg_item.get('executionStartDate'),
        'workflowExecutionId': cfg_item.get('workflowExecutionId'),
        'recordType': cfg_item.get('recordType'),
    }
    return key if all(key.values()) else None


def get_asset_details(databaseId, assetId):
    """Get asset details from DynamoDB, including an ARCHIVED asset's row.

    Archiving an asset is reversible and moves its row to the `databaseId + '#deleted'` partition
    (assetService.archive_asset), so a query of the active partition alone reports an archived asset as
    absent. Authorization treats an asset it cannot resolve as permanently gone and falls back to the
    asset's DATABASE, which is a weaker check — so resolving the archived row keeps an archived asset
    authorized on its own attributes (assetName, assetType, tags) exactly as it was before archiving.
    Only a genuinely deleted asset, whose row exists in neither partition, reaches the fallback.

    Returns the row (active first, then archived) or None when the asset exists in neither."""
    try:
        for database_id in (databaseId, f"{databaseId}{ARCHIVED_DATABASE_SUFFIX}"):
            response = asset_table.query(
                KeyConditionExpression=Key('databaseId').eq(database_id) & Key('assetId').eq(assetId),
                ScanIndexForward=False
            )
            items = response.get('Items')
            if items:
                # The first (most recent) item.
                return items[0]
        return None
    except Exception as e:
        logger.exception(f"Error getting asset details: {e}")
        raise Exception(f"Error retrieving asset.")


def _batch_get_rows(table_name, keys):
    """BatchGetItem `keys` (a list of full primary-key dicts) from one table. Chunks at the DynamoDB
    per-call key limit and re-requests UnprocessedKeys — throttling, or a response that reached the
    16 MB size limit — with exponential backoff.

    Returns the rows that came back. Never raises: a failed or incomplete batch simply yields fewer
    rows, leaving the caller to resolve the remainder with its per-item read."""
    items = []
    for start in range(0, len(keys), BATCH_GET_CHUNK_SIZE):
        pending = keys[start:start + BATCH_GET_CHUNK_SIZE]
        attempt = 0
        while pending:
            try:
                response = dynamodb.batch_get_item(RequestItems={table_name: {'Keys': pending}})
            except Exception as e:
                logger.warning(f"Batch read of {table_name} failed "
                               f"(falling back to per-item reads): {e}")
                break
            items.extend(response.get('Responses', {}).get(table_name, []))
            pending = (response.get('UnprocessedKeys', {}).get(table_name, {}).get('Keys', []))
            if not pending:
                break
            attempt += 1
            if attempt > BATCH_GET_MAX_RETRIES:
                logger.info(f"Batch read of {table_name} left {len(pending)} keys unprocessed after "
                            f"{BATCH_GET_MAX_RETRIES} retries; reading them individually")
                break
            time.sleep(BATCH_GET_RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1)))
    return items


def prewarm_asset_details(pairs):
    """Resolve many asset rows in batched reads and populate `_asset_details_cache`, so the per-asset
    authorization loops that follow read from the memo instead of issuing one Query each.

    `pairs` is an iterable of (databaseId, assetId), which is the asset table's complete primary key —
    each pair addresses at most one item, so a batched read returns exactly what the per-item Query
    would have.

    Anything the batch does not resolve falls back to the single-row read, so the memo a caller sees is
    identical to what the one-at-a-time path would have produced: an asset that does not exist memoizes
    as None, which the authorization loops treat as a missing asset. The batch addresses the ACTIVE
    partition only, so an archived asset is a batch miss that the fall-back read then resolves from the
    archived partition — a miss must never be memoized as None here, or archiving an asset would
    downgrade its authorization to its database. Already-cached pairs are skipped, so a pair is never
    read twice. Returns every requested pair's row keyed by pair (None for an asset that exists in
    neither partition)."""
    requested = []
    wanted = []
    seen = set()
    for pair in pairs:
        database_id, asset_id = pair
        if not database_id or not asset_id or pair in seen:
            continue
        seen.add(pair)
        requested.append(pair)
        if pair not in _asset_details_cache:
            wanted.append(pair)

    # A single unresolved asset — the common case — is already one round-trip, so it goes straight to
    # the single-row read; batching starts to pay off from two.
    if len(wanted) > 1:
        wanted_keys = set(wanted)
        rows = _batch_get_rows(
            asset_storage_table_name,
            [{'databaseId': database_id, 'assetId': asset_id} for database_id, asset_id in wanted])
        for item in rows:
            pair = (item.get('databaseId', ''), item.get('assetId', ''))
            if pair in wanted_keys:
                _asset_details_cache[pair] = item

    # Single-row read for every pair the batch did not resolve, so a pair the caller asked for always
    # ends up in the memo (as a row, or as None for an asset that does not exist).
    for pair in wanted:
        if pair not in _asset_details_cache:
            _get_asset_details_cached(pair[0], pair[1])

    return {pair: _asset_details_cache.get(pair) for pair in requested}


def build_execution_items(input_items, fetch_main_row, describe_execution,
                          persist_main_row, workflow_id_filter, workflow_database_id,
                          fetch_execution_log_and_error=None):
    """Join WorkflowExecutionInputs rows with V2 main rows into the legacy wire
    shape. Dedupes by workflowExecutionId. Reconciles running status lazily.

    Polling policy (reduces Step Functions describe_execution calls):
      - If the row already has executionStopDate, it is terminal -> never poll.
      - Otherwise poll SFN only when lastSfnSyncCheckDate is unset or older than
        SFN_SYNC_MIN_INTERVAL_SECONDS. Every poll stamps lastSfnSyncCheckDate=now and
        persists, so rapid successive list calls do not each hit SFN. The end-state
        process-output lambda writes the stop date directly, so a successful execution
        is usually terminal in the table before the next poll window even opens.
      - The poll still happens once the window elapses, so executions cancelled/aborted
        directly in Step Functions (outside VAMS) are still detected.
      - When a poll observes a terminal status, pull the full execution log (and, for a
        non-success status, the error message) via fetch_execution_log_and_error and
        persist onto the row. The normal success path captures the log in the end-state
        process-output lambda; this covers terminations recorded out-of-band here.

    Callbacks (injected for testability):
      fetch_main_row(execution_id) -> main row dict or None
      describe_execution(arn) -> SFN describe_execution response or None
      persist_main_row(item) -> persist reconciled main row (no return)
      fetch_execution_log_and_error(execution_id, main_item, describe_response) ->
          (error_text, log_text). log_text is the full CloudWatch execution log (captured
          for any terminal status); error_text is the specific SFN error/cause message
          (empty unless the status is a non-success terminal). ('', '') if unavailable.
    """
    result_items = []
    # Preserve newest-first ordering from the GSI query; track the first input
    # file seen per execution for the legacy single-file wire field.
    seen = {}
    for input_item in input_items:
        execution_id = input_item.get('workflowExecutionId', '')
        if not execution_id or execution_id in seen:
            continue
        seen[execution_id] = input_item

    for execution_id, input_item in seen.items():
        main_item = fetch_main_row(execution_id)
        if not main_item:
            continue

        # Optional workflow filter
        if workflow_id_filter:
            expected = er.workflow_composite_key(workflow_database_id, workflow_id_filter)
            if main_item.get('workflowDatabaseId:workflowId', '') != expected:
                continue

        start_date = main_item.get('executionStartDate', '')
        stop_date = main_item.get('executionStopDate', '')
        status = main_item.get('executionStatus', '')
        execution_error = main_item.get('executionError', '')
        execution_log = main_item.get('executionLog', '')

        # Lazy reconciliation. Only poll Step Functions when the execution is not yet
        # terminal in the table (no stop date) AND we have not polled it within the
        # min sync interval. This favors the table (and the stop date the process-output
        # lambda writes) over direct SFN calls, while still catching out-of-band aborts.
        last_sync = main_item.get('lastSfnSyncCheckDate', '')
        if not stop_date and er.iso_seconds_since(last_sync) >= SFN_SYNC_MIN_INTERVAL_SECONDS:
            execution = describe_execution(main_item.get('workflow_execution_arn', ''))
            # Stamp the sync check time on every poll so a burst of list calls does not
            # each re-hit SFN; persist even when nothing else changed.
            main_item['lastSfnSyncCheckDate'] = er.iso_now()
            if execution:
                status = execution.get('status', status)
                sfn_stop = execution.get('stopDate')
                if sfn_stop:
                    stop_date = sfn_stop.strftime("%Y-%m-%dT%H:%M:%SZ") if hasattr(sfn_stop, "strftime") else str(sfn_stop)
                    sfn_start = execution.get('startDate')
                    if sfn_start and hasattr(sfn_start, "strftime"):
                        start_date = sfn_start.strftime("%Y-%m-%dT%H:%M:%SZ")
                    main_item['executionStartDate'] = start_date
                    main_item['executionStopDate'] = stop_date
                    main_item['executionStatus'] = status
                    # This poll observed a terminal status the end-state lambda did not
                    # record (e.g. a direct SFN cancel/abort). Capture the full execution
                    # log always, and the specific error message for non-success statuses.
                    if fetch_execution_log_and_error is not None:
                        err_text, log_text = fetch_execution_log_and_error(
                            execution_id, main_item, execution)
                        if log_text:
                            execution_log = log_text
                            main_item['executionLog'] = log_text
                        if status in NON_SUCCESS_TERMINAL_STATUSES and err_text:
                            execution_error = err_text
                            main_item['executionError'] = err_text
            persist_main_row(main_item)

        result_items.append({
            'workflowDatabaseId': main_item.get('workflowDatabaseId', ''),
            'workflowId': main_item.get('workflowId', ''),
            'workflowExecutionId': execution_id,
            'executionStatus': status,
            # triggerType + executionGroupId are surfaced so the asset-scoped board can apply the
            # same status/trigger/group filters as the global board. triggeredByUserId is the sub-line
            # of that board's Trigger column; it is already on this main row, so surfacing it costs
            # nothing and stops the column rendering half-empty on the asset tab.
            'triggerType': main_item.get('triggerType', ''),
            'triggeredByUserId': main_item.get('triggeredByUserId', ''),
            'executionGroupId': main_item.get('executionGroupId', ''),
            'startDate': start_date,
            'stopDate': stop_date,
            # Alias the dates under the same keys the global/workflow lists use, so the shared web
            # ExecutionsBoard (which reads executionStartDate/executionStopDate for the Started,
            # Stopped, Duration columns and sort) renders them on the asset Workflows tab too.
            'executionStartDate': start_date,
            'executionStopDate': stop_date,
            'inputAssetFileKey': input_item.get('inputAssetFileKey', ''),
            'databaseId': input_item.get('databaseId', ''),
            'assetId': input_item.get('assetId', ''),
            'executionError': redact_log_text(execution_error),
            'executionLog': redact_log_text(execution_log),
        })
    return result_items


def get_executions(event, database_id, asset_id, workflow_database_id, workflow_id, query_params):
    """List an asset's workflow executions (V2). Returns an API response.

    Enforces Tier 2 (GET on the requested asset) then, per execution, the same
    _execution_access_check rule the details/logs paths use — so a row this listing shows never 403s
    when it is opened. Resolves executions via the inputs GSI + V2 main rows, reconciles status, and
    returns
    `success(body={'message': {Items, filterStartDate, [filterEndDate], [NextToken], [warnings]}})`.
    The listing is lower-bounded by executionStartDate: the caller's `filterStartDate` query
    parameter, or 90 days before now by default; the applied value is echoed back as
    `filterStartDate`. A caller `filterEndDate` adds the upper bound and is echoed back when set.
    A `warnings` entry means the MAX_EXECUTIONS_INSPECTED cap withheld rows from this page.
    """
    asset_of_workflow = get_asset_details(database_id, asset_id)

    if not asset_of_workflow:
        return validation_error(status_code=404, body={'message': "Asset not found"}, event=event)

    # Add Casbin Enforcer to check if the current user has permissions to GET the asset (Tier 2)
    asset_of_workflow.update({
        "object__type": "asset"
    })

    asset_of_workflow_allowed = False

    if len(claims_and_roles["tokens"]) > 0:
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if casbin_enforcer.enforce(asset_of_workflow, "GET"):
            asset_of_workflow_allowed = True

    if asset_of_workflow_allowed:
        logger.info("Listing executions (V2)")
        inputs_table = dynamodb.Table(workflow_execution_inputs_table)
        main_table = dynamodb.Table(workflow_execution_database_v2)

        partition_key = f"{database_id}:{asset_id}"
        # Lower-bound the listing by executionStartDate: the caller's filterStartDate, or 90 days
        # before now by default. The inputs GSI is sorted by executionStartDate, so this is a
        # key-range bound that stops the query at the cutoff instead of paging through older rows.
        filter_start_date = _resolve_filter_start_date(query_params)
        filter_end_date = _resolve_date_filter(query_params, 'filterEndDate') or ""
        key_condition = Key('databaseId:assetId').eq(partition_key)
        if filter_end_date:
            key_condition = key_condition & Key('executionStartDate').between(
                filter_start_date, filter_end_date)
        else:
            key_condition = key_condition & Key('executionStartDate').gte(filter_start_date)
        query_kwargs = {
            'IndexName': 'WorkflowExecInputsByAssetGSI',
            'KeyConditionExpression': key_condition,
            'ScanIndexForward': False,
        }

        # Resume from a prior page if the caller supplied a continuation token. A token that cannot
        # be decoded is a caller error: continuing without it would silently serve page 1 again.
        resume_output_key = None
        inputs_drained = False
        # The oldest executionStartDate an earlier page already returned, and the execution at that
        # exact date that was served. Empty on the first page.
        served_through = ''
        served_through_id = ''
        starting_token = query_params.get('startingToken') if query_params else None
        if starting_token:
            decoded = _decode_starting_token(starting_token)
            if decoded is None:
                return validation_error(
                    body={'message': "startingToken is invalid."}, event=event)
            (resume_inputs_key, resume_output_key, inputs_drained,
             served_through, served_through_id) = _split_asset_list_token(decoded)
            # The cursor IS the last row served, so it supplies both halves of the high-water mark.
            if resume_inputs_key:
                query_kwargs['ExclusiveStartKey'] = resume_inputs_key
                if not served_through:
                    served_through = resume_inputs_key.get('executionStartDate', '')
                    served_through_id = resume_inputs_key.get('workflowExecutionId', '')

        # Page the asset's inputs GSI newest-first (sorted by executionStartDate),
        # deduping by workflowExecutionId as we go (first-seen wins = newest input
        # row for that execution). Stop once MAX_EXECUTIONS_INSPECTED distinct
        # executions are collected so the downstream main-row fetch + Step Functions
        # describe_execution fan-out stays bounded.
        #
        # Each direction carries its own resume point, and the input side records whether it ran to
        # exhaustion: the budget is spent across both queries, so a page that fills up on either one
        # has to say where BOTH stand or the unread direction becomes unreachable.
        deduped_inputs = {}
        # The caller's pageSize bounds the page, and MAX_EXECUTIONS_INSPECTED bounds the work; the
        # walk stops at whichever comes first. Capping HERE rather than slicing the finished list is
        # what keeps NextToken correct — the resume key is recorded against the last row actually
        # collected, so a smaller page defers the remainder instead of skipping it.
        try:
            requested_page_size = int(str(query_params.get('pageSize') or '0').strip())
        except (TypeError, ValueError):
            requested_page_size = 0
        inspect_cap = (min(MAX_EXECUTIONS_INSPECTED, requested_page_size)
                       if requested_page_size > 0 else MAX_EXECUTIONS_INSPECTED)
        bounded = False
        last_evaluated_key = None
        output_last_evaluated_key = None
        last_input_row_key = None
        # Declared with the other cursor state, not inside the output block: the token build below
        # reads it, and an input-side cap can skip that block entirely.
        last_output_row_key = None
        if inputs_drained:
            resp = {'Items': []}
        else:
            resp = inputs_table.query(**query_kwargs)
        while True:
            for input_item in resp.get('Items', []):
                execution_id = input_item.get('workflowExecutionId', '')
                if not execution_id or execution_id in deduped_inputs:
                    continue
                deduped_inputs[execution_id] = input_item
                last_input_row_key = _asset_list_input_row_key(input_item)
                if len(deduped_inputs) >= inspect_cap:
                    bounded = True
                    # Resume after the last row COLLECTED, not at the end of the DynamoDB page. The
                    # query reads a whole page (up to its Limit) while the cap can stop part-way
                    # through it, so `resp['LastEvaluatedKey']` points past rows this page never
                    # returned — resuming there skips them, and re-reading the page repeats the ones
                    # already served. The row-derived key is used whenever it is available, and the
                    # server's key only as the fallback for a page that yielded no new row.
                    last_evaluated_key = last_input_row_key or resp.get('LastEvaluatedKey')
                    # Advance the high-water mark to the row this page stops at. The GSI is walked
                    # newest-first, so the last row served is the oldest one served, and the output
                    # walk on a later page uses it to skip what the input direction already returned.
                    if last_input_row_key:
                        served_through = last_input_row_key.get('executionStartDate', '') or served_through
                        served_through_id = last_input_row_key.get('workflowExecutionId', '') or served_through_id
                    break
            if bounded or 'LastEvaluatedKey' not in resp:
                inputs_drained = inputs_drained or not bounded
                break
            query_kwargs['ExclusiveStartKey'] = resp['LastEvaluatedKey']
            resp = inputs_table.query(**query_kwargs)

        if bounded:
            logger.warning(
                "executionService inspected the most recent %d executions for asset %s; "
                "older executions were not listed", inspect_cap, asset_id)

        # An asset's history is the UNION of runs that read it and runs that wrote to it. The output
        # direction cannot be found through the inputs GSI at all: a results-only or
        # generate-from-nothing pipeline (inputFileArity 'none') writes no input rows, so its output
        # target is its only association with the asset.
        #
        # Queried second and merged, so an execution that is BOTH an input and the output target keeps
        # its input row (first-seen wins) and is listed once. Bounded by the same
        # MAX_EXECUTIONS_INSPECTED budget across both directions.
        if len(deduped_inputs) < inspect_cap:
            cfg_table = dynamodb.Table(workflow_execution_configuration_table)
            output_key_condition = Key('outputDatabaseId:outputAssetId').eq(
                er.output_asset_partition_key(database_id, asset_id))
            # An earlier page's oldest served row bounds this query from above. Both GSIs are walked
            # newest-first, so anything at or newer than that date has already been returned — through
            # the INPUT direction, which this query cannot see: dedupe is per-request, so a dual-role
            # execution (an input for this asset AND its output target) would otherwise be served
            # again on the page that first reaches the output side. Narrowing the range is what makes
            # the two independent walks behave as one ordered sequence across pages.
            output_upper_bound = filter_end_date
            if served_through and (not output_upper_bound or served_through < output_upper_bound):
                output_upper_bound = served_through
            if output_upper_bound:
                # `between` is inclusive, so a row at exactly the boundary date is still returned; the
                # per-row guard below drops it. Keeping the bound inclusive is deliberate — excluding
                # it would need a synthesized "just below" timestamp, and two executions can legitimately
                # share a start date, so an exclusive bound could skip a sibling that was never served.
                output_key_condition = output_key_condition & Key('executionStartDate').between(
                    filter_start_date, output_upper_bound)
            else:
                output_key_condition = output_key_condition & Key('executionStartDate').gte(
                    filter_start_date)
            output_kwargs = {
                'IndexName': 'WorkflowExecConfigByOutputAssetGSI',
                'KeyConditionExpression': output_key_condition,
                'ScanIndexForward': False,
            }
            if resume_output_key:
                output_kwargs['ExclusiveStartKey'] = resume_output_key
            try:
                out_resp = cfg_table.query(**output_kwargs)
                while True:
                    for cfg_item in out_resp.get('Items', []):
                        execution_id = cfg_item.get('workflowExecutionId', '')
                        last_output_row_key = (_asset_list_output_row_key(cfg_item)
                                              or last_output_row_key)
                        if not execution_id or execution_id in deduped_inputs:
                            continue
                        # The range bound above is inclusive, so rows at exactly the high-water date
                        # still arrive. Newer than it was already served. AT it, only the one execution
                        # the cursor names was served — two executions can share a start date, so
                        # dropping the whole date would lose a sibling that was never returned.
                        row_date = cfg_item.get('executionStartDate', '')
                        if served_through and (row_date > served_through
                                               or (row_date == served_through
                                                   and execution_id == served_through_id)):
                            continue
                        # A placeholder input row: this execution has no input file for the asset (it
                        # only wrote here), so the per-row input fields the response builder reads are
                        # absent by design rather than missing.
                        deduped_inputs[execution_id] = {
                            'workflowExecutionId': execution_id,
                            'databaseId': database_id,
                            'assetId': asset_id,
                            'executionStartDate': cfg_item.get('executionStartDate', ''),
                        }
                        if len(deduped_inputs) >= inspect_cap:
                            bounded = True
                            # Same precedence as the input direction: resume after the last row
                            # COLLECTED, since the cap can stop part-way through a query page and the
                            # server's key points past rows this page never returned.
                            output_last_evaluated_key = (last_output_row_key
                                                         or out_resp.get('LastEvaluatedKey'))
                            break
                    if bounded or 'LastEvaluatedKey' not in out_resp:
                        break
                    output_kwargs['ExclusiveStartKey'] = out_resp['LastEvaluatedKey']
                    out_resp = cfg_table.query(**output_kwargs)
            except Exception as e:
                # Best-effort: the input-matched executions are still returned. A missing index on an
                # un-migrated deployment must degrade the listing, not fail it.
                logger.warning(f"Could not list executions by output asset (non-fatal): {e}")

        # Memoized for the whole listing, so the authorization pass and the response builder below
        # share one read of each execution's main row rather than issuing two.
        main_row_memo = {}

        def _fetch_main_row(execution_id):
            if execution_id not in main_row_memo:
                r = main_table.query(
                    KeyConditionExpression=Key('workflowExecutionId').eq(execution_id),
                    ScanIndexForward=False,
                )
                rows = r.get('Items', [])
                main_row_memo[execution_id] = rows[0] if rows else None
            return main_row_memo[execution_id]

        def _describe(arn):
            if not arn:
                return None
            try:
                return sfn.describe_execution(executionArn=arn)
            except Exception as e:
                logger.exception(e)
                return None

        def _persist(item):
            # Persist the lazily-reconciled main row (status/dates/sync-time/log/error) to V2.
            _persist_reconciled_main_row(
                main_table, item, LIST_RECONCILED_MAIN_ROW_ATTRIBUTES)

        def _fetch_execution_log_and_error(execution_id, main_item, describe_response):
            """For a terminal execution, return (error_text, log_text).

            log_text is the full recent CloudWatch execution log scoped to this execution
            within the shared workflow log group (captured for any terminal status).
            error_text is the specific Step Functions error/cause message (the caller only
            stores it for non-success statuses). Best-effort: returns ('', '') on any
            failure (diagnostics are non-critical to the listing).

            Both land on the same main row, so they share one byte budget."""
            error_text = ""
            try:
                err = describe_response.get('error', '') if describe_response else ''
                cause = describe_response.get('cause', '') if describe_response else ''
                error_text = redact_log_text(": ".join(p for p in (err, cause) if p))
            except Exception as e:
                logger.info(f"Could not read SFN error fields (non-critical): {e}")
            log_text = _fetch_execution_logs(
                main_item.get('executionLogGroupArn', ''), execution_id)
            ((log_text, _log_truncated),
             (error_text, _error_truncated)) = er.truncate_text_budget(
                [log_text, error_text], total_limit=er.MAX_LOG_FIELD_BYTES)
            return error_text, log_text

        # Tier-2 once per deduped execution, evaluated by the same _execution_access_check rule the
        # details/logs paths use, so a row this tab lists cannot 403 when it is opened. Enforcing GET on
        # the REQUESTED asset alone (done above) is not enough: an execution that also read another asset
        # exposes that asset's data too, and the details path requires GET on every one of them.
        #
        # The workflow ids come from the input row when it carries them — authoritative-by-construction,
        # written at launch from the same workflow as the main row — and otherwise from the main row,
        # which the output-direction placeholder rows need because they are synthesized from the
        # configuration index and carry no workflow ids. Either way the read is the memoized one the
        # response builder also uses. One enforcer for the whole listing, and the configuration row is
        # read lazily per candidate, only after its workflow GET passes.
        authorized_inputs = []
        casbin_enforcer = CasbinEnforcer(claims_and_roles) if len(claims_and_roles["tokens"]) > 0 else None
        for input_item in deduped_inputs.values():
            if casbin_enforcer is None:
                continue
            candidate_id = input_item.get('workflowExecutionId', '')
            authorization_item = input_item
            if not input_item.get('workflowId', ''):
                authorization_item = _fetch_main_row(candidate_id) or input_item
            allowed, reason = _execution_access_check(
                candidate_id, authorization_item, "GET", casbin_enforcer=casbin_enforcer)
            if allowed:
                authorized_inputs.append(input_item)
            else:
                logger.info(f"Execution {candidate_id} is not listed for asset {asset_id}: {reason}")

        # Each direction (input-side, then output-target) is read newest-first, but they are read as
        # two separate queries and concatenated — so without this the output-only executions all trail
        # the input-side ones regardless of date, and an asset whose newest activity was a pipeline
        # writing INTO it shows that run below much older entries. Sorted here rather than in each
        # caller so the API, the web tab, and the CLI all get one chronological list.
        authorized_inputs.sort(key=lambda i: i.get('executionStartDate') or '', reverse=True)

        items = build_execution_items(
            input_items=authorized_inputs,
            fetch_main_row=_fetch_main_row,
            describe_execution=_describe,
            persist_main_row=_persist,
            workflow_id_filter=workflow_id or '',
            workflow_database_id=workflow_database_id or '',
            fetch_execution_log_and_error=_fetch_execution_log_and_error,
        )

        # Apply the optional equality filters (same semantics as the global board) so the asset
        # Executions tab's filters work.
        #
        # workflowId / workflowDatabaseId are accepted as QUERY parameters here in addition to the
        # `.../executions/{workflowId}` path form threaded through build_execution_items above. The
        # path form cannot be driven from a browser: its companion `workflowDatabaseId` is read from
        # a GET request BODY, which fetch/XHR cannot send. So a UI restricted to the path form could
        # only ever supply half the composite key.
        #
        # These are matched per FIELD (via _global_list_matches_filters) rather than against the
        # `workflowDatabaseId:workflowId` composite the path form builds. That difference is
        # load-bearing: the composite comparison treats a missing database as the empty string, so
        # `workflowId` alone yields the key ":wf1", which matches no row and returns an empty list
        # with no indication the filter was the cause. Per-field equality makes a database-less
        # workflow filter behave as asked instead.
        extra_filters = {
            "workflowId": (query_params.get("workflowId") or "").strip(),
            "workflowDatabaseId": (query_params.get("workflowDatabaseId") or "").strip(),
            "status": (query_params.get("status") or "").strip(),
            "triggerType": (query_params.get("triggerType") or "").strip(),
            "groupId": (query_params.get("groupId") or "").strip(),
            "triggeredByUserId": (query_params.get("triggeredByUserId") or "").strip(),
        }
        if any(extra_filters.values()):
            items = [it for it in items if _global_list_matches_filters(it, extra_filters)]

        # Surface the effective start-date filter that was applied (the caller's filterStartDate
        # or the default 90-days-before-now), so the response is self-describing. The upper bound is
        # echoed only when the caller supplied one — there is no default end date.
        result = {"Items": items, "filterStartDate": filter_start_date}
        if filter_end_date:
            result["filterEndDate"] = filter_end_date

        # Surface a continuation token when the candidate set was capped with more
        # rows available, so large assets are not silently cut off at the newest 200.
        #
        # The token names where EACH direction stands: the input cursor while that query still has
        # rows, then `inputsDone` plus the output cursor once it is drained. A cap reached through the
        # output query is therefore just as continuable as one reached through the inputs — and an
        # output-only execution (inputFileArity 'none') stays reachable, since the output direction is
        # walked in sequence rather than restarted from the newest row on every page.
        if bounded:
            token_payload = {}
            if inputs_drained:
                token_payload['inputsDone'] = True
            elif last_evaluated_key:
                token_payload['inputsKey'] = last_evaluated_key
            # Carried forward explicitly. Once the inputs drain there is no inputsKey to derive it
            # from, yet the output walk still needs to know which executions earlier pages returned
            # through the input direction — without it, the page that first reaches the output side
            # re-serves every dual-role execution.
            if served_through:
                token_payload['servedThrough'] = served_through
                if served_through_id:
                    token_payload['servedThroughId'] = served_through_id
            if output_last_evaluated_key:
                token_payload['outputKey'] = output_last_evaluated_key
            if 'inputsKey' in token_payload or 'outputKey' in token_payload:
                result["NextToken"] = base64.b64encode(
                    json.dumps(token_payload).encode('utf-8')).decode('utf-8')
            # Named so a capped page reads as a stated bound rather than the end of the asset's
            # history. The advice matches what the response offers: a token is present whenever the
            # walk can continue, and its absence means this really is the end.
            continuation = ("continue with NextToken to see the rest"
                            if "NextToken" in result else
                            "narrow the date range to see the rest")
            # Only the WORK budget is a withheld-rows condition worth warning about. A page bounded
            # by the caller's own pageSize is an ordinary page: it carries NextToken and warning-free
            # output, so a client paging normally is not told its request hit a limit.
            if inspect_cap >= MAX_EXECUTIONS_INSPECTED:
                result["warnings"] = [
                    f"This page reached the limit of {MAX_EXECUTIONS_INSPECTED} executions inspected "
                    f"for this asset, so older executions are not listed. Narrow the filters or "
                    f"{continuation}."]

        return success(body={'message': result})
    else:
        return authorization_error(body={'message': "Not Authorized"})


def get_execution_main_row(execution_id):
    """Fetch the single V2 main execution row by workflowExecutionId (PK), or None."""
    main_table = dynamodb.Table(workflow_execution_database_v2)
    resp = main_table.query(
        KeyConditionExpression=Key('workflowExecutionId').eq(execution_id),
        ScanIndexForward=False,
    )
    rows = resp.get('Items', [])
    return rows[0] if rows else None


def get_pipeline_execution_rows(execution_id):
    """All PipelineExecutions rows for an execution, via PipelineExecByWorkflowExecGSI
    (PK workflowExecutionId). Returns a list (possibly empty)."""
    pexec_table = dynamodb.Table(pipeline_executions_table)
    items = []
    query_kwargs = {
        'IndexName': 'PipelineExecByWorkflowExecGSI',
        'KeyConditionExpression': Key('workflowExecutionId').eq(execution_id),
    }
    resp = pexec_table.query(**query_kwargs)
    while True:
        items.extend(resp.get('Items', []))
        if 'LastEvaluatedKey' not in resp:
            break
        query_kwargs['ExclusiveStartKey'] = resp['LastEvaluatedKey']
        resp = pexec_table.query(**query_kwargs)
    return items


def get_execution_input_assets(execution_id):
    """Distinct (databaseId, assetId) input-file assets tied to an execution.

    Reads WorkflowExecutionInputsStorageTable by PK workflowExecutionId (one row per
    input file). Returns a list of unique (databaseId, assetId) tuples so the abort
    can enforce asset-level POST permission on every asset the run touched."""
    inputs_table = dynamodb.Table(workflow_execution_inputs_table)
    seen = set()
    query_kwargs = {'KeyConditionExpression': Key('workflowExecutionId').eq(execution_id)}
    resp = inputs_table.query(**query_kwargs)
    while True:
        for row in resp.get('Items', []):
            pair = (row.get('databaseId', ''), row.get('assetId', ''))
            if all(pair):
                seen.add(pair)
        if 'LastEvaluatedKey' not in resp:
            break
        query_kwargs['ExclusiveStartKey'] = resp['LastEvaluatedKey']
        resp = inputs_table.query(**query_kwargs)
    return list(seen)


def _stop_sfn_execution(execution_arn):
    """Best-effort Step Functions StopExecution. Returns True if a running execution
    was stopped (or was already gone); False only on an unexpected error. A missing
    or already-stopped execution is not an error for abort purposes."""
    if not execution_arn:
        return False
    try:
        sfn.stop_execution(executionArn=execution_arn)
        return True
    except sfn.exceptions.ExecutionDoesNotExist:
        logger.info(f"Execution already gone (nothing to stop): {execution_arn}")
        return False
    except botocore.exceptions.ClientError as e:
        # ExecutionAlreadyStopped-style errors are benign for abort; log and move on.
        logger.info(f"Could not stop execution {execution_arn} (continuing): {e}")
        return False


def _stop_sfn_execution_reporting(execution_arn):
    """Best-effort Step Functions StopExecution for a registered sub-execution. Returns
    (ok, reason); a missing/already-stopped execution is ok, a real failure (e.g. AccessDenied)
    returns its error code as reason for the caller to surface as a warning."""
    if not execution_arn:
        return True, ""
    try:
        sfn.stop_execution(executionArn=execution_arn)
        return True, ""
    except sfn.exceptions.ExecutionDoesNotExist:
        return True, ""
    except botocore.exceptions.ClientError as e:
        code = e.response.get('Error', {}).get('Code', '')
        # Already-stopped is benign; anything else is a real warning.
        if code in ('ExecutionAlreadyStopped', 'ExecutionLimitExceeded'):
            return True, ""
        logger.warning(f"Could not stop registered sub-execution {execution_arn}: {e}")
        return False, code or str(e)
    except Exception as e:
        logger.warning(f"Could not stop registered sub-execution {execution_arn}: {e}")
        return False, str(e)


def _terminate_batch_job_reporting(job_id):
    """Best-effort AWS Batch TerminateJob for a registered job. Returns (ok, reason).

    A job already in a terminal state is accepted rather than reported: TerminateJob is a no-op on a
    finished job, and an abort racing a job that just completed is normal, not a failure. A real
    failure (a missing permission, say) returns its error code so the caller can surface a warning
    naming what was left running.
    """
    if not job_id:
        return True, ""
    try:
        batch_client.terminate_job(jobId=job_id, reason="Aborted by VAMS execution abort")
        return True, ""
    except botocore.exceptions.ClientError as e:
        code = e.response.get('Error', {}).get('Code', '')
        logger.warning(f"Could not terminate registered Batch job {job_id}: {e}")
        return False, code or str(e)
    except Exception as e:
        logger.warning(f"Could not terminate registered Batch job {job_id}: {e}")
        return False, str(e)


# Sub-process resource types the abort path can stop today (mirrors registerPipelineExecution).
# Other registered types are tracked but not yet abortable.
RESOURCE_TYPE_STEP_FUNCTIONS_EXECUTION = "stepFunctionsExecution"
# An AWS Batch job a pipeline submitted ITSELF. A job submitted by a nested state machine through the
# Step Functions `.sync` integration needs no entry here: Step Functions owns that job's lifecycle, so
# stopping the sub-execution already terminates it.
RESOURCE_TYPE_BATCH_JOB = "batchJob"


def _abort_registered_sub_process(sub):
    """Best-effort abort of one registered sub-process. Dispatches on the entry's resourceType.

    Returns a non-fatal warning string when the sub-process could not be stopped (a real
    StopExecution failure, or a resource type not yet abortable), or "" when nothing needs to be
    surfaced (stopped cleanly, or no actionable locator). Never raises."""
    resource_type = sub.get("resourceType", "") or RESOURCE_TYPE_STEP_FUNCTIONS_EXECUTION
    if resource_type == RESOURCE_TYPE_STEP_FUNCTIONS_EXECUTION:
        execution_arn = sub.get("executionArn", "")
        if not execution_arn:
            return ""
        ok, err = _stop_sfn_execution_reporting(execution_arn)
        if not ok:
            return f"Sub-process abort failed for {execution_arn}: {err}"
        return ""
    if resource_type == RESOURCE_TYPE_BATCH_JOB:
        # Only pipelines that submit their Batch job THEMSELVES register it: when a nested state
        # machine submits through the Step Functions `.sync` integration, Step Functions owns the
        # job and stopping that execution already terminates it. A self-submitted job has no such
        # owner, so without this it would keep running (and billing) after an abort.
        job_id = sub.get("jobId", "") or sub.get("jobArn", "")
        if not job_id:
            return ""
        ok, err = _terminate_batch_job_reporting(job_id)
        if not ok:
            return f"Sub-process abort failed for Batch job {job_id}: {err}"
        return ""
    # Not yet abortable (e.g. batchJob, ecsTask). Surface what was left running.
    locator = (sub.get("executionArn") or sub.get("jobArn") or sub.get("jobId")
               or sub.get("taskArn") or sub.get("arn") or resource_type)
    logger.info(f"Registered sub-process of type '{resource_type}' is not yet abortable: {locator}")
    return (f"Sub-process of type '{resource_type}' ({locator}) could not be aborted: "
            f"abort for this resource type is not yet supported; it may still be running.")


def _metadata_source_entities(config_row):
    """The entities an execution captured metadata from, from its configuration row:
    ([databaseId, ...], [(databaseId, assetId), ...]).

    The databases are the ones the caller NAMED as a metadata source (`inputMetadataDatabaseId`), which
    is the deliberate act that makes a database's own metadata part of the run. A database merely
    DERIVED from an input file's asset is deliberately excluded: reading such an execution already
    requires GET on that input asset, and gating additionally on its database would narrow every
    ordinary execution to callers holding database-level GET — `databaseMetadata` defaults on, so every
    run records its input databases. `metadataSourceDatabases` (all captured databases) drives the
    detail view, not this gate. Metadata sources are not input FILES, so they appear only here — never
    in the workflow-input rows — and a row written before metadata sources existed carries none of
    these attributes, yielding empty values."""
    row = config_row or {}
    databases = []
    seen_databases = set()
    for database_id in [row.get('inputMetadataDatabaseId', '') or '']:
        if database_id and database_id not in seen_databases:
            seen_databases.add(database_id)
            databases.append(database_id)
    assets = []
    seen = set()
    for source in row.get('metadataSourceAssets') or []:
        pair = ((source or {}).get('databaseId', ''), (source or {}).get('assetId', ''))
        if all(pair) and pair not in seen:
            seen.add(pair)
            assets.append(pair)
    return databases, assets


def _read_assets_of_execution(input_assets, metadata_source_assets):
    """The distinct assets an execution read: its input-file assets plus the assets named purely as
    metadata sources. An asset that is both appears once, in input-file order."""
    merged = list(input_assets)
    seen = set(merged)
    for pair in metadata_source_assets:
        if pair not in seen:
            seen.add(pair)
            merged.append(pair)
    return merged


def _execution_workflow_casbin_object(casbin_enforcer, workflow_database_id, workflow_id):
    """The workflow Casbin object for a run's workflow, shaped exactly as the workflow routes shape
    theirs (workflowService._workflow_casbin_object): every attribute of the definition row rides along
    so the ABAC-visible `category` and `name` are present, with `name` falling back to `workflowName`.
    A rule scoped on those fields therefore decides an execution the same way it decides the workflow.

    The ids from the execution's own row stay authoritative, so a definition that no longer resolves
    (the workflow was deleted while its runs remain) is still authorized on the ids alone.

    The definition read is memoized per ENFORCER, keyed by (databaseId, workflowId): a list page
    authorizes every row against its workflow and many rows share one, so the page pays one read per
    distinct workflow. An enforcer is built per request, so the memo cannot carry a row — and the
    attributes an ABAC rule reads from it — across requests."""
    memo = _workflow_definition_cache.get(casbin_enforcer)
    if memo is None:
        memo = {}
        _workflow_definition_cache[casbin_enforcer] = memo
    key = (workflow_database_id, workflow_id)
    if key not in memo:
        # A definition that cannot be read contributes no attributes rather than failing the check: the
        # identity fields below still gate the object, so an ABAC rule written on them keeps working and
        # one on category/name simply does not match.
        try:
            memo[key] = get_workflow_definition(workflow_database_id, workflow_id) or {}
        except Exception:
            logger.exception(
                "Could not read the workflow definition for an execution authorization object")
            memo[key] = {}
    definition = memo[key]
    obj = dict(definition)
    obj.update({
        "object__type": "workflow",
        "workflowId": workflow_id,
        "databaseId": workflow_database_id,
    })
    obj.setdefault("name", definition.get("workflowName", ""))
    return obj


def _execution_access_check(execution_id, main_item, asset_action, config_row=None,
                            config_row_loader=None, casbin_enforcer=None):
    """The Tier-2 rule over the entities an execution actually read or wrote: workflow GET, GET on EVERY
    database whose metadata was captured, `asset_action` on EVERY distinct asset the run read, and — for
    a run with no inputs of either kind — `asset_action` on the asset it wrote to.

    Every asset is required rather than any one of them: the read paths return the metadata of all of
    them, so a caller who can reach only some must not reach the execution. The assets a run read are its
    input-file assets plus the assets named purely as metadata sources. A run with no inputs at all is
    associated with the asset it wrote to and nothing else, so that asset is its only data-level gate; a
    results-only run with no inputs has none, leaving workflow GET as the sole control — which is what
    makes such a run readable at all.

    An asset that no longer resolves substitutes the DATABASE it lived in, under the same action, so a run
    outlives the asset it ran against instead of becoming unreachable to everyone.

    The workflow itself is never modified by these operations, so workflow access is always GET. The
    per-asset action varies by operation: an abort changes the run's effect on the assets (POST), while
    detail/log reads only require GET.

    Every read path — authorize_execution_access (details/logs/abort), _execution_visible_to_caller (the
    global list) and the per-asset listing (get_executions) — evaluates this one function, so a row any
    list shows cannot 403 when its details are opened.

    The configuration row is read LAZILY, only after workflow GET passes: a row the caller cannot see at
    all must not pay for a read. Every remaining check needs it (the metadata sources, the output asset,
    and the results-only fallback all live on it), so a candidate that clears workflow GET costs exactly
    one read. `config_row` supplies an already-read item; `config_row_loader` is a zero-argument callable
    used instead, so a caller that also needs the row for its own projection (the global list, which
    reports the output target) memoizes the same single read. `casbin_enforcer` may be passed in so a
    batch caller builds one enforcer for a whole page instead of one per row.

    Returns (allowed: bool, denied_reason: str); denied_reason is for logging only."""
    if len(claims_and_roles["tokens"]) == 0:
        return False, "no tokens"

    if casbin_enforcer is None:
        casbin_enforcer = CasbinEnforcer(claims_and_roles)

    # Workflow-level GET (the run's workflow; not modifying the workflow itself).
    workflow_obj = _execution_workflow_casbin_object(
        casbin_enforcer, main_item.get('workflowDatabaseId', ''), main_item.get('workflowId', ''))
    if not _enforce_cached(casbin_enforcer, workflow_obj, "GET"):
        return False, "workflow GET denied"

    if config_row is None:
        config_row = (config_row_loader() if config_row_loader is not None
                      else get_workflow_execution_configuration_row(execution_id))
    metadata_source_databases, metadata_source_assets = _metadata_source_entities(config_row)

    # The captured database metadata is returned by the read paths, so reading the execution needs GET
    # on every database it was captured from. A database is authorized on its ids alone, so no database
    # record is resolved.
    for database_id in metadata_source_databases:
        if not _enforce_cached(
                casbin_enforcer, {"object__type": "database", "databaseId": database_id}, "GET"):
            return False, f"metadata-source database GET denied ({database_id})"

    # asset_action on every distinct asset the run read (input files + metadata sources).
    input_assets = get_execution_input_assets(execution_id)
    read_assets = _read_assets_of_execution(input_assets, metadata_source_assets)
    output_database_id = config_row.get('outputDatabaseId', '')
    output_asset_id = config_row.get('outputAssetId', '')
    # The run's assets and its output asset resolve together in batched reads before any enforcement, so
    # a run over many assets costs a few round-trips rather than one per asset and the output asset joins
    # the same batch instead of trailing it with a read of its own. Every lookup below is then a memo hit,
    # and the memo is shared across a whole list page, so an asset several rows reference is read once.
    entity_pairs = read_assets + ([(output_database_id, output_asset_id)]
                                  if output_database_id and output_asset_id else [])
    # A list page resolves a bounded number of DISTINCT assets. Once that bound is reached the page stops
    # resolving new ones, and this row is DENIED rather than authorized on assets it never resolved: the
    # rule stays "every asset the run read must allow the action", so the rows the page does return are
    # exactly the rows that satisfied it. The caller is told the bound applied (response `warnings`) and
    # continues from the same NextToken.
    if not _authz_entities_within_budget(entity_pairs):
        return False, "per-page entity-resolution bound reached"
    prewarm_asset_details(entity_pairs)
    # An asset the run read may have been deleted since. Deleting an asset does not delete the
    # executions that ran against it, and those runs stay the record of what happened to it, so a
    # resolvable asset is authorized on the asset while a deleted one defers to its DATABASE. A database
    # is never removed — deleting one rewrites the record under a '#deleted' id — so its permissions
    # remain answerable for as long as the execution exists, which keeps the history of a deleted asset
    # reachable by whoever can read the database it lived in rather than by nobody.
    missing_asset_databases = set()
    for database_id, asset_id in read_assets:
        asset = _get_asset_details_cached(database_id, asset_id)
        if not asset:
            missing_asset_databases.add(database_id)
            continue
        asset.update({"object__type": "asset"})
        if not _enforce_cached(casbin_enforcer, asset, asset_action):
            return False, f"asset {asset_action} denied ({database_id}/{asset_id})"

    # No inputs of either kind: the output asset is the run's only data-level association, so it
    # carries the gate. Without an asset output there is nothing to gate on and workflow GET stands
    # alone. A deleted output asset defers to its database for the same reason an input asset does.
    if not input_assets and not metadata_source_assets:
        if output_database_id and output_asset_id:
            output_asset = _get_asset_details_cached(output_database_id, output_asset_id)
            if not output_asset:
                missing_asset_databases.add(output_database_id)
            else:
                output_asset.update({"object__type": "asset"})
                if not _enforce_cached(casbin_enforcer, output_asset, asset_action):
                    return False, (f"output asset {asset_action} denied "
                                   f"({output_database_id}/{output_asset_id})")

    # Each database whose asset the run can no longer resolve. Reaching here means every asset that DOES
    # resolve already authorized, so these are the run's only remaining data-level association.
    for database_id in sorted(missing_asset_databases):
        if not _enforce_cached(
                casbin_enforcer, {"object__type": "database", "databaseId": database_id},
                asset_action):
            return False, f"deleted asset's database {asset_action} denied ({database_id})"

    return True, ""


def authorize_execution_access(execution_id, main_item, asset_action, config_row=None):
    """Tier-2 authorization for an execution operation (details, logs, abort). `config_row` supplies an
    already-read configuration row so a caller that holds one does not pay for a second read.

    Returns (allowed: bool, denied_reason: str); the rule itself is _execution_access_check."""
    return _execution_access_check(execution_id, main_item, asset_action, config_row=config_row)


def authorize_abort(execution_id, main_item, config_row=None):
    """Abort authorization: workflow GET + POST on every asset the run read (and, for a run with no
    inputs, the asset it wrote to) — the abort changes the run's effect on those assets, so write
    access is required — plus GET on every captured metadata-source database."""
    return authorize_execution_access(execution_id, main_item, "POST", config_row=config_row)


def abort_execution(event, execution_id):
    """Abort a running workflow execution and reconcile the stored statuses.

    Order of operations:
      1. Resolve the V2 main row (404 if unknown).
      2. Authorize: workflow GET + POST on every asset the run read (403 if denied).
      3. Stop each still-running pipeline's registered sub-processes first (Step Functions
         executions are stopped; other resource types warn), then the main execution.
      4. Mark every non-terminal pipeline row ABORTED (with a stop date) and the main
         row ABORTED (with a stop date)."""
    main_item = get_execution_main_row(execution_id)
    if not main_item:
        return validation_error(status_code=404, body={'message': "Execution not found"}, event=event)

    allowed, reason = authorize_abort(execution_id, main_item)
    if not allowed:
        logger.info(f"Abort not authorized for execution {execution_id}: {reason}")
        return authorization_error()

    now = er.iso_now()
    main_table = dynamodb.Table(workflow_execution_database_v2)
    # Non-fatal warnings surfaced to the caller alongside the success response.
    warnings = []

    # 1) Abort still-running inner pipeline executions first, then mark their rows ABORTED.
    pipeline_rows = get_pipeline_execution_rows(execution_id)
    for prow in pipeline_rows:
        status = prow.get('executionStatus', '')
        if status in TERMINAL_STATUSES:
            continue  # already finished; leave as-is

        # Stop each registered sub-process (best-effort; a failure is surfaced as a warning).
        # Only Step Functions executions can be stopped today; other resource types (Batch jobs,
        # ECS tasks, ...) are registered but not yet abortable, so they surface a warning so the
        # caller knows the sub-process was left running.
        for sub in prow.get('registeredSubExecutions', []) or []:
            warning = _abort_registered_sub_process(sub or {})
            if warning:
                warnings.append(warning)

        # Status + stop date only, conditioned on the row not already being terminal: the pipeline
        # is still running, so a whole-item write would replace any registration or output the
        # pipeline recorded since this request read the row.
        eo.set_pipeline_status(
            dynamodb, pipeline_executions_table,
            prow.get('pipelineExecutionId', ''), prow.get('workflowExecutionId', ''),
            ABORTED_STATUS, stop_date=prow.get('executionStopDate') or now)

    # 2) Abort the main (outer) Step Functions execution.
    _stop_sfn_execution(main_item.get('workflow_execution_arn', ''))

    # 3) Mark the main row ABORTED (unless it already reached a terminal state).
    if main_item.get('executionStatus', '') not in TERMINAL_STATUSES:
        main_item['executionStatus'] = ABORTED_STATUS
        if not main_item.get('executionStopDate'):
            main_item['executionStopDate'] = now
        main_item['lastSfnSyncCheckDate'] = now
        _persist_reconciled_main_row(main_table, main_item, ABORT_MAIN_ROW_ATTRIBUTES)

    logger.info(f"Aborted execution {execution_id}")
    # AUDIT LOG: execution aborted — it stops a run mid-flight, so who stopped it is audit-worthy.
    log_actions(event, "workflowExecutionAbort", {
        "executionId": execution_id,
        "workflowId": main_item.get('workflowId', ''),
        "workflowDatabaseId": main_item.get('workflowDatabaseId', ''),
        "operation": "abort",
    })
    # Include a "warnings" list only when a best-effort sub-process abort failed.
    body = {'message': "Execution aborted"}
    if warnings:
        body['warnings'] = warnings
    return success(body=body)


# ---------------------------------------------------------------------------
# Execution details + logs (read APIs)
# ---------------------------------------------------------------------------

# Valid log retrieval modes for the logs API.
LOG_MODE_TRUNCATED = "truncated"
LOG_MODE_FULL = "full"

# Upper bound on CloudWatch events returned by a single full-search logs call.
MAX_LOG_EVENTS_PER_CALL = 1000

# Slack subtracted from an execution's recorded start when bounding a live log search, covering clock
# skew between the recorded start and the first emitted event.
LOG_SEARCH_WINDOW_MARGIN_MS = 5 * 60 * 1000

# Upper bound on registered sub-process logs / sub-executions read per logs request. A pipeline may
# register an unbounded number of these; each read is a CloudWatch/SFN API call, so a single logs GET
# must not fan out without limit. Excess entries are skipped and flagged in the response warnings.
MAX_REGISTERED_LOGS_INSPECTED = 20
MAX_REGISTERED_SUB_EXECUTIONS_INSPECTED = 20

# Upper bound on rows collected per sub-collection (output files/metadata/results, input files/metadata)
# in the execution-details view, so an output-heavy execution does not read without limit. This bounds
# the READ; what bounds the assembled response is MAX_DETAIL_COLLECTION_BYTES_RETURNED below, since a
# row count says nothing about how large each row is. A collection hitting the cap is flagged truncated.
MAX_DETAIL_ROWS_PER_COLLECTION = 2000

# Rows returned per input collection (inputFiles, inputMetadata, inputDatabaseMetadata) once the detail
# view is assembled. These rows are the heaviest in the response — a metadata row carries an arbitrary
# key/value map and an input-file row a full asset-relative path — so they are trimmed at this
# watermark, below the read cap above, rather than only when the read cap itself is reached. A trimmed
# collection is named in truncatedCollections, so a partial section is never returned unflagged.
MAX_DETAIL_INPUT_ROWS_RETURNED = 1000

# Byte budget for one returned metadata collection, measured over the serialized rows.
#
# A row count alone cannot bound the response: a metadata row carries an entity's whole captured map,
# which is itself bounded only per entity (MAX_METADATA_ENTRIES_PER_ENTITY entries /
# MAX_METADATA_BYTES_PER_ENTITY bytes in executeWorkflow), so the row cap admits wildly different
# payloads. Measured on real serialization, 1000 rows of 200 entries is 11.9 MB — roughly twice the
# 6 MB Lambda synchronous-response limit, which fails the whole request with a 502 instead of
# returning the flagged partial view the caps exist to produce. At the scale VAMS targets (thousands
# of files, hundreds of metadata entries each) that is reachable, not hypothetical.
#
# Per-collection ceiling, applied to whatever share of the response budget a collection is granted. It
# bounds any single collection on its own; the whole-response arithmetic below is what decides how much
# each one actually gets. Hitting it names the collection in truncatedCollections, the same signal the
# row caps raise, so a client cannot mistake a bounded view for a complete one.
MAX_DETAIL_COLLECTION_BYTES_RETURNED = 4 * 1024 * 1024

# Whole-response byte ceiling the detail view assembles within, under the 6 MB Lambda synchronous
# response limit. The remainder covers the envelope: the pipelines section (with each step's rendered
# config body) and the JSON structure around all of it.
#
# Distinct from MAX_DETAIL_ROWS_PER_COLLECTION, which bounds the READ (how many rows are fetched from
# DynamoDB) and says nothing about their size. This bounds what is RETURNED, in bytes.
#
# Counted in WIRE bytes (_wire_bytes), not in the bytes a row serializes to on its own: the response
# carries the body as a JSON *string*, so every quote and backslash in a row is escaped a second time
# on the way out. Measured on escape-heavy values that is a 1.4x difference and approaches 2x when a
# row is quote-dense, which is the difference between this ceiling and a payload the Lambda
# synchronous-response limit rejects with a 502 carrying no body — and therefore none of the
# truncatedCollections flags either.
DETAIL_RESPONSE_BYTE_CEILING = 5 * 1024 * 1024

# Hard ceiling on the assembled response as Lambda returns it — the whole envelope, not the sum of the
# collection budgets — under the 6 MB synchronous-response limit. The budget arithmetic above is
# per-collection and additive, so parts it does not charge for (the envelope's own keys, the separators
# between rows, a section another bound holds a share of) can still carry the total past the limit. This
# is measured on the finished payload and trimmed against, so what the caller receives is bounded by
# what was actually counted rather than by the sum of the estimates.
DETAIL_PAYLOAD_WIRE_CEILING = 5632 * 1024

# Order the response budget is allocated in: the file collections are served first and the metadata
# collections divide what is left. Files answer "what did this run read and write", which nothing else
# in the response supplies; a truncated metadata collection can be paged in full from the dedicated
# metadata route, while a truncated file collection has no such fallback.
#
# Priority is not exemption. When the file collections alone would breach the ceiling they are trimmed
# too and named in truncatedCollections: the 6 MB Lambda limit returns a 502 with no body at all, which
# is strictly worse than a correctly flagged partial. Files first, metadata second, both bounded.
DETAIL_FILE_BUDGET_COLLECTIONS = ("inputFiles", "outputs.files")

# Floor the metadata collections are granted regardless of how large the file collections are, so a
# file-heavy execution still shows some of the metadata it read rather than empty tables. The files are
# capped at the ceiling less this floor, so granting it never pushes the response over the ceiling.
MIN_DETAIL_METADATA_BYTES_RETURNED = 256 * 1024

# Share of the response ceiling the fixed section (the per-step entries and their rendered configuration
# bodies) may occupy before the collections divide the rest.
#
# The section is charged against the ceiling but is not itself bounded by a collection cap, and one part
# of it grows without limit: each step's inline configuration body is capped in the low hundreds of KB
# and a workflow may carry MAX_SPECIFIED_PIPELINES steps, so the bodies alone can exceed the whole
# response — a 502 with no body at all, reached before a single collection row is allocated and with
# nothing named in truncatedCollections. Holding this share shortens the bodies instead, which is
# recoverable: the step keeps renderedConfigLocation, the pointer to the fully rendered object.
MAX_DETAIL_FIXED_SECTION_BYTES = 2 * 1024 * 1024

# Floor an inline configuration body is kept at. A body shortened below this is dropped rather than
# reported as a fragment too short to read as configuration; either way the step is flagged truncated
# and keeps its renderedConfigLocation.
MIN_DETAIL_RENDERED_CONFIG_BYTES = 4 * 1024

# Rows per page of the paged detail-metadata read, and the cap a caller's pageSize is clamped to. The
# paged route returns ONE collection, so it has the whole response to itself and can carry far more
# rows per call than the detail view grants a collection sharing with everything else.
DEFAULT_DETAIL_METADATA_PAGE_SIZE = 100
MAX_DETAIL_METADATA_PAGE_SIZE = 500

# Scope discriminator on an input-metadata row (mirrors executionRecords.build_input_metadata_record).
# 'asset' rows describe an asset or one of its files; a 'database' row describes a metadata-source
# database's own metadata and carries no assetId, so it is reported as its own detail collection.
INPUT_METADATA_SCOPE_ASSET = "asset"
INPUT_METADATA_SCOPE_DATABASE = "database"

# Byte budget for one page of the paged detail-metadata read, measured over the serialized rows. Rows
# are added until the next one would cross it, and the page's NextToken then resumes at the first row
# left out — so unlike the detail view's trim, nothing is dropped, it is deferred to the next page.
MAX_DETAIL_METADATA_PAGE_BYTES = 4 * 1024 * 1024

# Upper bound on rows SCANNED (as opposed to returned) by one paged detail-metadata request. The
# collections are stored together and split by scope, so a request for the database-scope subset can
# read past a large number of asset-scope rows before filling a page. Reaching the bound ends the page
# early with a continuation token at the last row scanned, so the walk resumes without skipping rows.
MAX_DETAIL_METADATA_ROWS_SCANNED = 20000

# Rows requested per underlying DynamoDB query when the collection is a scope-filtered subset. `Limit`
# applies BEFORE the scope split, so requesting only the rows still needed would take one query per
# matching row when the subset is sparse.
DETAIL_METADATA_QUERY_PAGE_SIZE = 200

# The detail view's metadata collections, addressable individually by the paged read. 'input' and
# 'inputDatabase' are the scope split of the one per-pipeline input-metadata table; 'output' is the
# per-pipeline output-metadata table.
DETAIL_METADATA_COLLECTION_INPUT = "input"
DETAIL_METADATA_COLLECTION_INPUT_DATABASE = "inputDatabase"
DETAIL_METADATA_COLLECTION_OUTPUT = "output"
DETAIL_METADATA_COLLECTIONS = (
    DETAIL_METADATA_COLLECTION_INPUT,
    DETAIL_METADATA_COLLECTION_INPUT_DATABASE,
    DETAIL_METADATA_COLLECTION_OUTPUT,
)

# Upper bound on the number of active executions abort_group aborts per request. Each abort is a
# multi-round-trip synchronous operation (per-member auth + SFN StopExecution + row writes), so a
# very large group is processed in bounded passes: the response flags moreRemaining=true and the
# caller re-invokes to continue (no silent partial abort, and no 15-min Lambda timeout at scale).
MAX_GROUP_ABORT_PER_REQUEST = 200


def _query_all(table_name, key_condition):
    """Query a DynamoDB table fully (following pagination) for a key condition."""
    table = dynamodb.Table(table_name)
    items = []
    kwargs = {'KeyConditionExpression': key_condition}
    resp = table.query(**kwargs)
    while True:
        items.extend(resp.get('Items', []))
        if 'LastEvaluatedKey' not in resp:
            break
        kwargs['ExclusiveStartKey'] = resp['LastEvaluatedKey']
        resp = table.query(**kwargs)
    return items


def _query_capped(table_name, key_condition, max_items):
    """Query a table for a key condition but stop once max_items rows are collected. Returns
    (items, truncated) where truncated is True when more rows exist beyond the cap. Used by the
    execution-details assembly to bound each output/input sub-collection so the assembled response
    cannot exceed the Lambda synchronous-response limit for an output-heavy execution."""
    table = dynamodb.Table(table_name)
    items = []
    kwargs = {'KeyConditionExpression': key_condition, 'Limit': max_items}
    resp = table.query(**kwargs)
    while True:
        items.extend(resp.get('Items', []))
        if len(items) >= max_items:
            return items[:max_items], (len(items) > max_items or 'LastEvaluatedKey' in resp)
        if 'LastEvaluatedKey' not in resp:
            return items, False
        kwargs['ExclusiveStartKey'] = resp['LastEvaluatedKey']
        resp = table.query(**kwargs)


def _wire_bytes(value):
    """UTF-8 size `value` occupies in the response as it is actually SENT.

    The response carries its body as a JSON *string* (models.common.success serializes the body, and the
    integration returns that object verbatim), so every quote and backslash inside a row is escaped a
    SECOND time on the way out. Measuring the plain serialization therefore under-counts: 1.4x on
    escape-heavy values and close to 2x on quote-dense ones, enough for a payload assembled inside a
    5 MiB budget to breach the 6 MB Lambda synchronous-response limit — which fails the request with a
    502 carrying no body, losing the rows and the truncation flags together. Every byte budget in the
    detail and paged-metadata views is counted in these units.

    An unserializable value measures as 0 rather than raising — the response serializer is where that
    failure belongs."""
    try:
        return len(json.dumps(json.dumps(value, default=str)).encode("utf-8"))
    except Exception:
        return 0


def _rows_serialized_bytes(rows):
    """Wire size of a collection's rows (see _wire_bytes). Measured on the same shape the response
    returns, so the budget arithmetic reflects what the caller actually receives."""
    return sum(_wire_bytes(row) for row in rows)


def _detail_payload_wire_bytes(details):
    """Size of the finished detail response as Lambda returns it: the details view inside the response
    envelope models.common.success builds around it, body included as the JSON string it is sent as.

    The per-collection budgets are estimates that charge for rows and not for the structure around them,
    so this is what says whether the assembled response actually fits. An unserializable payload measures
    as 0 — the response serializer is where that raises."""
    try:
        body = json.dumps({'message': details}, default=_json_default)
        return len(json.dumps({"isBase64Encoded": False, "statusCode": 200,
                               "headers": commonHeaders(), "body": body}).encode("utf-8"))
    except Exception:
        return 0


# Order the collections are given up in when the finished payload exceeds the hard ceiling: least costly
# to lose first. The three metadata collections have a paged route a caller can read them in full from,
# so they go before outputs.results (no paged route, and for a run with no input files those results ARE
# its output) and before the file collections, which nothing else in the API supplies. Same priority the
# budget allocation applies up front, in reverse — files are served first, so they are surrendered last.
DETAIL_PAYLOAD_TRIM_ORDER = (
    ("outputs", "metadata"),
    ("inputDatabaseMetadata",),
    ("inputMetadata",),
    ("outputs", "results"),
    ("outputs", "files"),
    ("inputFiles",),
)


def _enforce_detail_payload_ceiling(details, ceiling=DETAIL_PAYLOAD_WIRE_CEILING):
    """Trim the assembled details view until the payload it will be sent as fits `ceiling`, naming every
    collection it takes rows from in truncatedCollections. Mutates `details`; returns the final size.

    The budgets the collections were granted are per-collection and additive, so they cannot see the
    response as a whole: the envelope's own fields, the separators between rows, and any section held
    under a separate share all add bytes no collection was charged for. Exceeding the Lambda
    synchronous-response limit fails the request with a 502 carrying no body — losing the rows AND the
    truncation flags that exist to report exactly this — so the finished payload is measured and trimmed
    against rather than assumed to follow from the estimates.

    Rows are dropped from the tail of one collection at a time in DETAIL_PAYLOAD_TRIM_ORDER, enough to
    cover the overshoot, then the payload is re-measured; a collection emptied without clearing it yields
    to the next. When no collection has rows left the payload is returned as it stands and the overrun is
    logged: what remains is the fixed section, which is bounded on its own path."""
    actual = _detail_payload_wire_bytes(details)
    if actual <= ceiling:
        return actual
    truncated = set(details.get("truncatedCollections") or [])
    while actual > ceiling:
        progressed = False
        for path in DETAIL_PAYLOAD_TRIM_ORDER:
            container = details
            for key in path[:-1]:
                container = container.get(key) or {}
            rows = container.get(path[-1])
            if not isinstance(rows, list) or not rows:
                continue
            name = ".".join(path)
            before = len(rows)
            dropped = 0
            while rows and dropped <= actual - ceiling:
                dropped += _wire_bytes(rows.pop())
            truncated.add(name)
            progressed = True
            actual = _detail_payload_wire_bytes(details)
            logger.info(f"Detail collection {name} trimmed to {len(rows)} of {before} rows so the "
                        f"response fits {ceiling} bytes (now {actual}).")
            if actual <= ceiling:
                break
        if not progressed:
            logger.warning(f"Execution details payload is {actual} bytes with every collection emptied; "
                           f"the untrimmable section exceeds {ceiling} bytes on its own.")
            break
    details["truncatedCollections"] = sorted(truncated)
    return actual


def _allocate_detail_byte_budgets(file_bytes, ceiling=DETAIL_RESPONSE_BYTE_CEILING,
                                  metadata_floor=MIN_DETAIL_METADATA_BYTES_RETURNED,
                                  fixed_bytes=0):
    """Split the response byte ceiling between the file collections and the metadata collections.

    `fixed_bytes` is the part of the response no bound can trim — the per-step entries and their
    rendered configuration bodies, which identify what ran and are the point of the view. It is charged
    against the ceiling BEFORE the collections divide it, so a run with several large configuration
    bodies trims its collections instead of overflowing: those bodies are echoed per step and each is
    capped in the low hundreds of KB, so a few steps consume most of a megabyte on their own.

    Files are served first out of what is left: they get what they need up to
    `ceiling - fixed_bytes - metadata_floor`, and the metadata collections divide the remainder.
    Returns (file_budget, metadata_budget).

    Reserving the metadata floor out of the FILE allowance (rather than adding it on top afterwards) is
    what keeps the total within the ceiling: a file-heavy execution is trimmed to the reduced allowance
    and flagged, instead of the response overflowing by the floor's worth of bytes and relying on a log
    line nobody reads. Both budgets are therefore real bounds, and their sum never exceeds the ceiling.

    A ceiling smaller than the floor still yields a non-negative pair, so a misconfigured pair degrades
    to "metadata only" rather than producing a negative budget that would drop every row. Fixed bytes
    exceeding the ceiling on their own collapse both budgets to zero rather than going negative: the
    collections are then empty and flagged, which is the most the bound can do about a part of the
    response it cannot trim."""
    collections_ceiling = max(0, ceiling - max(0, fixed_bytes))
    metadata_floor = max(0, min(metadata_floor, collections_ceiling))
    file_budget = max(0, collections_ceiling - metadata_floor)
    if file_bytes < file_budget:
        # Files fit inside their allowance, so the metadata collections take everything they leave.
        return file_budget, max(metadata_floor, collections_ceiling - file_bytes)
    # Files are at or over the allowance: they are trimmed to it (and flagged by the trim), and the
    # metadata collections get exactly the reserved floor.
    return file_budget, metadata_floor


def _trim_rows_to_byte_budget(rows, name, truncated,
                              max_bytes=MAX_DETAIL_COLLECTION_BYTES_RETURNED):
    """Drop rows from the end of an already row-trimmed collection until it serializes within
    max_bytes, naming it in `truncated` when anything is dropped.

    The row caps cannot bound the response on their own: a metadata row carries a whole entity's
    captured map, so a collection at the row cap ranges from a few hundred KB to several times the
    Lambda synchronous-response limit. Exceeding that limit fails the request outright, which loses
    even the truncation flags — strictly worse than returning fewer rows and saying so.

    Rows are measured cumulatively in order and the first row that would cross the budget ends the
    collection, so the retained prefix is deterministic and a caller sees whole rows rather than a
    truncated map. A single row larger than the whole budget is still kept, since a collection that
    silently came back empty would read as 'this run captured nothing'."""
    kept = []
    used = 0
    for row in rows:
        row_bytes = _wire_bytes(row)
        if kept and used + row_bytes > max_bytes:
            truncated.add(name)
            logger.info(f"Detail collection {name} trimmed to {len(kept)} of {len(rows)} rows to stay "
                        f"within {max_bytes} bytes (would have been {used + row_bytes}).")
            return kept
        kept.append(row)
        used += row_bytes
    return kept


def _trim_returned_rows(rows, name, truncated, max_rows=MAX_DETAIL_INPUT_ROWS_RETURNED,
                        max_bytes=MAX_DETAIL_COLLECTION_BYTES_RETURNED):
    """Trim an assembled detail collection to max_rows AND to a byte budget, naming it in the
    `truncated` set when rows are dropped. Returns the trimmed list. Applied per collection after
    the input-metadata split, so each collection's own flag reflects only its own trim."""
    if len(rows) > max_rows:
        truncated.add(name)
        rows = rows[:max_rows]
    return _trim_rows_to_byte_budget(rows, name, truncated, max_bytes=max_bytes)


def _trim_returned_rows_per_pipeline(rows, name, truncated,
                                     max_rows=MAX_DETAIL_INPUT_ROWS_RETURNED,
                                     max_bytes=MAX_DETAIL_COLLECTION_BYTES_RETURNED):
    """Trim a PER-PIPELINE detail collection to max_rows, taking an even share from each producing
    pipeline rather than a prefix of the assembled list. Names the collection in `truncated` when rows
    are dropped, and preserves both the pipeline order and each pipeline's own row order.

    The rows arrive grouped by pipeline (the assembly walks the steps in order), so a prefix trim
    spends the whole budget on the first pipelines and returns NONE of the later ones'. For a
    collection whose purpose is to say which metadata each pipeline read, a pipeline with no rows reads
    as "this step read nothing" — a claim about the step, not a visible consequence of the cap. Taking
    a share from each keeps every pipeline represented, so the truncation flag is the only thing a
    reader has to interpret.

    The BYTE budget is spent by the same round-robin rather than applied to the assembled result: a
    trailing byte trim would cut whole pipelines off the end, which is the prefix-trim problem the
    round-robin exists to avoid. Both bounds therefore stop the walk together, and either one naming
    the collection in `truncated` reports the same thing — the view is a subset."""
    groups = {}
    order = []
    for row in rows:
        pipeline_id = row.get("pipelineId", "")
        if pipeline_id not in groups:
            groups[pipeline_id] = []
            order.append(pipeline_id)
        groups[pipeline_id].append(row)
    if len(rows) > max_rows:
        truncated.add(name)
    # Round-robin one row per pipeline per pass, so a pipeline with fewer rows than its even share
    # leaves its remainder to the others instead of reserving it.
    kept = {pipeline_id: [] for pipeline_id in order}
    taken = 0
    used = 0
    depth = 0
    stop = False
    while taken < max_rows and not stop:
        progressed = False
        for pipeline_id in order:
            if depth >= len(groups[pipeline_id]):
                continue
            row = groups[pipeline_id][depth]
            row_bytes = _wire_bytes(row)
            # Keep at least one row overall, so a collection of individually huge rows is not reported
            # as empty (which would read as "this run captured nothing").
            if taken and used + row_bytes > max_bytes:
                truncated.add(name)
                logger.info(f"Detail collection {name} trimmed to {taken} of {len(rows)} rows to stay "
                            f"within {max_bytes} bytes.")
                stop = True
                break
            kept[pipeline_id].append(row)
            used += row_bytes
            taken += 1
            progressed = True
            if taken >= max_rows:
                break
        if not progressed:
            break
        depth += 1
    return [row for pipeline_id in order for row in kept[pipeline_id]]


def get_workflow_definition(workflow_database_id, workflow_id):
    """Fetch the workflow definition row (for description + pipeline name/description
    cross-fetch). Returns the item or {}."""
    if not workflow_database_id or not workflow_id:
        return {}
    table = dynamodb.Table(workflow_database)
    resp = table.get_item(Key={'databaseId': workflow_database_id, 'workflowId': workflow_id})
    return resp.get('Item', {}) or {}


def get_pipeline_definition(pipeline_database_id, pipeline_id):
    """Fetch a pipeline definition row (for human-readable name/description). Returns {}
    when the pipeline cannot be found (e.g. deleted), so the detail view degrades gracefully."""
    if not pipeline_database_id or not pipeline_id:
        return {}
    table = dynamodb.Table(pipeline_database)
    resp = table.get_item(Key={'databaseId': pipeline_database_id, 'pipelineId': pipeline_id})
    return resp.get('Item', {}) or {}


def get_pipeline_definitions(pairs):
    """Resolve many pipeline definition rows in batched reads, keyed by (databaseId, pipelineId).

    (databaseId, pipelineId) is the pipeline table's complete primary key, so each pair addresses at
    most one item. A pair the batch does not resolve falls back to the per-item
    `get_pipeline_definition`, and a pipeline that no longer exists maps to {} — the same graceful
    degradation the single-row read gives the detail view."""
    wanted = []
    seen = set()
    for pair in pairs:
        database_id, pipeline_id = pair
        if not database_id or not pipeline_id or pair in seen:
            continue
        seen.add(pair)
        wanted.append(pair)

    definitions = {}
    # A single-step workflow is already one round-trip, so it goes straight to the single-row read;
    # batching starts to pay off from two steps.
    if len(wanted) > 1:
        rows = _batch_get_rows(
            pipeline_database,
            [{'databaseId': database_id, 'pipelineId': pipeline_id}
             for database_id, pipeline_id in wanted])
        for item in rows:
            pair = (item.get('databaseId', ''), item.get('pipelineId', ''))
            if pair in seen:
                definitions[pair] = item

    for pair in wanted:
        if pair not in definitions:
            definitions[pair] = get_pipeline_definition(pair[0], pair[1])

    return definitions


def _scrub_pipeline_detail(prow, pipeline_def, rendered_config="", rendered_config_truncated=False,
                           config_snapshot=None):
    """Public-facing per-pipeline detail. Cross-fetches a human-readable name/description
    from the pipeline definition and exposes only non-internal status/timing/type fields, plus
    the exact rendered input configuration body that was sent to this pipeline and the template
    snapshot (which template/tags/override + config format the run used).

    Deliberately omitted as internal: every S3 bucket/prefix field (input/output/aux/temp),
    all ARNs (pipelineResourceArn, sub-execution arns), and the STS/vended-role fields.

    The one exception is renderedConfigLocation, emitted whenever the object exists. The config
    body always goes to Amazon S3 for the pipeline to read, and it is the FULLY rendered body —
    `renderedConfig` inline is post-user-tag / pre-system-tag, a different stage of the same body.
    So a caller wanting what the step actually ran with needs this pointer even when the inline copy
    is complete. It is scoped to that one object (bucket + key of this step's config.json), and a
    caller reading this response has already cleared the execution-detail authorization gate.
    `renderedConfigTruncated` is the truncation signal and is emitted unconditionally."""
    # V2 pipeline records carry a human-readable pipelineName; fall back to category, then the id.
    name = (pipeline_def.get('pipelineName', '') or pipeline_def.get('pipelineId', '')
            or prow.get('pipelineId', ''))
    snapshot = config_snapshot or {}
    detail = {
        "pipelineId": prow.get('pipelineId', ''),
        "pipelineDatabaseId": prow.get('pipelineDatabaseId', ''),
        # Per-pipeline-execution id: the log endpoint's pipelineExecutionId parameter, letting the
        # detail view request logs scoped to this one step. Validated back against the execution
        # server-side before any of its logs are returned.
        "pipelineExecutionId": prow.get('pipelineExecutionId', ''),
        "name": name,
        "description": pipeline_def.get('description', ''),
        "pipelineType": pipeline_def.get('category', ''),
        "pipelineExecutionType": prow.get('pipelineExecutionType', ''),
        "endStatePipeline": prow.get('endStatePipeline', 'false') == 'true',
        "executionStatus": prow.get('executionStatus', ''),
        "executionStartDate": prow.get('executionStartDate', ''),
        "executionStopDate": prow.get('executionStopDate', ''),
        # The exact configuration body delivered to this pipeline at run time (empty if none / not
        # yet recorded). truncated flags an offloaded/oversized body the detail view capped.
        "renderedConfig": rendered_config,
        "renderedConfigTruncated": rendered_config_truncated,
        # Template snapshot the run resolved from (from the input-configuration row).
        "templateId": snapshot.get('templateId', ''),
        "templateTags": snapshot.get('templateTags', []),
        "customTemplateOverrideUsed": bool(snapshot.get('customTemplateOverrideUsed', False)),
        "configFormat": snapshot.get('configFormat', ''),
        # The systemConfig THIS STEP ran under (pipeline systemConfig merged with the chosen
        # template's overrides), plus the overrides alone so a reader can see what the template
        # changed. Empty for a run recorded before these were captured.
        "effectiveSystemConfig": snapshot.get('effectiveSystemConfig', {}) or {},
        "templateOverrides": snapshot.get('templateOverrides', {}) or {},
    }
    # Location of the full config body, for a truncated inline copy only. bucket is the step's run
    # bucket from the pipeline-execution row; key is the per-execution config.json the pipeline read.
    # Emitted as a {bucket, key} pair — the shape the asset records use for an S3 object location —
    # rather than a bare key, because a key alone does not identify the object across the run bucket
    # and the auxiliary bucket.
    #
    # Emitted whenever the object exists, NOT only when the inline body was truncated. The two fields
    # describe different STAGES of the same body: `renderedConfig` is post-user-tag / pre-system-tag
    # (templateResolution defers the system tags — see its docstring), while this object is the fully
    # rendered body the step actually ran with. So a caller wanting what ran needs this pointer even
    # when the inline copy is complete, which is the common case. Withholding it there left the API
    # with no route to the real config at all, short of the caller reconstructing an internal S3 key.
    # `renderedConfigTruncated` remains the truncation signal on its own — it is emitted
    # unconditionally above, so nothing depended on this field's absence to convey it.
    config_s3_key = snapshot.get('inputConfigurationFileS3Key', '') or ''
    if config_s3_key:
        detail["renderedConfigLocation"] = {
            "bucket": prow.get('S3AssetPipelineBucket', '') or '',
            "key": config_s3_key,
        }
    return detail


def _scrub_input_file(row):
    """Public-facing input-file record (asset-relative locator only; no S3 internals). versionId is
    the concrete S3 version the run read (resolved at launch); empty for folder/whole-asset inputs."""
    return {
        "databaseId": row.get('databaseId', ''),
        "assetId": row.get('assetId', ''),
        "inputAssetFileKey": row.get('inputAssetFileKey', ''),
        "versionId": row.get('versionId', ''),
    }


def _scrub_input_metadata(row):
    """Public-facing input-metadata record (asset-relative filePath + the metadata and attributes
    maps; the internal source S3 key is omitted).

    scope says what the metadata describes: 'asset' for an asset/file row, 'database' for a
    metadata-source database's own metadata (empty assetId, '/' filePath). Rows stored before the
    discriminator existed carry none, so they read as asset metadata.

    'attributes' is that file's ATTRIBUTES, reported separately from 'metadata' because the two are
    independently gated by a pipeline's metadataInputs (fileMetadata vs fileAttributes). Reporting them
    apart is what makes a per-step fileAttributes gate observable through the API: a step the gate
    excluded shows an empty map while a step it allowed shows the values. A row is empty here when the
    run captured no attributes for that file, and asset/database-scope rows always are."""
    return {
        "databaseId": row.get('databaseId', ''),
        "assetId": row.get('assetId', ''),
        "filePath": row.get('filePath', ''),
        "scope": row.get('scope', '') or INPUT_METADATA_SCOPE_ASSET,
        "metadata": row.get('metadata', {}) or {},
        "attributes": row.get('attributes', {}) or {},
    }


def _scrub_output_file(row):
    """Public-facing output-file traceability record. Exposes the asset-relative path,
    type, and size/contentType/version when available; the underlying S3 bucket/key are
    internal and omitted. Size/contentType may be absent if a lifecycle policy has already
    expired a temporary output file -- the listing still surfaces the (path, type)."""
    out = {
        "relativeFilePath": row.get('relativeFilePath', ''),
        "fileType": row.get('fileType', ''),
    }
    if row.get('fileSize') not in (None, ""):
        # fileSize is stored as a DynamoDB Number (Decimal); coerce to int so the response is
        # JSON-serializable (json.dumps cannot encode Decimal).
        try:
            out["fileSize"] = int(row.get('fileSize'))
        except (ValueError, TypeError):
            out["fileSize"] = row.get('fileSize')
    if row.get('contentType'):
        out["contentType"] = row.get('contentType')
    if row.get('s3VersionId'):
        out["s3VersionId"] = row.get('s3VersionId')
    return out


def _scrub_output_metadata(row):
    """Public-facing output-metadata record (target path + key/value; source S3 omitted)."""
    return {
        "targetFilePath": row.get('targetFilePath', ''),
        "metadataKey": row.get('metadataKey', ''),
        "metadataValue": row.get('metadataValue', ''),
    }


def _scrub_output_result(row):
    """Public-facing output-result record (relative path + content; internal S3 key omitted)."""
    return {
        "relativeFilePath": row.get('relativeFilePath', ''),
        "resultsContent": row.get('resultsContent', ''),
        "resultsContentTruncated": row.get('resultsContentTruncated', False),
    }


def get_workflow_execution_configuration_row(execution_id):
    """Fetch the workflow-execution configuration row (PK workflowExecutionId, SK 'configuration').

    Returns the item, or {} when the row genuinely does not exist — a legacy run, or one whose row has
    already been deleted by a permanent delete.

    A FAILED read raises. This row is load-bearing for authorization, not just for projection: it
    carries the metadata-source databases and assets the read gate checks and the output-target ids that
    gate a run with no inputs. Answering a failed read with {} makes an execution look as though it read
    and wrote nothing, which removes every data-level check and leaves workflow GET alone — so a
    DynamoDB throttle would silently turn a denial into an approval. Failing the request is the only
    safe answer: `lambda_handler` maps a throttle to its own response and anything else to a 500, and a
    caller retries.

    Callers that need "absent" to be distinguishable from "unreadable" get that for free — absent
    returns {}, unreadable never returns."""
    cfg_table = dynamodb.Table(workflow_execution_configuration_table)
    resp = cfg_table.get_item(Key={'workflowExecutionId': execution_id,
                                   'recordType': 'configuration'})
    return resp.get('Item') or {}


def get_produced_file_versions(execution_id):
    """Map an execution's produced asset file versions, keyed by (databaseId, assetId,
    normalizedFilePath) -> versionId, from the asset file version-history table.

    Reads the sparse WorkflowExecutionIdIndex GSI (only workflow-produced versions carry
    changeWorkflowExecutionId). Best-effort: returns {} when the table is not configured
    (older deployments) or on any read failure, so output enrichment degrades to path-only."""
    if not asset_file_version_history_table or not execution_id:
        return {}
    versions = {}
    try:
        kwargs = {
            'IndexName': 'WorkflowExecutionIdIndex',
            'KeyConditionExpression': Key('changeWorkflowExecutionId').eq(execution_id),
        }
        resp = asset_file_version_history_table.query(**kwargs)
        while True:
            for row in resp.get('Items', []):
                key = (row.get('databaseId', ''), row.get('assetId', ''),
                       row.get('filePath', ''))
                version_id = row.get('versionId', '')
                if version_id and version_id != 'null':
                    versions[key] = version_id
            if 'LastEvaluatedKey' not in resp:
                break
            kwargs['ExclusiveStartKey'] = resp['LastEvaluatedKey']
            resp = asset_file_version_history_table.query(**kwargs)
    except Exception as e:
        logger.exception(f"Failed reading produced file versions for {execution_id}: {e}")
        return {}
    return versions


def _enrich_output_files_with_asset_versions(output_files, execution_id, config_row):
    """Annotate each asset output file with its target asset identity and, when available, the
    authoritative S3 file version it produced. Only applies when the execution's output target
    is an asset.

    assetId / databaseId are derived from the execution's asset output target (the configuration
    row), so they are set on every output file for an asset-output execution. assetFileVersionId
    is the only field sourced from the version-history table and is added only when a matching
    record exists (e.g. it is absent for legacy executions written before version history). Mutates
    and returns output_files."""
    if (config_row.get('outputLocationType') or 'asset') != 'asset':
        return output_files
    output_database_id = config_row.get('outputDatabaseId', '')
    output_asset_id = config_row.get('outputAssetId', '')
    produced = get_produced_file_versions(execution_id)
    for f in output_files:
        if output_asset_id:
            f['assetId'] = output_asset_id
        if output_database_id:
            f['databaseId'] = output_database_id
        # History filePath is asset-relative with one leading slash; the output record's
        # relativeFilePath has no leading slash, so normalize before matching.
        normalized = er.normalize_file_key(f.get('relativeFilePath', ''))
        file_version_id = produced.get((output_database_id, output_asset_id, normalized))
        if file_version_id:
            f['assetFileVersionId'] = file_version_id
    return output_files


def assemble_execution_details(execution_id, main_item, config_row=None):
    """Assemble the full, traceability-focused detail view for an execution.

    Cross-fetches workflow + per-pipeline definitions for human-readable names/descriptions,
    and gathers per-pipeline inputs (files/metadata/configuration) and outputs
    (files/metadata/results). Input metadata is reported in two collections: asset/file metadata under
    inputMetadata, and each metadata-source database's own metadata under inputDatabaseMetadata (it
    belongs to no asset). Tolerates partially-populated executions (still running) and records the
    end-state lambda has not written yet. Scrubs all internal fields (ARNs, S3 bucket/key/prefix
    locations, STS/vended-role fields).

    Collections are bounded three ways, and every bound that cuts a collection names it in
    truncatedCollections, so a partial section is always flagged:

      - READ: each read stops at MAX_DETAIL_ROWS_PER_COLLECTION rows.
      - ROWS: the input collections are trimmed to MAX_DETAIL_INPUT_ROWS_RETURNED.
      - BYTES: the whole response is assembled within DETAIL_RESPONSE_BYTE_CEILING. The file
        collections are served first and the metadata collections divide the remainder, with
        MIN_DETAIL_METADATA_BYTES_RETURNED reserved so a file-heavy run still shows some metadata.
        Files are prioritized, not exempt: a run whose files alone exceed the allowance has them
        trimmed and flagged too, because breaching the Lambda response limit returns no body at all.

    The fixed section (pipelines + inputConfigurations) is charged against the ceiling before the
    collections divide it and is held to MAX_DETAIL_FIXED_SECTION_BYTES of it. Every step is always
    reported; what yields is each step's inline renderedConfig, shortened or dropped with
    renderedConfigTruncated set, renderedConfigLocation kept as the route to the fully rendered object,
    and "pipelines"/"inputConfigurations" named in truncatedCollections.

    The two input-metadata collections share one read, so a read-cap hit flags both; a row or byte trim
    flags only the collection actually trimmed.

    The input-metadata rows are per pipeline execution and describe the entities that step read, so
    both their read and their trim are shared out across the steps rather than spent first-come: each
    pipeline reads at most its own share of the collection budget, and the trim keeps a share of each
    pipeline's rows. A pipeline's rows are therefore thinned by a cap, never dropped wholesale — which
    would read as that step having read no metadata.

    `config_row` supplies an already-read configuration row (the authorization pass reads the same one)
    so assembling the view does not repeat that read."""
    workflow_def = get_workflow_definition(
        main_item.get('workflowDatabaseId', ''), main_item.get('workflowId', ''))

    # Per-pipeline rows, with name/description cross-fetched (cache by pipeline key).
    pipeline_rows = get_pipeline_execution_rows(execution_id)
    pipeline_def_cache = {}
    pipelines = []
    output_files = []
    output_metadata = []
    output_results = []
    input_files = []
    input_metadata = []
    input_configurations = []
    # Track which sub-collections were capped so the response can flag partial data (no silent cap).
    truncated = set()

    def _collect(target, table_name, pexec, scrub, name, pipeline_id="", per_pipeline_cap=0):
        """Append up to the per-collection cap (across all pipelines) from a pexec-keyed table,
        recording truncation. Stamps each scrubbed row with the producing pipelineId so the UI can
        attribute outputs/metadata to the pipeline that produced them. Bounds the assembled response
        for output-heavy executions.

        per_pipeline_cap additionally bounds ONE pipeline's share of the collection budget. Without it
        the budget is first-come across the steps, so one row-heavy early pipeline can consume the whole
        collection and later pipelines contribute nothing — for a per-pipeline collection that reads as
        those steps having no rows. Left at 0 (the shared first-come budget) for collections whose rows
        are attributed to a pipeline but not compared across them."""
        remaining = MAX_DETAIL_ROWS_PER_COLLECTION - len(target)
        if per_pipeline_cap > 0:
            remaining = min(remaining, per_pipeline_cap)
        if remaining <= 0:
            truncated.add(name)
            return
        rows, was_truncated = _query_capped(table_name, Key('pipelineExecutionId').eq(pexec), remaining)
        for r in rows:
            scrubbed = scrub(r)
            scrubbed["pipelineId"] = pipeline_id
            target.append(scrubbed)
        if was_truncated:
            truncated.add(name)

    # A step may read up to what is left of the input-metadata budget, minus a floor reserved for each
    # step still to be read — so a row-heavy step is not capped by an even share the other steps cannot
    # use, and a step late in the order is still guaranteed rows.
    #
    # A fixed even share (budget // steps) was the wrong bound: four arity-none steps holding one
    # database row each reserved four-fifths of the budget, so a 5-step run whose first step read 900
    # files returned 399 of them and flagged the collection truncated, while the same run on one step
    # returned all 900. Reserving only the floor means that run now returns all 900 and flags nothing.
    #
    # When the steps genuinely want more than the budget, the earlier ones fill first and the later ones
    # fall back on their floor. That favors read order over evenness, which is the right trade here: the
    # alternative needs each step's row count before reading any of them, and the collection is flagged
    # truncated either way.
    _input_metadata_remaining = [MAX_DETAIL_ROWS_PER_COLLECTION]
    _input_metadata_steps_left = [max(1, len(pipeline_rows))]
    # Half the budget spread across the steps, so the reserve cannot itself starve an early step.
    _input_metadata_floor = max(1, MAX_DETAIL_ROWS_PER_COLLECTION // (2 * max(1, len(pipeline_rows))))

    def _input_metadata_share():
        """This step's read cap: what is left, less a floor for each step still to come."""
        _input_metadata_steps_left[0] = max(0, _input_metadata_steps_left[0] - 1)
        reserved = _input_metadata_floor * _input_metadata_steps_left[0]
        return max(1, _input_metadata_remaining[0] - reserved)

    # Every step's definition resolves in batched reads up front, so a multi-step workflow costs a few
    # round-trips rather than one per step. Repeated pipelines collapse to a single key.
    pipeline_def_cache.update(get_pipeline_definitions(
        (prow.get('pipelineDatabaseId', ''), prow.get('pipelineId', '')) for prow in pipeline_rows))

    for prow in pipeline_rows:
        pexec_id = prow.get('pipelineExecutionId', '')
        pkey = (prow.get('pipelineDatabaseId', ''), prow.get('pipelineId', ''))
        if pkey not in pipeline_def_cache:
            pipeline_def_cache[pkey] = get_pipeline_definition(pkey[0], pkey[1])

        # Resolve this pipeline's rendered input configuration (the exact config body sent to the
        # pipeline). It is per-pipeline-execution; attach it to the pipeline detail so the UI can show
        # each pipeline's config inline. The flat list indexes the steps that recorded a configuration
        # and carries the truncation flag; the body itself is reported once, on the pipeline entry,
        # because a body echoed in both places charges the response twice for the same bytes.
        pipeline_config = ""
        pipeline_config_truncated = False
        # The template snapshot (which template/tags/override the run used, and the config format)
        # lives on the same configuration row; carry it onto the pipeline detail so the UI's
        # per-pipeline template section renders and the config editor highlights the right format.
        config_snapshot = {}
        if pexec_id:
            for row in _query_all(pipeline_execution_input_configuration_table,
                                  Key('pipelineExecutionId').eq(pexec_id)):
                pipeline_config = row.get('inputConfiguration', '')
                pipeline_config_truncated = row.get('inputConfigurationTruncated', False)
                config_snapshot = row
                input_configurations.append({
                    "pipelineId": prow.get('pipelineId', ''),
                    "inputConfigurationTruncated": pipeline_config_truncated,
                })

        pipelines.append(_scrub_pipeline_detail(
            prow, pipeline_def_cache[pkey], pipeline_config, pipeline_config_truncated,
            config_snapshot))

        if not pexec_id:
            continue

        _pid = prow.get('pipelineId', '')
        # Input metadata is recorded per pipeline execution, each pipeline's rows describing the
        # entities IT read; gather (capped, with each pipeline bounded to its share of what the earlier
        # steps left) then dedupe within a pipeline below.
        _before = len(input_metadata)
        _collect(input_metadata, pipeline_execution_input_metadata_table, pexec_id,
                 _scrub_input_metadata, "inputMetadata", _pid,
                 per_pipeline_cap=_input_metadata_share())
        _input_metadata_remaining[0] = max(
            0, _input_metadata_remaining[0] - (len(input_metadata) - _before))
        # Outputs per pipeline execution (files / metadata / results), each capped and tagged
        # with the producing pipelineId for per-pipeline attribution in the UI.
        _collect(output_files, pipeline_execution_output_files_table, pexec_id,
                 _scrub_output_file, "outputs.files", _pid)
        _collect(output_metadata, pipeline_execution_output_metadata_table, pexec_id,
                 _scrub_output_metadata, "outputs.metadata", _pid)
        _collect(output_results, pipeline_execution_output_results_table, pexec_id,
                 _scrub_output_result, "outputs.results", _pid)

    # Input files are tracked at the workflow-execution level (not per-pipeline).
    _input_rows, _input_trunc = _query_capped(
        workflow_execution_inputs_table, Key('workflowExecutionId').eq(execution_id),
        MAX_DETAIL_ROWS_PER_COLLECTION)
    input_files = [_scrub_input_file(row) for row in _input_rows]
    if _input_trunc:
        truncated.add("inputFiles")
    input_files = _trim_returned_rows(input_files, "inputFiles", truncated)


    # Dedupe input metadata by (pipelineId, scope, databaseId, assetId, filePath) — the row's full
    # identity. A database row carries an empty assetId and a '/' filePath, so dropping any of the
    # entity keys would collapse it into an asset-level row (or into the legacy flat row, which has
    # empty ids too). pipelineId is part of the identity because the rows are per-pipeline-execution
    # and each pipeline reads a different set of entities: the collections answer which metadata went
    # into which pipeline, so two pipelines' rows for the same entity are two distinct facts, not one
    # row read twice. A key without it keeps whichever pipeline was collected last and reports that
    # pipeline as the only reader.
    deduped_md = {}
    for md in input_metadata:
        deduped_md[(md.get("pipelineId", ""), md.get("scope", ""), md.get("databaseId", ""),
                    md.get("assetId", ""), md.get("filePath", ""))] = md
    input_metadata = list(deduped_md.values())

    # Database metadata is its own detail collection: it describes a metadata-source database rather
    # than an asset, so a client rendering asset/file columns cannot present it in the same table.
    input_database_metadata = [md for md in input_metadata
                               if md.get("scope") == INPUT_METADATA_SCOPE_DATABASE]
    input_metadata = [md for md in input_metadata
                      if md.get("scope") != INPUT_METADATA_SCOPE_DATABASE]
    # Both collections come from the one capped input-metadata read, and a dropped row's scope is not
    # knowable, so a READ-cap hit flags both rather than only the one that happens to look short. This
    # is the one coarse case: the two collections cannot be distinguished for rows that were never
    # read. The per-collection trim below is exact, because it runs after the scope split.
    if "inputMetadata" in truncated:
        truncated.add("inputDatabaseMetadata")

    # For asset-output executions, join each output file to the authoritative S3 asset file
    # version it produced (via the version-history table). Best-effort: leaves entries
    # path-only when no history record exists (e.g. legacy runs).
    #
    # Runs BEFORE the byte budget is measured: enrichment adds assetId/databaseId/assetFileVersionId to
    # every output-file row, so measuring beforehand would under-count the collection and let the
    # assembled response exceed the ceiling the budget exists to hold.
    if config_row is None:
        config_row = get_workflow_execution_configuration_row(execution_id)
    output_files = _enrich_output_files_with_asset_versions(output_files, execution_id, config_row)

    # The file collections are served first (DETAIL_FILE_BUDGET_COLLECTIONS), sharing ONE allowance so
    # the pair cannot breach the ceiling by each spending it: inputFiles is granted first and the output
    # files take what it leaves. Either one trimmed is named in truncatedCollections — neither has a
    # paged route to escalate to, so the flag is the caller's only signal.
    #
    # The per-step entries and their rendered configuration bodies are charged against the ceiling before
    # the collections divide it, and are held to their own share of it (MAX_DETAIL_FIXED_SECTION_BYTES).
    # A step's identity is never dropped — reporting what ran is the point of the view — so what yields
    # is the inline configuration body: shortened or removed, flagged with renderedConfigTruncated, and
    # left with renderedConfigLocation as the route to the fully rendered object. Without the share, a
    # run whose steps carry large bodies breaches the Lambda synchronous-response limit on the fixed
    # section alone, before a collection is allocated a byte and with nothing named as partial.
    _fixed_bytes = (_rows_serialized_bytes(pipelines)
                    + _rows_serialized_bytes(input_configurations))
    if _fixed_bytes > MAX_DETAIL_FIXED_SECTION_BYTES:
        _config_shortened_pipelines = set()

        def _shorten_rendered_config(entry, max_bytes):
            """Hold one step's inline body to max_bytes of serialized JSON, dropping it when what would
            be left is too short to read as configuration. True when the body changed."""
            body = entry.get("renderedConfig") or ""
            if not body or len(json.dumps(body).encode("utf-8")) <= max_bytes:
                return False
            entry["renderedConfig"] = ("" if max_bytes < MIN_DETAIL_RENDERED_CONFIG_BYTES
                                       else body.encode("utf-8")[:max_bytes].decode("utf-8", "ignore"))
            entry["renderedConfigTruncated"] = True
            _config_shortened_pipelines.add(entry.get("pipelineId", ""))
            return True

        # Every step is granted the same share of what the step entries themselves leave, so the section
        # reports each step's configuration to the same depth rather than serving the first steps in full
        # and leaving the last ones nothing.
        _body_bytes = sum(len(json.dumps(p.get("renderedConfig") or "").encode("utf-8"))
                          for p in pipelines)
        _config_share = max(0, MAX_DETAIL_FIXED_SECTION_BYTES - (_fixed_bytes - _body_bytes))
        _config_share //= max(1, len(pipelines))
        for _pipeline in pipelines:
            _shorten_rendered_config(_pipeline, _config_share)
        _fixed_bytes = (_rows_serialized_bytes(pipelines)
                        + _rows_serialized_bytes(input_configurations))
        if _fixed_bytes > MAX_DETAIL_FIXED_SECTION_BYTES:
            # A share derived from the bodies can still land over: escaping inflates each body's
            # serialized size, and enough steps put the entries' own fields over the share on their own.
            # Dropping the inline copies is the last move that keeps every step reported.
            for _pipeline in pipelines:
                _shorten_rendered_config(_pipeline, 0)
            _fixed_bytes = (_rows_serialized_bytes(pipelines)
                            + _rows_serialized_bytes(input_configurations))
        if _config_shortened_pipelines:
            # Named like any other bounded part of the response: the bodies live on the pipeline entries
            # and the flat list indexes them, so both sections report partial configuration.
            truncated.add("pipelines")
            truncated.add("inputConfigurations")
    _file_budget, _metadata_budget = _allocate_detail_byte_budgets(
        _rows_serialized_bytes(input_files) + _rows_serialized_bytes(output_files),
        fixed_bytes=_fixed_bytes)
    input_files = _trim_rows_to_byte_budget(input_files, "inputFiles", truncated,
                                            max_bytes=_file_budget)
    output_files = _trim_returned_rows_per_pipeline(
        output_files, "outputs.files", truncated,
        max_rows=MAX_DETAIL_ROWS_PER_COLLECTION,
        max_bytes=max(0, _file_budget - _rows_serialized_bytes(input_files)))

    # Both metadata collections are per-pipeline, so the trim takes a share from each producing pipeline
    # rather than a prefix — a prefix would return the first pipelines' rows and none of the later ones'.
    #
    # The metadata collections divide what the files left (_metadata_budget). They share it rather than
    # each taking it, so the assembled response stays within DETAIL_RESPONSE_BYTE_CEILING: each is
    # granted the remainder after the ones before it. A collection trimmed here is named in
    # truncatedCollections and can be read in full from the paged metadata route, which is why metadata
    # yields to files rather than the other way round.
    _metadata_remaining = _metadata_budget

    def _grant(rows, name):
        """Trim `rows` to what the metadata allowance has left, then charge the retained bytes to it."""
        nonlocal _metadata_remaining
        kept = _trim_returned_rows_per_pipeline(rows, name, truncated,
                                                max_bytes=max(0, _metadata_remaining))
        _metadata_remaining -= _rows_serialized_bytes(kept)
        return kept

    # Granted in order of what a trimmed collection costs the caller, not in the order they are reported.
    # outputs.results has no paged route, so a row trimmed from it is unreachable — and for a run with no
    # input files those results ARE its output. It is therefore served first, and the three collections
    # that DO have a paged route to escalate to divide what it leaves. This is the same reasoning that
    # puts files ahead of metadata, applied one level down.
    output_results = _grant(output_results, "outputs.results")
    input_metadata = _grant(input_metadata, "inputMetadata")
    input_database_metadata = _grant(input_database_metadata, "inputDatabaseMetadata")
    output_metadata = _grant(output_metadata, "outputs.metadata")

    # Reported from the same helper the authorization pass gates on, so the databases a client is told
    # about are exactly the ones access to this view required.
    metadata_source_databases, _metadata_source_assets = _metadata_source_entities(config_row)

    return {
        "workflowExecutionId": execution_id,
        "workflowId": main_item.get('workflowId', ''),
        "workflowDatabaseId": main_item.get('workflowDatabaseId', ''),
        # Human-readable name for UI display (breadcrumbs/headers); falls back to the id downstream.
        "workflowName": workflow_def.get('workflowName', ''),
        "workflowDescription": workflow_def.get('description', ''),
        # The workflow's systemConfig as it stands NOW. The workflow-level settings are the outer gate
        # of the workflow -> pipeline -> template chain; each pipeline step's own resolved settings are
        # snapshotted per step (see effectiveSystemConfig on each pipeline entry). This one is read live
        # rather than snapshotted, so a settings view must label it as current, not as-run.
        "workflowSystemConfig": workflow_def.get('systemConfig', {}) or {},
        "executionStatus": main_item.get('executionStatus', ''),
        "executionStartDate": main_item.get('executionStartDate', ''),
        "executionStopDate": main_item.get('executionStopDate', ''),
        "triggerType": main_item.get('triggerType', ''),
        "triggeredByUserId": main_item.get('triggeredByUserId', ''),
        "executionError": redact_log_text(main_item.get('executionError', '')),
        # Output target of the run: where outputs were written. locationType 'none' = results-only
        # (no asset outputs); 'asset' carries the destination asset/database ids.
        "outputLocationType": (config_row or {}).get('outputLocationType', '') or 'asset',
        "outputDatabaseId": (config_row or {}).get('outputDatabaseId', ''),
        "outputAssetId": (config_row or {}).get('outputAssetId', ''),
        # The (dynamic-tag-templated) output base path this run wrote under; '/' = asset root.
        "outputFileBaseExecutionPathExtension":
            (config_row or {}).get('outputFileBaseExecutionPathExtension', '') or '/',
        # The metadata sources of the run (never input files): every database whose metadata was
        # captured, and the assets named purely as sources, in selection order. metadataSourceDatabaseId
        # is the single database the CALLER named, which only a run with no input files has — a run with
        # input files derives its databases from them instead, so the databases list is what a client
        # should render. It reports every captured database, including the ones derived from the input
        # files that do not gate the read. All three are always sent, empty when none, so a client can
        # tell "no source" from "a source that carries no metadata" — the latter yields a selection here
        # with no metadata row.
        "metadataSourceDatabaseId": (config_row or {}).get('inputMetadataDatabaseId', ''),
        "metadataSourceDatabases": [
            d for d in ((config_row or {}).get('metadataSourceDatabases') or []) if d
        ],
        "metadataSourceAssets": [
            {"databaseId": s.get('databaseId', ''), "assetId": s.get('assetId', '')}
            for s in ((config_row or {}).get('metadataSourceAssets') or [])
        ],
        "pipelines": pipelines,
        "inputFiles": input_files,
        "inputMetadata": input_metadata,
        # The metadata-source database's own metadata (read-only input), separate from inputMetadata
        # because it belongs to no asset.
        "inputDatabaseMetadata": input_database_metadata,
        "inputConfigurations": input_configurations,
        "outputs": {
            "files": output_files,
            "metadata": output_metadata,
            "results": output_results,
        },
        # Names of any sub-collections that are partial (empty when the view is complete): read at the
        # MAX_DETAIL_ROWS_PER_COLLECTION read cap, or trimmed to MAX_DETAIL_INPUT_ROWS_RETURNED for the
        # input collections. Names are "inputFiles", "inputMetadata", "inputDatabaseMetadata",
        # "outputs.files", "outputs.metadata", "outputs.results", plus "pipelines" and
        # "inputConfigurations" when the step section's configuration bodies were bounded.
        "truncatedCollections": sorted(truncated),
    }


def get_execution_details(event, execution_id):
    """Return the full detail/traceability view for an execution (404 if unknown).

    Authorization mirrors list-executions reads: workflow GET, GET on a captured metadata-source
    database, and GET on every asset the run read (or the asset it wrote to when it read none). The
    configuration row both need is read once and threaded through."""
    main_item = get_execution_main_row(execution_id)
    if not main_item:
        return validation_error(status_code=404, body={'message': "Execution not found"}, event=event)

    config_row = get_workflow_execution_configuration_row(execution_id)
    allowed, reason = authorize_execution_access(execution_id, main_item, "GET",
                                                 config_row=config_row)
    if not allowed:
        logger.info(f"Details access not authorized for execution {execution_id}: {reason}")
        return authorization_error()

    # Safety net: reconcile a non-terminal row against SFN (RUNNING is written at launch, so the
    # common path skips the poll) so an out-of-band abort never shows RUNNING forever.
    _reconcile_main_status(execution_id, main_item)

    details = assemble_execution_details(execution_id, main_item, config_row=config_row)
    # The assembly's budgets are per-collection estimates; this measures the payload that will actually
    # be sent and trims until it fits, so a response cannot exceed the Lambda limit (a 502 with no body,
    # and none of the truncation flags) on structure no collection was charged for.
    _enforce_detail_payload_ceiling(details)
    return success(body={'message': details})


# ---------------------------------------------------------------------------
# Paged detail metadata (one collection at a time)
# ---------------------------------------------------------------------------

def _detail_metadata_step_order(execution_id, pipeline_id_filter=""):
    """The execution's pipeline executions in a STABLE order, as (pipelineExecutionId, pipelineId).

    Every metadata collection is keyed on pipelineExecutionId, so paging across a multi-step execution
    walks the steps one after another and the continuation token names a POSITION in this order. The
    order must therefore be the same on every request of the same execution, whatever order DynamoDB
    returns the rows in — sorted by pipelineExecutionId, which is unique per step. Sorting on the start
    date would be unstable (two steps of a parallel workflow can share one), and a step's position
    shifting between requests is what makes a token skip or repeat rows.

    `pipeline_id_filter` narrows to one step's rows; the order of what remains is unchanged, so a token
    is only valid alongside the same filter it was issued with."""
    steps = []
    for prow in get_pipeline_execution_rows(execution_id):
        pexec_id = prow.get('pipelineExecutionId', '')
        pipeline_id = prow.get('pipelineId', '')
        if not pexec_id:
            continue
        if pipeline_id_filter and pipeline_id != pipeline_id_filter:
            continue
        steps.append((pexec_id, pipeline_id))
    steps.sort(key=lambda step: step[0])
    return steps


def _encode_detail_metadata_token(step_index, last_evaluated_key, collection="",
                                  pipeline_id_filter=""):
    """Encode the paged read's resume point: which step to continue in, and where within it.

    Both halves are required. A step index alone resumes a partially-read step from its beginning
    (repeating rows), and a LastEvaluatedKey alone cannot say which step it belongs to. `stepKey` pins
    the index to the step it was computed for, so a token issued before the execution gained a step is
    detected rather than silently applied to a different step.

    The token also carries the query it was issued for. Each collection reads a DIFFERENT table and
    each pipelineId filter a different step list, so a resume point is only meaningful against the same
    pair: replaying an inputMetadata token against the output collection would apply one table's key to
    another, and replaying a filterless token under a filter would index into a shorter step list. Both
    serve wrong rows with a 200, which is worse than an error."""
    return base64.b64encode(json.dumps({
        "stepIndex": step_index,
        "stepKey": last_evaluated_key.get("pipelineExecutionId", "") if last_evaluated_key else "",
        "lastEvaluatedKey": last_evaluated_key or None,
        "collection": collection or "",
        "pipelineIdFilter": pipeline_id_filter or "",
    }).encode("utf-8")).decode("utf-8")


def _decode_detail_metadata_token(token, steps, collection="", pipeline_id_filter=""):
    """Decode a paged-read continuation token into (step_index, last_evaluated_key), or None when it
    cannot be used against `steps`.

    Returning None is a caller error (a 400), never a silent restart at page 1: continuing without the
    token would re-serve rows the caller already has, and continuing with a stale step index would skip
    rows. The stored stepKey is re-checked against the step now at that index, so a token whose step
    order has changed underneath it is refused rather than resumed in the wrong step.

    The token is also refused when the collection or pipelineId filter it was issued for differs from
    the one now being read. Each collection reads a different table and each filter a different step
    list, so a cross-applied resume point serves rows from the wrong query with a 200 rather than
    failing — silently skipping or repeating rows. Both are matched exactly, which also refuses a token
    minted before this binding existed: such a token cannot prove which query produced it, and a walk
    that restarts is recoverable where one served the wrong rows is not."""
    decoded = _decode_starting_token(token)
    if decoded is None:
        return None
    if (decoded.get("collection", "") or "") != (collection or ""):
        return None
    if (decoded.get("pipelineIdFilter", "") or "") != (pipeline_id_filter or ""):
        return None
    try:
        step_index = int(decoded.get("stepIndex", -1))
    except (TypeError, ValueError):
        return None
    if step_index < 0 or step_index > len(steps):
        return None
    last_evaluated_key = decoded.get("lastEvaluatedKey") or None
    if last_evaluated_key is not None and not isinstance(last_evaluated_key, dict):
        return None
    step_key = decoded.get("stepKey", "") or ""
    if step_key:
        # A within-step resume point only means anything in the step it came from.
        if step_index >= len(steps) or steps[step_index][0] != step_key:
            return None
    return step_index, last_evaluated_key


def _detail_metadata_collection_source(collection):
    """(table_name, scrub, row_scope_filter) for a detail metadata collection.

    row_scope_filter is None when the table holds only this collection's rows, and otherwise the
    predicate selecting this collection's subset of the shared input-metadata table — 'input' is the
    scope=='asset' rows and 'inputDatabase' the scope=='database' rows, matching the split the detail
    view makes. A row stored before the discriminator existed reads as asset metadata, exactly as
    _scrub_input_metadata defaults it."""
    if collection == DETAIL_METADATA_COLLECTION_OUTPUT:
        return pipeline_execution_output_metadata_table, _scrub_output_metadata, None
    if collection == DETAIL_METADATA_COLLECTION_INPUT_DATABASE:
        return (pipeline_execution_input_metadata_table, _scrub_input_metadata,
                lambda row: (row.get('scope', '') or INPUT_METADATA_SCOPE_ASSET)
                == INPUT_METADATA_SCOPE_DATABASE)
    return (pipeline_execution_input_metadata_table, _scrub_input_metadata,
            lambda row: (row.get('scope', '') or INPUT_METADATA_SCOPE_ASSET)
            != INPUT_METADATA_SCOPE_DATABASE)


def page_detail_metadata(execution_id, collection, page_size, token, pipeline_id_filter=""):
    """One page of a detail metadata collection, walking the execution's steps in a stable order.

    Returns {"Items": [...], "collection": ..., ["NextToken": ...]}. Rows carry the same scrubbed shape
    the details view returns, plus the producing pipelineId, so a client renders them with identical
    columns.

    Rows are read step by step. A step whose rows fill the page yields a token holding both the step
    position and that step's own LastEvaluatedKey, so the next request continues inside the same step;
    a step read to exhaustion advances the position instead. Nothing is dropped by a bound here — the
    page ends and the token resumes at the first row left out, which is what makes the walk complete
    across every step rather than only bounded within one.

    Raises VAMSGeneralErrorResponse for an unusable token (the handler maps it to a 400)."""
    steps = _detail_metadata_step_order(execution_id, pipeline_id_filter)
    table_name, scrub, scope_filter = _detail_metadata_collection_source(collection)

    step_index, last_evaluated_key = 0, None
    if token:
        resumed = _decode_detail_metadata_token(token, steps, collection, pipeline_id_filter)
        if resumed is None:
            raise VAMSGeneralErrorResponse("startingToken is invalid.")
        step_index, last_evaluated_key = resumed

    table = dynamodb.Table(table_name)
    items = []
    used_bytes = 0
    scanned = 0
    # Set when a bound (page size / bytes / scan cap) ends the page with rows still unread, carrying the
    # exact resume point so the next page starts at the first row this one left out.
    next_token = None

    while step_index < len(steps) and next_token is None:
        pexec_id, pipeline_id = steps[step_index]
        # The scope-filtered collections read a fixed query page rather than only the rows still needed:
        # Limit applies before the scope split, so sizing it to the remainder would take one query per
        # matching row whenever the subset is sparse.
        remaining = page_size - len(items)
        query_limit = (DETAIL_METADATA_QUERY_PAGE_SIZE if scope_filter is not None
                       else max(1, remaining))
        kwargs = {'KeyConditionExpression': Key('pipelineExecutionId').eq(pexec_id),
                  'Limit': query_limit}
        if last_evaluated_key is not None:
            kwargs['ExclusiveStartKey'] = last_evaluated_key
        resp = table.query(**kwargs)
        rows = resp.get('Items', [])
        step_last_key = resp.get('LastEvaluatedKey')
        # Where a bound firing inside this query page resumes: the key of the last row this page
        # RETURNED. It starts as the key this query itself continued after, so a bound that fires before
        # anything here is returned resumes exactly where this query did — the rows in this page are then
        # re-read on the next request rather than stepped over. It advances only on a returned row, so a
        # row skipped by the scope filter never becomes a resume point for rows that follow it.
        resume_after = last_evaluated_key

        for row in rows:
            scanned += 1
            if scope_filter is not None and not scope_filter(row):
                continue
            scrubbed = scrub(row)
            scrubbed["pipelineId"] = pipeline_id
            row_bytes = _detail_metadata_row_bytes(scrubbed)
            # Keep at least one row per page: a page that came back empty while a token said there was
            # more would stall the walk on a single oversized row.
            if items and (len(items) >= page_size
                          or used_bytes + row_bytes > MAX_DETAIL_METADATA_PAGE_BYTES):
                # Resume at THIS row, not after it: it has not been returned.
                next_token = _encode_detail_metadata_token(step_index, resume_after, collection,
                                                           pipeline_id_filter)
                break
            items.append(scrubbed)
            used_bytes += row_bytes
            resume_after = _detail_metadata_resume_key(row, pexec_id)
        if next_token is not None:
            break

        if step_last_key is not None:
            # More rows in this step. Continue in it — on this pass when the page has room, on the next
            # request otherwise.
            last_evaluated_key = step_last_key
            if len(items) >= page_size or scanned >= MAX_DETAIL_METADATA_ROWS_SCANNED:
                next_token = _encode_detail_metadata_token(step_index, step_last_key, collection,
                                                           pipeline_id_filter)
            continue

        # This step is exhausted; the next page (or this one) starts the following step at its beginning.
        step_index += 1
        last_evaluated_key = None
        if step_index < len(steps) and (len(items) >= page_size
                                        or scanned >= MAX_DETAIL_METADATA_ROWS_SCANNED):
            next_token = _encode_detail_metadata_token(step_index, None, collection,
                                                       pipeline_id_filter)

    result = {"Items": items, "collection": collection}
    # Absent on the last page: its presence is what tells a client there is more, so it must never be
    # emitted for a walk that reached the final step's final row.
    if next_token is not None:
        result["NextToken"] = next_token
    return result


def _detail_metadata_row_bytes(row):
    """Wire size of one scrubbed metadata row (see _wire_bytes), the units MAX_DETAIL_METADATA_PAGE_BYTES
    is expressed in — the page's rows are embedded in the body string, so their escapes are re-escaped on
    the way out just as the detail view's are."""
    return _wire_bytes(row)


def _detail_metadata_resume_key(row, pexec_id):
    """The ExclusiveStartKey that resumes a step's query AFTER `row`, built from the row's own primary
    key. The sort-key attribute differs per collection (the input table sorts on
    'databaseId:assetId:filePath', the output table on 'targetFilePath:metadataKey'), so it is taken
    from the row rather than named here — which also keeps this correct for a row shape that gains a
    different sort key."""
    key = {'pipelineExecutionId': row.get('pipelineExecutionId', '') or pexec_id}
    for attr in ('databaseId:assetId:filePath', 'targetFilePath:metadataKey'):
        if attr in row:
            key[attr] = row[attr]
    return key


def get_execution_details_metadata(event, execution_id, query_params):
    """Return one page of one of an execution's metadata collections (404 if the execution is unknown).

    Authorization is the SAME Tier-2 rule the details view enforces (workflow GET, GET on every captured
    metadata-source database, GET on every asset the run read), evaluated for GET — so exactly the
    callers who can open the details page can page its metadata, and no others."""
    main_item = get_execution_main_row(execution_id)
    if not main_item:
        return validation_error(status_code=404, body={'message': "Execution not found"}, event=event)

    config_row = get_workflow_execution_configuration_row(execution_id)
    allowed, reason = authorize_execution_access(execution_id, main_item, "GET",
                                                 config_row=config_row)
    if not allowed:
        logger.info(f"Details metadata access not authorized for execution {execution_id}: {reason}")
        return authorization_error()

    request = parse(dict(query_params or {}), model=DetailMetadataPageRequestModel)
    # Clamped rather than rejected (as the execution lists treat pageSize): the cap bounds the RESPONSE
    # size, so an over-large request is answered with a bounded page and its NextToken.
    page_size = min(request.pageSize or DEFAULT_DETAIL_METADATA_PAGE_SIZE,
                    MAX_DETAIL_METADATA_PAGE_SIZE)
    result = page_detail_metadata(
        execution_id, request.collection, page_size, request.resolved_starting_token(),
        pipeline_id_filter=request.pipelineId or "")
    return success(body={'message': result})


def _reconcile_main_status(execution_id, main_item):
    """Lazily reconcile a non-terminal main row's status against Step Functions (in place). No-op
    when already terminal or polled within SFN_SYNC_MIN_INTERVAL_SECONDS. Best-effort."""
    if main_item.get("executionStopDate") or main_item.get("executionStatus", "") in TERMINAL_STATUSES:
        return
    last_sync = main_item.get("lastSfnSyncCheckDate", "")
    if er.iso_seconds_since(last_sync) < SFN_SYNC_MIN_INTERVAL_SECONDS:
        return
    arn = main_item.get("workflow_execution_arn", "")
    if not arn:
        return
    try:
        described = sfn.describe_execution(executionArn=arn)
    except Exception as e:
        logger.info(f"Details status reconcile poll failed (non-critical): {e}")
        return
    main_item["lastSfnSyncCheckDate"] = er.iso_now()
    status = described.get("status", main_item.get("executionStatus", ""))
    sfn_stop = described.get("stopDate")
    if sfn_stop:
        stop_date = sfn_stop.strftime("%Y-%m-%dT%H:%M:%SZ") if hasattr(sfn_stop, "strftime") else str(sfn_stop)
        main_item["executionStopDate"] = stop_date
        main_item["executionStatus"] = status
    else:
        # Still running: keep RUNNING (never regress to NEW) and persist the sync-check stamp.
        main_item["executionStatus"] = status or main_item.get("executionStatus", "")
    try:
        _persist_reconciled_main_row(
            dynamodb.Table(workflow_execution_database_v2), main_item,
            DETAIL_RECONCILED_MAIN_ROW_ATTRIBUTES)
    except Exception as e:
        logger.info(f"Could not persist reconciled main row (non-critical): {e}")


def _log_search_window_start(main_item):
    """Epoch-ms lower bound for a live log search: the execution's own start, less a small margin.

    The margin covers clock skew between the recorded start and the first emitted event. Returns None
    when the row carries no parseable start date, which leaves the search unbounded (the prior
    behavior) rather than guessing a window."""
    raw = (main_item or {}).get('executionStartDate', '') or ''
    if not raw:
        return None
    try:
        parsed = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    return int(parsed.timestamp() * 1000) - LOG_SEARCH_WINDOW_MARGIN_MS


def _full_log_search(log_group_arn, filter_terms, query_params, default_start_time=None):
    """Live CloudWatch FilterLogEvents search within the shared workflow log group.

    filter_terms is the list of REQUIRED literal terms the search is scoped to (e.g. the
    execution id, and -- for a pipeline-scoped search -- the pipeline execution id). Every
    term is AND-ed into the filter pattern so results are restricted to exactly that
    execution (and pipeline, when given) and nothing else. An optional caller filterPattern
    is appended as an additional term. Returns {events, nextToken}.

    default_start_time (epoch ms) bounds the search when the caller supplies no startTime. The
    workflow log group is SHARED by every state machine and retained for ten years, and
    FilterLogEvents spends a bounded scan budget across streams in the group: on a group holding
    older streams, an unbounded search returns those streams' events and reports nothing for a run
    that finished seconds ago, even though the events are present (verified against a group where a
    stream-scoped query returned 12 events and the same unbounded group-wide query returned 0).
    Anchoring on the execution's own start makes the search look where its events actually are."""
    if not log_group_arn:
        return {"events": [], "nextToken": None}
    parts = log_group_arn.split(":log-group:")
    if len(parts) < 2:
        return {"events": [], "nextToken": None}
    log_group_name = parts[1]
    if log_group_name.endswith(":*"):
        log_group_name = log_group_name[:-2]

    # Build an AND-ed term filter pattern. Each required scope term is quoted so the match
    # is on the literal id; this is what guarantees a pipeline-scoped search returns only
    # that pipeline execution's events.
    terms = [f'"{t}"' for t in filter_terms if t]
    # The caller's filterPattern is treated as a single LITERAL term (a substring to match),
    # NOT raw CloudWatch filter-pattern syntax. Embedded double-quotes are stripped so it cannot
    # break out of the quoted term and inject OR (`?`)/negation that would neutralize the AND-ed
    # execution-scope terms above and surface other executions' events from the shared log group.
    caller_pattern = (query_params.get('filterPattern') or '').strip().replace('"', '')
    if caller_pattern:
        terms.append(f'"{caller_pattern}"')
    filter_pattern = " ".join(terms)

    kwargs = {
        'logGroupName': log_group_name,
        'limit': min(int(query_params.get('limit', 100) or 100), MAX_LOG_EVENTS_PER_CALL),
    }
    if filter_pattern:
        kwargs['filterPattern'] = filter_pattern
    if query_params.get('startTime'):
        kwargs['startTime'] = int(query_params['startTime'])
    elif default_start_time:
        # Caller's explicit startTime always wins; this only fills the unbounded case.
        kwargs['startTime'] = int(default_start_time)
    if query_params.get('endTime'):
        kwargs['endTime'] = int(query_params['endTime'])
    if query_params.get('nextToken'):
        kwargs['nextToken'] = query_params['nextToken']

    try:
        resp = logs_client.filter_log_events(**kwargs)
    except Exception as e:
        logger.info(f"Full log search failed (non-critical): {e}")
        return {"events": [], "nextToken": None}

    events = [
        {"timestamp": e.get('timestamp'), "message": e.get('message', '')}
        for e in resp.get('events', [])
    ]
    return {"events": events, "nextToken": resp.get('nextToken')}


def step_invocation_log_group_arn(pipeline_row, reference_log_group_arn=""):
    """The log group of the resource a step INVOKED, derived from what the execute path already
    recorded — or "" when the step's execution type has no reachable invocation log.

    This is the step's SECONDARY log: the primary per-step log is whatever the pipeline's own
    sub-process registered, whereas this is the log of the invocation the top-level state machine made.
    For a Lambda step (every use-case pipeline's vamsExecute) that log exists and holds the reason a
    launch failed, but nothing pointed at it, so it was unreachable from the execution view.

    Supported by execution type:
      Lambda        -> /aws/lambda/{functionName}, derived from pipelineResourceArn.
      SQS           -> none. A queue has no invocation log; the CONSUMER's log is a separate resource
                       VAMS does not own, and a pipeline that wants it can register it explicitly.
      EventBridge   -> none, for the same reason: the bus does not log deliveries by default.
      DeadlineCloud -> none here. Its job logs live in Deadline Cloud's own session logs, reachable
                       through the job, not through a CloudWatch group derivable from the pipeline.
    Returning "" for those is deliberate: an empty section labelled "no log" is worse than no section.
    """
    row = pipeline_row or {}
    if (row.get("pipelineExecutionType") or "Lambda") != "Lambda":
        return ""
    resource = row.get("pipelineResourceArn") or ""
    if not resource:
        return ""
    # pipelineResourceArn holds either a bare function name or a full function ARN.
    function_name = resource.split(":function:")[-1].split(":")[0] if ":" in resource else resource
    if not function_name:
        return ""
    # Partition / region / account come from an ARN rather than being assumed: the resource's own ARN
    # when it is one, else the execution's log-group ARN (always same-account, same-partition, and
    # already on the main row). Nothing is hard-coded, so this holds in GovCloud and ISO partitions.
    partition, region, account = "", os.environ.get("AWS_REGION", ""), ""
    for candidate in (resource, reference_log_group_arn):
        if not candidate.startswith("arn:"):
            continue
        parts = candidate.split(":")
        if len(parts) > 4 and parts[4]:
            partition = partition or parts[1]
            region = parts[3] or region
            account = account or parts[4]
            break
    if not (partition and region and account):
        return ""
    return f"arn:{partition}:logs:{region}:{account}:log-group:/aws/lambda/{function_name}:*"


def _fetch_registered_log_events(log_group_arn, log_stream_name, query_params, log_stream_prefix="",
                                 scope_terms=None, default_start_time=None):
    """Best-effort fetch of events from a registered sub-process log location. Returns
    (ok, events) on success or (False, reason) on a real failure (e.g. AccessDenied), never
    raising; the caller surfaces a failure as a warning.

    Scoping precedence within the log group: an exact logStreamName (one stream) takes priority;
    otherwise a logStreamPrefix narrows to streams under that prefix (e.g. an AWS Batch/ECS task
    family); with neither, the whole group is read. `scope_terms` (e.g. an execution id) are AND-ed
    into the filter pattern as required literal terms so a group SHARED across executions (a nested
    state machine's own log group) returns only this execution's events, not every execution's."""
    parts = (log_group_arn or "").split(":log-group:")
    if len(parts) < 2:
        return False, "unparseable log group ARN"
    log_group_name = parts[1]
    if log_group_name.endswith(":*"):
        log_group_name = log_group_name[:-2]
    kwargs = {
        'logGroupName': log_group_name,
        'limit': min(int(query_params.get('limit', 100) or 100), MAX_LOG_EVENTS_PER_CALL),
    }
    # Scope to an exact stream when reported; else to a stream prefix; else read the whole group.
    if log_stream_name:
        kwargs['logStreamNames'] = [log_stream_name]
    elif log_stream_prefix:
        kwargs['logStreamNamePrefix'] = log_stream_prefix
    filter_pattern = " ".join(f'"{t}"' for t in (scope_terms or []) if t)
    if filter_pattern:
        kwargs['filterPattern'] = filter_pattern
    if query_params.get('startTime'):
        kwargs['startTime'] = int(query_params['startTime'])
    elif default_start_time:
        # Same reason as _full_log_search: a registered group (e.g. a shared Lambda log group) can
        # hold far older streams, and an unbounded search spends its scan budget there.
        kwargs['startTime'] = int(default_start_time)
    if query_params.get('endTime'):
        kwargs['endTime'] = int(query_params['endTime'])
    try:
        resp = logs_client.filter_log_events(**kwargs)
    except botocore.exceptions.ClientError as e:
        code = e.response.get('Error', {}).get('Code', '')
        logger.warning(f"Could not read registered log {log_group_arn}: {e}")
        return False, code or str(e)
    except Exception as e:
        logger.warning(f"Could not read registered log {log_group_arn}: {e}")
        return False, str(e)
    return True, [
        {"timestamp": e.get('timestamp'), "message": e.get('message', ''),
         "logGroupArn": log_group_arn}
        for e in resp.get('events', [])
    ]


# A small set of Step Functions history event types that summarize what an execution did, kept
# concise so the whole-execution view reads as a state-transition timeline rather than raw history.
_SFN_HISTORY_SUMMARY_TYPES = (
    "ExecutionStarted", "ExecutionSucceeded", "ExecutionFailed", "ExecutionAborted",
    "ExecutionTimedOut",
    "TaskStateEntered", "TaskStateExited", "TaskSucceeded", "TaskFailed", "TaskTimedOut",
    "TaskScheduled", "TaskStarted",
    "MapStateEntered", "MapStateExited", "ParallelStateEntered", "ParallelStateExited",
    "PassStateEntered", "PassStateExited", "ChoiceStateEntered", "ChoiceStateExited",
    "WaitStateEntered", "WaitStateExited", "SucceedStateEntered", "FailStateEntered",
)


def _sfn_history_event_line(ev):
    """One human-readable timeline line for a Step Functions history event: the state/resource name
    when the event carries one, plus a failure error/cause when present. Returns "" to skip an event
    that has no useful summary detail."""
    ev_type = ev.get("type", "")
    # State name lives on the *StateEntered/*StateExited detail blocks.
    for detail_key in ("stateEnteredEventDetails", "stateExitedEventDetails"):
        detail = ev.get(detail_key)
        if detail and detail.get("name"):
            return f"{ev_type}: {detail['name']}"
    # Task events carry resource + resourceType.
    for detail_key in ("taskScheduledEventDetails", "taskStartedEventDetails",
                       "taskSucceededEventDetails", "taskFailedEventDetails",
                       "taskTimedOutEventDetails"):
        detail = ev.get(detail_key)
        if detail:
            resource = detail.get("resource", "") or detail.get("resourceType", "")
            err = detail.get("error", "")
            cause = detail.get("cause", "")
            suffix = f" — {err}: {cause}" if err else ""
            return f"{ev_type}{(' ' + resource) if resource else ''}{suffix}"
    # Execution-level failure/abort/timeout carry an error + cause.
    for detail_key in ("executionFailedEventDetails", "executionAbortedEventDetails",
                       "executionTimedOutEventDetails"):
        detail = ev.get(detail_key)
        if detail:
            err = detail.get("error", "")
            cause = detail.get("cause", "")
            return f"{ev_type}{(' — ' + err + ': ' + cause) if err else ''}"
    return ev_type


def _sfn_execution_history_events(execution_arn, query_params):
    """The Step Functions execution history as a formatted, chronological event list — the
    authoritative record of what the whole workflow execution did, available immediately (no
    CloudWatch ingestion lag). Returns {"events": [{timestamp, message}], "nextToken": ...}; empty
    on any failure (best-effort, never raises). Only summary-worthy event types are kept."""
    if not execution_arn:
        return {"events": [], "nextToken": None}
    kwargs = {
        "executionArn": execution_arn,
        "maxResults": min(int(query_params.get("limit", 100) or 100), MAX_LOG_EVENTS_PER_CALL),
        "includeExecutionData": False,
    }
    if query_params.get("nextToken"):
        kwargs["nextToken"] = query_params["nextToken"]
    try:
        resp = sfn.get_execution_history(**kwargs)
    except Exception as e:
        logger.info(f"SFN get_execution_history failed (non-critical): {e}")
        return {"events": [], "nextToken": None}
    events = []
    for ev in resp.get("events", []):
        if ev.get("type", "") not in _SFN_HISTORY_SUMMARY_TYPES:
            continue
        line = _sfn_history_event_line(ev)
        if not line:
            continue
        ts = ev.get("timestamp")
        # describe/history timestamps are datetimes; expose epoch millis like CloudWatch events.
        ts_ms = int(ts.timestamp() * 1000) if hasattr(ts, "timestamp") else ts
        events.append({"timestamp": ts_ms, "message": line})
    return {"events": events, "nextToken": resp.get("nextToken")}


def _resolve_sfn_log_group_arn(state_machine_arn):
    """Resolve a Step Functions state machine's CloudWatch log group ARN from its logging
    configuration, so a registered sub-SFN's logs can be read even when the pipeline reported only
    the state-machine/execution ARN (no explicit logGroupArn). Returns "" when the state machine has
    no CloudWatch logging destination or on any failure (best-effort, never raises)."""
    if not state_machine_arn:
        return ""
    try:
        desc = sfn.describe_state_machine(stateMachineArn=state_machine_arn)
    except Exception as e:
        logger.info(f"describe_state_machine failed for {state_machine_arn} (non-critical): {e}")
        return ""
    for dest in (desc.get("loggingConfiguration", {}) or {}).get("destinations", []) or []:
        arn = (dest.get("cloudWatchLogsLogGroup", {}) or {}).get("logGroupArn", "")
        if arn:
            return arn
    return ""


def get_execution_logs(event, execution_id, query_params):
    """Return execution logs in one of two modes (404 if the execution is unknown):

      - truncated (default): the stored execution log + error. When a pipelineExecutionId
        is supplied, the stored per-pipeline log record for that pipeline instead.
      - full: a live CloudWatch FilterLogEvents search scoped to this execution (and, when
        pipelineExecutionId is supplied, scoped strictly to that one pipeline execution).

    Authorization mirrors list-executions reads: workflow GET, GET on a captured metadata-source
    database, and GET on every asset the run read (or the asset it wrote to when it read none)."""
    main_item = get_execution_main_row(execution_id)
    if not main_item:
        return validation_error(status_code=404, body={'message': "Execution not found"}, event=event)

    allowed, reason = authorize_execution_access(execution_id, main_item, "GET")
    if not allowed:
        logger.info(f"Logs access not authorized for execution {execution_id}: {reason}")
        return authorization_error()

    mode = (query_params.get('mode') or LOG_MODE_TRUNCATED).strip().lower()
    if mode not in (LOG_MODE_TRUNCATED, LOG_MODE_FULL):
        return validation_error(
            body={'message': f"mode must be '{LOG_MODE_TRUNCATED}' or '{LOG_MODE_FULL}'"}, event=event)

    pipeline_execution_id = (query_params.get('pipelineExecutionId') or '').strip()

    # When a pipeline is specified, confirm it belongs to THIS execution before returning
    # any of its logs. This both validates the request and guarantees a pipeline-scoped
    # search can only ever surface logs for a pipeline of this execution.
    pipeline_row = None
    if pipeline_execution_id:
        for prow in get_pipeline_execution_rows(execution_id):
            if prow.get('pipelineExecutionId', '') == pipeline_execution_id:
                pipeline_row = prow
                break
        if pipeline_row is None:
            return validation_error(
                status_code=404,
                body={'message': "Pipeline execution not found for this execution"}, event=event)

    if mode == LOG_MODE_TRUNCATED:
        if pipeline_execution_id:
            log_rows = _query_all(pipeline_execution_logs_table,
                                  Key('pipelineExecutionId').eq(pipeline_execution_id))
            result_log = ""
            error_log = ""
            for row in log_rows:
                result_log = result_log or row.get('resultLog', '')
                error_log = error_log or row.get('errorLog', '')
            # The end-state lambda captures the stored logs synchronously as the run completes,
            # before CloudWatch has finished ingesting the run's events, so the stored resultLog
            # can be empty even for a succeeded pipeline. Fall back to a live pipeline-scoped search
            # so a caller always gets whatever CloudWatch holds now (logsSource flags the origin).
            logs_source = "stored"
            if not result_log and not error_log:
                live = _full_log_search(
                    main_item.get('executionLogGroupArn', ''),
                    [execution_id, pipeline_execution_id], query_params,
                    default_start_time=_log_search_window_start(main_item))
                if live["events"]:
                    result_log = "\n".join(e.get('message', '') for e in live["events"])
                    logs_source = "live"
            return success(body={'message': {
                "mode": LOG_MODE_TRUNCATED,
                "pipelineExecutionId": pipeline_execution_id,
                "resultLog": redact_log_text(result_log),
                "errorLog": redact_log_text(error_log),
                "logsSource": logs_source,
            }})
        # Whole-execution truncated logs. The stored executionLog is captured before CloudWatch
        # ingestion completes and is frequently empty. Fall back first to a live execution-scoped
        # CloudWatch search, then to the Step Functions execution history — the authoritative record
        # of what the whole execution did, available immediately with no ingestion lag. (The Step
        # Functions state machine's own CloudWatch logs do not reliably carry the executionId as a
        # filterable literal, which is why the CloudWatch search is often empty for the whole run.)
        execution_log = main_item.get('executionLog', '')
        logs_source = "stored"
        if not execution_log:
            live = _full_log_search(
                main_item.get('executionLogGroupArn', ''), [execution_id], query_params,
                default_start_time=_log_search_window_start(main_item))
            if live["events"]:
                execution_log = "\n".join(e.get('message', '') for e in live["events"])
                logs_source = "live"
            else:
                history = _sfn_execution_history_events(
                    main_item.get('workflow_execution_arn', ''), query_params)
                if history["events"]:
                    execution_log = "\n".join(e.get('message', '') for e in history["events"])
                    logs_source = "sfnHistory"
        return success(body={'message': {
            "mode": LOG_MODE_TRUNCATED,
            "executionLog": redact_log_text(execution_log),
            "executionError": redact_log_text(main_item.get('executionError', '')),
            "logsSource": logs_source,
        }})

    # mode == full: live CloudWatch search, strictly scoped to this execution (and pipeline).
    log_group_arn = main_item.get('executionLogGroupArn', '')
    # Lower bound shared by every live read below, so none of them scans unbounded history.
    window_start = _log_search_window_start(main_item)
    scope_terms = [execution_id]
    if pipeline_execution_id:
        scope_terms.append(pipeline_execution_id)
    search = _full_log_search(log_group_arn, scope_terms, query_params,
                              default_start_time=_log_search_window_start(main_item))

    # When scoped to a pipeline, also pull from any sub-process logs that pipeline registered
    # (best-effort; a failure on any registered log is surfaced as a non-fatal warning).
    sub_process_events = []
    warnings = []
    if pipeline_row is not None:
        # Log-group ARNs already read this request, so a group reported in registeredLogs is not
        # re-read when it is also resolved from a sub-execution's state machine (avoids duplicates).
        read_log_group_arns = set()
        # The step's SECONDARY log: the log of the resource the top-level state machine invoked for
        # this step. Derived from what the execute path already recorded (pipelineExecutionType +
        # pipelineResourceArn), so it needs no registration by the pipeline — which is why a
        # vamsExecute lambda's own log was previously unreachable. Empty for SQS / EventBridge /
        # DeadlineCloud, which have no invocation log to read.
        invocation_log_arn = step_invocation_log_group_arn(pipeline_row, log_group_arn)
        if invocation_log_arn:
            read_log_group_arns.add(invocation_log_arn)
            ok, events_or_err = _fetch_registered_log_events(
                invocation_log_arn, "", query_params, scope_terms=scope_terms,
                default_start_time=window_start)
            if ok:
                sub_process_events.extend(events_or_err)
            else:
                # Non-fatal and named: a missing IAM grant on this group must not fail the logs GET,
                # but it should say which log could not be read rather than silently omitting it.
                warnings.append(
                    f"Step invocation log retrieval failed for {invocation_log_arn}: {events_or_err}")

        # Explicitly-registered log locations (logGroupArn reported by the pipeline). Capped so an
        # unbounded registration list cannot turn one logs GET into an unbounded CloudWatch burst,
        # and scoped to this execution: a registered group may be shared across executions of the
        # same pipeline, and an exact stream/prefix narrows streams independently of the terms.
        registered_logs = pipeline_row.get('registeredLogs', []) or []
        for log in registered_logs[:MAX_REGISTERED_LOGS_INSPECTED]:
            log_arn = (log or {}).get('logGroupArn', '')
            stream = (log or {}).get('logStreamName', '')
            stream_prefix = (log or {}).get('logStreamPrefix', '')
            if not log_arn:
                continue
            read_log_group_arns.add(log_arn)
            ok, events_or_err = _fetch_registered_log_events(
                log_arn, stream, query_params, log_stream_prefix=stream_prefix,
                scope_terms=scope_terms, default_start_time=window_start)
            if ok:
                sub_process_events.extend(events_or_err)
            else:
                warnings.append(f"Sub-process log retrieval failed for {log_arn}: {events_or_err}")
        if len(registered_logs) > MAX_REGISTERED_LOGS_INSPECTED:
            warnings.append(
                f"Only the first {MAX_REGISTERED_LOGS_INSPECTED} of {len(registered_logs)} "
                f"registered logs were read.")

        # A registered Step Functions sub-execution: surface ITS execution history (the sub-SFN's
        # own state timeline) and, when the sub state machine has a CloudWatch logging destination
        # that was not already read above, its resolved log group too — scoped to THIS execution so
        # a group shared across executions does not leak other runs' events. Capped as above.
        registered_subs = [
            s for s in (pipeline_row.get('registeredSubExecutions', []) or [])
            if (s or {}).get('resourceType') == RESOURCE_TYPE_STEP_FUNCTIONS_EXECUTION]
        for sub in registered_subs[:MAX_REGISTERED_SUB_EXECUTIONS_INSPECTED]:
            sub_exec_arn = sub.get('executionArn', '')
            if sub_exec_arn:
                sub_hist = _sfn_execution_history_events(sub_exec_arn, query_params)
                sub_process_events.extend(sub_hist["events"])
            resolved_arn = _resolve_sfn_log_group_arn(sub.get('stateMachineArn', ''))
            if resolved_arn and resolved_arn not in read_log_group_arns:
                read_log_group_arns.add(resolved_arn)
                # The nested state machine's log group is shared across all of its executions; scope
                # the read to this execution (and pipeline) so only this run's events are returned.
                ok, events_or_err = _fetch_registered_log_events(
                    resolved_arn, "", query_params, scope_terms=scope_terms,
                    default_start_time=window_start)
                if ok:
                    sub_process_events.extend(events_or_err)
                else:
                    warnings.append(
                        f"Sub-SFN log retrieval failed for {resolved_arn}: {events_or_err}")
        if len(registered_subs) > MAX_REGISTERED_SUB_EXECUTIONS_INSPECTED:
            warnings.append(
                f"Only the first {MAX_REGISTERED_SUB_EXECUTIONS_INSPECTED} of "
                f"{len(registered_subs)} registered sub-executions were read.")

    # Every log string surfaced to the caller passes through the credential redactor first: a
    # CloudWatch/history message can carry an inline token, AWS key, or JWT (see common.logRedaction).
    message = {
        "mode": LOG_MODE_FULL,
        "pipelineExecutionId": pipeline_execution_id,
        "events": redact_log_events(search["events"]),
        "nextToken": search["nextToken"],
    }
    # For the WHOLE execution (no single pipeline in scope), include the Step Functions execution
    # history — the authoritative timeline of the run's state transitions, present even when the
    # CloudWatch text search returns nothing.
    if not pipeline_execution_id:
        history = _sfn_execution_history_events(
            main_item.get('workflow_execution_arn', ''), query_params)
        if history["events"]:
            message["sfnHistoryEvents"] = redact_log_events(history["events"])
    if sub_process_events:
        message["subProcessEvents"] = redact_log_events(sub_process_events)
    if warnings:
        message["warnings"] = warnings
    return success(body={'message': message})


def handle_details_request(event):
    """Validate the executionId path param, enforce API authorization, return details."""
    pathParams = event.get('pathParameters', {}) or {}
    execution_id = pathParams.get('executionId', '')
    if not execution_id:
        return validation_error(body={'message': 'Missing path parameter (executionId) in API call'}, event=event)

    logger.info("Validating path parameters")
    (valid, message) = validate({
        'executionId': {'value': execution_id, 'validator': 'GUID'},
    })
    if not valid:
        logger.error(message)
        return validation_error(body={'message': message}, event=event)

    if not _enforce_api(event):
        return authorization_error()

    logger.info(f"Getting execution details {execution_id}")
    return get_execution_details(event, execution_id)


def handle_details_metadata_request(event):
    """Validate the executionId path param, enforce API authorization, return one page of one of the
    execution's metadata collections."""
    pathParams = event.get('pathParameters', {}) or {}
    queryParameters = event.get('queryStringParameters', {}) or {}
    execution_id = pathParams.get('executionId', '')
    if not execution_id:
        return validation_error(body={'message': 'Missing path parameter (executionId) in API call'}, event=event)

    logger.info("Validating path parameters")
    (valid, message) = validate({
        'executionId': {'value': execution_id, 'validator': 'GUID'},
    })
    if not valid:
        logger.error(message)
        return validation_error(body={'message': message}, event=event)

    if not _enforce_api(event):
        return authorization_error()

    logger.info(f"Getting execution details metadata {execution_id}")
    return get_execution_details_metadata(event, execution_id, queryParameters)


def _numeric_log_param_error(query_params):
    """Message naming the first of limit/startTime/endTime that is not an integer, or "". The log
    readers pass these straight to int(), so a non-numeric value must fail as a 400 here."""
    for name in ('limit', 'startTime', 'endTime'):
        raw = query_params.get(name)
        if raw is None or str(raw).strip() == "":
            continue
        try:
            int(str(raw).strip())
        except ValueError:
            return f"{name} is invalid. Must be an integer."
    return ""


def handle_logs_request(event):
    """Validate the executionId path param, enforce API authorization, return logs."""
    pathParams = event.get('pathParameters', {}) or {}
    queryParameters = event.get('queryStringParameters', {}) or {}
    execution_id = pathParams.get('executionId', '')
    if not execution_id:
        return validation_error(body={'message': 'Missing path parameter (executionId) in API call'}, event=event)

    logger.info("Validating path parameters")
    (valid, message) = validate({
        'executionId': {'value': execution_id, 'validator': 'GUID'},
    })
    if not valid:
        logger.error(message)
        return validation_error(body={'message': message}, event=event)

    message = _numeric_log_param_error(queryParameters)
    if message:
        logger.error(message)
        return validation_error(body={'message': message}, event=event)

    if not _enforce_api(event):
        return authorization_error()

    logger.info(f"Getting execution logs {execution_id}")
    return get_execution_logs(event, execution_id, queryParameters)


def _enforce_api(event):
    """Tier-1 API authorization helper (shared by the detail/log GET handlers)."""
    if len(claims_and_roles["tokens"]) > 0:
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if casbin_enforcer.enforceAPI(event):
            return True
    return False


def _enforce_api_route(event, route_path, method):
    """Tier-1 API authorization against a route OTHER than the one being served, for an operation
    that delegates to a second endpoint. Fails closed on empty tokens."""
    if len(claims_and_roles["tokens"]) == 0:
        return False
    probe = {
        "requestContext": {
            "http": {"method": method, "path": route_path},
            "authorizer": event.get("requestContext", {}).get("authorizer"),
        },
    }
    return CasbinEnforcer(claims_and_roles).enforceAPI(probe)


def handle_delete_request(event):
    """Validate the executionId path param and abort the execution."""
    pathParams = event.get('pathParameters', {}) or {}

    execution_id = pathParams.get('executionId', '')
    if not execution_id:
        return validation_error(body={'message': 'Missing path parameter (executionId) in API call'}, event=event)

    logger.info("Validating path parameters")
    (valid, message) = validate({
        'executionId': {'value': execution_id, 'validator': 'GUID'},
    })
    if not valid:
        logger.error(message)
        return validation_error(body={'message': message}, event=event)

    # Tier-1 API authorization.
    method_allowed_on_api = False
    if len(claims_and_roles["tokens"]) > 0:
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if casbin_enforcer.enforceAPI(event):
            method_allowed_on_api = True

    if not method_allowed_on_api:
        return authorization_error()

    logger.info(f"Aborting workflow execution {execution_id}")
    return abort_execution(event, execution_id)


def handle_get_request(event):
    """Validate path/body params, enforce API authorization, and list executions.

    Note on ordering: parameter validation runs BEFORE the Tier-1 enforceAPI check
    here (the reverse of the gold-standard placement) to preserve this endpoint's
    existing response behavior exactly -- a malformed request returns its 400
    validation error regardless of the caller's API authorization.
    """
    pathParams = event.get('pathParameters', {}) or {}
    queryParameters = event.get('queryStringParameters', {})

    # Set 50 maxItems/pageSize to avoid performance issues with state machine API throttling.
    validate_pagination_info(queryParameters, 50)

    # workflowId is optional in the path (list all of the asset's executions when absent).
    workflowId = pathParams.get('workflowId', '')

    # Parse the request body to JSON (optional workflowDatabaseId filter).
    body = {}
    if event.get('body'):
        try:
            body = json.loads(event['body'])
            logger.info(f"Request body: {body}")
        except json.JSONDecodeError as e:
            logger.exception(f"Invalid JSON in request body: {e}")
            return validation_error(body={'message': 'Invalid JSON in request body'}, event=event)

    # Validate path parameters first, then the body fields (workflowDatabaseId), matching
    # the original combined-validation ordering so error messages are unchanged.
    logger.info("Validating path parameters")
    (valid, message) = validate({
        'workflowId': {'value': workflowId, 'validator': 'ID', 'optional': True},
        'assetId': {'value': pathParams.get('assetId', ''), 'validator': 'ASSET_ID'},
        'databaseId': {'value': pathParams.get('databaseId', ''), 'validator': 'ID'},
    })
    if not valid:
        logger.error(message)
        return validation_error(body={'message': message}, event=event)

    # The query-parameter form of the workflow filter (get_executions applies these as equality
    # filters). Validated to the same rule as the path/body form: an unvalidated value would be
    # compared as-is and return an empty list, which is indistinguishable from "this asset has no
    # executions for that workflow" — the caller could not tell a typo from a genuine empty result.
    (valid, message) = validate({
        'workflowId': {
            'value': (queryParameters.get('workflowId') or '') if queryParameters else '',
            'validator': 'ID', 'optional': True,
        },
        'workflowDatabaseId': {
            'value': (queryParameters.get('workflowDatabaseId') or '') if queryParameters else '',
            'validator': 'ID', 'allowGlobalKeyword': True, 'optional': True,
        },
    })
    if not valid:
        logger.error(message)
        return validation_error(body={'message': message}, event=event)

    # Body field validation (workflowDatabaseId) via the request model.
    request_model = parse(body, model=ListExecutionsRequestModel)
    workflow_database_id = request_model.workflowDatabaseId

    # Tier-1 API authorization.
    method_allowed_on_api = False
    if len(claims_and_roles["tokens"]) > 0:
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if casbin_enforcer.enforceAPI(event):
            method_allowed_on_api = True

    if not method_allowed_on_api:
        return authorization_error()

    logger.info("Listing Workflow Executions")
    return get_executions(
        event, pathParams.get('databaseId'), pathParams['assetId'],
        workflow_database_id, workflowId, queryParameters)


# ---------------------------------------------------------------------------
# Global (asset-less) execution list
# ---------------------------------------------------------------------------

def _execution_visible_to_caller(execution_id, main_item, casbin_enforcer=None, config_row=None,
                                 config_row_loader=None):
    """True when the caller may see an execution in a list: the same rule the details/logs paths
    enforce, evaluated for GET. A listed row therefore never 403s when it is opened. Empty tokens ->
    not visible.

    `casbin_enforcer` may be passed in so a batch caller (the global list) builds one enforcer for the
    whole page instead of one per row; `config_row` / `config_row_loader` share the caller's single
    configuration read (see _execution_access_check)."""
    allowed, _reason = _execution_access_check(
        execution_id, main_item, "GET", config_row=config_row,
        config_row_loader=config_row_loader, casbin_enforcer=casbin_enforcer)
    return allowed


def _global_list_matches_filters(main_item, filters):
    """Apply the optional global-list query filters to a main row. Supported filters (all AND-ed):
    workflowId, workflowDatabaseId, status, triggerType, groupId, triggeredByUserId."""
    if filters.get("workflowId") and main_item.get("workflowId", "") != filters["workflowId"]:
        return False
    if filters.get("workflowDatabaseId") and main_item.get("workflowDatabaseId", "") != filters["workflowDatabaseId"]:
        return False
    if filters.get("status") and main_item.get("executionStatus", "") != filters["status"]:
        return False
    if filters.get("triggerType") and main_item.get("triggerType", "") != filters["triggerType"]:
        return False
    if filters.get("groupId") and main_item.get("executionGroupId", "") != filters["groupId"]:
        return False
    if filters.get("triggeredByUserId") and main_item.get("triggeredByUserId", "") != filters["triggeredByUserId"]:
        return False
    return True


def _global_list_row_key(main_item):
    """The ExclusiveStartKey that resumes the by-date GSI query after this row.

    A GSI continuation names both the index's own keys and the base table's, so a synthesized one
    carries all four. Returns None when the row is missing any of them, so a malformed row yields no
    token rather than one that resumes from the wrong place."""
    key = {
        "allListPartition": main_item.get("allListPartition"),
        "executionStartDate": main_item.get("executionStartDate"),
        "workflowExecutionId": main_item.get("workflowExecutionId"),
        "workflowDatabaseId:workflowId": main_item.get("workflowDatabaseId:workflowId"),
    }
    return key if all(key.values()) else None


def _global_list_row(main_item, config_row=None):
    """Public-facing global-list row (no S3/ARN internals).

    The output target (`outputLocationType` / `outputAssetId` / `outputDatabaseId`) lives on the
    execution's CONFIGURATION row, not on this main row. It is threaded in from the caller rather than
    read here so the projection shares whatever read the visibility check already needed, keeping a
    listed row at ONE configuration read rather than two. The caller reads it lazily, so a row that is
    filtered out never pays for one at all."""
    config_row = config_row or {}
    return {
        "workflowExecutionId": main_item.get("workflowExecutionId", ""),
        "workflowId": main_item.get("workflowId", ""),
        "workflowDatabaseId": main_item.get("workflowDatabaseId", ""),
        "executionStatus": main_item.get("executionStatus", ""),
        "executionStartDate": main_item.get("executionStartDate", ""),
        "executionStopDate": main_item.get("executionStopDate", ""),
        "triggerType": main_item.get("triggerType", ""),
        "triggeredByUserId": main_item.get("triggeredByUserId", ""),
        "executionGroupId": main_item.get("executionGroupId", ""),
        "outputLocationType": config_row.get("outputLocationType", ""),
        "outputAssetId": config_row.get("outputAssetId", ""),
        "outputDatabaseId": config_row.get("outputDatabaseId", ""),
    }


def get_global_executions(event, query_params):
    """List executions across all assets (asset-less), permission-filtered by the caller's access to
    each execution's input and/or output assets. Queries the by-date GSI newest-first (bounded by the
    date-range key condition), applies the optional equality filters + per-execution visibility check,
    and returns a NextToken page (Rule 15)."""
    filters = {
        "workflowId": (query_params.get("workflowId") or "").strip(),
        "workflowDatabaseId": (query_params.get("workflowDatabaseId") or "").strip(),
        "status": (query_params.get("status") or "").strip(),
        "triggerType": (query_params.get("triggerType") or "").strip(),
        "groupId": (query_params.get("groupId") or "").strip(),
        "triggeredByUserId": (query_params.get("triggeredByUserId") or "").strip(),
    }
    # Recency window (default 90 days) / custom range, applied as the GSI sort-key condition.
    filter_start_date = _resolve_filter_start_date(query_params)
    filter_end_date = _resolve_date_filter(query_params, "filterEndDate") or ""
    # pageSize/maxItems were normalized to valid ints by validate_pagination_info in the handler;
    # cap the page so a single request cannot drive the per-candidate authorization fan-out
    # (input-asset + output-asset reads + Casbin enforce per row) over an unbounded page.
    try:
        page_size = int(query_params.get("pageSize") or query_params.get("maxItems") or 50)
    except (TypeError, ValueError):
        page_size = 50
    page_size = min(max(1, page_size), MAX_GLOBAL_LIST_PAGE_SIZE)
    main_table = dynamodb.Table(workflow_execution_database_v2)

    # By-date GSI key condition: constant partition + executionStartDate range (newest-first below).
    key_cond = Key("allListPartition").eq(er.ALL_EXECUTIONS_LIST_PARTITION)
    if filter_start_date and filter_end_date:
        key_cond = key_cond & Key("executionStartDate").between(filter_start_date, filter_end_date)
    elif filter_start_date:
        key_cond = key_cond & Key("executionStartDate").gte(filter_start_date)
    elif filter_end_date:
        key_cond = key_cond & Key("executionStartDate").lte(filter_end_date)

    query_kwargs = {
        "IndexName": "WorkflowExecutionsByDateGSI",
        "KeyConditionExpression": key_cond,
        "ScanIndexForward": False,  # newest first
        "Limit": page_size,
    }
    # Equality filters (status/trigger/workflow/group/user) as a FilterExpression so unmatched rows
    # drop before the per-row visibility fan-out. The Python filter below stays as a safety net.
    _filter_attr = {
        "workflowId": "workflowId", "workflowDatabaseId": "workflowDatabaseId",
        "status": "executionStatus", "triggerType": "triggerType",
        "groupId": "executionGroupId", "triggeredByUserId": "triggeredByUserId",
    }
    filter_expr = None
    for fkey, attr_name in _filter_attr.items():
        if filters.get(fkey):
            cond = Attr(attr_name).eq(filters[fkey])
            filter_expr = cond if filter_expr is None else (filter_expr & cond)
    if filter_expr is not None:
        query_kwargs["FilterExpression"] = filter_expr
    starting_token = query_params.get("startingToken") or query_params.get("NextToken")
    if starting_token:
        decoded = _decode_starting_token(starting_token)
        if decoded is None:
            return validation_error(body={"message": "startingToken is invalid."}, event=event)
        query_kwargs["ExclusiveStartKey"] = decoded

    # Dedup by workflowExecutionId (one main row per execution; guard defensively).
    items = []
    seen = set()
    # One CasbinEnforcer + a fresh per-request asset cache for the whole page: the visibility check
    # re-reads the same few assets and re-evaluates the same policy across many rows, so build the
    # enforcer once and memoize both the asset lookups and the decisions rather than repeating them per
    # row. The decision memo belongs to the enforcer built just below, so the page starts with an empty
    # one by construction; it is cleared here as well, so a decision cannot outlive the page even if a
    # future change hands this path a longer-lived enforcer. The entity budget is armed here (and only
    # here): it bounds the breadth of a PAGE, whereas the single-execution paths authorize one run and
    # must not be bounded by it.
    _asset_details_cache.clear()
    _authz_decision_cache.clear()
    _arm_authz_entity_budget()
    page_enforcer = CasbinEnforcer(claims_and_roles) if claims_and_roles.get("tokens") else None
    # The key of the last row this page evaluated. The entity bound can stop a page mid-way through a
    # query that DynamoDB then reports as exhausted, leaving no LastEvaluatedKey to continue from — so
    # the walk carries its own resume point rather than depending on one.
    last_row_key = None
    try:
        resp = main_table.query(**query_kwargs)
        for main_item in resp.get("Items", []):
            execution_id = main_item.get("workflowExecutionId", "")
            if not execution_id or execution_id in seen:
                continue
            seen.add(execution_id)
            last_row_key = _global_list_row_key(main_item)
            if not _global_list_matches_filters(main_item, filters):
                continue
            # AT MOST one configuration read per execution, shared by the visibility check (which
            # authorizes on the metadata sources and the output asset, both recorded there) and the row
            # projection (which reports the output target). Memoized and lazy: a row the caller cannot
            # see at all never reaches the read — eagerly reading here would charge a lookup for every
            # candidate the visibility filter then discards, which for a narrowly-scoped role is most of
            # the page.
            cached_config_row = {}

            def _config_row(execution_id=execution_id, cache=cached_config_row):
                if "item" not in cache:
                    cache["item"] = get_workflow_execution_configuration_row(execution_id)
                return cache["item"]

            if not _execution_visible_to_caller(
                    execution_id, main_item, page_enforcer, config_row_loader=_config_row):
                continue
            items.append(_global_list_row(main_item, _config_row()))
        entity_bound_reached = _authz_entity_budget_exceeded()
    finally:
        # The budget bounds a list page only; leaving it armed would bound a single-execution
        # authorization later in the same invocation.
        _disarm_authz_entity_budget()

    # Echo the applied recency window so the caller can show the active range (matches the per-asset
    # list's filterStartDate echo). filterEndDate is included only when the caller set one.
    result = {"Items": items, "filterStartDate": filter_start_date}
    if filter_end_date:
        result["filterEndDate"] = filter_end_date
    # The continuation DynamoDB reported, or — when the entity bound withheld rows from a query it
    # reported as exhausted — one synthesized from the last row this page evaluated. Without that
    # fallback the withheld executions would be unreachable rather than deferred: the bound is spent per
    # request, so the next page resolves its own entities and reaches them.
    next_key = resp.get("LastEvaluatedKey")
    if next_key is None and entity_bound_reached:
        next_key = last_row_key
    if next_key is not None:
        result["NextToken"] = base64.b64encode(
            json.dumps(next_key).encode("utf-8")).decode("utf-8")
    if entity_bound_reached:
        # The page reached the distinct-entity bound, so rows whose assets it did not resolve were
        # withheld rather than admitted unchecked. Named so a short page is a stated bound, not an
        # apparent absence of executions. The advice matches what the response actually offers: a token
        # is present whenever the walk can continue, and its absence means this really is the end.
        continuation = ("continue with NextToken to see the rest"
                        if "NextToken" in result else
                        "read them by narrowing to fewer executions per page")
        result["warnings"] = [
            f"This page reached the limit of {MAX_AUTHZ_ENTITIES_RESOLVED_PER_PAGE} distinct assets "
            f"resolved for permission checks, so some executions were not evaluated and are not "
            f"listed. Narrow the filters or {continuation}."]
    return success(body={"message": result})


# ---------------------------------------------------------------------------
# Re-run
# ---------------------------------------------------------------------------

def _to_asset_relative_key(full_key, asset_root_s3_key):
    """Convert a stored FULL asset-bucket key (assetRootS3Key + relative) back to the asset-relative
    key the execute request expects (leading '/'; '/' = whole asset). Strips the asset root prefix
    when present; a whole-asset root ('/assetId/') collapses back to '/'."""
    fk = "/" + (full_key or "").lstrip("/")
    root = (asset_root_s3_key or "").strip("/")
    if root:
        body = fk.lstrip("/")
        if body == root or body == root + "/":
            return "/"
        if body.startswith(root + "/"):
            return "/" + body[len(root) + 1:]
    return fk


def _reconstruct_execute_request(execution_id, main_item, config_row):
    """Rebuild the asset-less execute request body from an execution's stored records.

    inputFiles come from the workflow-input rows (databaseId/assetId/relativeFileKey); the output
    target and the metadata-source selection from the configuration row; per-pipeline template
    parameters from each pipeline's input-configuration snapshot (templateId + templateTags +
    customTemplateOverrideUsed). The output is the V2 execute body shape (see
    models.executions.ExecuteWorkflowRequestV2Model)."""
    input_files = []
    for row in _query_all(workflow_execution_inputs_table, Key("workflowExecutionId").eq(execution_id)):
        # inputAssetFileKey is the normalized FULL asset-bucket key (assetRootS3Key + relative);
        # relativeFileKey must be asset-relative, so strip the stored asset root before re-emitting.
        full_key = row.get("inputAssetFileKey", "/")
        asset_root = row.get("assetRootS3Key", "") or ""
        input_files.append({
            "databaseId": row.get("databaseId", ""),
            "assetId": row.get("assetId", ""),
            "relativeFileKey": _to_asset_relative_key(full_key, asset_root),
        })

    pipeline_execution_parameters = {}
    for prow in get_pipeline_execution_rows(execution_id):
        pexec_id = prow.get("pipelineExecutionId", "")
        pipeline_id = prow.get("pipelineId", "")
        if not pexec_id or not pipeline_id:
            continue
        cfg_rows = _query_all(pipeline_execution_input_configuration_table,
                              Key("pipelineExecutionId").eq(pexec_id))
        cfg = cfg_rows[0] if cfg_rows else {}
        params = {}
        if cfg.get("templateId"):
            params["templateId"] = cfg.get("templateId")
        if cfg.get("templateTags"):
            params["templateTags"] = cfg.get("templateTags")
        # A template-less override run has no templateId; re-run needs the raw override body, which
        # is snapshotted (inline) on the config record. When that body was truncated at capture, it
        # cannot be faithfully reproduced: rather than silently launching a divergent run (a
        # template-less pipeline would fall through to an empty config), fail the re-run explicitly.
        if cfg.get("customTemplateOverrideUsed") and cfg.get("customTemplateOverride"):
            if cfg.get("customTemplateOverrideTruncated") and not cfg.get("templateId"):
                raise VAMSGeneralErrorResponse(
                    "This execution's custom configuration was too large to store in full, so the "
                    "run cannot be reproduced exactly. Start a new execution with the configuration "
                    "instead of re-running.")
            if not cfg.get("customTemplateOverrideTruncated"):
                params["customTemplateOverride"] = cfg.get("customTemplateOverride")
        if params:
            pipeline_execution_parameters[pipeline_id] = params

    # The metadata sources travel in their own fields, never as inputFiles: they are entities, not
    # files, so re-emitting them as inputFiles would fail an arity-'none' workflow's own no-input-files
    # rule. They are recorded on the configuration row only (there is no workflow-input row for a
    # metadata source), and a row written before they existed yields empty values. The database field
    # replays the caller's NAMED selection (inputMetadataDatabaseId) rather than the captured set: a run
    # with input files derives its databases from those files, so the re-run derives the same ones from
    # the same inputFiles, and naming them here would instead be read as an arity-'none' selection.
    _metadata_source_databases, metadata_source_assets = _metadata_source_entities(config_row)
    body = {
        "inputFiles": input_files,
        "metadataSourceDatabaseId": config_row.get("inputMetadataDatabaseId", "") or "",
        "metadataSourceAssets": [{"databaseId": database_id, "assetId": asset_id}
                                 for database_id, asset_id in metadata_source_assets],
        "outputAssetId": config_row.get("outputAssetId", ""),
        "outputDatabaseId": config_row.get("outputDatabaseId", ""),
        "pipelineExecutionParameters": pipeline_execution_parameters,
        "triggerType": "manual",
    }
    # Preserve the original run's output base path extension so a re-run writes to the same layout.
    # ALWAYS send it when the configuration row has one, including "/" (asset root): omitting the field
    # means "inherit the workflow's default", so a run recorded at the asset root would silently adopt a
    # default added to the workflow after that run — writing somewhere the original never did. The
    # stored value is the RESOLVED one, so a re-run reproduces the original folder rather than
    # re-resolving per-run tags; that is what "same layout" means for a re-run.
    if "outputFileBaseExecutionPathExtension" in config_row:
        body["outputFileBaseExecutionPathExtension"] = (
            config_row.get("outputFileBaseExecutionPathExtension") or "/")
    return body


def rerun_execution(event, execution_id, request_model):
    """Reconstruct the execute request from the stored records and launch a NEW execution via the
    asset-less V2 execute handler (lambda cross-call). Re-validation of the caller's permissions on
    every referenced asset/workflow/pipeline happens inside the execute handler itself, so this only
    resolves the original execution + reconstructs the body. Returns the new execution response."""
    main_item = get_execution_main_row(execution_id)
    if not main_item:
        return validation_error(status_code=404, body={"message": "Execution not found"}, event=event)

    # The caller must be able to see the original execution (workflow GET + GET on every asset it read,
    # or on the asset it wrote to for a run with no inputs).
    if not _execution_visible_to_caller(execution_id, main_item):
        logger.info(f"Re-run not authorized for execution {execution_id}")
        return authorization_error()

    workflow_database_id = main_item.get("workflowDatabaseId", "")
    workflow_id = main_item.get("workflowId", "")
    execute_path = f"/workflows/{workflow_database_id}/{workflow_id}/execute"
    # A re-run launches a new execution, so the caller must hold Tier-1 on the execute route too.
    # The delegated invoke is a lambdaCrossCall, which enforceAPI auto-approves, so the route check
    # runs here against the caller's own constraints.
    if not _enforce_api_route(event, execute_path, "POST"):
        logger.info(f"Re-run denied: caller lacks API access to the execute route for "
                    f"{workflow_database_id}:{workflow_id}")
        return authorization_error()

    if not execute_workflow_v2_function:
        logger.error("EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME not configured; cannot re-run")
        return general_error(body={"message": "Re-run is not available in this deployment."}, event=event)

    config_row = get_workflow_execution_configuration_row(execution_id)
    body = _reconstruct_execute_request(execution_id, main_item, config_row)
    if request_model.executionGroupId:
        body["executionGroupId"] = request_model.executionGroupId

    # Invoke the V2 execute handler as the CALLING user (propagate identity so its two-tier auth runs
    # against the caller, not a system principal). Build the execute handler's event shape.
    username = claims_and_roles["tokens"][0] if claims_and_roles.get("tokens") else "SYSTEM_USER"
    invoke_event = {
        "requestContext": {
            "http": {"method": "POST", "path": execute_path},
            "authorizer": event.get("requestContext", {}).get("authorizer"),
        },
        "pathParameters": {"workflowDatabaseId": workflow_database_id, "workflowId": workflow_id},
        "queryStringParameters": {},
        # Stored template-tag values are DynamoDB numerics, so the delegated body needs the same
        # Decimal-aware encoder the response path uses.
        "body": json.dumps(body, default=_json_default),
        # Propagate the caller's REAL MFA state so the delegated execute handler does not activate
        # MFA-gated roles for a non-MFA session (a re-run must not exceed a direct execute's rights).
        "lambdaCrossCall": {"userName": username,
                            "mfaEnabled": bool(claims_and_roles.get("mfaEnabled", False))},
    }
    response = lambda_client.invoke(
        FunctionName=execute_workflow_v2_function,
        InvocationType="RequestResponse",
        Payload=json.dumps(invoke_event).encode("utf-8"))
    payload = response.get("Payload")
    if payload:
        inner = json.loads(payload.read().decode("utf-8"))
        status_code = inner.get("statusCode", 500)
        inner_body = json.loads(inner["body"]) if inner.get("body") else {}
        if status_code == 200:
            # AUDIT LOG: re-run. The delegated invoke audits the new execution's own launch, but that
            # entry cannot say the run was a RE-run of an earlier one, so the provenance is recorded
            # here: which execution was replayed, and which new execution it produced.
            log_actions(event, "workflowExecutionRerun", {
                "sourceExecutionId": execution_id,
                "workflowId": main_item.get('workflowId', ''),
                "workflowDatabaseId": main_item.get('workflowDatabaseId', ''),
                "newExecutionId": (inner_body.get("message") or {}).get("executionId", "")
                if isinstance(inner_body.get("message"), dict) else "",
                "operation": "rerun",
            })
            return success(body=inner_body)
        return validation_error(status_code=status_code, body=inner_body, event=event)
    return internal_error(event=event)


# ---------------------------------------------------------------------------
# Permanent delete (DynamoDB rows only)
# ---------------------------------------------------------------------------

def _delete_all_rows(table_name, key_condition, key_attrs):
    """Delete every row matching key_condition from a table. key_attrs is the ordered (PK, SK) attr
    name list used to build each delete Key. Paginates the query and deletes through a batch_writer
    (batches of 25 with auto-retry) rather than one delete_item per row, so an output-heavy execution
    with many sub-rows deletes in far fewer round-trips."""
    table = dynamodb.Table(table_name)
    with table.batch_writer() as batch:
        for row in _query_all(table_name, key_condition):
            batch.delete_item(Key={attr: row[attr] for attr in key_attrs if attr in row})


def permanent_delete_execution(event, execution_id):
    """Permanently delete an execution's DynamoDB rows across all sub-tables (admin-gated at the
    route/permission level; not-in-progress guarded here). Does NOT touch Step Functions history.

    Removes: main row, workflow inputs, workflow configuration, output index, and — per pipeline —
    the PipelineExecutions row plus its input/output/log/config sub-rows."""
    main_item = get_execution_main_row(execution_id)
    if not main_item:
        return validation_error(status_code=404, body={"message": "Execution not found"}, event=event)

    # The configuration row carries both the metadata sources the authorization pass checks and the
    # output-target ids the index cleanup below needs, and it is deleted at the end, so read it once
    # here (re-reading a deleted row would return {}).
    config_row = get_workflow_execution_configuration_row(execution_id)

    # Authorize like an abort (workflow GET + POST on every asset the run read — a destructive op).
    allowed, reason = authorize_abort(execution_id, main_item, config_row=config_row)
    if not allowed:
        logger.info(f"Permanent delete not authorized for execution {execution_id}: {reason}")
        return authorization_error()

    # Guard: the execution must not be in progress (reconcile against SFN when not yet terminal).
    status = main_item.get("executionStatus", "")
    if not main_item.get("executionStopDate") and status not in TERMINAL_STATUSES:
        arn = main_item.get("workflow_execution_arn", "")
        if arn:
            try:
                described = sfn.describe_execution(executionArn=arn)
                if not described.get("stopDate"):
                    return validation_error(body={
                        "message": "Execution is in progress; abort it before permanent delete."},
                        event=event)
            except botocore.exceptions.ClientError as e:
                # A Step Functions execution whose history has expired (or was deleted) can no longer
                # be running, so a stale non-terminal row is still deletable.
                if e.response.get('Error', {}).get('Code', '') != 'ExecutionDoesNotExist':
                    logger.info(f"Could not confirm execution terminal state (continuing to guard): {e}")
                    return validation_error(body={
                        "message": "Execution is in progress; abort it before permanent delete."},
                        event=event)
                logger.info(f"Step Functions execution no longer exists for {execution_id}; "
                            "treating the row as not in progress")
            except Exception as e:
                logger.info(f"Could not confirm execution terminal state (continuing to guard): {e}")
                return validation_error(body={
                    "message": "Execution is in progress; abort it before permanent delete."}, event=event)

    # Per-pipeline sub-rows.
    for prow in get_pipeline_execution_rows(execution_id):
        pexec_id = prow.get("pipelineExecutionId", "")
        if not pexec_id:
            continue
        _delete_all_rows(pipeline_execution_input_configuration_table,
                         Key("pipelineExecutionId").eq(pexec_id),
                         ["pipelineExecutionId", "recordType"])
        _delete_all_rows(pipeline_execution_input_metadata_table,
                         Key("pipelineExecutionId").eq(pexec_id),
                         ["pipelineExecutionId", "databaseId:assetId:filePath"])
        _delete_all_rows(pipeline_execution_input_files_table,
                         Key("pipelineExecutionId").eq(pexec_id),
                         ["pipelineExecutionId", "databaseId:assetId:inputAssetFileKey"])
        _delete_all_rows(pipeline_execution_output_files_table,
                         Key("pipelineExecutionId").eq(pexec_id),
                         ["pipelineExecutionId", "fileType:relativeFilePath"])
        _delete_all_rows(pipeline_execution_output_metadata_table,
                         Key("pipelineExecutionId").eq(pexec_id),
                         ["pipelineExecutionId", "targetFilePath:metadataKey"])
        _delete_all_rows(pipeline_execution_output_results_table,
                         Key("pipelineExecutionId").eq(pexec_id),
                         ["pipelineExecutionId", "relativeFilePath"])
        _delete_all_rows(pipeline_execution_logs_table,
                         Key("pipelineExecutionId").eq(pexec_id),
                         ["pipelineExecutionId", "logType"])
        dynamodb.Table(pipeline_executions_table).delete_item(
            Key={"pipelineExecutionId": pexec_id, "workflowExecutionId": execution_id})

    # Workflow-level rows.
    _delete_all_rows(workflow_execution_inputs_table,
                     Key("workflowExecutionId").eq(execution_id),
                     ["workflowExecutionId", "databaseId:assetId:inputAssetFileKey"])
    dynamodb.Table(workflow_execution_configuration_table).delete_item(
        Key={"workflowExecutionId": execution_id, "recordType": "configuration"})

    # The output-asset relationship lives on the configuration row deleted just above (it carries the
    # output target and the composite key backing WorkflowExecConfigByOutputAssetGSI), so deleting that
    # row also removes the execution from the by-output-asset index.

    # Main row (query for the SK, then delete).
    dynamodb.Table(workflow_execution_database_v2).delete_item(
        Key={"workflowExecutionId": execution_id,
             "workflowDatabaseId:workflowId": main_item.get("workflowDatabaseId:workflowId", "")})

    logger.info(f"Permanently deleted execution records for {execution_id}")
    # AUDIT LOG: permanent delete — admin-only and irreversible, the highest-value audit event of the
    # execution surface: after this the execution's own records no longer evidence what happened.
    log_actions(event, "workflowExecutionPermanentDelete", {
        "executionId": execution_id,
        "workflowId": main_item.get('workflowId', ''),
        "workflowDatabaseId": main_item.get('workflowDatabaseId', ''),
        "operation": "permanentDelete",
    })
    return success(body={"message": "Execution records permanently deleted"})


# ---------------------------------------------------------------------------
# Abort-by-group
# ---------------------------------------------------------------------------

def _executions_in_group(group_id):
    """All execution main rows in a group, via the sparse WorkflowExecutionsByGroupGSI (paginated)."""
    main_table = dynamodb.Table(workflow_execution_database_v2)
    items = []
    kwargs = {
        "IndexName": "WorkflowExecutionsByGroupGSI",
        "KeyConditionExpression": Key("executionGroupId").eq(group_id),
        "ScanIndexForward": False,
    }
    resp = main_table.query(**kwargs)
    while True:
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        resp = main_table.query(**kwargs)
    return items


def abort_group(event, group_id):
    """Abort every active execution in a group. A group is enumerated via the ByGroupGSI (all members
    regardless of the caller's access), so authorization is checked FIRST on each member: members the
    caller cannot access are NOT reported by id (that would leak the existence/ids/count of other
    users' executions) — they are counted opaquely. Only authorized members appear in `results`."""
    executions = _executions_in_group(group_id)
    if not executions:
        return validation_error(status_code=404, body={"message": "No executions found for group"}, event=event)

    results = []
    skipped_inaccessible = 0
    aborted_this_pass = 0
    more_remaining = False
    for main_item in executions:
        execution_id = main_item.get("workflowExecutionId", "")
        if not execution_id:
            continue
        # Authorize BEFORE anything else so an inaccessible member never surfaces its id/status.
        allowed, _reason = authorize_abort(execution_id, main_item)
        if not allowed:
            skipped_inaccessible += 1
            continue
        # Authorized: terminal members are reported (the caller can already see them via details).
        if main_item.get("executionStopDate") or main_item.get("executionStatus", "") in TERMINAL_STATUSES:
            results.append({"executionId": execution_id, "status": "skipped-terminal"})
            continue
        # Bound the number of (expensive, multi-round-trip) aborts per request. Once the cap is hit,
        # stop and signal moreRemaining so the caller re-invokes to continue — rather than risk a
        # 15-min Lambda timeout mid-group with no way to resume.
        if aborted_this_pass >= MAX_GROUP_ABORT_PER_REQUEST:
            more_remaining = True
            break
        resp = abort_execution(event, execution_id)
        aborted_this_pass += 1
        results.append({"executionId": execution_id,
                        "status": "aborted" if resp.get("statusCode") == 200 else "error"})
    message = {"groupId": group_id, "results": results}
    if skipped_inaccessible:
        message["skippedInaccessibleCount"] = skipped_inaccessible
    if more_remaining:
        # More active, authorized members remain beyond this request's cap; re-invoke to continue.
        message["moreRemaining"] = True
    # AUDIT LOG: group abort — one call stops many runs. Each member's own abort is audited by
    # abort_execution, so this entry records the GROUP action: how many this pass stopped, how many were
    # withheld for access, and whether more remain beyond the per-request cap.
    log_actions(event, "workflowExecutionGroupAbort", {
        "executionGroupId": group_id,
        "abortedCount": aborted_this_pass,
        "skippedInaccessibleCount": skipped_inaccessible,
        "moreRemaining": bool(more_remaining),
        "operation": "abortGroup",
    })
    return success(body={"message": message})


# ---------------------------------------------------------------------------
# New route handlers
# ---------------------------------------------------------------------------

def handle_global_list_request(event):
    """GET /workflows/executions — global (asset-less), permission-filtered list."""
    query_params = event.get("queryStringParameters", {}) or {}
    # Coerce non-numeric/negative pageSize/maxItems to a valid default (mirrors the asset-scoped
    # list) so a bad value returns a graceful page rather than a 500.
    validate_pagination_info(query_params, 50)
    if not _enforce_api(event):
        return authorization_error()
    return get_global_executions(event, query_params)


def handle_rerun_request(event):
    """POST /workflows/executions/{executionId}/rerun."""
    path_params = event.get("pathParameters", {}) or {}
    execution_id = path_params.get("executionId", "")
    (valid, message) = validate({"executionId": {"value": execution_id, "validator": "GUID"}})
    if not valid:
        return validation_error(body={"message": message}, event=event)
    if not _enforce_api(event):
        return authorization_error()
    body = {}
    if event.get("body"):
        try:
            body = json.loads(event["body"])
        except json.JSONDecodeError:
            return validation_error(body={"message": "Invalid JSON in request body"}, event=event)
    request_model = parse(body, model=RerunExecutionRequestModel)
    return rerun_execution(event, execution_id, request_model)


def handle_permanent_delete_request(event):
    """DELETE /workflows/executions/{executionId}/permanent."""
    path_params = event.get("pathParameters", {}) or {}
    execution_id = path_params.get("executionId", "")
    (valid, message) = validate({"executionId": {"value": execution_id, "validator": "GUID"}})
    if not valid:
        return validation_error(body={"message": message}, event=event)
    if not _enforce_api(event):
        return authorization_error()
    body = {}
    if event.get("body"):
        try:
            body = json.loads(event["body"])
        except json.JSONDecodeError:
            return validation_error(body={"message": "Invalid JSON in request body"}, event=event)
    # Confirmation guard (confirmDelete must be true).
    parse(body, model=PermanentDeleteRequestModel)
    return permanent_delete_execution(event, execution_id)


def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for the workflow execution service API.

    GET .../executions[/{workflowId}]              -> list an asset's workflow executions.
    GET /workflows/executions                       -> global (asset-less) permission-filtered list.
    GET /workflows/executions/{executionId}/details -> full execution detail/traceability.
    GET /workflows/executions/{executionId}/details/metadata -> one page of one metadata collection.
    GET /workflows/executions/{executionId}/logs    -> execution logs (truncated | full).
    POST /workflows/executions/{executionId}/rerun  -> re-run (new execution from stored records).
    DELETE /workflows/executions/{executionId}       -> abort a running execution (or ?groupId= group).
    DELETE /workflows/executions/{executionId}/permanent -> permanent delete of the DynamoDB rows."""
    global claims_and_roles
    logger.info(event)
    # Normalize the REST (v1) proxy event before the first requestContext.http /
    # queryStringParameters access (coerces null params to {} and injects the
    # v2-style http block the dispatch below reads).
    normalize_event(event)
    claims_and_roles = request_to_claims(event)
    # Fresh per-request asset and decision caches: a warm container reuses module globals across
    # invocations, and every authorization path reads asset attributes through the asset memo, so a
    # carried-over row would decide the next request's ABAC check on stale attributes. The decision memo
    # is keyed per enforcer AND per caller identity, so it cannot answer this request with an earlier
    # one's decisions; it is cleared here too, as defense in depth against a future path that reuses an
    # enforcer. The entity budget starts disarmed; the list page arms it.
    _asset_details_cache.clear()
    _authz_decision_cache.clear()
    _disarm_authz_entity_budget()

    try:
        method = event['requestContext']['http']['method']
        path = event['requestContext']['http']['path']

        if method == 'GET':
            # Dispatch GETs by matching the master route templates (never hard-coded
            # path fragments) so the detail/log/global reads are routed before the asset list view.
            # The paged metadata sub-resource is matched before the details route it sits under: the
            # templates are exact-match so neither claims the other's path, but keeping the more
            # specific route first means a later template change cannot silently reroute it.
            if API_WORKFLOW_EXECUTION_DETAILS_METADATA.matches(path):
                return handle_details_metadata_request(event)
            elif API_WORKFLOW_EXECUTION_DETAILS.matches(path):
                return handle_details_request(event)
            elif API_WORKFLOW_EXECUTION_LOGS.matches(path):
                return handle_logs_request(event)
            elif API_WORKFLOW_EXECUTIONS_GLOBAL.matches(path):
                return handle_global_list_request(event)
            else:
                return handle_get_request(event)
        elif method == 'POST':
            if API_WORKFLOW_EXECUTION_RERUN.matches(path):
                return handle_rerun_request(event)
            return validation_error(body={'message': "Method not allowed"}, event=event)
        elif method == 'DELETE':
            # Permanent delete is a distinct sub-resource; the bare execution DELETE is the abort
            # (which also accepts ?groupId= to abort a whole group).
            if API_WORKFLOW_EXECUTION_PERMANENT.matches(path):
                return handle_permanent_delete_request(event)
            query_params = event.get('queryStringParameters', {}) or {}
            group_id = (query_params.get('groupId') or '').strip()
            if group_id:
                if not _enforce_api(event):
                    return authorization_error()
                return abort_group(event, group_id)
            return handle_delete_request(event)
        else:
            return validation_error(body={'message': "Method not allowed"}, event=event)

    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': _clean_validation_message(v)}, event=event)
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, event=event)
    except botocore.exceptions.ClientError as err:
        if err.response['Error']['Code'] in ('LimitExceededException', 'ThrottlingException'):
            logger.exception("Throttling Error")
            return general_error(
                status_code=err.response['ResponseMetadata']['HTTPStatusCode'],
                body={'message': 'ThrottlingException: Too many requests within a given period.'},
                event=event
            )
        elif err.response['Error']['Code'] == 'ExecutionLimitExceeded':
            logger.exception("ExecutionLimitExceeded")
            return general_error(
                status_code=err.response['ResponseMetadata']['HTTPStatusCode'],
                body={'message': 'ExecutionLimitExceeded: Reached the maximum state machine execution limit of 1,000,000'},
                event=event
            )
        else:
            logger.exception(err)
            return internal_error(event=event)
    except Exception as e:
        logger.exception(e)
        return internal_error(event=event)
