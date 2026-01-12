#!/usr/bin/env python3
# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Data Migration Script for VAMS v2.3 to v2.4 - Complete Migration

This script performs a complete migration from v2.3 to v2.4 including:
1. Constraints table migration (AuthEntitiesTable → ConstraintsStorageTable)
2. Metadata table migration (Old metadata format → New metadata format)
3. Metadata schema migration (Old schema format → New schema format)
4. OpenSearch reindexing (Assets and Files)

Key Features:
- Phase 1-4: Constraints migration with denormalization and GSI support
- Phase 5: Metadata migration from single-record to individual-field format
- Phase 5.5: Metadata schema migration from field-per-record to schema-per-database
- Phase 6: OpenSearch reindexing via Lambda invocation
- Optional deletion of old data
- Dry-run mode for safe testing
- Comprehensive error handling and logging
- Idempotent operations (safe to re-run)

Usage:
    # Dry run (recommended first step)
    python v2.3_to_v2.4_migration.py --config v2.3_to_v2.4_migration_config.json --dry-run
    
    # Production migration (keeps old data)
    python v2.3_to_v2.4_migration.py --config v2.3_to_v2.4_migration_config.json
    
    # Migration with cleanup (deletes old data)
    python v2.3_to_v2.4_migration.py --config v2.3_to_v2.4_migration_config.json --delete-old-data

Requirements:
    - Python 3.6+
    - boto3
    - AWS credentials with DynamoDB read/write and Lambda invoke permissions
"""

import argparse
import boto3
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Any, Tuple
from botocore.exceptions import ClientError
from boto3.dynamodb.types import TypeDeserializer

# Add tools directory to path for importing reindex_utility
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'tools'))
from reindex_utility import invoke_reindexer_lambda

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
# PHASE 1-4: CONSTRAINTS MIGRATION
#######################

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


def verify_constraints_migration(dynamodb_client, auth_table_name: str, constraints_table_name: str, 
                                 expected_count: int, deleted: bool = False) -> bool:
    """
    Verify constraints migration success by counting items in both tables.
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
    logger.info("Verifying constraints migration...")
    
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
            logger.warning(f"Expected 0 constraints in old table after deletion, found {old_count}. Note: The old table may contain other entity types besides constraints.")
            verification_passed = False
        
        if new_unique_count < expected_count:
            logger.warning(f"Expected at least {expected_count} unique constraints in new table, found {new_unique_count}")
            verification_passed = False
        
        if verification_passed:
            logger.info("✅ Constraints verification PASSED")
        else:
            logger.warning("⚠️ Constraints verification FAILED - counts don't match expected values")
        
        return verification_passed
        
    except ClientError as e:
        logger.error(f"Error during constraints verification: {e}")
        return False


#######################
# PHASE 5.5: METADATA SCHEMA MIGRATION
#######################

def scan_old_metadata_schema_table(dynamodb_client, old_schema_table_name: str, limit: int = None) -> List[Dict]:
    """
    Scan old metadata schema table for all field records.
    
    Args:
        dynamodb_client: Boto3 DynamoDB client
        old_schema_table_name: Name of the old metadata schema table
        limit: Maximum number of records to retrieve (for testing)
        
    Returns:
        List of field items from old table
    """
    logger.info(f"Scanning {old_schema_table_name} for metadata schema fields...")
    
    field_records = []
    scan_kwargs = {
        'TableName': old_schema_table_name
    }
    
    if limit:
        scan_kwargs['Limit'] = limit
    
    try:
        response = dynamodb_client.scan(**scan_kwargs)
        field_records.extend(response.get('Items', []))
        
        # Handle pagination
        while 'LastEvaluatedKey' in response and (not limit or len(field_records) < limit):
            scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
            if limit:
                scan_kwargs['Limit'] = limit - len(field_records)
            response = dynamodb_client.scan(**scan_kwargs)
            field_records.extend(response.get('Items', []))
        
        logger.info(f"Found {len(field_records)} field records in old schema table")
        return field_records
        
    except ClientError as e:
        logger.error(f"Error scanning old metadata schema table: {e}")
        raise


def group_fields_by_database(old_field_records: List[Dict]) -> Dict[str, List[Dict]]:
    """
    Group field records by databaseId.
    
    Args:
        old_field_records: List of field records from old table
        
    Returns:
        Dictionary mapping databaseId to list of field records
    """
    grouped = {}
    for record in old_field_records:
        database_id = extract_string_value(record.get('databaseId', {}))
        if database_id:
            if database_id not in grouped:
                grouped[database_id] = []
            grouped[database_id].append(record)
    
    logger.info(f"Grouped fields into {len(grouped)} databases")
    return grouped


def delete_existing_migrated_schemas(dynamodb_client, new_schema_table_name: str, 
                                     database_id: str, dry_run: bool = False) -> int:
    """
    Delete existing 'migratedSchema' schemas for a specific database.
    
    Args:
        dynamodb_client: Boto3 DynamoDB client
        new_schema_table_name: Name of the new metadata schema table
        database_id: The database ID to check
        dry_run: If True, don't actually delete
        
    Returns:
        Number of schemas deleted
    """
    try:
        # Query for existing migratedSchema using DatabaseIdMetadataEntityTypeIndex GSI
        composite_key = f"{database_id}:assetMetadata"
        
        response = dynamodb_client.query(
            TableName=new_schema_table_name,
            IndexName='DatabaseIdMetadataEntityTypeIndex',
            KeyConditionExpression='#pk = :pkValue',
            ExpressionAttributeNames={'#pk': 'databaseId:metadataEntityType'},
            ExpressionAttributeValues={':pkValue': {'S': composite_key}}
        )
        
        # Filter for schemas named 'migratedSchema'
        schemas_to_delete = []
        for item in response.get('Items', []):
            schema_name = item.get('schemaName', {}).get('S', '')
            if schema_name == 'migratedSchema':
                schemas_to_delete.append({
                    'metadataSchemaId': item.get('metadataSchemaId'),
                    'databaseId:metadataEntityType': item.get('databaseId:metadataEntityType')
                })
        
        if not schemas_to_delete:
            return 0
        
        if dry_run:
            logger.info(f"DRY RUN: Would delete {len(schemas_to_delete)} existing 'migratedSchema' for database {database_id}")
            return len(schemas_to_delete)
        
        # Delete each schema
        deleted_count = 0
        for schema_key in schemas_to_delete:
            try:
                dynamodb_client.delete_item(
                    TableName=new_schema_table_name,
                    Key=schema_key
                )
                deleted_count += 1
            except ClientError as e:
                logger.warning(f"Error deleting schema: {e}")
        
        logger.info(f"Deleted {deleted_count} existing 'migratedSchema' for database {database_id}")
        return deleted_count
        
    except ClientError as e:
        logger.warning(f"Error checking for existing schemas: {e}")
        return 0


