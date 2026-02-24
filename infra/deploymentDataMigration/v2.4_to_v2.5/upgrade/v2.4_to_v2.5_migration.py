#!/usr/bin/env python3
# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Data Migration Script for VAMS v2.4 to v2.5 - Full Table Copy (V1 -> V2)

This script performs a full table copy from V1 DynamoDB tables to V2 tables with new
key schemas. The V2 tables use databaseId-prefixed composite keys for efficient
querying of asset versions and file versions by database via GSIs.

Table Key Schema Changes:
  assetVersionsStorageTable (V1):       PK = assetId,                       SK = assetVersionId
  assetVersionsStorageTableV2 (V2):     PK = databaseId:assetId,            SK = assetVersionId

  assetFileVersionsStorageTable (V1):   PK = assetId:assetVersionId,        SK = fileKey
  assetFileVersionsStorageTableV2 (V2): PK = databaseId:assetId:assetVersionId, SK = fileKey
                                        + databaseId:assetId field (for GSI)

Migration Phases:
1. Build lookup cache: Scan asset storage table, build assetId -> databaseId mapping
2. Migrate asset versions: Scan V1, transform keys, batch_write to V2
3. Migrate asset file versions: Scan V1, transform keys, batch_write to V2
4. Backfill metadata versions: Add databaseId:assetId field to AssetFileMetadataVersionsStorageTable
5. Verify: Count records in V1 and V2 tables, verify V2 counts match expectations

Key Features:
- Idempotent (safe to re-run) - PutItem overwrites existing records in batch_write
- Dry-run mode for safe testing
- Comprehensive error handling and logging
- Continue-on-error for individual batches
- Progress reporting every 100 records
- Batch writes of 25 items for efficiency

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


def clear_table(dynamodb_client, table_name: str, key_schema: List[Dict],
                 dry_run: bool = False) -> int:
    """
    Delete all items from a DynamoDB table.

    Args:
        dynamodb_client: Boto3 DynamoDB client
        table_name: Name of the table to clear
        key_schema: List of key attribute names, e.g. ['databaseId:assetId', 'assetVersionId']
        dry_run: If True, only count items without deleting

    Returns:
        Number of items deleted (or that would be deleted in dry-run)
    """
    logger.info(f"Clearing all items from {table_name}...")

    deleted_count = 0
    scan_kwargs = {'TableName': table_name}

    try:
        while True:
            response = dynamodb_client.scan(**scan_kwargs)
            items = response.get('Items', [])

            for item in items:
                if dry_run:
                    deleted_count += 1
                    continue

                # Build the key from the item using the key schema
                key = {}
                for key_attr in key_schema:
                    if key_attr in item:
                        key[key_attr] = item[key_attr]

                if key:
                    dynamodb_client.delete_item(TableName=table_name, Key=key)
                    deleted_count += 1

                if deleted_count % 100 == 0 and deleted_count > 0:
                    logger.info(f"  Deleted {deleted_count} items from {table_name}...")

            if 'LastEvaluatedKey' not in response:
                break
            scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']

        action = "Would delete" if dry_run else "Deleted"
        logger.info(f"  {action} {deleted_count} items from {table_name}")
        return deleted_count

    except ClientError as e:
        logger.error(f"Error clearing table {table_name}: {e}")
        raise


#######################
# PHASE 2: MIGRATE ASSET VERSIONS (V1 -> V2)
#######################

