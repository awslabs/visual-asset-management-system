#!/usr/bin/env python3
# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
VAMS OpenSearch Reindexer Lambda Function

This Lambda function provides reindexing capabilities for VAMS OpenSearch indexes.
It can be invoked directly or triggered via CloudFormation custom resources.

Key Features:
- Reindex assets and/or files by updating AssetsMetadata DynamoDB table
- DynamoDB Streams automatically trigger OpenSearch indexing
- Supports both direct Lambda invocation and CloudFormation custom resource events
- All configuration read from environment variables
- Comprehensive error handling and logging

Environment Variables Required:
- ASSET_STORAGE_TABLE_NAME: DynamoDB table for assets
- S3_ASSET_BUCKETS_STORAGE_TABLE_NAME: DynamoDB table for S3 bucket configs
- METADATA_STORAGE_TABLE_NAME: DynamoDB table for metadata (target for updates)
- OPENSEARCH_ASSET_INDEX_SSM_PARAM: SSM parameter for asset index name
- OPENSEARCH_FILE_INDEX_SSM_PARAM: SSM parameter for file index name
- OPENSEARCH_ENDPOINT_SSM_PARAM: SSM parameter for OpenSearch endpoint
- OPENSEARCH_TYPE: Type of OpenSearch deployment (serverless or provisioned)

Usage:
    Direct Invocation:
    {
        "operation": "both",  # or "assets" or "files"
        "dry_run": false,
        "limit": null  # optional limit for testing
    }
    
    Custom Resource (automatic):
    Triggered by CloudFormation during stack operations
