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
     are ignored, and the step is idempotent (an already-migrated object carries
     its databaseId in front of the location base, so it matches no base and is
     skipped).

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
import random
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, Iterator, List, Optional, Tuple

import boto3
from botocore.exceptions import BotoCoreError, ClientError, ReadTimeoutError

_HERE = os.path.dirname(os.path.abspath(__file__))

# Shared migration tooling (infra/deploymentDataMigration/tools)
sys.path.insert(0, os.path.join(_HERE, "..", "..", "tools"))
from ssm_resource_lookup import ResourceParamKeys, SsmResourceLookup  # noqa: E402

# The backend's hybrid inline/S3 template body storage, so migrated template rows carry the same
# threshold, key layout and content hashes the pipeline template service writes.
sys.path.insert(0, os.path.join(_HERE, "..", "..", "..", "..", "backend", "backend"))
try:
    from common.workflows import templateBodyStorage as tbs  # noqa: E402
except ImportError as _tbs_error:  # pragma: no cover - a broken checkout, not a runtime path
    raise ImportError(
        "Could not import backend/backend/common/workflows/templateBodyStorage.py. Run this script "
        "from its location inside the VAMS repository so the backend package is importable."
    ) from _tbs_error

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

# UnprocessedItems retry budget. DynamoDB returns rows unprocessed when the table or one of its GSIs
# throttles -- the constant-partition by-date GSIs concentrate the bulk write on one partition -- so the
# retries back off instead of re-issuing into the same exhausted write bucket.
_BATCH_WRITE_MAX_RETRIES = 8
_BATCH_WRITE_BACKOFF_BASE_SECONDS = 0.05
_BATCH_WRITE_BACKOFF_MAX_SECONDS = 5.0


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


def workflow_date_to_iso(stored_date: str) -> str:
    """Convert a V1 workflow/pipeline record's dateCreated to ISO-8601 UTC, or "" when unparseable.

    Those records store a display-formatted date, quoted, e.g. `"March 24 2026 - 14:41:39"` — a
    different format from the execution rows' `to_iso` input, so it needs its own parse."""
    if not stored_date:
        return ""
    cleaned = stored_date.strip().strip('"').strip()
    for fmt in ("%B %d %Y - %H:%M:%S", "%b %d %Y - %H:%M:%S"):
        try:
            return (datetime.strptime(cleaned, fmt)
                    .replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        except (ValueError, TypeError):
            continue
    return ""


def scan_all_items(dynamodb_client, table_name: str, limit: int = None,
                   projection: str = None) -> List[Dict]:
    """All items in a table, paged to exhaustion. ``projection`` is an optional comma-separated
    attribute list, which keeps a scan of a large table off the full item payload."""
    logger.info(f"Scanning {table_name} for all records...")
    records = []
    scan_kwargs = {'TableName': table_name}
    if projection:
        scan_kwargs['ProjectionExpression'] = projection
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


def iter_all_items(dynamodb_client, table_name: str, limit: int = None,
                   projection: str = None) -> Iterator[Dict]:
    """Yield a table's items page by page, so a large table is never held in memory at once.
    Same paging + ``limit`` + ``projection`` semantics as ``scan_all_items``."""
    logger.info(f"Scanning {table_name} for all records...")
    scan_kwargs = {'TableName': table_name}
    if projection:
        scan_kwargs['ProjectionExpression'] = projection
    yielded = 0
    try:
        while True:
            response = dynamodb_client.scan(**scan_kwargs)
            for item in response.get('Items', []):
                yield item
                yielded += 1
                if limit and yielded >= limit:
                    return
            if 'LastEvaluatedKey' not in response:
                return
            scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
    except ClientError as e:
        logger.error(f"Error scanning table {table_name}: {e}")
        raise


def build_workflow_created_date_cache(dynamodb_client, workflow_table_name: str) -> Dict[str, str]:
    """Map workflowId -> its V1 dateCreated as ISO-8601 UTC (absent when unparseable).

    Used to date an execution whose own startDate is empty. An execution cannot predate the workflow
    it ran, so the workflow's creation is a sound lower bound and keeps such rows in the date-ordered
    indexes instead of dropping them out of every listing."""
    cache: Dict[str, str] = {}
    for item in iter_all_items(dynamodb_client, workflow_table_name):
        workflow_id = item.get('workflowId', {}).get('S', '')
        if not workflow_id:
            continue
        created = workflow_date_to_iso(item.get('dateCreated', {}).get('S', ''))
        if created:
            cache[workflow_id] = created
    logger.info(f"Cached creation dates for {len(cache)} workflows")
    return cache


def build_workflow_pipeline_cache(dynamodb_client, workflow_table_name: str) -> Dict[str, List[Dict]]:
    """Map workflowId -> list of pipeline dicts (name, databaseId, pipelineExecutionType, ...)
    from the workflow table's specifiedPipelines.functions. Keyed by workflowId only
    (workflowIds are unique across databases in VAMS)."""
    logger.info(f"Building workflow -> pipelines cache from {workflow_table_name}...")
    cache: Dict[str, List[Dict]] = {}
    for item in iter_all_items(dynamodb_client, workflow_table_name):
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


def build_asset_bucket_name_index(dynamodb_client, buckets_table_name: str) -> Dict[str, str]:
    """Map bucketId -> bucketName from the S3 asset buckets table. A bucket registered under several
    prefixes has one row per prefix sharing a bucketId, so the name is the same whichever row wins."""
    logger.info(f"Building bucketId -> bucketName index from {buckets_table_name}...")
    index: Dict[str, str] = {}
    for item in iter_all_items(dynamodb_client, buckets_table_name,
                               projection='bucketId,bucketName'):
        bucket_id = item.get('bucketId', {}).get('S', '')
        bucket_name = item.get('bucketName', {}).get('S', '')
        if bucket_id and bucket_name:
            index[bucket_id] = bucket_name
    logger.info(f"Indexed {len(index)} asset buckets")
    return index


def build_asset_location_lookup(
    dynamodb_client, asset_table_name: str, bucket_name_index: Dict[str, str]
) -> Dict[Tuple[str, str], Dict[str, str]]:
    """Map (databaseId, assetId) -> {'bucket': bucketName, 'root': assetLocation.Key}.

    The V2 workflow-input record stores each input file's own asset root (bucket name + bucket-relative
    asset-root prefix) so a re-run can turn the stored FULL key back into an asset-relative one; a row
    without them re-reads a whole-asset selection as a folder selection."""
    logger.info(f"Building asset location lookup from {asset_table_name}...")
    lookup: Dict[Tuple[str, str], Dict[str, str]] = {}
    for item in iter_all_items(dynamodb_client, asset_table_name,
                               projection='databaseId,assetId,bucketId,assetLocation'):
        database_id = item.get('databaseId', {}).get('S', '')
        asset_id = item.get('assetId', {}).get('S', '')
        if not database_id or not asset_id:
            continue
        # An archived asset lives under a 'databaseId#deleted' partition; its executions were recorded
        # against the live databaseId, so the lookup is keyed on that.
        database_id = database_id[:-len('#deleted')] if database_id.endswith('#deleted') else database_id
        location_key = item.get('assetLocation', {}).get('M', {}).get('Key', {}).get('S', '') or ''
        bucket_name = bucket_name_index.get(item.get('bucketId', {}).get('S', ''), '')
        lookup.setdefault((database_id, asset_id), {'bucket': bucket_name, 'root': location_key})
    logger.info(f"Indexed {len(lookup)} asset locations")
    return lookup


def _item_identity(item: Dict) -> str:
    """A human-readable identity for a wire-format row, for per-item error logging. Reports the
    first recognized identifying attributes so a failed write names the record to re-migrate."""
    parts = []
    for attr in ('workflowExecutionId', 'pipelineExecutionId', 'databaseId', 'pipelineId',
                 'workflowId', 'templateId'):
        value = (item.get(attr) or {}).get('S')
        if value:
            parts.append(f"{attr}={value}")
    return ', '.join(parts) if parts else '<unidentified row>'


def _write_chunk_item_by_item(dynamodb_client, table_name: str, chunk: List[Dict]) -> Tuple[int, int]:
    """Fall back to one PutItem per row after a chunk-level batch_write_item failure, so a single
    invalid record does not discard the other rows in its chunk. Each failure names its row."""
    written, errors = 0, 0
    for item in chunk:
        try:
            dynamodb_client.put_item(TableName=table_name, Item=item)
            written += 1
        except ClientError as e:
            errors += 1
            logger.error(f"Failed writing row to {table_name} ({_item_identity(item)}): {e}")
    return written, errors


def _batch_write_backoff_seconds(attempt: int) -> float:
    """Exponential backoff with jitter for an UnprocessedItems retry (1-based attempt)."""
    delay = min(_BATCH_WRITE_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)),
                _BATCH_WRITE_BACKOFF_MAX_SECONDS)
    return delay * (1 + random.random())


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
            while unprocessed and retry_count < _BATCH_WRITE_MAX_RETRIES:
                retry_count += 1
                # Sleep before re-issuing: retries fired back to back hit the same exhausted write
                # bucket, so the rows come back unprocessed and are dropped rather than written.
                time.sleep(_batch_write_backoff_seconds(retry_count))
                response = dynamodb_client.batch_write_item(RequestItems={table_name: unprocessed})
                unprocessed = response.get('UnprocessedItems', {}).get(table_name, [])
            if unprocessed:
                # A lingering throttle degrades to slow single writes rather than lost rows: each
                # remaining row is written on its own, and only a row that still fails is an error.
                logger.warning(f"{len(unprocessed)} row(s) still unprocessed in {table_name} after "
                               f"{_BATCH_WRITE_MAX_RETRIES} retries. Writing them one row at a time.")
                written -= len(unprocessed)
                remaining = [request.get('PutRequest', {}).get('Item', {})
                             for request in unprocessed]
                row_written, row_errors = _write_chunk_item_by_item(
                    dynamodb_client, table_name, remaining)
                written += row_written
                errors += row_errors
        except ClientError as e:
            # A validation failure on one row fails the whole request, so retry the chunk one row
            # at a time to keep the valid rows and identify the offending ones.
            logger.error(f"Error in batch_write_item to {table_name}: {e}. "
                         f"Retrying the {len(chunk)}-row chunk one row at a time.")
            chunk_written, chunk_errors = _write_chunk_item_by_item(dynamodb_client, table_name, chunk)
            written += chunk_written
            errors += chunk_errors
    return written, errors