def map_old_datatype_to_new(old_datatype: str) -> str:
    """
    Map old dataType to new metadataFieldValueType.
    
    Args:
        old_datatype: Old dataType value
        
    Returns:
        New metadataFieldValueType value, or None if should be skipped
    """
    mapping = {
        'string': 'string',
        'textarea': 'multiline_string',
        'number': 'number',
        'boolean': 'boolean',
        'date': 'date',
        'inline-controlled-list': 'inline_controlled_list',
        'location': 'lla',
        'controlled-list': None  # Skip - no values provided
    }
    
    return mapping.get(old_datatype.lower())


def transform_location_field(old_field_record: Dict) -> Dict:
    """
    Transform location field to LLA format with default value.
    
    Args:
        old_field_record: Old field record in DynamoDB format
        
    Returns:
        Default value as JSON string for LLA type
    """
    latitude = extract_string_value(old_field_record.get('latitudeField', {}))
    longitude = extract_string_value(old_field_record.get('longitudeField', {}))
    zoom_level = extract_string_value(old_field_record.get('zoomLevelField', {}))
    
    # Convert to float if possible, otherwise use 0
    try:
        lat_val = float(latitude) if latitude else 0.0
    except ValueError:
        lat_val = 0.0
    
    try:
        long_val = float(longitude) if longitude else 0.0
    except ValueError:
        long_val = 0.0
    
    try:
        alt_val = float(zoom_level) if zoom_level else 0.0
    except ValueError:
        alt_val = 0.0
    
    # Create LLA JSON
    lla_value = {
        "lat": lat_val,
        "long": long_val,
        "alt": alt_val
    }
    
    return json.dumps(lla_value)


def transform_schema_field_to_new_format(old_field_record: Dict) -> Dict:
    """
    Transform a single field record to new schema field format.
    
    Args:
        old_field_record: Old field record in DynamoDB format
        
    Returns:
        New field definition dict, or None if should be skipped
    """
    # Extract field values
    field_name = extract_string_value(old_field_record.get('field', {}))
    data_type = extract_string_value(old_field_record.get('dataType', {}))
    required = old_field_record.get('required', {}).get('BOOL', False)
    sequence_number = extract_string_value(old_field_record.get('sequenceNumber', {}))
    depends_on = extract_list_value(old_field_record.get('dependsOn', {}))
    inline_controlled_values = extract_string_value(old_field_record.get('inlineControlledListValues', {}))
    
    # Map dataType to new format
    new_value_type = map_old_datatype_to_new(data_type)
    
    # Skip controlled-list without values
    if new_value_type is None:
        logger.debug(f"Skipping field '{field_name}' with type '{data_type}' (no values provided)")
        return None
    
    # Build new field definition
    new_field = {
        'metadataFieldKeyName': field_name,
        'metadataFieldValueType': new_value_type,
        'required': required
    }
    
    # Add sequence if present
    if sequence_number:
        try:
            new_field['sequence'] = int(sequence_number)
        except ValueError:
            pass  # Skip invalid sequence numbers
    
    # Add dependsOn if present
    if depends_on:
        # Extract string values from DynamoDB list format
        depends_on_list = [item.get('S', '') for item in depends_on if 'S' in item]
        if depends_on_list:
            new_field['dependsOnFieldKeyName'] = depends_on_list
    
    # Handle inline controlled list values
    if new_value_type == 'inline_controlled_list' and inline_controlled_values:
        # Parse comma-delimited string to array
        controlled_keys = [key.strip() for key in inline_controlled_values.split(',') if key.strip()]
        if controlled_keys:
            new_field['controlledListKeys'] = controlled_keys
    
    # Handle location type - add default value
    if new_value_type == 'lla':
        default_value = transform_location_field(old_field_record)
        new_field['defaultMetadataFieldValue'] = default_value
    
    return new_field


def transform_schema_fields_to_new_format(database_id: str, old_field_records: List[Dict]) -> List[Dict]:
    """
    Transform all field records for a database to new schema format.
    
    Args:
        database_id: The database ID
        old_field_records: List of old field records
        
    Returns:
        List of new field definitions
    """
    new_fields = []
    skipped_count = 0
    
    for record in old_field_records:
        new_field = transform_schema_field_to_new_format(record)
        if new_field:
            new_fields.append(new_field)
        else:
            skipped_count += 1
    
    if skipped_count > 0:
        logger.info(f"Skipped {skipped_count} fields for database {database_id} (controlled-list without values)")
    
    return new_fields


def create_metadata_schema_record(database_id: str, transformed_fields: List[Dict]) -> Dict:
    """
    Create a new metadata schema record.
    
    Args:
        database_id: The database ID
        transformed_fields: List of transformed field definitions
        
    Returns:
        New schema record in DynamoDB format
    """
    # Generate unique ID
    metadata_schema_id = str(uuid.uuid4())
    
    # Create composite sort key
    composite_key = f"{database_id}:assetMetadata"
    
    # Add metadata
    now = datetime.now(timezone.utc).isoformat()
    
    # Convert fields to JSON string for storage
    fields_json = json.dumps({'fields': transformed_fields})
    
    # Build schema item
    schema_item = {
        'metadataSchemaId': {'S': metadata_schema_id},
        'databaseId:metadataEntityType': {'S': composite_key},
        'databaseId': {'S': database_id},
        'metadataSchemaEntityType': {'S': 'assetMetadata'},
        'schemaName': {'S': 'migratedSchema'},
        'fields': {'S': fields_json},
        'enabled': {'BOOL': True},
        'dateCreated': {'S': now},
        'dateModified': {'S': now},
        'createdBy': {'S': 'MIGRATION'},
        'modifiedBy': {'S': 'MIGRATION'}
    }
    
    return schema_item


