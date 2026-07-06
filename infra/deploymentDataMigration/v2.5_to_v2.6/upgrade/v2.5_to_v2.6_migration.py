#!/usr/bin/env python3
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Data Migration Script for VAMS v2.5 to v2.6 - OpenSearch Reindex (v2 -> v3)
and Asset History Backfill

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
from typing import Dict, Optional

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

    operation = args.operation or config.get('operation', 'both')
    dry_run = args.dry_run or bool(config.get('dry_run', False))
    # CLI flag wins; otherwise fall back to config (default false)
    clear_indexes = args.clear_indexes or bool(config.get('clear_indexes', False))
    limit = args.limit if args.limit is not None else config.get('limit')
    profile = args.profile or config.get('aws_profile')
    region = args.region or config.get('aws_region')

    # Resolve the reindexer function name: explicit config value wins; otherwise look it
    # up from the deployment's SSM resource-name parameters via the base prefix (core
    # stack output 'ResourceNamesSSMParamPrefixOutput').
    function_name = config.get('reindexer_function_name')
    if function_name and function_name.startswith('<'):
        function_name = None  # unfilled template placeholder
    base_param_prefix = config.get('resource_names_ssm_param_prefix')
    if base_param_prefix and base_param_prefix.startswith('<'):
        base_param_prefix = None
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

    reindex_ok = True
    if result.get('timeout'):
        logger.warning("Reindex invocation timed out -- Lambda continues processing in the background.")
        logger.warning("Verify completion via CloudWatch Logs.")
    elif 'error' in result:
        logger.error("Reindex migration failed.")
        reindex_ok = False
    else:
        logger.info("Reindex migration completed.")

    # Phase 2: asset history backfill (skippable via config)
    if config.get('skip_asset_history_backfill'):
        logger.info("Skipping asset history backfill (skip_asset_history_backfill=true).")
        return 0 if reindex_ok else 1

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

    return 0 if reindex_ok else 1


if __name__ == "__main__":
    sys.exit(main())
