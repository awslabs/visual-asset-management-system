#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import base64
import json
import os
import boto3
import botocore
from datetime import datetime, timedelta, timezone
from boto3.dynamodb.conditions import Key
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from common.validators import validate
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger
from common.dynamodb import validate_pagination_info
from common.workflows import executionRecords as er
from common.apiRoutes import (
    API_WORKFLOW_EXECUTION_DETAILS,
    API_WORKFLOW_EXECUTION_LOGS,
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
from models.workflows import ListExecutionsRequestModel

logger = safeLogger(service="ExecutionService")

# Claims/roles for the current request (set per-invocation in lambda_handler).
claims_and_roles = {}


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
    asset_storage_table_name = os.environ["ASSET_STORAGE_TABLE_NAME"]
    workflow_execution_database_v2 = os.environ["WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME"]
    workflow_execution_inputs_table = os.environ["WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME"]
    pipeline_executions_table = os.environ["PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME"]
    # Detail-assembly tables (read-only): per-pipeline input/output records, logs, and the
    # workflow/pipeline definition tables used to cross-fetch human-readable names/descriptions.
    workflow_execution_configuration_table = os.environ["WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME"]
    pipeline_execution_input_files_table = os.environ["PIPELINE_EXECUTION_INPUT_FILES_STORAGE_TABLE_NAME"]
    pipeline_execution_input_metadata_table = os.environ["PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME"]
    pipeline_execution_input_configuration_table = os.environ["PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME"]
    pipeline_execution_output_files_table = os.environ["PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME"]
    pipeline_execution_output_metadata_table = os.environ["PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE_NAME"]
    pipeline_execution_output_results_table = os.environ["PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE_NAME"]
    pipeline_execution_logs_table = os.environ["PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME"]
    workflow_database = os.environ["WORKFLOW_STORAGE_TABLE_NAME"]
    pipeline_database = os.environ["PIPELINE_STORAGE_TABLE_NAME"]
    # Optional: asset file version-history table, used to enrich asset-output files with the
    # authoritative S3 version each execution produced. Absent in older deployments -> the
    # enrichment is skipped and outputs surface the relative path only.
    asset_file_version_history_table_name = os.environ.get("ASSET_FILE_VERSION_HISTORY_STORAGE_TABLE_NAME")
    if not all([asset_storage_table_name, workflow_execution_database_v2,
                workflow_execution_inputs_table, pipeline_executions_table,
                workflow_execution_configuration_table, pipeline_execution_input_files_table,
                pipeline_execution_input_metadata_table, pipeline_execution_input_configuration_table,
                pipeline_execution_output_files_table, pipeline_execution_output_metadata_table,
                pipeline_execution_output_results_table, pipeline_execution_logs_table,
                workflow_database, pipeline_database]):
        logger.exception("Failed loading environment variables")
        raise Exception("Failed Loading Environment Variables")
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

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
        # Keep the stored log within DynamoDB item limits.
        text, _ = er.truncate_text(text, limit=er.MAX_LOG_FIELD_BYTES)
        return text
    except Exception as e:
        logger.info(f"Could not fetch CloudWatch logs (non-critical): {e}")
        return ""


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
            'startDate': start_date,
            'stopDate': stop_date,
            'inputAssetFileKey': input_item.get('inputAssetFileKey', ''),
            'databaseId': input_item.get('databaseId', ''),
            'assetId': input_item.get('assetId', ''),
            'executionError': execution_error,
            'executionLog': execution_log,
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

        # Resume from a prior page if the caller supplied a continuation token.
        starting_token = query_params.get('startingToken') if query_params else None
        if starting_token:
            try:
                query_kwargs['ExclusiveStartKey'] = json.loads(
                    base64.b64decode(starting_token).decode('utf-8'))
            except Exception as e:
                logger.exception(e)

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
            main_table.put_item(Item=item)

        def _fetch_execution_log_and_error(execution_id, main_item, describe_response):
            """For a terminal execution, return (error_text, log_text).

            log_text is the full recent CloudWatch execution log scoped to this execution
            within the shared workflow log group (captured for any terminal status).
            error_text is the specific Step Functions error/cause message (the caller only
            stores it for non-success statuses). Best-effort: returns ('', '') on any
            failure (diagnostics are non-critical to the listing)."""
            error_text = ""
            try:
                err = describe_response.get('error', '') if describe_response else ''
                cause = describe_response.get('cause', '') if describe_response else ''
                error_text = ": ".join(p for p in (err, cause) if p)
            except Exception as e:
                logger.info(f"Could not read SFN error fields (non-critical): {e}")
            log_text = _fetch_execution_logs(
                main_item.get('executionLogGroupArn', ''), execution_id)
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
        asset = get_asset_details(database_id, asset_id)
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
        main_table.put_item(Item=main_item)

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


def _scrub_pipeline_detail(prow, pipeline_def):
    """Public-facing per-pipeline detail. Cross-fetches a human-readable name/description
    from the pipeline definition and exposes only non-internal status/timing/type fields.

    Deliberately omitted as internal: every S3 bucket/prefix field (input/output/aux/temp),
    all ARNs (pipelineResourceArn, sub-execution arns), and the STS/vended-role fields."""
    name = pipeline_def.get('pipelineId', '') or prow.get('pipelineId', '')
    return {
        "pipelineId": prow.get('pipelineId', ''),
        "pipelineDatabaseId": prow.get('pipelineDatabaseId', ''),
        "name": name,
        "description": pipeline_def.get('description', ''),
        "pipelineType": pipeline_def.get('pipelineType', ''),
        "pipelineExecutionType": prow.get('pipelineExecutionType', ''),
        "endStatePipeline": prow.get('endStatePipeline', 'false') == 'true',
        "executionStatus": prow.get('executionStatus', ''),
        "executionStartDate": prow.get('executionStartDate', ''),
        "executionStopDate": prow.get('executionStopDate', ''),
    }


def _scrub_input_file(row):
    """Public-facing input-file record (asset-relative locator only; no S3 internals)."""
    return {
        "databaseId": row.get('databaseId', ''),
        "assetId": row.get('assetId', ''),
        "inputAssetFileKey": row.get('inputAssetFileKey', ''),
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
    for prow in pipeline_rows:
        pexec_id = prow.get('pipelineExecutionId', '')
        pkey = (prow.get('pipelineDatabaseId', ''), prow.get('pipelineId', ''))
        if pkey not in pipeline_def_cache:
            pipeline_def_cache[pkey] = get_pipeline_definition(pkey[0], pkey[1])
        pipelines.append(_scrub_pipeline_detail(prow, pipeline_def_cache[pkey]))

        if not pexec_id:
            continue

        # Input configuration is per-pipeline.
        for row in _query_all(pipeline_execution_input_configuration_table,
                              Key('pipelineExecutionId').eq(pexec_id)):
            input_configurations.append({
                "pipelineId": prow.get('pipelineId', ''),
                "inputConfiguration": row.get('inputConfiguration', ''),
                "inputConfigurationTruncated": row.get('inputConfigurationTruncated', False),
            })

        # Input metadata is recorded once per execution; gather then dedupe below.
        for row in _query_all(pipeline_execution_input_metadata_table,
                              Key('pipelineExecutionId').eq(pexec_id)):
            input_metadata.append(_scrub_input_metadata(row))

        # Outputs per pipeline execution (files / metadata / results).
        for row in _query_all(pipeline_execution_output_files_table,
                              Key('pipelineExecutionId').eq(pexec_id)):
            output_files.append(_scrub_output_file(row))
        for row in _query_all(pipeline_execution_output_metadata_table,
                              Key('pipelineExecutionId').eq(pexec_id)):
            output_metadata.append(_scrub_output_metadata(row))
        for row in _query_all(pipeline_execution_output_results_table,
                              Key('pipelineExecutionId').eq(pexec_id)):
            output_results.append(_scrub_output_result(row))

    # Input files are tracked at the workflow-execution level (not per-pipeline).
    for row in _query_all(workflow_execution_inputs_table,
                          Key('workflowExecutionId').eq(execution_id)):
        input_files.append(_scrub_input_file(row))

    # Dedupe input metadata by (assetId, filePath).
    deduped_md = {}
    for md in input_metadata:
        deduped_md[(md.get("assetId", ""), md.get("filePath", ""))] = md
    input_metadata = list(deduped_md.values())

    # For asset-output executions, join each output file to the authoritative S3 asset file
    # version it produced (via the version-history table). Best-effort: leaves entries
    # path-only when no history record exists (e.g. legacy runs).
    output_files = _enrich_output_files_with_asset_versions(
        output_files, execution_id, get_workflow_execution_configuration_row(execution_id))

    return {
        "workflowExecutionId": execution_id,
        "workflowId": main_item.get('workflowId', ''),
        "workflowDatabaseId": main_item.get('workflowDatabaseId', ''),
        "workflowDescription": workflow_def.get('description', ''),
        "executionStatus": main_item.get('executionStatus', ''),
        "executionStartDate": main_item.get('executionStartDate', ''),
        "executionStopDate": main_item.get('executionStopDate', ''),
        "triggerType": main_item.get('triggerType', ''),
        "triggeredByUserId": main_item.get('triggeredByUserId', ''),
        "executionError": main_item.get('executionError', ''),
        "pipelines": pipelines,
        "inputFiles": input_files,
        "inputMetadata": input_metadata,
        "inputConfigurations": input_configurations,
        "outputs": {
            "files": output_files,
            "metadata": output_metadata,
            "results": output_results,
        },
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

    details = assemble_execution_details(execution_id, main_item)
    return success(body={'message': details})


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
    caller_pattern = (query_params.get('filterPattern') or '').strip()
    if caller_pattern:
        terms.append(caller_pattern)
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


def _fetch_registered_log_events(log_group_arn, log_stream_name, query_params, log_stream_prefix=""):
    """Best-effort fetch of events from a registered sub-process log location. Returns
    (ok, events) on success or (False, reason) on a real failure (e.g. AccessDenied), never
    raising; the caller surfaces a failure as a warning.

    Scoping precedence within the log group: an exact logStreamName (one stream) takes priority;
    otherwise a logStreamPrefix narrows to streams under that prefix (e.g. an AWS Batch/ECS task
    family); with neither, the whole group is read."""
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
            return success(body={'message': {
                "mode": LOG_MODE_TRUNCATED,
                "pipelineExecutionId": pipeline_execution_id,
                "resultLog": result_log,
                "errorLog": error_log,
            }})
        return success(body={'message': {
            "mode": LOG_MODE_TRUNCATED,
            "executionLog": main_item.get('executionLog', ''),
            "executionError": main_item.get('executionError', ''),
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
        for log in pipeline_row.get('registeredLogs', []) or []:
            log_arn = (log or {}).get('logGroupArn', '')
            stream = (log or {}).get('logStreamName', '')
            stream_prefix = (log or {}).get('logStreamPrefix', '')
            if not log_arn:
                continue
            ok, events_or_err = _fetch_registered_log_events(
                log_arn, stream, query_params, log_stream_prefix=stream_prefix)
            if ok:
                sub_process_events.extend(events_or_err)
            else:
                warnings.append(f"Sub-process log retrieval failed for {log_arn}: {events_or_err}")

    message = {
        "mode": LOG_MODE_FULL,
        "pipelineExecutionId": pipeline_execution_id,
        "events": search["events"],
        "nextToken": search["nextToken"],
    }
    if sub_process_events:
        message["subProcessEvents"] = sub_process_events
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


def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for the workflow execution service API.

    GET .../executions[/{workflowId}]              -> list an asset's workflow executions.
    GET /workflows/executions/{executionId}/details -> full execution detail/traceability.
    GET /workflows/executions/{executionId}/logs    -> execution logs (truncated | full).
    DELETE /workflows/executions/{executionId}       -> abort a running execution."""
    global claims_and_roles
    logger.info(event)
    claims_and_roles = request_to_claims(event)

    try:
        method = event['requestContext']['http']['method']
        path = event['requestContext']['http']['path']

        if method == 'GET':
            # Dispatch GETs by matching the master route templates (never hard-coded
            # path fragments) so the detail/log reads are routed before the list view.
            if API_WORKFLOW_EXECUTION_DETAILS.matches(path):
                return handle_details_request(event)
            elif API_WORKFLOW_EXECUTION_LOGS.matches(path):
                return handle_logs_request(event)
            else:
                return handle_get_request(event)
        elif method == 'DELETE':
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
