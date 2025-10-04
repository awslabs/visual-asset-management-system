#!/usr/bin/env python3
# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
VAMS OpenSearch Reindexing Utility

This utility script provides reusable functions for triggering OpenSearch reindexing
by directly updating the AssetsMetadata DynamoDB table. Updates to this table trigger
DynamoDB Streams which automatically reindex assets and files in OpenSearch.

Key Features:
- Directly inserts/updates records in AssetsMetadata table
- Preserves existing metadata attributes
- Sets _asset_table_updated timestamp for tracking
- Supports batch processing for efficiency
- Includes dry-run mode for testing
- Optional index clearing functionality
- Comprehensive error handling and progress tracking

Usage:
    from tools.reindex_utility import ReindexUtility
    
    utility = ReindexUtility(
        session=boto3.Session(profile_name='my-profile'),
        asset_table_name='vams-AssetStorageTable',
        s3_buckets_table_name='vams-S3AssetBucketsTable',
        assets_metadata_table_name='vams-AssetsMetadataTable'
    )
    
    # Optionally clear indexes first
    utility.clear_opensearch_indexes(
        asset_index='vams-assets',
        file_index='vams-files',
        endpoint='https://search-vams.us-east-1.es.amazonaws.com'
    )
    
    # Reindex assets
    asset_results = utility.reindex_assets(dry_run=False)
    
    # Reindex files
    file_results = utility.reindex_files(dry_run=False)

Requirements:
    - Python 3.6+
    - boto3
    - opensearchpy (for index clearing)
    - aws-requests-auth (for OpenSearch authentication)
    - AWS credentials with appropriate permissions
