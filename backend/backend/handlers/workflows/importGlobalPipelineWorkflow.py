#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""
Global Pipeline and Workflow Import Lambda Function

This Lambda function handles CloudFormation custom resource events to automatically
register new VAMS pipelines and workflows at a global database level during CDK deployment.
It orchestrates the creation and management of global pipeline and workflow entries by
invoking existing VAMS APIs.
"""

import os
import boto3
import json
import datetime
from typing import Dict, Any, Tuple, Optional
from botocore.exceptions import ClientError
from botocore.config import Config
from aws_lambda_powertools.utilities.typing import LambdaContext
from customLogging.logger import safeLogger

# Configure AWS clients with retry configuration
retry_config = Config(
    retries={
        'max_attempts': 5,
        'mode': 'adaptive'
    }
)

lambda_client = boto3.client('lambda', config=retry_config)
logger = safeLogger(service="ImportGlobalPipelineWorkflow")

# Load environment variables with error handling
try:
    create_pipeline_function_name = os.environ["CREATE_PIPELINE_FUNCTION_NAME"]
    pipeline_service_function_name = os.environ["PIPELINE_SERVICE_FUNCTION_NAME"]
    create_workflow_function_name = os.environ["CREATE_WORKFLOW_FUNCTION_NAME"]
    workflow_service_function_name = os.environ["WORKFLOW_SERVICE_FUNCTION_NAME"]
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e


class ImportError(Exception):
    """Base exception for import operations"""
    pass


class ValidationError(ImportError):
    """Raised when input validation fails"""
    pass


class ServiceError(ImportError):
    """Raised when service operations fail"""
    pass


class AuthorizationError(ImportError):
    """Raised when authorization fails"""
    pass


def send_cfn_response(event: Dict[str, Any], context: LambdaContext, 
                     response_status: str, response_data: Dict[str, Any] = None,
                     physical_resource_id: str = None, reason: str = None) -> None:
    """
    Send response to CloudFormation custom resource.
    
    Args:
        event: CloudFormation custom resource event
        context: Lambda context
        response_status: SUCCESS or FAILED
        response_data: Optional response data
        physical_resource_id: Optional physical resource ID
        reason: Optional reason for failure
    """
    import urllib3
    
    if response_data is None:
        response_data = {}
    
    if physical_resource_id is None:
        physical_resource_id = context.log_stream_name
    
    if reason is None:
        reason = f"See CloudWatch Log Stream: {context.log_stream_name}"
    
    response_body = {
        'Status': response_status,
        'Reason': reason,
        'PhysicalResourceId': physical_resource_id,
        'StackId': event['StackId'],
        'RequestId': event['RequestId'],
        'LogicalResourceId': event['LogicalResourceId'],
        'Data': response_data
    }
    
    json_response_body = json.dumps(response_body)
    
    logger.info(f"Response body: {json_response_body}")
    
    headers = {
        'content-type': '',
        'content-length': str(len(json_response_body))
    }
    
    try:
        http = urllib3.PoolManager()
        response = http.request(
            'PUT',
            event['ResponseURL'],
            body=json_response_body,
            headers=headers
        )
        logger.info(f"CloudFormation response status: {response.status}")
    except Exception as e:
        logger.exception(f"Failed to send CloudFormation response: {e}")


def validate_pipeline_parameters(pipeline_data: Dict[str, Any]) -> None:
    """
    Validate pipeline parameters.
    
    Args:
        pipeline_data: Pipeline configuration data
        
    Raises:
        ValidationError: If validation fails
    """
    logger.info("Validating pipeline parameters")
    
    # Required pipeline fields
    required_fields = [
        'pipelineId', 'pipelineDescription', 'pipelineType', 'pipelineExecutionType',
        'assetType', 'outputType', 'waitForCallback', 'lambdaName'
    ]
    
    for field in required_fields:
        if field not in pipeline_data or not pipeline_data[field]:
            raise ValidationError(f"Missing required pipeline field: {field}")
    
    # Validate pipelineType
    allowed_pipeline_types = ['standardFile', 'previewFile']
    if pipeline_data['pipelineType'] not in allowed_pipeline_types:
        raise ValidationError(f"Invalid pipelineType. Allowed values: {', '.join(allowed_pipeline_types)}")
    
    # Validate pipelineExecutionType
    allowed_execution_types = ['Lambda']
    if pipeline_data['pipelineExecutionType'] not in allowed_execution_types:
        raise ValidationError(f"Invalid pipelineExecutionType. Allowed values: {', '.join(allowed_execution_types)}")
    
    # Validate waitForCallback
    allowed_callback_values = ['Enabled', 'Disabled']
    if pipeline_data['waitForCallback'] not in allowed_callback_values:
        raise ValidationError(f"Invalid waitForCallback. Allowed values: {', '.join(allowed_callback_values)}")
    
    # Validate pipelineId format (basic validation)
    pipeline_id = pipeline_data['pipelineId']
    if len(pipeline_id) < 3 or len(pipeline_id) > 63:
        raise ValidationError("pipelineId must be between 3 and 63 characters")
    
    # Validate assetType format (should be file extension)
    asset_type = pipeline_data['assetType']
    if not asset_type.startswith('.') or len(asset_type) < 2:
        raise ValidationError("assetType must be a valid file extension (e.g., '.txt')")
    
    # Validate outputType format (should be file extension)
    output_type = pipeline_data['outputType']
    if not output_type.startswith('.') or len(output_type) < 2:
        raise ValidationError("outputType must be a valid file extension (e.g., '.json')")
    
    # Validate timeout values if provided
    task_timeout = None
    task_heartbeat = None
    
    # Validate taskTimeout if provided and not empty (empty string counts as undefined)
    if 'taskTimeout' in pipeline_data and pipeline_data['taskTimeout'] and pipeline_data['taskTimeout'].strip():
        if not pipeline_data['taskTimeout'].strip().isdigit():
            raise ValidationError("taskTimeout must be a valid integer")
        task_timeout = int(pipeline_data['taskTimeout'].strip())
    
    # Validate taskHeartbeatTimeout if provided and not empty (empty string counts as undefined)
    if 'taskHeartbeatTimeout' in pipeline_data and pipeline_data['taskHeartbeatTimeout'] and pipeline_data['taskHeartbeatTimeout'].strip():
        if not pipeline_data['taskHeartbeatTimeout'].strip().isdigit():
            raise ValidationError("taskHeartbeatTimeout must be a valid integer")
        task_heartbeat = int(pipeline_data['taskHeartbeatTimeout'].strip())
    
    # If both are provided, heartbeat must be less than timeout
    if task_timeout is not None and task_heartbeat is not None:
        if task_heartbeat >= task_timeout:
            raise ValidationError("taskHeartbeatTimeout must be less than taskTimeout")
    
    # Validate inputParameters if provided (should be valid JSON)
    if 'inputParameters' in pipeline_data and pipeline_data['inputParameters']:
        try:
            json.loads(pipeline_data['inputParameters'])
        except (ValueError, TypeError):
            raise ValidationError("inputParameters must be valid JSON")
    
    logger.info("Pipeline parameters validation passed")


def validate_workflow_parameters(workflow_data: Dict[str, Any]) -> None:
    """
    Validate workflow parameters.
    
    Args:
        workflow_data: Workflow configuration data
        
    Raises:
        ValidationError: If validation fails
    """
    logger.info("Validating workflow parameters")
    
    # Required workflow fields
    required_fields = ['workflowId', 'workflowDescription']
    
    for field in required_fields:
        if field not in workflow_data or not workflow_data[field]:
            raise ValidationError(f"Missing required workflow field: {field}")
    
    # Validate workflowId format (basic validation)
    workflow_id = workflow_data['workflowId']
    if len(workflow_id) < 3 or len(workflow_id) > 63:
        raise ValidationError("workflowId must be between 3 and 63 characters")
    
    logger.info("Workflow parameters validation passed")


def sanitize_and_set_defaults(resource_properties: Dict[str, Any]) -> Dict[str, Any]:
    """
    Sanitize input parameters and set default values.
    Automatically sets databaseId to "GLOBAL" for all operations.
    
    Args:
        resource_properties: Raw resource properties from CloudFormation
        
    Returns:
        Sanitized and processed resource properties
        
    Raises:
        ValidationError: If sanitization fails
    """
    logger.info("Sanitizing parameters and setting defaults")
    
    # Create a copy to avoid modifying the original
    sanitized = dict(resource_properties)
    
    # Automatically set databaseId to "GLOBAL" (not user-configurable)
    sanitized['databaseId'] = 'GLOBAL'
    logger.info("Automatically set databaseId to 'GLOBAL'")
    
    # Set default timeout values only if not provided (preserve empty strings as-is)
    if 'taskTimeout' not in sanitized:
        sanitized['taskTimeout'] = ''  
    
    if 'taskHeartbeatTimeout' not in sanitized:
        sanitized['taskHeartbeatTimeout'] = ''  
    
    # Set default inputParameters if not provided
    if 'inputParameters' not in sanitized or not sanitized['inputParameters']:
        sanitized['inputParameters'] = ''
    
    # Strip whitespace from string fields
    string_fields = ['pipelineId', 'pipelineDescription', 'workflowId', 'workflowDescription', 
                    'pipelineType', 'pipelineExecutionType', 'assetType', 'outputType', 
                    'waitForCallback', 'lambdaName']
    
    for field in string_fields:
        if field in sanitized and isinstance(sanitized[field], str):
            sanitized[field] = sanitized[field].strip()
    
    logger.info("Parameter sanitization completed")
    return sanitized


def validate_resource_properties(resource_properties: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate and sanitize all resource properties for pipeline and workflow import.
    
    Args:
        resource_properties: Raw resource properties from CloudFormation
        
    Returns:
        Validated and sanitized resource properties
        
    Raises:
        ValidationError: If validation fails
    """
    logger.info("Starting comprehensive parameter validation")
    
    # First sanitize and set defaults
    sanitized_properties = sanitize_and_set_defaults(resource_properties)
    
    # Validate pipeline parameters
    validate_pipeline_parameters(sanitized_properties)
    
    # Validate workflow parameters
    validate_workflow_parameters(sanitized_properties)
    
    logger.info("All parameter validation completed successfully")
    return sanitized_properties