def s(val):
    """Wrap a python string as a DynamoDB wire-format String attribute."""
    return {'S': val if val is not None else ''}


# Mirrors executionService.TERMINAL_STATUSES: the statuses the backend treats as finished.
_V2_TERMINAL_EXECUTION_STATUSES = ('SUCCEEDED', 'FAILED', 'ABORTED', 'TIMED_OUT')
TIMED_OUT_STATUS = 'TIMED_OUT'


def migrate_workflow_executions(dynamodb_client, cfg, dry_run: bool, limit: int):
    legacy_table = cfg['workflow_executions_storage_table_name_v1']
    main_v2 = cfg['workflow_executions_storage_table_name_v2']
    wf_inputs = cfg['workflow_execution_inputs_storage_table_name']
    pexec = cfg['pipeline_executions_storage_table_name']
    pin_files = cfg['pipeline_execution_input_files_storage_table_name']
    workflow_table = cfg['workflow_storage_table_name']
    # Configuration snapshot tables. The detail view reads the workflow row for the execution's
    # output target, and re-run reads the per-pipeline rows for template parameters.
    wf_config = cfg.get('workflow_execution_configuration_storage_table_name')
    pexec_config = cfg.get('pipeline_execution_input_configuration_storage_table_name')
    # Asset location source for each input row's own asset root. Optional: without both names the rows
    # write with an empty bucket + root, which is what makes a whole-asset re-run read as a folder.
    asset_table = cfg.get('asset_storage_table_name')
    buckets_table = cfg.get('s3_asset_buckets_storage_table_name')

    pipeline_cache = build_workflow_pipeline_cache(dynamodb_client, workflow_table)
    workflow_created_dates = build_workflow_created_date_cache(dynamodb_client, workflow_table)
    asset_locations: Dict[Tuple[str, str], Dict[str, str]] = {}
    if asset_table and buckets_table:
        asset_locations = build_asset_location_lookup(
            dynamodb_client, asset_table, build_asset_bucket_name_index(dynamodb_client, buckets_table))
    else:
        logger.warning(
            "The asset storage / S3 asset buckets table names are unset, so migrated workflow-input "
            "rows carry no s3Bucket or assetRootS3Key: re-running a whole-asset execution reads as a "
            "folder selection.")
    # Streamed rather than materialized: a deployment can hold hundreds of thousands of legacy
    # executions, and rows are written in batches as they are read.
    legacy_rows = iter_all_items(dynamodb_client, legacy_table, limit)

    counts = {"main": 0, "inputs": 0, "pexec": 0, "pin_files": 0, "no_start_date": 0,
              "estimated_start_date": 0,
              "unresolved_status": 0, "wf_config": 0, "pexec_config": 0, "errors": 0}
    migration_now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    main_batch, inputs_batch, pexec_batch, pin_files_batch = [], [], [], []
    wf_config_batch, pexec_config_batch = [], []

    scanned = 0
    for idx, row in enumerate(legacy_rows, 1):
        scanned = idx
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
        # V1 'COMPLETE' is not in the v2.6 terminal-status set.
        status = row.get('executionStatus', {}).get('S', '')
        if status == 'COMPLETE':
            status = 'SUCCEEDED'

        # Non-terminal V1 rows are recorded TIMED_OUT: their SFN history has expired and would
        # otherwise be re-polled forever.
        if status not in _V2_TERMINAL_EXECUTION_STATUSES:
            counts["unresolved_status"] += 1
            status = TIMED_OUT_STATUS
        # The stop date is what the backend gates re-polling on, so a terminal row missing one falls
        # back to its start date (or the migration timestamp when it has neither).
        if not stop_date:
            stop_date = start_date or migration_now

        # executionStartDate is the sort key of the by-workflow, by-group and by-date execution GSIs
        # and of the by-asset inputs GSI, and DynamoDB rejects an empty string for an indexed key
        # attribute — so a row without one is invisible to every date-ordered listing.
        #
        # A legacy row keeps startDate == "" when the run never started: every such row observed is
        # executionStatus NEW, while every row that actually ran carries a real date. Its own start
        # instant therefore exists nowhere (the V1 state machines and their SFN history are long
        # gone), so it is dated from the workflow it referenced — which it cannot predate. The row
        # carries startDateEstimated=true so a reader is never misled into treating the derived value
        # as a recorded one. Only when even that is unavailable does the attribute stay unset.
        start_date_estimated = False
        if not start_date:
            derived = workflow_created_dates.get(workflow_id, '')
            if derived:
                start_date = derived
                start_date_estimated = True
                counts["estimated_start_date"] += 1
            else:
                counts["no_start_date"] += 1

        # 1) V2 main row. PK attribute is 'workflowExecutionId' (matches the WorkflowExecutionsStorageTableV2
        # hash key + build_workflow_execution_record); SK is 'workflowDatabaseId:workflowId'.
        main_row = {
            'workflowExecutionId': s(execution_id),
            'workflowDatabaseId:workflowId': s(f"{workflow_database_id}:{workflow_id}"),
            'workflowId': s(workflow_id),
            'workflowDatabaseId': s(workflow_database_id),
            'workflow_arn': s(workflow_arn),
            'workflow_execution_arn': s(execution_arn),
            # Constant PK for the by-date global-list GSI (WorkflowExecutionsByDateGSI), so migrated
            # executions appear in the global newest-first list alongside new ones.
            'allListPartition': s('execution'),
            'executionStopDate': s(stop_date),
            'executionStatus': s(status),
            # A V1 row recorded no triggering user, so the migration attributes it to the reserved
            # system identity every other system-authored row in this file uses.
            'triggeredByUserId': s('SYSTEM_USER'),
            'triggerType': s('Manual'),
            'executionLogGroupArn': s(''),
            # New v2.6 sync/error/log fields. Every migrated row carries a stop date and a terminal
            # status, so listExecutions will not re-poll them; leave the sync-check time, error
            # message, and log fields empty.
            'lastSfnSyncCheckDate': s(''),
            'executionError': s(''),
            'executionLog': s(''),
        }
        if start_date:
            main_row['executionStartDate'] = s(start_date)
            if start_date_estimated:
                # Marks the date as DERIVED (from the workflow's creation), not recorded by the run.
                main_row['startDateEstimated'] = {'BOOL': True}
        main_batch.append(main_row)

        # 2) WorkflowExecutionInputs row. s3Bucket + assetRootS3Key locate the input file's own asset
        # root; a re-run strips the root to recover the asset-relative key, and without it a
        # whole-asset input ('{root}/') is re-read as a folder selection.
        asset_location = asset_locations.get((database_id, asset_id)) or {}
        inputs_row = {
            'workflowExecutionId': s(execution_id),
            'databaseId:assetId:inputAssetFileKey': s(f"{database_id}:{asset_id}:{input_file_key}"),
            'databaseId:assetId': s(f"{database_id}:{asset_id}"),
            'assetId': s(asset_id),
            'databaseId': s(database_id),
            'inputAssetFileKey': s(input_file_key),
            's3Bucket': s(asset_location.get('bucket', '')),
            'assetRootS3Key': s(asset_location.get('root', '')),
            # V1 recorded no S3 VersionId for an execution's inputs, so the run's exact version is
            # unknown rather than "latest".
            'versionId': s(''),
            'workflowId': s(workflow_id),
            'workflowDatabaseId': s(workflow_database_id),
        }
        if start_date:
            inputs_row['executionStartDate'] = s(start_date)
            if start_date_estimated:
                inputs_row['startDateEstimated'] = {'BOOL': True}
        inputs_batch.append(inputs_row)

        # 3) PipelineExecutions stubs (one per pipeline; DELETED fallback)
        pipelines = pipeline_cache.get(workflow_id)
        if not pipelines:
            pipelines = [{'name': 'DELETED', 'databaseId': workflow_database_id,
                          'pipelineExecutionType': 'Lambda', 'waitForCallback': 'Disabled'}]
        # Historical executions are complete, so their pipeline rows mirror the parent's terminal
        # status (empty when the source row had none). This keeps migrated pipeline rows consistent
        # with the v2.6 status model (fresh rows default NEW; a stored terminal status is authoritative).
        migrated_pipeline_status = status
        prev_pexec_id = ""
        for p_idx, pipeline in enumerate(pipelines):
            pexec_id = derive_guid(execution_id, p_idx)
            is_end = (p_idx == len(pipelines) - 1)
            pipeline_name = pipeline.get('name') or 'DELETED'
            # A v2.4.x workflow entry carried no databaseId, so the cached value is present but empty;
            # an empty pipelineDatabaseId resolves no pipeline definition and indexes under ':id'.
            pipeline_db = pipeline.get('databaseId') or workflow_database_id
            # A built-in whose id was consolidated in v2.6 no longer exists under its V1 id, so the
            # execution row records the effective V2 id — the same rewrite the definitions step
            # applies to a workflow's pipeline references, keeping the two records consistent.
            pipeline_db, pipeline_name, default_template_id = _remap_pipeline_reference(
                pipeline_db, pipeline_name)
            pexec_row = {
                'pipelineExecutionId': s(pexec_id),
                'workflowExecutionId': s(execution_id),
                'pipelineId': s(pipeline_name),
                'pipelineDatabaseId': s(pipeline_db),
                'pipelineDatabaseId:pipelineId': s(f"{pipeline_db}:{pipeline_name}"),
                'endStatePipeline': s('true' if is_end else 'false'),
                'executionStartDate': s(start_date),
                'executionStopDate': s(stop_date),
                'executionStatus': s(migrated_pipeline_status),
                'pipelineExecutionType': s(pipeline.get('pipelineExecutionType', 'Lambda')),
                'waitForCallback': s(pipeline.get('waitForCallback', 'Disabled')),
                'pipelineResourceArn': s(''),
                'credentialVendingState': s('notVended'),
                'pipeline_execution_sub_arn': s(''),
                'pipeline_execution_sub_execution_arn': s(''),
            }
            # from_pipeline_execution_id is the PipelineExecChainGSI sort key, which DynamoDB rejects
            # as an empty string. Set it only when this pipeline chains from a prior one, matching
            # build_pipeline_execution_record's sparse-GSI contract (the first pipeline of an
            # execution is simply absent from the chain index).
            if prev_pexec_id:
                pexec_row['from_pipeline_execution_id'] = s(prev_pexec_id)
            pexec_batch.append(pexec_row)
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
            # 4) Per-pipeline configuration snapshot. A V1 execution recorded no template, so the
            # snapshot is empty except for the effective template a consolidated built-in now needs,
            # which a re-run resolves through instead of failing template resolution.
            if pexec_config:
                pexec_config_batch.append({
                    'pipelineExecutionId': s(pexec_id),
                    'recordType': s('configuration'),
                    'inputConfiguration': s(''),
                    'inputConfigurationTruncated': bool_(False),
                    'inputConfigurationFileS3Key': s(''),
                    'templateId': s(default_template_id),
                    'templateSchemaVersion': s(''),
                    'tagSchemaVersion': s(''),
                    'templateTags': {'L': []},
                    'customTemplateOverrideUsed': bool_(False),
                    'customTemplateOverride': s(''),
                    'customTemplateOverrideTruncated': bool_(False),
                    'configFormat': s(''),
                    'migratedRecord': bool_(True),
                })
            prev_pexec_id = pexec_id

        # 5) Workflow-level configuration snapshot. A V1 execution always wrote back to its single
        # input asset at the asset root, so the output target is that asset.
        if wf_config:
            wf_config_batch.append({
                'workflowExecutionId': s(execution_id),
                'recordType': s('configuration'),
                'inputMetadata': s(''),
                'inputMetadataTruncated': bool_(False),
                'specifiedPipelinesSnapshot': {'L': []},
                'outputLocationType': s('asset'),
                'outputAssetId': s(asset_id),
                'outputDatabaseId': s(database_id),
                # Partition key of WorkflowExecConfigByOutputAssetGSI, which backs "executions that
                # wrote to this asset" in the asset's execution history. The index is sparse, so a row
                # omitting this attribute is absent from it entirely — a migrated execution would not
                # appear in its own output asset's history.
                'outputDatabaseId:outputAssetId': s(f"{database_id}:{asset_id}"),
                'outputFileBaseExecutionPathExtension': s('/'),
                'inputMetadataDatabaseId': s(''),
                'inputMetadataFileS3Key': s(''),
                'migratedRecord': bool_(True),
            })

        # Flush batches at 25
        for table, batch, key in (
            (main_v2, main_batch, "main"), (wf_inputs, inputs_batch, "inputs"),
            (pexec, pexec_batch, "pexec"), (pin_files, pin_files_batch, "pin_files"),
            (wf_config, wf_config_batch, "wf_config"),
            (pexec_config, pexec_config_batch, "pexec_config"),
        ):
            if table and len(batch) >= 25:
                w, e = flush_batch_write(dynamodb_client, table, batch, dry_run)
                counts[key] += w
                counts["errors"] += e
                batch.clear()

        if idx % 100 == 0:
            logger.info(f"  Processed {idx} legacy executions...")

    # Final flush
    for table, batch, key in (
        (main_v2, main_batch, "main"), (wf_inputs, inputs_batch, "inputs"),
        (pexec, pexec_batch, "pexec"), (pin_files, pin_files_batch, "pin_files"),
        (wf_config, wf_config_batch, "wf_config"),
        (pexec_config, pexec_config_batch, "pexec_config"),
    ):
        if not table:
            continue
        w, e = flush_batch_write(dynamodb_client, table, batch, dry_run)
        counts[key] += w
        counts["errors"] += e

    return counts, scanned


