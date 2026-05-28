#!/usr/bin/env python3
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Data Migration Script for VAMS v2.5 to v2.6 - OpenSearch Reindex (v2 -> v3)

The v2.6 release introduces:
  1. A new ``geo_MD_location`` field of OpenSearch type ``geo_shape`` on every
     asset and file document. The asset and file indexers populate it from a
     ``location`` metadata key (GeoJSON or {latitude, longitude, altitude})
     or from individual latitude / longitude / altitude metadata fields.
  2. New OpenSearch index names ``vams-assets-v3`` and ``vams-files-v3`` (the
     prior v2 indexes are abandoned). The CDK custom resource that creates the
     index mappings only runs on first creation, so the schema change is
     introduced cleanly by switching index names.
  3. (Provisioned deployments only) An OpenSearch engine upgrade from 2.7 to
     3.5, which itself requires a reindex.

Because the v3 indexes are empty after the v2.6 CDK deploy, this migration
delegates to the existing reindexer Lambda (``crReindexer``) to re-populate
both indexes from the source DynamoDB and S3 records. ``--clear-indexes`` is
**off by default** -- the v3 indexes start empty and do not need to be
cleared. Pass ``--clear-indexes`` only if you are re-running the migration
against an already-populated v3 index and want a clean slate.

Usage:
    # Dry run (recommended first step)
    python v2.5_to_v2.6_migration.py --config v2.5_to_v2.6_migration_config.json --dry-run

    # Production migration
    python v2.5_to_v2.6_migration.py --config v2.5_to_v2.6_migration_config.json

    # Re-run with index clear (only if v3 is already populated)
    python v2.5_to_v2.6_migration.py --config v2.5_to_v2.6_migration_config.json --clear-indexes

    # Test with limited items
    python v2.5_to_v2.6_migration.py --config v2.5_to_v2.6_migration_config.json --limit 100 --dry-run

    # Asynchronous invocation for very large datasets
    python v2.5_to_v2.6_migration.py --config v2.5_to_v2.6_migration_config.json --async

Requirements:
    - Python 3.6+
    - boto3
    - AWS credentials with lambda:InvokeFunction permission
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

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


