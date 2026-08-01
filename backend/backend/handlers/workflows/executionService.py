#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import base64
import json
import os
import boto3
import botocore
from datetime import datetime, timedelta, timezone
from boto3.dynamodb.conditions import Key, Attr
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from common.validators import validate
from common.resourceNames import get_table_name, ResourceKeys
from common.auth.apiEvent import normalize_event
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger
from common.dynamodb import validate_pagination_info
from common.logRedaction import redact_log_text, redact_log_events
from common.workflows import executionRecords as er
from common.apiRoutes import (
    API_WORKFLOW_EXECUTION_DETAILS,
    API_WORKFLOW_EXECUTION_LOGS,
    API_WORKFLOW_EXECUTION_RERUN,
    API_WORKFLOW_EXECUTION_PERMANENT,
    API_WORKFLOW_EXECUTIONS_GLOBAL,
)
from models.common import (
    APIGatewayProxyResponseV2,
    internal_error,
    success,
    validation_error,
    authorization_error,
    general_error,
    VAMSGeneralErrorResponse
)
from models.executions import (
    ListExecutionsRequestModel,
    RerunExecutionRequestModel,
    PermanentDeleteRequestModel,
)

logger = safeLogger(service="ExecutionService")

# Claims/roles for the current request (set per-invocation in lambda_handler).
claims_and_roles = {}

# Per-request memo of asset rows keyed by (databaseId, assetId), reset at each invocation. The global
# execution list authorizes every row against its input/output assets; many executions reference the
# same few assets, so caching collapses the repeated get_asset_details reads within one list request.
_asset_details_cache = {}


def _get_asset_details_cached(database_id, asset_id):
    """get_asset_details with per-request memoization (asset rows are stable within one request)."""
    key = (database_id, asset_id)
    if key not in _asset_details_cache:
        _asset_details_cache[key] = get_asset_details(database_id, asset_id)
    return _asset_details_cache[key]


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
    workflow_execution_outputs_index_table = get_table_name(
        ResourceKeys.WORKFLOW_EXECUTION_OUTPUTS_INDEX_TABLE)
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


def _resolve_filter_start_date(query_params):
    """ISO-8601 lower bound on executionStartDate for the listing. Uses the caller's
    `filterStartDate` query parameter when provided; otherwise defaults to 90 days before now.
    Always returns a non-empty ISO-8601 string (the effective filter applied)."""
    raw = (query_params or {}).get('filterStartDate')
    if raw is not None and str(raw).strip() != "":
        return str(raw).strip()
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


