#!/usr/bin/env python3
# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Data Migration Script for VAMS v2.2 to v2.3

This script performs OpenSearch dual-index migration by directly updating
the AssetsMetadata DynamoDB table. Updates to this table trigger DynamoDB
Streams which automatically reindex assets and files in OpenSearch.

Key Features:
- Directly inserts/updates records in AssetsMetadata table
- Preserves existing metadata attributes
- Sets _asset_table_updated timestamp for tracking
- Optional index clearing before migration
- Verification of migration success
- Detailed migration reports

Usage:
    python v2.2_to_v2.3_migration.py --config v2.2_to_v2.3_migration_config.json
    python v2.2_to_v2.3_migration.py --profile <aws-profile-name> --dry-run

Requirements:
    - Python 3.6+
    - boto3
    - opensearchpy (for index clearing)
    - aws-requests-auth (for OpenSearch authentication)
    - AWS credentials with permissions to:
      * DynamoDB: GetItem, PutItem, Scan on asset, S3 buckets, and AssetsMetadata tables
      * S3: ListBucket, GetObject, HeadObject on asset buckets
      * SSM: GetParameter for OpenSearch configuration
      * OpenSearch: DeleteByQuery (if clearing indexes)
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

# Add tools directory to path for importing reindex_utility
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'tools'))
from reindex_utility import ReindexUtility

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
    # Source DynamoDB tables
    "asset_storage_table_name": "YOUR_ASSET_STORAGE_TABLE_NAME",
    "s3_asset_buckets_table_name": "YOUR_S3_ASSET_BUCKETS_TABLE_NAME",
    
    # Target DynamoDB table
    "assets_metadata_table_name": "YOUR_ASSETS_METADATA_TABLE_NAME",
    
    # SSM parameters for OpenSearch configuration (for verification and optional clearing)
    "opensearch_asset_index_ssm_param": "YOUR_OPENSEARCH_ASSET_INDEX_SSM_PARAM",
    "opensearch_file_index_ssm_param": "YOUR_OPENSEARCH_FILE_INDEX_SSM_PARAM",
    "opensearch_endpoint_ssm_param": "YOUR_OPENSEARCH_ENDPOINT_SSM_PARAM",
    
    # AWS settings
    "aws_profile": None,
    "aws_region": None,
    
    # Migration settings
    "clear_indexes_before_migration": False,
    "reindex_settings": {
        "asset_batch_size": 25,
        "file_batch_size": 100,
        "memory_batch_size": 1000,
        "max_workers": 10
    },
    
    # Verification settings
    "log_level": "INFO",
    "dry_run": False,
    "verification_wait_time": 120
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

# =====================================================================
# HELPER FUNCTIONS
# =====================================================================

def get_aws_session(profile_name=None, region=None):
    """
    Create a boto3 session with the specified profile and region.
    
    Args:
        profile_name (str, optional): AWS profile name to use
        region (str, optional): AWS region to use
        
    Returns:
        boto3.Session: AWS session
    """
    session_args = {}
    if profile_name:
        session_args['profile_name'] = profile_name
    if region:
        session_args['region_name'] = region
        
    return boto3.Session(**session_args)

def get_ssm_parameter_value(ssm_client, parameter_name):
    """
    Get SSM parameter value.
    
    Args:
        ssm_client: SSM client
        parameter_name (str): Parameter name
        
    Returns:
        str: Parameter value
    """
    try:
        response = ssm_client.get_parameter(Name=parameter_name)
        return response['Parameter']['Value']
    except Exception as e:
        logger.error(f"Error getting SSM parameter {parameter_name}: {e}")
        raise

def get_opensearch_index_stats(opensearch_client, index_name):
    """
    Get document count and other stats for an OpenSearch index.
    
    Args:
        opensearch_client: OpenSearch client
        index_name (str): Name of the index
        
    Returns:
        dict: Index statistics
    """
    try:
        if not opensearch_client.indices.exists(index=index_name):
            return {'exists': False, 'document_count': 0}
        
        stats = opensearch_client.indices.stats(index=index_name)
        doc_count = stats['indices'][index_name]['total']['docs']['count']
        
        return {
            'exists': True,
            'document_count': doc_count,
            'size_in_bytes': stats['indices'][index_name]['total']['store']['size_in_bytes']
        }
    except Exception as e:
        logger.warning(f"Error getting stats for index {index_name}: {e}")
        return {'exists': False, 'document_count': 0, 'error': str(e)}

# =====================================================================
# VERIFICATION FUNCTIONS
# =====================================================================