def load_config_from_file(config_file: str) -> dict:
    """Load configuration from a JSON file, stripping comment fields."""
    try:
        with open(config_file, 'r') as f:
            config = json.load(f)
        config = {k: v for k, v in config.items() if not k.startswith('_comment') and k != 'comments'}
        logger.info(f"Loaded configuration from {config_file}")
        return config
    except FileNotFoundError:
        logger.error(f"Configuration file not found: {config_file}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in configuration file: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Error loading configuration from {config_file}: {e}")
        sys.exit(1)


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
    Invoke the deployed crReindexer Lambda to repopulate the v3 OpenSearch indexes.
    """
    logger.info("=" * 80)
    logger.info("VAMS v2.5 -> v2.6 OPENSEARCH REINDEX (vams-*-v2 -> vams-*-v3)")
    logger.info("=" * 80)
    logger.info(f"Function: {function_name}")
    logger.info(f"Operation: {operation}")
    logger.info(f"Dry Run: {dry_run}")
    logger.info(f"Limit: {limit}")
    logger.info(f"Clear Indexes: {clear_indexes}")
    logger.info(f"Invocation Type: {invocation_type}")
    logger.info("=" * 80)

    try:
        session_kwargs = {}
        if profile:
            session_kwargs['profile_name'] = profile
        if region:
            session_kwargs['region_name'] = region

        session = boto3.Session(**session_kwargs)
        lambda_client = session.client('lambda')

        payload = {
            'operation': operation,
            'dry_run': dry_run,
            'clear_indexes': clear_indexes,
        }
        if limit is not None:
            payload['limit'] = limit

        payload_json = json.dumps(payload)
        logger.info(f"Payload: {payload_json}")

        logger.info(f"Invoking Lambda function: {function_name}")
        start_time = time.time()

        response = lambda_client.invoke(
            FunctionName=function_name,
            InvocationType=invocation_type,
            Payload=payload_json
        )

        elapsed_time = time.time() - start_time

        if invocation_type == 'RequestResponse':
            status_code = response['StatusCode']
            if status_code != 200:
                error_msg = f"Lambda invocation failed with status code: {status_code}"
                logger.error(error_msg)
                if 'FunctionError' in response:
                    error_payload = json.loads(response['Payload'].read())
                    logger.error(f"Error: {json.dumps(error_payload, indent=2)}")
                return {'statusCode': status_code, 'error': error_msg}

            response_payload = json.loads(response['Payload'].read())

            logger.info("=" * 80)
            logger.info("LAMBDA INVOCATION SUCCESSFUL")
            logger.info(f"Execution Time: {elapsed_time:.2f} seconds")
            logger.info("=" * 80)

            if 'body' in response_payload:
                body = json.loads(response_payload['body'])
                if 'results' in body:
                    _log_results(body['results'])
                else:
                    logger.info(f"Response: {json.dumps(body, indent=2)}")
            else:
                logger.info(f"Response: {json.dumps(response_payload, indent=2)}")

            return response_payload

        # Asynchronous invocation
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
                'function_name': function_name,
            }

        error_msg = f"Lambda invocation failed with status code: {status_code}"
        logger.error(error_msg)
        return {'statusCode': status_code, 'error': error_msg}

    except ReadTimeoutError as e:
        logger.warning("=" * 80)
        logger.warning("LAMBDA INVOCATION TIMED OUT")
        logger.warning("=" * 80)
        logger.warning(f"The Lambda function '{function_name}' invocation timed out.")
        logger.warning("The function continues to run in the background. Monitor CloudWatch Logs to verify completion.")
        logger.warning("=" * 80)
        return {
            'timeout': True,
            'warning': str(e),
            'function_name': function_name,
            'message': 'Lambda invocation timed out but function is still processing',
        }

    except ClientError as e:
        error_code = e.response['Error']['Code']
        error_message = e.response['Error']['Message']
        logger.error(f"AWS Error ({error_code}): {error_message}")
        if error_code == 'ResourceNotFoundException':
            logger.error(f"Lambda function '{function_name}' not found. Verify the function name from the CDK output 'ReindexerFunctionNameOutput'.")
        elif error_code == 'AccessDeniedException':
            logger.error("Access denied. Ensure your IAM principal has lambda:InvokeFunction permission for the reindexer function.")
        return {'error': error_message, 'error_code': error_code}

    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return {'error': str(e)}


def _log_results(results: Dict) -> None:
    """Pretty-print the reindex results returned by the Lambda."""
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
        a = results['assets']
        logger.info("Asset Reindex Results:")
        logger.info(f"  Total: {a.get('total_count', 0)}")
        logger.info(f"  Success: {a.get('success_count', 0)}")
        logger.info(f"  Failed: {a.get('failed_count', 0)}")
        if a.get('errors'):
            logger.warning(f"  Errors: {len(a['errors'])} errors occurred")

    if 'files' in results:
        f = results['files']
        logger.info("File Reindex Results:")
        logger.info(f"  Buckets Processed: {f.get('buckets_processed', 0)}")
        logger.info(f"  Objects Scanned: {f.get('objects_scanned', 0)}")
        logger.info(f"  Total: {f.get('total_count', 0)}")
        logger.info(f"  Success: {f.get('success_count', 0)}")
        logger.info(f"  Failed: {f.get('failed_count', 0)}")
        if f.get('errors'):
            logger.warning(f"  Errors: {len(f['errors'])} errors occurred")


def main():
    parser = argparse.ArgumentParser(
        description='VAMS v2.5 to v2.6 OpenSearch reindex migration (vams-*-v2 -> vams-*-v3).',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Reindex both assets and files synchronously (recommended for small/medium deployments)
  python v2.5_to_v2.6_migration.py --config v2.5_to_v2.6_migration_config.json

  # Dry run with a small subset
  python v2.5_to_v2.6_migration.py --config v2.5_to_v2.6_migration_config.json --dry-run --limit 100

  # Re-run after a partial failure (clears v3 first)
  python v2.5_to_v2.6_migration.py --config v2.5_to_v2.6_migration_config.json --clear-indexes

  # Asynchronous invocation for very large datasets
  python v2.5_to_v2.6_migration.py --config v2.5_to_v2.6_migration_config.json --async

Notes:
  - The reindexer Lambda function name is exposed by the CDK stack output 'ReindexerFunctionNameOutput'.
  - Synchronous invocation (default) waits for completion and prints a summary.
  - For deployments with hundreds of thousands or millions of objects, use --async and monitor CloudWatch Logs.
  - --clear-indexes defaults to FALSE because the v3 indexes are empty after the v2.6 deploy. Use it only when re-running.
        """
    )

    parser.add_argument('--config', required=True,
                        help='Path to the migration JSON configuration file')
    parser.add_argument('--operation', choices=['assets', 'files', 'both'],
                        help='Operation to perform (default: both, can also be set in config)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Test without making changes (also configurable in JSON)')
    parser.add_argument('--limit', type=int,
                        help='Maximum number of items per category to reindex (testing)')
    parser.add_argument('--clear-indexes', action='store_true',
                        help='Clear existing v3 OpenSearch documents before reindex (default: false). '
                             'The v3 indexes start empty after the CDK deploy, so this is only needed when re-running.')
    parser.add_argument('--profile',
                        help='AWS profile name')
    parser.add_argument('--region',
                        help='AWS region')
    parser.add_argument('--async', dest='async_invoke', action='store_true',
                        help='Use asynchronous invocation (recommended for large datasets)')
    parser.add_argument('--log-level', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'], default='INFO',
                        help='Logging level (default: INFO)')

    args = parser.parse_args()

    logging.getLogger().setLevel(getattr(logging, args.log_level))

    config = load_config_from_file(args.config)

    function_name = config.get('reindexer_function_name')
    if not function_name:
        logger.error("Configuration is missing required field 'reindexer_function_name'.")
        sys.exit(1)

    operation = args.operation or config.get('operation', 'both')
    dry_run = args.dry_run or bool(config.get('dry_run', False))
    # CLI flag wins; otherwise fall back to config (default false)
    clear_indexes = args.clear_indexes or bool(config.get('clear_indexes', False))
    limit = args.limit if args.limit is not None else config.get('limit')
    profile = args.profile or config.get('aws_profile')
    region = args.region or config.get('aws_region')

    invocation_type = 'Event' if args.async_invoke else 'RequestResponse'

    result = invoke_reindexer_lambda(
        function_name=function_name,
        operation=operation,
        dry_run=dry_run,
        limit=limit,
        clear_indexes=clear_indexes,
        profile=profile,
        region=region,
        invocation_type=invocation_type,
    )

    if result.get('timeout'):
        logger.warning("Reindex invocation timed out -- Lambda continues processing in the background.")
        logger.warning("Verify completion via CloudWatch Logs.")
        return 0
    if 'error' in result:
        logger.error("Reindex migration failed.")
        return 1

    logger.info("Reindex migration completed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