def get_asset_details(databaseId, assetId):
    """Get asset details from DynamoDB"""
    try:
        response = asset_table.query(
            KeyConditionExpression=Key('databaseId').eq(databaseId) & Key('assetId').eq(assetId),
            ScanIndexForward=False
        )

        if not response.get('Items'):
            return None

        # Return the first (most recent) item
        return response['Items'][0]
    except Exception as e:
        logger.exception(f"Error getting asset details: {e}")
        raise Exception(f"Error retrieving asset.")


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

    Enforces Tier 2 (asset GET) then per-execution (workflow GET), resolves
    executions via the inputs GSI + V2 main rows, reconciles status, and returns
    `success(body={'message': {Items, filterStartDate, [NextToken]}})`. The listing is
    lower-bounded by executionStartDate: the caller's `filterStartDate` query parameter, or 90
    days before now by default; the applied value is echoed back as `filterStartDate`.
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
        key_condition = (Key('databaseId:assetId').eq(partition_key)
                         & Key('executionStartDate').gte(filter_start_date))
        query_kwargs = {
            'IndexName': 'WorkflowExecInputsByAssetGSI',
            'KeyConditionExpression': key_condition,
            'ScanIndexForward': False,
        }

        # Resume from a prior page if the caller supplied a continuation token. A token that cannot
        # be decoded is a caller error: continuing without it would silently serve page 1 again.
        starting_token = query_params.get('startingToken') if query_params else None
        if starting_token:
            decoded = _decode_starting_token(starting_token)
            if decoded is None:
                return validation_error(
                    body={'message': "startingToken is invalid."}, event=event)
            query_kwargs['ExclusiveStartKey'] = decoded

        # Page the asset's inputs GSI newest-first (sorted by executionStartDate),
        # deduping by workflowExecutionId as we go (first-seen wins = newest input
        # row for that execution). Stop once MAX_EXECUTIONS_INSPECTED distinct
        # executions are collected so the downstream main-row fetch + Step Functions
        # describe_execution fan-out stays bounded.
        deduped_inputs = {}
        bounded = False
        last_evaluated_key = None
        resp = inputs_table.query(**query_kwargs)
        while True:
            for input_item in resp.get('Items', []):
                execution_id = input_item.get('workflowExecutionId', '')
                if not execution_id or execution_id in deduped_inputs:
                    continue
                deduped_inputs[execution_id] = input_item
                if len(deduped_inputs) >= MAX_EXECUTIONS_INSPECTED:
                    bounded = True
                    last_evaluated_key = resp.get('LastEvaluatedKey')
                    break
            if bounded or 'LastEvaluatedKey' not in resp:
                break
            query_kwargs['ExclusiveStartKey'] = resp['LastEvaluatedKey']
            resp = inputs_table.query(**query_kwargs)

        if bounded:
            logger.warning(
                "executionService inspected the most recent %d executions for asset %s; "
                "older executions were not listed", MAX_EXECUTIONS_INSPECTED, asset_id)

        def _fetch_main_row(execution_id):
            r = main_table.query(
                KeyConditionExpression=Key('workflowExecutionId').eq(execution_id),
                ScanIndexForward=False,
            )
            rows = r.get('Items', [])
            return rows[0] if rows else None

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

        # Tier-2 Casbin enforce once per deduped execution (workflow object). The
        # input row's workflowId/workflowDatabaseId are authoritative-by-construction
        # (written at launch from the same workflow as the main row), so they drive
        # the enforce here and avoid an extra main-row read just for authorization.
        authorized_inputs = []
        casbin_enforcer = CasbinEnforcer(claims_and_roles) if len(claims_and_roles["tokens"]) > 0 else None
        for input_item in deduped_inputs.values():
            if casbin_enforcer is None:
                continue
            enforce_obj = {
                "object__type": "workflow",
                "workflowId": input_item.get('workflowId', ''),
                "databaseId": input_item.get('workflowDatabaseId', ''),
            }
            if casbin_enforcer.enforce(enforce_obj, "GET"):
                authorized_inputs.append(input_item)

        items = build_execution_items(
            input_items=authorized_inputs,
            fetch_main_row=_fetch_main_row,
            describe_execution=_describe,
            persist_main_row=_persist,
            workflow_id_filter=workflow_id or '',
            workflow_database_id=workflow_database_id or '',
            fetch_execution_log_and_error=_fetch_execution_log_and_error,
        )

        # Apply the optional status/triggerType/groupId filters (same semantics as the global
        # board) so the asset Workflows tab's filters work. workflowId/workflowDatabaseId are
        # already applied inside build_execution_items via workflow_id_filter.
        extra_filters = {
            "status": (query_params.get("status") or "").strip(),
            "triggerType": (query_params.get("triggerType") or "").strip(),
            "groupId": (query_params.get("groupId") or "").strip(),
        }
        if any(extra_filters.values()):
            items = [it for it in items if _global_list_matches_filters(it, extra_filters)]

        # Surface the effective start-date filter that was applied (the caller's filterStartDate
        # or the default 90-days-before-now), so the response is self-describing.
        result = {"Items": items, "filterStartDate": filter_start_date}

        # Surface a continuation token when the candidate set was capped with more
        # rows available, so large assets are not silently cut off at the newest 200.
        if bounded and last_evaluated_key:
            result["NextToken"] = base64.b64encode(
                json.dumps(last_evaluated_key).encode('utf-8')).decode('utf-8')

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


