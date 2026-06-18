#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import base64
import json
import os
import boto3
import botocore
from boto3.dynamodb.conditions import Key
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from common.validators import validate
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger
from common.dynamodb import validate_pagination_info
from common import executionRecords as er
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

logger = safeLogger(service="ListExecutionsWorkflow")

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
    workflow_execution_database = os.environ["WORKFLOW_EXECUTION_STORAGE_TABLE_NAME"]
    asset_storage_table_name = os.environ["ASSET_STORAGE_TABLE_NAME"]
    workflow_execution_database_v2 = os.environ["WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME"]
    workflow_execution_inputs_table = os.environ["WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME"]
    if not all([workflow_execution_database, asset_storage_table_name,
                workflow_execution_database_v2, workflow_execution_inputs_table]):
        logger.exception("Failed loading environment variables")
        raise Exception("Failed Loading Environment Variables")
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

asset_table = dynamodb.Table(asset_storage_table_name)

# Upper bound on the number of distinct executions inspected per asset listing.
# Caps the DynamoDB main-row fetches + Step Functions describe_execution fan-out;
# older executions beyond this are surfaced via the NextToken continuation.
MAX_EXECUTIONS_INSPECTED = 200

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
            'executionId': execution_id,
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
    `success(body={'message': {Items, [NextToken]}})` preserving the prior wire shape.
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
        query_kwargs = {
            'IndexName': 'WorkflowExecInputsByAssetGSI',
            'KeyConditionExpression': Key('databaseId:assetId').eq(partition_key),
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
                "listExecutions inspected the most recent %d executions for asset %s; "
                "older executions were not listed", MAX_EXECUTIONS_INSPECTED, asset_id)

        def _fetch_main_row(execution_id):
            r = main_table.query(
                KeyConditionExpression=Key('executionId').eq(execution_id),
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

        result = {"Items": items}

        # Surface a continuation token when the candidate set was capped with more
        # rows available, so large assets are not silently cut off at the newest 200.
        if bounded and last_evaluated_key:
            result["NextToken"] = base64.b64encode(
                json.dumps(last_evaluated_key).encode('utf-8')).decode('utf-8')

        return success(body={'message': result})
    else:
        return authorization_error(body={'message': "Not Authorized"})


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
    """Lambda handler for the list-workflow-executions API (GET)."""
    global claims_and_roles
    logger.info(event)
    claims_and_roles = request_to_claims(event)

    try:
        method = event['requestContext']['http']['method']

        if method == 'GET':
            return handle_get_request(event)
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
