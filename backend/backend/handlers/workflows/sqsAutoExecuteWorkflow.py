"""
SQS Auto-Execute Workflow Handler for VAMS.
Automatically triggers workflow executions based on file indexing events.

Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
SPDX-License-Identifier: Apache-2.0
"""

import os
import boto3
import json
from typing import Dict, List, Optional, Any
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from botocore.config import Config
from aws_lambda_powertools.utilities.typing import LambdaContext
from customLogging.logger import safeLogger
from models.common import APIGatewayProxyResponseV2, internal_error, success

# Configure AWS clients with retry configuration
retry_config = Config(
    retries={
        'max_attempts': 5,
        'mode': 'adaptive'
    }
)

dynamodb = boto3.resource('dynamodb', config=retry_config)
lambda_client = boto3.client('lambda', config=retry_config)
logger = safeLogger(service_name="SqsAutoExecuteWorkflow")

# Excluded patterns or prefixes from file paths to exclude
excluded_prefixes = ['pipeline', 'pipelines', 'preview', 'previews', 'temp-upload', 'temp-uploads']
excluded_patterns = ['.previewFile.']

# Load environment variables with error handling
try:
    workflow_storage_table_name = os.environ["WORKFLOW_STORAGE_TABLE_NAME"]
    asset_storage_table_name = os.environ["ASSET_STORAGE_TABLE_NAME"]
    database_storage_table_name = os.environ["DATABASE_STORAGE_TABLE_NAME"]
    s3_asset_buckets_table_name = os.environ["S3_ASSET_BUCKETS_STORAGE_TABLE_NAME"]
    execute_workflow_lambda_name = os.environ["EXECUTE_WORKFLOW_LAMBDA_FUNCTION_NAME"]
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

# Initialize DynamoDB tables
workflow_storage_table = dynamodb.Table(workflow_storage_table_name)
asset_storage_table = dynamodb.Table(asset_storage_table_name)
database_storage_table = dynamodb.Table(database_storage_table_name)
s3_asset_buckets_table = dynamodb.Table(s3_asset_buckets_table_name)


def extract_file_extension(file_path: str) -> Optional[str]:
    """Extract file extension from file path"""
    if '.' in file_path and not file_path.endswith('/'):
        return file_path.split('.')[-1].lower()
    return None


def is_folder_path(file_path: str) -> bool:
    """Check if path represents a folder"""
    return file_path.endswith('/') or '.' not in os.path.basename(file_path)


def should_skip_file(s3_key: str, file_path: str) -> bool:
    """Check if file should be skipped based on excluded patterns"""
    # Skip folder markers
    if file_path.endswith('/'):
        return True
    
    # Check if s3_key contains any excluded patterns
    if any(pattern in s3_key for pattern in excluded_patterns):
        logger.info(f"Skipping file with excluded pattern: {s3_key}")
        return True
    
    # Check if s3_key starts with any excluded prefixes
    path_parts = s3_key.split('/')
    for part in path_parts:
        if any(part.startswith(prefix) for prefix in excluded_prefixes):
            logger.info(f"Skipping file with excluded prefix: {s3_key}")
            return True
    
    return False


def get_workflows_for_database(database_id: str) -> List[Dict[str, Any]]:
    """Get all workflows for a specific database"""
    try:
        response = workflow_storage_table.query(
            KeyConditionExpression=Key('databaseId').eq(database_id)
        )
        return response.get('Items', [])
    except Exception as e:
        logger.exception(f"Error getting workflows for database {database_id}: {e}")
        return []


def validate_and_parse_extensions(auto_trigger_extensions: Optional[str]) -> Optional[List[str]]:
    """
    Validate and parse comma-delimited extension list
    
    Args:
        auto_trigger_extensions: Comma-delimited string of extensions
    
    Returns:
        List of normalized extensions, or None if invalid/empty
    """
    # Skip if field is None, empty, or whitespace
    if not auto_trigger_extensions or not auto_trigger_extensions.strip():
        return None
    
    # Check for "all" trigger - return special marker
    trigger_value = auto_trigger_extensions.strip().lower()
    if trigger_value in ['.all', 'all']:
        return ['__ALL__']  # Special marker for "all files"
    
    # Parse comma-delimited extensions
    extensions = []
    for ext in auto_trigger_extensions.split(','):
        # Strip whitespace
        ext = ext.strip()
        
        # Skip empty strings
        if not ext:
            continue
        
        # Remove leading dots and convert to lowercase
        ext = ext.lstrip('.').lower()
        
        # Skip if empty after processing
        if not ext:
            continue
        
        # Validate extension format (alphanumeric and common chars only)
        if not all(c.isalnum() or c in ['-', '_'] for c in ext):
            logger.warning(f"Invalid extension format: {ext}, skipping")
            continue
        
        extensions.append(ext)
    
    # Return None if no valid extensions found
    return extensions if extensions else None