def run_workflow_executions_step(config: dict, args) -> int:
    """Run the workflow-executions storage overhaul step. Returns 0 on success."""
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

    step_cfg = dict(config)
    base_param_prefix = config.get('resource_names_ssm_param_prefix')
    if base_param_prefix and str(base_param_prefix).startswith('<'):
        base_param_prefix = None
    lookup = (SsmResourceLookup(base_param_prefix, profile=profile, region=region)
              if base_param_prefix else None)

    def resolve(cfg_key, param_key):
        """The explicit config value, else the SSM-published name. A shipped placeholder value
        ('<...>' / 'YOUR-...') is not a name, so it falls through to SSM."""
        override = config.get(cfg_key)
        if override and not str(override).startswith('<') and not str(override).startswith('YOUR-'):
            return override
        if not lookup:
            raise ValueError(
                f"Config '{cfg_key}' is unset and no resource_names_ssm_param_prefix is configured "
                "to resolve it from SSM.")
        return lookup.resolve(param_key)

    try:
        for cfg_key, param_key in (
            ('workflow_executions_storage_table_name_v1',
             ResourceParamKeys.WORKFLOW_EXECUTIONS_STORAGE_TABLE),
            ('workflow_executions_storage_table_name_v2',
             ResourceParamKeys.WORKFLOW_EXECUTIONS_STORAGE_TABLE_V2),
            ('workflow_execution_inputs_storage_table_name',
             ResourceParamKeys.WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE),
            ('pipeline_executions_storage_table_name',
             ResourceParamKeys.PIPELINE_EXECUTIONS_STORAGE_TABLE),
            ('pipeline_execution_input_files_storage_table_name',
             ResourceParamKeys.PIPELINE_EXECUTION_INPUT_FILES_STORAGE_TABLE),
            ('workflow_storage_table_name', ResourceParamKeys.WORKFLOW_STORAGE_TABLE),
        ):
            step_cfg[cfg_key] = resolve(cfg_key, param_key)
    except Exception as e:
        logger.error(f"Failed resolving table names for the workflowExecutions step: {e}")
        return 1

    # The two configuration-snapshot tables back the detail view's output target and re-run's
    # per-pipeline template parameters. Unlike the six above they are optional: without either an
    # explicit name or an SSM prefix the snapshot rows are skipped and migrated executions show an
    # empty output target and cannot be re-run through a require-template pipeline.
    for cfg_key, param_key in (
        ('workflow_execution_configuration_storage_table_name',
         ResourceParamKeys.WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE),
        ('pipeline_execution_input_configuration_storage_table_name',
         ResourceParamKeys.PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE),
    ):
        step_cfg[cfg_key] = None
        try:
            step_cfg[cfg_key] = resolve(cfg_key, param_key)
        except Exception as e:
            logger.warning(f"Could not resolve '{cfg_key}': {e}")
        if not step_cfg[cfg_key]:
            logger.warning(
                f"'{cfg_key}' is unset and could not be resolved from SSM; migrated executions will "
                "have no configuration snapshot (empty output target in the detail view, and re-run "
                "unavailable for pipelines that require a template).")

    # The asset + bucket tables supply each input row's own asset root (s3Bucket + assetRootS3Key).
    # Also optional: a missing name leaves those two fields empty, which the migration step warns about.
    for cfg_key, param_key in (
        ('asset_storage_table_name', ResourceParamKeys.ASSET_STORAGE_TABLE),
        ('s3_asset_buckets_storage_table_name', ResourceParamKeys.S3_ASSET_BUCKETS_STORAGE_TABLE),
    ):
        step_cfg[cfg_key] = None
        try:
            step_cfg[cfg_key] = resolve(cfg_key, param_key)
        except Exception as e:
            logger.warning(f"Could not resolve '{cfg_key}': {e}")

    logger.info("=" * 80)
    logger.info("VAMS v2.5 -> v2.6 WORKFLOW EXECUTIONS STORAGE OVERHAUL (V1 -> V2)")
    logger.info(f"Dry Run: {dry_run}")
    logger.info("=" * 80)

    start = datetime.now(timezone.utc)
    try:
        counts, total = migrate_workflow_executions(dynamodb_client, step_cfg, dry_run, limit)
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
    logger.info(f"  Workflow config rows:      {counts['wf_config']}")
    logger.info(f"  Pipeline config rows:      {counts['pexec_config']}")
    logger.info(f"  Start date estimated:      {counts['estimated_start_date']} "
                f"(no recorded start; dated from the workflow's creation, flagged "
                f"startDateEstimated=true)")
    logger.info(f"  Without a start date:      {counts['no_start_date']} "
                f"(no recorded start and no workflow date to derive one from -- these remain "
                f"omitted from the date-ordered execution indexes)")
    logger.info(f"  Recorded as TIMED_OUT:     {counts['unresolved_status']} "
                f"(no terminal status/stop date in V1)")
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
    rather than assuming an assetId prefix. ``database_ids`` is the set of live database ids.

    Asset-location uniqueness is enforced per source bucket, so two assets in different buckets may
    share a location key. The shared auxiliary bucket keys previews on the location key alone, which
    already conflates such assets under the old layout, so the index keeps the first databaseId seen
    and logs every collision for the operator to hand-resolve."""
    logger.info(f"Building asset location-key -> databaseId index from {asset_table_name}...")
    location_index: Dict[str, str] = {}
    database_ids = set()
    collisions = 0
    # databaseId + assetLocation are the only attributes needed; projecting keeps the scan of a
    # large asset table off the full item payload.
    for item in iter_all_items(dynamodb_client, asset_table_name,
                               projection='databaseId,assetLocation'):
        database_id = item.get('databaseId', {}).get('S', '')
        if not database_id or database_id.endswith('#deleted'):
            continue
        database_ids.add(database_id)
        location_key = item.get('assetLocation', {}).get('M', {}).get('Key', {}).get('S', '')
        location_key = (location_key or '').strip('/')
        if not location_key:
            continue
        base = location_key + '/'
        existing = location_index.get(base)
        if existing and existing != database_id:
            collisions += 1
            logger.warning(
                f"Asset location key '{base}' is used by both database '{existing}' and database "
                f"'{database_id}'. Previews under it relocate to '{existing}'; previews belonging "
                f"to '{database_id}' must be copied by hand.")
            continue
        location_index[base] = database_id
    logger.info(f"Indexed {len(location_index)} asset locations across {len(database_ids)} databases")
    if collisions:
        logger.warning(f"{collisions} asset location key(s) are shared across databases; see the "
                       "warnings above for the keys needing a hand copy.")
    return location_index, database_ids


def _longest_location_base(old_key: str, location_index: Dict[str, str]) -> Optional[str]:
    """The longest asset location-key base in ``location_index`` that ``old_key`` starts with, or
    None. Bases always end with '/', so only the key's own '/'-delimited prefixes are candidates —
    a bounded number of dict lookups per object rather than a pass over every indexed asset."""
    segments = old_key.split('/')
    # Longest first: drop the trailing filename segment, then walk back toward the root.
    for cut in range(len(segments) - 1, 0, -1):
        base = '/'.join(segments[:cut]) + '/'
        if base in location_index:
            return base
    return None


def _new_aux_preview_key(old_key: str, location_index: Dict[str, str]) -> Optional[str]:
    """Compute the new aux preview key for an old key, or None to skip it.

    Old preview objects are keyed ``{assetLocationKey}{relativeFileKey}/preview/...`` (the asset
    location key may include a custom base prefix). The new layout inserts the asset's databaseId at
    the front: ``{databaseId}/{assetLocationKey}{relativeFileKey}/preview/...``. Returns None when
    the key is a reserved (non-preview) prefix, has no ``preview`` segment, does not start with a
    known asset location key, or is already migrated."""
    segments = old_key.split('/')
    if not segments:
        return None
    # Skip reserved working prefixes (never asset preview data).
    if segments[0] in _AUX_RESERVED_TOP_PREFIXES:
        return None
    # Only relocate objects that live under a 'preview' segment (viewer/preview data).
    if _AUX_PREVIEW_SEGMENT not in segments:
        return None
    # Match against the longest asset location-key base the object starts with, then prefix that
    # asset's databaseId. Longest match wins so nested location keys resolve to the right asset.
    # An already-relocated key carries its databaseId in front of the base, so it no longer starts
    # with any base and drops out here — the base match, not the leading segment, decides, so a
    # location key whose own first segment happens to equal a databaseId still relocates.
    best_base = _longest_location_base(old_key, location_index)
    if best_base is None:
        return None
    database_id = location_index[best_base]
    # Defensive: a base that itself starts with its own databaseId segment would match an
    # already-relocated key a second time. Skip when the key already carries the prefix.
    if old_key.startswith(f"{database_id}/{best_base}"):
        return None
    return f"{database_id}/{old_key}"


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

    location_index, _database_ids = _build_asset_location_index(dynamodb_client, asset_table_name)

    stats = {'objects_scanned': 0, 'objects_relocated': 0, 'objects_skipped': 0, 'errors': 0}
    failed_keys: List[str] = []

    paginator = s3_client.get_paginator('list_objects_v2')
    for page in paginator.paginate(Bucket=aux_bucket_name):
        for obj in page.get('Contents', []):
            old_key = obj.get('Key', '')
            if not old_key or old_key.endswith('/'):
                continue
            if limit and stats['objects_scanned'] >= limit:
                break
            stats['objects_scanned'] += 1

            new_key = _new_aux_preview_key(old_key, location_index)
            if not new_key or new_key == old_key:
                stats['objects_skipped'] += 1
                continue

            if dry_run:
                logger.info(f"  Would relocate: {old_key} -> {new_key}")
                stats['objects_relocated'] += 1
                continue

            try:
                # Managed transfer rather than copy_object: it switches to a multipart copy above
                # the 5 GB single-part limit, which an aux artifact can exceed.
                s3_client.copy(
                    CopySource={'Bucket': aux_bucket_name, 'Key': old_key},
                    Bucket=aux_bucket_name,
                    Key=new_key,
                )
                s3_client.delete_object(Bucket=aux_bucket_name, Key=old_key)
                stats['objects_relocated'] += 1
            except (ClientError, BotoCoreError) as e:
                stats['errors'] += 1
                failed_keys.append(old_key)
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
    if failed_keys:
        logger.error("Objects left at their old key (re-run the step after resolving the cause):")
        for key in failed_keys:
            logger.error(f"  {key}")
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


# =============================================================================
# STEP 4: Pipeline + workflow DEFINITION migration (V1 -> V2 tables)
# =============================================================================
#
# The workflowExecutions step (STEP 2) reshapes execution HISTORY. This step migrates the pipeline
# and workflow DEFINITIONS themselves from the V1 tables (PipelineStorageTable / WorkflowStorageTable)
# into the V2 tables (PipelineStorageTableV2 / WorkflowStorageTableV2, plus per-pipeline templates).
#
# Scope + safety:
#   - GLOBAL built-ins are (re)created by the CDK vamsSchema importer at deploy time with new
#     consolidated ids + templates; migrating the old GLOBAL rows would clobber those
#     freshly-registered built-ins, so a GLOBAL definition whose id is in _BUILTIN_DEFINITION_IDS is
#     skipped. This is the "don't-clobber-built-ins" rule. A GLOBAL definition the user created (V1
#     accepted the GLOBAL keyword on create) is not a built-in and is migrated like a user-database
#     definition; every skipped GLOBAL id is logged so the classification is auditable.
#   - Soft-deleted rows (databaseId ending in '#deleted') are skipped.
#   - Idempotent: V2 rows are keyed by the same (databaseId, pipelineId/workflowId), so a re-run
#     overwrites rather than duplicates. Migrated rows are flagged migratedRecord=true. The overwrite
#     is unconditional, so a re-run also discards post-migration edits to a migrated definition.
#   - The V1 tables are never modified (read-only source).
#
# V1 -> V2 field mapping (pipeline):
#   userProvidedResource JSON {isProvided, resourceId} + pipelineExecutionType + waitForCallback +
#   taskTimeout  ->  executionConfig (typed per executionType). assetType/outputType/pipelineType are
#   dropped (assetType folds into systemConfig.inputFileFilters.allow; outputType folds into a template).
#   inputParameters (the old per-pipeline default config JSON) -> a single migrated template's configBody
#   so an existing user pipeline keeps its default parameters as a selectable template.
#
# Consolidated built-in id remap: a user workflow may reference a built-in pipeline whose id was
# consolidated in v2.6 (e.g. conversion-3d-basic-to-obj -> conversion-3d-basic). CONSOLIDATED_PIPELINE_ID_MAP
# rewrites those references (and records which template the old id maps to) so the migrated workflow
# still resolves. A user's OWN pipelines (non-GLOBAL) are never remapped.

# Old built-in pipelineId -> (new consolidated pipelineId, template id the old id maps to). Mirrors the
# WB7 vamsSchema consolidations. Used only to rewrite workflow specifiedPipelines references to GLOBAL
# built-ins; user-owned pipelines pass through unchanged.
CONSOLIDATED_PIPELINE_ID_MAP = {
    "conversion-3d-basic-to-obj": ("conversion-3d-basic", "convert-to-obj"),
    "conversion-3d-basic-to-stl": ("conversion-3d-basic", "convert-to-stl"),
    "conversion-3d-basic-to-gltf": ("conversion-3d-basic", "convert-to-gltf"),
    "conversion-3d-basic-to-glb": ("conversion-3d-basic", "convert-to-glb"),
    "rapid-pipeline-to-glb": ("rapid-pipeline", "rapid-pipeline-to-glb"),
    "rapid-pipeline-to-gltf": ("rapid-pipeline", "rapid-pipeline-to-gltf"),
    "vntana-model-ops-to-usdz": ("vntana-model-ops", "model-ops-to-usdz"),
    "vntana-model-ops-to-glb": ("vntana-model-ops", "model-ops-to-glb"),
    "vntana-model-ops-to-gltf": ("vntana-model-ops", "model-ops-to-gltf"),
    "3dRecon-splat-toolbox-objects": ("3dRecon-splat-toolbox", "splat-objects"),
    "3dRecon-splat-toolbox-environments-360": ("3dRecon-splat-toolbox", "splat-environments-360"),
    # These built-ins keep their ids but require a template whose shipped definition carries no
    # isDefault flag, so the reference names that template the same way a consolidated id's
    # per-format template is named. Without it, template resolution fails on every execute.
    "rapid-pipeline-eks-to-glb": ("rapid-pipeline-eks-to-glb", "rapid-pipeline-eks-to-glb"),
    "isaaclab-evaluation": ("isaaclab-evaluation", "isaaclab-evaluation-cartpole"),
    "conversion-coordinate-transform": ("conversion-coordinate-transform",
                                        "coordinate-transform-wgs84-to-osgb36-laz"),
}

GLOBAL_DATABASE = "GLOBAL"

# Shipped built-in pipeline/workflow ids: the v2.6 ids the CDK vamsSchema importer registers plus
# the pre-consolidation v2.5 ids they replace. A built-in workflow carries its pipeline's id, so the
# one set covers both. A GLOBAL definition whose id is in this set is a built-in and is skipped (the
# importer owns it); any other GLOBAL definition was created by a user (V1 accepted the GLOBAL
# keyword on create) and is migrated like a user-database definition.
_BUILTIN_DEFINITION_IDS = set(CONSOLIDATED_PIPELINE_ID_MAP) | {
    # v2.6 consolidated + newly shipped built-ins.
    "3dRecon-splat-toolbox",
    "conversion-3d-basic",
    "conversion-coordinate-transform",
    "genai-metadata-3d-labeling-obj-glb-fbx-ply-stl-usd",
    "isaaclab-evaluation",
    "isaaclab-training",
    "metadata-extraction-cad-mesh",
    "nvidia-cosmos-predict2-text2world-2b",
    "nvidia-cosmos-predict2-text2world-14b",
    "nvidia-cosmos-predict2-video2world-2b",
    "nvidia-cosmos-predict2-video2world-14b",
    "nvidia-cosmos-reason2-2b",
    "nvidia-cosmos-reason2-8b",
    "nvidia-cosmos-transfer2-edge-2b",
    "nvidia-cosmos3-nano",
    "nvidia-cosmos3-super",
    "nvidia-cosmos3-super-image2video",
    "nvidia-cosmos3-super-text2image",
    "nvidia-gr00t-finetune-n1-5-3b",
    "preview-3d-thumbnail",
    "preview-pc-potree-viewer-las-laz-e57-ply",
    "rapid-pipeline",
    "rapid-pipeline-eks-to-glb",
    "vntana-model-ops",
    # v2.5 built-in workflow ids with no matching pipeline id.
    "rapid-pipeline-obj-to-gltf",
}

_PIPELINE_SCHEMA_VERSION = 1
_TEMPLATE_SCHEMA_VERSION = 1
_WORKFLOW_SCHEMA_VERSION = 1

# templateId of the single template carrying a migrated pipeline's V1 inputParameters.
_MIGRATED_TEMPLATE_ID = 'migrated-default'

# The only V2 trigger type, and the equivalent of V1's autoTriggerOnFileExtensionsUpload.
_TRIGGER_TYPE_FILE_UPLOAD = 'fileUpload'


def n(val) -> Dict:
    """DynamoDB wire-format Number attribute from an int/str."""
    return {'N': str(val)}


def bool_(val) -> Dict:
    """DynamoDB wire-format Boolean attribute."""
    return {'BOOL': bool(val)}


def m(val: Dict) -> Dict:
    """DynamoDB wire-format Map attribute from a python dict of already-wired values."""
    return {'M': val}


def string_list(values: List[str]) -> Dict:
    """DynamoDB wire-format List of Strings."""
    return {'L': [s(v) for v in values]}


def _remap_pipeline_reference(pipeline_database_id: str, pipeline_id: str,
                              migrated_template_pipelines=None) -> Tuple[str, str, str]:
    """Rewrite a workflow's reference to a (possibly consolidated) built-in pipeline. Only GLOBAL
    references are remapped; a user-owned pipeline passes through unchanged. Returns the effective
    (pipelineDatabaseId, pipelineId, defaultTemplateId) — for a consolidated built-in the third element
    is the per-format template that reproduces the pre-consolidation behavior (e.g. the old
    conversion-3d-basic-to-obj id maps to pipeline conversion-3d-basic + template convert-to-obj), which
    the migrated workflow ref carries so the pipeline (which now requires a template) still executes.

    For a user-owned pipeline whose V1 inputParameters were migrated into the 'migrated-default'
    template (its composite key is in migrated_template_pipelines), the ref carries that template id
    so a run applies the pipeline's V1 parameters without the caller naming a template per run."""
    if pipeline_database_id == GLOBAL_DATABASE and pipeline_id in CONSOLIDATED_PIPELINE_ID_MAP:
        new_id, template_id = CONSOLIDATED_PIPELINE_ID_MAP[pipeline_id]
        return GLOBAL_DATABASE, new_id, template_id
    if (pipeline_database_id, pipeline_id) in (migrated_template_pipelines or ()):
        return pipeline_database_id, pipeline_id, _MIGRATED_TEMPLATE_ID
    return pipeline_database_id, pipeline_id, ""