def scan_all_items(dynamodb_client, table_name: str, limit: int = None) -> List[Dict]:
    """
    Scan all items from a DynamoDB table with pagination support.

    Args:
        dynamodb_client: Boto3 DynamoDB client
        table_name: Name of the DynamoDB table
        limit: Maximum number of records to retrieve (for testing)

    Returns:
        List of all items from the table
    """
    logger.info(f"Scanning {table_name} for all records...")

    records = []
    scan_kwargs = {
        'TableName': table_name
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
                logger.info(f"  Scanned {len(records)} records from {table_name}...")

        if limit and len(records) > limit:
            records = records[:limit]

        logger.info(f"Found {len(records)} records in {table_name}")
        return records

    except ClientError as e:
        logger.error(f"Error scanning table {table_name}: {e}")
        raise


def flush_batch_write(dynamodb_client, table_name: str, batch: List[Dict],
                      dry_run: bool = False) -> Tuple[int, int]:
    """
    Write a batch of PutRequest items to a DynamoDB table using batch_write_item.

    Handles unprocessed items by retrying them.

    Args:
        dynamodb_client: Boto3 DynamoDB client
        table_name: Name of the target DynamoDB table
        batch: List of DynamoDB item dicts (already in DynamoDB wire format)
        dry_run: If True, don't actually write

    Returns:
        Tuple of (written_count, error_count)
    """
    if not batch:
        return 0, 0

    if dry_run:
        return len(batch), 0

    write_requests = [{'PutRequest': {'Item': item}} for item in batch]

    written = 0
    errors = 0

    try:
        response = dynamodb_client.batch_write_item(
            RequestItems={table_name: write_requests}
        )
        written += len(write_requests)

        # Handle unprocessed items with retry
        unprocessed = response.get('UnprocessedItems', {}).get(table_name, [])
        retry_count = 0
        max_retries = 3

        while unprocessed and retry_count < max_retries:
            retry_count += 1
            logger.warning(f"  Retrying {len(unprocessed)} unprocessed items (attempt {retry_count}/{max_retries})")
            response = dynamodb_client.batch_write_item(
                RequestItems={table_name: unprocessed}
            )
            unprocessed = response.get('UnprocessedItems', {}).get(table_name, [])

        if unprocessed:
            errors += len(unprocessed)
            written -= len(unprocessed)
            logger.error(f"  Failed to write {len(unprocessed)} items after {max_retries} retries")

    except ClientError as e:
        logger.error(f"Error in batch_write_item to {table_name}: {e}")
        errors += len(batch)
        written = 0

    return written, errors


def migrate_asset_versions(dynamodb_client, v1_table_name: str, v2_table_name: str,
                           v1_records: List[Dict], asset_db_cache: Dict[str, str],
                           batch_size: int = 25,
                           dry_run: bool = False) -> Tuple[int, int, int, int]:
    """
    Migrate asset version records from V1 table to V2 table with new key schema.

    V1 key: PK = assetId, SK = assetVersionId
    V2 key: PK = databaseId:assetId, SK = assetVersionId

    Copies all existing attributes and adds databaseId, databaseId:assetId, and assetId fields.

    Args:
        dynamodb_client: Boto3 DynamoDB client
        v1_table_name: Name of the V1 AssetVersionsStorageTable
        v2_table_name: Name of the V2 AssetVersionsStorageTableV2
        v1_records: List of all V1 items (DynamoDB wire format)
        asset_db_cache: Mapping of assetId -> databaseId
        batch_size: Number of items per batch_write (max 25)
        dry_run: If True, don't actually write

    Returns:
        Tuple of (migrated_count, skipped_count, orphaned_count, error_count)
    """
    migrated_count = 0
    skipped_count = 0
    orphaned_count = 0
    error_count = 0
    total = len(v1_records)

    logger.info(f"Migrating {total} asset version records from {v1_table_name} to {v2_table_name}...")

    batch = []

    for idx, record in enumerate(v1_records, 1):
        asset_id = record.get('assetId', {}).get('S', '')
        asset_version_id = record.get('assetVersionId', {}).get('S', '')

        if not asset_id or not asset_version_id:
            logger.warning(f"Record {idx}: Missing assetId or assetVersionId, skipping")
            error_count += 1
            continue

        # Look up databaseId from cache
        database_id = asset_db_cache.get(asset_id)
        if not database_id:
            orphaned_count += 1
            logger.warning(f"Record {idx}: assetId={asset_id} not found in asset table (orphaned version), skipping")
            continue

        # Build V2 item: copy all attributes from V1
        v2_item = dict(record)

        # Set the new composite PK: databaseId:assetId
        composite_pk = f"{database_id}:{asset_id}"
        v2_item['databaseId:assetId'] = {'S': composite_pk}

        # Remove old PK field (assetId was the V1 PK, it stays as a regular attribute)
        # assetId remains as-is since the V2 schema still needs it as an attribute

        # Ensure databaseId field exists
        v2_item['databaseId'] = {'S': database_id}

        # Ensure assetId field exists (it should already, but be explicit)
        v2_item['assetId'] = {'S': asset_id}

        # assetVersionId stays as the SK (same in V1 and V2)

        batch.append(v2_item)

        # Flush batch when it reaches batch_size
        if len(batch) >= batch_size:
            written, errs = flush_batch_write(dynamodb_client, v2_table_name, batch, dry_run)
            migrated_count += written
            error_count += errs
            batch = []

        # Progress reporting
        if idx % 100 == 0:
            logger.info(f"  Progress: {idx}/{total} records processed "
                        f"(migrated={migrated_count}, orphaned={orphaned_count}, "
                        f"errors={error_count})")

    # Flush remaining items
    if batch:
        written, errs = flush_batch_write(dynamodb_client, v2_table_name, batch, dry_run)
        migrated_count += written
        error_count += errs

    return migrated_count, skipped_count, orphaned_count, error_count


#######################
# PHASE 3: MIGRATE ASSET FILE VERSIONS (V1 -> V2)
#######################

def extract_asset_and_version_id_from_file_version_pk(pk_value: str, asset_db_cache: Dict[str, str]) -> Tuple[str, str]:
    """
    Extract the assetId and assetVersionId from an AssetFileVersionsStorageTable partition key.

    The PK format is "{assetId}:{assetVersionId}" where assetVersionId is a simple
    value like "1", "2", "v1". We split from the right on the last ":" to safely
    handle assetIds that might contain special characters.

    Args:
        pk_value: The partition key value (e.g., "xd130a6d6...:1")
        asset_db_cache: Mapping of assetId -> databaseId for validation

    Returns:
        Tuple of (assetId, assetVersionId). Both empty strings if PK cannot be parsed.
    """
    # Split from the right on the last ":" since assetVersionId is the last segment
    last_colon_idx = pk_value.rfind(':')
    if last_colon_idx <= 0:
        logger.warning(f"Cannot parse PK value (no colon found): {pk_value}")
        return '', ''

    candidate_asset_id = pk_value[:last_colon_idx]
    candidate_version_id = pk_value[last_colon_idx + 1:]

    # Validate that the extracted assetId exists in our cache
    if candidate_asset_id not in asset_db_cache:
        # If not found, log a warning -- this could be an orphaned file version
        logger.debug(f"Extracted assetId '{candidate_asset_id}' from PK '{pk_value}' not found in asset cache")

    return candidate_asset_id, candidate_version_id


def migrate_asset_file_versions(dynamodb_client, v1_table_name: str, v2_table_name: str,
                                v1_records: List[Dict], asset_db_cache: Dict[str, str],
                                batch_size: int = 25,
                                dry_run: bool = False) -> Tuple[int, int, int, int]:
    """
    Migrate asset file version records from V1 table to V2 table with new key schema.

    V1 key: PK = assetId:assetVersionId, SK = fileKey
    V2 key: PK = databaseId:assetId:assetVersionId, SK = fileKey
            + databaseId:assetId field (for GSI)

    Copies all existing attributes, transforms the PK, adds databaseId:assetId,
    and removes the old assetId:assetVersionId field.

    Args:
        dynamodb_client: Boto3 DynamoDB client
        v1_table_name: Name of the V1 AssetFileVersionsStorageTable
        v2_table_name: Name of the V2 AssetFileVersionsStorageTableV2
        v1_records: List of all V1 items (DynamoDB wire format)
        asset_db_cache: Mapping of assetId -> databaseId
        batch_size: Number of items per batch_write (max 25)
        dry_run: If True, don't actually write

    Returns:
        Tuple of (migrated_count, skipped_count, orphaned_count, error_count)
    """
    migrated_count = 0
    skipped_count = 0
    orphaned_count = 0
    error_count = 0
    total = len(v1_records)

    logger.info(f"Migrating {total} asset file version records from {v1_table_name} to {v2_table_name}...")

    batch = []

    for idx, record in enumerate(v1_records, 1):
        pk_value = record.get('assetId:assetVersionId', {}).get('S', '')
        sk_value = record.get('fileKey', {}).get('S', '')

        if not pk_value or not sk_value:
            logger.warning(f"Record {idx}: Missing assetId:assetVersionId or fileKey, skipping")
            error_count += 1
            continue

        # Extract assetId and assetVersionId from the composite PK
        asset_id, asset_version_id = extract_asset_and_version_id_from_file_version_pk(pk_value, asset_db_cache)
        if not asset_id or not asset_version_id:
            error_count += 1
            logger.warning(f"Record {idx}: Could not extract assetId/assetVersionId from PK={pk_value}")
            continue

        # Look up databaseId from cache
        database_id = asset_db_cache.get(asset_id)
        if not database_id:
            orphaned_count += 1
            logger.warning(f"Record {idx}: assetId={asset_id} (from PK={pk_value}) not found in asset table (orphaned file version), skipping")
            continue

        # Build V2 item: copy all attributes from V1
        v2_item = dict(record)

        # Remove the old V1 PK field (assetId:assetVersionId)
        if 'assetId:assetVersionId' in v2_item:
            del v2_item['assetId:assetVersionId']

        # Set the new composite PK: databaseId:assetId:assetVersionId
        new_pk = f"{database_id}:{asset_id}:{asset_version_id}"
        v2_item['databaseId:assetId:assetVersionId'] = {'S': new_pk}

        # Add databaseId:assetId field (for the GSI)
        v2_item['databaseId:assetId'] = {'S': f"{database_id}:{asset_id}"}

        # fileKey (SK) stays the same

        batch.append(v2_item)

        # Flush batch when it reaches batch_size
        if len(batch) >= batch_size:
            written, errs = flush_batch_write(dynamodb_client, v2_table_name, batch, dry_run)
            migrated_count += written
            error_count += errs
            batch = []

        # Progress reporting
        if idx % 100 == 0:
            logger.info(f"  Progress: {idx}/{total} file version records processed "
                        f"(migrated={migrated_count}, orphaned={orphaned_count}, "
                        f"errors={error_count})")

    # Flush remaining items
    if batch:
        written, errs = flush_batch_write(dynamodb_client, v2_table_name, batch, dry_run)
        migrated_count += written
        error_count += errs

    return migrated_count, skipped_count, orphaned_count, error_count


#######################
# PHASE 4: BACKFILL databaseId:assetId ON METADATA VERSIONS
#######################

def backfill_metadata_versions_database_asset_id(dynamodb_client, table_name: str,
                                                   records: List[Dict],
                                                   dry_run: bool = False) -> Tuple[int, int, int]:
    """
    Backfill databaseId:assetId on AssetFileMetadataVersionsStorageTable records.

    The table PK is databaseId:assetId:assetVersionId, so we can derive databaseId:assetId
    by stripping the last :assetVersionId segment from the PK. No cache lookup needed.

    Args:
        dynamodb_client: Boto3 DynamoDB client
        table_name: Name of the AssetFileMetadataVersionsStorageTable
        records: List of all items (DynamoDB wire format)
        dry_run: If True, don't actually write

    Returns:
        Tuple of (updated_count, skipped_count, error_count)
    """
    updated_count = 0
    skipped_count = 0
    error_count = 0
    total = len(records)

    logger.info(f"Backfilling databaseId:assetId on {total} metadata version records...")

    for idx, record in enumerate(records, 1):
        pk_value = record.get('databaseId:assetId:assetVersionId', {}).get('S', '')
        sk_value = record.get('type:filePath:metadataKey', {}).get('S', '')
        existing_field = record.get('databaseId:assetId', {}).get('S', '')

        if not pk_value or not sk_value:
            logger.warning(f"Record {idx}: Missing PK or SK, skipping")
            error_count += 1
            continue

        # Skip records that already have databaseId:assetId (idempotent)
        if existing_field:
            skipped_count += 1
            continue

        # Derive databaseId:assetId from the PK (databaseId:assetId:assetVersionId)
        # Split from the right on the last ":" to strip assetVersionId
        last_colon_idx = pk_value.rfind(':')
        if last_colon_idx <= 0:
            logger.warning(f"Record {idx}: Cannot parse PK '{pk_value}', skipping")
            error_count += 1
            continue

        database_asset_key = pk_value[:last_colon_idx]

        if dry_run:
            updated_count += 1
            continue

        try:
            dynamodb_client.update_item(
                TableName=table_name,
                Key={
                    'databaseId:assetId:assetVersionId': {'S': pk_value},
                    'type:filePath:metadataKey': {'S': sk_value}
                },
                UpdateExpression='SET #gsiKey = :gsiVal',
                ExpressionAttributeNames={
                    '#gsiKey': 'databaseId:assetId'
                },
                ExpressionAttributeValues={
                    ':gsiVal': {'S': database_asset_key}
                }
            )
            updated_count += 1
        except ClientError as e:
            logger.error(f"Error updating metadata version record {idx}: {e}")
            error_count += 1

        # Progress reporting
        if idx % 100 == 0:
            logger.info(f"  Progress: {idx}/{total} metadata version records processed "
                        f"(updated={updated_count}, skipped={skipped_count}, errors={error_count})")

    return updated_count, skipped_count, error_count


def verify_metadata_versions_backfill(dynamodb_client, table_name: str) -> bool:
    """
    Verify that all records in AssetFileMetadataVersionsStorageTable have databaseId:assetId.

    Args:
        dynamodb_client: Boto3 DynamoDB client
        table_name: Name of the table

    Returns:
        True if verification passed, False otherwise
    """
    logger.info(f"Verifying metadata versions backfill on {table_name}...")

    total_count = 0
    with_field_count = 0
    without_field_count = 0

    scan_kwargs = {'TableName': table_name}

    try:
        response = dynamodb_client.scan(**scan_kwargs)
        while True:
            for item in response.get('Items', []):
                total_count += 1
                has_field = 'databaseId:assetId' in item and item.get('databaseId:assetId', {}).get('S', '')
                if has_field:
                    with_field_count += 1
                else:
                    without_field_count += 1

            if 'LastEvaluatedKey' not in response:
                break
            scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
            response = dynamodb_client.scan(**scan_kwargs)

        logger.info(f"  Total records: {total_count}")
        logger.info(f"  Records with databaseId:assetId: {with_field_count}")
        logger.info(f"  Records without databaseId:assetId: {without_field_count}")

        if without_field_count == 0:
            logger.info(f"  Metadata versions backfill verification PASSED")
            return True
        else:
            logger.warning(f"  Metadata versions backfill verification FAILED - {without_field_count} records missing field")
            return False

    except ClientError as e:
        logger.error(f"Error during metadata versions verification: {e}")
        return False


#######################
# PHASE 5: VERIFICATION
#######################

def count_table_items(dynamodb_client, table_name: str) -> int:
    """
    Count the total number of items in a DynamoDB table.

    Args:
        dynamodb_client: Boto3 DynamoDB client
        table_name: Name of the DynamoDB table

    Returns:
        Total item count
    """
    count = 0
    scan_kwargs = {
        'TableName': table_name,
        'Select': 'COUNT'
    }

    try:
        response = dynamodb_client.scan(**scan_kwargs)
        count += response.get('Count', 0)

        while 'LastEvaluatedKey' in response:
            scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
            response = dynamodb_client.scan(**scan_kwargs)
            count += response.get('Count', 0)

        return count

    except ClientError as e:
        logger.error(f"Error counting items in {table_name}: {e}")
        raise


def verify_asset_versions_migration(dynamodb_client,
                                     v1_table_name: str, v2_table_name: str,
                                     orphaned_count: int) -> bool:
    """
    Verify asset versions migration by comparing V1 and V2 table counts.

    V2 count should equal V1 count minus orphaned records.

    Args:
        dynamodb_client: Boto3 DynamoDB client
        v1_table_name: Name of the V1 AssetVersionsStorageTable
        v2_table_name: Name of the V2 AssetVersionsStorageTableV2
        orphaned_count: Number of orphaned records skipped during migration

    Returns:
        True if verification passed, False otherwise
    """
    logger.info(f"Verifying asset versions migration...")
    logger.info(f"  V1 table: {v1_table_name}")
    logger.info(f"  V2 table: {v2_table_name}")

    try:
        v1_count = count_table_items(dynamodb_client, v1_table_name)
        v2_count = count_table_items(dynamodb_client, v2_table_name)
        expected_v2_count = v1_count - orphaned_count

        logger.info(f"  V1 record count: {v1_count}")
        logger.info(f"  V2 record count: {v2_count}")
        logger.info(f"  Orphaned records skipped: {orphaned_count}")
        logger.info(f"  Expected V2 count (V1 - orphans): {expected_v2_count}")

        # Verify V2 count >= expected (>= because V2 may already have records from a prior run)
        if v2_count >= expected_v2_count:
            logger.info(f"  Asset versions verification PASSED (V2 has {v2_count} records, expected >= {expected_v2_count})")
            return True
        else:
            logger.warning(f"  Asset versions verification FAILED (V2 has {v2_count} records, expected >= {expected_v2_count})")
            return False

    except Exception as e:
        logger.error(f"Error during asset versions verification: {e}")
        return False


def verify_asset_file_versions_migration(dynamodb_client,
                                          v1_table_name: str, v2_table_name: str,
                                          orphaned_count: int) -> bool:
    """
    Verify asset file versions migration by comparing V1 and V2 table counts.

    V2 count should equal V1 count minus orphaned records.

    Args:
        dynamodb_client: Boto3 DynamoDB client
        v1_table_name: Name of the V1 AssetFileVersionsStorageTable
        v2_table_name: Name of the V2 AssetFileVersionsStorageTableV2
        orphaned_count: Number of orphaned records skipped during migration

    Returns:
        True if verification passed, False otherwise
    """
    logger.info(f"Verifying asset file versions migration...")
    logger.info(f"  V1 table: {v1_table_name}")
    logger.info(f"  V2 table: {v2_table_name}")

    try:
        v1_count = count_table_items(dynamodb_client, v1_table_name)
        v2_count = count_table_items(dynamodb_client, v2_table_name)
        expected_v2_count = v1_count - orphaned_count

        logger.info(f"  V1 record count: {v1_count}")
        logger.info(f"  V2 record count: {v2_count}")
        logger.info(f"  Orphaned records skipped: {orphaned_count}")
        logger.info(f"  Expected V2 count (V1 - orphans): {expected_v2_count}")

        # Verify V2 count >= expected (>= because V2 may already have records from a prior run)
        if v2_count >= expected_v2_count:
            logger.info(f"  Asset file versions verification PASSED (V2 has {v2_count} records, expected >= {expected_v2_count})")
            return True
        else:
            logger.warning(f"  Asset file versions verification FAILED (V2 has {v2_count} records, expected >= {expected_v2_count})")
            return False

    except Exception as e:
        logger.error(f"Error during asset file versions verification: {e}")
        return False


def verify_v2_key_structure(dynamodb_client, v2_table_name: str,
                             pk_field: str, sample_size: int = 10) -> bool:
    """
    Spot-check V2 table records to verify they have the proper key structure.

    Args:
        dynamodb_client: Boto3 DynamoDB client
        v2_table_name: Name of the V2 table
        pk_field: Name of the PK field to check (e.g., 'databaseId:assetId')
        sample_size: Number of records to sample

    Returns:
        True if all sampled records have valid key structure, False otherwise
    """
    logger.info(f"  Spot-checking V2 key structure in {v2_table_name} (PK field: {pk_field})...")

    try:
        response = dynamodb_client.scan(
            TableName=v2_table_name,
            Limit=sample_size
        )

        items = response.get('Items', [])
        if not items:
            logger.info(f"  No items in V2 table to verify")
            return True

        invalid_count = 0
        for item in items:
            pk_value = item.get(pk_field, {}).get('S', '')
            if not pk_value or ':' not in pk_value:
                invalid_count += 1
                logger.warning(f"  Invalid key structure: {pk_field}='{pk_value}'")

        if invalid_count == 0:
            logger.info(f"  Key structure check PASSED ({len(items)} records sampled, all have valid '{pk_field}')")
            return True
        else:
            logger.warning(f"  Key structure check FAILED ({invalid_count}/{len(items)} records have invalid '{pk_field}')")
            return False

    except ClientError as e:
        logger.error(f"Error during key structure verification: {e}")
        return False


#######################
# MAIN MIGRATION FUNCTION
#######################

def main():
    """Main function to run the migration."""
    parser = argparse.ArgumentParser(
        description='VAMS v2.4 to v2.5 Migration Script - Full Table Copy (V1 -> V2)',
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
  - Table names must be provided in config file (V1 and V2 for both tables)
  - Dry-run mode is recommended for initial testing
  - Migration is idempotent - PutItem overwrites existing V2 records
  - No V1 data is deleted - this is a copy operation
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
    parser.add_argument('--clear-v2', action='store_true',
                        help='Clear all data in V2 destination tables before migrating (use with caution)')

    args = parser.parse_args()

    # Load configuration from file
    config = load_config_from_file(args.config)

    # Command-line arguments override config file
    asset_table_name = config.get('asset_storage_table_name')
    versions_v1_table_name = config.get('asset_versions_storage_table_name_v1')
    versions_v2_table_name = config.get('asset_versions_storage_table_name_v2')
    file_versions_v1_table_name = config.get('asset_file_versions_storage_table_name_v1')
    file_versions_v2_table_name = config.get('asset_file_versions_storage_table_name_v2')
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

    if not versions_v1_table_name:
        logger.error("Error: asset_versions_storage_table_name_v1 is required in config file")
        return 1

    if not versions_v2_table_name:
        logger.error("Error: asset_versions_storage_table_name_v2 is required in config file")
        return 1

    if not file_versions_v1_table_name:
        logger.error("Error: asset_file_versions_storage_table_name_v1 is required in config file")
        return 1

    if not file_versions_v2_table_name:
        logger.error("Error: asset_file_versions_storage_table_name_v2 is required in config file")
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
    logger.info("VAMS v2.4 to v2.5 MIGRATION - Full Table Copy (V1 -> V2)")
    logger.info("=" * 80)
    logger.info(f"Asset Storage Table (lookup): {asset_table_name}")
    logger.info(f"Asset Versions V1 Table:      {versions_v1_table_name}")
    logger.info(f"Asset Versions V2 Table:      {versions_v2_table_name}")
    logger.info(f"File Versions V1 Table:       {file_versions_v1_table_name}")
    logger.info(f"File Versions V2 Table:       {file_versions_v2_table_name}")
    logger.info(f"Batch Size: {batch_size}")
    logger.info(f"Dry Run: {dry_run}")
    if limit:
        logger.info(f"Limit: {limit} items")
    logger.info("=" * 80)

    # Track migration timing
    migration_start_time = datetime.now(timezone.utc)

    # Optional: Clear V2 tables before migrating
    clear_v2 = args.clear_v2
    if clear_v2:
        logger.info("")
        logger.info("=" * 80)
        logger.info("PRE-MIGRATION: CLEARING V2 DESTINATION TABLES")
        logger.info("=" * 80)

        try:
            clear_table(dynamodb_client, versions_v2_table_name,
                        ['databaseId:assetId', 'assetVersionId'], dry_run)
            clear_table(dynamodb_client, file_versions_v2_table_name,
                        ['databaseId:assetId:assetVersionId', 'fileKey'], dry_run)
        except Exception as e:
            logger.error(f"Failed to clear V2 tables: {e}")
            return 1

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
    # PHASE 2: MIGRATE ASSET VERSIONS (V1 -> V2)
    #######################

    logger.info("")
    logger.info("=" * 80)
    logger.info("PHASE 2: MIGRATE ASSET VERSIONS (V1 -> V2)")
    logger.info("=" * 80)

    av_migrated_count = 0
    av_skipped_count = 0
    av_orphaned_count = 0
    av_error_count = 0
    v1_version_records = []

    try:
        v1_version_records = scan_all_items(dynamodb_client, versions_v1_table_name, limit)
        if not v1_version_records:
            logger.warning("No asset version records found in V1 table. Nothing to migrate for asset versions.")
        else:
            if dry_run:
                logger.info(f"DRY RUN: Would migrate {len(v1_version_records)} asset version records to V2")

            av_migrated_count, av_skipped_count, av_orphaned_count, av_error_count = migrate_asset_versions(
                dynamodb_client, versions_v1_table_name, versions_v2_table_name,
                v1_version_records, asset_db_cache, batch_size, dry_run
            )

            logger.info("")
            logger.info(f"Asset versions migration complete:")
            logger.info(f"  Migrated to V2: {av_migrated_count}")
            logger.info(f"  Orphaned (assetId not in asset table): {av_orphaned_count}")
            logger.info(f"  Errors: {av_error_count}")

    except Exception as e:
        logger.error(f"Phase 2 failed: {e}")
        return 1

    #######################
    # PHASE 3: MIGRATE ASSET FILE VERSIONS (V1 -> V2)
    #######################

    logger.info("")
    logger.info("=" * 80)
    logger.info("PHASE 3: MIGRATE ASSET FILE VERSIONS (V1 -> V2)")
    logger.info("=" * 80)

    fv_migrated_count = 0
    fv_skipped_count = 0
    fv_orphaned_count = 0
    fv_error_count = 0
    v1_file_version_records = []

    try:
        v1_file_version_records = scan_all_items(dynamodb_client, file_versions_v1_table_name, limit)
        if not v1_file_version_records:
            logger.warning("No asset file version records found in V1 table. Nothing to migrate for file versions.")
        else:
            if dry_run:
                logger.info(f"DRY RUN: Would migrate {len(v1_file_version_records)} asset file version records to V2")

            fv_migrated_count, fv_skipped_count, fv_orphaned_count, fv_error_count = migrate_asset_file_versions(
                dynamodb_client, file_versions_v1_table_name, file_versions_v2_table_name,
                v1_file_version_records, asset_db_cache, batch_size, dry_run
            )

            logger.info("")
            logger.info(f"File versions migration complete:")
            logger.info(f"  Migrated to V2: {fv_migrated_count}")
            logger.info(f"  Orphaned (assetId not in asset table): {fv_orphaned_count}")
            logger.info(f"  Errors: {fv_error_count}")

    except Exception as e:
        logger.error(f"Phase 3 failed: {e}")
        return 1

    #######################
    # PHASE 4: BACKFILL databaseId:assetId ON METADATA VERSIONS
    #######################

    logger.info("")
    logger.info("=" * 80)
    logger.info("PHASE 4: BACKFILL databaseId:assetId ON METADATA VERSION RECORDS")
    logger.info("=" * 80)

    mv_updated_count = 0
    mv_skipped_count = 0
    mv_error_count = 0
    metadata_versions_table_name = config.get('asset_file_metadata_versions_storage_table_name')

    if metadata_versions_table_name:
        try:
            mv_records = scan_all_items(dynamodb_client, metadata_versions_table_name, limit)
            if not mv_records:
                logger.warning("No metadata version records found. Nothing to backfill.")
            else:
                if dry_run:
                    logger.info(f"DRY RUN: Would backfill databaseId:assetId on {len(mv_records)} metadata version records")

                mv_updated_count, mv_skipped_count, mv_error_count = backfill_metadata_versions_database_asset_id(
                    dynamodb_client, metadata_versions_table_name, mv_records, dry_run
                )

                logger.info("")
                logger.info(f"Metadata versions backfill complete:")
                logger.info(f"  Updated: {mv_updated_count}")
                logger.info(f"  Skipped (already had field): {mv_skipped_count}")
                logger.info(f"  Errors: {mv_error_count}")

        except Exception as e:
            logger.error(f"Phase 4 failed: {e}")
            return 1
    else:
        logger.warning("asset_file_metadata_versions_storage_table_name not in config, skipping Phase 4")

    #######################
    # PHASE 5: VERIFICATION
    #######################

    logger.info("")
    logger.info("=" * 80)
    logger.info("PHASE 5: VERIFICATION")
    logger.info("=" * 80)

    verification_passed = True
    if not dry_run:
        try:
            logger.info("--- Verifying Asset Versions (V1 -> V2) ---")
            av_verification = verify_asset_versions_migration(
                dynamodb_client, versions_v1_table_name, versions_v2_table_name, av_orphaned_count
            )
            av_key_check = verify_v2_key_structure(
                dynamodb_client, versions_v2_table_name, 'databaseId:assetId'
            )

            logger.info("")
            logger.info("--- Verifying Asset File Versions (V1 -> V2) ---")
            fv_verification = verify_asset_file_versions_migration(
                dynamodb_client, file_versions_v1_table_name, file_versions_v2_table_name, fv_orphaned_count
            )
            fv_key_check = verify_v2_key_structure(
                dynamodb_client, file_versions_v2_table_name, 'databaseId:assetId:assetVersionId'
            )

            # Verify metadata versions backfill (if table was configured)
            mv_verification = True
            if metadata_versions_table_name:
                logger.info("")
                logger.info("--- Verifying Metadata Versions Backfill ---")
                mv_verification = verify_metadata_versions_backfill(
                    dynamodb_client, metadata_versions_table_name
                )

            verification_passed = av_verification and av_key_check and fv_verification and fv_key_check and mv_verification
        except Exception as e:
            logger.error(f"Verification failed: {e}")
            verification_passed = False
    else:
        logger.info("DRY RUN: Skipping verification (no changes were made)")

    #######################
    # FINAL SUMMARY
    #######################

    total_error_count = av_error_count + fv_error_count + mv_error_count
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
    logger.info("")
    logger.info(f"Asset Versions (V1 -> V2):")
    logger.info(f"  V1 Records Scanned: {len(v1_version_records)}")
    logger.info(f"  Migrated to V2: {av_migrated_count}")
    logger.info(f"  Orphaned (no matching asset): {av_orphaned_count}")
    logger.info(f"  Errors: {av_error_count}")
    logger.info("")
    logger.info(f"Asset File Versions (V1 -> V2):")
    logger.info(f"  V1 Records Scanned: {len(v1_file_version_records)}")
    logger.info(f"  Migrated to V2: {fv_migrated_count}")
    logger.info(f"  Orphaned (no matching asset): {fv_orphaned_count}")
    logger.info(f"  Errors: {fv_error_count}")
    logger.info("")
    logger.info(f"Metadata Versions (backfill databaseId:assetId):")
    logger.info(f"  Updated: {mv_updated_count}")
    logger.info(f"  Skipped (already had field): {mv_skipped_count}")
    logger.info(f"  Errors: {mv_error_count}")
    logger.info("")

    if verification_passed and total_error_count == 0:
        logger.info(f"Status: SUCCESS")
        if dry_run:
            logger.info("Note: This was a dry run - no changes were made")
        return 0
    elif total_error_count > 0:
        logger.warning(f"Status: COMPLETED WITH ERRORS ({total_error_count} total errors)")
        return 1
    elif not verification_passed:
        logger.warning(f"Status: VERIFICATION FAILED")
        return 1
    else:
        logger.info(f"Status: SUCCESS")
        return 0


if __name__ == "__main__":
    sys.exit(main())
