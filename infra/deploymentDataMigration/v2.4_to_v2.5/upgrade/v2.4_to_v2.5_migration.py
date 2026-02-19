#!/usr/bin/env python3
# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Data Migration Script for VAMS v2.4 to v2.5 - Asset Version databaseId Backfill

This script backfills `databaseId` and `databaseId:assetId` fields on all existing
asset version records in the AssetVersionsStorageTable. These fields are needed for
efficient querying of asset versions by database.

Migration Phases:
1. Build lookup cache: Scan asset storage table, build assetId -> databaseId mapping
2. Backfill: Scan asset versions table, update records missing databaseId
3. Verify: Count records with/without databaseId, report orphaned versions

Key Features:
- Idempotent (safe to re-run) - skips records that already have databaseId
- Dry-run mode for safe testing
- Comprehensive error handling and logging
- Continue-on-error for individual records
- Progress reporting

Usage:
    # Dry run (recommended first step)
    python v2.4_to_v2.5_migration.py --config v2.4_to_v2.5_migration_config.json --dry-run

    # Production migration
    python v2.4_to_v2.5_migration.py --config v2.4_to_v2.5_migration_config.json

    # Test with limited items
    python v2.4_to_v2.5_migration.py --config v2.4_to_v2.5_migration_config.json --limit 10 --dry-run

Requirements:
    - Python 3.6+
    - boto3
    - AWS credentials with DynamoDB read/write permissions