def _v1_execution_config(row: Dict) -> Dict:
    """Build the V2 executionConfig wire-format Map from a V1 pipeline row's loose fields
    (pipelineExecutionType / waitForCallback / taskTimeout + the userProvidedResource JSON)."""
    exec_type = row.get('pipelineExecutionType', {}).get('S', 'Lambda') or 'Lambda'
    wait_for_callback = row.get('waitForCallback', {}).get('S', 'Disabled') or 'Disabled'
    task_timeout = row.get('taskTimeout', {}).get('S', '') or ''

    # userProvidedResource is a JSON string {isProvided, resourceId, eventSource, eventDetailType};
    # the resourceId is the Lambda function name for a Lambda pipeline, the queue url for SQS, and the
    # event-bus arn for EventBridge.
    upr = {}
    try:
        upr = json.loads(row.get('userProvidedResource', {}).get('S', '') or '{}') or {}
    except (ValueError, TypeError):
        upr = {}
    resource_id = upr.get('resourceId', '') or ''

    lambda_block, sqs_block, eb_block = {}, {}, {}
    if exec_type == 'Lambda' and resource_id:
        lambda_block = {'resourceId': s(resource_id)}
    elif exec_type == 'SQS' and resource_id:
        sqs_block = {'queueUrl': s(resource_id)}
    elif exec_type == 'EventBridge':
        # source and detailType identify the event a customer's EventBridge rule matches on, so they
        # are carried alongside the bus. V1 stored the literal 'default' for the account default bus,
        # which is not a bus ARN; an empty busArn is what the task builder resolves to 'default'.
        bus_arn = '' if resource_id == 'default' else resource_id
        eb_block = {
            'busArn': s(bus_arn),
            'source': s(upr.get('eventSource', '') or ''),
            'detailType': s(upr.get('eventDetailType', '') or ''),
        }

    return m({
        'executionType': s(exec_type),
        'waitForCallback': s(wait_for_callback),
        'taskTimeout': s(task_timeout),
        'taskHeartbeatTimeout': s(row.get('taskHeartbeatTimeout', {}).get('S', '') or ''),
        'lambda': m(lambda_block),
        'sqs': m(sqs_block),
        'eventBridge': m(eb_block),
        'deadlineCloud': m({}),
    })