# Sub-process resource type the abort path can stop today (mirrors registerPipelineExecution).
# Other registered types are tracked but not yet abortable.
RESOURCE_TYPE_STEP_FUNCTIONS_EXECUTION = "stepFunctionsExecution"


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
    # Not yet abortable (e.g. batchJob, ecsTask). Surface what was left running.
    locator = (sub.get("executionArn") or sub.get("jobArn") or sub.get("jobId")
               or sub.get("taskArn") or sub.get("arn") or resource_type)
    logger.info(f"Registered sub-process of type '{resource_type}' is not yet abortable: {locator}")
    return (f"Sub-process of type '{resource_type}' ({locator}) could not be aborted: "
            f"abort for this resource type is not yet supported; it may still be running.")


def authorize_execution_access(execution_id, main_item, asset_action):
    """Tier-2 authorization for an execution operation: workflow GET + `asset_action`
    on every distinct input-file asset tied to the execution.

    The workflow itself is never modified by these operations, so workflow access is
    always GET. The per-asset action varies by operation: an abort changes the run's
    effect on the assets (POST), while detail/log reads only require GET. Returns
    (allowed: bool, denied_reason: str); denied_reason is for logging only."""
    if len(claims_and_roles["tokens"]) == 0:
        return False, "no tokens"

    casbin_enforcer = CasbinEnforcer(claims_and_roles)

    # Workflow-level GET (the run's workflow; not modifying the workflow itself).
    workflow_obj = {
        "object__type": "workflow",
        "workflowId": main_item.get('workflowId', ''),
        "databaseId": main_item.get('workflowDatabaseId', ''),
    }
    if not casbin_enforcer.enforce(workflow_obj, "GET"):
        return False, "workflow GET denied"

    # asset_action on every distinct input-file asset tied to the execution.
    input_assets = get_execution_input_assets(execution_id)
    for database_id, asset_id in input_assets:
        asset = _get_asset_details_cached(database_id, asset_id)
        if not asset:
            # An input asset that no longer exists cannot be authorized against; deny.
            return False, f"input asset missing ({database_id}/{asset_id})"
        asset.update({"object__type": "asset"})
        if not casbin_enforcer.enforce(asset, asset_action):
            return False, f"asset {asset_action} denied ({database_id}/{asset_id})"

    return True, ""


def authorize_abort(execution_id, main_item):
    """Abort authorization: workflow GET + POST on every input asset (the abort
    changes the run's effect on those assets, so write access is required)."""
    return authorize_execution_access(execution_id, main_item, "POST")


