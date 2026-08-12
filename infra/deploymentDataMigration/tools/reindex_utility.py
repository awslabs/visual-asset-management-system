#!/usr/bin/env python3
# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
VAMS Indexer and OpenSearch Reindexing Utility

This utility script triggers the global indexing and OpenSearch reindexing for assets and files.
It supports two run modes:

- lambda (default): Invokes the deployed reindexer Lambda function. All reindexing logic runs in
  the cloud, so no direct AWS resource access is required locally. The Lambda is bound by the
  15-minute maximum execution time, which can be insufficient for very large asset repositories.

- direct: Imports the backend reindexer handler code and runs it locally in this Python process,
  with no execution-time limit. Use this for very large repositories where the Lambda would
  otherwise time out (and leave some records unindexed). The local process still calls AWS
  (DynamoDB, SSM, and OpenSearch) using your local AWS credentials, so it requires the same
  permissions the Lambda role has, plus the backend handler's Python dependencies installed
  locally. See "Direct mode" in the README for the required libraries and inputs.

Key Features:
- Lambda invocation mode (synchronous or asynchronous) for typical datasets
- Direct local-run mode for very large datasets that exceed the Lambda 15-minute limit
- Progress monitoring and comprehensive error handling and result reporting

Usage (lambda mode):
    python reindex_utility.py --function-name vams-prod-reindexer --operation both

    python reindex_utility.py --function-name vams-prod-reindexer --operation assets --dry-run

Usage (direct mode — runs the backend handler locally, no 15-minute limit):
    # --backend-path defaults to the backend source resolved relative to this script; pass it only
    # to override a non-standard checkout layout.
    python reindex_utility.py --mode direct --operation both \
        --asset-storage-table-name vams-prod-assetStorage \
        --s3-asset-buckets-storage-table-name vams-prod-s3AssetBuckets \
        --asset-file-metadata-storage-table-name vams-prod-assetFileMetadata \
        --opensearch-asset-index-ssm-param /vams-prod/aos/assetIndexName \
        --opensearch-file-index-ssm-param /vams-prod/aos/fileIndexName \
        --opensearch-endpoint-ssm-param /vams-prod/aos/endPoint \
        --opensearch-type provisioned \
        --region us-east-1

Requirements:
    - Python 3.6+
    - lambda mode:  boto3; AWS credentials with lambda:InvokeFunction permission
    - direct mode:  boto3, botocore, urllib3, aws-lambda-powertools (and opensearch-py only when
                    using --clear-indexes); AWS credentials with the same DynamoDB / SSM /
                    OpenSearch permissions the reindexer Lambda role has; the VAMS backend source
                    available locally.