def _v1_system_config(row: Dict) -> Dict:
    """Build the V2 systemConfig wire-format Map from a V1 pipeline row. assetType (a single '.ext'
    or '.all') folds into inputFileFilters.allow ('.all' = allow-all, i.e. empty allow list)."""
    asset_type = row.get('assetType', {}).get('S', '') or ''
    allow = [] if (not asset_type or asset_type == '.all') else [asset_type]
    return m({
        'inputFileArity': s('one'),
        'assetScope': m({
            'crossAssetAllowed': bool_(False),
            'singleAssetOnly': bool_(True),
            # A V1 execute request that omitted fileKey ran against the whole asset, so whole-asset
            # runs stay permitted; the remaining scopes had no V1 equivalent.
            'wholeAssetAllowed': bool_(True),
            'folderAllowed': bool_(False),
        }),
        'metadataInputs': m({
            'assetMetadata': bool_(True),
            'fileMetadata': bool_(True),
            'fileAttributes': bool_(True),
            'databaseMetadata': bool_(True),
        }),
        'requireTemplate': bool_(False),
        # A V1 pipeline had no inline-config-override capability, and the V2 builders default the
        # flag off, so a migrated pipeline gains none; an operator opts in per pipeline.
        'allowCustomTemplateOverride': bool_(False),
        'auxPreviewPipelineSuffix': s(''),
        'inputFileFilters': m({'allow': string_list(allow), 'exclude': string_list([])}),
    })


def _v1_date_created(row: Dict, now: str) -> str:
    """The V1 row's dateCreated as ISO-8601 UTC. V1 stored it as a JSON-quoted
    '%B %d %Y - %H:%M:%S' string; an absent or unparseable value falls back to ``now``."""
    raw = row.get('dateCreated', {}).get('S', '') or ''
    if not raw:
        return now
    try:
        raw = json.loads(raw) if raw.startswith('"') else raw
        return (datetime.strptime(raw, '%B %d %Y - %H:%M:%S')
                .replace(tzinfo=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))
    except (ValueError, TypeError):
        return now


def _v2_pipeline_item(row: Dict, now: str) -> Dict:
    """Build a PipelineStorageTableV2 wire-format row from a V1 pipeline row."""
    database_id = row.get('databaseId', {}).get('S', '')
    pipeline_id = row.get('pipelineId', {}).get('S', '')
    name = row.get('name', {}).get('S', '') or pipeline_id
    description = row.get('description', {}).get('S', '') or ''
    category = row.get('pipelineType', {}).get('S', '') or ''  # standardFile/previewFile -> category
    enabled = row.get('enabled', {}).get('BOOL', True)
    date_created = _v1_date_created(row, now)
    return {
        'databaseId': s(database_id),
        'pipelineId': s(pipeline_id),
        'databaseId:category': s(f"{database_id}:{category}"),
        # Constant PK for the by-date global-list GSI (PipelinesByDateGSI), so migrated
        # pipelines appear in the global list alongside new ones.
        'allListPartition': s('pipeline'),
        'pipelineName': s(name),
        'category': s(category),
        'description': s(description),
        'executionConfig': _v1_execution_config(row),
        'systemConfig': _v1_system_config(row),
        'enabled': bool_(enabled),
        'archived': bool_(False),
        # dateModified is the by-date GSI sort key, so carrying the V1 creation date keeps migrated
        # pipelines in their original order in the newest-first global list rather than collapsing
        # every one of them onto the migration timestamp.
        'dateCreated': s(date_created),
        'dateModified': s(date_created),
        'createdBy': s('SYSTEM_USER'),
        'modifiedBy': s('SYSTEM_USER'),
        'schemaVersion': n(_PIPELINE_SCHEMA_VERSION),
        'migratedRecord': bool_(True),
    }


def _migrated_template_body_storage(database_id: str, pipeline_id: str, config_body: str) -> Dict:
    """Inline-vs-S3 storage fields for a migrated template body, mirroring what the pipeline template
    service writes. A V1 inputParameters JSON had no length cap, so a body over the inline threshold
    is offloaded to the default asset bucket rather than written onto the row, where it would exceed
    DynamoDB's 400 KB item limit and lose the template. `pendingBody` is the body to upload, empty
    when the body stays inline."""
    plan = tbs.plan_body_storage(config_body, '')
    if not plan['offload']:
        return {
            'bodyStorage': tbs.BODY_STORAGE_INLINE,
            'configBody': config_body,
            'configBodyS3Key': '',
            'configBodyHash': plan['configBodyHash'],
            'webFormHash': plan['webFormHash'],
            'pendingBody': '',
        }
    return {
        'bodyStorage': tbs.BODY_STORAGE_S3,
        'configBody': '',
        'configBodyS3Key': tbs.config_body_s3_key(database_id, pipeline_id, _MIGRATED_TEMPLATE_ID),
        'configBodyHash': plan['configBodyHash'],
        'webFormHash': plan['webFormHash'],
        'pendingBody': config_body,
    }