"""

import json
import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from botocore.exceptions import ClientError
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)


class ReindexUtility:
    """
    Utility class for triggering OpenSearch reindexing of VAMS assets and files
    by updating the AssetsMetadata DynamoDB table.
    """
    
    def __init__(
        self,
        session,
        asset_table_name: str,
        s3_buckets_table_name: str,
        assets_metadata_table_name: str,
        asset_batch_size: int = 25,
        file_batch_size: int = 100,
        memory_batch_size: int = 1000,
        max_workers: int = 10
    ):
        """
        Initialize the reindex utility.
        
        Args:
            session: boto3.Session object
            asset_table_name: Name of the DynamoDB asset storage table (source)
            s3_buckets_table_name: Name of the DynamoDB S3 buckets table (source)
            assets_metadata_table_name: Name of the AssetsMetadata table (target)
            asset_batch_size: Number of assets to process in each DynamoDB batch (max 25)
            file_batch_size: Number of files to process in each batch
            memory_batch_size: Number of items to load into memory before batch writing
            max_workers: Maximum number of concurrent workers
        """
        self.session = session
        self.asset_table_name = asset_table_name
        self.s3_buckets_table_name = s3_buckets_table_name
        self.assets_metadata_table_name = assets_metadata_table_name
        self.asset_batch_size = min(asset_batch_size, 25)  # DynamoDB batch limit
        self.file_batch_size = file_batch_size
        self.memory_batch_size = memory_batch_size
        self.max_workers = max_workers
        
        # Initialize AWS clients
        self.dynamodb_client = session.client('dynamodb')
        self.dynamodb_resource = session.resource('dynamodb')
        self.s3_client = session.client('s3')
        
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
            from opensearchpy import OpenSearch, RequestsHttpConnection
            from aws_requests_auth.aws_auth import AWSRequestsAuth
            
            # Create OpenSearch client
            credentials = self.session.get_credentials()
            host = endpoint.replace('https://', '').replace('http://', '')
            
            awsauth = AWSRequestsAuth(
                aws_access_key=credentials.access_key,
                aws_secret_access_key=credentials.secret_key,
                aws_token=credentials.token,
                aws_host=host,
                aws_region=self.session.region_name,
                aws_service='aoss'  # Use 'es' for managed OpenSearch
            )
            
            client = OpenSearch(
                hosts=[{'host': host, 'port': 443}],
                http_auth=awsauth,
                use_ssl=True,
                verify_certs=True,
                connection_class=RequestsHttpConnection
            )
            
            # Clear asset index
            logger.info(f"Clearing asset index: {asset_index}")
            if client.indices.exists(index=asset_index):
                response = client.delete_by_query(
                    index=asset_index,
                    body={"query": {"match_all": {}}}
                )
                results['asset_index']['deleted_count'] = response.get('deleted', 0)
                results['asset_index']['success'] = True
                logger.info(f"Deleted {results['asset_index']['deleted_count']} documents from {asset_index}")
            else:
                logger.warning(f"Asset index {asset_index} does not exist")
            
            # Clear file index
            logger.info(f"Clearing file index: {file_index}")
            if client.indices.exists(index=file_index):
                response = client.delete_by_query(
                    index=file_index,
                    body={"query": {"match_all": {}}}
                )
                results['file_index']['deleted_count'] = response.get('deleted', 0)
                results['file_index']['success'] = True
                logger.info(f"Deleted {results['file_index']['deleted_count']} documents from {file_index}")
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
    
    def reindex_assets(
        self,
        dry_run: bool = False,
        limit: Optional[int] = None
    ) -> Dict:
        """
        Reindex assets by inserting/updating records in AssetsMetadata table.
        
        This function:
        1. Scans the asset storage table for all assets
        2. For each asset, fetches existing metadata record (if exists)
        3. Preserves all existing attributes
        4. Updates/inserts with _asset_table_updated timestamp
        5. Batch writes to AssetsMetadata table
        
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
            'start_time': datetime.utcnow().isoformat(),
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
                results['end_time'] = datetime.utcnow().isoformat()
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
            
            results['end_time'] = datetime.utcnow().isoformat()
            
            logger.info("=" * 80)
            logger.info("ASSET REINDEXING COMPLETE")
            logger.info(f"  Total: {results['total_count']}")
            logger.info(f"  Success: {results['success_count']}")
            logger.info(f"  Failed: {results['failed_count']}")
            logger.info("=" * 80)
            
            return results
            
        except Exception as e:
            logger.exception(f"Error during asset reindexing: {e}")
            results['end_time'] = datetime.utcnow().isoformat()
            results['errors'].append({'error': str(e), 'type': 'fatal'})
            return results
    
    def reindex_files(
        self,
        dry_run: bool = False,
        limit: Optional[int] = None
    ) -> Dict:
        """
        Reindex files by inserting/updating records in AssetsMetadata table.
        
        This function:
        1. Scans S3 buckets table for bucket configurations
        2. Lists all S3 objects with asset metadata
        3. For each file, fetches existing metadata record (if exists)
        4. Formats assetId as /{assetId}/{relative/path/to/file}
        5. Preserves all existing attributes
        6. Updates/inserts with _asset_table_updated timestamp
        7. Batch writes to AssetsMetadata table
        
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
            'start_time': datetime.utcnow().isoformat(),
            'end_time': None
        }
        
        try:
            # Get all S3 bucket configurations
            logger.info(f"Scanning S3 buckets table: {self.s3_buckets_table_name}")
            bucket_configs = self._scan_s3_buckets_table()
            
            logger.info(f"Found {len(bucket_configs)} bucket configurations")
            
            if len(bucket_configs) == 0:
                logger.warning("No bucket configurations found")
                results['end_time'] = datetime.utcnow().isoformat()
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
            
            results['end_time'] = datetime.utcnow().isoformat()
            
            logger.info("=" * 80)
            logger.info("S3 FILE REINDEXING COMPLETE")
            logger.info(f"  Buckets processed: {results['buckets_processed']}")
            logger.info(f"  Objects scanned: {results['objects_scanned']}")
            logger.info(f"  Files processed: {results['total_count']}")
            logger.info(f"  Success: {results['success_count']}")
            logger.info(f"  Failed: {results['failed_count']}")
            logger.info("=" * 80)
            
            return results
            
        except Exception as e:
            logger.exception(f"Error during S3 file reindexing: {e}")
            results['end_time'] = datetime.utcnow().isoformat()
            results['errors'].append({'error': str(e), 'type': 'fatal'})
            return results
    
    def _scan_asset_table(self, limit: Optional[int] = None) -> List[Dict]:
        """
        Scan the asset storage table and return all asset records.
        
        Args:
            limit: Optional limit on number of records to return
            
        Returns:
            list: List of asset records
        """
        table = self.dynamodb_resource.Table(self.asset_table_name)
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
        """
        Scan the S3 buckets table and return all bucket configurations.
        
        Returns:
            list: List of bucket configuration records
        """
        table = self.dynamodb_resource.Table(self.s3_buckets_table_name)
        
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
        """
        Batch get existing metadata records from AssetsMetadata table.
        
        Args:
            keys: List of (databaseId, assetId) tuples
            
        Returns:
            dict: Dictionary mapping "databaseId#assetId" to record
        """
        if not keys:
            return {}
        
        records = {}
        
        try:
            # Process in batches of 100 (DynamoDB BatchGetItem limit)
            batch_size = 100
            for i in range(0, len(keys), batch_size):
                batch_keys = keys[i:i + batch_size]
                
                # Build request items
                request_items = {
                    self.assets_metadata_table_name: {
                        'Keys': [
                            {'databaseId': db_id, 'assetId': asset_id}
                            for db_id, asset_id in batch_keys
                        ]
                    }
                }
                
                # Batch get with retry for unprocessed keys
                response = self.dynamodb_client.batch_get_item(RequestItems=request_items)
                
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
                    response = self.dynamodb_client.batch_get_item(RequestItems=unprocessed)
                    
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
        """
        Update a batch of assets in the AssetsMetadata table.
        
        Args:
            assets: List of asset records to update
            timestamp: ISO format timestamp to set
            
        Returns:
            dict: Results with success, failed counts and errors
        """
        table = self.dynamodb_resource.Table(self.assets_metadata_table_name)
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
        """
        Process all objects in a bucket and update AssetsMetadata table.
        
        Args:
            bucket_name: Name of the S3 bucket
            base_prefix: Base prefix for assets in the bucket
            dry_run: If True, don't update records
            limit: Optional limit on number of files to process
            
        Returns:
            dict: Results with success, failed, total counts and errors
        """
        results = {
            'success': 0,
            'failed': 0,
            'total': 0,
            'objects_scanned': 0,
            'errors': []
        }
        
        try:
            # Collect files in memory batches
            files_batch = []
            current_timestamp = datetime.now(timezone.utc).isoformat()
            
            # List all objects in the bucket
            paginator = self.s3_client.get_paginator('list_objects_v2')
            
            for page in paginator.paginate(Bucket=bucket_name, Prefix=base_prefix):
                objects = page.get('Contents', [])
                results['objects_scanned'] += len(objects)
                
                for obj in objects:
                    # Skip folder markers
                    if obj['Key'].endswith('/'):
                        continue
                    
                    # Stop if we've reached the limit
                    if limit and results['total'] >= limit:
                        break
                    
                    try:
                        # Get object metadata
                        head_response = self.s3_client.head_object(
                            Bucket=bucket_name,
                            Key=obj['Key']
                        )
                        
                        metadata = head_response.get('Metadata', {})
                        asset_id = metadata.get('assetid')
                        database_id = metadata.get('databaseid')
                        
                        # Only process files with asset metadata
                        if not asset_id or not database_id:
                            logger.debug(f"Skipping file without asset metadata: {obj['Key']}")
                            continue
                        
                        # Calculate relative path from base prefix
                        relative_path = obj['Key']
                        if base_prefix and obj['Key'].startswith(base_prefix):
                            relative_path = obj['Key'][len(base_prefix):].lstrip('/')
                        
                        # Format assetId as /{assetId}/{relative/path}
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
                                logger.debug(f"DRY RUN: Would update {len(files_batch)} files")
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
                    logger.debug(f"DRY RUN: Would update {len(files_batch)} files")
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
        """
        Update a batch of files in the AssetsMetadata table.
        
        Args:
            files: List of file records to update
            timestamp: ISO format timestamp to set
            
        Returns:
            dict: Results with success, failed counts and errors
        """
        table = self.dynamodb_resource.Table(self.assets_metadata_table_name)
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


def main():
    """
    Main function for standalone execution.
    """
    import argparse
    import sys
    
    parser = argparse.ArgumentParser(
        description='VAMS OpenSearch Reindexing Utility',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Reindex assets only
  python reindex_utility.py --profile my-profile \\
    --asset-table vams-Assets \\
    --assets-metadata-table vams-AssetsMetadata \\
    --operation assets
  
  # Reindex files only
  python reindex_utility.py --profile my-profile \\
    --s3-buckets-table vams-S3Buckets \\
    --assets-metadata-table vams-AssetsMetadata \\
    --operation files
  
  # Reindex both (assets first, then files)
  python reindex_utility.py --profile my-profile \\
    --asset-table vams-Assets \\
    --s3-buckets-table vams-S3Buckets \\
    --assets-metadata-table vams-AssetsMetadata \\
    --operation both
  
  # Clear indexes first, then reindex
  python reindex_utility.py --profile my-profile \\
    --asset-table vams-Assets \\
    --s3-buckets-table vams-S3Buckets \\
    --assets-metadata-table vams-AssetsMetadata \\
    --clear-indexes \\
    --asset-index-name vams-assets \\
    --file-index-name vams-files \\
    --opensearch-endpoint https://search-vams.us-east-1.es.amazonaws.com
  
  # Dry run
  python reindex_utility.py --profile my-profile \\
    --asset-table vams-Assets \\
    --assets-metadata-table vams-AssetsMetadata \\
    --dry-run
        """
    )
    
    parser.add_argument('--profile', help='AWS profile name')
    parser.add_argument('--region', help='AWS region')
    parser.add_argument('--limit', type=int, help='Maximum number of items to process (for testing)')
    parser.add_argument('--asset-table', help='Name of the asset storage table')
    parser.add_argument('--s3-buckets-table', help='Name of the S3 buckets table')
    parser.add_argument('--assets-metadata-table', required=True, help='Name of the AssetsMetadata table')
    parser.add_argument('--operation', choices=['assets', 'files', 'both'], default='both',
                        help='Operation to perform (default: both)')
    parser.add_argument('--clear-indexes', action='store_true', help='Clear OpenSearch indexes before reindexing')
    parser.add_argument('--asset-index-name', help='Asset index name (required if clearing indexes)')
    parser.add_argument('--file-index-name', help='File index name (required if clearing indexes)')
    parser.add_argument('--opensearch-endpoint', help='OpenSearch endpoint URL (required if clearing indexes)')
    parser.add_argument('--dry-run', action='store_true', help='Perform dry run without making changes')
    parser.add_argument('--log-level', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'], default='INFO',
                        help='Logging level (default: INFO)')
    
    args = parser.parse_args()
    
    # Configure logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler(sys.stdout)]
    )
    
    # Validate required arguments
    if args.operation in ['assets', 'both'] and not args.asset_table:
        parser.error("--asset-table is required for assets operation")
    
    if args.operation in ['files', 'both'] and not args.s3_buckets_table:
        parser.error("--s3-buckets-table is required for files operation")
    
    if args.clear_indexes:
        if not args.asset_index_name or not args.file_index_name or not args.opensearch_endpoint:
            parser.error("--asset-index-name, --file-index-name, and --opensearch-endpoint are required when using --clear-indexes")
    
    # Create boto3 session
    import boto3
    session_kwargs = {}
    if args.profile:
        session_kwargs['profile_name'] = args.profile
    if args.region:
        session_kwargs['region_name'] = args.region
    
    session = boto3.Session(**session_kwargs)
    
    # Create utility instance
    utility = ReindexUtility(
        session=session,
        asset_table_name=args.asset_table or '',
        s3_buckets_table_name=args.s3_buckets_table or '',
        assets_metadata_table_name=args.assets_metadata_table
    )
    
    # Execute operations
    try:
        # Clear indexes if requested
        if args.clear_indexes:
            logger.info("Clearing OpenSearch indexes...")
            clear_results = utility.clear_opensearch_indexes(
                asset_index=args.asset_index_name,
                file_index=args.file_index_name,
                endpoint=args.opensearch_endpoint
            )
            
            if not clear_results.get('asset_index', {}).get('success') or \
               not clear_results.get('file_index', {}).get('success'):
                logger.warning("Index clearing completed with issues")
        
        if args.operation in ['assets', 'both']:
            logger.info("Starting asset reindexing...")
            asset_results = utility.reindex_assets(dry_run=args.dry_run, limit=args.limit)
            
            if asset_results['failed_count'] > 0:
                logger.warning(f"Asset reindexing completed with {asset_results['failed_count']} failures")
            else:
                logger.info("Asset reindexing completed successfully")
        
        if args.operation in ['files', 'both']:
            logger.info("Starting file reindexing...")
            file_results = utility.reindex_files(dry_run=args.dry_run, limit=args.limit)
            
            if file_results['failed_count'] > 0:
                logger.warning(f"File reindexing completed with {file_results['failed_count']} failures")
            else:
                logger.info("File reindexing completed successfully")
        
        logger.info("Reindexing utility completed")
        return 0
        
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