"""

import json
import logging
import os
import time
import urllib3
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Any
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
dynamodb_client = boto3.client('dynamodb')
dynamodb_resource = boto3.resource('dynamodb')
s3_client = boto3.client('s3')
ssm_client = boto3.client('ssm')

# HTTP client for CloudFormation responses
http = urllib3.PoolManager()

# Environment Variables - Loaded at module initialization
ASSET_STORAGE_TABLE_NAME = os.environ.get('ASSET_STORAGE_TABLE_NAME', '')
S3_ASSET_BUCKETS_STORAGE_TABLE_NAME = os.environ.get('S3_ASSET_BUCKETS_STORAGE_TABLE_NAME', '')
METADATA_STORAGE_TABLE_NAME = os.environ.get('METADATA_STORAGE_TABLE_NAME', '')
OPENSEARCH_ASSET_INDEX_SSM_PARAM = os.environ.get('OPENSEARCH_ASSET_INDEX_SSM_PARAM', '')
OPENSEARCH_FILE_INDEX_SSM_PARAM = os.environ.get('OPENSEARCH_FILE_INDEX_SSM_PARAM', '')
OPENSEARCH_ENDPOINT_SSM_PARAM = os.environ.get('OPENSEARCH_ENDPOINT_SSM_PARAM', '')
OPENSEARCH_TYPE = os.environ.get('OPENSEARCH_TYPE', 'serverless')

# AWS region for OpenSearch authentication
AWS_REGION = os.environ.get('AWS_REGION', 'us-east-1')


class DecimalEncoder(json.JSONEncoder):
    """Helper class to convert Decimal to int/float for JSON serialization"""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return int(obj) if obj % 1 == 0 else float(obj)
        return super(DecimalEncoder, self).default(obj)


class ReindexUtility:
    """
    Utility class for triggering OpenSearch reindexing of VAMS assets and files
    by updating the AssetsMetadata DynamoDB table.
    """
    
    def __init__(
        self,
        asset_table_name: str,
        s3_buckets_table_name: str,
        assets_metadata_table_name: str,
        asset_batch_size: int = 25,
        file_batch_size: int = 100,
        memory_batch_size: int = 1000
    ):
        """
        Initialize the reindex utility.
        
        Args:
            asset_table_name: Name of the DynamoDB asset storage table (source)
            s3_buckets_table_name: Name of the DynamoDB S3 buckets table (source)
            assets_metadata_table_name: Name of the AssetsMetadata table (target)
            asset_batch_size: Number of assets to process in each DynamoDB batch (max 25)
            file_batch_size: Number of files to process in each batch
            memory_batch_size: Number of items to load into memory before batch writing
        """
        self.asset_table_name = asset_table_name
        self.s3_buckets_table_name = s3_buckets_table_name
        self.assets_metadata_table_name = assets_metadata_table_name
        self.asset_batch_size = min(asset_batch_size, 25)  # DynamoDB batch limit
        self.file_batch_size = file_batch_size
        self.memory_batch_size = memory_batch_size
        
        logger.info(f"ReindexUtility initialized:")
        logger.info(f"  Asset table (source): {asset_table_name}")
        logger.info(f"  S3 buckets table (source): {s3_buckets_table_name}")
        logger.info(f"  AssetsMetadata table (target): {assets_metadata_table_name}")
    
    def clear_opensearch_indexes(
        self,
        asset_index: str,
        file_index: str,
        endpoint: str
    ) -> Dict:
        """
        Clear all documents from OpenSearch indexes without deleting the indexes.
        
        Args:
            asset_index: Name of the asset index
            file_index: Name of the file index
            endpoint: OpenSearch endpoint URL
            
        Returns:
            dict: Results with deleted document counts
        """
        logger.info("=" * 80)
        logger.info("CLEARING OPENSEARCH INDEXES")
        logger.info("=" * 80)
        
        results = {
            'asset_index': {
                'name': asset_index,
                'deleted_count': 0,
                'success': False
            },
            'file_index': {
                'name': file_index,
                'deleted_count': 0,
                'success': False
            }
        }
        
        try:
            from opensearchpy import OpenSearch, RequestsHttpConnection, AWSV4SignerAuth
            from botocore.session import Session
            
            # Create OpenSearch client with AWS authentication
            host = endpoint.replace('https://', '').replace('http://', '')
            service = 'aoss' if OPENSEARCH_TYPE == 'serverless' else 'es'
            
            # Get AWS credentials from boto3 session
            session = Session()
            credentials = session.get_credentials()
            
            # Use AWSV4SignerAuth for AWS authentication
            awsauth = AWSV4SignerAuth(credentials, AWS_REGION, service)
            
            client = OpenSearch(
                hosts=[{'host': host, 'port': 443}],
                http_auth=awsauth,
                use_ssl=True,
                verify_certs=True,
                connection_class=RequestsHttpConnection,
                pool_maxsize=20,
                timeout=30,
                max_retries=3,
                retry_on_timeout=True
            )
            
            # Clear asset index
            logger.info(f"Clearing asset index: {asset_index}")
            if client.indices.exists(index=asset_index):
                try:
                    # Try delete_by_query first (works for managed OpenSearch)
                    response = client.delete_by_query(
                        index=asset_index,
                        body={"query": {"match_all": {}}}
                    )
                    results['asset_index']['deleted_count'] = response.get('deleted', 0)
                    results['asset_index']['success'] = True
                    logger.info(f"Deleted {results['asset_index']['deleted_count']} documents from {asset_index}")
                except Exception as e:
                    if '404' in str(e) or 'NotFoundError' in str(type(e).__name__):
                        # delete_by_query not supported in OpenSearch Serverless
                        # Use scroll and bulk delete instead
                        logger.info(f"delete_by_query not supported, using scroll and bulk delete for: {asset_index}")
                        deleted_count = self._scroll_and_delete_documents(client, asset_index)
                        results['asset_index']['deleted_count'] = deleted_count
                        results['asset_index']['success'] = True
                        logger.info(f"Deleted {deleted_count} documents from {asset_index}")
                    else:
                        raise
            else:
                logger.warning(f"Asset index {asset_index} does not exist")
            
            # Clear file index
            logger.info(f"Clearing file index: {file_index}")
            if client.indices.exists(index=file_index):
                try:
                    # Try delete_by_query first (works for managed OpenSearch)
                    response = client.delete_by_query(
                        index=file_index,
                        body={"query": {"match_all": {}}}
                    )
                    results['file_index']['deleted_count'] = response.get('deleted', 0)
                    results['file_index']['success'] = True
                    logger.info(f"Deleted {results['file_index']['deleted_count']} documents from {file_index}")
                except Exception as e:
                    if '404' in str(e) or 'NotFoundError' in str(type(e).__name__):
                        # delete_by_query not supported in OpenSearch Serverless
                        # Use scroll and bulk delete instead
                        logger.info(f"delete_by_query not supported, using scroll and bulk delete for: {file_index}")
                        deleted_count = self._scroll_and_delete_documents(client, file_index)
                        results['file_index']['deleted_count'] = deleted_count
                        results['file_index']['success'] = True
                        logger.info(f"Deleted {deleted_count} documents from {file_index}")
                    else:
                        raise
            else:
                logger.warning(f"File index {file_index} does not exist")
            
            logger.info("=" * 80)
            logger.info("INDEX CLEARING COMPLETE")
            logger.info(f"  Asset index: {results['asset_index']['deleted_count']} documents deleted")
            logger.info(f"  File index: {results['file_index']['deleted_count']} documents deleted")
            logger.info("=" * 80)
            
            return results
            
        except Exception as e:
            logger.exception(f"Error clearing OpenSearch indexes: {e}")
            results['error'] = str(e)
            return results
    
    def _scroll_and_delete_documents(self, client, index_name: str) -> int:
        """
        Delete all documents from an index using scroll and bulk delete.
        This is used for OpenSearch Serverless where delete_by_query is not supported.
        
        Args:
            client: OpenSearch client
            index_name: Name of the index to clear
            
        Returns:
            int: Number of documents deleted
        """
        deleted_count = 0
        
        try:
            # Use search with scroll to get all document IDs
            scroll_size = 1000
            scroll_time = '2m'
            
            # Initial search
            response = client.search(
                index=index_name,
                scroll=scroll_time,
                size=scroll_size,
                body={
                    "query": {"match_all": {}},
                    "_source": False  # We only need IDs
                }
            )
            
            scroll_id = response.get('_scroll_id')
            hits = response.get('hits', {}).get('hits', [])
            
            while hits:
                # Build bulk delete operations
                bulk_body = []
                for hit in hits:
                    bulk_body.append({
                        "delete": {
                            "_index": index_name,
                            "_id": hit['_id']
                        }
                    })
                
                # Execute bulk delete
                if bulk_body:
                    bulk_response = client.bulk(body=bulk_body)
                    
                    # Count successful deletes
                    for item in bulk_response.get('items', []):
                        if 'delete' in item and item['delete'].get('result') in ['deleted', 'not_found']:
                            deleted_count += 1
                    
                    logger.info(f"Deleted batch of {len(bulk_body)} documents from {index_name} (total: {deleted_count})")
                
                # Get next batch
                if scroll_id:
                    try:
                        response = client.scroll(scroll_id=scroll_id, scroll=scroll_time)
                        scroll_id = response.get('_scroll_id')
                        hits = response.get('hits', {}).get('hits', [])
                    except Exception as scroll_error:
                        # Scroll may not be fully supported in OpenSearch Serverless
                        # If we get an error, assume we've processed all documents
                        logger.info(f"Scroll continuation not supported or completed: {scroll_error}")
                        break
                else:
                    break
            
            # Clear scroll context
            if scroll_id:
                try:
                    client.clear_scroll(scroll_id=scroll_id)
                except Exception as e:
                    logger.debug(f"Error clearing scroll context (expected in Serverless): {e}")
            
            return deleted_count
            
        except Exception as e:
            logger.exception(f"Error in scroll and delete for {index_name}: {e}")
            return deleted_count
    
    def reindex_assets(
        self,
        dry_run: bool = False,
        limit: Optional[int] = None
    ) -> Dict:
        """
        Reindex assets by inserting/updating records in AssetsMetadata table.
        
        Args:
            dry_run: If True, don't actually update records
            limit: Optional limit on number of assets to process (for testing)
            
        Returns:
            dict: Results with success_count, failed_count, total_count, errors
        """
        logger.info("=" * 80)
        logger.info("ASSET REINDEXING")
        logger.info("=" * 80)
        
        if dry_run:
            logger.info("DRY RUN MODE - No changes will be made")
        
        results = {
            'success_count': 0,
            'failed_count': 0,
            'total_count': 0,
            'errors': [],
            'start_time': datetime.now(timezone.utc).isoformat(),
            'end_time': None
        }
        
        try:
            # Scan asset table
            logger.info(f"Scanning asset table: {self.asset_table_name}")
            assets = self._scan_asset_table(limit)
            results['total_count'] = len(assets)
            
            logger.info(f"Found {len(assets)} assets to reindex")
            
            if len(assets) == 0:
                logger.warning("No assets found in table")
                results['end_time'] = datetime.now(timezone.utc).isoformat()
                return results
            
            # Process assets in memory batches
            current_timestamp = datetime.now(timezone.utc).isoformat()
            
            for i in range(0, len(assets), self.memory_batch_size):
                memory_batch = assets[i:i + self.memory_batch_size]
                batch_num = (i // self.memory_batch_size) + 1
                total_batches = (len(assets) + self.memory_batch_size - 1) // self.memory_batch_size
                
                logger.info(f"Processing memory batch {batch_num}/{total_batches} ({len(memory_batch)} assets)")
                
                if dry_run:
                    results['success_count'] += len(memory_batch)
                    logger.info(f"DRY RUN: Would update {len(memory_batch)} assets")
                else:
                    batch_results = self._update_assets_in_metadata_table(
                        memory_batch,
                        current_timestamp
                    )
                    results['success_count'] += batch_results['success']
                    results['failed_count'] += batch_results['failed']
                    results['errors'].extend(batch_results['errors'])
                
                # Log progress
                if (i + self.memory_batch_size) % 5000 == 0:
                    logger.info(f"Progress: {results['success_count']}/{results['total_count']} assets updated")
            
            results['end_time'] = datetime.now(timezone.utc).isoformat()
            
            logger.info("=" * 80)
            logger.info("ASSET REINDEXING COMPLETE")
            logger.info(f"  Total: {results['total_count']}")
            logger.info(f"  Success: {results['success_count']}")
            logger.info(f"  Failed: {results['failed_count']}")
            logger.info("=" * 80)
            
            return results
            
        except Exception as e:
            logger.exception(f"Error during asset reindexing: {e}")
            results['end_time'] = datetime.now(timezone.utc).isoformat()
            results['errors'].append({'error': str(e), 'type': 'fatal'})
            return results
    
    def reindex_files(
        self,
        dry_run: bool = False,
        limit: Optional[int] = None
    ) -> Dict:
        """
        Reindex files by inserting/updating records in AssetsMetadata table.
        
        Args:
            dry_run: If True, don't actually update records
            limit: Optional limit on number of files to process (for testing)
            
        Returns:
            dict: Results with success_count, failed_count, total_count, errors
        """
        logger.info("=" * 80)
        logger.info("S3 FILE REINDEXING")
        logger.info("=" * 80)
        
        if dry_run:
            logger.info("DRY RUN MODE - No changes will be made")
        
        results = {
            'success_count': 0,
            'failed_count': 0,
            'total_count': 0,
            'buckets_processed': 0,
            'objects_scanned': 0,
            'errors': [],
            'start_time': datetime.now(timezone.utc).isoformat(),
            'end_time': None
        }
        
        try:
            # Get all S3 bucket configurations
            logger.info(f"Scanning S3 buckets table: {self.s3_buckets_table_name}")
            bucket_configs = self._scan_s3_buckets_table()
            
            logger.info(f"Found {len(bucket_configs)} bucket configurations")
            
            if len(bucket_configs) == 0:
                logger.warning("No bucket configurations found")
                results['end_time'] = datetime.now(timezone.utc).isoformat()
                return results
            
            # Process each bucket
            for bucket_config in bucket_configs:
                bucket_name = bucket_config.get('bucketName')
                base_prefix = bucket_config.get('baseAssetsPrefix', '')
                
                if not bucket_name:
                    logger.warning(f"Skipping invalid bucket config: {bucket_config}")
                    continue
                
                logger.info(f"Processing bucket: {bucket_name} (prefix: {base_prefix})")
                
                bucket_results = self._process_bucket(
                    bucket_name,
                    base_prefix,
                    dry_run,
                    limit - results['total_count'] if limit else None
                )
                
                results['success_count'] += bucket_results['success']
                results['failed_count'] += bucket_results['failed']
                results['total_count'] += bucket_results['total']
                results['objects_scanned'] += bucket_results['objects_scanned']
                results['errors'].extend(bucket_results['errors'])
                results['buckets_processed'] += 1
                
                logger.info(f"Bucket {bucket_name} complete: {bucket_results['success']}/{bucket_results['total']} files processed")
                
                # Stop if we've reached the limit
                if limit and results['total_count'] >= limit:
                    logger.info(f"Reached limit of {limit} files")
                    break
            
            results['end_time'] = datetime.now(timezone.utc).isoformat()
            
            logger.info("=" * 80)
            logger.info("S3 FILE REINDEXING COMPLETE")
            logger.info(f"  Buckets processed: {results['buckets_processed']}")
            logger.info(f"  Objects scanned: {results['objects_scanned']}")
            logger.info(f"  Valid files processed: {results['total_count']}")
            logger.info(f"  Success: {results['success_count']}")
            logger.info(f"  Failed: {results['failed_count']}")
            logger.info("=" * 80)
            
            return results
            
        except Exception as e:
            logger.exception(f"Error during S3 file reindexing: {e}")
            results['end_time'] = datetime.now(timezone.utc).isoformat()
            results['errors'].append({'error': str(e), 'type': 'fatal'})
            return results
    
    def _scan_asset_table(self, limit: Optional[int] = None) -> List[Dict]:
        """Scan the asset storage table and return all asset records."""
        table = dynamodb_resource.Table(self.asset_table_name)
        assets = []
        
        try:
            scan_kwargs = {}
            if limit:
                scan_kwargs['Limit'] = limit
            
            response = table.scan(**scan_kwargs)
            assets.extend(response.get('Items', []))
            
            # Paginate if necessary
            while 'LastEvaluatedKey' in response and (not limit or len(assets) < limit):
                scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
                response = table.scan(**scan_kwargs)
                assets.extend(response.get('Items', []))
                
                if len(assets) % 1000 == 0:
                    logger.info(f"Scanned {len(assets)} assets...")
            
            # Filter valid assets
            valid_assets = [
                asset for asset in assets
                if asset.get('databaseId') and asset.get('assetId')
            ]
            
            if len(valid_assets) < len(assets):
                logger.warning(f"Filtered out {len(assets) - len(valid_assets)} invalid asset records")
            
            return valid_assets
            
        except ClientError as e:
            logger.error(f"Error scanning asset table: {e}")
            raise
    
    def _scan_s3_buckets_table(self) -> List[Dict]:
        """Scan the S3 buckets table and return all bucket configurations."""
        table = dynamodb_resource.Table(self.s3_buckets_table_name)
        
        try:
            response = table.scan()
            buckets = response.get('Items', [])
            
            while 'LastEvaluatedKey' in response:
                response = table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
                buckets.extend(response.get('Items', []))
            
            return buckets
            
        except ClientError as e:
            logger.error(f"Error scanning S3 buckets table: {e}")
            raise
    
    def _batch_get_existing_records(
        self,
        keys: List[Tuple[str, str]]
    ) -> Dict[str, Dict]:
        """Batch get existing metadata records from AssetsMetadata table."""
        if not keys:
            return {}
        
        records = {}
        
        try:
            # Process in batches of 100 (DynamoDB BatchGetItem limit)
            batch_size = 100
            for i in range(0, len(keys), batch_size):
                batch_keys = keys[i:i + batch_size]
                
                # Build request items in DynamoDB low-level format
                request_items = {
                    self.assets_metadata_table_name: {
                        'Keys': [
                            {
                                'databaseId': {'S': db_id},
                                'assetId': {'S': asset_id}
                            }
                            for db_id, asset_id in batch_keys
                        ]
                    }
                }
                
                # Batch get with retry for unprocessed keys
                response = dynamodb_client.batch_get_item(RequestItems=request_items)
                
                # Process responses
                if self.assets_metadata_table_name in response.get('Responses', {}):
                    for item in response['Responses'][self.assets_metadata_table_name]:
                        # Convert from low-level format to high-level format
                        from boto3.dynamodb.types import TypeDeserializer
                        deserializer = TypeDeserializer()
                        deserialized_item = {k: deserializer.deserialize(v) for k, v in item.items()}
                        
                        record_key = f"{deserialized_item['databaseId']}#{deserialized_item['assetId']}"
                        records[record_key] = deserialized_item
                
                # Handle unprocessed keys
                unprocessed = response.get('UnprocessedKeys', {})
                while unprocessed:
                    logger.warning(f"Retrying {len(unprocessed.get(self.assets_metadata_table_name, {}).get('Keys', []))} unprocessed keys")
                    time.sleep(0.5)  # Brief delay before retry
                    response = dynamodb_client.batch_get_item(RequestItems=unprocessed)
                    
                    if self.assets_metadata_table_name in response.get('Responses', {}):
                        for item in response['Responses'][self.assets_metadata_table_name]:
                            from boto3.dynamodb.types import TypeDeserializer
                            deserializer = TypeDeserializer()
                            deserialized_item = {k: deserializer.deserialize(v) for k, v in item.items()}
                            
                            record_key = f"{deserialized_item['databaseId']}#{deserialized_item['assetId']}"
                            records[record_key] = deserialized_item
                    
                    unprocessed = response.get('UnprocessedKeys', {})
            
            return records
            
        except Exception as e:
            logger.error(f"Error batch getting metadata records: {e}")
            return {}
    
    def _update_assets_in_metadata_table(
        self,
        assets: List[Dict],
        timestamp: str
    ) -> Dict:
        """Update a batch of assets in the AssetsMetadata table."""
        table = dynamodb_resource.Table(self.assets_metadata_table_name)
        results = {'success': 0, 'failed': 0, 'errors': []}
        
        try:
            # Batch fetch existing records for efficiency
            existing_records = self._batch_get_existing_records(
                [(asset['databaseId'], asset['assetId']) for asset in assets]
            )
            
            # Process in DynamoDB batch size chunks
            for i in range(0, len(assets), self.asset_batch_size):
                batch = assets[i:i + self.asset_batch_size]
                
                with table.batch_writer() as batch_writer:
                    for asset in batch:
                        try:
                            database_id = asset['databaseId']
                            asset_id = asset['assetId']
                            
                            # Get existing metadata record from batch fetch
                            record_key = f"{database_id}#{asset_id}"
                            existing_record = existing_records.get(record_key)
                            
                            # Merge with existing attributes
                            if existing_record:
                                # Preserve all existing attributes
                                metadata_record = existing_record.copy()
                            else:
                                # Create new record with minimal keys
                                metadata_record = {
                                    'databaseId': database_id,
                                    'assetId': asset_id
                                }
                            
                            # Update timestamp
                            metadata_record['_asset_table_updated'] = timestamp
                            
                            # Write to table
                            batch_writer.put_item(Item=metadata_record)
                            results['success'] += 1
                            
                        except Exception as e:
                            results['failed'] += 1
                            error_msg = f"Failed to update asset {asset.get('assetId')}: {str(e)}"
                            logger.error(error_msg)
                            results['errors'].append({
                                'assetId': asset.get('assetId'),
                                'error': str(e)
                            })
            
            return results
            
        except Exception as e:
            logger.error(f"Error in batch update: {e}")
            results['failed'] = len(assets) - results['success']
            results['errors'].append({'error': str(e), 'type': 'batch_error'})
            return results
    
    def _process_bucket(
        self,
        bucket_name: str,
        base_prefix: str,
        dry_run: bool,
        limit: Optional[int] = None
    ) -> Dict:
        """Process all objects in a bucket and update AssetsMetadata table."""
        # Excluded patterns and prefixes from fileIndexer
        excluded_prefixes = ['pipeline', 'pipelines', 'preview', 'previews', 'temp-upload', 'temp-uploads']
        excluded_patterns = ['.previewFile.']
        
        results = {
            'success': 0,
            'failed': 0,
            'total': 0,
            'objects_scanned': 0,
            'skipped_excluded': 0,
            'errors': []
        }
        
        try:
            # Collect files in memory batches
            files_batch = []
            current_timestamp = datetime.now(timezone.utc).isoformat()
            
            # Normalize base prefix - remove leading slash if present
            if base_prefix.startswith('/'):
                base_prefix = base_prefix[1:]
            
            # List all objects in the bucket recursively
            paginator = s3_client.get_paginator('list_objects_v2')
            
            # Use empty prefix to list all objects in bucket
            pagination_config = {'Bucket': bucket_name}
            if base_prefix and base_prefix != '/':
                pagination_config['Prefix'] = base_prefix
            
            for page in paginator.paginate(**pagination_config):
                objects = page.get('Contents', [])
                results['objects_scanned'] += len(objects)
                
                for obj in objects:
                    # Skip folder markers
                    if obj['Key'].endswith('/'):
                        continue
                    
                    # Check for excluded patterns in the key
                    s3_key = obj['Key']
                    
                    # Skip if key contains any excluded patterns
                    if any(pattern in s3_key for pattern in excluded_patterns):
                        results['skipped_excluded'] += 1
                        continue
                    
                    # Check if any path component starts with excluded prefixes
                    path_parts = s3_key.split('/')
                    skip_file = False
                    for part in path_parts:
                        if any(part.startswith(prefix) for prefix in excluded_prefixes):
                            results['skipped_excluded'] += 1
                            skip_file = True
                            break
                    
                    if skip_file:
                        continue
                    
                    # Stop if we've reached the limit
                    if limit and results['total'] >= limit:
                        break
                    
                    try:
                        # Get object metadata
                        head_response = s3_client.head_object(
                            Bucket=bucket_name,
                            Key=obj['Key']
                        )
                        
                        metadata = head_response.get('Metadata', {})
                        
                        # Try both lowercase and original case for metadata keys
                        asset_id = metadata.get('assetid') or metadata.get('assetId')
                        database_id = metadata.get('databaseid') or metadata.get('databaseId')
                        
                        # Only process files with asset metadata
                        if not asset_id or not database_id:
                            continue
                        
                        # Calculate relative path from base prefix
                        relative_path = obj['Key']
                        if base_prefix and obj['Key'].startswith(base_prefix):
                            relative_path = obj['Key'][len(base_prefix):].lstrip('/')
                        
                        # Check if the relative path already starts with the asset ID
                        if relative_path.startswith(f"{asset_id}/"):
                            # Path already includes assetId, just prepend with /
                            formatted_asset_id = f"/{relative_path}"
                        else:
                            # Path doesn't include assetId, add it
                            formatted_asset_id = f"/{asset_id}/{relative_path}"
                        
                        files_batch.append({
                            'databaseId': database_id,
                            'assetId': formatted_asset_id,
                            'original_asset_id': asset_id,
                            'relative_path': relative_path
                        })
                        
                        results['total'] += 1
                        
                        # Process batch when it reaches memory_batch_size
                        if len(files_batch) >= self.memory_batch_size:
                            if dry_run:
                                results['success'] += len(files_batch)
                            else:
                                batch_results = self._update_files_in_metadata_table(
                                    files_batch,
                                    current_timestamp
                                )
                                results['success'] += batch_results['success']
                                results['failed'] += batch_results['failed']
                                results['errors'].extend(batch_results['errors'])
                            
                            files_batch = []
                        
                    except Exception as e:
                        results['failed'] += 1
                        error_msg = f"Error processing {obj['Key']}: {str(e)}"
                        logger.warning(error_msg)
                        results['errors'].append({
                            'key': obj['Key'],
                            'error': str(e)
                        })
                
                # Log progress every 1000 objects
                if results['objects_scanned'] % 1000 == 0:
                    logger.info(f"Scanned {results['objects_scanned']} objects in {bucket_name}, processed {results['total']} files")
                
                # Break if we've reached the limit
                if limit and results['total'] >= limit:
                    break
            
            # Process remaining files in batch
            if files_batch:
                if dry_run:
                    results['success'] += len(files_batch)
                else:
                    batch_results = self._update_files_in_metadata_table(
                        files_batch,
                        current_timestamp
                    )
                    results['success'] += batch_results['success']
                    results['failed'] += batch_results['failed']
                    results['errors'].extend(batch_results['errors'])
            
            return results
            
        except Exception as e:
            logger.error(f"Error processing bucket {bucket_name}: {e}")
            results['errors'].append({
                'bucket': bucket_name,
                'error': str(e),
                'type': 'bucket_error'
            })
            return results
    
    def _update_files_in_metadata_table(
        self,
        files: List[Dict],
        timestamp: str
    ) -> Dict:
        """Update a batch of files in the AssetsMetadata table."""
        table = dynamodb_resource.Table(self.assets_metadata_table_name)
        results = {'success': 0, 'failed': 0, 'errors': []}
        
        try:
            # Batch fetch existing records for efficiency
            existing_records = self._batch_get_existing_records(
                [(file_record['databaseId'], file_record['assetId']) for file_record in files]
            )
            
            # Process in DynamoDB batch size chunks (max 25 for batch_writer)
            batch_size = min(self.file_batch_size, 25)
            for i in range(0, len(files), batch_size):
                batch = files[i:i + batch_size]
                
                with table.batch_writer() as batch_writer:
                    for file_record in batch:
                        try:
                            database_id = file_record['databaseId']
                            asset_id = file_record['assetId']  # Already formatted
                            
                            # Get existing metadata record from batch fetch
                            record_key = f"{database_id}#{asset_id}"
                            existing_record = existing_records.get(record_key)
                            
                            # Merge with existing attributes
                            if existing_record:
                                # Preserve all existing attributes
                                metadata_record = existing_record.copy()
                            else:
                                # Create new record with minimal keys
                                metadata_record = {
                                    'databaseId': database_id,
                                    'assetId': asset_id
                                }
                            
                            # Update timestamp
                            metadata_record['_asset_table_updated'] = timestamp
                            
                            # Write to table
                            batch_writer.put_item(Item=metadata_record)
                            results['success'] += 1
                            
                        except Exception as e:
                            results['failed'] += 1
                            error_msg = f"Failed to update file {file_record.get('assetId')}: {str(e)}"
                            logger.error(error_msg)
                            results['errors'].append({
                                'assetId': file_record.get('assetId'),
                                'error': str(e)
                            })
            
            return results
            
        except Exception as e:
            logger.error(f"Error in batch update: {e}")
            results['failed'] = len(files) - results['success']
            results['errors'].append({'error': str(e), 'type': 'batch_error'})
            return results


def send_cfn_response(event: Dict, context: Any, status: str, data: Dict = None, physical_resource_id: str = None, reason: str = None):
    """
    Send response to CloudFormation for custom resource.
    
    Args:
        event: CloudFormation event
        context: Lambda context
        status: SUCCESS or FAILED
        data: Optional response data
        physical_resource_id: Optional physical resource ID
        reason: Optional reason for failure
    """
    response_body = {
        'Status': status,
        'Reason': reason or f'See CloudWatch Log Stream: {context.log_stream_name}',
        'PhysicalResourceId': physical_resource_id or context.log_stream_name,
        'StackId': event['StackId'],
        'RequestId': event['RequestId'],
        'LogicalResourceId': event['LogicalResourceId'],
        'Data': data or {}
    }
    
    json_response_body = json.dumps(response_body)
    
    headers = {
        'content-type': '',
        'content-length': str(len(json_response_body))
    }
    
    try:
        response = http.request(
            'PUT',
            event['ResponseURL'],
            body=json_response_body,
            headers=headers
        )
        logger.info(f"CloudFormation response status: {response.status}")
    except Exception as e:
        logger.error(f"Failed to send CloudFormation response: {e}")


def lambda_handler(event: Dict, context: Any) -> Dict:
    """
    Lambda handler for reindexing operations.
    
    Supports two invocation modes:
    1. Direct invocation with parameters
    2. CloudFormation custom resource events
    
    Args:
        event: Lambda event
        context: Lambda context
        
    Returns:
        dict: Results of the reindexing operation
    """
    logger.info(f"Received event: {json.dumps(event, cls=DecimalEncoder)}")
    
    # Check if this is a CloudFormation custom resource event
    is_cfn_event = 'RequestType' in event and 'ResponseURL' in event
    
    try:
        # Validate required environment variables
        if not all([ASSET_STORAGE_TABLE_NAME, S3_ASSET_BUCKETS_STORAGE_TABLE_NAME, METADATA_STORAGE_TABLE_NAME]):
            error_msg = "Missing required environment variables"
            logger.error(error_msg)
            logger.error(f"  ASSET_STORAGE_TABLE_NAME: {ASSET_STORAGE_TABLE_NAME}")
            logger.error(f"  S3_ASSET_BUCKETS_STORAGE_TABLE_NAME: {S3_ASSET_BUCKETS_STORAGE_TABLE_NAME}")
            logger.error(f"  METADATA_STORAGE_TABLE_NAME: {METADATA_STORAGE_TABLE_NAME}")
            if is_cfn_event:
                send_cfn_response(event, context, 'FAILED', reason=error_msg)
                return {'statusCode': 500, 'body': json.dumps({'error': error_msg})}
            else:
                return {'statusCode': 500, 'body': json.dumps({'error': error_msg})}
        
        # Log environment configuration
        logger.info("Environment Configuration:")
        logger.info(f"  ASSET_STORAGE_TABLE_NAME: {ASSET_STORAGE_TABLE_NAME}")
        logger.info(f"  S3_ASSET_BUCKETS_STORAGE_TABLE_NAME: {S3_ASSET_BUCKETS_STORAGE_TABLE_NAME}")
        logger.info(f"  METADATA_STORAGE_TABLE_NAME: {METADATA_STORAGE_TABLE_NAME}")
        logger.info(f"  OPENSEARCH_TYPE: {OPENSEARCH_TYPE}")
        logger.info(f"  OPENSEARCH_ASSET_INDEX_SSM_PARAM: {OPENSEARCH_ASSET_INDEX_SSM_PARAM}")
        logger.info(f"  OPENSEARCH_FILE_INDEX_SSM_PARAM: {OPENSEARCH_FILE_INDEX_SSM_PARAM}")
        logger.info(f"  OPENSEARCH_ENDPOINT_SSM_PARAM: {OPENSEARCH_ENDPOINT_SSM_PARAM}")
        
        # Initialize reindex utility
        utility = ReindexUtility(
            asset_table_name=ASSET_STORAGE_TABLE_NAME,
            s3_buckets_table_name=S3_ASSET_BUCKETS_STORAGE_TABLE_NAME,
            assets_metadata_table_name=METADATA_STORAGE_TABLE_NAME
        )
        
        # Handle CloudFormation custom resource events
        if is_cfn_event:
            request_type = event['RequestType']
            logger.info(f"CloudFormation {request_type} request")
            
            # Only perform reindexing on Create and Update
            if request_type in ['Create', 'Update']:
                try:
                    # Get operation from properties (default to 'both')
                    properties = event.get('ResourceProperties', {})
                    operation = properties.get('Operation', 'both')
                    clear_indexes = properties.get('ClearIndexes', 'false').lower() == 'true'
                    
                    logger.info(f"Starting reindex operation: {operation}, Clear indexes: {clear_indexes}")
                    
                    results = {}
                    
                    # Clear indexes if requested
                    if clear_indexes:
                        try:
                            # Get index names and endpoint from SSM
                            asset_index = ssm_client.get_parameter(Name=OPENSEARCH_ASSET_INDEX_SSM_PARAM)['Parameter']['Value']
                            file_index = ssm_client.get_parameter(Name=OPENSEARCH_FILE_INDEX_SSM_PARAM)['Parameter']['Value']
                            endpoint = ssm_client.get_parameter(Name=OPENSEARCH_ENDPOINT_SSM_PARAM)['Parameter']['Value']
                            
                            logger.info(f"Clearing indexes - Asset: {asset_index}, File: {file_index}, Endpoint: {endpoint}")
                            
                            clear_results = utility.clear_opensearch_indexes(
                                asset_index=asset_index,
                                file_index=file_index,
                                endpoint=endpoint
                            )
                            results['clear_indexes'] = clear_results
                            
                            if not clear_results.get('asset_index', {}).get('success') or \
                               not clear_results.get('file_index', {}).get('success'):
                                logger.warning("Index clearing completed with issues")
                        except Exception as e:
                            logger.error(f"Error clearing indexes: {e}")
                            results['clear_indexes_error'] = str(e)
                    
                    if operation in ['assets', 'both']:
                        logger.info("Reindexing assets...")
                        asset_results = utility.reindex_assets(dry_run=False)
                        results['assets'] = asset_results
                        
                        if asset_results.get('failed_count', 0) > 0:
                            logger.warning(f"Asset reindexing had {asset_results['failed_count']} failures")
                    
                    if operation in ['files', 'both']:
                        logger.info("Reindexing files...")
                        file_results = utility.reindex_files(dry_run=False)
                        results['files'] = file_results
                        
                        if file_results.get('failed_count', 0) > 0:
                            logger.warning(f"File reindexing had {file_results['failed_count']} failures")
                    
                    # Send success response
                    send_cfn_response(
                        event, 
                        context, 
                        'SUCCESS',
                        data={
                            'Message': 'Reindexing completed',
                            'Results': json.dumps(results, cls=DecimalEncoder)
                        }
                    )
                    
                    return {
                        'statusCode': 200,
                        'body': json.dumps({
                            'message': 'Reindexing completed',
                            'results': results
                        }, cls=DecimalEncoder)
                    }
                    
                except Exception as e:
                    error_msg = f"Reindexing failed: {str(e)}"
                    logger.exception(error_msg)
                    send_cfn_response(event, context, 'FAILED', reason=error_msg)
                    return {'statusCode': 500, 'body': json.dumps({'error': error_msg})}
            
            else:  # Delete
                logger.info("Delete request - no action needed")
                send_cfn_response(event, context, 'SUCCESS', data={'Message': 'Delete completed'})
                return {'statusCode': 200, 'body': json.dumps({'message': 'Delete completed'})}
        
        # Handle direct Lambda invocation
        else:
            operation = event.get('operation', 'both')
            dry_run = event.get('dry_run', False)
            limit = event.get('limit')
            clear_indexes = event.get('clear_indexes', False)
            
            logger.info(f"Direct invocation - Operation: {operation}, Dry run: {dry_run}, Limit: {limit}, Clear indexes: {clear_indexes}")
            
            results = {}
            
            # Clear indexes if requested
            if clear_indexes:
                try:
                    # Get index names and endpoint from SSM
                    asset_index = ssm_client.get_parameter(Name=OPENSEARCH_ASSET_INDEX_SSM_PARAM)['Parameter']['Value']
                    file_index = ssm_client.get_parameter(Name=OPENSEARCH_FILE_INDEX_SSM_PARAM)['Parameter']['Value']
                    endpoint = ssm_client.get_parameter(Name=OPENSEARCH_ENDPOINT_SSM_PARAM)['Parameter']['Value']
                    
                    logger.info(f"Clearing indexes - Asset: {asset_index}, File: {file_index}, Endpoint: {endpoint}")
                    
                    clear_results = utility.clear_opensearch_indexes(
                        asset_index=asset_index,
                        file_index=file_index,
                        endpoint=endpoint
                    )
                    results['clear_indexes'] = clear_results
                    
                    if not clear_results.get('asset_index', {}).get('success') or \
                       not clear_results.get('file_index', {}).get('success'):
                        logger.warning("Index clearing completed with issues")
                except Exception as e:
                    logger.error(f"Error clearing indexes: {e}")
                    results['clear_indexes_error'] = str(e)
            
            if operation in ['assets', 'both']:
                logger.info("Reindexing assets...")
                asset_results = utility.reindex_assets(dry_run=dry_run, limit=limit)
                results['assets'] = asset_results
            
            if operation in ['files', 'both']:
                logger.info("Reindexing files...")
                file_results = utility.reindex_files(dry_run=dry_run, limit=limit)
                results['files'] = file_results
            
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': 'Reindexing completed',
                    'results': results
                }, cls=DecimalEncoder)
            }
    
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        logger.exception(error_msg)
        
        if is_cfn_event:
            send_cfn_response(event, context, 'FAILED', reason=error_msg)
        
        return {
            'statusCode': 500,
            'body': json.dumps({'error': error_msg})
        }
