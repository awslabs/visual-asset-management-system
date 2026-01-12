#!/usr/bin/env python3
# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
VAMS Indexer and OpenSearch Reindexing Utility - Lambda Invocation Wrapper

This utility script provides a simple wrapper for triggering the global indexing and OpenSearch reindexing
by invoking the deployed Lambda function. All reindexing logic now runs in the cloud
via the deployed Lambda function, eliminating the need for local execution with
direct AWS resource access.

Key Features:
- Invokes deployed Lambda function for reindexing
- Supports both synchronous and asynchronous invocation
- Provides progress monitoring for long-running operations
- Comprehensive error handling and result reporting
- No direct AWS resource access required locally

Usage:
    python reindex_utility.py --function-name vams-prod-reindexer --operation both
    
    python reindex_utility.py --function-name vams-prod-reindexer --operation assets --dry-run
    
    python reindex_utility.py --function-name vams-prod-reindexer --operation files --limit 1000

Requirements:
    - Python 3.6+
    - boto3
    - AWS credentials with lambda:InvokeFunction permission
"""

import argparse
import json
import logging
import sys
import time
from typing import Dict, Optional

import boto3
from botocore.exceptions import ClientError, ReadTimeoutError

logger = logging.getLogger(__name__)


def invoke_reindexer_lambda(
    function_name: str,
    operation: str = "both",
    dry_run: bool = False,
    limit: Optional[int] = None,
    clear_indexes: bool = False,
    profile: Optional[str] = None,
    region: Optional[str] = None,
    invocation_type: str = "RequestResponse"
) -> Dict:
    """
    Invoke the deployed reindexer Lambda function.
    
    Args:
        function_name: Name of the Lambda function to invoke
        operation: Operation to perform ('assets', 'files', or 'both')
        dry_run: If True, perform dry run without making changes
        limit: Optional limit on number of items to process
        clear_indexes: If True, clear OpenSearch indexes before reindexing
        profile: AWS profile name
        region: AWS region
        invocation_type: 'RequestResponse' for synchronous, 'Event' for asynchronous
        
    Returns:
        dict: Results from the Lambda invocation
    """
    logger.info("=" * 80)
    logger.info("VAMS OPENSEARCH REINDEXER - LAMBDA INVOCATION")
    logger.info("=" * 80)
    logger.info(f"Function: {function_name}")
    logger.info(f"Operation: {operation}")
    logger.info(f"Dry Run: {dry_run}")
    logger.info(f"Limit: {limit}")
    logger.info(f"Clear Indexes: {clear_indexes}")
    logger.info(f"Invocation Type: {invocation_type}")
    logger.info("=" * 80)
    
    try:
        # Create boto3 session
        session_kwargs = {}
        if profile:
            session_kwargs['profile_name'] = profile
        if region:
            session_kwargs['region_name'] = region
        
        session = boto3.Session(**session_kwargs)
        lambda_client = session.client('lambda')
        
        # Prepare payload
        payload = {
            'operation': operation,
            'dry_run': dry_run,
            'clear_indexes': clear_indexes
        }
        
        if limit is not None:
            payload['limit'] = limit
        
        payload_json = json.dumps(payload)
        logger.info(f"Payload: {payload_json}")
        
        # Invoke Lambda function
        logger.info(f"Invoking Lambda function: {function_name}")
        start_time = time.time()
        
        response = lambda_client.invoke(
            FunctionName=function_name,
            InvocationType=invocation_type,
            Payload=payload_json
        )
        
        elapsed_time = time.time() - start_time
        
        # Handle response based on invocation type
        if invocation_type == 'RequestResponse':
            # Synchronous invocation - get results
            status_code = response['StatusCode']
            
            if status_code == 200:
                # Read response payload
                response_payload = json.loads(response['Payload'].read())
                
                logger.info("=" * 80)
                logger.info("LAMBDA INVOCATION SUCCESSFUL")
                logger.info(f"Execution Time: {elapsed_time:.2f} seconds")
                logger.info("=" * 80)
                
                # Parse and display results
                if 'body' in response_payload:
                    body = json.loads(response_payload['body'])
                    
                    if 'results' in body:
                        results = body['results']
                        
                        # Display index clearing results
                        if 'clear_indexes' in results:
                            clear_results = results['clear_indexes']
                            logger.info("Index Clearing Results:")
                            if 'asset_index' in clear_results:
                                logger.info(f"  Asset Index: {clear_results['asset_index'].get('deleted_count', 0)} documents deleted")
                            if 'file_index' in clear_results:
                                logger.info(f"  File Index: {clear_results['file_index'].get('deleted_count', 0)} documents deleted")
                            if 'error' in clear_results:
                                logger.error(f"  Error: {clear_results['error']}")
                        
                        # Display asset results
                        if 'assets' in results:
                            asset_results = results['assets']
                            logger.info("Asset Reindexing Results:")
                            logger.info(f"  Total: {asset_results.get('total_count', 0)}")
                            logger.info(f"  Success: {asset_results.get('success_count', 0)}")
                            logger.info(f"  Failed: {asset_results.get('failed_count', 0)}")
                            
                            if asset_results.get('errors'):
                                logger.warning(f"  Errors: {len(asset_results['errors'])} errors occurred")
                        
                        # Display file results
                        if 'files' in results:
                            file_results = results['files']
                            logger.info("File Reindexing Results:")
                            logger.info(f"  Buckets Processed: {file_results.get('buckets_processed', 0)}")
                            logger.info(f"  Objects Scanned: {file_results.get('objects_scanned', 0)}")
                            logger.info(f"  Total: {file_results.get('total_count', 0)}")
                            logger.info(f"  Success: {file_results.get('success_count', 0)}")
                            logger.info(f"  Failed: {file_results.get('failed_count', 0)}")
                            
                            if file_results.get('errors'):
                                logger.warning(f"  Errors: {len(file_results['errors'])} errors occurred")
                        
                        logger.info("=" * 80)
                        return response_payload
                    else:
                        logger.info(f"Response: {json.dumps(body, indent=2)}")
                        return response_payload
                else:
                    logger.info(f"Response: {json.dumps(response_payload, indent=2)}")
                    return response_payload
            else:
                error_msg = f"Lambda invocation failed with status code: {status_code}"
                logger.error(error_msg)
                
                if 'FunctionError' in response:
                    error_payload = json.loads(response['Payload'].read())
                    logger.error(f"Error: {json.dumps(error_payload, indent=2)}")
                
                return {
                    'statusCode': status_code,
                    'error': error_msg
                }
        
        else:
            # Asynchronous invocation - just confirm submission
            status_code = response['StatusCode']
            
            if status_code == 202:
                logger.info("=" * 80)
                logger.info("LAMBDA INVOCATION SUBMITTED (ASYNCHRONOUS)")
                logger.info(f"Function: {function_name}")
                logger.info("Check CloudWatch Logs for execution results")
                logger.info("=" * 80)
                
                return {
                    'statusCode': 202,
                    'message': 'Reindexing job submitted asynchronously',
                    'function_name': function_name
                }
            else:
                error_msg = f"Lambda invocation failed with status code: {status_code}"
                logger.error(error_msg)
                return {
                    'statusCode': status_code,
                    'error': error_msg
                }
    
    except ReadTimeoutError as e:
        logger.warning("=" * 80)
        logger.warning("LAMBDA INVOCATION TIMED OUT")
        logger.warning("=" * 80)
        logger.warning(f"The Lambda function '{function_name}' invocation timed out after waiting for a response.")
        logger.warning("However, the Lambda function is still processing in the background and will continue until completion.")
        logger.warning(f"To monitor progress and verify completion:")
        logger.warning(f"  1. Check CloudWatch Logs for function: {function_name}")
        logger.warning(f"  2. Look for log streams with recent timestamps")
        logger.warning(f"  3. Verify the reindexing completed successfully in the logs")
        logger.warning("=" * 80)
        
        return {
            'timeout': True,
            'warning': str(e),
            'function_name': function_name,
            'message': 'Lambda invocation timed out but function is still processing'
        }
    
    except ClientError as e:
        error_code = e.response['Error']['Code']
        error_message = e.response['Error']['Message']
        logger.error(f"AWS Error ({error_code}): {error_message}")
        
        if error_code == 'ResourceNotFoundException':
            logger.error(f"Lambda function '{function_name}' not found. Please verify the function name.")
        elif error_code == 'AccessDeniedException':
            logger.error("Access denied. Please ensure you have lambda:InvokeFunction permission.")
        
        return {
            'error': error_message,
            'error_code': error_code
        }
    
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return {
            'error': str(e)
        }


def main():
    """
    Main function for standalone execution.
    """
    parser = argparse.ArgumentParser(
        description='VAMS OpenSearch Reindexing Utility - Lambda Invocation Wrapper',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Reindex both assets and files (synchronous)
  python reindex_utility.py --function-name vams-prod-reindexer --operation both
  
  # Reindex assets only (synchronous)
  python reindex_utility.py --function-name vams-prod-reindexer --operation assets
  
  # Reindex files only (synchronous)
  python reindex_utility.py --function-name vams-prod-reindexer --operation files
  
  # Dry run to test without making changes
  python reindex_utility.py --function-name vams-prod-reindexer --operation both --dry-run
  
  # Limit processing for testing
  python reindex_utility.py --function-name vams-prod-reindexer --operation assets --limit 100
  
  # Clear indexes before reindexing
  python reindex_utility.py --function-name vams-prod-reindexer --operation both --clear-indexes
  
  # Asynchronous invocation (for large datasets)
  python reindex_utility.py --function-name vams-prod-reindexer --operation both --async
  
  # Use specific AWS profile and region
  python reindex_utility.py --function-name vams-prod-reindexer --operation both --profile my-profile --region us-west-2

Notes:
  - The Lambda function name can be found in the CDK stack outputs as 'ReindexerFunctionNameOutput'
  - Synchronous invocation (default) waits for completion and shows results
  - Asynchronous invocation (--async) submits the job and returns immediately
  - For large datasets, use asynchronous invocation and monitor CloudWatch Logs
  - Dry run mode allows testing without making actual changes
        """
    )
    
    parser.add_argument('--function-name', required=True, 
                        help='Name of the deployed reindexer Lambda function (from CDK output: ReindexerFunctionNameOutput)')
    parser.add_argument('--operation', choices=['assets', 'files', 'both'], default='both',
                        help='Operation to perform (default: both)')
    parser.add_argument('--dry-run', action='store_true', 
                        help='Perform dry run without making changes')
    parser.add_argument('--limit', type=int, 
                        help='Maximum number of items to process (for testing)')
    parser.add_argument('--clear-indexes', action='store_true',
                        help='Clear OpenSearch indexes before reindexing (removes all documents from indexes)')
    parser.add_argument('--profile', 
                        help='AWS profile name')
    parser.add_argument('--region', 
                        help='AWS region')
    parser.add_argument('--async', dest='async_invoke', action='store_true',
                        help='Use asynchronous invocation (for large datasets)')
    parser.add_argument('--log-level', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'], default='INFO',
                        help='Logging level (default: INFO)')
    
    args = parser.parse_args()
    
    # Configure logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler(sys.stdout)]
    )
    
    # Determine invocation type
    invocation_type = 'Event' if args.async_invoke else 'RequestResponse'
    
    # Invoke Lambda function
    result = invoke_reindexer_lambda(
        function_name=args.function_name,
        operation=args.operation,
        dry_run=args.dry_run,
        limit=args.limit,
        clear_indexes=args.clear_indexes,
        profile=args.profile,
        region=args.region,
        invocation_type=invocation_type
    )
    
    # Return appropriate exit code
    # Treat timeout as a warning, not a failure (Lambda continues processing in background)
    if 'timeout' in result and result.get('timeout'):
        logger.warning("Reindexing invocation timed out - Lambda function continues processing in background")
        logger.warning("Check CloudWatch Logs to verify completion")
        return 0  # Exit successfully since the Lambda is still processing
    elif 'error' in result:
        logger.error("Reindexing failed")
        return 1
    else:
        logger.info("Reindexing completed successfully")
        return 0


if __name__ == "__main__":
    sys.exit(main())