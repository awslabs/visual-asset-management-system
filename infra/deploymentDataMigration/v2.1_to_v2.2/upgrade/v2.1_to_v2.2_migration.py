#!/usr/bin/env python3
# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Data Migration Script for VAMS v2.1 to v2.2

This script performs the following migrations:
1. Copy data from assetsTable to assetVersionsTable
2. Update assetLocation field in assetsTable to use baseAssetsPrefix
3. Add bucketId to assets based on lookup from S3_Asset_Buckets table
4. Add defaultBucketId to all records in the databases table
5. Move version number from currentVersion.Version to currentVersionId
6. Remove specified fields from assetsTable records
7. Migrate comments to reference current asset versions (optional)
8. Migrate assetLinks table to new schema with database IDs

Usage:
    python v2.1_to_v2.2_migration.py --profile <aws-profile-name>

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
    "comment_storage_table_name": "YOUR_COMMENT_STORAGE_TABLE_NAME",
    "asset_links_table_name": "YOUR_ASSET_LINKS_TABLE_NAME",
    "asset_links_table_v2_name": "YOUR_ASSET_LINKS_TABLE_V2_NAME",
    
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
    If no profile is provided, use the current environment's AWS access credentials.
    
    Args:
        profile_name (str, optional): AWS profile name to use
        region (str, optional): AWS region to use
        
    Returns:
        boto3.client: DynamoDB client
    """
    session_args = {}
    if profile_name:
        session_args['profile_name'] = profile_name
    if region:
        session_args['region_name'] = region
        
    session = boto3.Session(**session_args)
    return session.client('dynamodb')

def get_dynamodb_resource(profile_name=None, region=None):
    """
    Create a boto3 DynamoDB resource with the specified profile and region.
    If no profile is provided, use the current environment's AWS access credentials.
    
    Args:
        profile_name (str, optional): AWS profile name to use
        region (str, optional): AWS region to use
        
    Returns:
        boto3.resource: DynamoDB resource
    """
    session_args = {}
    if profile_name:
        session_args['profile_name'] = profile_name
    if region:
        session_args['region_name'] = region
        
    session = boto3.Session(**session_args)
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
    asset_id = asset.get('assetId')
    
    # Case 1: If currentVersion exists, use that (original behavior)
    if 'currentVersion' in asset:
        current_version = asset['currentVersion']
        
        version_record = {
            'assetId': asset_id,
            'assetVersionId': current_version.get('Version', '0'),
            'isCurrentVersion': True,
            'dateCreated': datetime.now().isoformat(),
            'comment': current_version.get('Comment', f'Asset migration - Version {current_version.get("Version", "0")}'),
            'description': current_version.get('description', ''),
            'createdBy': current_version.get('CreatedBy', 'system'),
            'specifiedPipelines': current_version.get('specifiedPipelines', []),
        }
        
        return version_record
    
    # Case 2: If versions array exists and has records, use the top record
    elif 'versions' in asset and asset['versions'] and len(asset['versions']) > 0:
        top_version = asset['versions'][0]  # Get the first version in the array
        
        version_record = {
            'assetId': asset_id,
            'assetVersionId': top_version.get('Version', '0'),
            'isCurrentVersion': True,
            'dateCreated': datetime.now().isoformat(),
            'comment': top_version.get('Comment', f'Asset migration'),
            'description': top_version.get('description', ''),
            'createdBy': top_version.get('CreatedBy', 'system'),
            'specifiedPipelines': top_version.get('specifiedPipelines', []),
        }
        
        return version_record
    
    # Case 3: If neither currentVersion nor versions array has usable data,
    # create a new version "0" with current date and system-migration user
    else:
        logger.warning(f"Asset {asset_id} has no version information - creating default version")
        
        version_record = {
            'assetId': asset_id,
            'assetVersionId': '0',
            'isCurrentVersion': True,
            'dateCreated': datetime.now().isoformat(),
            'comment': 'Initial asset creation - Migration generated version',
            'description': '',
            'createdBy': 'system-migration',
            'specifiedPipelines': [],
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

        finalKey =f"{base_assets_prefix}{asset_id}/"

        #remove any starting slashes from the finalkey
        if(finalKey.startswith('/')):
            finalKey = finalKey[1:]

        #add forward slash as end if not exists. 
        if(not finalKey.endswith('/')):
            finalKey = finalKey + '/'

        #if only a slash, make it empty
        if (finalKey == '/'):
            finalKey = ''
        
        # Transform assetLocation to use baseAssetsPrefix
        updated_asset['assetLocation'] = {
            'Key': finalKey
        }
    
    # Add bucketId to the asset record
    if bucket_id:
        updated_asset['bucketId'] = bucket_id
    
    # Add currentVersionId based on available version information
    if 'currentVersion' in updated_asset and 'Version' in updated_asset['currentVersion']:
        # Case 1: Use Version from currentVersion
        updated_asset['currentVersionId'] = updated_asset['currentVersion']['Version']
    elif 'versions' in updated_asset and updated_asset['versions'] and len(updated_asset['versions']) > 0:
        # Case 2: Use Version from the first record in versions array
        updated_asset['currentVersionId'] = updated_asset['versions'][0].get('Version', '0')
    else:
        # Case 3: Default to '0' if no version information is available
        updated_asset['currentVersionId'] = '0'
    
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
            
            # Put the version record into the asset versions table
            put_item(dynamodb, asset_versions_table_name, version_record)
            success_count += 1
            logger.info(f"Successfully created version record for asset {asset_id}")
        except Exception as e:
            error_count += 1
            logger.error(f"Error creating version record for asset {asset_id}: {e}")
    
    logger.info(f"Completed migration of asset versions: {success_count} successful, {error_count} errors")
    return success_count, error_count

def batch_get_asset_database_ids(dynamodb, assets_table_name, asset_ids):
    """
    Batch get database IDs for a list of asset IDs.
    
    Args:
        dynamodb: DynamoDB resource
        assets_table_name (str): Name of the assets table
        asset_ids (list): List of asset IDs to look up
        
    Returns:
        dict: Dictionary mapping asset IDs to their database IDs
    """
    asset_database_map = {}
    
    # Remove duplicates
    unique_asset_ids = list(set(asset_ids))
    
    # Process in batches of 100 (DynamoDB batch_get_item limit)
    batch_size = 100
    for i in range(0, len(unique_asset_ids), batch_size):
        batch = unique_asset_ids[i:i + batch_size]
        
        try:
            # For each asset ID, we need to scan the table since we don't know the database ID
            # This is inefficient but necessary for this migration
            assets_table = dynamodb.Table(assets_table_name)
            
            for asset_id in batch:
                # Query for this asset ID across all databases
                response = assets_table.scan(
                    FilterExpression=boto3.dynamodb.conditions.Attr('assetId').eq(asset_id)
                )
                
                items = response.get('Items', [])
                
                if items:
                    # Use the first matching item's database ID
                    asset_database_map[asset_id] = items[0].get('databaseId')
                    
                # Handle pagination if necessary
                while 'LastEvaluatedKey' in response:
                    response = assets_table.scan(
                        FilterExpression=boto3.dynamodb.conditions.Attr('assetId').eq(asset_id),
                        ExclusiveStartKey=response['LastEvaluatedKey']
                    )
                    
                    items = response.get('Items', [])
                    
                    if items and asset_id not in asset_database_map:
                        asset_database_map[asset_id] = items[0].get('databaseId')
                        break
                
        except Exception as e:
            logger.exception(f"Error in batch get asset database IDs: {e}")
    
    return asset_database_map

def migrate_asset_links(dynamodb, asset_links_table_name, asset_links_table_v2_name, assets_table_name, limit=None):
    """
    Migrate asset links from old table to new table with updated schema.
    
    For each asset link:
    1. Extract relationId, assetIdfrom, assetIdto, and relationshipType
    2. Look up database IDs for both assets
    3. Create new record with transformed schema
    4. Insert into new asset links table
    
    Args:
        dynamodb: DynamoDB resource
        asset_links_table_name (str): Name of the old asset links table
        asset_links_table_v2_name (str): Name of the new asset links v2 table
        assets_table_name (str): Name of the assets table for database ID lookups
        limit (int, optional): Maximum number of links to process
        
    Returns:
        tuple: (success_count, error_count)
    """
    logger.info(f"Starting migration of asset links from {asset_links_table_name} to {asset_links_table_v2_name}")
    
    try:
        # Get all asset links from the old table
        asset_links_table = dynamodb.Table(asset_links_table_name)
        asset_links_table_v2 = dynamodb.Table(asset_links_table_v2_name)
        
        # Scan the old asset links table
        if limit:
            response = asset_links_table.scan(Limit=limit)
        else:
            response = asset_links_table.scan()
            
        links = response.get('Items', [])
        
        # Handle pagination if necessary
        while 'LastEvaluatedKey' in response:
            if limit and len(links) >= limit:
                break
                
            response = asset_links_table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
            links.extend(response.get('Items', []))
        
        logger.info(f"Found {len(links)} asset links to migrate")
        
        # Collect all asset IDs for batch lookup
        from_asset_ids = [link.get('assetIdFrom') for link in links if 'assetIdFrom' in link]
        to_asset_ids = [link.get('assetIdTo') for link in links if 'assetIdTo' in link]
        all_asset_ids = from_asset_ids + to_asset_ids
        
        # Batch get database IDs for all assets
        logger.info(f"Looking up database IDs for {len(all_asset_ids)} assets")
        asset_database_map = batch_get_asset_database_ids(dynamodb, assets_table_name, all_asset_ids)
        logger.info(f"Found database IDs for {len(asset_database_map)} assets")
        
        success_count = 0
        error_count = 0
        
        # Process each asset link
        for link in links:
            relation_id = link.get('relationId')
            from_asset_id = link.get('assetIdFrom')
            to_asset_id = link.get('assetIdTo')
            relationship_type = link.get('relationshipType', 'related')
            
            if not relation_id or not from_asset_id or not to_asset_id:
                error_count += 1
                logger.warning(f"Skipped migrating link - missing required fields: {link}")
                continue
            
            try:
                # Look up database IDs
                from_database_id = asset_database_map.get(from_asset_id)
                to_database_id = asset_database_map.get(to_asset_id)
                
                if not from_database_id or not to_database_id:
                    error_count += 1
                    logger.warning(f"Skipped migrating link {relation_id} - could not find database IDs for assets")
                    continue
                
                # Transform relationship type
                if relationship_type in ['parent', 'child']:
                    new_relationship_type = 'parentChild'
                else:
                    new_relationship_type = relationship_type
                
                # Create new asset link record
                new_link = {
                    'assetLinkId': relation_id,
                    'fromAssetDatabaseId:fromAssetId': f"{from_database_id}:{from_asset_id}",
                    'fromAssetDatabaseId': from_database_id,
                    'fromAssetId': from_asset_id,
                    'toAssetDatabaseId:toAssetId': f"{to_database_id}:{to_asset_id}",
                    'toAssetDatabaseId': to_database_id,
                    'toAssetId': to_asset_id,
                    'relationshipType': new_relationship_type,
                    'tags': []
                }
                
                # Put the new link into the v2 table
                asset_links_table_v2.put_item(Item=new_link)
                success_count += 1
                logger.info(f"Successfully migrated asset link {relation_id}")
                
            except Exception as e:
                error_count += 1
                logger.error(f"Error migrating asset link {relation_id}: {e}")
        
        logger.info(f"Completed migration of asset links: {success_count} successful, {error_count} errors")
        return success_count, error_count
        
    except Exception as e:
        logger.exception(f"Error in asset links migration: {e}")
        return 0, 0

def migrate_comments(dynamodb, comments_table_name, assets_table_name, limit=None):
    """
    Migrate comments to ensure they reference the current version of assets.
    
    For each comment:
    1. Extract assetId and the composite sort key (assetVersionId:commentId)
    2. Find the corresponding asset record from a scan of the assets table
    3. If assetVersionId in comment doesn't match currentVersionId of asset:
       - Create new comment with updated sort key using asset's currentVersionId
       - Insert new comment and delete old one
    
    Args:
        dynamodb: DynamoDB resource
        comments_table_name (str): Name of the comments table
        assets_table_name (str): Name of the assets table
        limit (int, optional): Maximum number of comments to process
        
    Returns:
        tuple: (success_count, error_count, migrated_count)
    """
    logger.info(f"Starting migration of comments in {comments_table_name}")
    
    # Get all comments
    comments = scan_table(dynamodb, comments_table_name, limit)
    logger.info(f"Found {len(comments)} comments to process")
    
    # Scan the assets table once and store the results in memory
    logger.info(f"Scanning assets table {assets_table_name} to build asset lookup")
    assets = scan_table(dynamodb, assets_table_name)
    logger.info(f"Found {len(assets)} assets in the scan")
    
    # Create a dictionary for quick asset lookup by assetId
    asset_lookup = {asset.get('assetId'): asset for asset in assets if asset.get('assetId')}
    logger.info(f"Created asset lookup with {len(asset_lookup)} entries")
    
    success_count = 0
    error_count = 0
    migrated_count = 0
    
    # Process each comment
    for comment in comments:
        asset_id = comment.get('assetId')
        sort_key = comment.get('assetVersionId:commentId')
        
        if not asset_id or not sort_key:
            error_count += 1
            logger.warning(f"Skipped migrating comment - missing assetId or assetVersionId:commentId: {comment}")
            continue
        
        try:
            # Split the sort key to get assetVersionId and commentId
            try:
                asset_version_id, comment_id = sort_key.split(':', 1)
            except ValueError:
                error_count += 1
                logger.warning(f"Skipped migrating comment - invalid sort key format: {sort_key}")
                continue
            
            # Find the asset in our lookup dictionary
            asset = asset_lookup.get(asset_id)
            
            if not asset:
                error_count += 1
                logger.warning(f"Skipped migrating comment - asset {asset_id} not found in asset lookup")
                continue
            
            # Check if the asset has currentVersionId
            if 'currentVersionId' not in asset:
                error_count += 1
                logger.warning(f"Skipped migrating comment - asset {asset_id} does not have currentVersionId")
                continue
            
            current_version_id = asset['currentVersionId']
            
            # Check if the assetVersionId from the comment matches the currentVersionId of the asset
            if asset_version_id == current_version_id:
                # No migration needed
                success_count += 1
                logger.info(f"Comment for asset {asset_id} with version {asset_version_id} is already current")
                continue
            
            # Create a new comment record with updated sort key
            new_comment = comment.copy()
            new_comment['assetVersionId:commentId'] = f"{current_version_id}:{comment_id}"
            
            # Put the new comment into the table
            comments_table = dynamodb.Table(comments_table_name)
            comments_table.put_item(Item=new_comment)
            
            # Delete the old comment
            comments_table.delete_item(
                Key={
                    'assetId': asset_id,
                    'assetVersionId:commentId': sort_key
                }
            )
            
            migrated_count += 1
            success_count += 1
            logger.info(f"Successfully migrated comment for asset {asset_id} from version {asset_version_id} to {current_version_id}")
            
        except Exception as e:
            error_count += 1
            logger.error(f"Error migrating comment for asset {asset_id}: {e}")
    
    logger.info(f"Completed migration of comments: {success_count} successful, {error_count} errors, {migrated_count} comments migrated")
    return success_count, error_count, migrated_count

def update_asset_records(dynamodb, assets_table_name, bucket_id, base_assets_prefix, limit=None):
    """
    Update asset records in the assets table.
    
    Args:
        dynamodb: DynamoDB resource
        assets_table_name (str): Name of the assets table
        bucket_id (str): Bucket ID to add to the asset records
        base_assets_prefix (str): Base assets prefix to use in the Key
        limit (int, optional): Maximum number of assets to process
        
    Returns:
        tuple: (success_count, error_count)
    """
    logger.info(f"Starting update of asset records in {assets_table_name}")
    
    # Get all assets
    assets = scan_table(dynamodb, assets_table_name, limit)
    logger.info(f"Found {len(assets)} assets to update")
    
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
    return success_count, error_count

# =====================================================================
# MAIN FUNCTION
# =====================================================================

def main():
    """Main function to run the migration."""
    parser = argparse.ArgumentParser(description='VAMS v2.1 to v2.2 Data Migration Script')
    parser.add_argument('--profile', help='AWS profile name to use')
    parser.add_argument('--region', help='AWS region to use')
    parser.add_argument('--limit', type=int, help='Maximum number of assets to process')
    parser.add_argument('--assets-table', help='Name of the assets table')
    parser.add_argument('--asset-versions-table', help='Name of the asset versions table')
    parser.add_argument('--s3-asset-buckets-table', help='Name of the S3_Asset_Buckets table')
    parser.add_argument('--databases-table', help='Name of the databases table')
    parser.add_argument('--comments-table', help='Name of the comments table for migrating comments')
    parser.add_argument('--asset-links-table', help='Name of the old asset links table')
    parser.add_argument('--asset-links-table-v2', help='Name of the new asset links v2 table')
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
    if args.comments_table:
        CONFIG['comment_storage_table_name'] = args.comments_table
    if args.asset_links_table:
        CONFIG['asset_links_table_name'] = args.asset_links_table
    if args.asset_links_table_v2:
        CONFIG['asset_links_table_v2_name'] = args.asset_links_table_v2
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
    
    logger.info("Starting VAMS v2.1 to v2.2 data migration")
    logger.info(f"Configuration: {json.dumps(CONFIG, indent=2)}")
    
    if CONFIG.get('dry_run', False):
        logger.info("DRY RUN MODE - No changes will be made")
        return 0
    
    # Perform the migration
    try:
        # Step 0: Look up the bucket ID - this is critical for the migration
        logger.info(f"Looking up bucket ID for bucket {CONFIG['asset_bucket_name']} with prefix {CONFIG['base_assets_prefix']}")
        bucket_id = query_bucket_id(
            dynamodb, 
            CONFIG['s3_asset_buckets_table_name'], 
            CONFIG['asset_bucket_name'], 
            CONFIG['base_assets_prefix']
        )
        
        if not bucket_id:
            logger.error(f"Could not find bucket ID for bucket {CONFIG['asset_bucket_name']} with prefix {CONFIG['base_assets_prefix']}")
            logger.error("Migration cannot proceed without a valid bucket ID")
            return 1
            
        logger.info(f"Found bucket ID {bucket_id} - proceeding with migration")
        
        # Step 1: Migrate asset versions
        version_success, version_errors = migrate_asset_versions(
            dynamodb, 
            CONFIG['assets_table_name'], 
            CONFIG['asset_versions_table_name'],
            args.limit if args.limit else None
        )
        
        # Step 2: Update asset records with the bucket ID
        asset_success, asset_errors = update_asset_records(
            dynamodb, 
            CONFIG['assets_table_name'],
            bucket_id,
            CONFIG['base_assets_prefix'],
            args.limit if args.limit else None
        )
        
        # Step 3: Update database records with the bucket ID
        db_success, db_errors = update_database_records(
            dynamodb,
            CONFIG['databases_table_name'],
            bucket_id,
            args.limit if args.limit else None
        )
        
        # Step 4: Migrate comments to reference current asset versions
        comment_success = 0
        comment_errors = 0
        comment_migrated = 0
        if CONFIG['comment_storage_table_name'] != 'YOUR_COMMENT_STORAGE_TABLE_NAME':
            comment_success, comment_errors, comment_migrated = migrate_comments(
                dynamodb,
                CONFIG['comment_storage_table_name'],
                CONFIG['assets_table_name'],
                args.limit if args.limit else None
            )
        else:
            logger.info("Skipping comment migration - no comments table specified")
            
        # Step 5: Migrate asset links to the new schema
        asset_links_success = 0
        asset_links_errors = 0
        if (CONFIG['asset_links_table_name'] != 'YOUR_ASSET_LINKS_TABLE_NAME' and 
            CONFIG['asset_links_table_v2_name'] != 'YOUR_ASSET_LINKS_TABLE_V2_NAME'):
            asset_links_success, asset_links_errors = migrate_asset_links(
                dynamodb,
                CONFIG['asset_links_table_name'],
                CONFIG['asset_links_table_v2_name'],
                CONFIG['assets_table_name'],
                args.limit if args.limit else None
            )
        else:
            logger.info("Skipping asset links migration - no asset links tables specified")
        
        # Print summary
        logger.info("Migration completed")
        logger.info(f"Asset versions migration: {version_success} successful, {version_errors} errors")
        logger.info(f"Asset records update: {asset_success} successful, {asset_errors} errors")
        logger.info(f"Database records update: {db_success} successful, {db_errors} errors")
        if CONFIG['comment_storage_table_name'] != 'YOUR_COMMENT_STORAGE_TABLE_NAME':
            logger.info(f"Comment migration: {comment_success} successful, {comment_errors} errors, {comment_migrated} comments migrated")
        if CONFIG['asset_links_table_name'] != 'YOUR_ASSET_LINKS_TABLE_NAME':
            logger.info(f"Asset links migration: {asset_links_success} successful, {asset_links_errors} errors")
        
        if version_errors > 0 or asset_errors > 0 or db_errors > 0 or comment_errors > 0 or asset_links_errors > 0:
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