def check_metadata_schema_exists(dynamodb_client, new_schema_table_name: str, database_id: str) -> bool:
    """
    Check if a migratedSchema already exists for a database.
    
    Args:
        dynamodb_client: Boto3 DynamoDB client
        new_schema_table_name: Name of the new metadata schema table
        database_id: The database ID to check
        
    Returns:
        True if schema exists, False otherwise
    """
    try:
        composite_key = f"{database_id}:assetMetadata"
        
        response = dynamodb_client.query(
            TableName=new_schema_table_name,
            IndexName='DatabaseIdMetadataEntityTypeIndex',
            KeyConditionExpression='#pk = :pkValue',
            ExpressionAttributeNames={'#pk': 'databaseId:metadataEntityType'},
            ExpressionAttributeValues={':pkValue': {'S': composite_key}},
            Limit=1
        )
        
        # Check if any item has schemaName = 'migratedSchema'
        for item in response.get('Items', []):
            schema_name = item.get('schemaName', {}).get('S', '')
            if schema_name == 'migratedSchema':
                return True
        
        return False
        
    except ClientError as e:
        logger.warning(f"Error checking if schema exists: {e}")
        return False


def batch_write_metadata_schemas(dynamodb_client, new_schema_table_name: str, 
                                 schema_records: List[Dict], dry_run: bool = False) -> Tuple[int, int, int]:
    """
    Batch write metadata schemas to new table.
    
    Args:
        dynamodb_client: Boto3 DynamoDB client
        new_schema_table_name: Name of the new metadata schema table
        schema_records: List of schema records to write
        dry_run: If True, don't actually write
        
    Returns:
        Tuple of (success_count, failure_count, skipped_count)
    """
    if dry_run:
        logger.info(f"DRY RUN: Would write {len(schema_records)} metadata schemas to {new_schema_table_name}")
        return len(schema_records), 0, 0
    
    logger.info(f"Writing {len(schema_records)} metadata schemas to {new_schema_table_name}...")
    
    success_count = 0
    failure_count = 0
    batch_size = 25  # DynamoDB BatchWriteItem limit
    
    for i in range(0, len(schema_records), batch_size):
        batch = schema_records[i:i + batch_size]
        
        request_items = {
            new_schema_table_name: [
                {'PutRequest': {'Item': item}} for item in batch
            ]
        }
        
        try:
            response = dynamodb_client.batch_write_item(RequestItems=request_items)
            
            # Handle unprocessed items
            unprocessed = response.get('UnprocessedItems', {})
            if unprocessed:
                logger.warning(f"Batch {i//batch_size + 1}: {len(unprocessed.get(new_schema_table_name, []))} unprocessed items")
                failure_count += len(unprocessed.get(new_schema_table_name, []))
                success_count += len(batch) - len(unprocessed.get(new_schema_table_name, []))
            else:
                success_count += len(batch)
                
            logger.info(f"Batch {i//batch_size + 1}/{(len(schema_records) + batch_size - 1)//batch_size}: Wrote {len(batch)} schemas")
            
        except ClientError as e:
            logger.error(f"Error writing batch {i//batch_size + 1}: {e}")
            failure_count += len(batch)
    
    return success_count, failure_count, 0


def batch_delete_old_metadata_schema_records(dynamodb_client, old_schema_table_name: str, 
                                             field_keys: List[Dict], dry_run: bool = False) -> Tuple[int, int]:
    """
    Batch delete field records from old metadata schema table.
    
    Args:
        dynamodb_client: Boto3 DynamoDB client
        old_schema_table_name: Name of the old metadata schema table
        field_keys: List of keys to delete
        dry_run: If True, don't actually delete
        
    Returns:
        Tuple of (success_count, failure_count)
    """
    if dry_run:
        logger.info(f"DRY RUN: Would delete {len(field_keys)} field records from {old_schema_table_name}")
        return len(field_keys), 0
    
    logger.info(f"Deleting {len(field_keys)} field records from {old_schema_table_name}...")
    
    success_count = 0
    failure_count = 0
    batch_size = 25  # DynamoDB BatchWriteItem limit
    
    for i in range(0, len(field_keys), batch_size):
        batch = field_keys[i:i + batch_size]
        
        request_items = {
            old_schema_table_name: [
                {'DeleteRequest': {'Key': key}} for key in batch
            ]
        }
        
        try:
            response = dynamodb_client.batch_write_item(RequestItems=request_items)
            
            # Handle unprocessed items
            unprocessed = response.get('UnprocessedItems', {})
            if unprocessed:
                logger.warning(f"Batch {i//batch_size + 1}: {len(unprocessed.get(old_schema_table_name, []))} unprocessed deletions")
                failure_count += len(unprocessed.get(old_schema_table_name, []))
                success_count += len(batch) - len(unprocessed.get(old_schema_table_name, []))
            else:
                success_count += len(batch)
                
            logger.info(f"Batch {i//batch_size + 1}/{(len(field_keys) + batch_size - 1)//batch_size}: Deleted {len(batch)} records")
            
        except ClientError as e:
            logger.error(f"Error deleting batch {i//batch_size + 1}: {e}")
            failure_count += len(batch)
    
    return success_count, failure_count