def verify_migration(session, opensearch_asset_index, opensearch_file_index, opensearch_endpoint, expected_assets, expected_files):
    """
    Verify the migration was successful by checking index document counts.
    
    Args:
        session: AWS session
        opensearch_asset_index (str): Name of the asset index
        opensearch_file_index (str): Name of the file index
        opensearch_endpoint (str): OpenSearch endpoint
        expected_assets (int): Expected number of asset documents
        expected_files (int): Expected number of file documents
        
    Returns:
        dict: Verification results
    """
    logger.info("Starting migration verification")
    
    try:
        from opensearchpy import OpenSearch, RequestsHttpConnection
        from aws_requests_auth.aws_auth import AWSRequestsAuth
        
        # Create OpenSearch client
        credentials = session.get_credentials()
        host = opensearch_endpoint.replace('https://', '').replace('http://', '')
        
        awsauth = AWSRequestsAuth(
            aws_access_key=credentials.access_key,
            aws_secret_access_key=credentials.secret_key,
            aws_token=credentials.token,
            aws_host=host,
            aws_region=session.region_name,
            aws_service='aoss'  # Use 'es' for managed OpenSearch
        )
        
        client = OpenSearch(
            hosts=[{'host': host, 'port': 443}],
            http_auth=awsauth,
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection
        )
        
        # Get index statistics
        asset_stats = get_opensearch_index_stats(client, opensearch_asset_index)
        file_stats = get_opensearch_index_stats(client, opensearch_file_index)
        
        verification_results = {
            'timestamp': datetime.utcnow().isoformat(),
            'asset_index': {
                'name': opensearch_asset_index,
                'expected_documents': expected_assets,
                'actual_documents': asset_stats.get('document_count', 0),
                'exists': asset_stats.get('exists', False),
                'size_bytes': asset_stats.get('size_in_bytes', 0)
            },
            'file_index': {
                'name': opensearch_file_index,
                'expected_documents': expected_files,
                'actual_documents': file_stats.get('document_count', 0),
                'exists': file_stats.get('exists', False),
                'size_bytes': file_stats.get('size_in_bytes', 0)
            }
        }
        
        # Calculate success rates
        asset_success_rate = (asset_stats.get('document_count', 0) / expected_assets * 100) if expected_assets > 0 else 0
        file_success_rate = (file_stats.get('document_count', 0) / expected_files * 100) if expected_files > 0 else 0
        
        verification_results['asset_index']['success_rate'] = round(asset_success_rate, 2)
        verification_results['file_index']['success_rate'] = round(file_success_rate, 2)
        
        # Determine overall success (allow 5% tolerance for indexing delays)
        verification_results['overall_success'] = (
            asset_stats.get('exists', False) and 
            file_stats.get('exists', False) and
            asset_success_rate >= 95.0 and
            file_success_rate >= 95.0
        )
        
        logger.info(f"Verification completed:")
        logger.info(f"  Asset index: {asset_stats.get('document_count', 0)}/{expected_assets} documents ({asset_success_rate:.1f}%)")
        logger.info(f"  File index: {file_stats.get('document_count', 0)}/{expected_files} documents ({file_success_rate:.1f}%)")
        
        return verification_results
        
    except Exception as e:
        logger.exception(f"Error during verification: {e}")
        return {
            'timestamp': datetime.utcnow().isoformat(),
            'error': str(e),
            'overall_success': False
        }

# =====================================================================
# MAIN FUNCTION
# =====================================================================

