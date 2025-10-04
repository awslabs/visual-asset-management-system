#!/usr/bin/env python3
# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
VAMS OpenSearch Reindexing Utility

This utility script provides reusable functions for triggering OpenSearch reindexing
by updating DynamoDB asset records and publishing S3 notification events to SNS.

Key Features:
- Updates _os_reindex_date timestamp for all assets in DynamoDB
- Generates S3 ObjectCreated notification events for all files
- Publishes events to SNS topic for automatic reindexing
- Supports batch processing for efficiency
- Includes dry-run mode for testing
- Comprehensive error handling and progress tracking

Usage:
    from tools.reindex_utility import ReindexUtility
    
    utility = ReindexUtility(
        session=boto3.Session(profile_name='my-profile'),
        asset_table_name='vams-AssetStorageTable',
        s3_buckets_table_name='vams-S3AssetBucketsTable',
        sns_topic_arn='arn:aws:sns:region:account:vams-s3ObjectCreated'
    )
    
    # Reindex assets first
    asset_results = utility.reindex_assets(dry_run=False)
    
    # Then reindex S3 files
    file_results = utility.reindex_s3_files(dry_run=False)

Requirements:
    - Python 3.6+
    - boto3
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
    Utility class for triggering OpenSearch reindexing of VAMS assets and files.
    """
    
    def __init__(
        self,
        session,
        asset_table_name: str,
        s3_buckets_table_name: str,
        sns_topic_arn: str,
        asset_batch_size: int = 25,
        s3_batch_size: int = 100,
        max_workers: int = 10,
        sns_publish_delay: float = 0.05
    ):
        """
        Initialize the reindex utility.
        
        Args:
            session: boto3.Session object
            asset_table_name: Name of the DynamoDB asset storage table
            s3_buckets_table_name: Name of the DynamoDB S3 buckets table
            sns_topic_arn: ARN of the SNS topic for S3 object created events
            asset_batch_size: Number of assets to process in each DynamoDB batch (max 25)
            s3_batch_size: Number of S3 objects to process in each batch
            max_workers: Maximum number of concurrent workers
            sns_publish_delay: Delay between SNS publishes to avoid throttling (seconds)
        """
        self.session = session
        self.asset_table_name = asset_table_name
        self.s3_buckets_table_name = s3_buckets_table_name
        self.sns_topic_arn = sns_topic_arn
        self.asset_batch_size = min(asset_batch_size, 25)  # DynamoDB batch limit
        self.s3_batch_size = s3_batch_size
        self.max_workers = max_workers
        self.sns_publish_delay = sns_publish_delay
        
        # Initialize AWS clients
        self.dynamodb_client = session.client('dynamodb')
        self.dynamodb_resource = session.resource('dynamodb')
        self.s3_client = session.client('s3')
        self.sns_client = session.client('sns')
        
        logger.info(f"ReindexUtility initialized:")
        logger.info(f"  Asset table: {asset_table_name}")
        logger.info(f"  S3 buckets table: {s3_buckets_table_name}")
        logger.info(f"  SNS topic: {sns_topic_arn}")
    
    def reindex_assets(
        self,
        dry_run: bool = False,
        limit: Optional[int] = None
    ) -> Dict:
        """
        Update _os_reindex_date timestamp for all assets to trigger reindexing.
        
        This function scans the asset storage table and updates the _os_reindex_date
        field with the current timestamp, which triggers the asset indexer to
        reindex the asset in OpenSearch.
        
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
            
            # Update assets in batches
            current_timestamp = datetime.now(timezone.utc).isoformat()
            
            for i in range(0, len(assets), self.asset_batch_size):
                batch = assets[i:i + self.asset_batch_size]
                batch_num = (i // self.asset_batch_size) + 1
                total_batches = (len(assets) + self.asset_batch_size - 1) // self.asset_batch_size
                
                logger.info(f"Processing asset batch {batch_num}/{total_batches} ({len(batch)} assets)")
                
                if dry_run:
                    results['success_count'] += len(batch)
                    logger.info(f"DRY RUN: Would update {len(batch)} assets")
                else:
                    batch_results = self._update_asset_batch(batch, current_timestamp)
                    results['success_count'] += batch_results['success']
                    results['failed_count'] += batch_results['failed']
                    results['errors'].extend(batch_results['errors'])
                
                # Log progress
                if (i + self.asset_batch_size) % 1000 == 0:
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
    
    def reindex_s3_files(
        self,
        dry_run: bool = False,
        limit: Optional[int] = None
    ) -> Dict:
        """
        Generate S3 ObjectCreated notification events for all files and publish to SNS.
        
        This function:
        1. Scans the S3 buckets table for bucket/prefix configurations
        2. Lists all objects in each bucket/prefix combination
        3. Constructs S3 ObjectCreated notification events
        4. Publishes events to SNS topic for automatic file reindexing
        
        Args:
            dry_run: If True, don't actually publish to SNS
            limit: Optional limit on number of files to process (for testing)
            
        Returns:
            dict: Results with success_count, failed_count, total_count, errors
        """
        logger.info("=" * 80)
        logger.info("S3 FILE REINDEXING")
        logger.info("=" * 80)
        
        if dry_run:
            logger.info("DRY RUN MODE - No SNS messages will be published")
        
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
    
    def _update_asset_batch(
        self,
        assets: List[Dict],
        timestamp: str
    ) -> Dict:
        """
        Update a batch of assets with the reindex timestamp.
        
        Args:
            assets: List of asset records to update
            timestamp: ISO format timestamp to set
            
        Returns:
            dict: Results with success, failed counts and errors
        """
        table = self.dynamodb_resource.Table(self.asset_table_name)
        results = {'success': 0, 'failed': 0, 'errors': []}
        
        try:
            # Use batch_writer for efficient updates
            with table.batch_writer() as batch:
                for asset in assets:
                    try:
                        # Update the asset with reindex timestamp
                        asset['_os_reindex_date'] = timestamp
                        batch.put_item(Item=asset)
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
        Process all objects in a bucket and publish S3 notification events.
        
        Args:
            bucket_name: Name of the S3 bucket
            base_prefix: Base prefix for assets in the bucket
            dry_run: If True, don't publish to SNS
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
                    
                    results['total'] += 1
                    
                    # Get object metadata
                    try:
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
                            results['total'] -= 1  # Don't count files without metadata
                            continue
                        
                        # Create and publish S3 notification event
                        if dry_run:
                            results['success'] += 1
                            logger.debug(f"DRY RUN: Would publish event for {obj['Key']}")
                        else:
                            event = self._create_s3_notification_event(
                                bucket_name=bucket_name,
                                object_key=obj['Key'],
                                object_size=obj.get('Size', 0),
                                etag=obj.get('ETag', '').strip('"'),
                                event_time=obj.get('LastModified', datetime.now(timezone.utc)).isoformat()
                            )
                            
                            if self._publish_to_sns(event):
                                results['success'] += 1
                            else:
                                results['failed'] += 1
                                results['errors'].append({
                                    'key': obj['Key'],
                                    'error': 'Failed to publish to SNS'
                                })
                            
                            # Add delay to avoid throttling
                            time.sleep(self.sns_publish_delay)
                        
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
            
            return results
            
        except Exception as e:
            logger.error(f"Error processing bucket {bucket_name}: {e}")
            results['errors'].append({
                'bucket': bucket_name,
                'error': str(e),
                'type': 'bucket_error'
            })
            return results
    
    def _create_s3_notification_event(
        self,
        bucket_name: str,
        object_key: str,
        object_size: int,
        etag: str,
        event_time: str
    ) -> Dict:
        """
        Create an S3 ObjectCreated notification event.
        
        This creates an event that matches the AWS S3 event notification format,
        which the file indexer Lambda function expects.
        
        Args:
            bucket_name: Name of the S3 bucket
            object_key: S3 object key
            object_size: Size of the object in bytes
            etag: ETag of the object
            event_time: ISO format timestamp of the event
            
        Returns:
            dict: S3 notification event
        """
        # Get AWS region from session
        region = self.session.region_name or 'us-east-1'
        
        # Create event matching AWS S3 notification format
        event = {
            "Records": [
                {
                    "eventVersion": "2.1",
                    "eventSource": "aws:s3",
                    "awsRegion": region,
                    "eventTime": event_time,
                    "eventName": "ObjectCreated:Put",
                    "userIdentity": {
                        "principalId": "VAMS-REINDEX-UTILITY"
                    },
                    "requestParameters": {
                        "sourceIPAddress": "127.0.0.1"
                    },
                    "responseElements": {
                        "x-amz-request-id": "REINDEX",
                        "x-amz-id-2": "REINDEX"
                    },
                    "s3": {
                        "s3SchemaVersion": "1.0",
                        "configurationId": "vams-reindex",
                        "bucket": {
                            "name": bucket_name,
                            "ownerIdentity": {
                                "principalId": "VAMS"
                            },
                            "arn": f"arn:aws:s3:::{bucket_name}"
                        },
                        "object": {
                            "key": object_key,
                            "size": object_size,
                            "eTag": etag,
                            "sequencer": "REINDEX"
                        }
                    }
                }
            ]
        }
        
        return event
    
    def _publish_to_sns(self, event: Dict) -> bool:
        """
        Publish an S3 notification event to SNS.
        
        Args:
            event: S3 notification event to publish
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            response = self.sns_client.publish(
                TopicArn=self.sns_topic_arn,
                Message=json.dumps(event),
                Subject='S3 Notification - Reindex'
            )
            
            return response.get('MessageId') is not None
            
        except Exception as e:
            logger.error(f"Error publishing to SNS: {e}")
            return False


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
  python reindex_utility.py --profile my-profile --asset-table vams-Assets --operation assets
  
  # Reindex S3 files only
  python reindex_utility.py --profile my-profile --s3-buckets-table vams-S3Buckets \\
    --sns-topic arn:aws:sns:us-east-1:123456789012:vams-s3ObjectCreated --operation files
  
  # Reindex both (assets first, then files)
  python reindex_utility.py --profile my-profile --asset-table vams-Assets \\
    --s3-buckets-table vams-S3Buckets \\
    --sns-topic arn:aws:sns:us-east-1:123456789012:vams-s3ObjectCreated --operation both
  
  # Dry run
  python reindex_utility.py --profile my-profile --asset-table vams-Assets --dry-run
        """
    )
    
    parser.add_argument('--profile', help='AWS profile name')
    parser.add_argument('--region', help='AWS region')
    parser.add_argument('--asset-table', help='Asset storage table name')
    parser.add_argument('--s3-buckets-table', help='S3 buckets table name')
    parser.add_argument('--sns-topic', help='SNS topic ARN for S3 notifications')
    parser.add_argument('--operation', choices=['assets', 'files', 'both'], default='both',
                        help='Operation to perform (default: both)')
    parser.add_argument('--dry-run', action='store_true', help='Perform dry run without making changes')
    parser.add_argument('--limit', type=int, help='Limit number of items to process (for testing)')
    parser.add_argument('--asset-batch-size', type=int, default=25, help='Asset batch size (default: 25)')
    parser.add_argument('--s3-batch-size', type=int, default=100, help='S3 batch size (default: 100)')
    parser.add_argument('--max-workers', type=int, default=10, help='Max concurrent workers (default: 10)')
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
    
    if args.operation in ['files', 'both']:
        if not args.s3_buckets_table:
            parser.error("--s3-buckets-table is required for files operation")
        if not args.sns_topic:
            parser.error("--sns-topic is required for files operation")
    
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
        sns_topic_arn=args.sns_topic or '',
        asset_batch_size=args.asset_batch_size,
        s3_batch_size=args.s3_batch_size,
        max_workers=args.max_workers
    )
    
    # Execute operations
    try:
        if args.operation in ['assets', 'both']:
            logger.info("Starting asset reindexing...")
            asset_results = utility.reindex_assets(dry_run=args.dry_run, limit=args.limit)
            
            if asset_results['failed_count'] > 0:
                logger.warning(f"Asset reindexing completed with {asset_results['failed_count']} failures")
            else:
                logger.info("Asset reindexing completed successfully")
        
        if args.operation in ['files', 'both']:
            logger.info("Starting S3 file reindexing...")
            file_results = utility.reindex_s3_files(dry_run=args.dry_run, limit=args.limit)
            
            if file_results['failed_count'] > 0:
                logger.warning(f"S3 file reindexing completed with {file_results['failed_count']} failures")
            else:
                logger.info("S3 file reindexing completed successfully")
        
        logger.info("Reindexing utility completed")
        return 0
        
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
