#!/usr/bin/env python3
# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Data Migration Script for VAMS v2.3 to v2.4 - Constraints Table Migration

This script migrates constraints from the old AuthEntitiesTable to the new
optimized ConstraintsStorageTable with GSI support for improved performance.

Key Features:
- Migrates constraints from AuthEntitiesTable to ConstraintsStorageTable
- Transforms old schema (entityType/sk) to new schema (simple primary key)
- Converts arrays to JSON strings for storage efficiency
- Extracts StringSets for GSI query optimization
- Optional deletion of old constraint data
- Dry-run mode for safe testing
- Comprehensive error handling and logging

Usage:
    # Get table names from CDK outputs or AWS Console
    
    # Dry run (recommended first step)
    python v2.3_to_v2.4_migration.py \
        --auth-table vams-AuthEntitiesTable \
        --constraints-table vams-ConstraintsStorageTable \
        --dry-run
    
    # Test with limited items
    python v2.3_to_v2.4_migration.py \
        --auth-table vams-AuthEntitiesTable \
        --constraints-table vams-ConstraintsStorageTable \
        --limit 10 \
        --dry-run
    
    # Production migration (keeps old data)
    python v2.3_to_v2.4_migration.py \
        --auth-table vams-AuthEntitiesTable \
        --constraints-table vams-ConstraintsStorageTable
    
    # Migration with cleanup (deletes old data)
    python v2.3_to_v2.4_migration.py \
        --auth-table vams-AuthEntitiesTable \
        --constraints-table vams-ConstraintsStorageTable \
        --delete-old-data

Requirements:
    - Python 3.6+
    - boto3
    - AWS credentials with DynamoDB read/write permissions