def verify_metadata_schema_migration(dynamodb_client, old_schema_table_name: str, new_schema_table_name: str, 
                                     expected_count: int, deleted: bool = False) -> bool:
    """
    Verify metadata schema migration success.
    
    Args:
        dynamodb_client: Boto3 DynamoDB client
        old_schema_table_name: Name of the old metadata schema table
        new_schema_table_name: Name of the new metadata schema table
        expected_count: Expected number of schemas migrated
        deleted: Whether old data was deleted
        
    Returns:
        True if verification passed, False otherwise
    """
    logger.info("Verifying metadata schema migration...")
    
    try:
        # Count records in old table
        old_response = dynamodb_client.scan(
            TableName=old_schema_table_name,
            Select='COUNT'
        )
        old_count = old_response.get('Count', 0)
        
        # Count schemas in new table
        new_response = dynamodb_client.scan(
            TableName=new_schema_table_name,
            Select='COUNT'
        )
        new_count = new_response.get('Count', 0)
        
        logger.info(f"Old table ({old_schema_table_name}): {old_count} field records")
        logger.info(f"New table ({new_schema_table_name}): {new_count} schemas")
        
        # Verify counts
        verification_passed = True
        
        if deleted and old_count > 0:
            logger.warning(f"Expected 0 records in old table after deletion, found {old_count}")
            verification_passed = False
        
        if new_count < expected_count:
            logger.warning(f"Expected at least {expected_count} schemas in new table, found {new_count}")
            verification_passed = False
        
        if verification_passed:
            logger.info("✅ Metadata schema verification PASSED")
        else:
            logger.warning("⚠️ Metadata schema verification FAILED - counts don't match expected values")
        
        return verification_passed
        
    except ClientError as e:
        logger.error(f"Error during metadata schema verification: {e}")
        return False


#######################
# PHASE 5: METADATA MIGRATION
#######################

def parse_asset_id(asset_id: str, database_id: str) -> Tuple[bool, str, str]:
    """
    Parse assetId to determine if it's an asset or file.
    
    Args:
        asset_id: The assetId from old metadata table
        database_id: The databaseId
        
    Returns:
        Tuple of (is_file, actual_asset_id, file_path)
        
    Examples:
        - Asset: "x08fb7689-34d5-4329-8eb1-81dcccca3459" 
          → (False, "x08fb7689...", "/")
        - File: "/x08fb7689-34d5-4329-8eb1-81dcccca3459/Brou_Lux.jpg"
          → (True, "x08fb7689...", "Brou_Lux.jpg")
    """
    if asset_id.startswith('/'):
        # It's a file
        # Format: /assetId/path/to/file.jpg
        # Remove leading slash and split
        parts = asset_id[1:].split('/', 1)
        actual_asset_id = parts[0]
        file_path = parts[1] if len(parts) > 1 else ""
        return (True, actual_asset_id, file_path)
    else:
        # It's an asset
        return (False, asset_id, "/")


def scan_old_metadata(dynamodb_client, old_metadata_table_name: str, limit: int = None) -> List[Dict]:
    """
    Scan old metadata table for all metadata records.
    
    Args:
        dynamodb_client: Boto3 DynamoDB client
        old_metadata_table_name: Name of the old metadata table
        limit: Maximum number of records to retrieve (for testing)
        
    Returns:
        List of metadata items from old table
    """
    logger.info(f"Scanning {old_metadata_table_name} for metadata records...")
    
    metadata_records = []
    scan_kwargs = {
        'TableName': old_metadata_table_name
    }
    
    if limit:
        scan_kwargs['Limit'] = limit
    
    try:
        response = dynamodb_client.scan(**scan_kwargs)
        metadata_records.extend(response.get('Items', []))
        
        # Handle pagination
        while 'LastEvaluatedKey' in response and (not limit or len(metadata_records) < limit):
            scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
            if limit:
                scan_kwargs['Limit'] = limit - len(metadata_records)
            response = dynamodb_client.scan(**scan_kwargs)
            metadata_records.extend(response.get('Items', []))
        
        logger.info(f"Found {len(metadata_records)} metadata records in old table")
        return metadata_records
        
    except ClientError as e:
        logger.error(f"Error scanning old metadata table: {e}")
        raise


def transform_metadata_to_new_format(old_metadata_record: Dict) -> List[Dict]:
    """
    Transform metadata from old single-record format to new individual-field format.
    
    Old format: Single record with all metadata fields as columns
    New format: Individual records per metadata field
    
    Args:
        old_metadata_record: Metadata record in old DynamoDB format
        
    Returns:
        List of metadata items in new DynamoDB format
    """
    items = []
    
    # Extract databaseId and assetId
    database_id = extract_string_value(old_metadata_record.get('databaseId', {}))
    asset_id = extract_string_value(old_metadata_record.get('assetId', {}))
    
    if not database_id or not asset_id:
        logger.warning(f"Skipping record with missing databaseId or assetId")
        return items
    
    # Parse assetId to determine if it's an asset or file
    is_file, actual_asset_id, file_path = parse_asset_id(asset_id, database_id)
    
    # Build composite keys
    composite_key = f"{database_id}:{actual_asset_id}:{file_path}"
    composite_key_asset = f"{database_id}:{actual_asset_id}"
    
    # Process all fields except databaseId, assetId, and fields starting with underscore
    for field_name, field_value in old_metadata_record.items():
        # Skip system fields
        if field_name in ['databaseId', 'assetId']:
            continue
        
        # Skip fields starting with underscore
        if field_name.startswith('_'):
            continue
        
        # Extract the actual value
        value_str = extract_string_value(field_value)
        
        # Skip empty values
        if not value_str:
            continue
        
        # Create new metadata record
        item = {
            'metadataKey': {'S': field_name},
            'databaseId:assetId:filePath': {'S': composite_key},
            'databaseId:assetId': {'S': composite_key_asset},
            'metadataValue': {'S': value_str},
            'metadataValueType': {'S': 'string'}
        }
        
        items.append(item)
    
    return items


def check_metadata_exists(dynamodb_client, new_metadata_table_name: str, composite_key: str, metadata_key: str) -> bool:
    """
    Check if a metadata record already exists in the new table.
    
    Args:
        dynamodb_client: Boto3 DynamoDB client
        new_metadata_table_name: Name of the new metadata table
        composite_key: The composite key (databaseId:assetId:filePath)
        metadata_key: The metadata key
        
    Returns:
        True if metadata exists, False otherwise
    """
    try:
        response = dynamodb_client.get_item(
            TableName=new_metadata_table_name,
            Key={
                'metadataKey': {'S': metadata_key},
                'databaseId:assetId:filePath': {'S': composite_key}
            }
        )
        return 'Item' in response
    except ClientError as e:
        logger.warning(f"Error checking if metadata exists: {e}")
        return False