def abort_execution(event, execution_id):
    """Abort a running workflow execution and reconcile the stored statuses.

    Order of operations:
      1. Resolve the V2 main row (404 if unknown).
      2. Authorize: workflow GET + POST on every input-file asset (403 if denied).
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
    pexec_table = dynamodb.Table(pipeline_executions_table)
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

        prow['executionStatus'] = ABORTED_STATUS
        if not prow.get('executionStopDate'):
            prow['executionStopDate'] = now
        pexec_table.put_item(Item=prow)

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

# Upper bound on registered sub-process logs / sub-executions read per logs request. A pipeline may
# register an unbounded number of these; each read is a CloudWatch/SFN API call, so a single logs GET
# must not fan out without limit. Excess entries are skipped and flagged in the response warnings.
MAX_REGISTERED_LOGS_INSPECTED = 20
MAX_REGISTERED_SUB_EXECUTIONS_INSPECTED = 20

# Upper bound on rows collected per sub-collection (output files/metadata/results, input files/metadata)
# in the execution-details view, so an output-heavy execution's assembled response cannot exceed the
# Lambda synchronous-response limit. A collection hitting the cap is flagged truncated in the response.
MAX_DETAIL_ROWS_PER_COLLECTION = 2000

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


def _scrub_pipeline_detail(prow, pipeline_def, rendered_config="", rendered_config_truncated=False,
                           config_snapshot=None):
    """Public-facing per-pipeline detail. Cross-fetches a human-readable name/description
    from the pipeline definition and exposes only non-internal status/timing/type fields, plus
    the exact rendered input configuration body that was sent to this pipeline and the template
    snapshot (which template/tags/override + config format the run used).

    Deliberately omitted as internal: every S3 bucket/prefix field (input/output/aux/temp),
    all ARNs (pipelineResourceArn, sub-execution arns), and the STS/vended-role fields."""
    # V2 pipeline records carry a human-readable pipelineName; fall back to category, then the id.
    name = (pipeline_def.get('pipelineName', '') or pipeline_def.get('pipelineId', '')
            or prow.get('pipelineId', ''))
    snapshot = config_snapshot or {}
    return {
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
    }


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
    """Public-facing input-metadata record (asset-relative filePath + the metadata map;
    the internal source S3 key is omitted)."""
    return {
        "databaseId": row.get('databaseId', ''),
        "assetId": row.get('assetId', ''),
        "filePath": row.get('filePath', ''),
        "metadata": row.get('metadata', {}) or {},
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
    """Fetch the workflow-execution configuration row (PK workflowExecutionId,
    SK 'configuration'), which records the output target. Returns the item or {}."""
    try:
        cfg_table = dynamodb.Table(workflow_execution_configuration_table)
        resp = cfg_table.get_item(Key={'workflowExecutionId': execution_id,
                                        'recordType': 'configuration'})
        return resp.get('Item') or {}
    except Exception as e:
        logger.exception(f"Failed reading workflow execution configuration row: {e}")
        return {}


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


def assemble_execution_details(execution_id, main_item):
    """Assemble the full, traceability-focused detail view for an execution.

    Cross-fetches workflow + per-pipeline definitions for human-readable names/descriptions,
    and gathers per-pipeline inputs (files/metadata/configuration) and outputs
    (files/metadata/results). Tolerates partially-populated executions (still running) and
    records the end-state lambda has not written yet. Scrubs all internal fields (ARNs,
    S3 bucket/key/prefix locations, STS/vended-role fields)."""
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

    def _collect(target, table_name, pexec, scrub, name, pipeline_id=""):
        """Append up to the per-collection cap (across all pipelines) from a pexec-keyed table,
        recording truncation. Stamps each scrubbed row with the producing pipelineId so the UI can
        attribute outputs/metadata to the pipeline that produced them. Bounds the assembled response
        for output-heavy executions."""
        remaining = MAX_DETAIL_ROWS_PER_COLLECTION - len(target)
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

    for prow in pipeline_rows:
        pexec_id = prow.get('pipelineExecutionId', '')
        pkey = (prow.get('pipelineDatabaseId', ''), prow.get('pipelineId', ''))
        if pkey not in pipeline_def_cache:
            pipeline_def_cache[pkey] = get_pipeline_definition(pkey[0], pkey[1])

        # Resolve this pipeline's rendered input configuration (the exact config body sent to the
        # pipeline). It is per-pipeline-execution (one small row); attach it to the pipeline detail
        # so the UI can show each pipeline's config inline, and also keep it in the flat list.
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
                    "inputConfiguration": pipeline_config,
                    "inputConfigurationTruncated": pipeline_config_truncated,
                })

        pipelines.append(_scrub_pipeline_detail(
            prow, pipeline_def_cache[pkey], pipeline_config, pipeline_config_truncated,
            config_snapshot))

        if not pexec_id:
            continue

        _pid = prow.get('pipelineId', '')
        # Input metadata is recorded once per execution; gather (capped) then dedupe below.
        _collect(input_metadata, pipeline_execution_input_metadata_table, pexec_id,
                 _scrub_input_metadata, "inputMetadata", _pid)
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

    # Dedupe input metadata by (assetId, filePath).
    deduped_md = {}
    for md in input_metadata:
        deduped_md[(md.get("assetId", ""), md.get("filePath", ""))] = md
    input_metadata = list(deduped_md.values())

    # For asset-output executions, join each output file to the authoritative S3 asset file
    # version it produced (via the version-history table). Best-effort: leaves entries
    # path-only when no history record exists (e.g. legacy runs).
    config_row = get_workflow_execution_configuration_row(execution_id)
    output_files = _enrich_output_files_with_asset_versions(output_files, execution_id, config_row)

    return {
        "workflowExecutionId": execution_id,
        "workflowId": main_item.get('workflowId', ''),
        "workflowDatabaseId": main_item.get('workflowDatabaseId', ''),
        # Human-readable name for UI display (breadcrumbs/headers); falls back to the id downstream.
        "workflowName": workflow_def.get('workflowName', ''),
        "workflowDescription": workflow_def.get('description', ''),
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
        "pipelines": pipelines,
        "inputFiles": input_files,
        "inputMetadata": input_metadata,
        "inputConfigurations": input_configurations,
        "outputs": {
            "files": output_files,
            "metadata": output_metadata,
            "results": output_results,
        },
        # Names of any sub-collections capped at MAX_DETAIL_ROWS_PER_COLLECTION (empty when complete).
        # A non-empty list signals the detail view is partial for this output-heavy execution.
        "truncatedCollections": sorted(truncated),
    }


def get_execution_details(event, execution_id):
    """Return the full detail/traceability view for an execution (404 if unknown).

    Authorization mirrors list-executions reads: workflow GET + GET on every input-file
    asset tied to the execution."""
    main_item = get_execution_main_row(execution_id)
    if not main_item:
        return validation_error(status_code=404, body={'message': "Execution not found"}, event=event)

    allowed, reason = authorize_execution_access(execution_id, main_item, "GET")
    if not allowed:
        logger.info(f"Details access not authorized for execution {execution_id}: {reason}")
        return authorization_error()

    # Safety net: reconcile a non-terminal row against SFN (RUNNING is written at launch, so the
    # common path skips the poll) so an out-of-band abort never shows RUNNING forever.
    _reconcile_main_status(execution_id, main_item)

    details = assemble_execution_details(execution_id, main_item)
    return success(body={'message': details})


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


def _full_log_search(log_group_arn, filter_terms, query_params):
    """Live CloudWatch FilterLogEvents search within the shared workflow log group.

    filter_terms is the list of REQUIRED literal terms the search is scoped to (e.g. the
    execution id, and -- for a pipeline-scoped search -- the pipeline execution id). Every
    term is AND-ed into the filter pattern so results are restricted to exactly that
    execution (and pipeline, when given) and nothing else. An optional caller filterPattern
    is appended as an additional term. Returns {events, nextToken}."""
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


def _fetch_registered_log_events(log_group_arn, log_stream_name, query_params, log_stream_prefix="",
                                 scope_terms=None):
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

    Authorization mirrors list-executions reads: workflow GET + GET on every input-file
    asset tied to the execution."""
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
                    [execution_id, pipeline_execution_id], query_params)
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
                main_item.get('executionLogGroupArn', ''), [execution_id], query_params)
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
    scope_terms = [execution_id]
    if pipeline_execution_id:
        scope_terms.append(pipeline_execution_id)
    search = _full_log_search(log_group_arn, scope_terms, query_params)

    # When scoped to a pipeline, also pull from any sub-process logs that pipeline registered
    # (best-effort; a failure on any registered log is surfaced as a non-fatal warning).
    sub_process_events = []
    warnings = []
    if pipeline_row is not None:
        # Log-group ARNs already read this request, so a group reported in registeredLogs is not
        # re-read when it is also resolved from a sub-execution's state machine (avoids duplicates).
        read_log_group_arns = set()
        # Explicitly-registered log locations (logGroupArn reported by the pipeline). Capped so an
        # unbounded registration list cannot turn one logs GET into an unbounded CloudWatch burst.
        registered_logs = pipeline_row.get('registeredLogs', []) or []
        for log in registered_logs[:MAX_REGISTERED_LOGS_INSPECTED]:
            log_arn = (log or {}).get('logGroupArn', '')
            stream = (log or {}).get('logStreamName', '')
            stream_prefix = (log or {}).get('logStreamPrefix', '')
            if not log_arn:
                continue
            read_log_group_arns.add(log_arn)
            ok, events_or_err = _fetch_registered_log_events(
                log_arn, stream, query_params, log_stream_prefix=stream_prefix)
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
                    resolved_arn, "", query_params, scope_terms=scope_terms)
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
        'executionId': {'value': execution_id, 'validator': 'ASSET_ID'},
    })
    if not valid:
        logger.error(message)
        return validation_error(body={'message': message}, event=event)

    if not _enforce_api(event):
        return authorization_error()

    logger.info(f"Getting execution details {execution_id}")
    return get_execution_details(event, execution_id)


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
        'executionId': {'value': execution_id, 'validator': 'ASSET_ID'},
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
        'executionId': {'value': execution_id, 'validator': 'ASSET_ID'},
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
    """True when the caller may see an execution under the global access rule: workflow GET AND
    (GET on ANY input-file asset OR GET on the output asset). A caller with data access to what the
    run reads from or writes to may see it; access to neither hides it. Empty tokens -> not visible.

    `casbin_enforcer` may be passed in so a batch caller (the global list) builds one enforcer for the
    whole page instead of one per row.

    The configuration row is read LAZILY, and only if the workflow and input-asset checks above have
    not already decided visibility — a row the caller cannot see, or can see via an input asset, must
    not pay for a read. `config_row` supplies an already-read item; `config_row_loader` is a
    zero-argument callable used instead, so a caller that also needs the row for its projection (the
    global list, which reports the output target) can memoize the same single read rather than issuing
    a second one."""
    if len(claims_and_roles["tokens"]) == 0:
        return False
    if casbin_enforcer is None:
        casbin_enforcer = CasbinEnforcer(claims_and_roles)

    workflow_obj = {
        "object__type": "workflow",
        "workflowId": main_item.get("workflowId", ""),
        "databaseId": main_item.get("workflowDatabaseId", ""),
    }
    if not casbin_enforcer.enforce(workflow_obj, "GET"):
        return False

    # Any input-file asset the caller can GET.
    input_assets = get_execution_input_assets(execution_id)
    for database_id, asset_id in input_assets:
        asset = _get_asset_details_cached(database_id, asset_id)
        if not asset:
            continue
        asset.update({"object__type": "asset"})
        if casbin_enforcer.enforce(asset, "GET"):
            return True

    # Or the output asset. This is the first point that needs the configuration row.
    if config_row is None:
        config_row = (config_row_loader() if config_row_loader is not None
                      else get_workflow_execution_configuration_row(execution_id))
    output_database_id = config_row.get("outputDatabaseId", "")
    output_asset_id = config_row.get("outputAssetId", "")
    if output_database_id and output_asset_id:
        output_asset = _get_asset_details_cached(output_database_id, output_asset_id)
        if output_asset:
            output_asset.update({"object__type": "asset"})
            if casbin_enforcer.enforce(output_asset, "GET"):
                return True

    # Results-only execution with NO input assets: there is no input or output asset to gate on, so
    # workflow GET (already passed) is the sole access control. This is gated on having no inputs so
    # it agrees with authorize_execution_access (used by details/logs), which requires GET on every
    # input asset — a results-only run that DOES have inputs is authorized on those inputs above, and
    # must not be listed to a caller who would then be denied its details.
    if ((config_row.get("outputLocationType") or "asset") == "none"
            and not output_asset_id and not input_assets):
        return True
    return False


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
    filter_end_date = (query_params.get("filterEndDate") or "").strip()
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
    # enforcer once and memoize asset lookups rather than repeating both per row.
    _asset_details_cache.clear()
    page_enforcer = CasbinEnforcer(claims_and_roles) if claims_and_roles.get("tokens") else None
    resp = main_table.query(**query_kwargs)
    for main_item in resp.get("Items", []):
        execution_id = main_item.get("workflowExecutionId", "")
        if not execution_id or execution_id in seen:
            continue
        seen.add(execution_id)
        if not _global_list_matches_filters(main_item, filters):
            continue
        # AT MOST one configuration read per execution, shared by the visibility check (which
        # authorizes on the output asset) and the row projection (which reports the output target).
        # Memoized and lazy: a row the caller cannot see, or one authorized via an input asset, never
        # reaches the read at all — eagerly reading here would charge a lookup for every candidate the
        # visibility filter then discards, which for a narrowly-scoped role is most of the page.
        cached_config_row = {}

        def _config_row(execution_id=execution_id, cache=cached_config_row):
            if "item" not in cache:
                cache["item"] = get_workflow_execution_configuration_row(execution_id)
            return cache["item"]

        if not _execution_visible_to_caller(
                execution_id, main_item, page_enforcer, config_row_loader=_config_row):
            continue
        items.append(_global_list_row(main_item, _config_row()))

    # Echo the applied recency window so the caller can show the active range (matches the per-asset
    # list's filterStartDate echo). filterEndDate is included only when the caller set one.
    result = {"Items": items, "filterStartDate": filter_start_date}
    if filter_end_date:
        result["filterEndDate"] = filter_end_date
    if "LastEvaluatedKey" in resp:
        result["NextToken"] = base64.b64encode(
            json.dumps(resp["LastEvaluatedKey"]).encode("utf-8")).decode("utf-8")
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
    target from the configuration row; per-pipeline template parameters from each pipeline's
    input-configuration snapshot (templateId + templateTags + customTemplateOverrideUsed). The output
    is the V2 execute body shape (see models.executions.ExecuteWorkflowRequestV2Model)."""
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

    body = {
        "inputFiles": input_files,
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

    # The caller must be able to see the original execution (workflow GET + input/output asset GET).
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
        "body": json.dumps(body),
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

    # Authorize like an abort (workflow GET + POST on every input asset — a destructive op).
    allowed, reason = authorize_abort(execution_id, main_item)
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

    # Capture the output-target ids from the configuration row BEFORE deleting it, so the
    # output-index row can be removed afterward (re-reading a deleted config row would return {}).
    config_row = get_workflow_execution_configuration_row(execution_id)
    output_database_id = config_row.get("outputDatabaseId", "")
    output_asset_id = config_row.get("outputAssetId", "")

    # Workflow-level rows.
    _delete_all_rows(workflow_execution_inputs_table,
                     Key("workflowExecutionId").eq(execution_id),
                     ["workflowExecutionId", "databaseId:assetId:inputAssetFileKey"])
    dynamodb.Table(workflow_execution_configuration_table).delete_item(
        Key={"workflowExecutionId": execution_id, "recordType": "configuration"})

    # Output index row (keyed on the captured output asset).
    if output_database_id and output_asset_id:
        dynamodb.Table(workflow_execution_outputs_index_table).delete_item(
            Key={"databaseId:assetId": f"{output_database_id}:{output_asset_id}",
                 "workflowExecutionId": execution_id})

    # Main row (query for the SK, then delete).
    dynamodb.Table(workflow_execution_database_v2).delete_item(
        Key={"workflowExecutionId": execution_id,
             "workflowDatabaseId:workflowId": main_item.get("workflowDatabaseId:workflowId", "")})

    logger.info(f"Permanently deleted execution records for {execution_id}")
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
    (valid, message) = validate({"executionId": {"value": execution_id, "validator": "ASSET_ID"}})
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
    (valid, message) = validate({"executionId": {"value": execution_id, "validator": "ASSET_ID"}})
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
    # Fresh per-request asset cache: a warm container reuses module globals across invocations, and
    # every authorization path reads asset attributes through this memo, so a carried-over row would
    # decide the next request's ABAC check on stale attributes.
    _asset_details_cache.clear()

    try:
        method = event['requestContext']['http']['method']
        path = event['requestContext']['http']['path']

        if method == 'GET':
            # Dispatch GETs by matching the master route templates (never hard-coded
            # path fragments) so the detail/log/global reads are routed before the asset list view.
            if API_WORKFLOW_EXECUTION_DETAILS.matches(path):
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
