#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""
Consolidated Lambda handler for EKS pipeline operations.
This single handler replaces multiple Lambda functions by using an operation parameter
to route requests to the appropriate handler logic.
"""

import os
import boto3
import json
import uuid
import time
import sys
from botocore.exceptions import ClientError

# Initialize Step Functions client for task token callbacks
sfn = boto3.client('stepfunctions')

# Import custom logging utilities
from customLogging.logger import safeLogger
logger = safeLogger(service="ConsolidatedEksHandler")

# Import Kubernetes utilities
from kubernetes_utils import (
    get_k8s_client,
    create_job,
    check_job_status,
    get_pod_logs_for_job,
    delete_job,
    with_retries
)

# Initialize AWS clients
s3 = boto3.client('s3')
sfn = boto3.client('stepfunctions')

# Get environment variables with defaults
CLUSTER_NAME = os.environ.get('EKS_CLUSTER_NAME')
CONTAINER_IMAGE_URI = os.environ.get('CONTAINER_IMAGE_URI')
NAMESPACE = os.environ.get('KUBERNETES_NAMESPACE', 'default')
REGION = os.environ.get('AWS_REGION', 'us-west-2')
ALLOWED_INPUT_FILEEXTENSIONS = os.environ.get('ALLOWED_INPUT_FILEEXTENSIONS',
                                             '.glb,.gltf,.fbx,.obj,.stl,.ply,.usd,.usdz,.dae,.abc')

def validate_required_parameters(event, required_params, operation_name):
    """
    Utility function to validate required parameters for any operation.

    Args:
        event: The event dictionary
        required_params: List of required parameter names
        operation_name: Name of the operation for error messages

    Returns:
        tuple: (is_valid, error_message, missing_params)
    """
    if not isinstance(event, dict):
        return False, f"{operation_name}: Event must be a dictionary", []

    missing_params = []
    for param in required_params:
        if param not in event or event[param] is None:
            missing_params.append(param)
        elif isinstance(event[param], str) and not event[param].strip():
            missing_params.append(f"{param} (empty string)")

    if missing_params:
        error_msg = f"{operation_name}: Missing or empty required parameters: {', '.join(missing_params)}"
        return False, error_msg, missing_params

    return True, None, []

def validate_s3_uri(uri, param_name):
    """
    Validate S3 URI format.

    Args:
        uri: The S3 URI to validate
        param_name: Parameter name for error messages

    Returns:
        tuple: (is_valid, error_message)
    """
    if not uri or not isinstance(uri, str):
        return False, f"{param_name}: S3 URI cannot be empty"

    if not uri.startswith('s3://'):
        return False, f"{param_name}: Must be a valid S3 URI starting with 's3://'"

    parts = uri.replace('s3://', '').split('/')
    if len(parts) < 2 or not parts[0]:  # Must have bucket and at least one path component
        return False, f"{param_name}: Invalid S3 URI format, must include bucket and key"

    return True, None

def extract_s3_components(s3_uri):
    """
    Extract bucket and key from S3 URI.

    Args:
        s3_uri: The S3 URI to parse

    Returns:
        tuple: (bucket, key) or (None, None) if invalid
    """
    try:
        if not s3_uri.startswith('s3://'):
            return None, None
        parts = s3_uri.replace('s3://', '').split('/', 1)
        if len(parts) != 2:
            return None, None

        bucket = parts[0]
        key = parts[1]

        return bucket, key
    except Exception:
        return None, None

def validate_file_extension(file_path, allowed_extensions_str):
    """
    Validate file extension against allowed extensions.

    Args:
        file_path: Path to the file
        allowed_extensions_str: Comma-separated string of allowed extensions

    Returns:
        tuple: (is_valid, error_message, extension)
    """
    if not file_path:
        return False, "File path cannot be empty", None

    _, extension = os.path.splitext(file_path)
    extension = extension.lower()

    allowed_extensions = [ext.strip().lower() for ext in allowed_extensions_str.split(',')]

    if extension not in allowed_extensions:
        return False, f"Unsupported file extension '{extension}'. Allowed: {', '.join(allowed_extensions)}", extension

    return True, None, extension

def create_error_response(status_code, operation, error_message, details=None):
    """
    Create standardized error response.

    Args:
        status_code: HTTP status code
        operation: Operation name
        error_message: Error message
        details: Additional details (optional)

    Returns:
        dict: Standardized error response
    """
    response = {
        'statusCode': status_code,
        'body': {
            "operation": operation,
            "message": error_message
        }
    }

    if details:
        response['body']['details'] = details

    return response

def lambda_handler(event, context):
    """
    Main handler that routes operations to the appropriate function with comprehensive error handling
    based on the 'operation' parameter in the event.
    """
    # Initialize execution context
    execution_id = context.aws_request_id if context else "unknown"
    start_time = time.time()

    logger.info(f"ConsolidatedEksHandler execution started - Request ID: {execution_id}")
    logger.info(f"Function name: {context.function_name if context else 'unknown'}")
    logger.info(f"Function version: {context.function_version if context else 'unknown'}")
    logger.info(f"Remaining time: {context.get_remaining_time_in_millis() if context else 'unknown'}ms")

    try:
        # Enhanced event logging with size validation
        try:
            if event is None:
                logger.error("Event is None")
                return create_error_response(400, "UNKNOWN", "Event is null", {"requestId": execution_id})

            event_str = json.dumps(event)
            event_size = len(event_str.encode('utf-8'))
            logger.info(f"Event size: {event_size} bytes")

            # Log event structure
            if isinstance(event, dict):
                event_keys = list(event.keys())
                logger.info(f"Event keys: {event_keys}")
            else:
                logger.info(f"Event type: {type(event)}")

            # Log full event if small enough
            if event_size < 2000:
                logger.info(f"Received event: {event}")
            else:
                logger.info("Event too large to log in full")

        except Exception as log_error:
            logger.warning(f"Could not log event details: {log_error}")
            # Continue processing even if logging fails

        # Validate event structure
        if not isinstance(event, dict):
            error_msg = f"Event must be a dictionary, got {type(event)}"
            logger.error(error_msg)
            return create_error_response(400, "UNKNOWN", error_msg, {"requestId": execution_id})

        # Extract operation from event with validation
        operation = event.get('operation')
        if not operation:
            error_msg = "Missing 'operation' parameter in event"
            logger.error(error_msg)
            return create_error_response(400, "UNKNOWN", error_msg, {"requestId": execution_id})

        if not isinstance(operation, str):
            error_msg = f"Operation must be a string, got {type(operation)}"
            logger.error(error_msg)
            return create_error_response(400, "UNKNOWN", error_msg, {"requestId": execution_id})

        operation = operation.strip().upper()
        logger.info(f"Operation: {operation}")

        # Validate operation is supported
        supported_operations = ['CONSTRUCT_PIPELINE', 'RUN_JOB', 'CHECK_JOB', 'PIPELINE_END']
        if operation not in supported_operations:
            error_msg = f"Unknown operation: {operation}. Supported operations: {', '.join(supported_operations)}"
            logger.error(error_msg)
            return create_error_response(400, operation, error_msg, {
                "supportedOperations": supported_operations,
                "requestId": execution_id
            })

        # Check remaining execution time before processing
        if context:
            remaining_time = context.get_remaining_time_in_millis()
            if remaining_time < 30000:  # Less than 30 seconds
                logger.warning(f"Low remaining execution time: {remaining_time}ms for operation {operation}")

        # Route to the appropriate handler with enhanced error handling
        logger.info(f"Routing to handler for operation: {operation}")

        try:
            if operation == 'CONSTRUCT_PIPELINE':
                result = handle_construct_pipeline(event)
            elif operation == 'RUN_JOB':
                result = handle_run_job(event)
            elif operation == 'CHECK_JOB':
                result = handle_check_job(event)
            elif operation == 'PIPELINE_END':
                result = handle_pipeline_end(event)
            else:
                # This should not happen due to validation above, but keeping for safety
                error_msg = f"Unhandled operation: {operation}"
                logger.error(error_msg)
                return create_error_response(500, operation, error_msg, {"requestId": execution_id})

            # Validate and enhance result
            if not isinstance(result, dict):
                logger.warning(f"Handler returned non-dict result: {type(result)}")
                result = {"statusCode": 200, "body": result}

            # Add execution metadata to successful responses
            if result.get('statusCode', 200) < 400:
                execution_time = time.time() - start_time
                if isinstance(result.get('body'), dict):
                    result['body']['requestId'] = execution_id
                    result['body']['executionTime'] = execution_time
                    result['body']['operation'] = operation

                logger.info(f"Operation {operation} completed successfully in {execution_time:.2f} seconds")
            else:
                logger.warning(f"Operation {operation} returned error status: {result.get('statusCode')}")

            return result

        except ValueError as ve:
            # Handle validation and configuration errors
            error_msg = f"Validation error in {operation}: {str(ve)}"
            logger.error(error_msg)
            return create_error_response(400, operation, error_msg, {
                "errorType": "ValidationError",
                "requestId": execution_id
            })

        except ClientError as ce:
            # Handle AWS service errors
            error_code = ce.response.get('Error', {}).get('Code', 'Unknown')
            error_msg = f"AWS service error in {operation}: {str(ce)}"
            logger.error(error_msg)
            logger.error(f"AWS Error Code: {error_code}")

            return create_error_response(500, operation, error_msg, {
                "errorType": "AWSServiceError",
                "awsErrorCode": error_code,
                "requestId": execution_id
            })

        except Exception as e:
            # Handle unexpected errors from operation handlers
            error_msg = f"Unexpected error in {operation}: {str(e)}"
            logger.exception(error_msg)

            # Add detailed error context
            error_context = {
                "operation": operation,
                "errorType": type(e).__name__,
                "requestId": execution_id,
                "executionTime": time.time() - start_time
            }

            return create_error_response(500, operation, error_msg, error_context)

    except Exception as e:
        # Top-level exception handler for critical errors
        execution_time = time.time() - start_time

        logger.exception(f"Critical error in ConsolidatedEksHandler: {str(e)}")
        logger.error(f"Error type: {type(e).__name__}")
        logger.error(f"Execution time before error: {execution_time:.2f} seconds")

        # Log system information for debugging
        try:
            import sys
            system_info = {
                "python_version": sys.version,
                "lambda_runtime": os.environ.get('AWS_EXECUTION_ENV', 'unknown'),
                "memory_limit": os.environ.get('AWS_LAMBDA_FUNCTION_MEMORY_SIZE', 'unknown'),
                "function_name": os.environ.get('AWS_LAMBDA_FUNCTION_NAME', 'unknown'),
                "region": os.environ.get('AWS_REGION', 'unknown')
            }
            logger.error(f"System information: {system_info}")
        except Exception as sys_error:
            logger.warning(f"Could not gather system information: {sys_error}")

        # Return comprehensive error response
        return {
            'statusCode': 500,
            'body': {
                "message": "Critical system error",
                "error": str(e),
                "errorType": type(e).__name__,
                "operation": "UNKNOWN",
                "requestId": execution_id,
                "executionTime": execution_time,
                "details": "A critical error occurred in the consolidated handler"
            }
        }

def handle_construct_pipeline(event):
    """
    Handler for constructing pipeline definition
    Transforms input parameters into a Kubernetes job manifest
    """
    logger.info("Starting CONSTRUCT_PIPELINE operation")

    # Validate required parameters
    required_params = [
        'inputS3AssetFilePath',
        'outputS3AssetFilesPath'
    ]

    is_valid, error_message, missing_params = validate_required_parameters(event, required_params, "CONSTRUCT_PIPELINE")
    if not is_valid:
        logger.error(error_message)
        return {
            "error": {
                "Error": "ConstructPipelineParameterError",
                "Cause": error_message
            }
        }

    # Validate S3 URIs
    for param in required_params:
        is_valid, error_message = validate_s3_uri(event[param], param)
        if not is_valid:
            logger.error(error_message)
            return {
                "error": {
                    "Error": "ConstructPipelineParameterError",
                    "Cause": error_message
                }
            }

    try:
        # Extract parameters with validation
        job_name = event.get("jobName")
        if not job_name or not job_name.strip():
            job_name = f"job-{time.strftime('%Y%m%d-%H%M%S')}"

        input_s3_asset_file_uri = event['inputS3AssetFilePath']
        output_s3_asset_files_uri = event['outputS3AssetFilesPath']

        # Parse S3 URIs with validation
        input_s3_asset_file_bucket, input_s3_asset_file_key = extract_s3_components(input_s3_asset_file_uri)
        if not input_s3_asset_file_bucket or not input_s3_asset_file_key:
            raise ValueError(f"Invalid S3 URI format for inputS3AssetFilePath: {input_s3_asset_file_uri}")

        output_s3_asset_files_bucket, output_s3_asset_files_key = extract_s3_components(output_s3_asset_files_uri)
        if not output_s3_asset_files_bucket or not output_s3_asset_files_key:
            raise ValueError(f"Invalid S3 URI format for outputS3AssetFilesPath: {output_s3_asset_files_uri}")

        # Get file information
        input_s3_asset_file_root, input_s3_asset_extension = os.path.splitext(input_s3_asset_file_key)
        input_s3_asset_file_filename = os.path.basename(input_s3_asset_file_root)

        # Get auxiliary bucket information with validation
        inputOutput_s3_assetAuxiliary_files_uri = event.get('inputOutputS3AssetAuxiliaryFilesPath', '')
        if inputOutput_s3_assetAuxiliary_files_uri:
            is_valid, error_message = validate_s3_uri(inputOutput_s3_assetAuxiliary_files_uri, 'inputOutputS3AssetAuxiliaryFilesPath')
            if not is_valid:
                raise ValueError(error_message)
            inputOutput_s3_assetAuxiliary_files_bucket, inputOutput_s3_assetAuxiliary_files_key = extract_s3_components(inputOutput_s3_assetAuxiliary_files_uri)
            if not inputOutput_s3_assetAuxiliary_files_bucket or not inputOutput_s3_assetAuxiliary_files_key:
                raise ValueError(f"Invalid S3 URI format for inputOutputS3AssetAuxiliaryFilesPath: {inputOutput_s3_assetAuxiliary_files_uri}")
        else:
            # Default to input bucket if not provided
            inputOutput_s3_assetAuxiliary_files_bucket = input_s3_asset_file_bucket
            inputOutput_s3_assetAuxiliary_files_key = "auxiliary"

        # Get output type
        output_s3_asset_extension = event.get('outputFileType', input_s3_asset_extension)

        # Handle .all format to generate all supported output formats
        is_all_formats = (output_s3_asset_extension == '.all')

        # Determine output filename
        if is_all_formats:
            output_s3_asset_file_filename = input_s3_asset_file_filename + '*'
            output_path_base = output_s3_asset_files_key.rstrip('/')
        elif input_s3_asset_extension == output_s3_asset_extension:
            output_s3_asset_file_filename = input_s3_asset_file_filename + output_s3_asset_extension
        else:
            output_s3_asset_file_filename = input_s3_asset_file_filename + '-' + output_s3_asset_extension.replace(".", "") + output_s3_asset_extension

        # Build container command with output path
        if not is_all_formats:
            output_path = output_s3_asset_files_key
            if output_path.endswith('/'):
                output_path = output_path + output_s3_asset_file_filename
            else:
                output_path = output_s3_asset_files_key

        # Format standard RapidPipeline command string
        if is_all_formats:
            # Generate all formats and upload using shell globbing
            standard_command_with_config = f"aws s3 cp s3://{input_s3_asset_file_bucket}/{input_s3_asset_file_key} . && /rpdx/rpdx --read_config rp_config.json -i {input_s3_asset_file_filename}{input_s3_asset_extension} -c && for file in {input_s3_asset_file_filename}*; do aws s3 cp \"$file\" s3://{output_s3_asset_files_bucket}/{output_path_base}/\"$file\"; done"
            standard_command_no_config = f"aws s3 cp s3://{input_s3_asset_file_bucket}/{input_s3_asset_file_key} . && /rpdx/rpdx -i {input_s3_asset_file_filename}{input_s3_asset_extension} -c && for file in {input_s3_asset_file_filename}*; do aws s3 cp \"$file\" s3://{output_s3_asset_files_bucket}/{output_path_base}/\"$file\"; done"
        else:
            standard_command_with_config = f"aws s3 cp s3://{input_s3_asset_file_bucket}/{input_s3_asset_file_key} . && /rpdx/rpdx --read_config rp_config.json -i {input_s3_asset_file_filename}{input_s3_asset_extension} -c -e {output_s3_asset_file_filename} && aws s3 cp {output_s3_asset_file_filename} s3://{output_s3_asset_files_bucket}/{output_path}"
            standard_command_no_config = f"aws s3 cp s3://{input_s3_asset_file_bucket}/{input_s3_asset_file_key} . && /rpdx/rpdx -i {input_s3_asset_file_filename}{input_s3_asset_extension} -c -e {output_s3_asset_file_filename} && aws s3 cp {output_s3_asset_file_filename} s3://{output_s3_asset_files_bucket}/{output_path}"

        # Handle custom configurations using config.json file
        processing_command = standard_command_no_config
        input_parameters = event.get('inputParameters', '')

        if input_parameters:
            # Write config json file to S3
            s3.put_object(
                Body=input_parameters,
                Bucket=inputOutput_s3_assetAuxiliary_files_bucket,
                Key=f"{inputOutput_s3_assetAuxiliary_files_key}/rp_config.json"
            )
            # Download config file from S3, read config file, then execute standard command
            processing_command = f"aws s3 cp s3://{inputOutput_s3_assetAuxiliary_files_bucket}/{inputOutput_s3_assetAuxiliary_files_key}/rp_config.json rp_config.json && {standard_command_with_config}"

        # Generate unique job ID
        job_id = str(uuid.uuid4())[:8]

        # Validate container image URI configuration
        if not CONTAINER_IMAGE_URI or CONTAINER_IMAGE_URI == "CONTAINER_IMAGE_PLACEHOLDER":
            raise ValueError(f"Invalid container image URI: {CONTAINER_IMAGE_URI}. Please check CONTAINER_IMAGE_URI environment variable.")

        logger.info(f"Using container image: {CONTAINER_IMAGE_URI}")

        # Create enhanced Kubernetes job manifest with proper resource allocation and service account
        job_manifest = {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {
                "name": f"rapid-pipeline-job-{job_id}",
                "labels": {
                    "app": "rapid-pipeline",
                    "jobName": job_name or "unknown",
                    "createdBy": "vams",
                    "pipeline-type": "rapidPipelineEKS",
                    "job-id": job_id
                },
                "annotations": {
                    "vams.aws.amazon.com/pipeline-type": "rapidPipelineEKS",
                    "vams.aws.amazon.com/job-id": job_id,
                    "vams.aws.amazon.com/input-file": input_s3_asset_file_key,
                    "vams.aws.amazon.com/output-type": output_s3_asset_extension
                }
            },
            "spec": {
                "ttlSecondsAfterFinished": 600,  # Delete job 10 minutes after completion
                "activeDeadlineSeconds": 7200,  # Maximum job runtime of 2 hours for large files
                "template": {
                    "metadata": {
                        "labels": {
                            "app": "rapid-pipeline",
                            "job-id": job_id
                        }
                    },
                    "spec": {
                        "serviceAccountName": os.environ.get("KUBERNETES_SERVICE_ACCOUNT", "rapid-pipeline-sa"),  # Service account for S3 access
                        "containers": [{
                            "name": "rapid-pipeline",
                            "image": CONTAINER_IMAGE_URI,
                            "imagePullPolicy": "Always",
                            "command": ["/bin/sh", "-c"],
                            "args": [processing_command],
                            "resources": {
                                "requests": {
                                    "memory": "16Gi",  # 16GB memory as specified in requirements
                                    "cpu": "2000m"     # 2 vCPU as specified in requirements
                                },
                                "limits": {
                                    "memory": "16Gi",  # Same as requests for guaranteed QoS
                                    "cpu": "2000m"     # Same as requests for guaranteed QoS
                                }
                            },
                            "env": [
                                {
                                    "name": "externalSfnTaskToken",
                                    "value": event.get("externalSfnTaskToken", "")
                                },
                                {
                                    "name": "AWS_DEFAULT_REGION",
                                    "value": REGION
                                },
                                {
                                    "name": "JOB_ID",
                                    "value": job_id
                                },
                                {
                                    "name": "INPUT_FILE_TYPE",
                                    "value": input_s3_asset_extension
                                },
                                {
                                    "name": "OUTPUT_FILE_TYPE",
                                    "value": output_s3_asset_extension
                                }
                            ],
                            # Add volume mounts for temporary storage if needed
                            "volumeMounts": [{
                                "name": "tmp-storage",
                                "mountPath": "/tmp"
                            }]
                        }],
                        "volumes": [{
                            "name": "tmp-storage",
                            "emptyDir": {
                                "sizeLimit": "10Gi"  # Temporary storage for processing
                            }
                        }],
                        "restartPolicy": "Never",
                        # Add node selector for better resource allocation
                        "nodeSelector": {
                            "role": "pipeline-worker"
                        },
                        # Add tolerations if needed for dedicated nodes
                        "tolerations": []
                    }
                },
                "backoffLimit": 2,  # Allow 2 retries for transient failures
                "parallelism": 1,   # Ensure only one pod runs at a time
                "completions": 1    # Job completes when one pod succeeds
            }
        }

        logger.info(f"Created job manifest for {job_name}")

        return {
            "jobName": job_name,
            "jobManifest": job_manifest,
            "inputMetadata": event.get("inputMetadata", ""),
            "inputParameters": event.get("inputParameters", ""),
            "externalSfnTaskToken": event.get("externalSfnTaskToken", ""),
            "status": "STARTING"
        }

    except Exception as e:
        logger.exception(f"Error constructing pipeline: {str(e)}")
        return {
            "error": {
                "Error": "ConstructPipelineError",
                "Cause": str(e)
            }
        }

def handle_run_job(event):
    """
    Handler for running a Kubernetes job
    Creates the job in the EKS cluster using kubernetes_utils
    """
    logger.info("Starting RUN_JOB operation")

    # Validate required parameters
    required_params = ['jobManifest']

    is_valid, error_message, missing_params = validate_required_parameters(event, required_params, "RUN_JOB")
    if not is_valid:
        logger.error(error_message)
        return create_error_response(400, "RUN_JOB", error_message)

    # Validate job manifest structure
    job_manifest = event.get('jobManifest')
    if not isinstance(job_manifest, dict):
        error_message = "Job manifest must be a dictionary"
        logger.error(error_message)
        return create_error_response(400, "RUN_JOB", error_message)

    # Validate essential job manifest fields
    required_manifest_fields = ['apiVersion', 'kind', 'metadata', 'spec']
    missing_fields = [field for field in required_manifest_fields if field not in job_manifest]
    if missing_fields:
        error_message = f"Job manifest missing required fields: {', '.join(missing_fields)}"
        logger.error(error_message)
        return create_error_response(400, "RUN_JOB", error_message)

    try:
        # Create the job using kubernetes_utils
        job_name = create_job(job_manifest, namespace=NAMESPACE)

        logger.info(f"Successfully created job: {job_name}")

        return {
            'statusCode': 200,
            'body': {
                "status": "RUNNING",
                "jobName": event.get("jobName", "unknown"),
                "k8sJobName": job_name,
                "externalSfnTaskToken": event.get("externalSfnTaskToken", ""),
                "message": "Job submitted to EKS cluster"
            }
        }

    except Exception as e:
        logger.exception(f"Error creating job: {str(e)}")
        return {
            'statusCode': 500,
            'body': {
                "status": "FAILED",
                "error": {
                    "Error": "JobCreationError",
                    "Cause": str(e)
                }
            }
        }

def handle_check_job(event):
    """
    Handler for monitoring a Kubernetes job
    Checks job status using kubernetes_utils with enhanced monitoring
    """
    logger.info("Starting CHECK_JOB operation")

    # Validate required parameters
    required_params = ['k8sJobName']

    is_valid, error_message, missing_params = validate_required_parameters(event, required_params, "CHECK_JOB")
    if not is_valid:
        logger.error(error_message)
        return create_error_response(400, "CHECK_JOB", error_message)

    # Validate job name format
    k8s_job_name = event.get('k8sJobName')
    if not isinstance(k8s_job_name, str) or not k8s_job_name.strip():
        error_message = "k8sJobName must be a non-empty string"
        logger.error(error_message)
        return create_error_response(400, "CHECK_JOB", error_message)

    # Extract monitoring context from event
    counter = event.get('counter', 0)
    max_attempts = event.get('maxAttempts', 360)
    start_time = event.get('startTime', '')

    logger.info(f"Checking job {k8s_job_name} - attempt {counter + 1}/{max_attempts}")

    # Check if we have an external task token for Step Functions callback
    external_task_token = event.get("externalSfnTaskToken", "")

    try:
        # Check job status using kubernetes_utils
        status, error_logs = check_job_status(k8s_job_name, namespace=NAMESPACE)

        # Enhanced status mapping with better transitions
        if status == "SUCCEEDED":
            logger.info(f"Job {k8s_job_name} completed successfully after {counter + 1} checks")

            # Get success logs for debugging
            success_logs = ""
            try:
                success_logs = get_pod_logs_for_job(k8s_job_name, NAMESPACE)
            except Exception as log_error:
                logger.warning(f"Could not retrieve success logs: {log_error}")
                success_logs = "Logs not available"

            # If we have an external task token, call back to Step Functions
            if external_task_token:
                logger.info(f"Calling Step Functions task success for external token")
                try:
                    result = {
                        "status": "COMPLETED",
                        "jobName": event.get("jobName", k8s_job_name),
                        "k8sJobName": k8s_job_name,
                        "counter": counter,
                        "maxAttempts": max_attempts,
                        "startTime": start_time,
                        "logs": success_logs,
                        "message": f"Job completed successfully after {counter + 1} status checks"
                    }

                    sfn.send_task_success(
                        taskToken=external_task_token,
                        output=json.dumps(result)
                    )
                    logger.info(f"Successfully notified Step Functions of job completion")
                except Exception as callback_error:
                    logger.error(f"Error calling Step Functions task success: {callback_error}")

            return {
                'statusCode': 200,
                'body': {
                    "status": "COMPLETED",
                    "jobName": event.get("jobName", k8s_job_name),
                    "k8sJobName": k8s_job_name,
                    "externalSfnTaskToken": external_task_token,
                    "counter": counter,
                    "maxAttempts": max_attempts,
                    "startTime": start_time,
                    "logs": success_logs,
                    "message": f"Job completed successfully after {counter + 1} status checks"
                }
            }

        elif status == "FAILED":
            logger.error(f"Job {k8s_job_name} failed after {counter + 1} checks")

            # Enhanced error information
            detailed_error_logs = error_logs or "No error logs available"
            try:
                # Try to get more detailed logs
                pod_logs = get_pod_logs_for_job(k8s_job_name, NAMESPACE)
                if pod_logs and pod_logs != "No pods found for job":
                    detailed_error_logs = pod_logs
            except Exception as log_error:
                logger.warning(f"Could not retrieve detailed error logs: {log_error}")

            # If we have an external task token, call back to Step Functions
            if external_task_token:
                logger.info(f"Calling Step Functions task failure for external token")
                try:
                    sfn.send_task_failure(
                        taskToken=external_task_token,
                        error='JobExecutionFailed',
                        cause=f"Job failed after {counter + 1} status checks. Error details: {detailed_error_logs}"
                    )
                    logger.info(f"Successfully notified Step Functions of job failure")
                except Exception as callback_error:
                    logger.error(f"Error calling Step Functions task failure: {callback_error}")

            return {
                'statusCode': 500,
                'body': {
                    "status": "FAILED",
                    "jobName": event.get("jobName", k8s_job_name),
                    "k8sJobName": k8s_job_name,
                    "externalSfnTaskToken": external_task_token,
                    "counter": counter,
                    "maxAttempts": max_attempts,
                    "startTime": start_time,
                    "error": {
                        "Error": "JobExecutionFailed",
                        "Cause": f"Job failed after {counter + 1} status checks. Error details: {detailed_error_logs}"
                    },
                    "logs": detailed_error_logs
                }
            }

        elif status == "RUNNING":
            logger.info(f"Job {k8s_job_name} is still running - check {counter + 1}/{max_attempts}")
            return {
                'statusCode': 200,
                'body': {
                    "status": "RUNNING",
                    "jobName": event.get("jobName", k8s_job_name),
                    "k8sJobName": k8s_job_name,
                    "externalSfnTaskToken": external_task_token,
                    "counter": counter,
                    "maxAttempts": max_attempts,
                    "startTime": start_time,
                    "message": f"Job is still running - check {counter + 1}/{max_attempts}"
                }
            }

        elif status == "UNKNOWN":
            logger.warning(f"Job {k8s_job_name} status is unknown - check {counter + 1}/{max_attempts}")
            return {
                'statusCode': 500,
                'body': {
                    "status": "UNKNOWN",
                    "jobName": event.get("jobName", k8s_job_name),
                    "k8sJobName": k8s_job_name,
                    "externalSfnTaskToken": external_task_token,
                    "counter": counter,
                    "maxAttempts": max_attempts,
                    "startTime": start_time,
                    "error": {
                        "Error": "JobStatusUnknown",
                        "Cause": f"Job status is unknown after {counter + 1} status checks. Error details: {error_logs or 'No error details available'}"
                    },
                    "logs": error_logs or "No logs available"
                }
            }

        else:
            logger.error(f"Unexpected job status '{status}' for job {k8s_job_name}")
            return {
                'statusCode': 500,
                'body': {
                    "status": "FAILED",
                    "jobName": event.get("jobName", k8s_job_name),
                    "k8sJobName": k8s_job_name,
                    "externalSfnTaskToken": external_task_token,
                    "counter": counter,
                    "maxAttempts": max_attempts,
                    "startTime": start_time,
                    "error": {
                        "Error": "UnexpectedJobStatus",
                        "Cause": f"Unexpected job status '{status}' after {counter + 1} status checks"
                    }
                }
            }

    except Exception as e:
        logger.exception(f"Error checking job status for {k8s_job_name}: {str(e)}")

        # If we have an external task token, call back to Step Functions with failure
        if external_task_token:
            logger.info(f"Calling Step Functions task failure for external token due to exception")
            try:
                sfn.send_task_failure(
                    taskToken=external_task_token,
                    error='JobCheckError',
                    cause=f"Failed to check job status after {counter + 1} attempts. Error: {str(e)}"
                )
                logger.info(f"Successfully notified Step Functions of job check error")
            except Exception as callback_error:
                logger.error(f"Error calling Step Functions task failure: {callback_error}")

        # Enhanced error handling with context preservation
        return {
            'statusCode': 500,
            'body': {
                "status": "FAILED",
                "jobName": event.get("jobName", k8s_job_name),
                "k8sJobName": k8s_job_name,
                "externalSfnTaskToken": external_task_token,
                "counter": counter,
                "maxAttempts": max_attempts,
                "startTime": start_time,
                "error": {
                    "Error": "JobCheckError",
                    "Cause": f"Failed to check job status after {counter + 1} attempts. Error: {str(e)}"
                }
            }
        }

def handle_pipeline_end(event):
    """
    Handler for pipeline end with comprehensive error handling and cleanup
    Performs final cleanup and notifies external processes if needed
    """
    logger.info("Starting PIPELINE_END operation")

    try:
        # Validate event structure with enhanced validation
        if not isinstance(event, dict):
            error_msg = "PIPELINE_END: Event must be a dictionary"
            logger.error(error_msg)
            return create_error_response(400, "PIPELINE_END", error_msg)

        # Log pipeline end context
        job_name = event.get('jobName', 'unknown')
        k8s_job_name = event.get('k8sJobName', 'unknown')
        has_error = "error" in event
        external_token_present = bool(event.get('externalSfnTaskToken'))

        logger.info(f"Pipeline end context - Job: {job_name}, K8s Job: {k8s_job_name}, Has Error: {has_error}, External Token: {external_token_present}")

        # Handle external task token notification with enhanced error handling
        external_sfn_task_token = event.get('externalSfnTaskToken')
        notification_success = True
        notification_error = None

        if external_sfn_task_token and external_sfn_task_token.strip():
            try:
                logger.info(f"Processing external task token: {external_sfn_task_token[:20]}...")

                # Validate token format (basic validation)
                if len(external_sfn_task_token) < 10:
                    logger.warning(f"External task token appears to be invalid (too short): {len(external_sfn_task_token)} characters")

                # Determine notification type and prepare data
                if has_error:
                    error_info = event.get("error", {})
                    error_type = error_info.get("Error", "PipelineExecutionFailed")
                    error_cause = error_info.get("Cause", "Unknown error in pipeline execution")

                    logger.info(f"Sending task failure notification - Error: {error_type}")

                    # Add retry logic for task failure notification
                    max_retries = 3
                    for attempt in range(max_retries):
                        try:
                            sfn.send_task_failure(
                                taskToken=external_sfn_task_token,
                                error=error_type,
                                cause=error_cause
                            )
                            logger.info(f"Task failure notification sent successfully on attempt {attempt + 1}")
                            break
                        except Exception as e:
                            if attempt == max_retries - 1:
                                raise e
                            logger.warning(f"Task failure notification attempt {attempt + 1} failed: {str(e)}")
                            time.sleep(2 ** attempt)  # Exponential backoff
                else:
                    # Prepare success output with pipeline metadata
                    success_output = {
                        'status': 'Success',
                        'jobName': job_name,
                        'k8sJobName': k8s_job_name,
                        'completedAt': time.time()
                    }

                    logger.info(f"Sending task success notification")

                    # Add retry logic for task success notification
                    max_retries = 3
                    for attempt in range(max_retries):
                        try:
                            sfn.send_task_success(
                                taskToken=external_sfn_task_token,
                                output=json.dumps(success_output)
                            )
                            logger.info(f"Task success notification sent successfully on attempt {attempt + 1}")
                            break
                        except Exception as e:
                            if attempt == max_retries - 1:
                                raise e
                            logger.warning(f"Task success notification attempt {attempt + 1} failed: {str(e)}")
                            time.sleep(2 ** attempt)  # Exponential backoff

            except Exception as e:
                notification_success = False
                notification_error = str(e)
                logger.error(f"Failed to send external task notification after retries: {str(e)}")
                logger.error(f"Token (first 20 chars): {external_sfn_task_token[:20] if external_sfn_task_token else 'None'}")

                # Don't fail the entire pipeline end operation due to notification failure
                # This is important for cleanup to still proceed
        else:
            logger.info("No external task token provided, skipping external notification")

        # Enhanced Kubernetes job cleanup with comprehensive error handling
        cleanup_success = True
        cleanup_message = "No cleanup needed"

        if k8s_job_name and k8s_job_name != 'unknown':
            try:
                logger.info(f"Starting cleanup for Kubernetes job: {k8s_job_name}")

                # Try to import cleanup function with fallback
                try:
                    from kubernetes_utils import cleanup_completed_job
                    cleanup_function_available = True
                except ImportError as ie:
                    logger.warning(f"Enhanced cleanup function not available: {ie}")
                    cleanup_function_available = False

                if cleanup_function_available:
                    try:
                        # Use enhanced cleanup with force=True since we're in pipeline end
                        success, message = cleanup_completed_job(k8s_job_name, namespace=NAMESPACE, force=True)
                        cleanup_success = success
                        cleanup_message = message

                        if success:
                            logger.info(f"Successfully cleaned up job {k8s_job_name}: {message}")
                        else:
                            logger.warning(f"Job cleanup completed with issues: {message}")

                    except Exception as cleanup_error:
                        cleanup_success = False
                        cleanup_message = f"Enhanced cleanup failed: {str(cleanup_error)}"
                        logger.error(cleanup_message)

                        # Fallback to basic cleanup
                        try:
                            logger.info("Attempting fallback cleanup using basic delete_job")
                            from kubernetes_utils import delete_job
                            delete_job(k8s_job_name, namespace=NAMESPACE)
                            cleanup_message = "Fallback cleanup completed"
                            logger.info(f"Fallback cleanup successful for job {k8s_job_name}")
                        except Exception as fallback_error:
                            cleanup_message = f"All cleanup methods failed: {str(fallback_error)}"
                            logger.error(cleanup_message)
                else:
                    # Basic cleanup without enhanced function
                    try:
                        from kubernetes_utils import delete_job
                        delete_job(k8s_job_name, namespace=NAMESPACE)
                        cleanup_message = "Basic cleanup completed"
                        logger.info(f"Basic cleanup successful for job {k8s_job_name}")
                    except Exception as basic_error:
                        cleanup_success = False
                        cleanup_message = f"Basic cleanup failed: {str(basic_error)}"
                        logger.error(cleanup_message)

            except Exception as e:
                cleanup_success = False
                cleanup_message = f"Cleanup operation failed: {str(e)}"
                logger.exception(f"Critical error during job cleanup: {str(e)}")
        else:
            logger.info("No Kubernetes job name provided, skipping job cleanup")

        # Prepare comprehensive response
        pipeline_status = "FAILED" if has_error else "COMPLETED"

        response_body = {
            "status": pipeline_status,
            "jobName": job_name,
            "k8sJobName": k8s_job_name,
            "cleanup": {
                "success": cleanup_success,
                "message": cleanup_message
            },
            "notification": {
                "success": notification_success,
                "error": notification_error
            },
            "completedAt": time.time()
        }

        if has_error:
            response_body["message"] = "Pipeline failed"
            response_body["error"] = event.get("error")
            logger.error(f"Pipeline completed with error: {event.get('error')}")
        else:
            response_body["message"] = "Pipeline completed successfully"
            logger.info("Pipeline completed successfully")

        # Log final status
        logger.info(f"Pipeline end operation completed - Status: {pipeline_status}")
        logger.info(f"Cleanup success: {cleanup_success}, Notification success: {notification_success}")

        return {
            'statusCode': 200,
            'body': response_body
        }

    except Exception as e:
        # Top-level error handler for pipeline end
        logger.exception(f"Critical error in handle_pipeline_end: {str(e)}")

        # Even if pipeline end fails, try to send failure notification if token exists
        try:
            external_sfn_task_token = event.get('externalSfnTaskToken') if isinstance(event, dict) else None
            if external_sfn_task_token and external_sfn_task_token.strip():
                logger.info("Attempting emergency failure notification")
                sfn.send_task_failure(
                    taskToken=external_sfn_task_token,
                    error="PipelineEndError",
                    cause=f"Critical error in pipeline end handler: {str(e)}"
                )
                logger.info("Emergency failure notification sent")
        except Exception as notification_error:
            logger.error(f"Emergency notification also failed: {str(notification_error)}")

        return {
            'statusCode': 500,
            'body': {
                "status": "FAILED",
                "message": "Critical error in pipeline end operation",
                "error": {
                    "Error": "PipelineEndCriticalError",
                    "Cause": str(e)
                },
                "errorType": type(e).__name__
            }
        }
