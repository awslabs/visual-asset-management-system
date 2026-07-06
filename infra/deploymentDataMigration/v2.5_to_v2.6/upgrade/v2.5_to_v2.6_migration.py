#!/usr/bin/env python3
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Data Migration Script for VAMS v2.5 to v2.6.

This is the single consolidated migration for the v2.6 release. It runs two
independent steps (select with --steps; default runs both):

  1. reindex            -- OpenSearch reindex (vams-*-v2 -> vams-*-v3)
  2. workflowExecutions -- Workflow Executions storage overhaul (V1 -> V2 data model)

------------------------------------------------------------------------------
STEP 1: OpenSearch Reindex (vams-*-v2 -> vams-*-v3)
------------------------------------------------------------------------------
The v2.6 release introduces:
  - A new ``geo_MD_location`` field of OpenSearch type ``geo_shape`` on every
    asset and file document, derived by the indexers from location metadata.
  - New OpenSearch index names ``vams-assets-v3`` and ``vams-files-v3`` (the
    prior v2 indexes are abandoned).
  - (Provisioned deployments only) An OpenSearch engine upgrade from 2.7 to 3.5.

Because the v3 indexes are empty after the v2.6 CDK deploy, this step delegates
to the deployed reindexer Lambda (``crReindexer``) to re-populate both indexes
from the source DynamoDB and S3 records. ``--clear-indexes`` is off by default.

------------------------------------------------------------------------------
STEP 2: Workflow Executions Storage Overhaul (V1 -> V2 data model)
------------------------------------------------------------------------------
Reshapes the legacy WorkflowExecutionsStorageTable (PK databaseId:assetId,
SK executionId; US-format dates) into the new workflow-keyed data model. The
legacy composite-key attributes carried a spurious '$' prefix, so the new clean
keys are rebuilt from the discrete databaseId/assetId/workflowId/workflowDatabaseId
attributes (which were stored without the '$') rather than parsed from the old keys:

  WorkflowExecutionsStorageTableV2:        PK workflowExecutionId, SK workflowDatabaseId:workflowId
  WorkflowExecutionInputsStorageTable:     PK workflowExecutionId, SK databaseId:assetId:inputAssetFileKey
  PipelineExecutionsStorageTable:          PK pipelineExecutionId, SK workflowExecutionId
  PipelineExecutionInputFilesStorageTable: PK pipelineExecutionId, SK databaseId:assetId:inputAssetFileKey

Per legacy execution row:
  1. V2 main row (clean keys, ISO dates, triggeredByUserId='system', triggerType='Manual',
     execution_arn -> workflow_execution_arn).
  2. WorkflowExecutionInputs row from legacy assetId/databaseId/inputAssetFileKey.
  3. One PipelineExecutions stub per pipeline in the workflow's specifiedPipelines
     (chained via from_pipeline_execution_id; endStatePipeline='true' on the last).
     File inputs are attached to the FIRST pipeline only.
  4. If the workflow or a pipeline no longer exists, the stub uses pipelineId='DELETED'
     so the file linkage is preserved.

GUIDs are derived deterministically (uuid5) from the legacy executionId so this
step is idempotent (re-runs overwrite the same rows).

------------------------------------------------------------------------------
Usage:
    # Dry run, both steps (recommended first)
    python v2.5_to_v2.6_migration.py --config v2.5_to_v2.6_migration_config.json --dry-run

    # Production, both steps
    python v2.5_to_v2.6_migration.py --config v2.5_to_v2.6_migration_config.json

    # Only the OpenSearch reindex step
    python v2.5_to_v2.6_migration.py --config v2.5_to_v2.6_migration_config.json --steps reindex

    # Only the workflow-executions step
    python v2.5_to_v2.6_migration.py --config v2.5_to_v2.6_migration_config.json --steps workflowExecutions

Requirements:
    - Python 3.6+
    - boto3
    - AWS credentials with lambda:InvokeFunction (reindex) and DynamoDB
      Scan/BatchWriteItem (workflowExecutions) permissions