def should_trigger_workflow(auto_trigger_extensions: Optional[str], file_extension: Optional[str]) -> bool:
    """
    Determine if workflow should be triggered based on autoTriggerOnFileExtensionsUpload field
    
    Args:
        auto_trigger_extensions: Comma-delimited string of extensions or ".all"/"all"
        file_extension: The file extension to check
    
    Returns:
        True if workflow should be triggered, False otherwise
    """
    # Validate and parse extensions
    parsed_extensions = validate_and_parse_extensions(auto_trigger_extensions)
    
    # If no valid extensions, don't trigger
    if not parsed_extensions:
        return False
    
    # Check for "all" trigger
    if '__ALL__' in parsed_extensions:
        return True
    
    # If no file extension, don't trigger
    if not file_extension:
        return False
    
    # Check if file extension matches any of the trigger extensions
    return file_extension in parsed_extensions


def invoke_execute_workflow(workflow: Dict[str, Any], database_id: str, asset_id: str, file_path: str) -> bool:
    """
    Invoke the executeWorkflow Lambda function
    
    Args:
        workflow: The workflow object
        database_id: Database ID (of the asset)
        asset_id: Asset ID
        file_path: File path that triggered the workflow
    
    Returns:
        True if invocation succeeded, False otherwise
    """
    try:
        workflow_id = workflow.get('workflowId')
        workflow_database_id = workflow.get('databaseId', database_id)  # Workflow's database (may be GLOBAL)
        
        # Build the event structure for executeWorkflow
        event = {
            'requestContext': {
                'http': {
                    'method': 'POST',
                    'path': f'/database/{database_id}/assets/{asset_id}/workflows/{workflow_id}'
                }
            },
            'lambdaCrossCall': {
                'userName': 'SYSTEM_USER'
            },
            'pathParameters': {
                'databaseId': database_id,
                'assetId': asset_id,
                'workflowId': workflow_id
            },
            'body': json.dumps({
                'workflowDatabaseId': workflow_database_id,  # Required field
                'fileKey': file_path,  # Use fileKey instead of assetFileKey
                'triggerSource': 'auto-trigger-sqs'
            })
        }
        
        # Invoke the executeWorkflow Lambda synchronously to check response
        logger.info(f"Invoking executeWorkflow for workflow {workflow_id} (database: {workflow_database_id}) on file {file_path}")
        response = lambda_client.invoke(
            FunctionName=execute_workflow_lambda_name,
            InvocationType='RequestResponse',  # Synchronous to check response
            Payload=json.dumps(event)
        )
        
        # Parse the response
        status_code = response.get('StatusCode', 0)
        if status_code == 200:
            # Parse the payload to check the actual API response
            payload = response.get('Payload')
            if payload:
                payload_str = payload.read().decode('utf-8')
                payload_data = json.loads(payload_str)
                
                # Check the API response status code
                api_status_code = payload_data.get('statusCode', 500)
                if api_status_code == 200:
                    logger.info(f"Successfully invoked workflow {workflow_id}")
                    return True
                else:
                    # Extract error message from response
                    error_body = payload_data.get('body', '{}')
                    if isinstance(error_body, str):
                        error_body = json.loads(error_body)
                    error_message = error_body.get('message', 'Unknown error')
                    logger.error(f"Workflow {workflow_id} execution failed with status {api_status_code}: {error_message}")
                    return False
            else:
                logger.error(f"No payload in response for workflow {workflow_id}")
                return False
        else:
            logger.error(f"Failed to invoke workflow {workflow_id}, Lambda status code: {status_code}")
            return False
            
    except Exception as e:
        logger.exception(f"Error invoking executeWorkflow for workflow {workflow.get('workflowId')}: {e}")
        return False