def _v2_migrated_template_item(row: Dict, now: str) -> Optional[Tuple[Dict, str]]:
    """Build a single migrated PipelineTemplatesStorageTable row carrying the V1 pipeline's
    inputParameters as the template configBody, so the migrated pipeline keeps its default config as a
    selectable template. Returns (item, bodyToUploadToS3) — the second element is empty when the body
    stays inline — or None when the V1 pipeline had no inputParameters."""
    database_id = row.get('databaseId', {}).get('S', '')
    pipeline_id = row.get('pipelineId', {}).get('S', '')
    input_parameters = row.get('inputParameters', {}).get('S', '') or ''
    if not input_parameters.strip():
        return None
    date_created = _v1_date_created(row, now)
    storage = _migrated_template_body_storage(database_id, pipeline_id, input_parameters)
    item = {
        'pipelineDatabaseId:pipelineId': s(f"{database_id}:{pipeline_id}"),
        'templateId': s(_MIGRATED_TEMPLATE_ID),
        'pipelineDatabaseId': s(database_id),
        'pipelineId': s(pipeline_id),
        'templateName': s('Migrated default parameters'),
        'description': s('Default input parameters migrated from the v2.5 pipeline definition.'),
        'configFormat': s('json'),
        'allowCustomEdit': bool_(True),
        'inputInstructions': s(''),
        'bodyStorage': s(storage['bodyStorage']),
        'configBody': s(storage['configBody']),
        'webFormJson': s(''),
        'configBodyS3Key': s(storage['configBodyS3Key']),
        # The hashes back unchanged-body detection on a later template update.
        'configBodyHash': s(storage['configBodyHash']),
        'webFormS3Key': s(''),
        'webFormHash': s(storage['webFormHash']),
        'overrides': m({}),
        # The pipeline's only template, so it is its default: the UI pre-selects it and a
        # require-template run falls back to it.
        'isDefault': bool_(True),
        'dateCreated': s(date_created),
        'dateModified': s(date_created),
        'createdBy': s('SYSTEM_USER'),
        'modifiedBy': s('SYSTEM_USER'),
        'schemaVersion': n(_TEMPLATE_SCHEMA_VERSION),
        'migratedRecord': bool_(True),
    }
    return item, storage['pendingBody']


def _existing_v2_deployment(dynamodb_client, table_name: str, database_id: str,
                            workflow_id: str) -> Tuple[str, Dict]:
    """The (workflow_arn, jobNames) already stored on the V2 workflow row, or ("", {'L': []}) when
    there is none. A re-run reads these so a V2 state machine deployed after the first migration (by
    re-saving the workflow) is preserved rather than cleared, which would orphan it and lose the
    output-path job names."""
    try:
        item = dynamodb_client.get_item(
            TableName=table_name,
            Key={'databaseId': s(database_id), 'workflowId': s(workflow_id)},
        ).get('Item') or {}
    except ClientError as e:
        logger.warning(f"Could not read the existing V2 workflow row {database_id}:{workflow_id}: {e}")
        return "", {'L': []}
    arn = item.get('workflow_arn', {}).get('S', '') or ''
    job_names = item.get('jobNames') if arn else None
    return arn, job_names or {'L': []}


def _v2_workflow_item(row: Dict, now: str, migrated_template_pipelines=None,
                      existing_arn: str = "", existing_job_names: Optional[Dict] = None) -> Dict:
    """Build a WorkflowStorageTableV2 wire-format row from a V1 workflow row, rewriting the
    specifiedPipelines.functions list into the V2 specifiedPipelines ref list (with consolidated
    built-in id remap). migrated_template_pipelines is the set of (databaseId, pipelineId) whose V1
    inputParameters became the 'migrated-default' template."""
    database_id = row.get('databaseId', {}).get('S', '')
    workflow_id = row.get('workflowId', {}).get('S', '')
    description = row.get('description', {}).get('S', '') or ''
    date_created = _v1_date_created(row, now)

    functions = row.get('specifiedPipelines', {}).get('M', {}).get('functions', {}).get('L', [])
    refs = []
    for fn in functions:
        fm = fn.get('M', {})
        p_id = fm.get('name', {}).get('S', '') or fm.get('pipelineId', {}).get('S', '')
        p_db = fm.get('databaseId', {}).get('S', '') or database_id
        eff_db, eff_id, default_template_id = _remap_pipeline_reference(
            p_db, p_id, migrated_template_pipelines)
        refs.append(m({
            'pipelineDatabaseId': s(eff_db),
            'pipelineId': s(eff_id),
            'pipelineDatabaseId:pipelineId': s(f"{eff_db}:{eff_id}"),
            'jobName': s(fm.get('name', {}).get('S', '') or ''),
            # A consolidated built-in requires a template, and a migrated user pipeline keeps its V1
            # parameters in a template; carry that template so the migrated workflow executes with
            # the same configuration without a human selecting one per run.
            'defaultTemplateId': s(default_template_id),
        }))

    return {
        'databaseId': s(database_id),
        'workflowId': s(workflow_id),
        'databaseId:category': s(f"{database_id}:"),
        # Constant PK for the by-date global-list GSI (WorkflowsByDateGSI), so migrated
        # workflows appear in the global list alongside new ones.
        'allListPartition': s('workflow'),
        'workflowName': s(workflow_id),
        'category': s(''),
        'description': s(description),
        # The V1 state machine's ASL references the removed V1 tracking lambdas and the V1 input
        # shape ($.bucketAsset / $.assetId), so it cannot run a V2 execution payload. The V1 ARN is
        # not carried over: executeWorkflow gates on a non-empty workflow_arn and returns "Workflow
        # has no deployed state machine." until the workflow is saved again, and that save deploys a
        # V2 state machine (workflowService PUT -> deploy_state_machine). A V2 ARN already on the
        # row (deployed by such a save before a re-run) is preserved.
        'workflow_arn': s(existing_arn),
        'aslSchemaVersion': s(''),
        'jobNames': existing_job_names or {'L': []},
        'specifiedPipelines': {'L': refs},
        'systemConfig': m({
            'inputFileArity': s('one'),
            'assetScope': m({
                'crossAssetAllowed': bool_(False),
                'singleAssetOnly': bool_(True),
                # A V1 execute request that omitted fileKey ran against the whole asset, so
                # whole-asset runs stay permitted; the remaining scopes had no V1 equivalent.
                'wholeAssetAllowed': bool_(True),
                'folderAllowed': bool_(False),
            }),
            'metadataInputs': m({
                'assetMetadata': bool_(True),
                'fileMetadata': bool_(True),
                'fileAttributes': bool_(True),
                'databaseMetadata': bool_(True),
            }),
            'inputFileFilters': m({'allow': string_list([]), 'exclude': string_list([])}),
            'concurrencyRestriction': s('none'),
            # A V1 execution always wrote back to its single input asset, and the V2 builders default
            # allowOverride off, so a migrated workflow keeps the input asset as its locked output
            # target; an operator opts in per workflow.
            'outputTarget': m({'locationType': s('asset'), 'allowOverride': bool_(False)}),
        }),
        'subDashboardUrl': s(''),
        'enabled': bool_(row.get('enabled', {}).get('BOOL', True)),
        'archived': bool_(False),
        # dateModified is the by-date GSI sort key, so carrying the V1 creation date keeps migrated
        # workflows in their original order in the newest-first global list.
        'dateCreated': s(date_created),
        'dateModified': s(date_created),
        'createdBy': s('SYSTEM_USER'),
        'modifiedBy': s('SYSTEM_USER'),
        'schemaVersion': n(_WORKFLOW_SCHEMA_VERSION),
        'migratedRecord': bool_(True),
    }


def _v1_auto_trigger_allow_patterns(raw: str) -> List[str]:
    """The inputFileFilters.allow patterns for a V1 autoTriggerOnFileExtensionsUpload value.

    V1 accepted a comma-delimited extension list ('jpg,.png') or the allow-all keyword ('all'/'.all').
    Each extension becomes the canonical '*.ext' pattern the V2 filter matcher reads; allow-all yields
    an empty list, which is how the matcher spells "no restriction"."""
    text = (raw or '').strip()
    if not text or text.lower() in ('all', '.all'):
        return []
    patterns = []
    for extension in text.split(','):
        body = extension.strip().lstrip('.').lower()
        if body and body not in patterns:
            patterns.append(body)
    return [f"*.{body}" for body in patterns]


def _v2_trigger_item(row: Dict, now: str) -> Optional[Dict]:
    """Build a WorkflowTriggersStorageTable fileUpload row from a V1 workflow's
    autoTriggerOnFileExtensionsUpload, so a workflow that auto-ran on upload keeps doing so. Returns
    None when the V1 workflow configured no auto-trigger."""
    raw = row.get('autoTriggerOnFileExtensionsUpload', {}).get('S', '') or ''
    if not raw.strip():
        return None
    database_id = row.get('databaseId', {}).get('S', '')
    workflow_id = row.get('workflowId', {}).get('S', '')
    date_created = _v1_date_created(row, now)
    return {
        'workflowDatabaseId:workflowId': s(f"{database_id}:{workflow_id}"),
        # Sort key. A V1 workflow had exactly one auto-trigger, so it is the workflow's first trigger
        # of the type and keeps the bare type as its key (no '#triggerId' suffix).
        'triggerType': s(_TRIGGER_TYPE_FILE_UPLOAD),
        # Partition key of TriggersByBaseTypeGSI, which the upload dispatcher queries by exact type.
        'triggerBaseType': s(_TRIGGER_TYPE_FILE_UPLOAD),
        'triggerId': s(''),
        'workflowDatabaseId': s(database_id),
        'workflowId': s(workflow_id),
        'triggerConfig': m({
            'inputFileFilters': m({
                'allow': string_list(_v1_auto_trigger_allow_patterns(raw)),
                'exclude': string_list([]),
            }),
            # A V1 auto-trigger named no per-pipeline template; a fired run resolves each pipeline's
            # own default, which for a migrated pipeline is its 'migrated-default' template.
            'defaultTemplateIds': m({}),
        }),
        'enabled': bool_(row.get('enabled', {}).get('BOOL', True)),
        'dateCreated': s(date_created),
        'dateModified': s(date_created),
        'migratedRecord': bool_(True),
    }


