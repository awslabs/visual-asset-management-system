#!/usr/bin/env python3
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Data Migration Script for VAMS v2.5 to v2.6 - OpenSearch Reindex (v2 -> v3),
Asset History Backfill, and Workflow Executions Storage Overhaul (V1 -> V2)

The v2.6 release introduces:
  1. A new ``geo_MD_location`` field of OpenSearch type ``geo_shape`` on every
     asset and file document. The asset and file indexers populate it from a
     ``location`` metadata key (GeoJSON or {latitude, longitude, altitude})
     or from individual latitude / longitude / altitude metadata fields.
  2. New OpenSearch index names ``vams-assets-v3`` and ``vams-files-v3`` (the
     prior v2 indexes are abandoned). The CDK custom resource that creates the
     index mappings only runs on first creation, so the schema change is
     introduced cleanly by switching index names.
  3. (Provisioned deployments only) An OpenSearch engine upgrade from 2.7 to
     3.5, which itself requires a reindex.
  4. A new ``AssetHistoryStorageTable`` that records asset lifecycle
     operations (create, edit, archive, unarchive, permanent delete). After
     the reindex, this migration backfills the table from existing asset
     records: a ``create`` record inferred from each asset's v0 version
     record (when present), plus ``archive``/``unarchive`` records inferred
     from the asset's archivedAt/archivedBy and unarchivedAt/unarchivedBy
     fields. Backfilled records carry ``migratedRecord: true`` and a
     deterministic record ID, so re-runs overwrite rather than duplicate.
     Set ``skip_asset_history_backfill: true`` in the config to run the
     reindex only.
  5. A new per-file auxiliary-bucket preview layout. Preview/viewer data
     (e.g. Potree octree files) moves from the old file-key-based layout
     ``{assetLocationKey}{relativeFileKey}/preview/...`` to the database-scoped
     per-file layout ``{databaseId}/{assetLocationKey}{relativeFileKey}/preview/...``.
     The ``auxPreviewRelocation`` step scans the auxiliary bucket, matches each
     object against the known asset location-key bases (the asset location key
     may include a custom base prefix, so matching is on that base rather than
     a bare assetId), looks up that asset's databaseId, inserts it in front of
     the key, and copies+deletes each preview object to the new key. Reserved
     working prefixes (``pipeline``/``pipelines``/``temp-upload``/``temp-uploads``)
     are ignored, and the step is idempotent (already-migrated objects, whose
     leading segment is a known databaseId, are skipped).

Because the v3 indexes are empty after the v2.6 CDK deploy, this migration
delegates to the existing reindexer Lambda (``crReindexer``) to re-populate
both indexes from the source DynamoDB and S3 records. ``--clear-indexes`` is
**off by default** -- the v3 indexes start empty and do not need to be
cleared. Pass ``--clear-indexes`` only if you are re-running the migration
against an already-populated v3 index and want a clean slate.

Configuration: set ``resource_names_ssm_param_prefix`` (from the core stack
output ``ResourceNamesSSMParamPrefixOutput``) and the reindexer function name
is resolved automatically from SSM Parameter Store. The explicit
``reindexer_function_name`` field remains supported as an optional override
and as the required path for deployments without the prefix filled in.

Usage:
    # Dry run (recommended first step)
    python v2.5_to_v2.6_migration.py --config v2.5_to_v2.6_migration_config.json --dry-run

    # Production migration
    python v2.5_to_v2.6_migration.py --config v2.5_to_v2.6_migration_config.json

    # Re-run with index clear (only if v3 is already populated)
    python v2.5_to_v2.6_migration.py --config v2.5_to_v2.6_migration_config.json --clear-indexes

    # Test with limited items
    python v2.5_to_v2.6_migration.py --config v2.5_to_v2.6_migration_config.json --limit 100 --dry-run

    # Asynchronous invocation for very large datasets
    python v2.5_to_v2.6_migration.py --config v2.5_to_v2.6_migration_config.json --async