def process_file_event(database_id: str, asset_id: str, bucket_name: str, s3_key: str, file_path: str) -> Dict[str, Any]:
    """
    Process a file event and trigger matching workflows
    
    Args:
        database_id: Database ID
        asset_id: Asset ID
        bucket_name: S3 bucket name
        s3_key: S3 object key
        file_path: Relative file path
    
    Returns:
        Dictionary with processing results
    """
    result = {
        'filePath': file_path,
        'databaseId': database_id,
        'assetId': asset_id,
        'workflowsFound': 0,
        'workflowsTriggered': 0,
        'workflowsSucceeded': 0,
        'workflowsFailed': 0,
        'failures': []
    }
    
    try:
        # Check if file should be skipped
        if should_skip_file(s3_key, file_path):
            logger.info(f"Skipping excluded file: {file_path}")
            return result
        
        # Extract file extension
        file_extension = extract_file_extension(file_path)
        logger.info(f"Processing file {file_path} with extension: {file_extension}")
        
        # Get workflows for the specific database
        database_workflows = get_workflows_for_database(database_id)
        
        # Get workflows for GLOBAL database
        global_workflows = get_workflows_for_database("GLOBAL")
        
        # Combine both lists
        all_workflows = database_workflows + global_workflows
        result['workflowsFound'] = len(all_workflows)
        
        logger.info(f"Found {len(all_workflows)} workflows ({len(database_workflows)} database-specific, {len(global_workflows)} global)")
        
        # Process each workflow
        for workflow in all_workflows:
            workflow_id = workflow.get('workflowId', 'unknown')
            
            try:
                # Get autoTriggerOnFileExtensionsUpload field
                auto_trigger_extensions = workflow.get('autoTriggerOnFileExtensionsUpload', '')
                
                # Check if workflow should be triggered
                if should_trigger_workflow(auto_trigger_extensions, file_extension):
                    result['workflowsTriggered'] += 1
                    logger.info(f"Triggering workflow {workflow_id} for file {file_path}")
                    
                    # Invoke executeWorkflow
                    if invoke_execute_workflow(workflow, database_id, asset_id, file_path):
                        result['workflowsSucceeded'] += 1
                    else:
                        result['workflowsFailed'] += 1
                        result['failures'].append({
                            'workflowId': workflow_id,
                            'error': 'Failed to invoke workflow'
                        })
                else:
                    logger.debug(f"Workflow {workflow_id} not triggered (extensions: {auto_trigger_extensions}, file ext: {file_extension})")
                    
            except Exception as e:
                # Log error but continue processing other workflows
                logger.exception(f"Error processing workflow {workflow_id}: {e}")
                result['workflowsFailed'] += 1
                result['failures'].append({
                    'workflowId': workflow_id,
                    'error': str(e)
                })
        
        return result
        
    except Exception as e:
        logger.exception(f"Error processing file event for {file_path}: {e}")
        result['failures'].append({
            'error': f"Error processing file event: {str(e)}"
        })
        return result


