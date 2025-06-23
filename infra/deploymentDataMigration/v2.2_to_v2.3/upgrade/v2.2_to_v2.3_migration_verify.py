#!/usr/bin/env python3
# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Verification script for VAMS v2.2 to v2.3 data migration

This script verifies that the migration from v2.2 to v2.3 was successful by:
1. Checking that all assets have a corresponding version record
2. Verifying that asset records have been updated correctly with new structure
3. Verifying that database records have been updated with defaultBucketId
4. Generating a report of the verification results

Usage:
    python v2.2_to_v2.3_migration_verify.py --config your_config.json

Requirements:
    - Python 3.6+
    - boto3
    - AWS credentials with permissions to read from DynamoDB tables
"""

import argparse
import boto3
import csv
import json
import logging
import os
import sys
import time
from datetime import datetime
from botocore.exceptions import ClientError

# Import the migration script for configuration and utility functions
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
# VERIFICATION FUNCTIONS
# =====================================================================

def verify_asset_versions(dynamodb, assets_table_name, asset_versions_table_name, limit=None):
    """
    Verify that all assets have a corresponding version record.
    
    Args:
        dynamodb: DynamoDB resource
        assets_table_name (str): Name of the assets table
        asset_versions_table_name (str): Name of the asset versions table
        limit (int, optional): Maximum number of assets to verify
        
    Returns:
        tuple: (success_count, error_count, errors)
    """
    logger.info(f"Verifying asset versions")
    
    # Get all assets
    assets = migration.scan_table(dynamodb, assets_table_name, limit)
    logger.info(f"Found {len(assets)} assets to verify")
    
    success_count = 0
    error_count = 0
    errors = []
    
    # Process each asset
    for asset in assets:
        asset_id = asset.get('assetId')
        
        try:
            # Check if asset has currentVersionId
            if 'currentVersionId' not in asset:
                error_count += 1
                error_msg = f"Asset {asset_id} does not have currentVersionId field"
                logger.warning(error_msg)
                errors.append({
                    'asset_id': asset_id,
                    'error_type': 'missing_current_version_id',
                    'error_message': error_msg
                })
                continue
            
            current_version_id = asset['currentVersionId']
            
            # Get version record from asset versions table
            versions_table = dynamodb.Table(asset_versions_table_name)
            response = versions_table.get_item(
                Key={
                    'assetId': asset_id,
                    'assetVersionId': current_version_id
                }
            )
            
            if 'Item' not in response:
                error_count += 1
                error_msg = f"No version record found for asset {asset_id} with version {current_version_id}"
                logger.warning(error_msg)
                errors.append({
                    'asset_id': asset_id,
                    'error_type': 'missing_version_record',
                    'error_message': error_msg
                })
                continue
            
            # Verify version record has required fields
            version_record = response['Item']
            required_fields = [
                'assetId', 'assetVersionId', 'isCurrentVersion', 
                'dateCreated', 'comment', 'description', 
                'createdBy', 'specifiedPipelines'
            ]
            
            missing_fields = []
            for field in required_fields:
                if field not in version_record:
                    missing_fields.append(field)
            
            if missing_fields:
                error_count += 1
                error_msg = f"Version record for asset {asset_id} is missing fields: {', '.join(missing_fields)}"
                logger.warning(error_msg)
                errors.append({
                    'asset_id': asset_id,
                    'error_type': 'incomplete_version_record',
                    'error_message': error_msg
                })
                continue
            
            success_count += 1
            
        except Exception as e:
            error_count += 1
            error_msg = f"Error verifying asset {asset_id}: {e}"
            logger.error(error_msg)
            errors.append({
                'asset_id': asset_id,
                'error_type': 'exception',
                'error_message': error_msg
            })
    
    logger.info(f"Completed verification of asset versions: {success_count} successful, {error_count} errors")
    return success_count, error_count, errors

def verify_asset_updates(dynamodb, assets_table_name, base_assets_prefix, limit=None):
    """
    Verify that asset records have been updated correctly.
    
    Args:
        dynamodb: DynamoDB resource
        assets_table_name (str): Name of the assets table
        base_assets_prefix (str): Base assets prefix to use in the Key
        limit (int, optional): Maximum number of assets to verify
        
    Returns:
        tuple: (success_count, error_count, errors)
    """
    logger.info(f"Verifying asset updates")
    
    # Get all assets
    assets = migration.scan_table(dynamodb, assets_table_name, limit)
    logger.info(f"Found {len(assets)} assets to verify")
    
    success_count = 0
    error_count = 0
    errors = []
    
    # Process each asset
    for asset in assets:
        asset_id = asset.get('assetId')
        
        try:
            # Verify assetLocation
            if 'assetLocation' not in asset:
                error_count += 1
                error_msg = f"Asset {asset_id} does not have assetLocation field"
                logger.warning(error_msg)
                errors.append({
                    'asset_id': asset_id,
                    'error_type': 'missing_asset_location',
                    'error_message': error_msg
                })
                continue
            
            asset_location = asset['assetLocation']
            if not isinstance(asset_location, dict):
                error_count += 1
                error_msg = f"Asset {asset_id} has invalid assetLocation: {asset_location}"
                logger.warning(error_msg)
                errors.append({
                    'asset_id': asset_id,
                    'error_type': 'invalid_asset_location',
                    'error_message': error_msg
                })
                continue
            
            # Check that assetLocation has Key but not Bucket
            if 'Key' not in asset_location:
                error_count += 1
                error_msg = f"Asset {asset_id} has incomplete assetLocation: {asset_location}"
                logger.warning(error_msg)
                errors.append({
                    'asset_id': asset_id,
                    'error_type': 'incomplete_asset_location',
                    'error_message': error_msg
                })
                continue
            
            if 'Bucket' in asset_location:
                error_count += 1
                error_msg = f"Asset {asset_id} still has Bucket in assetLocation: {asset_location}"
                logger.warning(error_msg)
                errors.append({
                    'asset_id': asset_id,
                    'error_type': 'bucket_in_asset_location',
                    'error_message': error_msg
                })
                continue
            
            # Check that assetLocation.Key has the correct format
            expected_key = f"{base_assets_prefix}{asset_id}/"
            if asset_location['Key'] != expected_key:
                error_count += 1
                error_msg = f"Asset {asset_id} has incorrect Key in assetLocation: {asset_location['Key']}, expected: {expected_key}"
                logger.warning(error_msg)
                errors.append({
                    'asset_id': asset_id,
                    'error_type': 'incorrect_asset_location_key',
                    'error_message': error_msg
                })
                continue
            
            # Verify bucketId exists
            if 'bucketId' not in asset:
                error_count += 1
                error_msg = f"Asset {asset_id} does not have bucketId field"
                logger.warning(error_msg)
                errors.append({
                    'asset_id': asset_id,
                    'error_type': 'missing_bucket_id',
                    'error_message': error_msg
                })
                continue
            
            # Verify fields were removed
            for field in migration.FIELDS_TO_REMOVE:
                if field in asset:
                    error_count += 1
                    error_msg = f"Asset {asset_id} still has field that should have been removed: {field}"
                    logger.warning(error_msg)
                    errors.append({
                        'asset_id': asset_id,
                        'error_type': 'field_not_removed',
                        'error_message': error_msg,
                        'field': field
                    })
                    continue
            
            success_count += 1
            
        except Exception as e:
            error_count += 1
            error_msg = f"Error verifying asset {asset_id}: {e}"
            logger.error(error_msg)
            errors.append({
                'asset_id': asset_id,
                'error_type': 'exception',
                'error_message': error_msg
            })
    
    logger.info(f"Completed verification of asset updates: {success_count} successful, {error_count} errors")
    return success_count, error_count, errors

def verify_database_updates(dynamodb, databases_table_name, limit=None):
    """
    Verify that database records have been updated with defaultBucketId.
    
    Args:
        dynamodb: DynamoDB resource
        databases_table_name (str): Name of the databases table
        limit (int, optional): Maximum number of databases to verify
        
    Returns:
        tuple: (success_count, error_count, errors)
    """
    logger.info(f"Verifying database updates")
    
    # Get all databases
    databases = migration.scan_table(dynamodb, databases_table_name, limit)
    logger.info(f"Found {len(databases)} databases to verify")
    
    success_count = 0
    error_count = 0
    errors = []
    
    # Process each database
    for database in databases:
        database_id = database.get('databaseId')
        
        try:
            # Verify defaultBucketId exists
            if 'defaultBucketId' not in database:
                error_count += 1
                error_msg = f"Database {database_id} does not have defaultBucketId field"
                logger.warning(error_msg)
                errors.append({
                    'database_id': database_id,
                    'error_type': 'missing_default_bucket_id',
                    'error_message': error_msg
                })
                continue
            
            success_count += 1
            
        except Exception as e:
            error_count += 1
            error_msg = f"Error verifying database {database_id}: {e}"
            logger.error(error_msg)
            errors.append({
                'database_id': database_id,
                'error_type': 'exception',
                'error_message': error_msg
            })
    
    logger.info(f"Completed verification of database updates: {success_count} successful, {error_count} errors")
    return success_count, error_count, errors

def generate_report(version_results, asset_results, database_results, output_file=None):
    """
    Generate a report of the verification results.
    
    Args:
        version_results (tuple): Results from verify_asset_versions
        asset_results (tuple): Results from verify_asset_updates
        database_results (tuple): Results from verify_database_updates
        output_file (str, optional): Path to output file
        
    Returns:
        tuple: (report_file, summary_file)
    """
    version_success, version_errors, version_error_details = version_results
    asset_success, asset_errors, asset_error_details = asset_results
    db_success, db_errors, db_error_details = database_results
    
    # Create report directory if it doesn't exist
    report_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'reports')
    os.makedirs(report_dir, exist_ok=True)
    
    # Generate report filename if not provided
    if not output_file:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_file = os.path.join(report_dir, f'migration_verification_{timestamp}.csv')
    
    # Write report to CSV file
    with open(output_file, 'w', newline='') as csvfile:
        fieldnames = ['asset_id', 'database_id', 'error_type', 'error_message', 'field']
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        
        writer.writeheader()
        
        # Write version errors
        for error in version_error_details:
            writer.writerow(error)
        
        # Write asset errors
        for error in asset_error_details:
            writer.writerow(error)
        
        # Write database errors
        for error in db_error_details:
            writer.writerow(error)
    
    # Generate summary
    summary = {
        'timestamp': datetime.now().isoformat(),
        'version_verification': {
            'success_count': version_success,
            'error_count': version_errors
        },
        'asset_verification': {
            'success_count': asset_success,
            'error_count': asset_errors
        },
        'database_verification': {
            'success_count': db_success,
            'error_count': db_errors
        },
        'total_success': version_success + asset_success + db_success,
        'total_errors': version_errors + asset_errors + db_errors,
        'report_file': output_file
    }
    
    # Write summary to JSON file
    summary_file = output_file.replace('.csv', '_summary.json')
    with open(summary_file, 'w') as f:
        json.dump(summary, f, indent=2)
    
    return output_file, summary_file

# =====================================================================
# MAIN FUNCTION
# =====================================================================

def main():
    """Main function to run the verification."""
    parser = argparse.ArgumentParser(description='VAMS v2.2 to v2.3 Migration Verification Script')
    parser.add_argument('--profile', help='AWS profile name to use')
    parser.add_argument('--region', help='AWS region to use')
    parser.add_argument('--limit', type=int, help='Maximum number of assets to verify')
    parser.add_argument('--assets-table', help='Name of the assets table')
    parser.add_argument('--asset-versions-table', help='Name of the asset versions table')
    parser.add_argument('--s3-asset-buckets-table', help='Name of the S3_Asset_Buckets table')
    parser.add_argument('--databases-table', help='Name of the databases table')
    parser.add_argument('--base-assets-prefix', help='Base assets prefix to use in assetLocation.Key')
    parser.add_argument('--asset-bucket-name', help='Name of the asset bucket used for storage')
    parser.add_argument('--config', help='Path to configuration file')
    parser.add_argument('--log-level', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'], 
                        help='Logging level')
    parser.add_argument('--output', help='Path to output report file')
    
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
    if args.s3_asset_buckets_table:
        migration.CONFIG['s3_asset_buckets_table_name'] = args.s3_asset_buckets_table
    if args.databases_table:
        migration.CONFIG['databases_table_name'] = args.databases_table
    if args.base_assets_prefix:
        migration.CONFIG['base_assets_prefix'] = args.base_assets_prefix
    if args.bucket_name:
        migration.CONFIG['bucket_name'] = args.bucket_name
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
    
    if migration.CONFIG['s3_asset_buckets_table_name'] == 'YOUR_S3_ASSET_BUCKETS_TABLE_NAME':
        logger.error("Please set the s3_asset_buckets_table_name in the CONFIG or provide it with --s3-asset-buckets-table")
        return 1
    
    if migration.CONFIG['databases_table_name'] == 'YOUR_DATABASES_TABLE_NAME':
        logger.error("Please set the databases_table_name in the CONFIG or provide it with --databases-table")
        return 1
    
    if migration.CONFIG['base_assets_prefix'] == 'YOUR_BASE_ASSETS_PREFIX':
        logger.error("Please set the base_assets_prefix in the CONFIG or provide it with --base-assets-prefix")
        return 1
    
    if migration.CONFIG['bucket_name'] == 'YOUR_BUCKET_NAME':
        logger.error("Please set the bucket_name in the CONFIG or provide it with --bucket-name")
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
    
    logger.info("Starting VAMS v2.2 to v2.3 migration verification")
    logger.info(f"Configuration: {json.dumps(migration.CONFIG, indent=2)}")
    
    try:
        # Verify asset versions
        version_results = verify_asset_versions(
            dynamodb, 
            migration.CONFIG['assets_table_name'], 
            migration.CONFIG['asset_versions_table_name'],
            args.limit
        )
        
        # Verify asset updates
        asset_results = verify_asset_updates(
            dynamodb, 
            migration.CONFIG['assets_table_name'], 
            migration.CONFIG['base_assets_prefix'],
            args.limit
        )
        
        # Verify database updates
        database_results = verify_database_updates(
            dynamodb,
            migration.CONFIG['databases_table_name'],
            args.limit
        )
        
        # Generate report
        report_file, summary_file = generate_report(
            version_results, 
            asset_results, 
            database_results, 
            args.output
        )
        
        # Print summary
        version_success, version_errors, _ = version_results
        asset_success, asset_errors, _ = asset_results
        db_success, db_errors, _ = database_results
        
        logger.info("Verification completed")
        logger.info(f"Asset versions verification: {version_success} successful, {version_errors} errors")
        logger.info(f"Asset updates verification: {asset_success} successful, {asset_errors} errors")
        logger.info(f"Database updates verification: {db_success} successful, {db_errors} errors")
        logger.info(f"Report saved to: {report_file}")
        logger.info(f"Summary saved to: {summary_file}")
        
        if version_errors > 0 or asset_errors > 0 or db_errors > 0:
            logger.warning("Verification completed with errors - check the report for details")
            return 1
        else:
            logger.info("Verification completed successfully")
            return 0
    except Exception as e:
        logger.error(f"Error during verification: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