"""

import argparse
import boto3
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Dict, List, Any, Tuple
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


def scan_old_constraints(dynamodb_client, auth_table_name: str, limit: int = None) -> List[Dict]:
    """
    Scan AuthEntitiesTable for all constraint entities.
    
    Args:
        dynamodb_client: Boto3 DynamoDB client
        auth_table_name: Name of the AuthEntitiesTable
        limit: Maximum number of constraints to retrieve (for testing)
        
    Returns:
        List of constraint items from old table
    """
    logger.info(f"Scanning {auth_table_name} for constraints...")
    
    constraints = []
    scan_kwargs = {
        'TableName': auth_table_name,
        'FilterExpression': 'entityType = :entityType AND begins_with(sk, :skPrefix)',
        'ExpressionAttributeValues': {
            ':entityType': {'S': 'constraint'},
            ':skPrefix': {'S': 'constraint#'}
        }
    }
    
    if limit:
        scan_kwargs['Limit'] = limit
    
    try:
        response = dynamodb_client.scan(**scan_kwargs)
        constraints.extend(response.get('Items', []))
        
        # Handle pagination
        while 'LastEvaluatedKey' in response and (not limit or len(constraints) < limit):
            scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
            if limit:
                scan_kwargs['Limit'] = limit - len(constraints)
            response = dynamodb_client.scan(**scan_kwargs)
            constraints.extend(response.get('Items', []))
        
        logger.info(f"Found {len(constraints)} constraints in old table")
        return constraints
        
    except ClientError as e:
        logger.error(f"Error scanning old table: {e}")
        raise


def extract_string_value(dynamo_value: Dict) -> str:
    """Extract string value from DynamoDB format."""
    if 'S' in dynamo_value:
        return dynamo_value['S']
    return ''


def extract_list_value(dynamo_value: Dict) -> List:
    """Extract list value from DynamoDB format."""
    if 'L' in dynamo_value:
        result = []
        for item in dynamo_value['L']:
            if 'M' in item:
                # Convert map to dict
                obj = {}
                for k, v in item['M'].items():
                    if 'S' in v:
                        obj[k] = v['S']
                    elif 'N' in v:
                        obj[k] = v['N']
                    elif 'BOOL' in v:
                        obj[k] = v['BOOL']
                result.append(obj)
        return result
    return []


def transform_constraint_to_new_format(old_constraint: Dict) -> List[Dict]:
    """
    Transform constraint from old schema to new denormalized schema.
    Creates one item per unique groupId/userId for efficient GSI queries.
    
    Old schema: entityType, sk, arrays
    New schema: denormalized items with simple PK, JSON strings, groupId/userId for GSI
    
    Args:
        old_constraint: Constraint item in old DynamoDB format
        
    Returns:
        List of constraint items in new denormalized DynamoDB format
    """
    items = []
    
    # Extract values from old format
    base_constraint_id = extract_string_value(old_constraint.get('constraintId', {}))
    name = extract_string_value(old_constraint.get('name', {}))
    description = extract_string_value(old_constraint.get('description', {}))
    object_type = extract_string_value(old_constraint.get('objectType', {}))
    
    criteria_and = extract_list_value(old_constraint.get('criteriaAnd', {}))
    criteria_or = extract_list_value(old_constraint.get('criteriaOr', {}))
    group_permissions = extract_list_value(old_constraint.get('groupPermissions', {}))
    user_permissions = extract_list_value(old_constraint.get('userPermissions', {}))
    
    # Base item data shared across all denormalized items
    base_item = {
        'name': {'S': name},
        'description': {'S': description},
        'objectType': {'S': object_type},
        # Convert to JSON strings
        'criteriaAnd': {'S': json.dumps(criteria_and)},
        'criteriaOr': {'S': json.dumps(criteria_or)},
        'groupPermissions': {'S': json.dumps(group_permissions)},
        'userPermissions': {'S': json.dumps(user_permissions)},
        # Add metadata
        'dateCreated': {'S': datetime.now(timezone.utc).isoformat()},
        'dateModified': {'S': datetime.now(timezone.utc).isoformat()},
        'createdBy': {'S': 'MIGRATION'},
        'modifiedBy': {'S': 'MIGRATION'}
    }
    
    # Create one item per UNIQUE groupId for GSI efficiency
    unique_group_ids = set([perm.get('groupId') for perm in group_permissions if perm.get('groupId')])
    for group_id in unique_group_ids:
        item = base_item.copy()
        item['constraintId'] = {'S': f"{base_constraint_id}#group#{group_id}"}
        item['groupId'] = {'S': group_id}  # For GroupPermissionsIndex GSI
        items.append(item)
    
    # Create one item per UNIQUE userId for GSI efficiency
    unique_user_ids = set([perm.get('userId') for perm in user_permissions if perm.get('userId')])
    for user_id in unique_user_ids:
        item = base_item.copy()
        item['constraintId'] = {'S': f"{base_constraint_id}#user#{user_id}"}
        item['userId'] = {'S': user_id}  # For UserPermissionsIndex GSI
        items.append(item)
    
    # Safety: If no permissions exist, create one base item
    if len(items) == 0:
        item = base_item.copy()
        item['constraintId'] = {'S': base_constraint_id}
        items.append(item)
    
    return items


def check_constraint_exists(dynamodb_client, constraints_table_name: str, base_constraint_id: str) -> bool:
    """
    Check if a constraint already exists in the new denormalized table.
    Scans for any item with the base constraintId (may have #group# or #user# suffix).
    
    Args:
        dynamodb_client: Boto3 DynamoDB client
        constraints_table_name: Name of the ConstraintsStorageTable
        base_constraint_id: The base constraint ID to check
        
    Returns:
        True if constraint exists, False otherwise
    """
    try:
        # Scan for items that start with the base constraintId
        response = dynamodb_client.scan(
            TableName=constraints_table_name,
            FilterExpression='begins_with(constraintId, :base_id)',
            ExpressionAttributeValues={':base_id': {'S': base_constraint_id}},
            Limit=1
        )
        return response.get('Count', 0) > 0
    except ClientError as e:
        logger.warning(f"Error checking if constraint {base_constraint_id} exists: {e}")
        return False


def batch_write_constraints(dynamodb_client, constraints_table_name: str, 
                            constraints: List[List[Dict]], dry_run: bool = False) -> Tuple[int, int, int]:
    """
    Batch write denormalized constraints to new table, skipping existing records.
    
    Args:
        dynamodb_client: Boto3 DynamoDB client
        constraints_table_name: Name of the ConstraintsStorageTable
        constraints: List of lists - each inner list contains denormalized items for one constraint
        dry_run: If True, don't actually write
        
    Returns:
        Tuple of (success_count, failure_count, skipped_count)
    """
    # Flatten the list of lists into a single list of items
    all_items = []
    for constraint_items in constraints:
        all_items.extend(constraint_items)
    
    if dry_run:
        logger.info(f"DRY RUN: Would write {len(all_items)} denormalized items ({len(constraints)} unique constraints) to {constraints_table_name}")
        return len(constraints), 0, 0
    
    logger.info(f"Writing {len(all_items)} denormalized items ({len(constraints)} unique constraints) to {constraints_table_name}...")
    logger.info("Checking for existing records to avoid duplicates...")
    
    # Filter out existing constraints by checking base constraintId
    constraints_to_write = []
    skipped_count = 0
    checked_base_ids = set()
    
    for constraint_items in constraints:
        # Extract base constraintId from first item
        first_item_id = constraint_items[0]['constraintId']['S']
        base_constraint_id = first_item_id.split('#group#')[0].split('#user#')[0]
        
        # Only check once per base constraint
        if base_constraint_id not in checked_base_ids:
            checked_base_ids.add(base_constraint_id)
            if check_constraint_exists(dynamodb_client, constraints_table_name, base_constraint_id):
                logger.debug(f"Skipping existing constraint: {base_constraint_id}")
                skipped_count += 1
            else:
                constraints_to_write.extend(constraint_items)
        else:
            constraints_to_write.extend(constraint_items)
    
    if skipped_count > 0:
        logger.info(f"Skipped {skipped_count} existing constraints (already in new table)")
    
    if not constraints_to_write:
        logger.info("No new constraints to write - all already exist in target table")
        return 0, 0, skipped_count
    
    logger.info(f"Writing {len(constraints_to_write)} denormalized items...")
    
    success_count = 0
    failure_count = 0
    batch_size = 25  # DynamoDB BatchWriteItem limit
    
    for i in range(0, len(constraints_to_write), batch_size):
        batch = constraints_to_write[i:i + batch_size]
        
        request_items = {
            constraints_table_name: [
                {'PutRequest': {'Item': item}} for item in batch
            ]
        }
        
        try:
            response = dynamodb_client.batch_write_item(RequestItems=request_items)
            
            # Handle unprocessed items
            unprocessed = response.get('UnprocessedItems', {})
            if unprocessed:
                logger.warning(f"Batch {i//batch_size + 1}: {len(unprocessed.get(constraints_table_name, []))} unprocessed items")
                failure_count += len(unprocessed.get(constraints_table_name, []))
                success_count += len(batch) - len(unprocessed.get(constraints_table_name, []))
            else:
                success_count += len(batch)
                
            logger.info(f"Batch {i//batch_size + 1}/{(len(constraints_to_write) + batch_size - 1)//batch_size}: Wrote {len(batch)} items")
            
        except ClientError as e:
            logger.error(f"Error writing batch {i//batch_size + 1}: {e}")
            failure_count += len(batch)
    
    # Return count of unique constraints, not denormalized items
    unique_constraints_written = len(constraints) - skipped_count
    return unique_constraints_written, failure_count, skipped_count


def batch_delete_old_constraints(dynamodb_client, auth_table_name: str, 
                                 constraint_keys: List[Dict], dry_run: bool = False) -> Tuple[int, int]:
    """
    Batch delete constraints from old table.
    
    Args:
        dynamodb_client: Boto3 DynamoDB client
        auth_table_name: Name of the AuthEntitiesTable
        constraint_keys: List of keys (entityType, sk) to delete
        dry_run: If True, don't actually delete
        
    Returns:
        Tuple of (success_count, failure_count)
    """
    if dry_run:
        logger.info(f"DRY RUN: Would delete {len(constraint_keys)} constraints from {auth_table_name}")
        return len(constraint_keys), 0
    
    logger.info(f"Deleting {len(constraint_keys)} constraints from {auth_table_name}...")
    
    success_count = 0
    failure_count = 0
    batch_size = 25  # DynamoDB BatchWriteItem limit
    
    for i in range(0, len(constraint_keys), batch_size):
        batch = constraint_keys[i:i + batch_size]
        
        request_items = {
            auth_table_name: [
                {'DeleteRequest': {'Key': key}} for key in batch
            ]
        }
        
        try:
            response = dynamodb_client.batch_write_item(RequestItems=request_items)
            
            # Handle unprocessed items
            unprocessed = response.get('UnprocessedItems', {})
            if unprocessed:
                logger.warning(f"Batch {i//batch_size + 1}: {len(unprocessed.get(auth_table_name, []))} unprocessed deletions")
                failure_count += len(unprocessed.get(auth_table_name, []))
                success_count += len(batch) - len(unprocessed.get(auth_table_name, []))
            else:
                success_count += len(batch)
                
            logger.info(f"Batch {i//batch_size + 1}/{(len(constraint_keys) + batch_size - 1)//batch_size}: Deleted {len(batch)} constraints")
            
        except ClientError as e:
            logger.error(f"Error deleting batch {i//batch_size + 1}: {e}")
            failure_count += len(batch)
    
    return success_count, failure_count


def verify_migration(dynamodb_client, auth_table_name: str, constraints_table_name: str, 
                    expected_count: int, deleted: bool = False) -> bool:
    """
    Verify migration success by counting items in both tables.
    Note: New table has denormalized items, so count will be higher than unique constraints.
    
    Args:
        dynamodb_client: Boto3 DynamoDB client
        auth_table_name: Name of the AuthEntitiesTable
        constraints_table_name: Name of the ConstraintsStorageTable
        expected_count: Expected number of unique constraints migrated
        deleted: Whether old data was deleted
        
    Returns:
        True if verification passed, False otherwise
    """
    logger.info("Verifying migration...")
    
    try:
        # Count constraints in old table
        old_response = dynamodb_client.scan(
            TableName=auth_table_name,
            Select='COUNT',
            FilterExpression='entityType = :entityType AND begins_with(sk, :skPrefix)',
            ExpressionAttributeValues={
                ':entityType': {'S': 'constraint'},
                ':skPrefix': {'S': 'constraint#'}
            }
        )
        old_count = old_response.get('Count', 0)
        
        # Count total items in new table (includes denormalized items)
        new_response = dynamodb_client.scan(
            TableName=constraints_table_name,
            Select='COUNT'
        )
        new_total_items = new_response.get('Count', 0)
        
        # Count unique constraints in new table by scanning and deduplicating
        scan_response = dynamodb_client.scan(TableName=constraints_table_name)
        unique_constraints = set()
        for item in scan_response.get('Items', []):
            full_id = item.get('constraintId', {}).get('S', '')
            base_id = full_id.split('#group#')[0].split('#user#')[0]
            unique_constraints.add(base_id)
        
        new_unique_count = len(unique_constraints)
        
        logger.info(f"Old table ({auth_table_name}): {old_count} constraints")
        logger.info(f"New table ({constraints_table_name}): {new_total_items} total items ({new_unique_count} unique constraints)")
        
        # Verify counts
        verification_passed = True
        
        if deleted and old_count > 0:
            logger.warning(f"Expected 0 constraints in old table after deletion, found {old_count}")
            verification_passed = False
        
        if new_unique_count < expected_count:
            logger.warning(f"Expected at least {expected_count} unique constraints in new table, found {new_unique_count}")
            verification_passed = False
        
        if verification_passed:
            logger.info("✅ Verification PASSED")
        else:
            logger.warning("⚠️ Verification FAILED - counts don't match expected values")
        
        return verification_passed
        
    except ClientError as e:
        logger.error(f"Error during verification: {e}")
        return False


def main():
    """Main function to run the migration."""
    parser = argparse.ArgumentParser(
        description='VAMS v2.3 to v2.4 Constraints Table Migration Script',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry run (recommended first)
  python v2.3_to_v2.4_migration.py --auth-table vams-AuthEntitiesTable --constraints-table vams-ConstraintsStorageTable --dry-run
  
  # Test with limited items
  python v2.3_to_v2.4_migration.py --auth-table vams-AuthEntitiesTable --constraints-table vams-ConstraintsStorageTable --limit 10 --dry-run
  
  # Production migration (keeps old data)
  python v2.3_to_v2.4_migration.py --auth-table vams-AuthEntitiesTable --constraints-table vams-ConstraintsStorageTable
  
  # Migration with cleanup
  python v2.3_to_v2.4_migration.py --auth-table vams-AuthEntitiesTable --constraints-table vams-ConstraintsStorageTable --delete-old-data
  
  # Use config file
  python v2.3_to_v2.4_migration.py --config v2.3_to_v2.4_migration_config.json

Notes:
  - Table names can be found in CloudFormation stack outputs
  - Dry-run mode is recommended for initial testing
  - Old data deletion is optional and can be done later
  - Migration is idempotent - can be safely re-run
        """
    )
    
    parser.add_argument('--config',
                        help='Path to configuration JSON file')
    parser.add_argument('--auth-table',
                        help='Name of the source AuthEntitiesTable')
    parser.add_argument('--constraints-table',
                        help='Name of the target ConstraintsStorageTable')
    parser.add_argument('--delete-old-data', action='store_true',
                        help='Delete constraints from old table after successful migration')
    parser.add_argument('--dry-run', action='store_true',
                        help='Perform dry run without making changes')
    parser.add_argument('--limit', type=int,
                        help='Maximum number of constraints to migrate (for testing)')
    parser.add_argument('--batch-size', type=int, default=25,
                        help='Batch size for DynamoDB operations (default: 25, max: 25)')
    parser.add_argument('--profile',
                        help='AWS profile name')
    parser.add_argument('--region',
                        help='AWS region')
    parser.add_argument('--log-level', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'], default='INFO',
                        help='Logging level (default: INFO)')
    
    args = parser.parse_args()
    
    # Load configuration from file if provided
    config = {}
    if args.config:
        config = load_config_from_file(args.config)
    
    # Command-line arguments override config file
    auth_table_name = args.auth_table or config.get('auth_entities_table_name')
    constraints_table_name = args.constraints_table or config.get('constraints_table_name')
    delete_old_data = args.delete_old_data or config.get('delete_old_data', False)
    dry_run = args.dry_run or config.get('dry_run', False)
    limit = args.limit or config.get('limit')
    batch_size = min(args.batch_size, 25)  # DynamoDB limit
    profile = args.profile or config.get('aws_profile')
    region = args.region or config.get('aws_region')
    log_level = args.log_level or config.get('log_level', 'INFO')
    
    # Validate required parameters
    if not auth_table_name:
        logger.error("Error: --auth-table is required (or provide via config file)")
        logger.error("Get table name from CloudFormation outputs or AWS Console")
        return 1
    
    if not constraints_table_name:
        logger.error("Error: --constraints-table is required (or provide via config file)")
        logger.error("Get table name from CloudFormation outputs or AWS Console")
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
    logger.info("VAMS v2.3 to v2.4 CONSTRAINTS TABLE MIGRATION")
    logger.info("=" * 80)
    logger.info(f"Source Table: {auth_table_name}")
    logger.info(f"Target Table: {constraints_table_name}")
    logger.info(f"Delete Old Data: {delete_old_data}")
    logger.info(f"Dry Run: {dry_run}")
    if limit:
        logger.info(f"Limit: {limit} constraints")
    logger.info("=" * 80)
    
    # Track migration timing
    migration_start_time = datetime.now(timezone.utc)
    
    # Phase 1: Scan old table
    logger.info("\n📖 PHASE 1: Scanning old table for constraints...")
    try:
        old_constraints = scan_old_constraints(dynamodb_client, auth_table_name, limit)
        if not old_constraints:
            logger.warning("No constraints found in old table. Migration not needed.")
            return 0
    except Exception as e:
        logger.error(f"Failed to scan old table: {e}")
        return 1
    
    # Phase 2: Transform data to denormalized format
    logger.info(f"\n🔄 PHASE 2: Transforming {len(old_constraints)} constraints to denormalized format...")
    transformed_constraints = []  # List of lists - each inner list has denormalized items for one constraint
    constraint_keys = []  # For deletion later
    transform_errors = 0
    total_denormalized_items = 0
    
    for idx, old_constraint in enumerate(old_constraints, 1):
        try:
            # Returns list of denormalized items for this constraint
            denormalized_items = transform_constraint_to_new_format(old_constraint)
            transformed_constraints.append(denormalized_items)
            total_denormalized_items += len(denormalized_items)
            
            # Store key for potential deletion
            constraint_keys.append({
                'entityType': old_constraint.get('entityType'),
                'sk': old_constraint.get('sk')
            })
            
            if idx % 100 == 0:
                logger.info(f"Transformed {idx}/{len(old_constraints)} constraints...")
                
        except Exception as e:
            logger.error(f"Error transforming constraint {idx}: {e}")
            transform_errors += 1
    
    logger.info(f"Transformation complete: {len(transformed_constraints)} constraints → {total_denormalized_items} denormalized items, {transform_errors} errors")
    
    if transform_errors > 0:
        logger.warning(f"⚠️ {transform_errors} constraints failed transformation")
    
    # Phase 3: Write to new table
    logger.info(f"\n💾 PHASE 3: Writing constraints to new table...")
    try:
        write_success, write_failure, write_skipped = batch_write_constraints(
            dynamodb_client, constraints_table_name, transformed_constraints, dry_run
        )
        logger.info(f"Write complete: {write_success} success, {write_failure} failures, {write_skipped} skipped (already exist)")
    except Exception as e:
        logger.error(f"Failed to write to new table: {e}")
        return 1
    
    # Phase 4: Optional deletion from old table
    if delete_old_data and write_success > 0:
        logger.info(f"\n🗑️ PHASE 4: Deleting constraints from old table...")
        try:
            # Only delete successfully written constraints
            keys_to_delete = constraint_keys[:write_success]
            delete_success, delete_failure = batch_delete_old_constraints(
                dynamodb_client, auth_table_name, keys_to_delete, dry_run
            )
            logger.info(f"Deletion complete: {delete_success} success, {delete_failure} failures")
        except Exception as e:
            logger.error(f"Failed to delete from old table: {e}")
            logger.warning("Migration succeeded but cleanup failed. Old data remains in AuthEntitiesTable.")
    elif delete_old_data:
        logger.warning("Skipping deletion - no constraints were successfully written")
    else:
        logger.info("\n⏭️ PHASE 4: Skipping deletion (--delete-old-data not specified)")
        logger.info("Old constraint data remains in AuthEntitiesTable for safety")
    
    # Phase 5: Verification
    if not dry_run:
        logger.info(f"\n✅ PHASE 5: Verifying migration...")
        verify_migration(dynamodb_client, auth_table_name, constraints_table_name, 
                        write_success, delete_old_data)
    
    migration_end_time = datetime.now(timezone.utc)
    migration_duration = (migration_end_time - migration_start_time).total_seconds()
    
    # Print summary
    logger.info("\n" + "=" * 80)
    logger.info("MIGRATION SUMMARY")
    logger.info("=" * 80)
    logger.info(f"Migration Duration: {migration_duration:.1f} seconds")
    logger.info(f"Constraints Found: {len(old_constraints)}")
    logger.info(f"Transformation Errors: {transform_errors}")
    logger.info(f"Write Success: {write_success}")
    logger.info(f"Write Failures: {write_failure}")
    logger.info(f"Write Skipped: {write_skipped if 'write_skipped' in locals() else 0}")
    if delete_old_data:
        logger.info(f"Delete Success: {delete_success if 'delete_success' in locals() else 0}")
        logger.info(f"Delete Failures: {delete_failure if 'delete_failure' in locals() else 0}")
    logger.info(f"Dry Run: {dry_run}")
    logger.info(f"Status: {'✅ SUCCESS' if write_failure == 0 and transform_errors == 0 else '⚠️ COMPLETED WITH ERRORS'}")
    logger.info("=" * 80)
    
    # Return appropriate exit code
    if write_failure > 0 or transform_errors > 0:
        logger.warning("Migration completed with errors")
        return 1
    else:
        logger.info("Migration completed successfully")
        if dry_run:
            logger.info("Note: This was a dry run - no changes were made")
        return 0


if __name__ == "__main__":
    sys.exit(main())