def main():
    """Main function to run the migration."""
    parser = argparse.ArgumentParser(description='VAMS v2.2 to v2.3 OpenSearch Dual-Index Migration Script')
    parser.add_argument('--profile', help='AWS profile name to use')
    parser.add_argument('--region', help='AWS region to use')
    parser.add_argument('--limit', type=int, help='Maximum number of assets/files to process (for testing)')
    parser.add_argument('--asset-storage-table', help='Name of the asset storage table')
    parser.add_argument('--s3-asset-buckets-table', help='Name of the S3 asset buckets table')
    parser.add_argument('--assets-metadata-table', help='Name of the AssetsMetadata table')
    parser.add_argument('--opensearch-asset-index-ssm', help='SSM parameter for asset index name')
    parser.add_argument('--opensearch-file-index-ssm', help='SSM parameter for file index name')
    parser.add_argument('--opensearch-endpoint-ssm', help='SSM parameter for OpenSearch endpoint')
    parser.add_argument('--clear-indexes', action='store_true', help='Clear OpenSearch indexes before migration')
    parser.add_argument('--asset-index-name', help='Asset index name (for clearing, overrides SSM)')
    parser.add_argument('--file-index-name', help='File index name (for clearing, overrides SSM)')
    parser.add_argument('--opensearch-endpoint', help='OpenSearch endpoint URL (for clearing, overrides SSM)')
    parser.add_argument('--dry-run', action='store_true', help='Perform a dry run without making changes')
    parser.add_argument('--config', help='Path to configuration file')
    parser.add_argument('--log-level', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'], 
                        help='Logging level')
    parser.add_argument('--skip-verification', action='store_true', help='Skip verification step')
    
    args = parser.parse_args()
    
    # Load configuration from file if provided
    if args.config:
        file_config = load_config_from_file(args.config)
        CONFIG.update(file_config)
    
    # Update configuration with command line arguments (these override file config)
    if args.asset_storage_table:
        CONFIG['asset_storage_table_name'] = args.asset_storage_table
    if args.s3_asset_buckets_table:
        CONFIG['s3_asset_buckets_table_name'] = args.s3_asset_buckets_table
    if args.assets_metadata_table:
        CONFIG['assets_metadata_table_name'] = args.assets_metadata_table
    if args.opensearch_asset_index_ssm:
        CONFIG['opensearch_asset_index_ssm_param'] = args.opensearch_asset_index_ssm
    if args.opensearch_file_index_ssm:
        CONFIG['opensearch_file_index_ssm_param'] = args.opensearch_file_index_ssm
    if args.opensearch_endpoint_ssm:
        CONFIG['opensearch_endpoint_ssm_param'] = args.opensearch_endpoint_ssm
    if args.profile:
        CONFIG['aws_profile'] = args.profile
    if args.region:
        CONFIG['aws_region'] = args.region
    if args.dry_run:
        CONFIG['dry_run'] = True
    if args.clear_indexes:
        CONFIG['clear_indexes_before_migration'] = True
    if args.log_level:
        CONFIG['log_level'] = args.log_level
        
    # Set logging level
    logging.getLogger().setLevel(getattr(logging, CONFIG.get('log_level', 'INFO')))
    
    # Validate configuration
    required_configs = [
        'asset_storage_table_name',
        's3_asset_buckets_table_name',
        'assets_metadata_table_name'
    ]
    
    for config_key in required_configs:
        if CONFIG[config_key].startswith('YOUR_'):
            logger.error(f"Please set the {config_key} in the CONFIG or provide it via command line")
            return 1
    
    # Initialize AWS session and clients
    try:
        session = get_aws_session(profile_name=CONFIG.get('aws_profile'), region=CONFIG.get('aws_region'))
        ssm_client = session.client('ssm')
    except Exception as e:
        logger.error(f"Error initializing AWS clients: {e}")
        return 1
    
    logger.info("=" * 80)
    logger.info("VAMS v2.2 to v2.3 OpenSearch Dual-Index Migration")
    logger.info("=" * 80)
    logger.info(f"Configuration: {json.dumps({k: v for k, v in CONFIG.items() if 'password' not in k.lower()}, indent=2)}")
    
    if CONFIG.get('dry_run', False):
        logger.info("DRY RUN MODE - No changes will be made")
    
    # Get OpenSearch configuration from SSM or command line
    opensearch_asset_index = args.asset_index_name
    opensearch_file_index = args.file_index_name
    opensearch_endpoint = args.opensearch_endpoint
    
    if not args.skip_verification or CONFIG.get('clear_indexes_before_migration'):
        try:
            logger.info("Loading OpenSearch configuration")
            
            if not opensearch_asset_index:
                opensearch_asset_index = get_ssm_parameter_value(ssm_client, CONFIG['opensearch_asset_index_ssm_param'])
            if not opensearch_file_index:
                opensearch_file_index = get_ssm_parameter_value(ssm_client, CONFIG['opensearch_file_index_ssm_param'])
            if not opensearch_endpoint:
                opensearch_endpoint = get_ssm_parameter_value(ssm_client, CONFIG['opensearch_endpoint_ssm_param'])
            
            logger.info(f"OpenSearch configuration loaded:")
            logger.info(f"  Asset index: {opensearch_asset_index}")
            logger.info(f"  File index: {opensearch_file_index}")
            logger.info(f"  Endpoint: {opensearch_endpoint}")
            
        except Exception as e:
            logger.warning(f"Could not load OpenSearch configuration: {e}")
            if CONFIG.get('clear_indexes_before_migration'):
                logger.error("Cannot clear indexes without OpenSearch configuration")
                return 1
            logger.warning("Verification will be skipped")
            args.skip_verification = True
    
    # Perform the migration
    try:
        migration_start_time = datetime.utcnow()
        
        # Create reindex utility instance
        logger.info("Initializing reindex utility")
        reindex_settings = CONFIG.get('reindex_settings', {})
        
        utility = ReindexUtility(
            session=session,
            asset_table_name=CONFIG['asset_storage_table_name'],
            s3_buckets_table_name=CONFIG['s3_asset_buckets_table_name'],
            assets_metadata_table_name=CONFIG['assets_metadata_table_name'],
            asset_batch_size=reindex_settings.get('asset_batch_size', 25),
            file_batch_size=reindex_settings.get('file_batch_size', 100),
            memory_batch_size=reindex_settings.get('memory_batch_size', 1000),
            max_workers=reindex_settings.get('max_workers', 10)
        )
        
        # Step 0: Clear indexes if requested (BEFORE migration)
        clear_results = None
        if CONFIG.get('clear_indexes_before_migration') and not CONFIG['dry_run']:
            logger.info("Step 0: Clearing OpenSearch indexes before migration")
            clear_results = utility.clear_opensearch_indexes(
                asset_index=opensearch_asset_index,
                file_index=opensearch_file_index,
                endpoint=opensearch_endpoint
            )
            
            if not clear_results.get('asset_index', {}).get('success') or \
               not clear_results.get('file_index', {}).get('success'):
                logger.warning("Index clearing completed with issues")
        
        # Step 1: Reindex assets (MUST be done first)
        logger.info("Step 1: Reindexing assets by updating AssetsMetadata table")
        asset_results = utility.reindex_assets(dry_run=CONFIG['dry_run'], limit=args.limit)
        
        # Step 2: Reindex S3 files (done after assets)
        logger.info("Step 2: Reindexing S3 files by updating AssetsMetadata table")
        file_results = utility.reindex_files(dry_run=CONFIG['dry_run'], limit=args.limit)
        
        # Step 3: Wait for DynamoDB Streams processing (if not dry run)
        verification_results = None
        if not CONFIG['dry_run'] and not args.skip_verification:
            wait_time = CONFIG.get('verification_wait_time', 120)
            logger.info(f"Step 3: Waiting {wait_time} seconds for DynamoDB Streams processing...")
            time.sleep(wait_time)
            
            # Step 4: Verify migration
            logger.info("Step 4: Verifying migration results")
            verification_results = verify_migration(
                session,
                opensearch_asset_index,
                opensearch_file_index,
                opensearch_endpoint,
                asset_results['success_count'],
                file_results['success_count']
            )
        
        migration_end_time = datetime.utcnow()
        migration_duration = (migration_end_time - migration_start_time).total_seconds()
        
        # Generate final report
        final_report = {
            'migration_info': {
                'version': 'v2.2_to_v2.3',
                'started_at': migration_start_time.isoformat(),
                'completed_at': migration_end_time.isoformat(),
                'duration_seconds': migration_duration,
                'dry_run': CONFIG['dry_run']
            },
            'index_clearing': clear_results,
            'asset_reindexing': asset_results,
            'file_reindexing': file_results,
            'verification': verification_results,
            'configuration': {k: v for k, v in CONFIG.items() if 'password' not in k.lower()}
        }
        
        # Save report to file
        timestamp = migration_start_time.strftime("%Y%m%d_%H%M%S")
        report_file = f"reports/migration_report_{timestamp}.json"
        
        try:
            os.makedirs(os.path.dirname(report_file), exist_ok=True)
            with open(report_file, 'w') as f:
                json.dump(final_report, f, indent=2, default=str)
            logger.info(f"Migration report saved to: {report_file}")
        except Exception as e:
            logger.warning(f"Could not save migration report: {e}")
        
        # Print summary
        logger.info("=" * 80)
        logger.info("MIGRATION SUMMARY")
        logger.info("=" * 80)
        logger.info(f"Migration Duration: {migration_duration:.1f} seconds")
        
        if clear_results:
            logger.info(f"Indexes Cleared:")
            logger.info(f"  Asset index: {clear_results.get('asset_index', {}).get('deleted_count', 0)} documents")
            logger.info(f"  File index: {clear_results.get('file_index', {}).get('deleted_count', 0)} documents")
        
        logger.info(f"Assets Processed: {asset_results['success_count']}/{asset_results['total_count']} successful ({asset_results['failed_count']} failed)")
        logger.info(f"Files Processed: {file_results['success_count']}/{file_results['total_count']} successful ({file_results['failed_count']} failed)")
        
        if verification_results and not CONFIG['dry_run']:
            logger.info(f"Asset Index: {verification_results['asset_index']['actual_documents']}/{verification_results['asset_index']['expected_documents']} documents ({verification_results['asset_index']['success_rate']:.1f}%)")
            logger.info(f"File Index: {verification_results['file_index']['actual_documents']}/{verification_results['file_index']['expected_documents']} documents ({verification_results['file_index']['success_rate']:.1f}%)")
            logger.info(f"Overall Success: {'✅ YES' if verification_results['overall_success'] else '❌ NO'}")
        
        logger.info("=" * 80)
        
        # Determine exit code
        if CONFIG['dry_run']:
            logger.info("Dry run completed successfully")
            return 0
        elif verification_results and verification_results['overall_success']:
            logger.info("Migration completed successfully")
            return 0
        elif verification_results:
            logger.warning("Migration completed with issues - check verification results")
            return 1
        else:
            logger.info("Migration completed (verification skipped)")
            return 0
            
    except Exception as e:
        logger.exception(f"Error during migration: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