def batch_write_metadata(dynamodb_client, new_metadata_table_name: str, 
                        metadata_items: List[Dict], dry_run: bool = False) -> Tuple[int, int, int]:
    """
    Batch write metadata to new table, skipping existing records.
    
    Args:
        dynamodb_client: Boto3 DynamoDB client
        new_metadata_table_name: Name of the new metadata table
        metadata_items: List of metadata items to write
        dry_run: If True, don't actually write
        
    Returns:
        Tuple of (success_count, failure_count, skipped_count)
    """
    if dry_run:
        logger.info(f"DRY RUN: Would write {len(metadata_items)} metadata items to {new_metadata_table_name}")
        return len(metadata_items), 0, 0
    
    logger.info(f"Writing {len(metadata_items)} metadata items to {new_metadata_table_name}...")
    logger.info("Checking for existing records to avoid duplicates...")
    
    # Filter out existing metadata
    items_to_write = []
    skipped_count = 0
    
    for item in metadata_items:
        composite_key = item['databaseId:assetId:filePath']['S']
        metadata_key = item['metadataKey']['S']
        
        if check_metadata_exists(dynamodb_client, new_metadata_table_name, composite_key, metadata_key):
            logger.debug(f"Skipping existing metadata: {composite_key} / {metadata_key}")
            skipped_count += 1
        else:
            items_to_write.append(item)
    
    if skipped_count > 0:
        logger.info(f"Skipped {skipped_count} existing metadata items (already in new table)")
    
    if not items_to_write:
        logger.info("No new metadata to write - all already exist in target table")
        return 0, 0, skipped_count
    
    logger.info(f"Writing {len(items_to_write)} new metadata items...")
    
    success_count = 0
    failure_count = 0
    batch_size = 25  # DynamoDB BatchWriteItem limit
    
    for i in range(0, len(items_to_write), batch_size):
        batch = items_to_write[i:i + batch_size]
        
        request_items = {
            new_metadata_table_name: [
                {'PutRequest': {'Item': item}} for item in batch
            ]
        }
        
        try:
            response = dynamodb_client.batch_write_item(RequestItems=request_items)
            
            # Handle unprocessed items
            unprocessed = response.get('UnprocessedItems', {})
            if unprocessed:
                logger.warning(f"Batch {i//batch_size + 1}: {len(unprocessed.get(new_metadata_table_name, []))} unprocessed items")
                failure_count += len(unprocessed.get(new_metadata_table_name, []))
                success_count += len(batch) - len(unprocessed.get(new_metadata_table_name, []))
            else:
                success_count += len(batch)
                
            if (i//batch_size + 1) % 10 == 0:
                logger.info(f"Batch {i//batch_size + 1}/{(len(items_to_write) + batch_size - 1)//batch_size}: Wrote {len(batch)} items")
            
        except ClientError as e:
            logger.error(f"Error writing batch {i//batch_size + 1}: {e}")
            failure_count += len(batch)
    
    return success_count, failure_count, skipped_count


def batch_delete_old_metadata(dynamodb_client, old_metadata_table_name: str, 
                              metadata_keys: List[Dict], dry_run: bool = False) -> Tuple[int, int]:
    """
    Batch delete metadata from old table.
    
    Args:
        dynamodb_client: Boto3 DynamoDB client
        old_metadata_table_name: Name of the old metadata table
        metadata_keys: List of keys to delete
        dry_run: If True, don't actually delete
        
    Returns:
        Tuple of (success_count, failure_count)
    """
    if dry_run:
        logger.info(f"DRY RUN: Would delete {len(metadata_keys)} metadata records from {old_metadata_table_name}")
        return len(metadata_keys), 0
    
    logger.info(f"Deleting {len(metadata_keys)} metadata records from {old_metadata_table_name}...")
    
    success_count = 0
    failure_count = 0
    batch_size = 25  # DynamoDB BatchWriteItem limit
    
    for i in range(0, len(metadata_keys), batch_size):
        batch = metadata_keys[i:i + batch_size]
        
        request_items = {
            old_metadata_table_name: [
                {'DeleteRequest': {'Key': key}} for key in batch
            ]
        }
        
        try:
            response = dynamodb_client.batch_write_item(RequestItems=request_items)
            
            # Handle unprocessed items
            unprocessed = response.get('UnprocessedItems', {})
            if unprocessed:
                logger.warning(f"Batch {i//batch_size + 1}: {len(unprocessed.get(old_metadata_table_name, []))} unprocessed deletions")
                failure_count += len(unprocessed.get(old_metadata_table_name, []))
                success_count += len(batch) - len(unprocessed.get(old_metadata_table_name, []))
            else:
                success_count += len(batch)
                
            if (i//batch_size + 1) % 10 == 0:
                logger.info(f"Batch {i//batch_size + 1}/{(len(metadata_keys) + batch_size - 1)//batch_size}: Deleted {len(batch)} records")
            
        except ClientError as e:
            logger.error(f"Error deleting batch {i//batch_size + 1}: {e}")
            failure_count += len(batch)
    
    return success_count, failure_count


def verify_metadata_migration(dynamodb_client, old_metadata_table_name: str, new_metadata_table_name: str, 
                              expected_count: int, deleted: bool = False) -> bool:
    """
    Verify metadata migration success by counting items in both tables.
    
    Args:
        dynamodb_client: Boto3 DynamoDB client
        old_metadata_table_name: Name of the old metadata table
        new_metadata_table_name: Name of the new metadata table
        expected_count: Expected number of metadata items migrated
        deleted: Whether old data was deleted
        
    Returns:
        True if verification passed, False otherwise
    """
    logger.info("Verifying metadata migration...")
    
    try:
        # Count records in old table
        old_response = dynamodb_client.scan(
            TableName=old_metadata_table_name,
            Select='COUNT'
        )
        old_count = old_response.get('Count', 0)
        
        # Count items in new table
        new_response = dynamodb_client.scan(
            TableName=new_metadata_table_name,
            Select='COUNT'
        )
        new_count = new_response.get('Count', 0)
        
        logger.info(f"Old table ({old_metadata_table_name}): {old_count} records")
        logger.info(f"New table ({new_metadata_table_name}): {new_count} metadata items")
        
        # Verify counts
        verification_passed = True
        
        if deleted and old_count > 0:
            logger.warning(f"Expected 0 records in old table after deletion, found {old_count}")
            verification_passed = False
        
        if new_count < expected_count:
            logger.warning(f"Expected at least {expected_count} metadata items in new table, found {new_count}")
            verification_passed = False
        
        if verification_passed:
            logger.info("✅ Metadata verification PASSED")
        else:
            logger.warning("⚠️ Metadata verification FAILED - counts don't match expected values")
        
        return verification_passed
        
    except ClientError as e:
        logger.error(f"Error during metadata verification: {e}")
        return False


#######################
# MAIN MIGRATION FUNCTION
#######################

def main():
    """Main function to run the complete migration."""
    parser = argparse.ArgumentParser(
        description='VAMS v2.3 to v2.4 Complete Migration Script',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry run (recommended first)
  python v2.3_to_v2.4_migration.py --config v2.3_to_v2.4_migration_config.json --dry-run
  
  # Production migration (keeps old data)
  python v2.3_to_v2.4_migration.py --config v2.3_to_v2.4_migration_config.json
  
  # Migration with cleanup
  python v2.3_to_v2.4_migration.py --config v2.3_to_v2.4_migration_config.json --delete-old-data

Notes:
  - Configuration file is required
  - Table names must be provided in config file
  - Dry-run mode is recommended for initial testing
  - Old data deletion is optional and can be done later
  - Migration is idempotent - can be safely re-run
        """
    )
    
    parser.add_argument('--config', required=True,
                        help='Path to configuration JSON file (required)')
    parser.add_argument('--delete-old-data', action='store_true',
                        help='Delete old data from source tables after successful migration')
    parser.add_argument('--dry-run', action='store_true',
                        help='Perform dry run without making changes')
    parser.add_argument('--limit', type=int,
                        help='Maximum number of items to migrate per table (for testing)')
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
    auth_table_name = config.get('auth_entities_table_name')
    constraints_table_name = config.get('constraints_table_name')
    old_metadata_table_name = config.get('old_metadata_table_name')
    new_metadata_table_name = config.get('new_metadata_table_name')
    old_metadata_schema_table_name = config.get('old_metadata_schema_table_name')
    new_metadata_schema_table_name = config.get('new_metadata_schema_table_name')
    reindexer_function_name = config.get('reindexer_function_name')
    clear_indexes_before_reindex = config.get('clear_indexes_before_reindex', False)
    delete_old_data = args.delete_old_data or config.get('delete_old_data', False)
    dry_run = args.dry_run or config.get('dry_run', False)
    limit = args.limit or config.get('limit')
    profile = args.profile or config.get('aws_profile')
    region = args.region or config.get('aws_region')
    log_level = args.log_level or config.get('log_level', 'INFO')
    
    # Validate required parameters for Phase 1-4 (Constraints)
    if not auth_table_name or not constraints_table_name:
        logger.error("Error: auth_entities_table_name and constraints_table_name are required in config file")
        return 1
    
    # Validate required parameters for Phase 5 (Metadata)
    if not old_metadata_table_name or not new_metadata_table_name:
        logger.error("Error: old_metadata_table_name and new_metadata_table_name are required in config file")
        return 1
    
    # Validate required parameters for Phase 5.5 (Metadata Schema)
    if not old_metadata_schema_table_name or not new_metadata_schema_table_name:
        logger.error("Error: old_metadata_schema_table_name and new_metadata_schema_table_name are required in config file")
        return 1
    
    # Validate required parameters for Phase 6 (Reindex)
    if not reindexer_function_name:
        logger.error("Error: reindexer_function_name is required in config file")
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
    logger.info("VAMS v2.3 to v2.4 COMPLETE MIGRATION")
    logger.info("=" * 80)
    logger.info("Phase 1-4: Constraints Migration")
    logger.info(f"  Source: {auth_table_name}")
    logger.info(f"  Target: {constraints_table_name}")
    logger.info("Phase 5: Metadata Migration")
    logger.info(f"  Source: {old_metadata_table_name}")
    logger.info(f"  Target: {new_metadata_table_name}")
    logger.info("Phase 5.5: Metadata Schema Migration")
    logger.info(f"  Source: {old_metadata_schema_table_name}")
    logger.info(f"  Target: {new_metadata_schema_table_name}")
    logger.info("Phase 6: OpenSearch Reindex")
    logger.info(f"  Function: {reindexer_function_name}")
    logger.info(f"  Clear Indexes: {clear_indexes_before_reindex}")
    logger.info(f"Delete Old Data: {delete_old_data}")
    logger.info(f"Dry Run: {dry_run}")
    if limit:
        logger.info(f"Limit: {limit} items per table")
    logger.info("=" * 80)
    
    # Track overall migration timing
    migration_start_time = datetime.now(timezone.utc)
    
    # Track migration statistics
    constraints_write_success = 0
    constraints_write_failure = 0
    constraints_write_skipped = 0
    metadata_write_success = 0
    metadata_write_failure = 0
    metadata_write_skipped = 0
    metadata_schema_write_success = 0
    metadata_schema_write_failure = 0
    metadata_schema_write_skipped = 0
    
    #######################
    # PHASE 1-4: CONSTRAINTS MIGRATION
    #######################
    
    logger.info("\n" + "=" * 80)
    logger.info("PHASE 1-4: CONSTRAINTS TABLE MIGRATION")
    logger.info("=" * 80)
    
    # Phase 1: Scan old constraints table
    logger.info("\n📖 PHASE 1: Scanning old table for constraints...")
    try:
        old_constraints = scan_old_constraints(dynamodb_client, auth_table_name, limit)
        if not old_constraints:
            logger.warning("No constraints found in old table. Skipping constraints migration.")
        else:
            # Phase 2: Transform constraints
            logger.info(f"\n🔄 PHASE 2: Transforming {len(old_constraints)} constraints to denormalized format...")
            transformed_constraints = []
            constraint_keys = []
            transform_errors = 0
            total_denormalized_items = 0
            
            for idx, old_constraint in enumerate(old_constraints, 1):
                try:
                    denormalized_items = transform_constraint_to_new_format(old_constraint)
                    transformed_constraints.append(denormalized_items)
                    total_denormalized_items += len(denormalized_items)
                    
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
            
            # Phase 3: Write to new table
            logger.info(f"\n💾 PHASE 3: Writing constraints to new table...")
            try:
                constraints_write_success, constraints_write_failure, constraints_write_skipped = batch_write_constraints(
                    dynamodb_client, constraints_table_name, transformed_constraints, dry_run
                )
                logger.info(f"Write complete: {constraints_write_success} success, {constraints_write_failure} failures, {constraints_write_skipped} skipped")
            except Exception as e:
                logger.error(f"Failed to write to new table: {e}")
                return 1
            
            # Phase 4: Optional deletion from old table
            if delete_old_data and constraints_write_success > 0:
                logger.info(f"\n🗑️ PHASE 4: Deleting constraints from old table...")
                try:
                    keys_to_delete = constraint_keys[:constraints_write_success]
                    delete_success, delete_failure = batch_delete_old_constraints(
                        dynamodb_client, auth_table_name, keys_to_delete, dry_run
                    )
                    logger.info(f"Deletion complete: {delete_success} success, {delete_failure} failures")
                except Exception as e:
                    logger.error(f"Failed to delete from old table: {e}")
                    logger.warning("Migration succeeded but cleanup failed. Old data remains in AuthEntitiesTable.")
            else:
                logger.info("\n⏭️ PHASE 4: Skipping deletion (--delete-old-data not specified or no successful writes)")
            
            # Verification
            if not dry_run and constraints_write_success > 0:
                verify_constraints_migration(dynamodb_client, auth_table_name, constraints_table_name, 
                                           constraints_write_success, delete_old_data)
    except Exception as e:
        logger.error(f"Constraints migration failed: {e}")
        return 1
    
    #######################
    # PHASE 5: METADATA MIGRATION
    #######################
    
    logger.info("\n" + "=" * 80)
    logger.info("PHASE 5: METADATA TABLE MIGRATION")
    logger.info("=" * 80)
    
    # Scan old metadata table
    logger.info("\n📖 Scanning old metadata table...")
    try:
        old_metadata_records = scan_old_metadata(dynamodb_client, old_metadata_table_name, limit)
        if not old_metadata_records:
            logger.warning("No metadata records found in old table. Skipping metadata migration.")
        else:
            # Transform metadata
            logger.info(f"\n🔄 Transforming {len(old_metadata_records)} metadata records to new format...")
            all_metadata_items = []
            metadata_record_keys = []
            transform_errors = 0
            
            for idx, old_record in enumerate(old_metadata_records, 1):
                try:
                    new_items = transform_metadata_to_new_format(old_record)
                    all_metadata_items.extend(new_items)
                    
                    # Store key for potential deletion (need to determine primary key structure)
                    database_id = extract_string_value(old_record.get('databaseId', {}))
                    asset_id = extract_string_value(old_record.get('assetId', {}))
                    if database_id and asset_id:
                        metadata_record_keys.append({
                            'databaseId': {'S': database_id},
                            'assetId': {'S': asset_id}
                        })
                    
                    if idx % 100 == 0:
                        logger.info(f"Transformed {idx}/{len(old_metadata_records)} records...")
                        
                except Exception as e:
                    logger.error(f"Error transforming metadata record {idx}: {e}")
                    transform_errors += 1
            
            logger.info(f"Transformation complete: {len(old_metadata_records)} records → {len(all_metadata_items)} metadata items, {transform_errors} errors")
            
            # Write to new table
            logger.info(f"\n💾 Writing metadata to new table...")
            try:
                metadata_write_success, metadata_write_failure, metadata_write_skipped = batch_write_metadata(
                    dynamodb_client, new_metadata_table_name, all_metadata_items, dry_run
                )
                logger.info(f"Write complete: {metadata_write_success} success, {metadata_write_failure} failures, {metadata_write_skipped} skipped")
            except Exception as e:
                logger.error(f"Failed to write metadata to new table: {e}")
                return 1
            
            # Optional deletion from old table
            if delete_old_data and metadata_write_success > 0:
                logger.info(f"\n🗑️ Deleting metadata from old table...")
                try:
                    keys_to_delete = metadata_record_keys[:len(old_metadata_records)]
                    delete_success, delete_failure = batch_delete_old_metadata(
                        dynamodb_client, old_metadata_table_name, keys_to_delete, dry_run
                    )
                    logger.info(f"Deletion complete: {delete_success} success, {delete_failure} failures")
                except Exception as e:
                    logger.error(f"Failed to delete from old metadata table: {e}")
                    logger.warning("Migration succeeded but cleanup failed. Old data remains in old metadata table.")
            else:
                logger.info("\n⏭️ Skipping deletion (--delete-old-data not specified or no successful writes)")
            
            # Verification
            if not dry_run and metadata_write_success > 0:
                verify_metadata_migration(dynamodb_client, old_metadata_table_name, new_metadata_table_name, 
                                        metadata_write_success, delete_old_data)
    except Exception as e:
        logger.error(f"Metadata migration failed: {e}")
        return 1
    
    #######################
    # PHASE 5.5: METADATA SCHEMA MIGRATION
    #######################
    
    logger.info("\n" + "=" * 80)
    logger.info("PHASE 5.5: METADATA SCHEMA TABLE MIGRATION")
    logger.info("=" * 80)
    
    # Scan old metadata schema table
    logger.info("\n📖 Scanning old metadata schema table...")
    try:
        old_schema_records = scan_old_metadata_schema_table(dynamodb_client, old_metadata_schema_table_name, limit)
        if not old_schema_records:
            logger.warning("No metadata schema records found in old table. Skipping metadata schema migration.")
        else:
            # Group fields by database
            logger.info(f"\n🔄 Grouping {len(old_schema_records)} field records by database...")
            grouped_fields = group_fields_by_database(old_schema_records)
            
            # Process each database
            schema_records_to_write = []
            field_keys_to_delete = []
            transform_errors = 0
            total_deleted_schemas = 0
            
            for database_id, field_records in grouped_fields.items():
                logger.info(f"\nProcessing database: {database_id} ({len(field_records)} fields)")
                
                try:
                    # Delete existing migratedSchema for this database
                    deleted_count = delete_existing_migrated_schemas(
                        dynamodb_client, new_metadata_schema_table_name, database_id, dry_run
                    )
                    total_deleted_schemas += deleted_count
                    
                    # Transform fields to new format
                    transformed_fields = transform_schema_fields_to_new_format(database_id, field_records)
                    
                    if transformed_fields:
                        # Create schema record
                        schema_record = create_metadata_schema_record(database_id, transformed_fields)
                        schema_records_to_write.append(schema_record)
                        
                        # Store keys for potential deletion
                        for record in field_records:
                            field_keys_to_delete.append({
                                'databaseId': record.get('databaseId'),
                                'field': record.get('field')
                            })
                    else:
                        logger.warning(f"No valid fields to migrate for database {database_id}")
                        
                except Exception as e:
                    logger.error(f"Error processing database {database_id}: {e}")
                    transform_errors += 1
            
            logger.info(f"\nTransformation complete: {len(grouped_fields)} databases → {len(schema_records_to_write)} schemas, {transform_errors} errors")
            if total_deleted_schemas > 0:
                logger.info(f"Deleted {total_deleted_schemas} existing 'migratedSchema' records")
            
            # Write to new table
            if schema_records_to_write:
                logger.info(f"\n💾 Writing metadata schemas to new table...")
                try:
                    metadata_schema_write_success, metadata_schema_write_failure, metadata_schema_write_skipped = batch_write_metadata_schemas(
                        dynamodb_client, new_metadata_schema_table_name, schema_records_to_write, dry_run
                    )
                    logger.info(f"Write complete: {metadata_schema_write_success} success, {metadata_schema_write_failure} failures, {metadata_schema_write_skipped} skipped")
                except Exception as e:
                    logger.error(f"Failed to write metadata schemas to new table: {e}")
                    return 1
                
                # Optional deletion from old table
                if delete_old_data and metadata_schema_write_success > 0:
                    logger.info(f"\n🗑️ Deleting field records from old metadata schema table...")
                    try:
                        delete_success, delete_failure = batch_delete_old_metadata_schema_records(
                            dynamodb_client, old_metadata_schema_table_name, field_keys_to_delete, dry_run
                        )
                        logger.info(f"Deletion complete: {delete_success} success, {delete_failure} failures")
                    except Exception as e:
                        logger.error(f"Failed to delete from old metadata schema table: {e}")
                        logger.warning("Migration succeeded but cleanup failed. Old data remains in old metadata schema table.")
                else:
                    logger.info("\n⏭️ Skipping deletion (--delete-old-data not specified or no successful writes)")
                
                # Verification
                if not dry_run and metadata_schema_write_success > 0:
                    verify_metadata_schema_migration(dynamodb_client, old_metadata_schema_table_name, new_metadata_schema_table_name, 
                                                    metadata_schema_write_success, delete_old_data)
            else:
                logger.warning("No metadata schemas to write")
                
    except Exception as e:
        logger.error(f"Metadata schema migration failed: {e}")
        return 1
    
    #######################
    # PHASE 6: OPENSEARCH REINDEX
    #######################
    
    logger.info("\n" + "=" * 80)
    logger.info("PHASE 6: OPENSEARCH REINDEXING")
    logger.info("=" * 80)
    
    if not dry_run:
        logger.info("\n🔍 Invoking reindexer Lambda function...")
        try:
            reindex_result = invoke_reindexer_lambda(
                function_name=reindexer_function_name,
                operation='both',
                dry_run=False,
                limit=limit,
                clear_indexes=clear_indexes_before_reindex,
                profile=profile,
                region=region,
                invocation_type='RequestResponse'
            )
            
            # Check for timeout specifically
            if reindex_result.get('timeout'):
                logger.warning("⏱️ Reindexer Lambda invocation timed out, but the function is still processing in the background.")
                logger.warning(f"Check CloudWatch Logs for Lambda function '{reindexer_function_name}' to monitor progress and verify completion.")
                logger.info("The reindexing operation will continue to run until completion.")
            elif 'error' in reindex_result:
                logger.error(f"Reindexing failed: {reindex_result.get('error')}")
                logger.warning("Migration completed but reindexing failed. You may need to run reindex manually.")
            else:
                logger.info("✅ Reindexing completed successfully")
        except Exception as e:
            logger.error(f"Error during reindexing: {e}")
            logger.warning("Migration completed but reindexing failed. You may need to run reindex manually.")
    else:
        logger.info("\n⏭️ Skipping reindex (dry-run mode)")
        logger.info(f"DRY RUN: Would invoke reindexer Lambda: {reindexer_function_name}")
    
    #######################
    # FINAL SUMMARY
    #######################
    
    migration_end_time = datetime.now(timezone.utc)
    migration_duration = (migration_end_time - migration_start_time).total_seconds()
    
    logger.info("\n" + "=" * 80)
    logger.info("COMPLETE MIGRATION SUMMARY")
    logger.info("=" * 80)
    logger.info(f"Migration Duration: {migration_duration:.1f} seconds")
    logger.info("\nPhase 1-4: Constraints Migration")
    logger.info(f"  Write Success: {constraints_write_success}")
    logger.info(f"  Write Failures: {constraints_write_failure}")
    logger.info(f"  Write Skipped: {constraints_write_skipped}")
    logger.info("\nPhase 5: Metadata Migration")
    logger.info(f"  Write Success: {metadata_write_success}")
    logger.info(f"  Write Failures: {metadata_write_failure}")
    logger.info(f"  Write Skipped: {metadata_write_skipped}")
    logger.info("\nPhase 5.5: Metadata Schema Migration")
    logger.info(f"  Write Success: {metadata_schema_write_success}")
    logger.info(f"  Write Failures: {metadata_schema_write_failure}")
    logger.info(f"  Write Skipped: {metadata_schema_write_skipped}")
    logger.info("\nPhase 6: OpenSearch Reindex")
    logger.info(f"  Status: {'Completed' if not dry_run else 'Skipped (dry-run)'}")
    logger.info(f"\nDry Run: {dry_run}")
    
    # Determine overall status
    total_failures = constraints_write_failure + metadata_write_failure + metadata_schema_write_failure
    if total_failures == 0:
        logger.info(f"Status: ✅ SUCCESS")
        if dry_run:
            logger.info("Note: This was a dry run - no changes were made")
        return 0
    else:
        logger.warning(f"Status: ⚠️ COMPLETED WITH ERRORS")
        return 1


if __name__ == "__main__":
    sys.exit(main())