Requirements:
    - Python 3.6+
    - boto3
    - AWS credentials with lambda:InvokeFunction permission (and ssm:GetParametersByPath
      when using the SSM prefix lookup)
"""

import argparse
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import boto3
from botocore.exceptions import ClientError, ReadTimeoutError

# Shared migration tooling (infra/deploymentDataMigration/tools)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools"))
from ssm_resource_lookup import ResourceParamKeys, SsmResourceLookup  # noqa: E402

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


#######################
# PHASE 2: ASSET HISTORY BACKFILL
#######################

# Snapshot fields captured for backfilled history records (mirrors
# backend common.assetHistory.build_asset_snapshot; migration scripts are
# standalone and do not import backend code).
def _build_asset_snapshot(asset, archived_reason=None, unarchived_reason=None):
    snapshot = {
        'assetName': asset.get('assetName', ''),
        'description': asset.get('description', ''),
        'isDistributable': asset.get('isDistributable', False),
        'tags': asset.get('tags', []),
        'bucketId': asset.get('bucketId', ''),
    }
    asset_location = asset.get('assetLocation') or {}
    if asset_location.get('Key'):
        snapshot['assetLocationKey'] = asset_location['Key']
    if archived_reason:
        snapshot['archivedReason'] = archived_reason
    if unarchived_reason:
        snapshot['unarchivedReason'] = unarchived_reason
    return snapshot


def _history_record(database_id, asset_id, change_source, change_user_id, record_date, snapshot):
    """Build one backfilled history record. The '#migrated' SK suffix is
    deterministic so re-runs overwrite rather than duplicate."""
    return {
        'databaseId:assetId': f"{database_id}:{asset_id}",
        'historyRecordId': f"{record_date}#migrated",
        'databaseId': database_id,
        'assetId': asset_id,
        'recordDate': record_date,
        'changeSource': change_source,
        'changeUserId': change_user_id or 'SYSTEM_USER',
        'assetSnapshot': snapshot,
        'migratedRecord': True,
    }


def backfill_asset_history(
    asset_table_name: str,
    versions_table_name: str,
    history_table_name: str,
    profile: Optional[str] = None,
    region: Optional[str] = None,
    dry_run: bool = False,
    limit: Optional[int] = None,
) -> Dict:
    """Backfill the asset history table from existing asset records.

    Per asset (live and archived partitions):
      - 'create' record from the assetVersions v0 record when one exists
        (recordDate = v0 dateCreated, changeUserId = v0 createdBy)
      - 'archive' record when archivedAt/archivedBy are present
      - 'unarchive' record when unarchivedAt/unarchivedBy are present
    """
    logger.info("=" * 80)
    logger.info("ASSET HISTORY BACKFILL")
    logger.info(f"Asset table: {asset_table_name}")
    logger.info(f"Versions table: {versions_table_name}")
    logger.info(f"History table: {history_table_name}")
    logger.info(f"Dry run: {dry_run}, Limit: {limit}")
    logger.info("=" * 80)

    session_kwargs = {}
    if profile:
        session_kwargs['profile_name'] = profile
    if region:
        session_kwargs['region_name'] = region
    session = boto3.Session(**session_kwargs)
    dynamodb = session.resource('dynamodb')

    asset_table = dynamodb.Table(asset_table_name)
    versions_table = dynamodb.Table(versions_table_name)
    history_table = dynamodb.Table(history_table_name)

    stats = {'assets_scanned': 0, 'create_records': 0, 'archive_records': 0,
             'unarchive_records': 0, 'records_written': 0, 'errors': 0}

    scan_kwargs = {}
    while True:
        response = asset_table.scan(**scan_kwargs)
        for asset in response.get('Items', []):
            if limit and stats['assets_scanned'] >= limit:
                break
            stats['assets_scanned'] += 1

            raw_db_id = asset.get('databaseId', '')
            asset_id = asset.get('assetId', '')
            if not raw_db_id or not asset_id:
                continue
            # The scan returns live and archived partitions; history records
            # always use the live database ID.
            database_id = raw_db_id[:-len('#deleted')] if raw_db_id.endswith('#deleted') else raw_db_id

            records = []

            # 'create' from the v0 version record when available
            try:
                v0 = versions_table.get_item(Key={
                    'databaseId:assetId': f"{database_id}:{asset_id}",
                    'assetVersionId': '0',
                }).get('Item')
            except ClientError as e:
                logger.warning(f"v0 lookup failed for {asset_id}: {e}")
                v0 = None
            if v0 and v0.get('dateCreated'):
                records.append(_history_record(
                    database_id, asset_id, 'create',
                    v0.get('createdBy', 'SYSTEM_USER'), v0['dateCreated'],
                    _build_asset_snapshot(asset)
                ))

            if asset.get('archivedAt') and asset.get('archivedBy'):
                records.append(_history_record(
                    database_id, asset_id, 'archive',
                    asset['archivedBy'], asset['archivedAt'],
                    _build_asset_snapshot(asset, archived_reason=asset.get('archivedReason'))
                ))

            if asset.get('unarchivedAt') and asset.get('unarchivedBy'):
                records.append(_history_record(
                    database_id, asset_id, 'unarchive',
                    asset['unarchivedBy'], asset['unarchivedAt'],
                    _build_asset_snapshot(asset, unarchived_reason=asset.get('unarchivedReason'))
                ))

            stats['create_records'] += sum(1 for r in records if r['changeSource'] == 'create')
            stats['archive_records'] += sum(1 for r in records if r['changeSource'] == 'archive')
            stats['unarchive_records'] += sum(1 for r in records if r['changeSource'] == 'unarchive')

            for record in records:
                if dry_run:
                    stats['records_written'] += 1
                    continue
                try:
                    history_table.put_item(Item=record)
                    stats['records_written'] += 1
                except ClientError as e:
                    stats['errors'] += 1
                    logger.error(f"Failed writing history record for {asset_id}: {e}")

            if stats['assets_scanned'] % 100 == 0:
                logger.info(f"  Processed {stats['assets_scanned']} assets, "
                            f"{stats['records_written']} history records...")

        if limit and stats['assets_scanned'] >= limit:
            break
        if 'LastEvaluatedKey' not in response:
            break
        scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']

    action = "Would write" if dry_run else "Wrote"
    logger.info("=" * 80)
    logger.info("ASSET HISTORY BACKFILL COMPLETE")
    logger.info(f"Assets scanned: {stats['assets_scanned']}")
    logger.info(f"{action} {stats['records_written']} records "
                f"(create: {stats['create_records']}, archive: {stats['archive_records']}, "
                f"unarchive: {stats['unarchive_records']}), errors: {stats['errors']}")
    logger.info("=" * 80)
    return stats



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
            'executionId': s(execution_id),
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
# STEP 3: Auxiliary-bucket preview relocation to the per-file preview layout
# =============================================================================

# Reserved top-level auxiliary-bucket prefixes that are NOT per-asset preview data and
# must never be relocated (execution working folders, temp uploads, etc.).
_AUX_RESERVED_TOP_PREFIXES = ("pipeline", "pipelines", "temp-upload", "temp-uploads")

# The auxiliary preview marker segment. Old preview objects live under
# ``{assetId}/{relativeFileKey}/preview/...``; the new layout is
# ``{databaseId}/{assetId}/{relativeFileKey}/preview/...``.
_AUX_PREVIEW_SEGMENT = "preview"


def _build_asset_location_index(dynamodb_client, asset_table_name: str):
    """Return (location_index, database_ids) from the asset storage table (live partitions only;
    ``#deleted`` archived partitions are skipped so previews resolve to the live database).

    ``location_index`` maps each asset's location-key base (``assetLocation.Key``, normalized to a
    trailing slash) -> databaseId. The asset location key may carry a custom base prefix (it is not
    necessarily the bare assetId), so old aux preview keys are matched against this location base
    rather than assuming an assetId prefix. ``database_ids`` is the set of known database ids, used
    to detect already-migrated objects (whose leading segment is a databaseId)."""
    logger.info(f"Building asset location-key -> databaseId index from {asset_table_name}...")
    location_index: Dict[str, str] = {}
    database_ids = set()
    for item in scan_all_items(dynamodb_client, asset_table_name):
        database_id = item.get('databaseId', {}).get('S', '')
        if not database_id or database_id.endswith('#deleted'):
            continue
        database_ids.add(database_id)
        location_key = item.get('assetLocation', {}).get('M', {}).get('Key', {}).get('S', '')
        location_key = (location_key or '').strip('/')
        if location_key:
            location_index[location_key + '/'] = database_id
    logger.info(f"Indexed {len(location_index)} asset locations across {len(database_ids)} databases")
    return location_index, database_ids


def _new_aux_preview_key(old_key: str, location_index: Dict[str, str], database_ids) -> Optional[str]:
    """Compute the new aux preview key for an old key, or None to skip it.

    Old preview objects are keyed ``{assetLocationKey}{relativeFileKey}/preview/...`` (the asset
    location key may include a custom base prefix). The new layout inserts the asset's databaseId at
    the front: ``{databaseId}/{assetLocationKey}{relativeFileKey}/preview/...``. Returns None when
    the key is a reserved (non-preview) prefix, has no ``preview`` segment, does not start with a
    known asset location key, or is already migrated (its leading segment is a known databaseId)."""
    segments = old_key.split('/')
    if not segments:
        return None
    # Skip reserved working prefixes (never asset preview data).
    if segments[0] in _AUX_RESERVED_TOP_PREFIXES:
        return None
    # Only relocate objects that live under a 'preview' segment (viewer/preview data).
    if _AUX_PREVIEW_SEGMENT not in segments:
        return None
    # Already migrated: leading segment is a known databaseId.
    if segments[0] in database_ids:
        return None
    # Match against the longest asset location-key base the object starts with, then prefix that
    # asset's databaseId. Longest match wins so nested location keys resolve to the right asset.
    best_base = None
    for base in location_index:
        if old_key.startswith(base) and (best_base is None or len(base) > len(best_base)):
            best_base = base
    if best_base is None:
        return None
    return f"{location_index[best_base]}/{old_key}"


def relocate_aux_previews(
    aux_bucket_name: str,
    asset_table_name: str,
    profile: Optional[str] = None,
    region: Optional[str] = None,
    dry_run: bool = False,
    limit: Optional[int] = None,
) -> Dict:
    """Relocate existing auxiliary-bucket preview objects to the per-file preview layout.

    Old layout: ``{assetLocationKey}{relativeFileKey}/preview/...`` (keyed on the asset file key;
    the asset location key may include a custom base prefix).
    New layout: ``{databaseId}/{assetLocationKey}{relativeFileKey}/preview/...`` (per-file,
    database-scoped).

    Each object under a ``preview`` segment that starts with a known asset location-key base is
    copied to the new key (databaseId inserted in front) and the old object deleted. Reserved
    working prefixes (``pipeline``/``pipelines``/``temp-upload``/``temp-uploads``) are ignored, as
    are objects with no matching asset location or that are already migrated. Idempotent: a re-run
    skips already-relocated objects (their leading segment is a known databaseId)."""
    logger.info("=" * 80)
    logger.info("AUXILIARY PREVIEW RELOCATION (per-file preview layout)")
    logger.info(f"Aux bucket: {aux_bucket_name}")
    logger.info(f"Asset table: {asset_table_name}")
    logger.info(f"Dry run: {dry_run}, Limit: {limit}")
    logger.info("=" * 80)

    session_kwargs = {}
    if profile:
        session_kwargs['profile_name'] = profile
    if region:
        session_kwargs['region_name'] = region
    session = boto3.Session(**session_kwargs)
    s3_client = session.client('s3')
    dynamodb_client = session.client('dynamodb')

    location_index, database_ids = _build_asset_location_index(dynamodb_client, asset_table_name)

    stats = {'objects_scanned': 0, 'objects_relocated': 0, 'objects_skipped': 0, 'errors': 0}

    paginator = s3_client.get_paginator('list_objects_v2')
    for page in paginator.paginate(Bucket=aux_bucket_name):
        for obj in page.get('Contents', []):
            old_key = obj.get('Key', '')
            if not old_key or old_key.endswith('/'):
                continue
            if limit and stats['objects_scanned'] >= limit:
                break
            stats['objects_scanned'] += 1

            new_key = _new_aux_preview_key(old_key, location_index, database_ids)
            if not new_key or new_key == old_key:
                stats['objects_skipped'] += 1
                continue

            if dry_run:
                logger.info(f"  Would relocate: {old_key} -> {new_key}")
                stats['objects_relocated'] += 1
                continue

            try:
                s3_client.copy_object(
                    Bucket=aux_bucket_name,
                    CopySource={'Bucket': aux_bucket_name, 'Key': old_key},
                    Key=new_key,
                )
                s3_client.delete_object(Bucket=aux_bucket_name, Key=old_key)
                stats['objects_relocated'] += 1
            except ClientError as e:
                stats['errors'] += 1
                logger.error(f"Failed relocating {old_key} -> {new_key}: {e}")

            if stats['objects_scanned'] % 500 == 0:
                logger.info(f"  Scanned {stats['objects_scanned']} objects, "
                            f"{stats['objects_relocated']} relocated...")

        if limit and stats['objects_scanned'] >= limit:
            break

    action = "Would relocate" if dry_run else "Relocated"
    logger.info("=" * 80)
    logger.info("AUXILIARY PREVIEW RELOCATION COMPLETE")
    logger.info(f"Objects scanned:   {stats['objects_scanned']}")
    logger.info(f"{action}: {stats['objects_relocated']}")
    logger.info(f"Objects skipped:   {stats['objects_skipped']}")
    logger.info(f"Errors:            {stats['errors']}")
    logger.info("=" * 80)
    return stats


def run_aux_preview_relocation_step(config: dict, args, base_param_prefix, profile, region, dry_run) -> int:
    """Resolve the aux bucket + asset table names (explicit config or SSM) and relocate previews.
    Returns 0 on success."""
    def _override(key):
        value = config.get(key)
        if value and str(value).startswith('<'):
            return None
        return value

    aux_bucket_override = _override('asset_auxiliary_bucket_name')
    asset_table_override = _override('asset_storage_table_name')

    if not base_param_prefix and not (aux_bucket_override and asset_table_override):
        logger.error(
            "Auxiliary preview relocation needs the aux bucket + asset table names: set "
            "'resource_names_ssm_param_prefix' or both 'asset_auxiliary_bucket_name' and "
            "'asset_storage_table_name' in the config."
        )
        return 1

    try:
        if aux_bucket_override and asset_table_override:
            aux_bucket_name, asset_table_name = aux_bucket_override, asset_table_override
        else:
            lookup = SsmResourceLookup(base_param_prefix, profile=profile, region=region)
            aux_bucket_name = lookup.resolve_with_override(
                aux_bucket_override, ResourceParamKeys.ASSET_AUXILIARY_BUCKET)
            asset_table_name = lookup.resolve_with_override(
                asset_table_override, ResourceParamKeys.ASSET_STORAGE_TABLE)
    except Exception as e:
        logger.error(f"Failed resolving names for auxiliary preview relocation: {e}")
        return 1

    limit = args.limit if args.limit is not None else config.get('limit')
    stats = relocate_aux_previews(
        aux_bucket_name=aux_bucket_name,
        asset_table_name=asset_table_name,
        profile=profile,
        region=region,
        dry_run=dry_run,
        limit=limit,
    )
    return 0 if stats.get('errors', 0) == 0 else 1


def main():
    parser = argparse.ArgumentParser(
        description='VAMS v2.5 to v2.6 OpenSearch reindex migration (vams-*-v2 -> vams-*-v3).',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Reindex both assets and files synchronously (recommended for small/medium deployments)
  python v2.5_to_v2.6_migration.py --config v2.5_to_v2.6_migration_config.json

  # Dry run with a small subset
  python v2.5_to_v2.6_migration.py --config v2.5_to_v2.6_migration_config.json --dry-run --limit 100

  # Re-run after a partial failure (clears v3 first)
  python v2.5_to_v2.6_migration.py --config v2.5_to_v2.6_migration_config.json --clear-indexes

  # Asynchronous invocation for very large datasets
  python v2.5_to_v2.6_migration.py --config v2.5_to_v2.6_migration_config.json --async

Notes:
  - The reindexer Lambda function name is exposed by the CDK stack output 'ReindexerFunctionNameOutput'.
  - Synchronous invocation (default) waits for completion and prints a summary.
  - For deployments with hundreds of thousands or millions of objects, use --async and monitor CloudWatch Logs.
  - --clear-indexes defaults to FALSE because the v3 indexes are empty after the v2.6 deploy. Use it only when re-running.
        """
    )

    parser.add_argument('--config', required=True,
                        help='Path to the migration JSON configuration file')
    parser.add_argument('--steps',
                        choices=['reindex', 'assetHistory', 'workflowExecutions', 'auxPreviewRelocation', 'all'],
                        default='all',
                        help="Which release migration step(s) to run (default: all)")
    parser.add_argument('--operation', choices=['assets', 'files', 'both'],
                        help='Operation to perform (default: both, can also be set in config)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Test without making changes (also configurable in JSON)')
    parser.add_argument('--limit', type=int,
                        help='Maximum number of items per category to reindex (testing)')
    parser.add_argument('--clear-indexes', action='store_true',
                        help='Clear existing v3 OpenSearch documents before reindex (default: false). '
                             'The v3 indexes start empty after the CDK deploy, so this is only needed when re-running.')
    parser.add_argument('--profile',
                        help='AWS profile name')
    parser.add_argument('--region',
                        help='AWS region')
    parser.add_argument('--async', dest='async_invoke', action='store_true',
                        help='Use asynchronous invocation (recommended for large datasets)')
    parser.add_argument('--log-level', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'], default='INFO',
                        help='Logging level (default: INFO)')

    args = parser.parse_args()

    logging.getLogger().setLevel(getattr(logging, args.log_level))

    config = load_config_from_file(args.config)

    run_reindex = args.steps in ('reindex', 'all')
    run_asset_history = args.steps in ('assetHistory', 'all')
    run_workflow_executions = args.steps in ('workflowExecutions', 'all')
    run_aux_preview_relocation = args.steps in ('auxPreviewRelocation', 'all')

    operation = args.operation or config.get('operation', 'both')
    dry_run = args.dry_run or bool(config.get('dry_run', False))
    # CLI flag wins; otherwise fall back to config (default false)
    clear_indexes = args.clear_indexes or bool(config.get('clear_indexes', False))
    limit = args.limit if args.limit is not None else config.get('limit')
    profile = args.profile or config.get('aws_profile')
    region = args.region or config.get('aws_region')

    base_param_prefix = config.get('resource_names_ssm_param_prefix')
    if base_param_prefix and base_param_prefix.startswith('<'):
        base_param_prefix = None

    reindex_ok = True
    if run_reindex:
        # Resolve the reindexer function name: explicit config value wins; otherwise look it
        # up from the deployment's SSM resource-name parameters via the base prefix (core
        # stack output 'ResourceNamesSSMParamPrefixOutput').
        function_name = config.get('reindexer_function_name')
        if function_name and function_name.startswith('<'):
            function_name = None  # unfilled template placeholder
        if not function_name:
            if not base_param_prefix:
                logger.error(
                    "Configuration must set either 'resource_names_ssm_param_prefix' (from the "
                    "core stack output 'ResourceNamesSSMParamPrefixOutput') or an explicit "
                    "'reindexer_function_name'."
                )
                sys.exit(1)
            try:
                lookup = SsmResourceLookup(base_param_prefix, profile=profile, region=region)
                function_name = lookup.resolve(ResourceParamKeys.CR_OS_REINDEXER_FUNCTION)
                logger.info(f"Resolved reindexer function from SSM: {function_name}")
            except Exception as e:
                logger.error(f"Failed resolving reindexer function name from SSM: {e}")
                sys.exit(1)

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
        elif 'error' in result:
            logger.error("Reindex migration failed.")
            reindex_ok = False
        else:
            logger.info("Reindex migration completed.")

    # Asset history backfill step (skippable via config or --steps)
    if run_asset_history and config.get('skip_asset_history_backfill'):
        logger.info("Skipping asset history backfill (skip_asset_history_backfill=true).")
        run_asset_history = False

    if run_asset_history:
        def _override(key):
            value = config.get(key)
            if value and str(value).startswith('<'):
                return None  # unfilled template placeholder
            return value

        explicit_names = (
            _override('asset_storage_table_name'),
            _override('asset_versions_table_name'),
            _override('asset_history_table_name'),
        )
        if not base_param_prefix and not all(explicit_names):
            logger.error(
                "Asset history backfill needs table names: set 'resource_names_ssm_param_prefix' "
                "or all of 'asset_storage_table_name', 'asset_versions_table_name', and "
                "'asset_history_table_name' in the config. Set 'skip_asset_history_backfill' "
                "to true to run the reindex only."
            )
            return 1

        try:
            if all(explicit_names):
                asset_table_name, versions_table_name, history_table_name = explicit_names
            else:
                lookup = SsmResourceLookup(base_param_prefix, profile=profile, region=region)
                asset_table_name = lookup.resolve_with_override(
                    explicit_names[0], ResourceParamKeys.ASSET_STORAGE_TABLE)
                versions_table_name = lookup.resolve_with_override(
                    explicit_names[1], ResourceParamKeys.ASSET_VERSIONS_STORAGE_TABLE)
                history_table_name = lookup.resolve_with_override(
                    explicit_names[2], ResourceParamKeys.ASSET_HISTORY_STORAGE_TABLE)
        except Exception as e:
            logger.error(f"Failed resolving table names for asset history backfill: {e}")
            return 1

        backfill_stats = backfill_asset_history(
            asset_table_name=asset_table_name,
            versions_table_name=versions_table_name,
            history_table_name=history_table_name,
            profile=profile,
            region=region,
            dry_run=dry_run,
            limit=limit,
        )
        if backfill_stats.get('errors'):
            logger.warning(f"Asset history backfill finished with {backfill_stats['errors']} errors.")

    exit_code = 0 if reindex_ok else 1

    if run_workflow_executions:
        logger.info("")
        logger.info("##### STEP: Workflow executions storage overhaul #####")
        rc = run_workflow_executions_step(config, args)
        if rc != 0:
            exit_code = rc

    if run_aux_preview_relocation:
        logger.info("")
        logger.info("##### STEP: Auxiliary preview relocation #####")
        rc = run_aux_preview_relocation_step(config, args, base_param_prefix, profile, region, dry_run)
        if rc != 0:
            exit_code = rc

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
