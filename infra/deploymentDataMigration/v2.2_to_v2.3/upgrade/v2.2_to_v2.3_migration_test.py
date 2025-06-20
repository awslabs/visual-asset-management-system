#!/usr/bin/env python3
# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Test script for VAMS v2.2 to v2.3 data migration

This script creates test data in the source tables and then runs the migration
to verify that the migration script works correctly.

Usage:
    python v2.2_to_v2.3_migration_test.py --profile <aws-profile-name>

Requirements:
    - Python 3.6+
    - boto3
    - AWS credentials with permissions to read/write to DynamoDB tables
"""

import argparse
import boto3
import json
import logging
import sys
import time
import uuid
from datetime import datetime
from botocore.exceptions import ClientError

# Import the migration script
import v2_2_to_v2_3_migration as migration

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
# TEST DATA
# =====================================================================

def create_test_asset(asset_id, database_id, asset_name, description, version="1"):
    """
    Create a test asset record with the v2.2 schema.
    
    Args:
        asset_id (str): Asset ID
        database_id (str): Database ID
        asset_name (str): Asset name
        description (str): Asset description
        version (str): Version number
        
    Returns:
        dict: Test asset record
    """
    now = datetime.now().isoformat()
    
    return {
        'databaseId': database_id,
        'assetId': asset_id,
        'assetName': asset_name,
        'description': description,
        'isDistributable': True,
        'tags': ['test-tag-1', 'test-tag-2'],
        'assetType': 'test-type',
        'assetLocation': {
            'Key': f'old-key-{asset_id}'
        },
        'previewLocation': {
            'Key': f'preview-key-{asset_id}'
        },
        'currentVersion': {
            'Version': version,
            'DateModified': now,
            'Comment': f'Test comment for version {version}',
            'description': description,
            'specifiedPipelines': ['pipeline-1', 'pipeline-2']
        },
        'isMultiFile': True,
        'pipelineId': 'test-pipeline-id',
        'executionId': 'test-execution-id',
        'versions': {
            version: {
                'Version': version,
                'DateModified': now,
                'Comment': f'Test comment for version {version}'
            }
        },
        'specifiedPipelines': ['pipeline-1', 'pipeline-2'],
        'Parent': 'test-parent',
        'objectFamily': 'test-family'
    }

# =====================================================================
# TEST FUNCTIONS
# =====================================================================

def create_test_data(dynamodb, assets_table_name, num_assets=5):
    """
    Create test data in the assets table.
    
    Args:
        dynamodb: DynamoDB resource
        assets_table_name (str): Name of the assets table
        num_assets (int): Number of test assets to create
        
    Returns:
        list: List of created asset IDs
    """
    logger.info(f"Creating {num_assets} test assets in {assets_table_name}")
    
    table = dynamodb.Table(assets_table_name)
    asset_ids = []
    
    for i in range(num_assets):
        asset_id = f"test-asset-{uuid.uuid4()}"
        database_id = "test-database"
        asset_name = f"Test Asset {i+1}"
        description = f"Test description for asset {i+1}"
        version = str(i+1)
        
        asset = create_test_asset(asset_id, database_id, asset_name, description, version)
        
        try:
            table.put_item(Item=asset)
            asset_ids.append(asset_id)
            logger.info(f"Created test asset {asset_id}")
        except Exception as e:
            logger.error(f"Error creating test asset {asset_id}: {e}")
    
    return asset_ids

def verify_asset_versions(dynamodb, asset_versions_table_name, asset_ids):
    """
    Verify that asset versions were created correctly.
    
    Args:
        dynamodb: DynamoDB resource
        asset_versions_table_name (str): Name of the asset versions table
        asset_ids (list): List of asset IDs to verify
        
    Returns:
        bool: True if all asset versions were created correctly, False otherwise
    """
    logger.info(f"Verifying asset versions in {asset_versions_table_name}")
    
    table = dynamodb.Table(asset_versions_table_name)
    success = True
    
    for asset_id in asset_ids:
        try:
            response = table.query(
                KeyConditionExpression="assetId = :assetId",
                ExpressionAttributeValues={
                    ":assetId": asset_id
                }
            )
            
            items = response.get('Items', [])
            
            if not items:
                logger.error(f"No version record found for asset {asset_id}")
                success = False
                continue
            
            version_record = items[0]
            
            # Verify required fields
            required_fields = [
                'assetId', 'assetVersionId', 'isCurrentVersion', 
                'dateCreated', 'comment', 'description', 
                'createdBy', 'specifiedPipelines'
            ]
            
            for field in required_fields:
                if field not in version_record:
                    logger.error(f"Missing field {field} in version record for asset {asset_id}")
                    success = False
            
            logger.info(f"Verified version record for asset {asset_id}")
            
        except Exception as e:
            logger.error(f"Error verifying version record for asset {asset_id}: {e}")
            success = False
    
    return success

def verify_updated_assets(dynamodb, assets_table_name, asset_ids, asset_bucket_name):
    """
    Verify that assets were updated correctly.
    
    Args:
        dynamodb: DynamoDB resource
        assets_table_name (str): Name of the assets table
        asset_ids (list): List of asset IDs to verify
        asset_bucket_name (str): Name of the asset bucket
        
    Returns:
        bool: True if all assets were updated correctly, False otherwise
    """
    logger.info(f"Verifying updated assets in {assets_table_name}")
    
    table = dynamodb.Table(assets_table_name)
    success = True
    
    for asset_id in asset_ids:
        try:
            response = table.scan(
                FilterExpression="assetId = :assetId",
                ExpressionAttributeValues={
                    ":assetId": asset_id
                }
            )
            
            items = response.get('Items', [])
            
            if not items:
                logger.error(f"No asset record found for asset {asset_id}")
                success = False
                continue
            
            asset = items[0]
            
            # Verify assetLocation
            if 'assetLocation' not in asset:
                logger.error(f"Missing assetLocation in asset {asset_id}")
                success = False
            elif 'Key' not in asset['assetLocation'] or 'Bucket' not in asset['assetLocation']:
                logger.error(f"Invalid assetLocation in asset {asset_id}: {asset['assetLocation']}")
                success = False
            elif asset['assetLocation']['Key'] != f"{asset_id}/":
                logger.error(f"Incorrect assetLocation.Key in asset {asset_id}: {asset['assetLocation']['Key']}")
                success = False
            elif asset['assetLocation']['Bucket'] != asset_bucket_name:
                logger.error(f"Incorrect assetLocation.Bucket in asset {asset_id}: {asset['assetLocation']['Bucket']}")
                success = False
            
            # Verify previewLocation
            if 'previewLocation' in asset:
                if 'Key' not in asset['previewLocation'] or 'Bucket' not in asset['previewLocation']:
                    logger.error(f"Invalid previewLocation in asset {asset_id}: {asset['previewLocation']}")
                    success = False
                elif asset['previewLocation']['Bucket'] != asset_bucket_name:
                    logger.error(f"Incorrect previewLocation.Bucket in asset {asset_id}: {asset['previewLocation']['Bucket']}")
                    success = False
            
            # Verify currentVersionId
            if 'currentVersionId' not in asset:
                logger.error(f"Missing currentVersionId in asset {asset_id}")
                success = False
            
            # Verify removed fields
            for field in migration.FIELDS_TO_REMOVE:
                if field in asset:
                    logger.error(f"Field {field} was not removed from asset {asset_id}")
                    success = False
            
            logger.info(f"Verified updated asset {asset_id}")
            
        except Exception as e:
            logger.error(f"Error verifying updated asset {asset_id}: {e}")
            success = False
    
    return success

def cleanup_test_data(dynamodb, assets_table_name, asset_versions_table_name, asset_ids):
    """
    Clean up test data from the tables.
    
    Args:
        dynamodb: DynamoDB resource
        assets_table_name (str): Name of the assets table
        asset_versions_table_name (str): Name of the asset versions table
        asset_ids (list): List of asset IDs to clean up
    """
    logger.info(f"Cleaning up test data")
    
    assets_table = dynamodb.Table(assets_table_name)
    versions_table = dynamodb.Table(asset_versions_table_name)
    
    for asset_id in asset_ids:
        try:
            # Find and delete the asset
            response = assets_table.scan(
                FilterExpression="assetId = :assetId",
                ExpressionAttributeValues={
                    ":assetId": asset_id
                }
            )
            
            for item in response.get('Items', []):
                assets_table.delete_item(
                    Key={
                        'databaseId': item['databaseId'],
                        'assetId': item['assetId']
                    }
                )
                logger.info(f"Deleted asset {asset_id} from {assets_table_name}")
            
            # Find and delete the version records
            response = versions_table.query(
                KeyConditionExpression="assetId = :assetId",
                ExpressionAttributeValues={
                    ":assetId": asset_id
                }
            )
            
            for item in response.get('Items', []):
                versions_table.delete_item(
                    Key={
                        'assetId': item['assetId'],
                        'assetVersionId': item['assetVersionId']
                    }
                )
                logger.info(f"Deleted version record for asset {asset_id} from {asset_versions_table_name}")
            
        except Exception as e:
            logger.error(f"Error cleaning up test data for asset {asset_id}: {e}")

# =====================================================================
# MAIN FUNCTION
# =====================================================================

def main():
    """Main function to run the test."""
    parser = argparse.ArgumentParser(description='VAMS v2.2 to v2.3 Migration Test Script')
    parser.add_argument('--profile', help='AWS profile name to use')
    parser.add_argument('--region', help='AWS region to use')
    parser.add_argument('--assets-table', help='Name of the assets table')
    parser.add_argument('--asset-versions-table', help='Name of the asset versions table')
    parser.add_argument('--asset-bucket', help='Name of the asset bucket')
    parser.add_argument('--num-assets', type=int, default=5, help='Number of test assets to create')
    parser.add_argument('--no-cleanup', action='store_true', help='Do not clean up test data after the test')
    parser.add_argument('--config', help='Path to configuration file')
    parser.add_argument('--log-level', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'], 
                        help='Logging level')
    
    args = parser.parse_args()
    
    # Load configuration from file if provided
    if args.config:
        file_config = migration.load_config_from_file(args.config)
        migration.CONFIG.update(file_config)
    
    # Update configuration with command line arguments (these override file config)
    if args.assets_table:
        migration.CONFIG['assets_table_name'] = args.assets_table
    if args.asset_versions_table:
        migration.CONFIG['asset_versions_table_name'] = args.asset_versions_table
    if args.asset_bucket:
        migration.CONFIG['asset_bucket_name'] = args.asset_bucket
    if args.profile:
        migration.CONFIG['aws_profile'] = args.profile
    if args.region:
        migration.CONFIG['aws_region'] = args.region
    if args.log_level:
        migration.CONFIG['log_level'] = args.log_level
        
    # Set logging level
    logging.getLogger().setLevel(getattr(logging, migration.CONFIG.get('log_level', 'INFO')))
    
    # Validate configuration
    if migration.CONFIG['assets_table_name'] == 'YOUR_ASSETS_TABLE_NAME':
        logger.error("Please set the assets_table_name in the CONFIG or provide it with --assets-table")
        return 1
    
    if migration.CONFIG['asset_versions_table_name'] == 'YOUR_ASSET_VERSIONS_TABLE_NAME':
        logger.error("Please set the asset_versions_table_name in the CONFIG or provide it with --asset-versions-table")
        return 1
    
    if migration.CONFIG['asset_bucket_name'] == 'YOUR_ASSET_BUCKET_NAME':
        logger.error("Please set the asset_bucket_name in the CONFIG or provide it with --asset-bucket")
        return 1
    
    # Initialize DynamoDB client
    try:
        dynamodb = migration.get_dynamodb_resource(
            profile_name=migration.CONFIG.get('aws_profile'), 
            region=migration.CONFIG.get('aws_region')
        )
    except Exception as e:
        logger.error(f"Error initializing DynamoDB client: {e}")
        return 1
    
    logger.info("Starting VAMS v2.2 to v2.3 migration test")
    logger.info(f"Configuration: {json.dumps(migration.CONFIG, indent=2)}")
    
    # Create test data
    asset_ids = create_test_data(
        dynamodb, 
        migration.CONFIG['assets_table_name'], 
        args.num_assets
    )
    
    if not asset_ids:
        logger.error("Failed to create test data")
        return 1
    
    # Run the migration
    try:
        # Step 1: Migrate asset versions
        version_success, version_errors = migration.migrate_asset_versions(
            dynamodb, 
            migration.CONFIG['assets_table_name'], 
            migration.CONFIG['asset_versions_table_name']
        )
        
        # Step 2: Update asset records
        asset_success, asset_errors = migration.update_asset_records(
            dynamodb, 
            migration.CONFIG['assets_table_name'], 
            migration.CONFIG['asset_bucket_name']
        )
        
        # Verify the results
        versions_verified = verify_asset_versions(
            dynamodb, 
            migration.CONFIG['asset_versions_table_name'], 
            asset_ids
        )
        
        assets_verified = verify_updated_assets(
            dynamodb, 
            migration.CONFIG['assets_table_name'], 
            asset_ids,
            migration.CONFIG['asset_bucket_name']
        )
        
        # Clean up test data
        if not args.no_cleanup:
            cleanup_test_data(
                dynamodb, 
                migration.CONFIG['assets_table_name'], 
                migration.CONFIG['asset_versions_table_name'], 
                asset_ids
            )
        
        # Print summary
        logger.info("Test completed")
        logger.info(f"Asset versions migration: {version_success} successful, {version_errors} errors")
        logger.info(f"Asset records update: {asset_success} successful, {asset_errors} errors")
        logger.info(f"Asset versions verification: {'Passed' if versions_verified else 'Failed'}")
        logger.info(f"Asset records verification: {'Passed' if assets_verified else 'Failed'}")
        
        if version_errors > 0 or asset_errors > 0 or not versions_verified or not assets_verified:
            logger.error("Test failed - check the logs for details")
            return 1
        else:
            logger.info("Test passed successfully")
            return 0
    except Exception as e:
        logger.error(f"Error during test: {e}")
        return 1
    finally:
        # Clean up test data if an exception occurred
        if not args.no_cleanup and 'asset_ids' in locals():
            cleanup_test_data(
                dynamodb, 
                migration.CONFIG['assets_table_name'], 
                migration.CONFIG['asset_versions_table_name'], 
                asset_ids
            )

if __name__ == "__main__":
    sys.exit(main())
