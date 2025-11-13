#!/usr/bin/env python3
# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Data Migration Script for VAMS v2.2 to v2.3 - Lambda Invocation Wrapper

This script simplifies the v2.2 to v2.3 migration by invoking the deployed
reindexer Lambda function via the reindex utility script. All reindexing logic 
runs in the cloud, requiring only lambda:InvokeFunction permission locally.

Key Features:
- Invokes deployed Lambda function for reindexing
- No direct AWS resource access needed locally
- Simplified configuration (only Lambda function name required)
- Supports dry-run and limited testing
- Optional index clearing before migration

Usage:
    # Get Lambda function name from CDK outputs
    aws cloudformation describe-stacks --stack-name your-stack \
      --query 'Stacks[0].Outputs[?OutputKey==`ReindexerFunctionNameOutput`].OutputValue' \
      --output text
    
    # Run migration
    python v2.2_to_v2.3_migration.py --function-name your-reindexer-function
    
    # Dry run
    python v2.2_to_v2.3_migration.py --function-name your-reindexer-function --dry-run
    
    # Clear indexes before reindexing
    python v2.2_to_v2.3_migration.py --function-name your-reindexer-function --clear-indexes

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
from datetime import datetime, timezone

# Add tools directory to path for importing reindex_utility
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'tools'))
from reindex_utility import invoke_reindexer_lambda

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


def load_config_from_file(config_file: str) -> dict:
    """
    Load configuration from a JSON file.
    
    Args:
        config_file: Path to the configuration file
        
    Returns:
        dict: Configuration dictionary
    """
    try:
        with open(config_file, 'r') as f:
            config = json.load(f)
            
        # Remove comment fields
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


def main():
    """Main function to run the migration."""
    parser = argparse.ArgumentParser(
        description='VAMS v2.2 to v2.3 Migration Script - Lambda Invocation Wrapper',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic migration
  python v2.2_to_v2.3_migration.py --function-name vams-prod-reindexer
  
  # Dry run
  python v2.2_to_v2.3_migration.py --function-name vams-prod-reindexer --dry-run
  
  # Test with limited items
  python v2.2_to_v2.3_migration.py --function-name vams-prod-reindexer --limit 100 --dry-run
  
  # Clear indexes before reindexing
  python v2.2_to_v2.3_migration.py --function-name vams-prod-reindexer --clear-indexes
  
  # Use specific AWS profile and region
  python v2.2_to_v2.3_migration.py --function-name vams-prod-reindexer --profile my-profile --region us-west-2

Notes:
  - The Lambda function name can be found in the CDK stack outputs as 'ReindexerFunctionNameOutput'
  - Only requires lambda:InvokeFunction permission locally
  - All reindexing logic runs in the deployed Lambda function
  - This script is a wrapper around the reindex_utility.py script
        """
    )
    
    parser.add_argument('--config',
                        help='Path to configuration JSON file')
    parser.add_argument('--function-name',
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
    parser.add_argument('--log-level', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'], default='INFO',
                        help='Logging level (default: INFO)')
    
    args = parser.parse_args()
    
    # Load configuration from file if provided
    config = {}
    if args.config:
        config = load_config_from_file(args.config)
    
    # Command-line arguments override config file
    function_name = args.function_name or config.get('function_name')
    operation = args.operation if args.operation != 'both' else config.get('operation', 'both')
    dry_run = args.dry_run or config.get('dry_run', False)
    limit = args.limit or config.get('limit')
    clear_indexes = args.clear_indexes or config.get('clear_indexes', False)
    profile = args.profile or config.get('aws_profile')
    region = args.region or config.get('aws_region')
    log_level = args.log_level or config.get('log_level', 'INFO')
    
    # Validate required parameters
    if not function_name:
        logger.error("Error: --function-name is required (or provide via config file)")
        logger.error("Get function name from CDK outputs:")
        logger.error("  aws cloudformation describe-stacks --stack-name your-stack \\")
        logger.error("    --query 'Stacks[0].Outputs[?OutputKey==`ReindexerFunctionNameOutput`].OutputValue' \\")
        logger.error("    --output text")
        return 1
    
    # Configure logging
    logging.getLogger().setLevel(getattr(logging, log_level))
    
    logger.info("=" * 80)
    logger.info("VAMS v2.2 to v2.3 MIGRATION")
    logger.info("=" * 80)
    logger.info("This script invokes the reindexer Lambda function to perform the migration.")
    logger.info("=" * 80)
    
    # Track migration timing
    migration_start_time = datetime.now(timezone.utc)
    
    # Invoke the reindexer Lambda function using the utility script
    result = invoke_reindexer_lambda(
        function_name=function_name,
        operation=operation,
        dry_run=dry_run,
        limit=limit,
        clear_indexes=clear_indexes,
        profile=profile,
        region=region,
        invocation_type='RequestResponse'
    )
    
    migration_end_time = datetime.now(timezone.utc)
    migration_duration = (migration_end_time - migration_start_time).total_seconds()
    
    # Print summary
    logger.info("=" * 80)
    logger.info("MIGRATION SUMMARY")
    logger.info("=" * 80)
    logger.info(f"Migration Duration: {migration_duration:.1f} seconds")
    logger.info(f"Status: {'✅ SUCCESS' if 'error' not in result else '❌ FAILED'}")
    logger.info("=" * 80)
    
    # Return appropriate exit code
    if 'error' in result:
        logger.error("Migration failed")
        return 1
    else:
        logger.info("Migration completed successfully")
        if dry_run:
            logger.info("Note: This was a dry run - no changes were made")
        return 0


if __name__ == "__main__":
    sys.exit(main())