"""

import argparse
import json
import logging
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import boto3
from botocore.exceptions import ClientError, ReadTimeoutError

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


def load_config_from_file(config_file: str) -> dict:
    """Load configuration from a JSON file, stripping comment fields."""
    try:
        with open(config_file, 'r') as f:
            config = json.load(f)
        config = {k: v for k, v in config.items() if not k.startswith('_comment') and k != 'comments'}
        logger.info(f"Loaded configuration from {config_file}")
        return config
    except FileNotFoundError:
        logger.error(f"Configuration file not found: {config_file}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in configuration file: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Error loading configuration from {config_file}: {e}")
        sys.exit(1)


# =============================================================================
# STEP 1: OpenSearch Reindex (vams-*-v2 -> vams-*-v3)
# =============================================================================

def invoke_reindexer_lambda(
    function_name: str,
    operation: str = "both",
    dry_run: bool = False,
    limit: Optional[int] = None,
    clear_indexes: bool = False,
    profile: Optional[str] = None,
    region: Optional[str] = None,
    invocation_type: str = "RequestResponse"
) -> Dict:
    """
    Invoke the deployed crReindexer Lambda to repopulate the v3 OpenSearch indexes.
    """
    logger.info("=" * 80)
    logger.info("VAMS v2.5 -> v2.6 OPENSEARCH REINDEX (vams-*-v2 -> vams-*-v3)")
    logger.info("=" * 80)
    logger.info(f"Function: {function_name}")
    logger.info(f"Operation: {operation}")
    logger.info(f"Dry Run: {dry_run}")
    logger.info(f"Limit: {limit}")
    logger.info(f"Clear Indexes: {clear_indexes}")
    logger.info(f"Invocation Type: {invocation_type}")
    logger.info("=" * 80)

    try:
        session_kwargs = {}
        if profile:
            session_kwargs['profile_name'] = profile
        if region:
            session_kwargs['region_name'] = region

        session = boto3.Session(**session_kwargs)
        lambda_client = session.client('lambda')

        payload = {
            'operation': operation,
            'dry_run': dry_run,
            'clear_indexes': clear_indexes,
        }
        if limit is not None:
            payload['limit'] = limit

        payload_json = json.dumps(payload)
        logger.info(f"Payload: {payload_json}")

        logger.info(f"Invoking Lambda function: {function_name}")
        start_time = time.time()

        response = lambda_client.invoke(
            FunctionName=function_name,
            InvocationType=invocation_type,
            Payload=payload_json
        )

        elapsed_time = time.time() - start_time

        if invocation_type == 'RequestResponse':
            status_code = response['StatusCode']
            if status_code != 200:
                error_msg = f"Lambda invocation failed with status code: {status_code}"
                logger.error(error_msg)
                if 'FunctionError' in response:
                    error_payload = json.loads(response['Payload'].read())
                    logger.error(f"Error: {json.dumps(error_payload, indent=2)}")
                return {'statusCode': status_code, 'error': error_msg}

            response_payload = json.loads(response['Payload'].read())

            logger.info("=" * 80)
            logger.info("LAMBDA INVOCATION SUCCESSFUL")
            logger.info(f"Execution Time: {elapsed_time:.2f} seconds")
            logger.info("=" * 80)

            if 'body' in response_payload:
                body = json.loads(response_payload['body'])
                if 'results' in body:
                    _log_results(body['results'])
                else:
                    logger.info(f"Response: {json.dumps(body, indent=2)}")
            else:
                logger.info(f"Response: {json.dumps(response_payload, indent=2)}")

            return response_payload

        # Asynchronous invocation
        status_code = response['StatusCode']
        if status_code == 202:
            logger.info("=" * 80)
            logger.info("LAMBDA INVOCATION SUBMITTED (ASYNCHRONOUS)")
            logger.info(f"Function: {function_name}")
            logger.info("Check CloudWatch Logs for execution results")
            logger.info("=" * 80)
            return {
                'statusCode': 202,
                'message': 'Reindexing job submitted asynchronously',
                'function_name': function_name,
            }

        error_msg = f"Lambda invocation failed with status code: {status_code}"
        logger.error(error_msg)
        return {'statusCode': status_code, 'error': error_msg}

    except ReadTimeoutError as e:
        logger.warning("=" * 80)
        logger.warning("LAMBDA INVOCATION TIMED OUT")
        logger.warning("=" * 80)
        logger.warning(f"The Lambda function '{function_name}' invocation timed out.")
        logger.warning("The function continues to run in the background. Monitor CloudWatch Logs to verify completion.")
        logger.warning("=" * 80)
        return {
            'timeout': True,
            'warning': str(e),
            'function_name': function_name,
            'message': 'Lambda invocation timed out but function is still processing',
        }

    except ClientError as e:
        error_code = e.response['Error']['Code']
        error_message = e.response['Error']['Message']
        logger.error(f"AWS Error ({error_code}): {error_message}")
        if error_code == 'ResourceNotFoundException':
            logger.error(f"Lambda function '{function_name}' not found. Verify the function name from the CDK output 'ReindexerFunctionNameOutput'.")
        elif error_code == 'AccessDeniedException':
            logger.error("Access denied. Ensure your IAM principal has lambda:InvokeFunction permission for the reindexer function.")
        return {'error': error_message, 'error_code': error_code}

    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return {'error': str(e)}


def _log_results(results: Dict) -> None:
    """Pretty-print the reindex results returned by the Lambda."""
    if 'clear_indexes' in results:
        clear_results = results['clear_indexes']
        logger.info("Index Clearing Results:")
        if 'asset_index' in clear_results:
            logger.info(f"  Asset Index: {clear_results['asset_index'].get('deleted_count', 0)} documents deleted")
        if 'file_index' in clear_results:
            logger.info(f"  File Index: {clear_results['file_index'].get('deleted_count', 0)} documents deleted")
        if 'error' in clear_results:
            logger.error(f"  Error: {clear_results['error']}")

    if 'assets' in results:
        a = results['assets']
        logger.info("Asset Reindex Results:")
        logger.info(f"  Total: {a.get('total_count', 0)}")
        logger.info(f"  Success: {a.get('success_count', 0)}")
        logger.info(f"  Failed: {a.get('failed_count', 0)}")
        if a.get('errors'):
            logger.warning(f"  Errors: {len(a['errors'])} errors occurred")

    if 'files' in results:
        f = results['files']
        logger.info("File Reindex Results:")
        logger.info(f"  Buckets Processed: {f.get('buckets_processed', 0)}")
        logger.info(f"  Objects Scanned: {f.get('objects_scanned', 0)}")
        logger.info(f"  Total: {f.get('total_count', 0)}")
        logger.info(f"  Success: {f.get('success_count', 0)}")
        logger.info(f"  Failed: {f.get('failed_count', 0)}")
        if f.get('errors'):
            logger.warning(f"  Errors: {len(f['errors'])} errors occurred")


def run_reindex_step(config: dict, args) -> int:
    """Run the OpenSearch reindex step. Returns a process-style exit code (0 = ok)."""
    function_name = config.get('reindexer_function_name')
    if not function_name:
        logger.error("Configuration is missing required field 'reindexer_function_name' (needed for the reindex step).")
        return 1

    operation = args.operation or config.get('operation', 'both')
    dry_run = args.dry_run or bool(config.get('dry_run', False))
    # CLI flag wins; otherwise fall back to config (default false)
    clear_indexes = args.clear_indexes or bool(config.get('clear_indexes', False))
    limit = args.limit if args.limit is not None else config.get('limit')
    profile = args.profile or config.get('aws_profile')
    region = args.region or config.get('aws_region')
    invocation_type = 'Event' if args.async_invoke else 'RequestResponse'

    result = invoke_reindexer_lambda(
        function_name=function_name,
        operation=operation,
        dry_run=dry_run,
        limit=limit,
        clear_indexes=clear_indexes,
        profile=profile,
        region=region,
        invocation_type=invocation_type,
    )

    if result.get('timeout'):
        logger.warning("Reindex invocation timed out -- Lambda continues processing in the background.")
        logger.warning("Verify completion via CloudWatch Logs.")
        return 0
    if 'error' in result:
        logger.error("Reindex step failed.")
        return 1

    logger.info("Reindex step completed.")
    return 0


# =============================================================================
# STEP 2: Workflow Executions Storage Overhaul (V1 -> V2 data model)
# =============================================================================

# Stable namespace for deterministic GUID derivation (idempotent re-runs).
_GUID_NAMESPACE = uuid.UUID("00000000-0000-0000-0000-00000000a6d6")

# DynamoDB BatchWriteItem accepts at most 25 items per request.
_BATCH_WRITE_MAX = 25


def derive_guid(*parts) -> str:
    """Deterministic 32-hex GUID from the given parts (idempotent)."""
    return uuid.uuid5(_GUID_NAMESPACE, "|".join(str(p) for p in parts)).hex


def normalize_file_key(file_key: str) -> str:
    if not file_key:
        return "/"
    return "/" + file_key.lstrip("/")


def to_iso(us_date: str) -> str:
    """Convert legacy US date '%m/%d/%Y, %H:%M:%S' to ISO-8601 UTC; passthrough if empty/unparseable."""
    if not us_date:
        return ""
    try:
        dt = datetime.strptime(us_date, "%m/%d/%Y, %H:%M:%S").replace(tzinfo=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, TypeError):
        return us_date


def scan_all_items(dynamodb_client, table_name: str, limit: int = None) -> List[Dict]:
    logger.info(f"Scanning {table_name} for all records...")
    records = []
    scan_kwargs = {'TableName': table_name}
    try:
        response = dynamodb_client.scan(**scan_kwargs)
        records.extend(response.get('Items', []))
        while 'LastEvaluatedKey' in response and (not limit or len(records) < limit):
            scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
            response = dynamodb_client.scan(**scan_kwargs)
            records.extend(response.get('Items', []))
        if limit and len(records) > limit:
            records = records[:limit]
        logger.info(f"Found {len(records)} records in {table_name}")
        return records
    except ClientError as e:
        logger.error(f"Error scanning table {table_name}: {e}")
        raise


def build_workflow_pipeline_cache(dynamodb_client, workflow_table_name: str) -> Dict[str, List[Dict]]:
    """Map workflowId -> list of pipeline dicts (name, databaseId, pipelineExecutionType, ...)
    from the workflow table's specifiedPipelines.functions. Keyed by workflowId only
    (workflowIds are unique across databases in VAMS)."""
    logger.info(f"Building workflow -> pipelines cache from {workflow_table_name}...")
    cache: Dict[str, List[Dict]] = {}
    for item in scan_all_items(dynamodb_client, workflow_table_name):
        workflow_id = item.get('workflowId', {}).get('S', '')
        if not workflow_id:
            continue
        specified = item.get('specifiedPipelines', {}).get('M', {})
        functions = specified.get('functions', {}).get('L', [])
        pipelines = []
        for fn in functions:
            m = fn.get('M', {})
            pipelines.append({
                'name': m.get('name', {}).get('S', ''),
                'databaseId': m.get('databaseId', {}).get('S', ''),
                'pipelineExecutionType': m.get('pipelineExecutionType', {}).get('S', 'Lambda'),
                'waitForCallback': m.get('waitForCallback', {}).get('S', 'Disabled'),
            })
        cache[workflow_id] = pipelines
    logger.info(f"Cached pipelines for {len(cache)} workflows")
    return cache


def flush_batch_write(dynamodb_client, table_name: str, batch: List[Dict], dry_run: bool = False) -> Tuple[int, int]:
    if not batch:
        return 0, 0
    if dry_run:
        return len(batch), 0
    written, errors = 0, 0
    # Slice into <=25-item requests; a single legacy row can append several
    # pipeline-execution stubs at once, so the accumulated batch may exceed 25.
    for start in range(0, len(batch), _BATCH_WRITE_MAX):
        chunk = batch[start:start + _BATCH_WRITE_MAX]
        write_requests = [{'PutRequest': {'Item': item}} for item in chunk]
        try:
            response = dynamodb_client.batch_write_item(RequestItems={table_name: write_requests})
            written += len(write_requests)
            unprocessed = response.get('UnprocessedItems', {}).get(table_name, [])
            retry_count = 0
            while unprocessed and retry_count < 3:
                retry_count += 1
                response = dynamodb_client.batch_write_item(RequestItems={table_name: unprocessed})
                unprocessed = response.get('UnprocessedItems', {}).get(table_name, [])
            if unprocessed:
                errors += len(unprocessed)
                written -= len(unprocessed)
        except ClientError as e:
            logger.error(f"Error in batch_write_item to {table_name}: {e}")
            errors += len(chunk)
    return written, errors


def s(val):
    """Wrap a python string as a DynamoDB wire-format String attribute."""
    return {'S': val if val is not None else ''}


def migrate_workflow_executions(dynamodb_client, cfg, dry_run: bool, limit: int):
    legacy_table = cfg['workflow_executions_storage_table_name_v1']
    main_v2 = cfg['workflow_executions_storage_table_name_v2']
    wf_inputs = cfg['workflow_execution_inputs_storage_table_name']
    pexec = cfg['pipeline_executions_storage_table_name']
    pin_files = cfg['pipeline_execution_input_files_storage_table_name']
    workflow_table = cfg['workflow_storage_table_name']

    pipeline_cache = build_workflow_pipeline_cache(dynamodb_client, workflow_table)
    legacy_rows = scan_all_items(dynamodb_client, legacy_table, limit)

    counts = {"main": 0, "inputs": 0, "pexec": 0, "pin_files": 0, "errors": 0}

    main_batch, inputs_batch, pexec_batch, pin_files_batch = [], [], [], []

    for idx, row in enumerate(legacy_rows, 1):
        execution_id = row.get('executionId', {}).get('S', '')
        if not execution_id:
            counts["errors"] += 1
            continue

        database_id = row.get('databaseId', {}).get('S', '')
        asset_id = row.get('assetId', {}).get('S', '')
        workflow_id = row.get('workflowId', {}).get('S', '')
        workflow_database_id = row.get('workflowDatabaseId', {}).get('S', '')
        workflow_arn = row.get('workflow_arn', {}).get('S', '')
        execution_arn = row.get('execution_arn', {}).get('S', '')
        input_file_key = normalize_file_key(row.get('inputAssetFileKey', {}).get('S', ''))
        start_date = to_iso(row.get('startDate', {}).get('S', ''))
        stop_date = to_iso(row.get('stopDate', {}).get('S', ''))
        status = row.get('executionStatus', {}).get('S', '')

        # 1) V2 main row
        main_batch.append({
            'workflowExecutionId': s(execution_id),
            'workflowDatabaseId:workflowId': s(f"{workflow_database_id}:{workflow_id}"),
            'workflowId': s(workflow_id),
            'workflowDatabaseId': s(workflow_database_id),
            'workflow_arn': s(workflow_arn),
            'workflow_execution_arn': s(execution_arn),
            'executionStartDate': s(start_date),
            'executionStopDate': s(stop_date),
            'executionStatus': s(status),
            'triggeredByUserId': s('system'),
            'triggerType': s('Manual'),
            'executionLogGroupArn': s(''),
            # New v2.6 sync/error/log fields. Historical rows already carry their final
            # stop date (when present), so listExecutions will not re-poll them; leave
            # the sync-check time, error message, and log fields empty.
            'lastSfnSyncCheckDate': s(''),
            'executionError': s(''),
            'executionLog': s(''),
        })

        # 2) WorkflowExecutionInputs row
        inputs_batch.append({
            'workflowExecutionId': s(execution_id),
            'databaseId:assetId:inputAssetFileKey': s(f"{database_id}:{asset_id}:{input_file_key}"),
            'databaseId:assetId': s(f"{database_id}:{asset_id}"),
            'assetId': s(asset_id),
            'databaseId': s(database_id),
            'inputAssetFileKey': s(input_file_key),
            'executionStartDate': s(start_date),
            'workflowId': s(workflow_id),
            'workflowDatabaseId': s(workflow_database_id),
        })

        # 3) PipelineExecutions stubs (one per pipeline; DELETED fallback)
        pipelines = pipeline_cache.get(workflow_id)
        if not pipelines:
            pipelines = [{'name': 'DELETED', 'databaseId': workflow_database_id,
                          'pipelineExecutionType': 'Lambda', 'waitForCallback': 'Disabled'}]
        prev_pexec_id = ""
        for p_idx, pipeline in enumerate(pipelines):
            pexec_id = derive_guid(execution_id, p_idx)
            is_end = (p_idx == len(pipelines) - 1)
            pipeline_name = pipeline.get('name') or 'DELETED'
            pipeline_db = pipeline.get('databaseId', workflow_database_id)
            pexec_batch.append({
                'pipelineExecutionId': s(pexec_id),
                'workflowExecutionId': s(execution_id),
                'pipelineId': s(pipeline_name),
                'pipelineDatabaseId': s(pipeline_db),
                'pipelineDatabaseId:pipelineId': s(f"{pipeline_db}:{pipeline_name}"),
                'endStatePipeline': s('true' if is_end else 'false'),
                'executionStartDate': s(''),
                'executionStopDate': s(''),
                'executionStatus': s(''),
                'pipelineExecutionType': s(pipeline.get('pipelineExecutionType', 'Lambda')),
                'waitForCallback': s(pipeline.get('waitForCallback', 'Disabled')),
                'pipelineResourceArn': s(''),
                'credentialVendingState': s('notVended'),
                'from_pipeline_execution_id': s(prev_pexec_id),
                'pipeline_execution_sub_arn': s(''),
                'pipeline_execution_sub_execution_arn': s(''),
            })
            # File inputs attached to the FIRST pipeline only
            if p_idx == 0:
                pin_files_batch.append({
                    'pipelineExecutionId': s(pexec_id),
                    'databaseId:assetId:inputAssetFileKey': s(f"{database_id}:{asset_id}:{input_file_key}"),
                    'databaseId:assetId': s(f"{database_id}:{asset_id}"),
                    'assetId': s(asset_id),
                    'databaseId': s(database_id),
                    'inputAssetFileKey': s(input_file_key),
                    'workflowExecutionId': s(execution_id),
                })
            prev_pexec_id = pexec_id

        # Flush batches at 25
        for table, batch, key in (
            (main_v2, main_batch, "main"), (wf_inputs, inputs_batch, "inputs"),
            (pexec, pexec_batch, "pexec"), (pin_files, pin_files_batch, "pin_files"),
        ):
            if len(batch) >= 25:
                w, e = flush_batch_write(dynamodb_client, table, batch, dry_run)
                counts[key] += w
                counts["errors"] += e
                batch.clear()

        if idx % 100 == 0:
            logger.info(f"  Processed {idx}/{len(legacy_rows)} legacy executions...")

    # Final flush
    for table, batch, key in (
        (main_v2, main_batch, "main"), (wf_inputs, inputs_batch, "inputs"),
        (pexec, pexec_batch, "pexec"), (pin_files, pin_files_batch, "pin_files"),
    ):
        w, e = flush_batch_write(dynamodb_client, table, batch, dry_run)
        counts[key] += w
        counts["errors"] += e

    return counts, len(legacy_rows)


def run_workflow_executions_step(config: dict, args) -> int:
    """Run the workflow-executions storage overhaul step. Returns 0 on success."""
    required = [
        'workflow_executions_storage_table_name_v1', 'workflow_executions_storage_table_name_v2',
        'workflow_execution_inputs_storage_table_name', 'pipeline_executions_storage_table_name',
        'pipeline_execution_input_files_storage_table_name', 'workflow_storage_table_name',
    ]
    missing = [k for k in required if not config.get(k)]
    if missing:
        logger.error(f"Configuration is missing required field(s) for the workflowExecutions step: {', '.join(missing)}")
        return 1

    dry_run = args.dry_run or bool(config.get('dry_run', False))
    limit = args.limit if args.limit is not None else config.get('limit')
    profile = args.profile or config.get('aws_profile')
    region = args.region or config.get('aws_region')

    session_kwargs = {}
    if profile:
        session_kwargs['profile_name'] = profile
    if region:
        session_kwargs['region_name'] = region
    dynamodb_client = boto3.Session(**session_kwargs).client('dynamodb')

    logger.info("=" * 80)
    logger.info("VAMS v2.5 -> v2.6 WORKFLOW EXECUTIONS STORAGE OVERHAUL (V1 -> V2)")
    logger.info(f"Dry Run: {dry_run}")
    logger.info("=" * 80)

    start = datetime.now(timezone.utc)
    try:
        counts, total = migrate_workflow_executions(dynamodb_client, config, dry_run, limit)
    except Exception as e:
        logger.error(f"Workflow-executions step failed: {e}")
        return 1
    duration = (datetime.now(timezone.utc) - start).total_seconds()

    logger.info("=" * 80)
    logger.info("WORKFLOW EXECUTIONS STEP SUMMARY")
    logger.info(f"  Duration: {duration:.1f}s   Dry Run: {dry_run}")
    logger.info(f"  Legacy executions scanned: {total}")
    logger.info(f"  V2 main rows written:      {counts['main']}")
    logger.info(f"  Workflow input rows:       {counts['inputs']}")
    logger.info(f"  Pipeline-exec stubs:       {counts['pexec']}")
    logger.info(f"  First-pipeline input rows: {counts['pin_files']}")
    logger.info(f"  Errors:                    {counts['errors']}")
    logger.info("=" * 80)

    return 0 if counts['errors'] == 0 else 1


# =============================================================================
# Entry point
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description='VAMS v2.5 to v2.6 consolidated migration (OpenSearch reindex + workflow-executions overhaul).',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Both steps, synchronous (recommended for small/medium deployments)
  python v2.5_to_v2.6_migration.py --config v2.5_to_v2.6_migration_config.json

  # Dry run, both steps, small subset
  python v2.5_to_v2.6_migration.py --config v2.5_to_v2.6_migration_config.json --dry-run --limit 100

  # Only the OpenSearch reindex step (re-run after a partial failure, clearing v3 first)
  python v2.5_to_v2.6_migration.py --config v2.5_to_v2.6_migration_config.json --steps reindex --clear-indexes

  # Only the workflow-executions step
  python v2.5_to_v2.6_migration.py --config v2.5_to_v2.6_migration_config.json --steps workflowExecutions

  # Reindex very large datasets asynchronously (monitor CloudWatch Logs)
  python v2.5_to_v2.6_migration.py --config v2.5_to_v2.6_migration_config.json --steps reindex --async

Notes:
  - --steps selects which release migration step(s) to run (default: all).
  - The reindexer Lambda function name is exposed by the CDK stack output 'ReindexerFunctionNameOutput'.
  - The workflow-executions step is idempotent (deterministic GUIDs) and never modifies the legacy V1 table.
  - --operation/--clear-indexes/--async apply only to the reindex step; they are ignored by workflowExecutions.
        """
    )

    parser.add_argument('--config', required=True,
                        help='Path to the migration JSON configuration file')
    parser.add_argument('--steps', choices=['reindex', 'workflowExecutions', 'all'], default='all',
                        help="Which release migration step(s) to run (default: all)")
    parser.add_argument('--dry-run', action='store_true',
                        help='Test without making changes (also configurable in JSON). Applies to both steps.')
    parser.add_argument('--limit', type=int,
                        help='Maximum number of items to process (testing). Applies to both steps.')
    parser.add_argument('--profile',
                        help='AWS profile name')
    parser.add_argument('--region',
                        help='AWS region')
    parser.add_argument('--log-level', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'], default='INFO',
                        help='Logging level (default: INFO)')
    # --- reindex-step-only options ---
    parser.add_argument('--operation', choices=['assets', 'files', 'both'],
                        help='[reindex step] Reindex assets, files, or both (default: both, can also be set in config)')
    parser.add_argument('--clear-indexes', action='store_true',
                        help='[reindex step] Clear existing v3 OpenSearch documents before reindex (default: false). '
                             'The v3 indexes start empty after the CDK deploy, so this is only needed when re-running.')
    parser.add_argument('--async', dest='async_invoke', action='store_true',
                        help='[reindex step] Use asynchronous Lambda invocation (recommended for large datasets)')

    args = parser.parse_args()

    logging.getLogger().setLevel(getattr(logging, args.log_level))

    config = load_config_from_file(args.config)

    run_reindex = args.steps in ('reindex', 'all')
    run_workflow_executions = args.steps in ('workflowExecutions', 'all')

    exit_code = 0

    if run_reindex:
        logger.info("")
        logger.info("##### STEP: OpenSearch reindex #####")
        rc = run_reindex_step(config, args)
        if rc != 0:
            exit_code = rc
            if args.steps == 'all':
                # Do not proceed to the second step if the first failed.
                logger.error("Reindex step failed; skipping the workflow-executions step.")
                return exit_code

    if run_workflow_executions:
        logger.info("")
        logger.info("##### STEP: Workflow executions storage overhaul #####")
        rc = run_workflow_executions_step(config, args)
        if rc != 0:
            exit_code = rc

    if exit_code == 0:
        logger.info("v2.5 -> v2.6 migration completed.")
    else:
        logger.error("v2.5 -> v2.6 migration completed with errors.")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
