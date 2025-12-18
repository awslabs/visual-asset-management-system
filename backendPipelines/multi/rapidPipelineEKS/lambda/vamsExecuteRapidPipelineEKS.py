#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""
Lambda Function to Call from within VAMS Pipeline and Workflows for Manual EKS Pipeline Execution
Note: Lambda function name must start with "vams" to allow invoke permissioning from vams.
"""
import os
import boto3
import json
import time
from customLogging.logger import safeLogger

# Get the open pipeline function name from environment variable
# This will be set during deployment to point to the appropriate function
OPEN_PIPELINE_FUNCTION_NAME_EKS = os.environ.get("OPEN_PIPELINE_FUNCTION_NAME_EKS")

# Initialize logger and AWS clients
logger = safeLogger(service="VamsExecuteRapidPipelineEKS")
lambda_client = boto3.client('lambda')
region = os.environ.get("AWS_REGION", "us-west-2")

def validate_event_parameters(event):
    """
    Comprehensive validation of event parameters for EKS pipeline execution.

    Args:
        event: The Lambda event object

    Returns:
        tuple: (is_valid, error_message, validated_data)
    """
    logger.info("Starting event parameter validation")

    # Required parameters for EKS pipeline
    required_params = [
        'inputS3AssetFilePath',
        'outputS3AssetFilesPath',
        'outputS3AssetPreviewPath',
        'outputS3AssetMetadataPath',
        'inputOutputS3AssetAuxiliaryFilesPath'
    ]

    # Optional parameters with defaults
    optional_params = {
        'inputMetadata': '',
        'inputParameters': '',
        'outputType': '',
        'executingUserName': '',
        'executingRequestContext': ''
    }

    try:
        # Handle different event structures
        if event is None:
            return False, "Event is None", None

        # Parse the request body if it exists
        if 'body' in event:
            if isinstance(event.get('body'), str):
                try:
                    data = json.loads(event['body'])
                except json.JSONDecodeError as e:
                    return False, f"Invalid JSON in event body: {str(e)}", None
            else:
                data = event['body']
        else:
            data = event

        if data is None:
            return False, "Event data is None", None

        if not isinstance(data, dict):
            return False, f"Event data must be a dictionary, got {type(data)}", None

        # Check for empty event
        if not data:
            return False, "Event data is empty", None

        # Validate required parameters
        missing_params = []
        for param in required_params:
            if param not in data or not data[param]:
                missing_params.append(param)

        if missing_params:
            return False, f"Missing required parameters: {', '.join(missing_params)}", None

        # Validate S3 URI format for required S3 paths
        s3_params = [
            'inputS3AssetFilePath',
            'outputS3AssetFilesPath',
            'outputS3AssetPreviewPath',
            'outputS3AssetMetadataPath',
            'inputOutputS3AssetAuxiliaryFilesPath'
        ]

        invalid_s3_uris = []
        for param in s3_params:
            if param in data and data[param]:
                if not data[param].startswith('s3://'):
                    invalid_s3_uris.append(param)
                elif len(data[param].split('/')) < 4:  # s3://bucket/key minimum
                    invalid_s3_uris.append(f"{param} (invalid format)")

        if invalid_s3_uris:
            return False, f"Invalid S3 URI format for: {', '.join(invalid_s3_uris)}", None

        # Check for TaskToken (required for VAMS workflow integration)
        if 'TaskToken' not in data or not data['TaskToken']:
            return False, "VAMS Workflow TaskToken not found in pipeline input. Make sure to register this pipeline in VAMS as needing a task token callback.", None

        # Validate input file extension if provided
        input_file_path = data.get('inputS3AssetFilePath', '')
        if input_file_path:
            allowed_extensions = ['.glb', '.gltf', '.fbx', '.obj', '.stl', '.ply', '.usd', '.usdz', '.dae', '.abc']
            file_extension = os.path.splitext(input_file_path)[1].lower()
            if file_extension and file_extension not in allowed_extensions:
                return False, f"Unsupported file extension '{file_extension}'. Supported extensions: {', '.join(allowed_extensions)}", None

        # Validate JSON format for inputMetadata and inputParameters if provided
        for json_param in ['inputMetadata', 'inputParameters']:
            if json_param in data and data[json_param]:
                try:
                    if isinstance(data[json_param], str):
                        json.loads(data[json_param])
                except json.JSONDecodeError:
                    return False, f"Invalid JSON format for {json_param}", None

        # Create validated data with defaults for optional parameters
        validated_data = {}
        for param in required_params:
            validated_data[param] = data[param]

        for param, default_value in optional_params.items():
            validated_data[param] = data.get(param, default_value)

        # Add TaskToken to validated data
        validated_data['TaskToken'] = data['TaskToken']

        logger.info("Event parameter validation successful")
        return True, None, validated_data

    except Exception as e:
        logger.exception(f"Unexpected error during parameter validation: {str(e)}")
        return False, f"Parameter validation error: {str(e)}", None

def execute_pipeline(input_s3_asset_file_path, output_s3_asset_files_path, output_s3_asset_preview_path, output_s3_asset_metadata_path,
                    inputOutput_s3_assetAuxiliary_files_path, input_metadata, input_parameters, external_task_token,
                    executing_userName, executing_requestContext, output_file_type):
    """
    Executes the EKS pipeline by invoking the open pipeline Lambda function with comprehensive error handling
    """
    try:
        # Validate that we have the required function name
        if not OPEN_PIPELINE_FUNCTION_NAME_EKS:
            error_msg = "OPEN_PIPELINE_FUNCTION_NAME_EKS environment variable is not set"
            logger.error(f"Configuration error: {error_msg}")
            raise ValueError(error_msg)

        # Validate function name format
        if not isinstance(OPEN_PIPELINE_FUNCTION_NAME_EKS, str) or not OPEN_PIPELINE_FUNCTION_NAME_EKS.strip():
            error_msg = f"Invalid OPEN_PIPELINE_FUNCTION_NAME_EKS value: {OPEN_PIPELINE_FUNCTION_NAME_EKS}"
            logger.error(f"Configuration error: {error_msg}")
            raise ValueError(error_msg)

        # Create the object message to be sent with validation
        messagePayload = {
            "inputS3AssetFilePath": input_s3_asset_file_path,
            "outputS3AssetFilesPath": output_s3_asset_files_path,
            "outputS3AssetPreviewPath": output_s3_asset_preview_path,
            "outputS3AssetMetadataPath": output_s3_asset_metadata_path,
            "inputOutputS3AssetAuxiliaryFilesPath": inputOutput_s3_assetAuxiliary_files_path,
            "inputMetadata": input_metadata,
            "inputParameters": input_parameters,
            "sfnExternalTaskToken": external_task_token,
            "executingUserName": executing_userName,
            "executingRequestContext": executing_requestContext,
            "outputFileType": output_file_type
        }

        # Log payload size for debugging
        payload_size = len(json.dumps(messagePayload).encode('utf-8'))
        logger.info(f"Payload size: {payload_size} bytes")

        # Check payload size limit (6MB for synchronous invocation)
        if payload_size > 6 * 1024 * 1024:
            error_msg = f"Payload size ({payload_size} bytes) exceeds Lambda synchronous invocation limit (6MB)"
            logger.error(error_msg)
            raise ValueError(error_msg)

        # Invoke the pipeline open pipeline lambda with retry mechanism
        logger.info(f"Invoking EKS Asset Pipeline Lambda: {OPEN_PIPELINE_FUNCTION_NAME_EKS}")
        logger.info(f"Payload keys: {list(messagePayload.keys())}")

        # Use retries for reliability with exponential backoff
        max_retries = 3
        retry_count = 0
        last_error = None
        base_wait_time = 1

        while retry_count < max_retries:
            try:
                # Add timeout and error handling context
                logger.info(f"Attempt {retry_count + 1}/{max_retries} - Invoking Lambda function")

                lambda_response = lambda_client.invoke(
                    FunctionName=OPEN_PIPELINE_FUNCTION_NAME_EKS,
                    InvocationType='RequestResponse',
                    Payload=json.dumps(messagePayload).encode('utf-8')
                )

                # Enhanced response validation
                status_code = lambda_response.get('StatusCode')
                logger.info(f"Lambda invocation status code: {status_code}")

                # Check for Lambda invocation errors
                if 'FunctionError' in lambda_response:
                    payload_str = lambda_response['Payload'].read().decode('utf-8')
                    error_type = lambda_response.get('FunctionError', 'Unknown')

                    logger.error(f"Lambda function error (type: {error_type}): {payload_str}")

                    # Try to parse error details
                    try:
                        error_details = json.loads(payload_str)
                        if isinstance(error_details, dict):
                            error_message = error_details.get('errorMessage', payload_str)
                            error_type_detail = error_details.get('errorType', error_type)
                            stack_trace = error_details.get('stackTrace', [])

                            logger.error(f"Error type: {error_type_detail}")
                            logger.error(f"Error message: {error_message}")
                            if stack_trace:
                                logger.error(f"Stack trace: {stack_trace}")

                            raise Exception(f"Lambda function error ({error_type_detail}): {error_message}")
                        else:
                            raise Exception(f"Lambda function error ({error_type}): {payload_str}")
                    except json.JSONDecodeError:
                        raise Exception(f"Lambda function error ({error_type}): {payload_str}")

                # Validate successful response
                if status_code != 200:
                    error_msg = f"Lambda invocation returned non-200 status code: {status_code}"
                    logger.error(error_msg)
                    raise Exception(error_msg)

                # Read and validate response payload
                try:
                    response_payload = lambda_response['Payload'].read().decode('utf-8')
                    if response_payload:
                        response_data = json.loads(response_payload)
                        logger.info(f"Lambda response received: {response_data}")

                        # Check if response indicates an error
                        if isinstance(response_data, dict):
                            response_status_code = response_data.get('statusCode')
                            if response_status_code and response_status_code >= 400:
                                error_body = response_data.get('body', {})
                                error_message = error_body.get('message', 'Unknown error') if isinstance(error_body, dict) else str(error_body)
                                logger.error(f"Lambda function returned error status {response_status_code}: {error_message}")
                                raise Exception(f"Pipeline execution failed with status {response_status_code}: {error_message}")
                    else:
                        logger.warning("Lambda response payload is empty")

                except json.JSONDecodeError as e:
                    logger.warning(f"Could not parse Lambda response as JSON: {e}")
                    logger.info(f"Raw response: {response_payload}")

                logger.info("Invoke Open Pipeline Lambda Successfully.")
                return

            except Exception as e:
                retry_count += 1
                last_error = e

                # Log detailed error information
                logger.error(f"Lambda invocation attempt {retry_count} failed: {str(e)}")
                logger.error(f"Error type: {type(e).__name__}")

                # Add context information
                context_info = {
                    "function_name": OPEN_PIPELINE_FUNCTION_NAME_EKS,
                    "attempt": retry_count,
                    "max_retries": max_retries,
                    "payload_size": payload_size,
                    "input_file": input_s3_asset_file_path,
                    "region": region
                }
                logger.error(f"Error context: {context_info}")

                if retry_count >= max_retries:
                    logger.error(f"All {max_retries} attempts failed. Last error: {str(e)}")
                    break

                # Exponential backoff with jitter
                import time
                import random
                wait_time = (base_wait_time * (2 ** (retry_count - 1))) + random.uniform(0, 1)
                logger.warning(f"Retry {retry_count}/{max_retries} after error. Waiting {wait_time:.2f}s before next attempt")
                time.sleep(wait_time)

        # If we've reached here, all retries failed
        if last_error:
            error_msg = f"Failed to invoke EKS Pipeline Lambda after {max_retries} attempts. Last error: {str(last_error)}"
            logger.error(error_msg)
            raise Exception(error_msg)
        else:
            error_msg = f"Failed to invoke EKS Pipeline Lambda after {max_retries} attempts with no recorded error"
            logger.error(error_msg)
            raise Exception(error_msg)

    except Exception as e:
        # Top-level error handling with context preservation
        logger.exception(f"Critical error in execute_pipeline: {str(e)}")

        # Add execution context to error
        execution_context = {
            "function": "execute_pipeline",
            "input_file": input_s3_asset_file_path,
            "output_path": output_s3_asset_files_path,
            "external_token": bool(external_task_token),
            "target_function": OPEN_PIPELINE_FUNCTION_NAME_EKS
        }
        logger.error(f"Execution context: {execution_context}")

        # Re-raise with enhanced error message
        raise Exception(f"Pipeline execution failed: {str(e)}. Context: {execution_context}")

def lambda_handler(event, context):
    """
    Main Lambda handler that processes VAMS pipeline execution requests with comprehensive error handling
    """
    # Initialize execution context for error tracking
    execution_id = context.aws_request_id if context else "unknown"
    start_time = time.time()

    logger.info(f"Lambda execution started - Request ID: {execution_id}")
    logger.info(f"Function name: {context.function_name if context else 'unknown'}")
    logger.info(f"Function version: {context.function_version if context else 'unknown'}")
    logger.info(f"Remaining time: {context.get_remaining_time_in_millis() if context else 'unknown'}ms")

    # Log event with size information
    try:
        event_str = json.dumps(event) if event else "null"
        event_size = len(event_str.encode('utf-8'))
        logger.info(f"Event size: {event_size} bytes")

        # Log event structure (without sensitive data)
        if isinstance(event, dict):
            event_keys = list(event.keys())
            logger.info(f"Event keys: {event_keys}")
        else:
            logger.info(f"Event type: {type(event)}")

        # Only log full event if it's small enough
        if event_size < 1000:
            logger.info(f"Received event: {event}")
        else:
            logger.info("Event too large to log in full")

    except Exception as log_error:
        logger.warning(f"Could not log event details: {log_error}")

    try:
        # Validate Lambda context
        if not context:
            logger.warning("Lambda context is None - running in test environment?")
        else:
            # Check remaining execution time
            remaining_time = context.get_remaining_time_in_millis()
            if remaining_time < 30000:  # Less than 30 seconds
                logger.warning(f"Low remaining execution time: {remaining_time}ms")

        # Validate event parameters with enhanced error context
        logger.info("Starting event parameter validation")
        is_valid, error_message, validated_data = validate_event_parameters(event)

        if not is_valid:
            logger.error(f"Event validation failed: {error_message}")

            # Enhanced error response with debugging information
            error_response = {
                'statusCode': 400,
                'body': json.dumps({
                    "message": "Bad Request - Invalid Parameters",
                    "error": error_message,
                    "details": "Please ensure all required parameters are provided with valid values",
                    "requestId": execution_id,
                    "timestamp": time.time(),
                    "function": context.function_name if context else "unknown"
                })
            }

            logger.error(f"Returning error response: {error_response}")
            return error_response

        logger.info("Event validation successful, proceeding with pipeline execution")

        # Extract validated parameters with additional validation
        try:
            external_task_token = validated_data['TaskToken']
            input_parameters = validated_data['inputParameters']
            input_metadata = validated_data['inputMetadata']
            executing_userName = validated_data['executingUserName']
            executing_requestContext = validated_data['executingRequestContext']
            output_file_type = validated_data['outputType']

            # Log parameter extraction success
            logger.info("Successfully extracted all validated parameters")
            logger.info(f"External task token present: {bool(external_task_token)}")
            logger.info(f"Input parameters present: {bool(input_parameters)}")
            logger.info(f"Input metadata present: {bool(input_metadata)}")
            logger.info(f"Output file type: {output_file_type}")

        except KeyError as e:
            error_msg = f"Missing expected parameter in validated data: {e}"
            logger.error(error_msg)
            return {
                'statusCode': 500,
                'body': json.dumps({
                    "message": "Internal Server Error - Parameter Extraction Failed",
                    "error": error_msg,
                    "requestId": execution_id
                })
            }

        # Check remaining time before starting pipeline execution
        if context:
            remaining_time = context.get_remaining_time_in_millis()
            if remaining_time < 60000:  # Less than 1 minute
                logger.warning(f"Starting pipeline execution with only {remaining_time}ms remaining")

        # Starts execution of pipeline with comprehensive error handling
        logger.info("Starting pipeline execution")
        try:
            execute_pipeline(
                validated_data['inputS3AssetFilePath'],
                validated_data['outputS3AssetFilesPath'],
                validated_data['outputS3AssetPreviewPath'],
                validated_data['outputS3AssetMetadataPath'],
                validated_data['inputOutputS3AssetAuxiliaryFilesPath'],
                input_metadata,
                input_parameters,
                external_task_token,
                executing_userName,
                executing_requestContext,
                output_file_type
            )

            # Calculate execution time
            execution_time = time.time() - start_time
            logger.info(f"Pipeline execution completed successfully in {execution_time:.2f} seconds")

            # Success response with execution metadata
            success_response = {
                'statusCode': 200,
                'body': json.dumps({
                    'message': 'Pipeline execution started successfully',
                    'status': 'Success',
                    'requestId': execution_id,
                    'executionTime': execution_time,
                    'timestamp': time.time()
                })
            }

            logger.info(f"Returning success response: {success_response}")
            return success_response

        except ValueError as ve:
            # Configuration or validation errors
            logger.error(f"Configuration/validation error during pipeline execution: {str(ve)}")
            return {
                'statusCode': 400,
                'body': json.dumps({
                    "message": "Bad Request - Configuration Error",
                    "error": str(ve),
                    "details": "There is a configuration issue preventing pipeline execution",
                    "requestId": execution_id,
                    "timestamp": time.time()
                })
            }

        except Exception as pe:
            # Pipeline execution errors
            logger.exception(f"Pipeline execution error: {str(pe)}")
            return {
                'statusCode': 500,
                'body': json.dumps({
                    "message": "Internal Server Error - Pipeline Execution Failed",
                    "error": str(pe),
                    "details": "An error occurred while starting the pipeline execution",
                    "requestId": execution_id,
                    "timestamp": time.time()
                })
            }

    except Exception as e:
        # Top-level exception handler with comprehensive logging
        execution_time = time.time() - start_time

        logger.exception(f"Unexpected error in lambda handler: {str(e)}")
        logger.error(f"Error type: {type(e).__name__}")
        logger.error(f"Execution time before error: {execution_time:.2f} seconds")

        # Log system information for debugging
        try:
            import sys
            import os

            system_info = {
                "python_version": sys.version,
                "lambda_runtime": os.environ.get('AWS_EXECUTION_ENV', 'unknown'),
                "memory_limit": os.environ.get('AWS_LAMBDA_FUNCTION_MEMORY_SIZE', 'unknown'),
                "function_name": os.environ.get('AWS_LAMBDA_FUNCTION_NAME', 'unknown'),
                "function_version": os.environ.get('AWS_LAMBDA_FUNCTION_VERSION', 'unknown'),
                "region": os.environ.get('AWS_REGION', 'unknown')
            }
            logger.error(f"System information: {system_info}")

        except Exception as sys_error:
            logger.warning(f"Could not gather system information: {sys_error}")

        # Enhanced error response with debugging information
        error_message = str(e)
        error_response = {
            'statusCode': 500,
            'body': json.dumps({
                "message": "Internal Server Error",
                "error": error_message,
                "errorType": type(e).__name__,
                "details": "An unexpected error occurred during pipeline execution",
                "requestId": execution_id,
                "executionTime": execution_time,
                "timestamp": time.time(),
                "function": context.function_name if context else "unknown"
            })
        }

        logger.error(f"Returning error response: {error_response}")
        return error_response
