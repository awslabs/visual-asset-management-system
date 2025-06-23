#!/usr/bin/env python3
# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Data Migration Script for VAMS v2.2 to v2.3

This script performs the following migrations:
1. Copy data from assetsTable to assetVersionsTable
2. Update assetLocation field in assetsTable to use baseAssetsPrefix
3. Add bucketId to assets based on lookup from S3_Asset_Buckets table
4. Add defaultBucketId to all records in the databases table
5. Move version number from currentVersion.Version to currentVersionId
6. Remove specified fields from assetsTable records

Usage:
    python v2.2_to_v2.3_migration.py --profile <aws-profile-name>

Requirements:
    - Python 3.6+
    - boto3
    - AWS credentials with permissions to read/write to DynamoDB tables
"""

import argparse
import boto3
import json
import logging
import os
import sys
import time
from datetime import datetime
from botocore.exceptions import ClientError

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# =====================================================================
# CONFIGURATION SECTION
# =====================================================================

# Default configuration - can be overridden by config file or command line arguments
CONFIG = {
    # Source tables
    "assets_table_name": "YOUR_ASSETS_TABLE_NAME",
    "asset_versions_table_name": "YOUR_ASSET_VERSIONS_TABLE_NAME",
    "s3_asset_buckets_table_name": "YOUR_S3_ASSET_BUCKETS_TABLE_NAME",
    "databases_table_name": "YOUR_DATABASES_TABLE_NAME",
    
    # Asset configuration
    "base_assets_prefix": "YOUR_BASE_ASSETS_PREFIX",
    "asset_bucket_name": "YOUR_ASSET_BUCKET_NAME",
    
    # AWS settings
    "aws_profile": None,
    "aws_region": None,
    
    # Migration settings
    "log_level": "INFO",
    "batch_size": 25,
    "dry_run": False
}

def load_config_from_file(config_file):
    """
    Load configuration from a JSON file.
    
    Args:
        config_file (str): Path to the configuration file
        
    Returns:
        dict: Configuration dictionary
    """
    try:
        with open(config_file, 'r') as f:
            config = json.load(f)
            
        # Remove comments if present
        if 'comments' in config:
            del config['comments']
            
        return config
    except Exception as e:
        logger.error(f"Error loading configuration from {config_file}: {e}")
        return {}

# Fields to remove from assets table records
FIELDS_TO_REMOVE = [
    "isMultiFile", 
    "pipelineId", 
    "executionId", 
    "versions", 
    "currentVersion", 
    "specifiedPipelines", 
    "Parent", 
    "objectFamily"
]

# =====================================================================
# HELPER FUNCTIONS
# =====================================================================

def get_dynamodb_client(profile_name=None, region=None):
    """
    Create a boto3 DynamoDB client with the specified profile and region.
    
    Args:
        profile_name (str, optional): AWS profile name to use
        region (str, optional): AWS region to use
        
    Returns:
        boto3.client: DynamoDB client
    """
    session = boto3.Session(profile_name=profile_name, region_name=region)
    return session.client('dynamodb')

def get_dynamodb_resource(profile_name=None, region=None):
    """
    Create a boto3 DynamoDB resource with the specified profile and region.
    
    Args:
        profile_name (str, optional): AWS profile name to use
        region (str, optional): AWS region to use
        
    Returns:
        boto3.resource: DynamoDB resource
    """
    session = boto3.Session(profile_name=profile_name, region_name=region)
    return session.resource('dynamodb')

def scan_table(dynamodb, table_name, limit=None):
    """
    Scan a DynamoDB table and return all items.
    
    Args:
        dynamodb: DynamoDB resource
        table_name (str): Name of the table to scan
        limit (int, optional): Maximum number of items to return
        
    Returns:
        list: List of items from the table
    """
    table = dynamodb.Table(table_name)
    items = []
    
    try:
        if limit:
            response = table.scan(Limit=limit)
        else:
            response = table.scan()
            
        items.extend(response.get('Items', []))
        
        # Paginate through results if necessary
        while 'LastEvaluatedKey' in response:
            if limit and len(items) >= limit:
                break
                
            response = table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
            items.extend(response.get('Items', []))
            
        return items
    except ClientError as e:
        logger.error(f"Error scanning table {table_name}: {e}")
        raise

def put_item(dynamodb, table_name, item):
    """
    Put an item into a DynamoDB table.
    
    Args:
        dynamodb: DynamoDB resource
        table_name (str): Name of the table
        item (dict): Item to put into the table
        
    Returns:
        dict: Response from DynamoDB
    """
    table = dynamodb.Table(table_name)
    
    try:
        response = table.put_item(Item=item)
        return response
    except ClientError as e:
        logger.error(f"Error putting item into table {table_name}: {e}")
        raise

def update_item(dynamodb, table_name, key, update_expression, expression_attribute_values, expression_attribute_names=None):
    """
    Update an item in a DynamoDB table.
    
    Args:
        dynamodb: DynamoDB resource
        table_name (str): Name of the table
        key (dict): Primary key of the item to update
        update_expression (str): Update expression
        expression_attribute_values (dict): Expression attribute values
        expression_attribute_names (dict, optional): Expression attribute names
        
    Returns:
        dict: Response from DynamoDB
    """
    table = dynamodb.Table(table_name)
    
    try:
        params = {
            'Key': key,
            'UpdateExpression': update_expression,
            'ExpressionAttributeValues': expression_attribute_values
        }
        
        if expression_attribute_names:
            params['ExpressionAttributeNames'] = expression_attribute_names
            
        response = table.update_item(**params)
        return response
    except ClientError as e:
        logger.error(f"Error updating item in table {table_name}: {e}")
        raise

def query_bucket_id(dynamodb, s3_asset_buckets_table_name, asset_bucket_name, base_assets_prefix):
    """
    Query the S3_Asset_Buckets table to get the bucketId.
    
    Args:
        dynamodb: DynamoDB resource
        s3_asset_buckets_table_name (str): Name of the S3_Asset_Buckets table
        asset_bucket_name (str): Asset bucket name to look up
        base_assets_prefix (str): Base assets prefix to look up
        
    Returns:
        str: The bucketId if found, None otherwise
    """
    table = dynamodb.Table(s3_asset_buckets_table_name)
    
    try:
        # Query using the bucketNameGSI index
        response = table.query(
            IndexName='bucketNameGSI',
            KeyConditionExpression='bucketName = :bucketName AND baseAssetsPrefix = :baseAssetsPrefix',
            ExpressionAttributeValues={
                ':bucketName': asset_bucket_name,
                ':baseAssetsPrefix': base_assets_prefix
            }
        )
        
        items = response.get('Items', [])
        
        if not items:
            logger.warning(f"No bucket found with bucketName={asset_bucket_name} and baseAssetsPrefix={base_assets_prefix}")
            return None
        
        bucket_id = items[0].get('bucketId')
        
        if not bucket_id:
            logger.warning(f"Found bucket record but it does not have a bucketId field")
            return None
        
        return bucket_id
    except ClientError as e:
        logger.error(f"Error querying S3_Asset_Buckets table: {e}")
        return None

# =====================================================================
# MIGRATION FUNCTIONS
# =====================================================================

def create_asset_version_record(asset):
    """
    Create an asset version record from an asset record.
    
    Args:
        asset (dict): Asset record from the assets table
        
    Returns:
        dict: Asset version record for the asset versions table
    """
    # Check if currentVersion exists
    if 'currentVersion' not in asset:
        logger.warning(f"Asset {asset.get('assetId')} does not have a currentVersion field")
        return None
    
    current_version = asset['currentVersion']
    asset_id = asset.get('assetId')
    
    # Map fields from currentVersion to the new record
    version_record = {
        'assetId': asset_id,
        'assetVersionId': current_version.get('Version', '0'),
        'isCurrentVersion': True,
        'dateCreated': current_version.get('DateModified', datetime.now().isoformat()),
        'comment': current_version.get('Comment', f'Initial asset creation - Version {current_version.get("Version", "0")}'),
        'description': current_version.get('description', ''),
        'createdBy': 'system',
        'specifiedPipelines': current_version.get('specifiedPipelines', []),
    }
    
    return version_record

def update_asset_location(asset, base_assets_prefix, bucket_id):
    """
    Update the assetLocation field in an asset record.
    
    Args:
        asset (dict): Asset record from the assets table
        base_assets_prefix (str): Base assets prefix to use in the Key
        bucket_id (str): Bucket ID to add to the asset record
        
    Returns:
        dict: Updated asset record
    """
    # Make a copy of the asset to avoid modifying the original
    updated_asset = asset.copy()
    
    # Update assetLocation if it exists
    if 'assetLocation' in updated_asset:
        asset_id = updated_asset.get('assetId')
        
        # Transform assetLocation to use baseAssetsPrefix
        updated_asset['assetLocation'] = {
            'Key': f"{base_assets_prefix}{asset_id}/"
        }
    
    # Add bucketId to the asset record
    if bucket_id:
        updated_asset['bucketId'] = bucket_id
    
    # Add currentVersionId if currentVersion exists
    if 'currentVersion' in updated_asset and 'Version' in updated_asset['currentVersion']:
        updated_asset['currentVersionId'] = updated_asset['currentVersion']['Version']
    
    # Remove specified fields
    for field in FIELDS_TO_REMOVE:
        if field in updated_asset:
            del updated_asset[field]
    
    return updated_asset

def update_database_records(dynamodb, databases_table_name, bucket_id, limit=None):
    """
    Update all records in the databases table to add defaultBucketId.
    
    Args:
        dynamodb: DynamoDB resource
        databases_table_name (str): Name of the databases table
        bucket_id (str): Bucket ID to add as defaultBucketId
        limit (int, optional): Maximum number of databases to process
        
    Returns:
        tuple: (success_count, error_count)
    """
    logger.info(f"Starting update of database records in {databases_table_name}")
    
    # Get all databases
    databases = scan_table(dynamodb, databases_table_name, limit)
    logger.info(f"Found {len(databases)} databases to update")
    
    success_count = 0
    error_count = 0
    
    # Process each database
    for database in databases:
        database_id = database.get('databaseId')
        
        if not database_id:
            error_count += 1
            logger.warning(f"Skipped updating database - missing databaseId: {database}")
            continue
        
        try:
            # Add defaultBucketId to the database record
            updated_database = database.copy()
            updated_database['defaultBucketId'] = bucket_id
            
            # Put the updated database back into the databases table
            put_item(dynamodb, databases_table_name, updated_database)
            success_count += 1
            logger.info(f"Successfully updated database {database_id}")
        except Exception as e:
            error_count += 1
            logger.error(f"Error updating database {database_id}: {e}")
    
    logger.info(f"Completed update of database records: {success_count} successful, {error_count} errors")
    return success_count, error_count

def migrate_asset_versions(dynamodb, assets_table_name, asset_versions_table_name, limit=None):
    """
    Migrate asset versions from assets table to asset versions table.
    
    Args:
        dynamodb: DynamoDB resource
        assets_table_name (str): Name of the assets table
        asset_versions_table_name (str): Name of the asset versions table
        limit (int, optional): Maximum number of assets to process
        
    Returns:
        tuple: (success_count, error_count)
    """
    logger.info(f"Starting migration of asset versions from {assets_table_name} to {asset_versions_table_name}")
    
    # Get all assets
    assets = scan_table(dynamodb, assets_table_name, limit)
    logger.info(f"Found {len(assets)} assets to process")
    
    success_count = 0
    error_count = 0
    
    # Process each asset
    for asset in assets:
        asset_id = asset.get('assetId')
        
        try:
            # Create asset version record
            version_record = create_asset_version_record(asset)
            
            if version_record:
                # Put the version record into the asset versions table
                put_item(dynamodb, asset_versions_table_name, version_record)
                success_count += 1
                logger.info(f"Successfully created version record for asset {asset_id}")
            else:
                error_count += 1
                logger.warning(f"Skipped creating version record for asset {asset_id} - no currentVersion field")
        except Exception as e:
            error_count += 1
            logger.error(f"Error creating version record for asset {asset_id}: {e}")
    
    logger.info(f"Completed migration of asset versions: {success_count} successful, {error_count} errors")
    return success_count, error_count

def update_asset_records(dynamodb, assets_table_name, s3_asset_buckets_table_name, asset_bucket_name, base_assets_prefix, limit=None):
    """
    Update asset records in the assets table.
    
    Args:
        dynamodb: DynamoDB resource
        assets_table_name (str): Name of the assets table
        s3_asset_buckets_table_name (str): Name of the S3_Asset_Buckets table
        asset_bucket_name (str): Name of the asset bucket to use for lookup
        base_assets_prefix (str): Base assets prefix to use in the Key
        limit (int, optional): Maximum number of assets to process
        
    Returns:
        tuple: (success_count, error_count, bucket_id)
    """
    logger.info(f"Starting update of asset records in {assets_table_name}")
    
    # Get all assets
    assets = scan_table(dynamodb, assets_table_name, limit)
    logger.info(f"Found {len(assets)} assets to update")
    
    # Look up the bucket ID once
    bucket_id = query_bucket_id(dynamodb, s3_asset_buckets_table_name, asset_bucket_name, base_assets_prefix)
    if bucket_id:
        logger.info(f"Found bucket ID {bucket_id} for bucket {asset_bucket_name} with prefix {base_assets_prefix}")
    else:
        logger.warning(f"Could not find bucket ID for bucket {asset_bucket_name} with prefix {base_assets_prefix}")
    
    success_count = 0
    error_count = 0
    
    # Process each asset
    for asset in assets:
        asset_id = asset.get('assetId')
        database_id = asset.get('databaseId')
        
        if not asset_id or not database_id:
            error_count += 1
            logger.warning(f"Skipped updating asset - missing assetId or databaseId: {asset}")
            continue
        
        try:
            # Update asset record
            updated_asset = update_asset_location(asset, base_assets_prefix, bucket_id)
            
            # Put the updated asset back into the assets table
            put_item(dynamodb, assets_table_name, updated_asset)
            success_count += 1
            logger.info(f"Successfully updated asset {asset_id}")
        except Exception as e:
            error_count += 1
            logger.error(f"Error updating asset {asset_id}: {e}")
    
    logger.info(f"Completed update of asset records: {success_count} successful, {error_count} errors")
    return success_count, error_count, bucket_id

# =====================================================================
# MAIN FUNCTION
# =====================================================================

def main():
    """Main function to run the migration."""
    parser = argparse.ArgumentParser(description='VAMS v2.2 to v2.3 Data Migration Script')
    parser.add_argument('--profile', help='AWS profile name to use')
    parser.add_argument('--region', help='AWS region to use')
    parser.add_argument('--limit', type=int, help='Maximum number of assets to process')
    parser.add_argument('--assets-table', help='Name of the assets table')
    parser.add_argument('--asset-versions-table', help='Name of the asset versions table')
    parser.add_argument('--s3-asset-buckets-table', help='Name of the S3_Asset_Buckets table')
    parser.add_argument('--databases-table', help='Name of the databases table')
    parser.add_argument('--base-assets-prefix', help='Base assets prefix to use in assetLocation.Key')
    parser.add_argument('--asset-bucket-name', help='Name of the asset bucket to use for lookup')
    parser.add_argument('--dry-run', action='store_true', help='Perform a dry run without making changes')
    parser.add_argument('--config', help='Path to configuration file')
    parser.add_argument('--log-level', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'], 
                        help='Logging level')
    parser.add_argument('--batch-size', type=int, help='Number of items to process in each batch')
    
    args = parser.parse_args()
    
    # Load configuration from file if provided
    if args.config:
        file_config = load_config_from_file(args.config)
        CONFIG.update(file_config)
    
    # Update configuration with command line arguments (these override file config)
    if args.assets_table:
        CONFIG['assets_table_name'] = args.assets_table
    if args.asset_versions_table:
        CONFIG['asset_versions_table_name'] = args.asset_versions_table
    if args.s3_asset_buckets_table:
        CONFIG['s3_asset_buckets_table_name'] = args.s3_asset_buckets_table
    if args.databases_table:
        CONFIG['databases_table_name'] = args.databases_table
    if args.base_assets_prefix:
        CONFIG['base_assets_prefix'] = args.base_assets_prefix
    if args.asset_bucket_name:
        CONFIG['asset_bucket_name'] = args.asset_bucket_name
    if args.profile:
        CONFIG['aws_profile'] = args.profile
    if args.region:
        CONFIG['aws_region'] = args.region
    if args.dry_run:
        CONFIG['dry_run'] = True
    if args.log_level:
        CONFIG['log_level'] = args.log_level
    if args.batch_size:
        CONFIG['batch_size'] = args.batch_size
        
    # Set logging level
    logging.getLogger().setLevel(getattr(logging, CONFIG.get('log_level', 'INFO')))
    
    # Validate configuration
    if CONFIG['assets_table_name'] == 'YOUR_ASSETS_TABLE_NAME':
        logger.error("Please set the assets_table_name in the CONFIG or provide it with --assets-table")
        return 1
    
    if CONFIG['asset_versions_table_name'] == 'YOUR_ASSET_VERSIONS_TABLE_NAME':
        logger.error("Please set the asset_versions_table_name in the CONFIG or provide it with --asset-versions-table")
        return 1
    
    if CONFIG['s3_asset_buckets_table_name'] == 'YOUR_S3_ASSET_BUCKETS_TABLE_NAME':
        logger.error("Please set the s3_asset_buckets_table_name in the CONFIG or provide it with --s3-asset-buckets-table")
        return 1
    
    if CONFIG['databases_table_name'] == 'YOUR_DATABASES_TABLE_NAME':
        logger.error("Please set the databases_table_name in the CONFIG or provide it with --databases-table")
        return 1
    
    if CONFIG['base_assets_prefix'] == 'YOUR_BASE_ASSETS_PREFIX':
        logger.error("Please set the base_assets_prefix in the CONFIG or provide it with --base-assets-prefix")
        return 1
    
    if CONFIG['asset_bucket_name'] == 'YOUR_ASSET_BUCKET_NAME':
        logger.error("Please set the asset_bucket_name in the CONFIG or provide it with --asset-bucket-name")
        return 1
    
    # Initialize DynamoDB client
    try:
        dynamodb = get_dynamodb_resource(profile_name=CONFIG.get('aws_profile'), region=CONFIG.get('aws_region'))
    except Exception as e:
        logger.error(f"Error initializing DynamoDB client: {e}")
        return 1
    
    logger.info("Starting VAMS v2.2 to v2.3 data migration")
    logger.info(f"Configuration: {json.dumps(CONFIG, indent=2)}")
    
    if CONFIG.get('dry_run', False):
        logger.info("DRY RUN MODE - No changes will be made")
        return 0
    
    # Perform the migration
    try:
        # Step 1: Migrate asset versions
        version_success, version_errors = migrate_asset_versions(
            dynamodb, 
            CONFIG['assets_table_name'], 
            CONFIG['asset_versions_table_name'],
            args.limit if args.limit else None
        )
        
        # Step 2: Update asset records and get bucket ID
        asset_success, asset_errors, bucket_id = update_asset_records(
            dynamodb, 
            CONFIG['assets_table_name'],
            CONFIG['s3_asset_buckets_table_name'],
            CONFIG['asset_bucket_name'],
            CONFIG['base_assets_prefix'],
            args.limit if args.limit else None
        )
        
        # Step 3: Update database records with the bucket ID
        if bucket_id:
            db_success, db_errors = update_database_records(
                dynamodb,
                CONFIG['databases_table_name'],
                bucket_id,
                args.limit if args.limit else None
            )
        else:
            logger.error("Could not find bucket ID, skipping database updates")
            db_success = 0
            db_errors = 0
        
        # Print summary
        logger.info("Migration completed")
        logger.info(f"Asset versions migration: {version_success} successful, {version_errors} errors")
        logger.info(f"Asset records update: {asset_success} successful, {asset_errors} errors")
        logger.info(f"Database records update: {db_success} successful, {db_errors} errors")
        
        if version_errors > 0 or asset_errors > 0 or db_errors > 0:
            logger.warning("Migration completed with errors - check the logs for details")
            return 1
        else:
            logger.info("Migration completed successfully")
            return 0
    except Exception as e:
        logger.error(f"Error during migration: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