def handle_s3_notification(event_record: Dict[str, Any], asset_bucket_name: Optional[str] = None, asset_bucket_prefix: Optional[str] = None) -> Dict[str, Any]:
    """Handle S3 bucket notification from SQS/SNS"""
    try:
        # Extract S3 information from event
        s3_info = event_record.get('s3', {})
        bucket_name = s3_info.get('bucket', {}).get('name')
        s3_key = s3_info.get('object', {}).get('key')
        event_name = event_record.get('eventName', '')
        
        if not bucket_name or not s3_key:
            logger.warning("Missing S3 bucket or key information")
            return {'error': 'Missing S3 information'}
        
        # Only process ObjectCreated events (skip ObjectRemoved events)
        if 'ObjectRemoved' in event_name or 'Delete' in event_name:
            logger.info(f"Skipping delete event: {event_name}")
            return {'skipped': 'delete event'}
        
        # URL decode the S3 key
        import urllib.parse
        s3_key = urllib.parse.unquote_plus(s3_key)
        
        # Skip folder markers
        if s3_key.endswith('/'):
            logger.info(f"Skipping folder marker: {s3_key}")
            return {'skipped': 'folder marker'}
        
        # Try to get metadata from S3 object
        try:
            s3_client = boto3.client('s3', config=retry_config)
            s3_response = s3_client.head_object(Bucket=bucket_name, Key=s3_key)
            s3_metadata = s3_response.get('Metadata', {})
            
            asset_id = s3_metadata.get('assetid')
            database_id = s3_metadata.get('databaseid')
            
            if not asset_id or not database_id:
                logger.warning(f"Missing asset/database ID in S3 metadata for {s3_key}")
                return {'skipped': 'missing metadata'}
            
            # Get asset details to calculate relative path
            asset_response = asset_storage_table.get_item(
                Key={
                    'databaseId': database_id,
                    'assetId': asset_id
                }
            )
            
            asset = asset_response.get('Item')
            if not asset:
                logger.warning(f"Asset not found: {database_id}/{asset_id}")
                return {'skipped': 'asset not found'}
            
            # Get bucket details
            bucket_id = asset.get('bucketId')
            if not bucket_id:
                logger.warning(f"No bucket ID for asset: {asset_id}")
                return {'skipped': 'no bucket ID'}
            
            bucket_response = s3_asset_buckets_table.query(
                KeyConditionExpression=Key('bucketId').eq(bucket_id),
                Limit=1
            )
            
            bucket_items = bucket_response.get('Items', [])
            if not bucket_items:
                logger.warning(f"Bucket not found: {bucket_id}")
                return {'skipped': 'bucket not found'}
            
            bucket = bucket_items[0]
            base_assets_prefix = bucket.get('baseAssetsPrefix', '/')
            
            # Normalize prefix
            if not base_assets_prefix.endswith('/'):
                base_assets_prefix += '/'
            if base_assets_prefix.startswith('/'):
                base_assets_prefix = base_assets_prefix[1:]
            
            # Calculate relative path
            asset_location = asset.get('assetLocation', {})
            asset_base_key = asset_location.get('Key', f"{base_assets_prefix}{asset_id}/")
            
            if s3_key.startswith(asset_base_key):
                relative_path = s3_key[len(asset_base_key):]
            else:
                relative_path = s3_key
            
            # Ensure relative path starts with a slash
            if not relative_path.startswith('/'):
                relative_path = '/' + relative_path
            
            # Process the file event
            return process_file_event(database_id, asset_id, bucket_name, s3_key, relative_path)
            
        except ClientError as e:
            logger.exception(f"Error getting S3 object metadata: {e}")
            return {'error': f'Error getting S3 metadata: {str(e)}'}
            
    except Exception as e:
        logger.exception(f"Error handling S3 notification: {e}")
        return {'error': f'Error handling S3 notification: {str(e)}'}