def parse_custom_resource_event(event: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """
    Parse and validate CloudFormation custom resource event.
    
    Args:
        event: CloudFormation custom resource event
        
    Returns:
        Tuple of (operation_type, validated_resource_properties)
        
    Raises:
        ValidationError: If event is invalid
    """
    logger.info("Parsing custom resource event")
    
    # Validate required CloudFormation fields
    required_fields = ['RequestType', 'ResourceProperties', 'ResponseURL', 
                      'StackId', 'RequestId', 'LogicalResourceId']
    
    for field in required_fields:
        if field not in event:
            raise ValidationError(f"Missing required field: {field}")
    
    operation_type = event['RequestType']
    if operation_type not in ['Create', 'Update', 'Delete']:
        raise ValidationError(f"Invalid RequestType: {operation_type}")
    
    resource_properties = event['ResourceProperties']
    
    # For DELETE operations, we only need basic validation
    if operation_type == 'Delete':
        # For DELETE, we only need pipelineId and workflowId
        if 'pipelineId' not in resource_properties or not resource_properties['pipelineId']:
            raise ValidationError("Missing required field for DELETE: pipelineId")
        if 'workflowId' not in resource_properties or not resource_properties['workflowId']:
            raise ValidationError("Missing required field for DELETE: workflowId")
        
        # Set databaseId to GLOBAL for DELETE operations
        validated_properties = {
            'databaseId': 'GLOBAL',
            'pipelineId': resource_properties['pipelineId'].strip(),
            'workflowId': resource_properties['workflowId'].strip()
        }
    else:
        # For CREATE and UPDATE, perform full validation
        validated_properties = validate_resource_properties(resource_properties)
    
    logger.info(f"Parsed operation type: {operation_type}")
    logger.info(f"Validated resource properties keys: {list(validated_properties.keys())}")
    
    return operation_type, validated_properties



def invoke_lambda_function(function_name: str, payload: Dict[str, Any], 
                          invocation_type: str = "RequestResponse") -> Dict[str, Any]:
    """
    Invoke a Lambda function with proper error handling and response parsing.
    
    Args:
        function_name: Name of the Lambda function to invoke
        payload: Payload to send to the function
        invocation_type: Type of invocation (RequestResponse or Event)
        
    Returns:
        Parsed response from the Lambda function
        
    Raises:
        ServiceError: If Lambda invocation fails
    """
    logger.info(f"Invoking Lambda function: {function_name}")
    logger.info(f"Payload keys: {list(payload.keys()) if isinstance(payload, dict) else 'non-dict payload'}")
    
    try:
        # Convert payload to JSON string
        json_payload = json.dumps(payload)
        
        # Invoke the Lambda function
        response = lambda_client.invoke(
            FunctionName=function_name,
            InvocationType=invocation_type,
            Payload=json_payload
        )
        
        # Parse the response
        success, parsed_response = parse_lambda_response(response)
        
        if not success:
            error_msg = parsed_response.get('error', 'Unknown error')
            logger.error(f"Lambda function {function_name} returned error: {error_msg}")
            raise ServiceError(f"Lambda function {function_name} failed: {error_msg}")
        
        logger.info(f"Lambda function {function_name} invoked successfully")
        return parsed_response
        
    except ClientError as e:
        error_code = e.response['Error']['Code']
        error_message = e.response['Error']['Message']
        logger.exception(f"AWS ClientError invoking {function_name}: {error_code} - {error_message}")
        
        if error_code == 'ResourceNotFoundException':
            raise ServiceError(f"Lambda function {function_name} not found")
        elif error_code == 'InvalidParameterValueException':
            raise ServiceError(f"Invalid parameters for Lambda function {function_name}")
        elif error_code == 'TooManyRequestsException':
            raise ServiceError(f"Rate limit exceeded for Lambda function {function_name}")
        else:
            raise ServiceError(f"AWS error invoking {function_name}: {error_message}")
            
    except (ValueError, TypeError) as e:
        logger.exception(f"Error encoding payload for {function_name}: {e}")
        raise ServiceError(f"Invalid payload format for {function_name}")
        
    except Exception as e:
        logger.exception(f"Unexpected error invoking {function_name}: {e}")
        raise ServiceError(f"Failed to invoke {function_name}: {str(e)}")


def parse_lambda_response(response: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
    """
    Parse Lambda function response and extract success/error information.
    
    Args:
        response: Raw response from Lambda invoke
        
    Returns:
        Tuple of (success: bool, parsed_data: dict)
    """
    try:
        # Get the payload from the response
        payload = response.get('Payload')
        if not payload:
            logger.error("No payload in Lambda response")
            return False, {"error": "No payload in response"}
        
        # Read and parse the payload
        payload_str = payload.read().decode('utf-8')
        parsed_payload = json.loads(payload_str)
        
        # Check for Lambda execution errors
        if 'errorMessage' in parsed_payload:
            logger.error(f"Lambda execution error: {parsed_payload['errorMessage']}")
            return False, {
                "error": parsed_payload['errorMessage'],
                "errorType": parsed_payload.get('errorType', 'Unknown'),
                "stackTrace": parsed_payload.get('stackTrace', [])
            }
        
        # For HTTP responses, return success and let the calling function handle status codes
        # This allows functions like check_pipeline_exists to handle 404 responses appropriately
        status_code = parsed_payload.get('statusCode', 200)
        if isinstance(status_code, int):
            logger.info(f"Lambda returned HTTP status {status_code}")
        else:
            logger.info("Lambda response parsed successfully")
        
        # Return the parsed payload - let calling functions handle status codes
        return True, parsed_payload
        
    except (ValueError, TypeError) as e:
        logger.exception(f"Error parsing Lambda response JSON: {e}")
        return False, {"error": f"Invalid JSON response: {str(e)}"}
        
    except Exception as e:
        logger.exception(f"Error parsing Lambda response: {e}")
        return False, {"error": f"Response parsing error: {str(e)}"}


def extract_error_message(response_data: Dict[str, Any]) -> str:
    """
    Extract a meaningful error message from Lambda response data.
    
    Args:
        response_data: Parsed response data from Lambda function
        
    Returns:
        Extracted error message
    """
    # Try to get error from various possible locations
    if 'error' in response_data:
        return str(response_data['error'])
    
    if 'message' in response_data:
        return str(response_data['message'])
    
    if 'errorMessage' in response_data:
        return str(response_data['errorMessage'])
    
    # Try to extract from body if it's a string
    if 'body' in response_data:
        body = response_data['body']
        if isinstance(body, str):
            try:
                body_data = json.loads(body)
                if 'message' in body_data:
                    return str(body_data['message'])
            except (ValueError, TypeError):
                pass
        elif isinstance(body, dict) and 'message' in body:
            return str(body['message'])
    
    # Fallback to generic message
    return "Unknown error occurred"


def check_pipeline_exists(pipeline_id: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """
    Check if a pipeline exists using the pipelineService API.
    
    Args:
        pipeline_id: ID of the pipeline to check
        
    Returns:
        Tuple of (exists: bool, pipeline_data: dict or None)
        
    Raises:
        ServiceError: If API call fails
    """
    logger.info(f"Checking if pipeline exists: {pipeline_id}")
    
    try:
        # Create event for pipelineService GET request
        event = {
            'requestContext': {
                'http': {
                    'method': 'GET',
                    'path': f'/pipelines/GLOBAL/{pipeline_id}'
                }
            },
            'pathParameters': {
                'databaseId': 'GLOBAL',
                'pipelineId': pipeline_id
            },
            'queryStringParameters': {},
            'lambdaCrossCall': {
                'userName': 'SYSTEM_USER'
            }
        }
        
        # Invoke pipelineService to check if pipeline exists
        response = invoke_lambda_function(pipeline_service_function_name, event)
        
        # Check if pipeline was found
        status_code = response.get('statusCode', 500)
        if status_code == 200:
            # Pipeline exists - extract pipeline data from response
            body = response.get('body', '{}')
            if isinstance(body, str):
                body_data = json.loads(body)
            else:
                body_data = body
            
            pipeline_data = body_data.get('message', {})
            logger.info(f"Pipeline {pipeline_id} exists")
            return True, pipeline_data
        elif status_code == 404:
            # Pipeline does not exist
            logger.info(f"Pipeline {pipeline_id} does not exist")
            return False, None
        else:
            # Unexpected status code
            error_msg = extract_error_message(response)
            logger.error(f"Unexpected status code {status_code} checking pipeline {pipeline_id}: {error_msg}")
            raise ServiceError(f"Error checking pipeline existence: {error_msg}")
            
    except ServiceError:
        # Re-raise ServiceError as-is
        raise
    except Exception as e:
        logger.exception(f"Error checking pipeline existence for {pipeline_id}: {e}")
        raise ServiceError(f"Failed to check pipeline existence: {str(e)}")


def create_pipeline(pipeline_data: Dict[str, Any], update_associated_workflows: bool = True) -> Dict[str, Any]:
    """
    Create or update a pipeline using the createPipeline API.
    
    Args:
        pipeline_data: Pipeline configuration data
        update_associated_workflows: Whether to update associated workflows
        
    Returns:
        Response from createPipeline API
        
    Raises:
        ServiceError: If API call fails
    """
    logger.info(f"Creating/updating pipeline: {pipeline_data['pipelineId']}")
    logger.info(f"Update associated workflows: {update_associated_workflows}")
    
    try:
        # Prepare the payload for createPipeline
        payload = {
            'body': {
                'databaseId': pipeline_data['databaseId'],
                'pipelineId': pipeline_data['pipelineId'],
                'description': pipeline_data['pipelineDescription'],
                'assetType': pipeline_data['assetType'],
                'outputType': pipeline_data['outputType'],
                'pipelineType': pipeline_data['pipelineType'],
                'pipelineExecutionType': pipeline_data['pipelineExecutionType'],
                'waitForCallback': pipeline_data['waitForCallback'],
                'lambdaName': pipeline_data['lambdaName'],
                'inputParameters': pipeline_data.get('inputParameters', ''),
                'taskTimeout': pipeline_data.get('taskTimeout', ''),
                'taskHeartbeatTimeout': pipeline_data.get('taskHeartbeatTimeout', ''),
                'updateAssociatedWorkflows': update_associated_workflows
            },
            'requestContext': {
                'http': {
                    'method': 'POST',
                    'path': '/pipelines'
                }
            },
            'lambdaCrossCall': {
                'userName': 'SYSTEM_USER'
            }
        }
        
        # Invoke createPipeline
        response = invoke_lambda_function(create_pipeline_function_name, payload)
        
        # Check response status
        status_code = response.get('statusCode', 500)
        if status_code == 200:
            logger.info(f"Pipeline {pipeline_data['pipelineId']} created/updated successfully")
            return response
        else:
            error_msg = extract_error_message(response)
            logger.error(f"Failed to create/update pipeline {pipeline_data['pipelineId']}: {error_msg}")
            raise ServiceError(f"Pipeline creation failed: {error_msg}")
            
    except ServiceError:
        # Re-raise ServiceError as-is
        raise
    except Exception as e:
        logger.exception(f"Error creating pipeline {pipeline_data['pipelineId']}: {e}")
        raise ServiceError(f"Failed to create pipeline: {str(e)}")


def update_pipeline(pipeline_data: Dict[str, Any], existing_pipeline: Dict[str, Any]) -> Dict[str, Any]:
    """
    Update an existing pipeline using the createPipeline API.
    Determines whether to update associated workflows based on workflow ID changes.
    
    Args:
        pipeline_data: New pipeline configuration data
        existing_pipeline: Existing pipeline data
        
    Returns:
        Response from createPipeline API
        
    Raises:
        ServiceError: If API call fails
    """
    logger.info(f"Updating existing pipeline: {pipeline_data['pipelineId']}")
    
    # Determine if we need to update associated workflows
    # This happens if the workflow ID has changed
    update_workflows = should_update_workflows(existing_pipeline, pipeline_data.get('workflowId', ''))
    
    logger.info(f"Will update associated workflows: {update_workflows}")
    
    # Use create_pipeline function with appropriate updateAssociatedWorkflows flag
    return create_pipeline(pipeline_data, update_associated_workflows=update_workflows)


def delete_pipeline(pipeline_id: str) -> Dict[str, Any]:
    """
    Delete a pipeline using the pipelineService API.
    
    Args:
        pipeline_id: ID of the pipeline to delete
        
    Returns:
        Response from pipelineService DELETE API
        
    Raises:
        ServiceError: If API call fails
    """
    logger.info(f"Deleting pipeline: {pipeline_id}")
    
    try:
        # Create event for pipelineService DELETE request
        event = {
            'requestContext': {
                'http': {
                    'method': 'DELETE',
                    'path': f'/pipelines/GLOBAL/{pipeline_id}'
                }
            },
            'pathParameters': {
                'databaseId': 'GLOBAL',
                'pipelineId': pipeline_id
            },
            'lambdaCrossCall': {
                'userName': 'SYSTEM_USER'
            }
        }
        
        # Invoke pipelineService to delete pipeline
        response = invoke_lambda_function(pipeline_service_function_name, event)
        
        # Check response status
        status_code = response.get('statusCode', 500)
        if status_code == 200:
            logger.info(f"Pipeline {pipeline_id} deleted successfully")
            return response
        elif status_code == 404:
            # Pipeline doesn't exist - consider this a success for DELETE
            logger.info(f"Pipeline {pipeline_id} not found - considering delete successful")
            return {"statusCode": 200, "message": "Pipeline not found - delete successful"}
        else:
            error_msg = extract_error_message(response)
            logger.error(f"Failed to delete pipeline {pipeline_id}: {error_msg}")
            raise ServiceError(f"Pipeline deletion failed: {error_msg}")
            
    except ServiceError:
        # Re-raise ServiceError as-is
        raise
    except Exception as e:
        logger.exception(f"Error deleting pipeline {pipeline_id}: {e}")
        raise ServiceError(f"Failed to delete pipeline: {str(e)}")


def should_update_workflows(existing_pipeline: Dict[str, Any], new_workflow_id: str) -> bool:
    """
    Determine if workflows should be updated based on changes to the pipeline.
    
    Args:
        existing_pipeline: Existing pipeline data
        new_workflow_id: New workflow ID from the update
        
    Returns:
        True if workflows should be updated, False otherwise
    """
    # For now, we'll use a simple heuristic:
    # Update workflows if the workflow ID has changed or if we can't determine the existing workflow ID
    
    # Try to extract existing workflow ID from pipeline data
    # This might be stored in different places depending on the pipeline structure
    existing_workflow_id = None
    
    # Check various possible locations for workflow ID in existing pipeline
    if 'workflowId' in existing_pipeline:
        existing_workflow_id = existing_pipeline['workflowId']
    elif 'specifiedPipelines' in existing_pipeline:
        # This might be stored in workflow references
        pass  # More complex logic could be added here
    
    if existing_workflow_id is None:
        # Can't determine existing workflow ID - assume update is needed
        logger.info("Cannot determine existing workflow ID - will update workflows")
        return True
    
    if existing_workflow_id != new_workflow_id:
        logger.info(f"Workflow ID changed from {existing_workflow_id} to {new_workflow_id} - will update workflows")
        return True
    
    logger.info("Workflow ID unchanged - will not update workflows")
    return False


def check_workflow_exists(workflow_id: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """
    Check if a workflow exists using the workflowService API.
    
    Args:
        workflow_id: ID of the workflow to check
        
    Returns:
        Tuple of (exists: bool, workflow_data: dict or None)
        
    Raises:
        ServiceError: If API call fails
    """
    logger.info(f"Checking if workflow exists: {workflow_id}")
    
    try:
        # Create event for workflowService GET request
        event = {
            'requestContext': {
                'http': {
                    'method': 'GET',
                    'path': f'/workflows/GLOBAL/{workflow_id}'
                }
            },
            'pathParameters': {
                'databaseId': 'GLOBAL',
                'workflowId': workflow_id
            },
            'queryStringParameters': {},
            'lambdaCrossCall': {
                'userName': 'SYSTEM_USER'
            }
        }
        
        # Invoke workflowService to check if workflow exists
        response = invoke_lambda_function(workflow_service_function_name, event)
        
        # Check if workflow was found
        status_code = response.get('statusCode', 500)
        if status_code == 200:
            # Workflow exists - extract workflow data from response
            body = response.get('body', '{}')
            if isinstance(body, str):
                body_data = json.loads(body)
            else:
                body_data = body
            
            workflow_data = body_data.get('message', {})
            logger.info(f"Workflow {workflow_id} exists")
            return True, workflow_data
        elif status_code == 404:
            # Workflow does not exist
            logger.info(f"Workflow {workflow_id} does not exist")
            return False, None
        else:
            # Unexpected status code
            error_msg = extract_error_message(response)
            logger.error(f"Unexpected status code {status_code} checking workflow {workflow_id}: {error_msg}")
            raise ServiceError(f"Error checking workflow existence: {error_msg}")
            
    except ServiceError:
        # Re-raise ServiceError as-is
        raise
    except Exception as e:
        logger.exception(f"Error checking workflow existence for {workflow_id}: {e}")
        raise ServiceError(f"Failed to check workflow existence: {str(e)}")


def create_workflow(workflow_data: Dict[str, Any], pipeline_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Create or update a workflow using the createWorkflow API.
    
    Args:
        workflow_data: Workflow configuration data
        pipeline_data: Pipeline configuration data (for specifiedPipelines)
        
    Returns:
        Response from createWorkflow API
        
    Raises:
        ServiceError: If API call fails
    """
    logger.info(f"Creating/updating workflow: {workflow_data['workflowId']}")
    
    try:
        # Build the specifiedPipelines structure
        # This should reference the pipeline that was created/updated
        specified_pipelines = {
            "functions": [
                {
                    "name": pipeline_data['pipelineId'],
                    "databaseId": pipeline_data['databaseId'],
                    "pipelineType": pipeline_data['pipelineType'],
                    "pipelineExecutionType": pipeline_data['pipelineExecutionType'],
                    "assetType": pipeline_data['assetType'],
                    "outputType": pipeline_data['outputType'],
                    "waitForCallback": pipeline_data['waitForCallback'],
                    "taskTimeout": pipeline_data.get('taskTimeout', ''),
                    "taskHeartbeatTimeout": pipeline_data.get('taskHeartbeatTimeout', ''),
                    "inputParameters": pipeline_data.get('inputParameters', ''),
                    "userProvidedResource": json.dumps({
                        "isProvided": True,
                        "resourceId": pipeline_data['lambdaName']
                    })
                }
            ]
        }
        
        # Prepare the payload for createWorkflow
        payload = {
            'body': {
                'databaseId': workflow_data['databaseId'],
                'workflowId': workflow_data['workflowId'],
                'description': workflow_data['workflowDescription'],
                'specifiedPipelines': specified_pipelines,
                'autoTriggerOnFileExtensionsUpload': workflow_data.get('autoTriggerOnFileExtensionsUpload', '')
            },
            'requestContext': {
                'http': {
                    'method': 'POST',
                    'path': '/workflows'
                }
            },
            'lambdaCrossCall': {
                'userName': 'SYSTEM_USER'
            }
        }
        
        # Invoke createWorkflow
        response = invoke_lambda_function(create_workflow_function_name, payload)
        
        # Check response status
        status_code = response.get('statusCode', 500)
        if status_code == 200:
            logger.info(f"Workflow {workflow_data['workflowId']} created/updated successfully")
            return response
        else:
            error_msg = extract_error_message(response)
            logger.error(f"Failed to create/update workflow {workflow_data['workflowId']}: {error_msg}")
            raise ServiceError(f"Workflow creation failed: {error_msg}")
            
    except ServiceError:
        # Re-raise ServiceError as-is
        raise
    except Exception as e:
        logger.exception(f"Error creating workflow {workflow_data['workflowId']}: {e}")
        raise ServiceError(f"Failed to create workflow: {str(e)}")


def update_workflow(workflow_data: Dict[str, Any], pipeline_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Update an existing workflow using the createWorkflow API.
    
    Args:
        workflow_data: New workflow configuration data
        pipeline_data: Pipeline configuration data
        
    Returns:
        Response from createWorkflow API
        
    Raises:
        ServiceError: If API call fails
    """
    logger.info(f"Updating existing workflow: {workflow_data['workflowId']}")
    
    # Use create_workflow function (createWorkflow API handles both create and update)
    return create_workflow(workflow_data, pipeline_data)


def delete_workflow(workflow_id: str) -> Dict[str, Any]:
    """
    Delete a workflow using the workflowService API.
    
    Args:
        workflow_id: ID of the workflow to delete
        
    Returns:
        Response from workflowService DELETE API
        
    Raises:
        ServiceError: If API call fails
    """
    logger.info(f"Deleting workflow: {workflow_id}")
    
    try:
        # Create event for workflowService DELETE request
        event = {
            'requestContext': {
                'http': {
                    'method': 'DELETE',
                    'path': f'/workflows/GLOBAL/{workflow_id}'
                }
            },
            'pathParameters': {
                'databaseId': 'GLOBAL',
                'workflowId': workflow_id
            },
            'lambdaCrossCall': {
                'userName': 'SYSTEM_USER'
            }
        }
        
        # Invoke workflowService to delete workflow
        response = invoke_lambda_function(workflow_service_function_name, event)
        
        # Check response status
        status_code = response.get('statusCode', 500)
        if status_code == 200:
            logger.info(f"Workflow {workflow_id} deleted successfully")
            return response
        elif status_code == 404:
            # Workflow doesn't exist - consider this a success for DELETE
            logger.info(f"Workflow {workflow_id} not found - considering delete successful")
            return {"statusCode": 200, "message": "Workflow not found - delete successful"}
        else:
            error_msg = extract_error_message(response)
            logger.error(f"Failed to delete workflow {workflow_id}: {error_msg}")
            raise ServiceError(f"Workflow deletion failed: {error_msg}")
            
    except ServiceError:
        # Re-raise ServiceError as-is
        raise
    except Exception as e:
        logger.exception(f"Error deleting workflow {workflow_id}: {e}")
        raise ServiceError(f"Failed to delete workflow: {str(e)}")


def pipeline_needs_update(existing_pipeline: Dict[str, Any], new_pipeline_data: Dict[str, Any]) -> bool:
    """
    Determine if a pipeline needs to be updated based on changes.
    
    Args:
        existing_pipeline: Existing pipeline data from the database
        new_pipeline_data: New pipeline configuration data
        
    Returns:
        True if pipeline needs update, False otherwise
    """
    # Compare key pipeline fields that would require an update
    fields_to_compare = [
        ('description', 'pipelineDescription'),
        ('assetType', 'assetType'),
        ('outputType', 'outputType'),
        ('pipelineType', 'pipelineType'),
        ('pipelineExecutionType', 'pipelineExecutionType'),
        ('waitForCallback', 'waitForCallback'),
        ('inputParameters', 'inputParameters'),
        ('taskTimeout', 'taskTimeout'),
        ('taskHeartbeatTimeout', 'taskHeartbeatTimeout')
    ]
    
    for existing_field, new_field in fields_to_compare:
        existing_value = existing_pipeline.get(existing_field, '')
        new_value = new_pipeline_data.get(new_field, '')
        
        # Normalize empty values (None, empty string, etc.)
        existing_value = existing_value if existing_value else ''
        new_value = new_value if new_value else ''
        
        # Convert to string for comparison
        existing_str = str(existing_value).strip()
        new_str = str(new_value).strip()
        
        if existing_str != new_str:
            logger.info(f"Pipeline field '{existing_field}' changed: '{existing_str}' -> '{new_str}'")
            return True
    
    # Check lambda name from userProvidedResource
    if 'userProvidedResource' in existing_pipeline:
        try:
            user_resource = json.loads(existing_pipeline['userProvidedResource'])
            existing_lambda = user_resource.get('resourceId', '')
        except (ValueError, TypeError):
            existing_lambda = ''
    else:
        existing_lambda = ''
    
    new_lambda = new_pipeline_data.get('lambdaName', '')
    
    # Normalize and compare lambda names
    existing_lambda = existing_lambda.strip() if existing_lambda else ''
    new_lambda = new_lambda.strip() if new_lambda else ''
    
    if existing_lambda != new_lambda:
        logger.info(f"Pipeline lambda name changed: '{existing_lambda}' -> '{new_lambda}'")
        return True
    
    logger.info("No significant pipeline changes detected")
    return False


def workflow_needs_update(existing_workflow: Dict[str, Any], new_workflow_data: Dict[str, Any], new_pipeline_data: Dict[str, Any]) -> bool:
    """
    Determine if a workflow needs to be updated based on changes.
    
    Args:
        existing_workflow: Existing workflow data
        new_workflow_data: New workflow configuration data
        new_pipeline_data: New pipeline configuration data (for specifiedPipelines comparison)
        
    Returns:
        True if workflow needs update, False otherwise
    """
    # Check if description has changed
    existing_description = existing_workflow.get('description', '').strip()
    new_description = new_workflow_data.get('workflowDescription', '').strip()
    
    if existing_description != new_description:
        logger.info(f"Workflow description changed: '{existing_description}' -> '{new_description}'")
        return True
    
    # Check if pipeline configuration in specifiedPipelines has changed
    existing_pipelines = existing_workflow.get('specifiedPipelines', {})
    if not existing_pipelines or 'functions' not in existing_pipelines:
        logger.info("Cannot determine existing pipeline configuration - update needed")
        return True
    
    existing_functions = existing_pipelines.get('functions', [])
    if not existing_functions:
        logger.info("No existing pipeline functions found - update needed")
        return True
    
    # For simplicity, we assume there's one pipeline function (which is typical for our use case)
    if len(existing_functions) != 1:
        logger.info("Multiple pipeline functions found - update needed for safety")
        return True
    
    existing_function = existing_functions[0]
    
    # Compare key pipeline fields in the workflow's specifiedPipelines
    pipeline_fields_to_compare = [
        ('name', 'pipelineId'),
        ('databaseId', 'databaseId'),
        ('pipelineType', 'pipelineType'),
        ('pipelineExecutionType', 'pipelineExecutionType'),
        ('assetType', 'assetType'),
        ('outputType', 'outputType'),
        ('waitForCallback', 'waitForCallback'),
        ('taskTimeout', 'taskTimeout'),
        ('taskHeartbeatTimeout', 'taskHeartbeatTimeout'),
        ('inputParameters', 'inputParameters')
    ]
    
    for existing_field, new_field in pipeline_fields_to_compare:
        existing_value = existing_function.get(existing_field, '')
        new_value = new_pipeline_data.get(new_field, '')
        
        # Normalize empty values
        existing_value = existing_value if existing_value else ''
        new_value = new_value if new_value else ''
        
        # Convert to string for comparison
        existing_str = str(existing_value).strip()
        new_str = str(new_value).strip()
        
        if existing_str != new_str:
            logger.info(f"Workflow pipeline field '{existing_field}' changed: '{existing_str}' -> '{new_str}'")
            return True
    
    # Check lambda name from userProvidedResource in the workflow
    if 'userProvidedResource' in existing_function:
        try:
            user_resource = json.loads(existing_function['userProvidedResource'])
            existing_lambda = user_resource.get('resourceId', '')
        except (ValueError, TypeError):
            existing_lambda = ''
    else:
        existing_lambda = ''
    
    new_lambda = new_pipeline_data.get('lambdaName', '')
    
    # Normalize and compare lambda names
    existing_lambda = existing_lambda.strip() if existing_lambda else ''
    new_lambda = new_lambda.strip() if new_lambda else ''
    
    if existing_lambda != new_lambda:
        logger.info(f"Workflow lambda name changed: '{existing_lambda}' -> '{new_lambda}'")
        return True
    
    logger.info("No significant workflow changes detected")
    return False


def rollback_create_operation(pipeline_id: str, workflow_id: str, 
                             pipeline_created: bool, workflow_created: bool) -> None:
    """
    Rollback a partially successful CREATE operation.
    
    Args:
        pipeline_id: ID of the pipeline
        workflow_id: ID of the workflow
        pipeline_created: Whether pipeline was successfully created
        workflow_created: Whether workflow was successfully created
    """
    logger.info(f"Rolling back CREATE operation - pipeline_created: {pipeline_created}, workflow_created: {workflow_created}")
    
    rollback_errors = []
    
    # Delete workflow if it was created
    if workflow_created:
        try:
            logger.info(f"Rolling back workflow creation: {workflow_id}")
            delete_workflow(workflow_id)
            logger.info(f"Successfully rolled back workflow: {workflow_id}")
        except Exception as e:
            error_msg = f"Failed to rollback workflow {workflow_id}: {str(e)}"
            logger.error(error_msg)
            rollback_errors.append(error_msg)
    
    # Delete pipeline if it was created
    if pipeline_created:
        try:
            logger.info(f"Rolling back pipeline creation: {pipeline_id}")
            delete_pipeline(pipeline_id)
            logger.info(f"Successfully rolled back pipeline: {pipeline_id}")
        except Exception as e:
            error_msg = f"Failed to rollback pipeline {pipeline_id}: {str(e)}"
            logger.error(error_msg)
            rollback_errors.append(error_msg)
    
    if rollback_errors:
        logger.error(f"Rollback completed with errors: {rollback_errors}")
    else:
        logger.info("Rollback completed successfully")


def handle_create_operation(resource_properties: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle CREATE operation for pipeline and workflow import.
    
    CREATE Operation Flow:
    1. Validate Input Parameters
    2. Set databaseId to "GLOBAL"
    3. Check Pipeline Existence
    4. Check Workflow Existence
    5. Create Pipeline (with updateAssociatedWorkflows=True)
    6. Create Workflow (only if not handled by pipeline creation)
    
    Args:
        resource_properties: CloudFormation resource properties
        
    Returns:
        Operation result data
        
    Raises:
        ValidationError: If validation fails
        ServiceError: If service operations fail
    """
    logger.info("Handling CREATE operation")
    
    pipeline_id = resource_properties['pipelineId']
    workflow_id = resource_properties['workflowId']
    
    pipeline_created = False
    workflow_created = False
    
    try:
        # Step 1 & 2: Validation and databaseId setting already done in parse_custom_resource_event
        
        # Step 3: Check Pipeline Existence
        logger.info("Step 3: Checking pipeline existence")
        pipeline_exists, existing_pipeline = check_pipeline_exists(pipeline_id)
        
        if pipeline_exists:
            raise ServiceError(f"Pipeline {pipeline_id} already exists. Use UPDATE operation to modify it.")
        
        # Step 4: Check Workflow Existence
        logger.info("Step 4: Checking workflow existence")
        workflow_exists, existing_workflow = check_workflow_exists(workflow_id)
        
        if workflow_exists:
            raise ServiceError(f"Workflow {workflow_id} already exists. Use UPDATE operation to modify it.")
        
        # Step 5: Create Pipeline (with updateAssociatedWorkflows=True)
        logger.info("Step 5: Creating pipeline")
        pipeline_response = create_pipeline(resource_properties, update_associated_workflows=True)
        pipeline_created = True
        logger.info("Pipeline created successfully")
        
        # Step 6: Create Workflow (only if not handled by pipeline creation)
        # Since we set updateAssociatedWorkflows=True, the pipeline creation might have handled workflow creation
        # We'll still create the workflow explicitly to ensure it exists with the correct configuration
        logger.info("Step 6: Creating workflow")
        workflow_response = create_workflow(resource_properties, resource_properties)
        workflow_created = True
        logger.info("Workflow created successfully")
        
        # Success - return result
        result = {
            "operation": "CREATE",
            "status": "SUCCESS",
            "pipelineId": pipeline_id,
            "workflowId": workflow_id,
            "message": f"Successfully created pipeline {pipeline_id} and workflow {workflow_id}",
            "pipelineResponse": pipeline_response,
            "workflowResponse": workflow_response
        }
        
        logger.info("CREATE operation completed successfully")
        return result
        
    except Exception as e:
        logger.exception(f"CREATE operation failed: {e}")
        
        # Rollback any partial success
        try:
            rollback_create_operation(pipeline_id, workflow_id, pipeline_created, workflow_created)
        except Exception as rollback_error:
            logger.exception(f"Rollback also failed: {rollback_error}")
        
        # Re-raise the original exception
        raise


def handle_update_operation(resource_properties: Dict[str, Any], 
                          old_resource_properties: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle UPDATE operation for pipeline and workflow import.
    
    UPDATE Operation Flow:
    1. Validate Input Parameters
    2. Set databaseId to "GLOBAL"
    3. Check Pipeline Existence
    4. Check Workflow Existence
    5. Determine Workflow Update Need
    6. Update Pipeline (with appropriate updateAssociatedWorkflows flag)
    7. Update Workflow (only if workflow definition changed and wasn't handled by pipeline update)
    
    Args:
        resource_properties: New CloudFormation resource properties
        old_resource_properties: Previous CloudFormation resource properties
        
    Returns:
        Operation result data
        
    Raises:
        ValidationError: If validation fails
        ServiceError: If service operations fail
    """
    logger.info("Handling UPDATE operation")
    
    pipeline_id = resource_properties['pipelineId']
    workflow_id = resource_properties['workflowId']
    
    try:
        # Step 1 & 2: Validation and databaseId setting already done in parse_custom_resource_event
        
        # Step 3: Check Pipeline Existence
        logger.info("Step 3: Checking pipeline existence")
        pipeline_exists, existing_pipeline = check_pipeline_exists(pipeline_id)
        
        if not pipeline_exists:
            logger.info(f"Pipeline {pipeline_id} does not exist - treating UPDATE as CREATE")
            # If pipeline doesn't exist, treat this as a CREATE operation
            return handle_create_operation(resource_properties)
        
        # Step 4: Check Workflow Existence
        logger.info("Step 4: Checking workflow existence")
        workflow_exists, existing_workflow = check_workflow_exists(workflow_id)
        
        # Step 5: Check if pipeline needs update
        logger.info("Step 5: Checking if pipeline needs update")
        pipeline_needs_updating = pipeline_needs_update(existing_pipeline, resource_properties)
        
        # Step 6: Check if workflow needs update
        logger.info("Step 6: Checking if workflow needs update")
        workflow_needs_updating = False
        if not workflow_exists:
            logger.info("Workflow doesn't exist - needs creation")
            workflow_needs_updating = True
        else:
            workflow_needs_updating = workflow_needs_update(existing_workflow, resource_properties, resource_properties)
        
        # Step 7: Update Pipeline (only if needed)
        pipeline_response = None
        old_workflow_id = old_resource_properties.get('workflowId', '')
        workflow_id_changed = (old_workflow_id != workflow_id)
        
        if pipeline_needs_updating:
            logger.info("Step 7: Updating pipeline (changes detected)")
            
            # Determine if we need to update associated workflows
            update_workflows = workflow_id_changed or should_update_workflows(existing_pipeline, workflow_id)
            
            pipeline_response = update_pipeline(resource_properties, existing_pipeline)
            logger.info("Pipeline updated successfully")
        else:
            logger.info("Step 7: Pipeline update not needed - no changes detected")
            pipeline_response = {"statusCode": 200, "message": "Pipeline update not needed - no changes detected"}
            update_workflows = False  # No pipeline update means no workflow update via pipeline
        
        # Step 8: Update Workflow (only if needed and not handled by pipeline update)
        workflow_response = None
        if not workflow_exists:
            logger.info("Step 8: Creating workflow (doesn't exist)")
            workflow_response = create_workflow(resource_properties, resource_properties)
            logger.info("Workflow created successfully")
        elif workflow_needs_updating and not (pipeline_needs_updating and update_workflows):
            # Only update workflow explicitly if it needs updating and wasn't handled by pipeline update
            logger.info("Step 8: Updating workflow (changes detected)")
            workflow_response = update_workflow(resource_properties, resource_properties)
            logger.info("Workflow updated successfully")
        else:
            if workflow_needs_updating and pipeline_needs_updating and update_workflows:
                logger.info("Step 8: Workflow update handled by pipeline update")
                workflow_response = {"statusCode": 200, "message": "Workflow update handled by pipeline update"}
            else:
                logger.info("Step 8: Workflow update not needed - no changes detected")
                workflow_response = {"statusCode": 200, "message": "Workflow update not needed - no changes detected"}
        
        # Success - return result
        pipeline_action = "updated" if pipeline_needs_updating else "unchanged"
        workflow_action = "created" if not workflow_exists else ("updated" if workflow_needs_updating else "unchanged")
        
        result = {
            "operation": "UPDATE",
            "status": "SUCCESS",
            "pipelineId": pipeline_id,
            "workflowId": workflow_id,
            "message": f"Pipeline {pipeline_id} {pipeline_action}, workflow {workflow_id} {workflow_action}",
            "pipelineResponse": pipeline_response,
            "workflowResponse": workflow_response,
            "pipelineNeedsUpdate": pipeline_needs_updating,
            "workflowNeedsUpdate": workflow_needs_updating,
            "workflowExists": workflow_exists,
            "updatedAssociatedWorkflows": update_workflows if pipeline_needs_updating else False
        }
        
        logger.info("UPDATE operation completed successfully")
        return result
        
    except Exception as e:
        logger.exception(f"UPDATE operation failed: {e}")
        raise


def handle_delete_operation(resource_properties: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle DELETE operation for pipeline and workflow import.
    
    DELETE Operation Flow:
    1. Set databaseId to "GLOBAL"
    2. Delete Workflow First
    3. Delete Pipeline
    4. Handle Partial Failures (log warnings but continue)
    
    Args:
        resource_properties: CloudFormation resource properties
        
    Returns:
        Operation result data
        
    Raises:
        ValidationError: If validation fails
        ServiceError: If service operations fail
    """
    logger.info("Handling DELETE operation")
    
    pipeline_id = resource_properties['pipelineId']
    workflow_id = resource_properties['workflowId']
    
    deletion_errors = []
    workflow_deleted = False
    pipeline_deleted = False
    
    try:
        # Step 1: databaseId already set to "GLOBAL" in parse_custom_resource_event
        
        # Step 2: Delete Workflow First
        logger.info("Step 2: Deleting workflow")
        try:
            workflow_response = delete_workflow(workflow_id)
            workflow_deleted = True
            logger.info("Workflow deleted successfully")
        except ServiceError as e:
            error_msg = f"Failed to delete workflow {workflow_id}: {str(e)}"
            logger.warning(error_msg)
            deletion_errors.append(error_msg)
        except Exception as e:
            error_msg = f"Unexpected error deleting workflow {workflow_id}: {str(e)}"
            logger.warning(error_msg)
            deletion_errors.append(error_msg)
        
        # Step 3: Delete Pipeline
        logger.info("Step 3: Deleting pipeline")
        try:
            pipeline_response = delete_pipeline(pipeline_id)
            pipeline_deleted = True
            logger.info("Pipeline deleted successfully")
        except ServiceError as e:
            error_msg = f"Failed to delete pipeline {pipeline_id}: {str(e)}"
            logger.warning(error_msg)
            deletion_errors.append(error_msg)
        except Exception as e:
            error_msg = f"Unexpected error deleting pipeline {pipeline_id}: {str(e)}"
            logger.warning(error_msg)
            deletion_errors.append(error_msg)
        
        # Step 4: Handle Partial Failures
        if deletion_errors:
            logger.warning(f"DELETE operation completed with warnings: {deletion_errors}")
            # For DELETE operations, we consider partial success as overall success
            # This prevents CloudFormation from getting stuck on resources that may not exist
            result = {
                "operation": "DELETE",
                "status": "SUCCESS_WITH_WARNINGS",
                "pipelineId": pipeline_id,
                "workflowId": workflow_id,
                "message": f"DELETE completed with warnings. Pipeline deleted: {pipeline_deleted}, Workflow deleted: {workflow_deleted}",
                "warnings": deletion_errors,
                "pipelineDeleted": pipeline_deleted,
                "workflowDeleted": workflow_deleted
            }
        else:
            result = {
                "operation": "DELETE",
                "status": "SUCCESS",
                "pipelineId": pipeline_id,
                "workflowId": workflow_id,
                "message": f"Successfully deleted pipeline {pipeline_id} and workflow {workflow_id}",
                "pipelineDeleted": pipeline_deleted,
                "workflowDeleted": workflow_deleted
            }
        
        logger.info("DELETE operation completed")
        return result
        
    except Exception as e:
        logger.exception(f"DELETE operation failed: {e}")
        # For DELETE operations, we're more lenient with errors
        # Return a success with warnings rather than failing
        result = {
            "operation": "DELETE",
            "status": "SUCCESS_WITH_WARNINGS",
            "pipelineId": pipeline_id,
            "workflowId": workflow_id,
            "message": f"DELETE operation encountered errors but completed. Pipeline deleted: {pipeline_deleted}, Workflow deleted: {workflow_deleted}",
            "warnings": deletion_errors + [str(e)],
            "pipelineDeleted": pipeline_deleted,
            "workflowDeleted": workflow_deleted
        }
        
        logger.info("DELETE operation completed with errors")
        return result


def lambda_handler(event: Dict[str, Any], context: LambdaContext) -> Dict[str, Any]:
    """
    Lambda handler for CloudFormation custom resource events for pipeline/workflow import.
    
    This function handles CREATE, UPDATE, and DELETE operations for automatically
    registering VAMS pipelines and workflows during CDK deployment.
    
    Args:
        event: CloudFormation custom resource event
        context: Lambda context
        
    Returns:
        CloudFormation custom resource response (via send_cfn_response)
    """
    logger.info(f"Received event: {json.dumps(event, default=str)}")
    
    try:
        # Parse the custom resource event
        operation_type, resource_properties = parse_custom_resource_event(event)
        
        logger.info(f"Processing {operation_type} operation")
        
        # Route to appropriate operation handler
        if operation_type == 'Create':
            result = handle_create_operation(resource_properties)
        elif operation_type == 'Update':
            result = handle_update_operation(resource_properties, event.get('OldResourceProperties', {}))
        elif operation_type == 'Delete':
            result = handle_delete_operation(resource_properties)
        else:
            raise ValidationError(f"Unsupported operation type: {operation_type}")
        
        # Send success response to CloudFormation
        send_cfn_response(
            event, 
            context, 
            'SUCCESS', 
            result,
            reason=f"{operation_type} operation completed successfully"
        )
        
        logger.info(f"{operation_type} operation completed successfully")
        return {"statusCode": 200, "body": json.dumps(result)}
        
    except ValidationError as e:
        logger.exception(f"Validation error: {e}")
        send_cfn_response(
            event, 
            context, 
            'FAILED', 
            {},
            reason=f"Validation error: {str(e)}"
        )
        return {"statusCode": 400, "body": json.dumps({"error": str(e)})}
        
    except ServiceError as e:
        logger.exception(f"Service error: {e}")
        send_cfn_response(
            event, 
            context, 
            'FAILED', 
            {},
            reason=f"Service error: {str(e)}"
        )
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}
        
    except AuthorizationError as e:
        logger.exception(f"Authorization error: {e}")
        send_cfn_response(
            event, 
            context, 
            'FAILED', 
            {},
            reason=f"Authorization error: {str(e)}"
        )
        return {"statusCode": 403, "body": json.dumps({"error": str(e)})}
        
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        send_cfn_response(
            event, 
            context, 
            'FAILED', 
            {},
            reason=f"Unexpected error: {str(e)}"
        )
        return {"statusCode": 500, "body": json.dumps({"error": "Internal server error"})}
