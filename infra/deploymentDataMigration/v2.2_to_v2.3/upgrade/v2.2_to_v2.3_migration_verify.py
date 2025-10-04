#!/usr/bin/env python3
# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Verification Script for VAMS v2.2 to v2.3 Migration

This script verifies that the v2.2 to v2.3 migration was successful by:
1. Checking that dual OpenSearch indexes exist and are populated
2. Verifying document counts match expected values from DynamoDB and S3
3. Testing search functionality across both indexes
4. Validating file tag inheritance from parent assets
5. Generating detailed verification reports

Usage:
    python v2.2_to_v2.3_migration_verify.py --profile <aws-profile-name>

Requirements:
    - Python 3.6+
    - boto3
    - opensearch-py
    - aws-requests-auth
    - AWS credentials with permissions to read DynamoDB tables, S3 buckets, and access OpenSearch
"""

import argparse
import boto3
import json
import logging
import os
import sys
import csv
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
    # DynamoDB tables
    "asset_storage_table_name": "YOUR_ASSET_STORAGE_TABLE_NAME",
    "metadata_storage_table_name": "YOUR_METADATA_STORAGE_TABLE_NAME",
    "s3_asset_buckets_table_name": "YOUR_S3_ASSET_BUCKETS_TABLE_NAME",
    
    # SSM parameters for OpenSearch configuration
    "opensearch_asset_index_ssm_param": "YOUR_OPENSEARCH_ASSET_INDEX_SSM_PARAM",
    "opensearch_file_index_ssm_param": "YOUR_OPENSEARCH_FILE_INDEX_SSM_PARAM",
    "opensearch_endpoint_ssm_param": "YOUR_OPENSEARCH_ENDPOINT_SSM_PARAM",
    
    # AWS settings
    "aws_profile": None,
    "aws_region": None,
    
    # Verification settings
    "log_level": "INFO",
    "sample_size": 100,  # Number of records to sample for detailed verification
    "tolerance_percentage": 5.0  # Acceptable difference percentage between expected and actual counts
}

def load_config_from_file(config_file):
    """Load configuration from a JSON file."""
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
    """Create a boto3 session with the specified profile and region."""
    session_args = {}
    if profile_name:
        session_args['profile_name'] = profile_name
    if region:
        session_args['region_name'] = region
        
    return boto3.Session(**session_args)

def get_ssm_parameter_value(ssm_client, parameter_name):
    """Get SSM parameter value."""
    try:
        response = ssm_client.get_parameter(Name=parameter_name)
        return response['Parameter']['Value']
    except Exception as e:
        logger.error(f"Error getting SSM parameter {parameter_name}: {e}")
        raise

def count_table_items(dynamodb, table_name):
    """Count total items in a DynamoDB table."""
    try:
        table = dynamodb.Table(table_name)
        response = table.scan(Select='COUNT')
        count = response['Count']
        
        # Handle pagination for accurate count
        while 'LastEvaluatedKey' in response:
            response = table.scan(
                Select='COUNT',
                ExclusiveStartKey=response['LastEvaluatedKey']
            )
            count += response['Count']
        
        return count
    except Exception as e:
        logger.error(f"Error counting items in table {table_name}: {e}")
        return 0

def count_s3_files(s3_client, dynamodb, s3_asset_buckets_table_name):
    """Count total S3 files across all asset buckets."""
    try:
        # Get all asset buckets
        bucket_response = dynamodb.Table(s3_asset_buckets_table_name).scan()
        buckets = bucket_response.get('Items', [])
        
        total_files = 0
        
        for bucket_info in buckets:
            bucket_name = bucket_info.get('bucketName')
            base_prefix = bucket_info.get('baseAssetsPrefix', '/')
            
            if not bucket_name:
                continue
            
            try:
                # Count objects in bucket with prefix
                paginator = s3_client.get_paginator('list_objects_v2')
                
                for page in paginator.paginate(Bucket=bucket_name, Prefix=base_prefix):
                    objects = page.get('Contents', [])
                    # Count only files (not folders)
                    file_count = sum(1 for obj in objects if not obj['Key'].endswith('/'))
                    total_files += file_count
                    
            except Exception as e:
                logger.warning(f"Error counting files in bucket {bucket_name}: {e}")
                continue
        
        return total_files
    except Exception as e:
        logger.error(f"Error counting S3 files: {e}")
        return 0

# =====================================================================
# VERIFICATION FUNCTIONS
# =====================================================================

def verify_opensearch_indexes(session, opensearch_asset_index, opensearch_file_index, opensearch_endpoint):
    """Verify that OpenSearch indexes exist and get their statistics."""
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
            aws_service='aoss' if os.environ.get('OPENSEARCH_TYPE', 'serverless') == 'serverless' else 'es'
        )
        
        client = OpenSearch(
            hosts=[{'host': host, 'port': 443}],
            http_auth=awsauth,
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection
        )
        
        results = {}
        
        # Check asset index
        try:
            if client.indices.exists(index=opensearch_asset_index):
                asset_stats = client.indices.stats(index=opensearch_asset_index)
                asset_count = asset_stats['indices'][opensearch_asset_index]['total']['docs']['count']
                asset_size = asset_stats['indices'][opensearch_asset_index]['total']['store']['size_in_bytes']
                
                results['asset_index'] = {
                    'exists': True,
                    'document_count': asset_count,
                    'size_bytes': asset_size,
                    'name': opensearch_asset_index
                }
                
                logger.info(f"Asset index '{opensearch_asset_index}': {asset_count} documents, {asset_size} bytes")
            else:
                results['asset_index'] = {
                    'exists': False,
                    'document_count': 0,
                    'size_bytes': 0,
                    'name': opensearch_asset_index,
                    'error': 'Index does not exist'
                }
                logger.error(f"Asset index '{opensearch_asset_index}' does not exist")
                
        except Exception as e:
            results['asset_index'] = {
                'exists': False,
                'document_count': 0,
                'size_bytes': 0,
                'name': opensearch_asset_index,
                'error': str(e)
            }
            logger.error(f"Error checking asset index: {e}")
        
        # Check file index
        try:
            if client.indices.exists(index=opensearch_file_index):
                file_stats = client.indices.stats(index=opensearch_file_index)
                file_count = file_stats['indices'][opensearch_file_index]['total']['docs']['count']
                file_size = file_stats['indices'][opensearch_file_index]['total']['store']['size_in_bytes']
                
                results['file_index'] = {
                    'exists': True,
                    'document_count': file_count,
                    'size_bytes': file_size,
                    'name': opensearch_file_index
                }
                
                logger.info(f"File index '{opensearch_file_index}': {file_count} documents, {file_size} bytes")
            else:
                results['file_index'] = {
                    'exists': False,
                    'document_count': 0,
                    'size_bytes': 0,
                    'name': opensearch_file_index,
                    'error': 'Index does not exist'
                }
                logger.error(f"File index '{opensearch_file_index}' does not exist")
                
        except Exception as e:
            results['file_index'] = {
                'exists': False,
                'document_count': 0,
                'size_bytes': 0,
                'name': opensearch_file_index,
                'error': str(e)
            }
            logger.error(f"Error checking file index: {e}")
        
        return results
        
    except Exception as e:
        logger.error(f"Error verifying OpenSearch indexes: {e}")
        return {
            'asset_index': {'exists': False, 'document_count': 0, 'error': str(e)},
            'file_index': {'exists': False, 'document_count': 0, 'error': str(e)}
        }

def test_search_functionality(session, opensearch_asset_index, opensearch_file_index, opensearch_endpoint):
    """Test basic search functionality across both indexes."""
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
            aws_service='aoss' if os.environ.get('OPENSEARCH_TYPE', 'serverless') == 'serverless' else 'es'
        )
        
        client = OpenSearch(
            hosts=[{'host': host, 'port': 443}],
            http_auth=awsauth,
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection
        )
        
        search_results = {}
        
        # Test asset index search
        try:
            asset_search = client.search(
                index=opensearch_asset_index,
                body={
                    "query": {"match_all": {}},
                    "size": 5
                }
            )
            
            search_results['asset_search'] = {
                'success': True,
                'total_hits': asset_search['hits']['total']['value'],
                'returned_hits': len(asset_search['hits']['hits']),
                'sample_fields': list(asset_search['hits']['hits'][0]['_source'].keys()) if asset_search['hits']['hits'] else []
            }
            
            logger.info(f"Asset search test: {asset_search['hits']['total']['value']} total hits")
            
        except Exception as e:
            search_results['asset_search'] = {
                'success': False,
                'error': str(e)
            }
            logger.error(f"Asset search test failed: {e}")
        
        # Test file index search
        try:
            file_search = client.search(
                index=opensearch_file_index,
                body={
                    "query": {"match_all": {}},
                    "size": 5
                }
            )
            
            search_results['file_search'] = {
                'success': True,
                'total_hits': file_search['hits']['total']['value'],
                'returned_hits': len(file_search['hits']['hits']),
                'sample_fields': list(file_search['hits']['hits'][0]['_source'].keys()) if file_search['hits']['hits'] else []
            }
            
            logger.info(f"File search test: {file_search['hits']['total']['value']} total hits")
            
        except Exception as e:
            search_results['file_search'] = {
                'success': False,
                'error': str(e)
            }
            logger.error(f"File search test failed: {e}")
        
        # Test tag aggregation
        try:
            tag_aggregation = client.search(
                index=f"{opensearch_asset_index},{opensearch_file_index}",
                body={
                    "query": {"match_all": {}},
                    "size": 0,
                    "aggs": {
                        "tags": {
                            "terms": {
                                "field": "list_tags.keyword",
                                "size": 10
                            }
                        }
                    }
                }
            )
            
            tag_buckets = tag_aggregation.get('aggregations', {}).get('tags', {}).get('buckets', [])
            search_results['tag_aggregation'] = {
                'success': True,
                'unique_tags': len(tag_buckets),
                'sample_tags': [bucket['key'] for bucket in tag_buckets[:5]]
            }
            
            logger.info(f"Tag aggregation test: {len(tag_buckets)} unique tags found")
            
        except Exception as e:
            search_results['tag_aggregation'] = {
                'success': False,
                'error': str(e)
            }
            logger.error(f"Tag aggregation test failed: {e}")
        
        return search_results
        
    except Exception as e:
        logger.error(f"Error testing search functionality: {e}")
        return {
            'asset_search': {'success': False, 'error': str(e)},
            'file_search': {'success': False, 'error': str(e)},
            'tag_aggregation': {'success': False, 'error': str(e)}
        }

def verify_file_tag_inheritance(session, opensearch_file_index, opensearch_endpoint, sample_size=10):
    """Verify that files have inherited tags from their parent assets."""
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
            aws_service='aoss' if os.environ.get('OPENSEARCH_TYPE', 'serverless') == 'serverless' else 'es'
        )
        
        client = OpenSearch(
            hosts=[{'host': host, 'port': 443}],
            http_auth=awsauth,
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection
        )
        
        # Search for files with tags
        search_response = client.search(
            index=opensearch_file_index,
            body={
                "query": {
                    "bool": {
                        "must": [
                            {"exists": {"field": "list_tags"}},
                            {"bool": {"must_not": {"term": {"list_tags.keyword": ""}}}}
                        ]
                    }
                },
                "size": sample_size
            }
        )
        
        files_with_tags = search_response['hits']['hits']
        total_files_with_tags = search_response['hits']['total']['value']
        
        # Search for files without tags
        search_no_tags = client.search(
            index=opensearch_file_index,
            body={
                "query": {
                    "bool": {
                        "must_not": [
                            {"exists": {"field": "list_tags"}},
                            {"bool": {"must": {"term": {"list_tags.keyword": ""}}}}
                        ]
                    }
                },
                "size": 5
            }
        )
        
        files_without_tags = search_no_tags['hits']['total']['value']
        
        tag_inheritance_results = {
            'files_with_tags': total_files_with_tags,
            'files_without_tags': files_without_tags,
            'sample_files': []
        }
        
        # Analyze sample files
        for hit in files_with_tags:
            source = hit['_source']
            tag_inheritance_results['sample_files'].append({
                'file_path': source.get('str_key'),
                'asset_id': source.get('str_assetid'),
                'database_id': source.get('str_databaseid'),
                'tags': source.get('list_tags', []),
                'tag_count': len(source.get('list_tags', []))
            })
        
        logger.info(f"Tag inheritance verification: {total_files_with_tags} files with tags, {files_without_tags} files without tags")
        
        return tag_inheritance_results
        
    except Exception as e:
        logger.error(f"Error verifying file tag inheritance: {e}")
        return {
            'files_with_tags': 0,
            'files_without_tags': 0,
            'sample_files': [],
            'error': str(e)
        }

def generate_verification_report(verification_results, output_file=None):
    """Generate a detailed verification report."""
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    
    if not output_file:
        output_file = f"reports/verification_report_{timestamp}.json"
    
    try:
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        
        with open(output_file, 'w') as f:
            json.dump(verification_results, f, indent=2, default=str)
        
        logger.info(f"Verification report saved to: {output_file}")
        
        # Also create a CSV summary
        csv_file = output_file.replace('.json', '_summary.csv')
        
        with open(csv_file, 'w', newline='') as csvfile:
            writer = csv.writer(csvfile)
            
            # Write header
            writer.writerow(['Component', 'Status', 'Expected', 'Actual', 'Success Rate', 'Notes'])
            
            # Write asset index results
            asset_info = verification_results.get('index_verification', {}).get('asset_index', {})
            writer.writerow([
                'Asset Index',
                'EXISTS' if asset_info.get('exists') else 'MISSING',
                verification_results.get('expected_counts', {}).get('assets', 0),
                asset_info.get('document_count', 0),
                f"{((asset_info.get('document_count', 0) / verification_results.get('expected_counts', {}).get('assets', 1)) * 100):.1f}%",
                asset_info.get('error', '')
            ])
            
            # Write file index results
            file_info = verification_results.get('index_verification', {}).get('file_index', {})
            writer.writerow([
                'File Index',
                'EXISTS' if file_info.get('exists') else 'MISSING',
                verification_results.get('expected_counts', {}).get('files', 0),
                file_info.get('document_count', 0),
                f"{((file_info.get('document_count', 0) / verification_results.get('expected_counts', {}).get('files', 1)) * 100):.1f}%",
                file_info.get('error', '')
            ])
            
            # Write search functionality results
            search_info = verification_results.get('search_functionality', {})
            writer.writerow([
                'Asset Search',
                'WORKING' if search_info.get('asset_search', {}).get('success') else 'FAILED',
                '',
                search_info.get('asset_search', {}).get('total_hits', 0),
                '',
                search_info.get('asset_search', {}).get('error', '')
            ])
            
            writer.writerow([
                'File Search',
                'WORKING' if search_info.get('file_search', {}).get('success') else 'FAILED',
                '',
                search_info.get('file_search', {}).get('total_hits', 0),
                '',
                search_info.get('file_search', {}).get('error', '')
            ])
            
            # Write tag inheritance results
            tag_info = verification_results.get('tag_inheritance', {})
            writer.writerow([
                'File Tag Inheritance',
                'WORKING' if 'error' not in tag_info else 'FAILED',
                '',
                tag_info.get('files_with_tags', 0),
                '',
                tag_info.get('error', '')
            ])
        
        logger.info(f"Verification summary saved to: {csv_file}")
        
    except Exception as e:
        logger.error(f"Error generating verification report: {e}")

# =====================================================================
# MAIN FUNCTION
# =====================================================================

def main():
    """Main function to run the verification."""
    parser = argparse.ArgumentParser(description='VAMS v2.2 to v2.3 Migration Verification Script')
    parser.add_argument('--profile', help='AWS profile name to use')
    parser.add_argument('--region', help='AWS region to use')
    parser.add_argument('--asset-storage-table', help='Name of the asset storage table')
    parser.add_argument('--metadata-storage-table', help='Name of the metadata storage table')
    parser.add_argument('--s3-asset-buckets-table', help='Name of the S3 asset buckets table')
    parser.add_argument('--opensearch-asset-index-ssm', help='SSM parameter for asset index name')
    parser.add_argument('--opensearch-file-index-ssm', help='SSM parameter for file index name')
    parser.add_argument('--opensearch-endpoint-ssm', help='SSM parameter for OpenSearch endpoint')
    parser.add_argument('--config', help='Path to configuration file')
    parser.add_argument('--output', help='Path to output verification report file')
    parser.add_argument('--log-level', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'], 
                        help='Logging level')
    parser.add_argument('--sample-size', type=int, help='Number of records to sample for detailed verification')
    parser.add_argument('--tolerance', type=float, help='Acceptable difference percentage between expected and actual counts')
    
    args = parser.parse_args()
    
    # Load configuration from file if provided
    if args.config:
        file_config = load_config_from_file(args.config)
        CONFIG.update(file_config)
    
    # Update configuration with command line arguments
    if args.asset_storage_table:
        CONFIG['asset_storage_table_name'] = args.asset_storage_table
    if args.metadata_storage_table:
        CONFIG['metadata_storage_table_name'] = args.metadata_storage_table
    if args.s3_asset_buckets_table:
        CONFIG['s3_asset_buckets_table_name'] = args.s3_asset_buckets_table
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
    if args.log_level:
        CONFIG['log_level'] = args.log_level
    if args.sample_size:
        CONFIG['sample_size'] = args.sample_size
    if args.tolerance:
        CONFIG['tolerance_percentage'] = args.tolerance
        
    # Set logging level
    logging.getLogger().setLevel(getattr(logging, CONFIG.get('log_level', 'INFO')))
    
    # Validate configuration
    required_configs = [
        'asset_storage_table_name',
        'metadata_storage_table_name',
        's3_asset_buckets_table_name',
        'opensearch_asset_index_ssm_param',
        'opensearch_file_index_ssm_param',
        'opensearch_endpoint_ssm_param'
    ]
    
    for config_key in required_configs:
        if CONFIG[config_key].startswith('YOUR_'):
            logger.error(f"Please set the {config_key} in the CONFIG or provide it via command line")
            return 1
    
    # Initialize AWS session and clients
    try:
        session = get_aws_session(profile_name=CONFIG.get('aws_profile'), region=CONFIG.get('aws_region'))
        dynamodb = session.resource('dynamodb')
        s3_client = session.client('s3')
        ssm_client = session.client('ssm')
    except Exception as e:
        logger.error(f"Error initializing AWS clients: {e}")
        return 1
    
    logger.info("Starting VAMS v2.2 to v2.3 migration verification")
    logger.info(f"Configuration: {json.dumps({k: v for k, v in CONFIG.items() if 'password' not in k.lower()}, indent=2)}")
    
    # Get OpenSearch configuration from SSM
    try:
        logger.info("Loading OpenSearch configuration from SSM parameters")
        opensearch_asset_index = get_ssm_parameter_value(ssm_client, CONFIG['opensearch_asset_index_ssm_param'])
        opensearch_file_index = get_ssm_parameter_value(ssm_client, CONFIG['opensearch_file_index_ssm_param'])
        opensearch_endpoint = get_ssm_parameter_value(ssm_client, CONFIG['opensearch_endpoint_ssm_param'])
        
        logger.info(f"OpenSearch configuration loaded:")
        logger.info(f"  Asset index: {opensearch_asset_index}")
        logger.info(f"  File index: {opensearch_file_index}")
        logger.info(f"  Endpoint: {opensearch_endpoint}")
        
    except Exception as e:
        logger.error(f"Error loading OpenSearch configuration from SSM: {e}")
        return 1
    
    # Perform verification
    try:
        verification_start_time = datetime.utcnow()
        
        # Step 1: Count expected documents
        logger.info("Step 1: Counting expected documents from source data")
        expected_assets = count_table_items(dynamodb, CONFIG['asset_storage_table_name'])
        expected_files = count_s3_files(s3_client, dynamodb, CONFIG['s3_asset_buckets_table_name'])
        
        logger.info(f"Expected documents: {expected_assets} assets, {expected_files} files")
        
        # Step 2: Verify OpenSearch indexes
        logger.info("Step 2: Verifying OpenSearch indexes")
        index_verification = verify_opensearch_indexes(session, opensearch_asset_index, opensearch_file_index, opensearch_endpoint)
        
        # Step 3: Test search functionality
        logger.info("Step 3: Testing search functionality")
        search_functionality = test_search_functionality(session, opensearch_asset_index, opensearch_file_index, opensearch_endpoint)
        
        # Step 4: Verify file tag inheritance
        logger.info("Step 4: Verifying file tag inheritance")
        tag_inheritance = verify_file_tag_inheritance(session, opensearch_file_index, opensearch_endpoint, CONFIG['sample_size'])
        
        verification_end_time = datetime.utcnow()
        verification_duration = (verification_end_time - verification_start_time).total_seconds()
        
        # Calculate success rates
        asset_success_rate = 0
        file_success_rate = 0
        
        if expected_assets > 0:
            asset_success_rate = (index_verification['asset_index']['document_count'] / expected_assets) * 100
        
        if expected_files > 0:
            file_success_rate = (index_verification['file_index']['document_count'] / expected_files) * 100
        
        # Determine overall success
        tolerance = CONFIG.get('tolerance_percentage', 5.0)
        overall_success = (
            index_verification['asset_index']['exists'] and
            index_verification['file_index']['exists'] and
            asset_success_rate >= (100 - tolerance) and
            file_success_rate >= (100 - tolerance) and
            search_functionality['asset_search']['success'] and
            search_functionality['file_search']['success']
        )
        
        # Compile verification results
        verification_results = {
            'verification_info': {
                'version': 'v2.2_to_v2.3',
                'started_at': verification_start_time.isoformat(),
                'completed_at': verification_end_time.isoformat(),
                'duration_seconds': verification_duration
            },
            'expected_counts': {
                'assets': expected_assets,
                'files': expected_files
            },
            'index_verification': index_verification,
            'search_functionality': search_functionality,
            'tag_inheritance': tag_inheritance,
            'success_rates': {
                'asset_index': round(asset_success_rate, 2),
                'file_index': round(file_success_rate, 2)
            },
            'overall_success': overall_success,
            'configuration': {k: v for k, v in CONFIG.items() if 'password' not in k.lower()}
        }
        
        # Generate verification report
        generate_verification_report(verification_results, args.output)
        
        # Print summary
        logger.info("=" * 80)
        logger.info("VERIFICATION SUMMARY")
        logger.info("=" * 80)
        logger.info(f"Verification Duration: {verification_duration:.1f} seconds")
        logger.info(f"Expected Assets: {expected_assets}")
        logger.info(f"Expected Files: {expected_files}")
        logger.info(f"Asset Index: {index_verification['asset_index']['document_count']}/{expected_assets} documents ({asset_success_rate:.1f}%)")
        logger.info(f"File Index: {index_verification['file_index']['document_count']}/{expected_files} documents ({file_success_rate:.1f}%)")
        logger.info(f"Asset Search: {'✅ WORKING' if search_functionality['asset_search']['success'] else '❌ FAILED'}")
        logger.info(f"File Search: {'✅ WORKING' if search_functionality['file_search']['success'] else '❌ FAILED'}")
        logger.info(f"Tag Aggregation: {'✅ WORKING' if search_functionality['tag_aggregation']['success'] else '❌ FAILED'}")
        logger.info(f"File Tag Inheritance: {tag_inheritance['files_with_tags']} files with tags")
        logger.info(f"Overall Success: {'✅ YES' if overall_success else '❌ NO'}")
        logger.info("=" * 80)
        
        # Determine exit code
        if overall_success:
            logger.info("Verification completed successfully - migration is working correctly")
            return 0
        else:
            logger.warning("Verification found issues - check the detailed report for more information")
            return 1
            
    except Exception as e:
        logger.error(f"Error during verification: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