def _offload_template_body(s3_client, bucket: str, key: str, body: str,
                           database_id: str, pipeline_id: str, dry_run: bool) -> bool:
    """Write an over-threshold migrated template body to the default asset bucket. False when it could
    not be stored, so the caller drops the template row rather than writing one whose configBody
    neither lives inline nor resolves in S3."""
    if not bucket or s3_client is None:
        logger.error(
            f"Pipeline '{database_id}:{pipeline_id}' has inputParameters larger than the "
            f"{tbs.INLINE_THRESHOLD_BYTES}-byte inline limit, but the default asset bucket could not "
            "be resolved, so its migrated template is skipped. Re-run the step with the bucket "
            "resolvable to migrate the template.")
        return False
    if dry_run:
        logger.info(f"  [DRY RUN] Would offload the '{database_id}:{pipeline_id}' template body to "
                    f"s3://{bucket}/{key}")
        return True
    try:
        tbs.write_body_to_s3(s3_client, bucket, key, body)
        return True
    except (ClientError, BotoCoreError) as e:
        logger.error(f"Failed offloading the '{database_id}:{pipeline_id}' template body to "
                     f"s3://{bucket}/{key}: {e}. Its migrated template is skipped.")
        return False


def resolve_default_bucket_name(dynamodb_client, buckets_table_name: str) -> str:
    """The bucket name of the VAMS default asset bucket (the row flagged isDefault), or '' when none
    is flagged. Mirrors common.workflows.defaultBucket: a bucket registered under several prefixes has
    a row per prefix, and the bucket-root row is preferred so the canonical base wins."""
    rows = []
    for item in iter_all_items(dynamodb_client, buckets_table_name,
                               projection='bucketName,baseAssetsPrefix,isDefault'):
        if item.get('isDefault', {}).get('BOOL') is not True:
            continue
        rows.append((item.get('baseAssetsPrefix', {}).get('S', '') or '',
                     item.get('bucketName', {}).get('S', '') or ''))
    if not rows:
        return ''
    rows.sort(key=lambda entry: (0 if entry[0].strip('/') == '' else 1, entry[0], entry[1]))
    distinct = {name for _prefix, name in rows}
    if len(distinct) > 1:
        logger.error(f"More than one bucket is flagged as the VAMS default ({sorted(distinct)}); using "
                     f"{rows[0][1]}. Clear the stale isDefault row(s) in the S3 asset buckets table.")
    return rows[0][1]


def migrate_pipeline_workflow_definitions(dynamodb_client, cfg, dry_run: bool, limit: int,
                                          s3_client=None) -> Tuple[Dict, Dict]:
    """Migrate user-database pipeline + workflow DEFINITIONS from V1 tables to V2 tables. GLOBAL
    built-ins are skipped (re-created by the CDK importer). Returns (counts, totals)."""
    v1_pipeline_table = cfg['pipeline_storage_table_name_v1']
    v2_pipeline_table = cfg['pipeline_storage_table_name_v2']
    v2_template_table = cfg['pipeline_templates_storage_table_name']
    v1_workflow_table = cfg['workflow_storage_table_name']
    v2_workflow_table = cfg['workflow_storage_table_name_v2']
    triggers_table = cfg.get('workflow_triggers_storage_table_name')
    # Default asset bucket for an offloaded template body. Optional: a body over the inline threshold
    # is skipped rather than written onto the row, where it would exceed the 400 KB item limit.
    template_body_bucket = cfg.get('pipeline_template_body_bucket_name')

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    counts = {'pipelines': 0, 'templates': 0, 'workflows': 0, 'triggers': 0, 'skipped_global': 0,
              'skipped_deleted': 0, 'errors': 0,
              'duplicate_pipeline_ids': 0, 'duplicate_workflow_ids': 0}

    # v2.6 requires pipeline and workflow ids to be unique across all databases. Pre-existing
    # duplicates are migrated as-is (their composite PK keeps them distinct) and reported here so an
    # operator can rename them; new creates are rejected by the API.
    pipeline_id_owners: dict = {}
    workflow_id_owners: dict = {}

    # --- Pipelines ---
    pipeline_batch, template_batch = [], []
    # (databaseId, pipelineId) of every pipeline whose V1 inputParameters became a migrated template.
    # The workflow pass reads this to point each ref's defaultTemplateId at that template.
    migrated_template_pipelines = set()
    v1_pipelines = scan_all_items(dynamodb_client, v1_pipeline_table, limit)
    for row in v1_pipelines:
        database_id = row.get('databaseId', {}).get('S', '')
        pipeline_id = row.get('pipelineId', {}).get('S', '')
        if not database_id or not pipeline_id:
            continue
        if database_id.endswith('#deleted'):
            counts['skipped_deleted'] += 1
            continue
        if database_id == GLOBAL_DATABASE and pipeline_id in _BUILTIN_DEFINITION_IDS:
            # Built-in: re-created by the CDK importer, never clobbered.
            counts['skipped_global'] += 1
            logger.info(f"  Skipping GLOBAL built-in pipeline '{pipeline_id}' "
                        "(re-created by the CDK vamsSchema importer)")
            continue
        prior = pipeline_id_owners.get(pipeline_id)
        if prior:
            counts['duplicate_pipeline_ids'] += 1
            logger.warning(f"  pipelineId '{pipeline_id}' exists in both '{prior}' and "
                           f"'{database_id}'; v2.6 requires ids unique across databases. Migrated "
                           "as-is; rename one before creating new pipelines with this id.")
        else:
            pipeline_id_owners[pipeline_id] = database_id
        pipeline_batch.append(_v2_pipeline_item(row, now))
        counts['pipelines'] += 1
        tpl = _v2_migrated_template_item(row, now)
        if tpl:
            item, pending_body = tpl
            if pending_body and not _offload_template_body(
                    s3_client, template_body_bucket,
                    item['configBodyS3Key']['S'], pending_body, database_id, pipeline_id, dry_run):
                counts['errors'] += 1
                continue
            template_batch.append(item)
            migrated_template_pipelines.add((database_id, pipeline_id))
            counts['templates'] += 1

    w, e = flush_batch_write(dynamodb_client, v2_pipeline_table, pipeline_batch, dry_run)
    counts['errors'] += e
    if template_batch:
        _, te = flush_batch_write(dynamodb_client, v2_template_table, template_batch, dry_run)
        counts['errors'] += te

    # --- Workflows ---
    workflow_batch, trigger_batch = [], []
    v1_workflows = scan_all_items(dynamodb_client, v1_workflow_table, limit)
    for row in v1_workflows:
        database_id = row.get('databaseId', {}).get('S', '')
        workflow_id = row.get('workflowId', {}).get('S', '')
        if not database_id or not workflow_id:
            continue
        if database_id.endswith('#deleted'):
            counts['skipped_deleted'] += 1
            continue
        if database_id == GLOBAL_DATABASE and workflow_id in _BUILTIN_DEFINITION_IDS:
            counts['skipped_global'] += 1
            logger.info(f"  Skipping GLOBAL built-in workflow '{workflow_id}' "
                        "(re-created by the CDK vamsSchema importer)")
            continue
        prior = workflow_id_owners.get(workflow_id)
        if prior:
            counts['duplicate_workflow_ids'] += 1
            logger.warning(f"  workflowId '{workflow_id}' exists in both '{prior}' and "
                           f"'{database_id}'; v2.6 requires ids unique across databases. Migrated "
                           "as-is; rename one before creating new workflows with this id.")
        else:
            workflow_id_owners[workflow_id] = database_id
        existing_arn, existing_job_names = _existing_v2_deployment(
            dynamodb_client, v2_workflow_table, database_id, workflow_id)
        workflow_batch.append(_v2_workflow_item(
            row, now, migrated_template_pipelines, existing_arn, existing_job_names))
        counts['workflows'] += 1
        # V1's autoTriggerOnFileExtensionsUpload is a fileUpload trigger row in V2; without one the
        # workflow stops firing on upload.
        trigger = _v2_trigger_item(row, now)
        if trigger:
            if triggers_table:
                trigger_batch.append(trigger)
                counts['triggers'] += 1
            else:
                logger.warning(
                    f"  Workflow '{database_id}:{workflow_id}' auto-runs on file upload, but the "
                    "workflow triggers table name is unset, so its trigger is not migrated and the "
                    "workflow will not fire on upload.")

    _, we = flush_batch_write(dynamodb_client, v2_workflow_table, workflow_batch, dry_run)
    counts['errors'] += we

    if trigger_batch:
        _, tre = flush_batch_write(dynamodb_client, triggers_table, trigger_batch, dry_run)
        counts['errors'] += tre

    return counts, {'v1_pipelines': len(v1_pipelines), 'v1_workflows': len(v1_workflows)}


# The by-date global-list GSIs (PipelinesByDateGSI / WorkflowsByDateGSI / WorkflowExecutionsByDateGSI)
# are keyed on a constant partition attribute. Rows written before the attribute existed are absent
# from those GSIs, so the global (cross-database) "all" lists omit them. This backfill stamps the
# constant value on any row missing it. (attr_name, constant_value) per table.
_ALL_LIST_PARTITION_ATTR = "allListPartition"
_GLOBAL_LIST_BACKFILL = [
    ("pipeline_storage_table_name_v2", "pipeline"),
    ("workflow_storage_table_name_v2", "workflow"),
    ("workflow_executions_storage_table_name_v2", "execution"),
]