"""

import argparse
import json
import logging
import os
import sys
import time
from typing import Dict, Optional

import boto3
from botocore.exceptions import ClientError, ReadTimeoutError

logger = logging.getLogger(__name__)

# Default location of the VAMS backend source (the 'backend/backend' directory containing the
# 'handlers' and 'common' packages), computed relative to this script so it works regardless of the
# current working directory. This script lives at infra/deploymentDataMigration/tools/, so the
# backend source is three directories up. Used as the default for --backend-path in direct mode;
# pass --backend-path to override if the developer's checkout layout differs.
DEFAULT_BACKEND_PATH = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "backend", "backend")
)


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


def _log_direct_results(response_payload: Dict) -> None:
    """Pretty-print the reindexer handler results (shared shape with the Lambda response)."""
    if 'body' not in response_payload:
        logger.info(f"Response: {json.dumps(response_payload, indent=2)}")
        return

    body = json.loads(response_payload['body'])
    if 'results' not in body:
        logger.info(f"Response: {json.dumps(body, indent=2)}")
        return

    results = body['results']

    if 'clear_indexes' in results:
        clear_results = results['clear_indexes']
        logger.info("Index Clearing Results:")
        if 'asset_index' in clear_results:
            logger.info(f"  Asset Index: {clear_results['asset_index'].get('deleted_count', 0)} documents deleted")
        if 'file_index' in clear_results:
            logger.info(f"  File Index: {clear_results['file_index'].get('deleted_count', 0)} documents deleted")
        if 'error' in clear_results:
            logger.error(f"  Error: {clear_results['error']}")

    if 'assets' in results:
        asset_results = results['assets']
        logger.info("Asset Reindexing Results:")
        logger.info(f"  Total: {asset_results.get('total_count', 0)}")
        logger.info(f"  Success: {asset_results.get('success_count', 0)}")
        logger.info(f"  Failed: {asset_results.get('failed_count', 0)}")
        if asset_results.get('errors'):
            logger.warning(f"  Errors: {len(asset_results['errors'])} errors occurred")

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


def run_reindexer_direct(
    backend_path: str,
    env_vars: Dict[str, str],
    operation: str = "both",
    dry_run: bool = False,
    limit: Optional[int] = None,
    clear_indexes: bool = False,
    profile: Optional[str] = None,
    region: Optional[str] = None,
) -> Dict:
    """
    Run the backend reindexer handler locally, in this Python process, with no execution-time limit.

    This imports the deployed Lambda's handler module (backend/backend/handlers/indexing/crReindexer.py)
    directly and calls its lambda_handler() with a direct-invocation event. It is intended for very
    large asset repositories where the deployed Lambda would exceed its 15-minute maximum runtime and
    leave records unindexed. The handler still reads all of its configuration from environment
    variables and uses the local AWS credentials, so every variable the Lambda would receive must be
    supplied here.

    Args:
        backend_path: Filesystem path to the VAMS backend source root that contains the `handlers`
            and `common` packages (i.e. the `backend/backend` directory). Added to sys.path so the
            handler's `from common...` and `handlers...` imports resolve.
        env_vars: The environment variables the handler requires. Set into os.environ before import.
        operation: 'assets', 'files', or 'both'.
        dry_run: If True, scan but do not write touch/delete records.
        limit: Optional cap on items processed (for testing).
        clear_indexes: If True, clear the OpenSearch indexes before reindexing (requires opensearch-py).
        profile: AWS profile name to activate for the local AWS SDK calls.
        region: AWS region for the local AWS SDK calls.

    Returns:
        dict: The handler response payload ({'statusCode', 'body'}), or an {'error': ...} dict.
    """
    logger.info("=" * 80)
    logger.info("VAMS OPENSEARCH REINDEXER - DIRECT LOCAL RUN (no execution-time limit)")
    logger.info("=" * 80)
    logger.info(f"Backend path: {backend_path}")
    logger.info(f"Operation: {operation}")
    logger.info(f"Dry Run: {dry_run}")
    logger.info(f"Limit: {limit}")
    logger.info(f"Clear Indexes: {clear_indexes}")
    logger.info("=" * 80)

    # The boto3 clients in the handler module are created at import time from the default session, so
    # the AWS profile/region must be established in the environment before the module is imported.
    if profile:
        os.environ['AWS_PROFILE'] = profile
    if region:
        # Set both so the default session and the handler's AWS_REGION read agree.
        os.environ['AWS_REGION'] = region
        os.environ.setdefault('AWS_DEFAULT_REGION', region)

    # Provide all of the handler's required environment variables (the handler reads these at module
    # load via os.environ.get). Any not supplied default to empty inside the handler, which it
    # validates and rejects, so we set them all explicitly here.
    for key, value in env_vars.items():
        if value is not None:
            os.environ[key] = value

    # Make the backend packages importable.
    resolved_backend_path = os.path.abspath(backend_path)
    if not os.path.isdir(resolved_backend_path):
        error_msg = (
            f"Backend path not found: {resolved_backend_path}. Point --backend-path at the VAMS "
            f"'backend/backend' directory that contains the 'handlers' and 'common' packages."
        )
        logger.error(error_msg)
        return {'error': error_msg}
    if resolved_backend_path not in sys.path:
        sys.path.insert(0, resolved_backend_path)

    try:
        # Imported lazily (after sys.path / env are set) so the handler's module-level client and
        # environment-variable initialization happens with the correct configuration.
        from handlers.indexing import crReindexer  # type: ignore
    except ImportError as e:
        error_msg = (
            f"Failed to import the backend reindexer handler from {resolved_backend_path}: {e}. "
            f"Ensure --backend-path is correct and the direct-mode Python libraries are installed "
            f"(boto3, botocore, urllib3, and opensearch-py when using --clear-indexes)."
        )
        logger.exception(error_msg)
        return {'error': error_msg}

    # Build the same event shape the handler expects for a direct (non-CloudFormation) invocation.
    event = {
        'operation': operation,
        'dry_run': dry_run,
        'clear_indexes': clear_indexes,
    }
    if limit is not None:
        event['limit'] = limit

    start_time = time.time()
    try:
        # context is unused on the direct-invocation path of the handler.
        response_payload = crReindexer.lambda_handler(event, None)
    except Exception as e:  # noqa: BLE001 - surface any handler error to the caller
        logger.exception(f"Direct reindexer run failed: {e}")
        return {'error': str(e)}

    elapsed_time = time.time() - start_time

    status_code = response_payload.get('statusCode') if isinstance(response_payload, dict) else None
    if status_code == 200:
        logger.info("=" * 80)
        logger.info("DIRECT REINDEXER RUN SUCCESSFUL")
        logger.info(f"Execution Time: {elapsed_time:.2f} seconds")
        logger.info("=" * 80)
        _log_direct_results(response_payload)
        logger.info("=" * 80)
        return response_payload

    # Non-200: surface the handler's error body.
    logger.error(f"Direct reindexer run failed with status code: {status_code}")
    try:
        body = json.loads(response_payload.get('body', '{}'))
        logger.error(f"Error: {json.dumps(body, indent=2)}")
        return {'statusCode': status_code, 'error': body.get('error', 'Unknown error')}
    except Exception:
        return {'statusCode': status_code, 'error': 'Reindexer run failed'}


def main():
    """
    Main function for standalone execution.
    """
    parser = argparse.ArgumentParser(
        description='VAMS OpenSearch Reindexing Utility (lambda invocation or direct local run)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples (lambda mode — default):
  # Reindex both assets and files (synchronous)
  python reindex_utility.py --function-name vams-prod-reindexer --operation both

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

Examples (direct mode — runs the backend handler locally, no 15-minute limit):
  # --backend-path defaults to the backend source resolved relative to this script; override only
  # for a non-standard checkout layout.
  python reindex_utility.py --mode direct --operation both \\
      --asset-storage-table-name vams-prod-assetStorage \\
      --s3-asset-buckets-storage-table-name vams-prod-s3AssetBuckets \\
      --asset-file-metadata-storage-table-name vams-prod-assetFileMetadata \\
      --opensearch-asset-index-ssm-param /vams-prod/aos/assetIndexName \\
      --opensearch-file-index-ssm-param /vams-prod/aos/fileIndexName \\
      --opensearch-endpoint-ssm-param /vams-prod/aos/endPoint \\
      --opensearch-type provisioned \\
      --region us-east-1

Notes:
  - lambda mode (default): invokes the deployed reindexer Lambda. The Lambda function name can be
    found in the CDK stack outputs as 'ReindexerFunctionNameOutput'. Bound by the 15-minute Lambda
    maximum; for large datasets use --async and monitor CloudWatch Logs.
  - direct mode: imports and runs the backend reindexer handler locally with no execution-time
    limit. Use for very large repositories where the Lambda would time out. Requires the backend
    source (--backend-path), all of the handler's environment-variable inputs (table names and SSM
    parameter names below), and the handler's Python libraries installed locally (boto3, botocore,
    urllib3, and opensearch-py when using --clear-indexes). Uses your local AWS credentials, which
    must have the same DynamoDB / SSM / OpenSearch permissions the reindexer Lambda role has.
  - The required table names and SSM parameter names are available in the CDK stack outputs / SSM;
    they match the environment variables configured on the reindexer Lambda.
  - Dry run mode allows testing without making actual changes (both modes).
        """
    )

    parser.add_argument('--mode', choices=['lambda', 'direct'], default='lambda',
                        help="Run mode: 'lambda' (invoke the deployed Lambda, default) or 'direct' "
                             "(run the backend handler locally with no execution-time limit)")

    # Common options (both modes)
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
                        help='AWS region (direct mode also uses this as the handler AWS_REGION)')
    parser.add_argument('--log-level', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'], default='INFO',
                        help='Logging level (default: INFO)')

    # Lambda-mode options
    lambda_group = parser.add_argument_group('lambda mode')
    lambda_group.add_argument('--function-name',
                              help='Name of the deployed reindexer Lambda function (from CDK output: '
                                   'ReindexerFunctionNameOutput). Required for --mode lambda.')
    lambda_group.add_argument('--async', dest='async_invoke', action='store_true',
                              help='Use asynchronous invocation (for large datasets)')

    # Direct-mode options (mirror the reindexer Lambda's environment variables)
    direct_group = parser.add_argument_group('direct mode')
    direct_group.add_argument('--backend-path', default=DEFAULT_BACKEND_PATH,
                              help="Path to the VAMS backend source root containing the 'handlers' and "
                                   "'common' packages (the 'backend/backend' directory). Defaults to the "
                                   "backend source resolved relative to this script (%(default)s); "
                                   "override only if your checkout layout differs.")
    direct_group.add_argument('--asset-storage-table-name',
                              help='DynamoDB asset storage table name (ASSET_STORAGE_TABLE_NAME).')
    direct_group.add_argument('--s3-asset-buckets-storage-table-name',
                              help='DynamoDB S3 asset buckets storage table name (S3_ASSET_BUCKETS_STORAGE_TABLE_NAME).')
    direct_group.add_argument('--asset-file-metadata-storage-table-name',
                              help='DynamoDB asset/file metadata storage table name (ASSET_FILE_METADATA_STORAGE_TABLE_NAME).')
    direct_group.add_argument('--opensearch-asset-index-ssm-param',
                              help='SSM parameter name for the asset index name (OPENSEARCH_ASSET_INDEX_SSM_PARAM).')
    direct_group.add_argument('--opensearch-file-index-ssm-param',
                              help='SSM parameter name for the file index name (OPENSEARCH_FILE_INDEX_SSM_PARAM).')
    direct_group.add_argument('--opensearch-endpoint-ssm-param',
                              help='SSM parameter name for the OpenSearch endpoint (OPENSEARCH_ENDPOINT_SSM_PARAM).')
    direct_group.add_argument('--opensearch-type', choices=['serverless', 'provisioned'], default='provisioned',
                              help='OpenSearch deployment type (OPENSEARCH_TYPE). Default: provisioned.')

    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler(sys.stdout)]
    )

    if args.mode == 'direct':
        # Validate the inputs the backend handler requires. --backend-path defaults to the backend
        # source resolved relative to this script, so it is not listed here; override it only if the
        # checkout layout differs.
        required_direct = {
            '--asset-storage-table-name': args.asset_storage_table_name,
            '--s3-asset-buckets-storage-table-name': args.s3_asset_buckets_storage_table_name,
            '--asset-file-metadata-storage-table-name': args.asset_file_metadata_storage_table_name,
            '--opensearch-asset-index-ssm-param': args.opensearch_asset_index_ssm_param,
            '--opensearch-file-index-ssm-param': args.opensearch_file_index_ssm_param,
            '--opensearch-endpoint-ssm-param': args.opensearch_endpoint_ssm_param,
        }
        missing = [name for name, value in required_direct.items() if not value]
        if missing:
            parser.error(
                "Direct mode requires the following arguments: " + ", ".join(missing)
            )

        # Clearing indexes connects to the OpenSearch endpoint directly. A provisioned domain is
        # always inside the VPC, so its endpoint is not reachable from a local machine — block it.
        # The touch-and-delete reindex itself only uses DynamoDB/SSM and works fine locally; only the
        # clear step needs the endpoint. To clear a provisioned domain's indexes, use lambda mode
        # (which runs in the VPC), then run direct mode for the bulk reindex. See the README.
        if args.clear_indexes and args.opensearch_type == 'provisioned':
            parser.error(
                "Direct mode cannot clear indexes for a provisioned OpenSearch domain: the domain "
                "endpoint is inside the VPC and is not reachable from a local machine. Clear the "
                "indexes in lambda mode instead (it runs in the VPC), then run direct mode without "
                "--clear-indexes for the bulk reindex. For example:\n"
                "  python reindex_utility.py --function-name <reindexer-fn> --operation both --clear-indexes --limit 1\n"
                "  python reindex_utility.py --mode direct --operation both ...   (no --clear-indexes)"
            )

        # Serverless collections are VPC-restricted when the collection is private
        # (openSearch.useServerless.allowPublic = false), which routes access through a VPC endpoint.
        # The script cannot detect that from these inputs, so warn rather than block: clearing works
        # for a public collection but will fail (hang/timeout) for a private one — use lambda mode in
        # that case.
        if args.clear_indexes and args.opensearch_type == 'serverless':
            logger.warning(
                "Clearing indexes in direct mode connects to the OpenSearch Serverless collection "
                "endpoint directly. If the collection is private (deployed with "
                "openSearch.useServerless.allowPublic = false), it is reachable only through its VPC "
                "endpoint and not from a local machine — clear the indexes in lambda mode instead, "
                "then run direct mode without --clear-indexes."
            )

        # The reindexer handler reads its configuration from these environment variables.
        env_vars = {
            'ASSET_STORAGE_TABLE_NAME': args.asset_storage_table_name,
            'S3_ASSET_BUCKETS_STORAGE_TABLE_NAME': args.s3_asset_buckets_storage_table_name,
            'ASSET_FILE_METADATA_STORAGE_TABLE_NAME': args.asset_file_metadata_storage_table_name,
            'OPENSEARCH_ASSET_INDEX_SSM_PARAM': args.opensearch_asset_index_ssm_param,
            'OPENSEARCH_FILE_INDEX_SSM_PARAM': args.opensearch_file_index_ssm_param,
            'OPENSEARCH_ENDPOINT_SSM_PARAM': args.opensearch_endpoint_ssm_param,
            'OPENSEARCH_TYPE': args.opensearch_type,
        }

        result = run_reindexer_direct(
            backend_path=args.backend_path,
            env_vars=env_vars,
            operation=args.operation,
            dry_run=args.dry_run,
            limit=args.limit,
            clear_indexes=args.clear_indexes,
            profile=args.profile,
            region=args.region,
        )

        if 'error' in result:
            logger.error("Reindexing failed")
            return 1
        logger.info("Reindexing completed successfully")
        return 0

    # Lambda mode (default)
    if not args.function_name:
        parser.error("--function-name is required for --mode lambda")

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