"""

import argparse
import boto3
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Dict, List, Tuple
from botocore.exceptions import ClientError

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


def load_config_from_file(config_file: str) -> dict:
    """
    Load configuration from a JSON file.

    Args:
        config_file: Path to the configuration file

    Returns:
        dict: Configuration dictionary
    """
    try:
        with open(config_file, 'r') as f:
            config = json.load(f)

        # Remove comment fields
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


#######################
# PHASE 1: BUILD LOOKUP CACHE
#######################

def build_asset_to_database_cache(dynamodb_client, asset_table_name: str, limit: int = None) -> Dict[str, str]:
    """
    Scan the asset storage table and build a mapping of assetId -> databaseId.

    The asset storage table has PK=databaseId, SK=assetId. We scan all items
    to build a reverse lookup from assetId to databaseId.

    Args:
        dynamodb_client: Boto3 DynamoDB client
        asset_table_name: Name of the AssetStorageTable
        limit: Maximum number of assets to retrieve (for testing)

    Returns:
        Dict mapping assetId -> databaseId
    """
    logger.info(f"Scanning {asset_table_name} to build assetId -> databaseId lookup cache...")

    cache = {}
    scan_kwargs = {
        'TableName': asset_table_name,
        'ProjectionExpression': 'databaseId, assetId'
    }

    items_scanned = 0

    try:
        response = dynamodb_client.scan(**scan_kwargs)
        for item in response.get('Items', []):
            asset_id = item.get('assetId', {}).get('S', '')
            database_id = item.get('databaseId', {}).get('S', '')
            if asset_id and database_id:
                cache[asset_id] = database_id
                items_scanned += 1

        # Handle pagination
        while 'LastEvaluatedKey' in response:
            if limit and items_scanned >= limit:
                break
            scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
            response = dynamodb_client.scan(**scan_kwargs)
            for item in response.get('Items', []):
                asset_id = item.get('assetId', {}).get('S', '')
                database_id = item.get('databaseId', {}).get('S', '')
                if asset_id and database_id:
                    cache[asset_id] = database_id
                    items_scanned += 1

            if items_scanned % 500 == 0:
                logger.info(f"  Cached {items_scanned} asset records...")

        logger.info(f"Built lookup cache with {len(cache)} assetId -> databaseId mappings")
        return cache

    except ClientError as e:
        logger.error(f"Error scanning asset storage table: {e}")
        raise


#######################
# PHASE 2: BACKFILL databaseId ON ASSET VERSIONS
#######################

def scan_asset_versions(dynamodb_client, versions_table_name: str, limit: int = None) -> List[Dict]:
    """
    Scan the asset versions table for all records.

    Args:
        dynamodb_client: Boto3 DynamoDB client
        versions_table_name: Name of the AssetVersionsStorageTable
        limit: Maximum number of records to retrieve (for testing)

    Returns:
        List of asset version items
    """
    logger.info(f"Scanning {versions_table_name} for asset version records...")

    records = []
    scan_kwargs = {
        'TableName': versions_table_name
    }

    try:
        response = dynamodb_client.scan(**scan_kwargs)
        records.extend(response.get('Items', []))

        # Handle pagination
        while 'LastEvaluatedKey' in response and (not limit or len(records) < limit):
            scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
            response = dynamodb_client.scan(**scan_kwargs)
            records.extend(response.get('Items', []))

            if len(records) % 500 == 0:
                logger.info(f"  Scanned {len(records)} version records...")

        if limit and len(records) > limit:
            records = records[:limit]

        logger.info(f"Found {len(records)} asset version records")
        return records

    except ClientError as e:
        logger.error(f"Error scanning asset versions table: {e}")
        raise


def update_version_record(dynamodb_client, versions_table_name: str,
                          asset_id: str, asset_version_id: str,
                          database_id: str, dry_run: bool = False) -> bool:
    """
    Update a single asset version record with databaseId and databaseId:assetId.

    Args:
        dynamodb_client: Boto3 DynamoDB client
        versions_table_name: Name of the AssetVersionsStorageTable
        asset_id: The assetId (partition key)
        asset_version_id: The assetVersionId (sort key)
        database_id: The databaseId to set
        dry_run: If True, don't actually write

    Returns:
        True if successful, False otherwise
    """
    composite_key = f"{database_id}:{asset_id}"

    if dry_run:
        logger.debug(f"DRY RUN: Would update {asset_id}/{asset_version_id} with databaseId={database_id}, databaseId:assetId={composite_key}")
        return True

    try:
        dynamodb_client.update_item(
            TableName=versions_table_name,
            Key={
                'assetId': {'S': asset_id},
                'assetVersionId': {'S': asset_version_id}
            },
            UpdateExpression='SET databaseId = :dbId, #compositeKey = :compositeVal',
            ExpressionAttributeNames={
                '#compositeKey': 'databaseId:assetId'
            },
            ExpressionAttributeValues={
                ':dbId': {'S': database_id},
                ':compositeVal': {'S': composite_key}
            }
        )
        return True
    except ClientError as e:
        logger.error(f"Error updating record {asset_id}/{asset_version_id}: {e}")
        return False


def backfill_database_ids(dynamodb_client, versions_table_name: str,
                          version_records: List[Dict],
                          asset_db_cache: Dict[str, str],
                          dry_run: bool = False) -> Tuple[int, int, int, int]:
    """
    Backfill databaseId on asset version records that are missing it.

    Args:
        dynamodb_client: Boto3 DynamoDB client
        versions_table_name: Name of the AssetVersionsStorageTable
        version_records: List of asset version items from DynamoDB
        asset_db_cache: Mapping of assetId -> databaseId
        dry_run: If True, don't actually write

    Returns:
        Tuple of (updated_count, skipped_count, orphaned_count, error_count)
    """
    updated_count = 0
    skipped_count = 0
    orphaned_count = 0
    error_count = 0
    total = len(version_records)

    logger.info(f"Processing {total} asset version records...")

    for idx, record in enumerate(version_records, 1):
        asset_id = record.get('assetId', {}).get('S', '')
        asset_version_id = record.get('assetVersionId', {}).get('S', '')
        existing_db_id = record.get('databaseId', {}).get('S', '')

        if not asset_id or not asset_version_id:
            logger.warning(f"Record {idx}: Missing assetId or assetVersionId, skipping")
            error_count += 1
            continue

        # Skip records that already have databaseId (idempotent)
        if existing_db_id:
            skipped_count += 1
            logger.debug(f"Record {idx}: {asset_id}/{asset_version_id} already has databaseId={existing_db_id}, skipping")
            continue

        # Look up databaseId from cache
        database_id = asset_db_cache.get(asset_id)
        if not database_id:
            orphaned_count += 1
            logger.warning(f"Record {idx}: assetId={asset_id} not found in asset table (orphaned version)")
            continue

        # Update the record
        success = update_version_record(
            dynamodb_client, versions_table_name,
            asset_id, asset_version_id,
            database_id, dry_run
        )

        if success:
            updated_count += 1
        else:
            error_count += 1

        # Progress reporting
        if idx % 100 == 0:
            logger.info(f"  Progress: {idx}/{total} records processed "
                        f"(updated={updated_count}, skipped={skipped_count}, "
                        f"orphaned={orphaned_count}, errors={error_count})")

    return updated_count, skipped_count, orphaned_count, error_count


#######################
# PHASE 3: VERIFICATION
#######################

def verify_migration(dynamodb_client, versions_table_name: str,
                     asset_db_cache: Dict[str, str]) -> bool:
    """
    Verify migration success by counting records with and without databaseId.

    Args:
        dynamodb_client: Boto3 DynamoDB client
        versions_table_name: Name of the AssetVersionsStorageTable
        asset_db_cache: Mapping of assetId -> databaseId (for orphan detection)

    Returns:
        True if verification passed, False otherwise
    """
    logger.info("Verifying migration...")

    total_count = 0
    with_db_id_count = 0
    without_db_id_count = 0
    orphaned_count = 0

    scan_kwargs = {
        'TableName': versions_table_name
    }

    try:
        response = dynamodb_client.scan(**scan_kwargs)
        while True:
            for item in response.get('Items', []):
                total_count += 1
                asset_id = item.get('assetId', {}).get('S', '')
                has_db_id = 'databaseId' in item and item.get('databaseId', {}).get('S', '')

                if has_db_id:
                    with_db_id_count += 1
                else:
                    without_db_id_count += 1
                    # Check if this is an orphan
                    if asset_id and asset_id not in asset_db_cache:
                        orphaned_count += 1

            if 'LastEvaluatedKey' not in response:
                break
            scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
            response = dynamodb_client.scan(**scan_kwargs)

        logger.info(f"Verification Results:")
        logger.info(f"  Total asset version records: {total_count}")
        logger.info(f"  Records with databaseId: {with_db_id_count}")
        logger.info(f"  Records without databaseId: {without_db_id_count}")
        if orphaned_count > 0:
            logger.info(f"  Orphaned versions (assetId not in asset table): {orphaned_count}")

        # Verification passes if all non-orphaned records have databaseId
        expected_without = orphaned_count
        verification_passed = (without_db_id_count == expected_without)

        if verification_passed:
            if orphaned_count > 0:
                logger.info(f"Verification PASSED (all non-orphaned records have databaseId; {orphaned_count} orphaned records remain without)")
            else:
                logger.info(f"Verification PASSED (all {total_count} records have databaseId)")
        else:
            non_orphan_missing = without_db_id_count - orphaned_count
            logger.warning(f"Verification FAILED - {non_orphan_missing} non-orphaned records still missing databaseId")

        return verification_passed

    except ClientError as e:
        logger.error(f"Error during verification: {e}")
        return False


#######################
# MAIN MIGRATION FUNCTION
#######################

def main():
    """Main function to run the migration."""
    parser = argparse.ArgumentParser(
        description='VAMS v2.4 to v2.5 Migration Script - Asset Version databaseId Backfill',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry run (recommended first)
  python v2.4_to_v2.5_migration.py --config v2.4_to_v2.5_migration_config.json --dry-run

  # Production migration
  python v2.4_to_v2.5_migration.py --config v2.4_to_v2.5_migration_config.json

  # Test with limited items
  python v2.4_to_v2.5_migration.py --config v2.4_to_v2.5_migration_config.json --limit 10 --dry-run

Notes:
  - Configuration file is required
  - Table names must be provided in config file
  - Dry-run mode is recommended for initial testing
  - Migration is idempotent - can be safely re-run
  - No data is deleted - only new fields are added
        """
    )

    parser.add_argument('--config', required=True,
                        help='Path to configuration JSON file (required)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Perform dry run without making changes')
    parser.add_argument('--limit', type=int,
                        help='Maximum number of items to process (for testing)')
    parser.add_argument('--profile',
                        help='AWS profile name')
    parser.add_argument('--region',
                        help='AWS region')
    parser.add_argument('--log-level', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'], default='INFO',
                        help='Logging level (default: INFO)')

    args = parser.parse_args()

    # Load configuration from file
    config = load_config_from_file(args.config)

    # Command-line arguments override config file
    asset_table_name = config.get('asset_storage_table_name')
    versions_table_name = config.get('asset_versions_storage_table_name')
    batch_size = config.get('batch_size', 25)
    dry_run = args.dry_run or config.get('dry_run', False)
    limit = args.limit or config.get('limit')
    profile = args.profile or config.get('aws_profile')
    region = args.region or config.get('aws_region')
    log_level = args.log_level or config.get('log_level', 'INFO')

    # Validate required parameters
    if not asset_table_name:
        logger.error("Error: asset_storage_table_name is required in config file")
        return 1

    if not versions_table_name:
        logger.error("Error: asset_versions_storage_table_name is required in config file")
        return 1

    # Configure logging
    logging.getLogger().setLevel(getattr(logging, log_level))

    # Initialize AWS session
    session_kwargs = {}
    if profile:
        session_kwargs['profile_name'] = profile
    if region:
        session_kwargs['region_name'] = region

    session = boto3.Session(**session_kwargs)
    dynamodb_client = session.client('dynamodb')

    logger.info("=" * 80)
    logger.info("VAMS v2.4 to v2.5 MIGRATION - Asset Version databaseId Backfill")
    logger.info("=" * 80)
    logger.info(f"Asset Storage Table: {asset_table_name}")
    logger.info(f"Asset Versions Table: {versions_table_name}")
    logger.info(f"Batch Size: {batch_size}")
    logger.info(f"Dry Run: {dry_run}")
    if limit:
        logger.info(f"Limit: {limit} items")
    logger.info("=" * 80)

    # Track migration timing
    migration_start_time = datetime.now(timezone.utc)

    #######################
    # PHASE 1: BUILD LOOKUP CACHE
    #######################

    logger.info("")
    logger.info("=" * 80)
    logger.info("PHASE 1: BUILD ASSET-TO-DATABASE LOOKUP CACHE")
    logger.info("=" * 80)

    try:
        asset_db_cache = build_asset_to_database_cache(dynamodb_client, asset_table_name, limit)
        if not asset_db_cache:
            logger.warning("No assets found in asset storage table. Nothing to migrate.")
            return 0
    except Exception as e:
        logger.error(f"Phase 1 failed: {e}")
        return 1

    #######################
    # PHASE 2: BACKFILL databaseId
    #######################

    logger.info("")
    logger.info("=" * 80)
    logger.info("PHASE 2: BACKFILL databaseId ON ASSET VERSION RECORDS")
    logger.info("=" * 80)

    try:
        version_records = scan_asset_versions(dynamodb_client, versions_table_name, limit)
        if not version_records:
            logger.warning("No asset version records found. Nothing to migrate.")
            return 0

        if dry_run:
            logger.info(f"DRY RUN: Would process {len(version_records)} asset version records")

        updated_count, skipped_count, orphaned_count, error_count = backfill_database_ids(
            dynamodb_client, versions_table_name,
            version_records, asset_db_cache, dry_run
        )

        logger.info("")
        logger.info(f"Backfill complete:")
        logger.info(f"  Updated: {updated_count}")
        logger.info(f"  Skipped (already had databaseId): {skipped_count}")
        logger.info(f"  Orphaned (assetId not in asset table): {orphaned_count}")
        logger.info(f"  Errors: {error_count}")

    except Exception as e:
        logger.error(f"Phase 2 failed: {e}")
        return 1

    #######################
    # PHASE 3: VERIFICATION
    #######################

    logger.info("")
    logger.info("=" * 80)
    logger.info("PHASE 3: VERIFICATION")
    logger.info("=" * 80)

    verification_passed = True
    if not dry_run:
        try:
            verification_passed = verify_migration(dynamodb_client, versions_table_name, asset_db_cache)
        except Exception as e:
            logger.error(f"Verification failed: {e}")
            verification_passed = False
    else:
        logger.info("DRY RUN: Skipping verification (no changes were made)")

    #######################
    # FINAL SUMMARY
    #######################

    migration_end_time = datetime.now(timezone.utc)
    migration_duration = (migration_end_time - migration_start_time).total_seconds()

    logger.info("")
    logger.info("=" * 80)
    logger.info("MIGRATION SUMMARY")
    logger.info("=" * 80)
    logger.info(f"Migration Duration: {migration_duration:.1f} seconds")
    logger.info(f"Dry Run: {dry_run}")
    logger.info("")
    logger.info(f"Asset Lookup Cache: {len(asset_db_cache)} assets")
    logger.info(f"Version Records Processed: {len(version_records)}")
    logger.info(f"  Updated: {updated_count}")
    logger.info(f"  Skipped (already migrated): {skipped_count}")
    logger.info(f"  Orphaned (no matching asset): {orphaned_count}")
    logger.info(f"  Errors: {error_count}")
    logger.info("")

    if verification_passed and error_count == 0:
        logger.info(f"Status: SUCCESS")
        if dry_run:
            logger.info("Note: This was a dry run - no changes were made")
        return 0
    elif error_count > 0:
        logger.warning(f"Status: COMPLETED WITH ERRORS ({error_count} errors)")
        return 1
    elif not verification_passed:
        logger.warning(f"Status: VERIFICATION FAILED")
        return 1
    else:
        logger.info(f"Status: SUCCESS")
        return 0


if __name__ == "__main__":
    sys.exit(main())