def backfill_global_list_partition(dynamodb_client, cfg, dry_run: bool, limit: int):
    """Set allListPartition on any V2 pipeline/workflow/execution row missing it, so the by-date
    global-list GSIs return pre-existing rows. Idempotent: only rows without the attribute are
    updated (ConditionExpression attribute_not_exists), so re-runs are no-ops."""
    counts = {"updated": 0, "already": 0, "errors": 0}
    for cfg_key, const_value in _GLOBAL_LIST_BACKFILL:
        table_name = cfg.get(cfg_key)
        if not table_name:
            continue
        rows = scan_all_items(dynamodb_client, table_name, limit)
        # Each table's primary key attributes, derived from the row itself (no schema lookup):
        # pipeline (databaseId, pipelineId); workflow (databaseId, workflowId);
        # execution (workflowExecutionId, workflowDatabaseId:workflowId).
        for row in rows:
            if _ALL_LIST_PARTITION_ATTR in row:
                counts["already"] += 1
                continue
            key = {}
            for pk_attr in ("workflowExecutionId", "databaseId"):
                if pk_attr in row:
                    key[pk_attr] = row[pk_attr]
                    break
            for sk_attr in ("workflowDatabaseId:workflowId", "pipelineId", "workflowId"):
                if sk_attr in row:
                    key[sk_attr] = row[sk_attr]
                    break
            if len(key) != 2:
                counts["errors"] += 1
                continue
            if dry_run:
                counts["updated"] += 1
                continue
            try:
                dynamodb_client.update_item(
                    TableName=table_name,
                    Key=key,
                    UpdateExpression="SET #a = :v",
                    ConditionExpression="attribute_not_exists(#a)",
                    ExpressionAttributeNames={"#a": _ALL_LIST_PARTITION_ATTR},
                    ExpressionAttributeValues={":v": s(const_value)},
                )
                counts["updated"] += 1
            except ClientError as e:
                if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                    counts["already"] += 1
                else:
                    logger.error(f"Error backfilling {table_name} key={key}: {e}")
                    counts["errors"] += 1
    return counts


def run_global_list_backfill_step(config: dict, args, base_param_prefix, profile, region, dry_run) -> int:
    """Backfill allListPartition on existing V2 pipeline/workflow/execution rows. Returns 0 on success."""
    session_kwargs = {}
    if profile:
        session_kwargs['profile_name'] = profile
    if region:
        session_kwargs['region_name'] = region
    dynamodb_client = boto3.Session(**session_kwargs).client('dynamodb')

    try:
        lookup = SsmResourceLookup(base_param_prefix, profile=profile, region=region) if base_param_prefix else None

        def resolve(cfg_key, param_key):
            override = config.get(cfg_key)
            if override and not str(override).startswith('<') and not str(override).startswith('YOUR-'):
                return override
            if not lookup:
                raise ValueError(
                    f"Config '{cfg_key}' is unset and no resource_names_ssm_param_prefix is configured "
                    "to resolve it from SSM.")
            return lookup.resolve(param_key)

        cfg = {
            'pipeline_storage_table_name_v2': resolve(
                'pipeline_storage_table_name_v2', ResourceParamKeys.PIPELINE_STORAGE_TABLE_V2),
            'workflow_storage_table_name_v2': resolve(
                'workflow_storage_table_name_v2', ResourceParamKeys.WORKFLOW_STORAGE_TABLE_V2),
            'workflow_executions_storage_table_name_v2': resolve(
                'workflow_executions_storage_table_name_v2', ResourceParamKeys.WORKFLOW_EXECUTIONS_STORAGE_TABLE_V2),
        }
    except Exception as e:
        logger.error(f"Failed resolving table names for the globalListBackfill step: {e}")
        return 1

    limit = args.limit if args.limit is not None else config.get('limit')

    logger.info("=" * 80)
    logger.info("VAMS v2.5 -> v2.6 GLOBAL-LIST PARTITION BACKFILL (allListPartition)")
    logger.info(f"Dry Run: {dry_run}")
    logger.info("=" * 80)

    start = datetime.now(timezone.utc)
    try:
        counts = backfill_global_list_partition(dynamodb_client, cfg, dry_run, limit)
    except Exception as e:
        logger.error(f"globalListBackfill step failed: {e}")
        return 1
    duration = (datetime.now(timezone.utc) - start).total_seconds()

    logger.info("=" * 80)
    logger.info("GLOBAL-LIST PARTITION BACKFILL SUMMARY")
    logger.info(f"  Duration: {duration:.1f}s   Dry Run: {dry_run}")
    logger.info(f"  Rows updated (attribute set):    {counts['updated']}")
    logger.info(f"  Rows already had the attribute:  {counts['already']}")
    logger.info(f"  Errors:                          {counts['errors']}")
    logger.info("=" * 80)

    return 0 if counts['errors'] == 0 else 1


def run_pipeline_workflow_definitions_step(config: dict, args, base_param_prefix, profile, region, dry_run) -> int:
    """Run the pipeline + workflow DEFINITION migration step (V1 -> V2). Returns 0 on success."""
    session_kwargs = {}
    if profile:
        session_kwargs['profile_name'] = profile
    if region:
        session_kwargs['region_name'] = region
    session = boto3.Session(**session_kwargs)
    dynamodb_client = session.client('dynamodb')
    s3_client = session.client('s3')

    # Resolve the five table names: explicit config overrides win, else resolve from the SSM prefix.
    try:
        lookup = SsmResourceLookup(base_param_prefix, profile=profile, region=region) if base_param_prefix else None

        def resolve(cfg_key, param_key):
            override = config.get(cfg_key)
            if override and not str(override).startswith('<') and not str(override).startswith('YOUR-'):
                return override
            if not lookup:
                raise ValueError(
                    f"Config '{cfg_key}' is unset and no resource_names_ssm_param_prefix is configured "
                    "to resolve it from SSM.")
            return lookup.resolve(param_key)

        cfg = {
            'pipeline_storage_table_name_v1': resolve(
                'pipeline_storage_table_name_v1', ResourceParamKeys.PIPELINE_STORAGE_TABLE),
            'pipeline_storage_table_name_v2': resolve(
                'pipeline_storage_table_name_v2', ResourceParamKeys.PIPELINE_STORAGE_TABLE_V2),
            'pipeline_templates_storage_table_name': resolve(
                'pipeline_templates_storage_table_name', ResourceParamKeys.PIPELINE_TEMPLATES_STORAGE_TABLE),
            'workflow_storage_table_name': resolve(
                'workflow_storage_table_name', ResourceParamKeys.WORKFLOW_STORAGE_TABLE),
            'workflow_storage_table_name_v2': resolve(
                'workflow_storage_table_name_v2', ResourceParamKeys.WORKFLOW_STORAGE_TABLE_V2),
            'workflow_triggers_storage_table_name': resolve(
                'workflow_triggers_storage_table_name', ResourceParamKeys.WORKFLOW_TRIGGERS_STORAGE_TABLE),
        }
    except Exception as e:
        logger.error(f"Failed resolving table names for the pipelineWorkflowDefinitions step: {e}")
        return 1

    # The default asset bucket houses an offloaded template body. Optional: only a pipeline whose V1
    # inputParameters exceed the inline threshold needs it, and that case reports itself.
    cfg['pipeline_template_body_bucket_name'] = None
    try:
        buckets_table = resolve('s3_asset_buckets_storage_table_name',
                                ResourceParamKeys.S3_ASSET_BUCKETS_STORAGE_TABLE)
        cfg['pipeline_template_body_bucket_name'] = resolve_default_bucket_name(
            dynamodb_client, buckets_table)
    except Exception as e:
        logger.warning(f"Could not resolve the VAMS default asset bucket: {e}")

    limit = args.limit if args.limit is not None else config.get('limit')

    logger.info("=" * 80)
    logger.info("VAMS v2.5 -> v2.6 PIPELINE + WORKFLOW DEFINITION MIGRATION (V1 -> V2)")
    logger.info(f"Dry Run: {dry_run}   (GLOBAL built-ins are skipped — re-created by the CDK importer)")
    logger.info("=" * 80)

    start = datetime.now(timezone.utc)
    try:
        counts, totals = migrate_pipeline_workflow_definitions(
            dynamodb_client, cfg, dry_run, limit, s3_client=s3_client)
    except Exception as e:
        logger.error(f"pipelineWorkflowDefinitions step failed: {e}")
        return 1
    duration = (datetime.now(timezone.utc) - start).total_seconds()

    logger.info("=" * 80)
    logger.info("PIPELINE + WORKFLOW DEFINITION MIGRATION SUMMARY")
    logger.info(f"  Duration: {duration:.1f}s   Dry Run: {dry_run}")
    logger.info(f"  V1 pipelines scanned:     {totals['v1_pipelines']}")
    logger.info(f"  V1 workflows scanned:     {totals['v1_workflows']}")
    logger.info(f"  V2 pipeline rows written: {counts['pipelines']}")
    logger.info(f"  V2 template rows written: {counts['templates']}")
    logger.info(f"  V2 workflow rows written: {counts['workflows']}")
    logger.info(f"  fileUpload trigger rows:  {counts['triggers']}")
    logger.info(f"  Skipped (GLOBAL built-in): {counts['skipped_global']}")
    logger.info(f"  Skipped (soft-deleted):    {counts['skipped_deleted']}")
    logger.info(f"  Errors:                    {counts['errors']}")
    logger.info("=" * 80)

    return 0 if counts['errors'] == 0 else 1


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
                        choices=['reindex', 'assetHistory', 'workflowExecutions', 'auxPreviewRelocation',
                                 'pipelineWorkflowDefinitions', 'globalListBackfill', 'all'],
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
    run_pipeline_workflow_definitions = args.steps in ('pipelineWorkflowDefinitions', 'all')
    run_global_list_backfill = args.steps in ('globalListBackfill', 'all')

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

    if run_pipeline_workflow_definitions:
        logger.info("")
        logger.info("##### STEP: Pipeline + workflow definition migration (V1 -> V2) #####")
        rc = run_pipeline_workflow_definitions_step(config, args, base_param_prefix, profile, region, dry_run)
        if rc != 0:
            exit_code = rc

    if run_global_list_backfill:
        logger.info("")
        logger.info("##### STEP: Global-list partition backfill (allListPartition) #####")
        rc = run_global_list_backfill_step(config, args, base_param_prefix, profile, region, dry_run)
        if rc != 0:
            exit_code = rc

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