def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for SQS auto-execute workflow"""
    try:
        logger.info(f"Processing SQS auto-execute workflow event: {json.dumps(event, default=str)}")
        
        results = []
        
        # Handle SQS records
        if 'Records' in event:
            for record in event['Records']:
                event_source = record.get('eventSource', '')
                
                if event_source == 'aws:sqs':
                    # Parse SQS message body
                    try:
                        body = record.get('body', '')
                        if isinstance(body, str):
                            body = json.loads(body)
                        
                        # Extract bucket info at top level (passed from sqsBucketSync in new direct path)
                        asset_bucket_name = body.get('ASSET_BUCKET_NAME')
                        asset_bucket_prefix = body.get('ASSET_BUCKET_PREFIX')
                        
                        # NEW PATH: Check if body directly contains Records array (direct from sqsBucketSync)
                        if 'Records' in body and not body.get('Type'):
                            logger.info("Processing direct SQS message from sqsBucketSync")
                            for inner_record in body['Records']:
                                # Check if this is a nested SQS message (from bucketSync)
                                if inner_record.get('eventSource') == 'aws:sqs':
                                    # Parse the nested SQS message body
                                    try:
                                        inner_body = inner_record.get('body', '')
                                        if isinstance(inner_body, str):
                                            inner_body = json.loads(inner_body)
                                        
                                        # Check if this nested message is an SNS message
                                        if inner_body.get('Type') == 'Notification' and inner_body.get('Message'):
                                            inner_sns_message = inner_body.get('Message')
                                            if isinstance(inner_sns_message, str):
                                                inner_sns_message = json.loads(inner_sns_message)
                                            
                                            # Now check for S3 records in the inner SNS message
                                            if 'Records' in inner_sns_message:
                                                for s3_record in inner_sns_message['Records']:
                                                    if s3_record.get('eventSource') == 'aws:s3':
                                                        result = handle_s3_notification(
                                                            s3_record,
                                                            asset_bucket_name,
                                                            asset_bucket_prefix
                                                        )
                                                        results.append(result)
                                    except json.JSONDecodeError as inner_e:
                                        logger.exception(f"Error parsing nested SQS/SNS message: {inner_e}")
                                        results.append({'error': f'Error parsing nested message: {str(inner_e)}'})
                                
                                # Also check if this is a direct S3 record
                                elif inner_record.get('eventSource') == 'aws:s3':
                                    result = handle_s3_notification(
                                        inner_record,
                                        asset_bucket_name,
                                        asset_bucket_prefix
                                    )
                                    results.append(result)
                        
                        # OLD PATH: Check if this is an SNS message (for backward compatibility with preview events)
                        elif body.get('Type') == 'Notification' and body.get('Message'):
                            logger.info("Processing SNS message (legacy path)")
                            # Parse SNS message
                            sns_message = body.get('Message')
                            if isinstance(sns_message, str):
                                sns_message = json.loads(sns_message)
                            
                            # Extract bucket info from SNS message (may override top-level values)
                            if sns_message.get('ASSET_BUCKET_NAME'):
                                asset_bucket_name = sns_message.get('ASSET_BUCKET_NAME')
                            if sns_message.get('ASSET_BUCKET_PREFIX'):
                                asset_bucket_prefix = sns_message.get('ASSET_BUCKET_PREFIX')
                            
                            # Check if SNS message contains Records array
                            if 'Records' in sns_message:
                                for inner_record in sns_message['Records']:
                                    # Check if this is a nested SQS message (from bucketSync)
                                    if inner_record.get('eventSource') == 'aws:sqs':
                                        # Parse the nested SQS message body
                                        try:
                                            inner_body = inner_record.get('body', '')
                                            if isinstance(inner_body, str):
                                                inner_body = json.loads(inner_body)
                                            
                                            # Check if this nested message is also an SNS message
                                            if inner_body.get('Type') == 'Notification' and inner_body.get('Message'):
                                                inner_sns_message = inner_body.get('Message')
                                                if isinstance(inner_sns_message, str):
                                                    inner_sns_message = json.loads(inner_sns_message)
                                                
                                                # Now check for S3 records in the inner SNS message
                                                if 'Records' in inner_sns_message:
                                                    for s3_record in inner_sns_message['Records']:
                                                        if s3_record.get('eventSource') == 'aws:s3':
                                                            result = handle_s3_notification(
                                                                s3_record,
                                                                asset_bucket_name,
                                                                asset_bucket_prefix
                                                            )
                                                            results.append(result)
                                        except json.JSONDecodeError as inner_e:
                                            logger.exception(f"Error parsing nested SQS/SNS message: {inner_e}")
                                            results.append({'error': f'Error parsing nested message: {str(inner_e)}'})
                                    
                                    # Also check if this is a direct S3 record (original path)
                                    elif inner_record.get('eventSource') == 'aws:s3':
                                        result = handle_s3_notification(
                                            inner_record,
                                            asset_bucket_name,
                                            asset_bucket_prefix
                                        )
                                        results.append(result)
                        
                    except json.JSONDecodeError as e:
                        logger.exception(f"Error parsing SQS/SNS message: {e}")
                        results.append({'error': f'Error parsing message: {str(e)}'})
        
        # Summarize results
        total_files = len(results)
        total_workflows_found = sum(r.get('workflowsFound', 0) for r in results)
        total_workflows_triggered = sum(r.get('workflowsTriggered', 0) for r in results)
        total_workflows_succeeded = sum(r.get('workflowsSucceeded', 0) for r in results)
        total_workflows_failed = sum(r.get('workflowsFailed', 0) for r in results)
        
        response_body = {
            'message': f'Processed {total_files} file(s), triggered {total_workflows_triggered} workflow(s)',
            'filesProcessed': total_files,
            'workflowsFound': total_workflows_found,
            'workflowsTriggered': total_workflows_triggered,
            'workflowsSucceeded': total_workflows_succeeded,
            'workflowsFailed': total_workflows_failed,
            'results': results
        }
        
        logger.info(f"Auto-execute workflow summary: {json.dumps(response_body, default=str)}")
        
        return success(body=response_body)
        
    except Exception as e:
        logger.exception(f"Internal error in SQS auto-execute workflow: {e}")
        return internal_error()