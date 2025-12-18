#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
import datetime
import time
from customLogging.logger import safeLogger

logger = safeLogger(service="OpenPipelineEKS")

sfn = boto3.client(
    'stepfunctions',
    region_name=os.environ["AWS_REGION"]
)

# State Machine ARN for starting pipeline execution
STATE_MACHINE_ARN = os.environ["STATE_MACHINE_ARN"]
ALLOWED_INPUT_FILEEXTENSIONS = os.environ["ALLOWED_INPUT_FILEEXTENSIONS"]

external_sfn_task_token = ""

def abort_external_workflow(error, context_info=None):
    """
    Abort external workflow with enhanced error reporting and retry logic
    """
    try:
        if external_sfn_task_token and external_sfn_task_token.strip():
            logger.info(f"Aborting external workflow with task token: {external_sfn_task_token[:20]}...")

            # Enhanced error message with context
            error_message = f'Pipeline Failure: {error}'
            cause_message = 'See AWS CloudWatch logs for detailed error cause.'

            if context_info:
                cause_message += f' Context: {context_info}'

            # Add retry logic for task failure notification
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    sfn.send_task_failure(
                        taskToken=external_sfn_task_token,
                        error=error_message,
                        cause=cause_message
                    )
                    logger.info(f"Successfully sent task failure notification on attempt {attempt + 1}")
                    break

                except Exception as e:
                    logger.warning(f"Failed to send task failure notification (attempt {attempt + 1}/{max_retries}): {str(e)}")
                    if attempt == max_retries - 1:
                        logger.error(f"Failed to notify external workflow after {max_retries} attempts: {str(e)}")
                    else:
                        import time
                        time.sleep(2 ** attempt)  # Exponential backoff
        else:
            logger.info("No external task token provided, skipping workflow abort notification")

    except Exception as e:
        logger.exception(f"Error in abort_external_workflow: {str(e)}")
        # Don't re-raise as this is a cleanup function

def validate_required_parameters(event, required_params):
    """
    Validate that all required parameters are present in the event
    """
    missing_params = []
    invalid_params = []

    for param in required_params:
        if param not in event:
            missing_params.append(param)
        elif event[param] is None:
            invalid_params.append(f"{param} (null)")
        elif isinstance(event[param], str) and not event[param].strip():
            invalid_params.append(f"{param} (empty)")

    if missing_params or invalid_params:
        error_details = []
        if missing_params:
            error_details.append(f"Missing: {', '.join(missing_params)}")
        if invalid_params:
            error_details.append(f"Invalid: {', '.join(invalid_params)}")
        return False, "; ".join(error_details)

    return True, None

def validate_s3_uri(uri, param_name):
    """
    Validate S3 URI format and structure
    """
    if not uri or not isinstance(uri, str):
        return False, f"{param_name}: S3 URI cannot be empty"

    if not uri.startswith('s3://'):
        return False, f"{param_name}: Must be a valid S3 URI starting with 's3://'"

    # Basic structure validation
    parts = uri.replace('s3://', '').split('/')
    if len(parts) < 2 or not parts[0]:
        return False, f"{param_name}: Invalid S3 URI format, must include bucket and key"

    # We now accept paths with trailing slashes - they'll be handled
    # appropriately when the path is used

    return True, None

def lambda_handler(event, context):
    """
    OpenPipelineEKS - Starts StepFunctions State Machine for EKS processing with comprehensive error handling
    """
    # Initialize execution context
    execution_id = context.aws_request_id if context else "unknown"
    start_time = datetime.datetime.now()

    logger.info(f"OpenPipelineEKS execution started - Request ID: {execution_id}")
    logger.info(f"Function name: {context.function_name if context else 'unknown'}")
    logger.info(f"Remaining time: {context.get_remaining_time_in_millis() if context else 'unknown'}ms")

    # Global variable for external task token (for abort_external_workflow)
    global external_sfn_task_token
    external_sfn_task_token = ""

    try:
        # Enhanced event logging
        try:
            event_str = json.dumps(event) if event else "null"
            event_size = len(event_str.encode('utf-8'))
            logger.info(f"Event size: {event_size} bytes")

            if isinstance(event, dict):
                logger.info(f"Event keys: {list(event.keys())}")

            # Log full event if small enough
            if event_size < 2000:
                logger.info(f"Event: {event}")
            else:
                logger.info("Event too large to log in full")

        except Exception as log_error:
            logger.warning(f"Could not log event details: {log_error}")

        # Validate event is not None or empty
        if not event:
            error_msg = "Event is null or empty"
            logger.error(error_msg)
            abort_external_workflow(error_msg, {"function": "openPipeline", "stage": "event_validation"})
            return {
                'statusCode': 400,
                'body': {
                    "message": error_msg,
                    "requestId": execution_id
                }
            }

        # Validate required parameters
        required_params = [
            'inputS3AssetFilePath',
            'outputS3AssetFilesPath',
            'outputS3AssetPreviewPath',
            'outputS3AssetMetadataPath',
            'inputOutputS3AssetAuxiliaryFilesPath',
            'outputFileType'
        ]

        is_valid, error_message = validate_required_parameters(event, required_params)
        if not is_valid:
            error_msg = f"Parameter validation failed: {error_message}"
            logger.error(error_msg)
            abort_external_workflow(error_msg, {"function": "openPipeline", "stage": "parameter_validation"})
            return {
                'statusCode': 400,
                'body': {
                    "message": "Bad Request - Missing or invalid parameters",
                    "error": error_message,
                    "requestId": execution_id
                }
            }

        # Extract and validate parameters with enhanced error handling
        try:
            # Get optional parameters with defaults
            input_Metadata = event.get('inputMetadata', '')
            input_Parameters = event.get('inputParameters', '')
            external_sfn_task_token = event.get('sfnExternalTaskToken', '')

            # Get required parameters
            input_s3_asset_files_uri = event['inputS3AssetFilePath']
            output_s3_asset_files_uri = event['outputS3AssetFilesPath']
            output_s3_asset_preview_uri = event['outputS3AssetPreviewPath']
            output_s3_asset_metadata_uri = event['outputS3AssetMetadataPath']
            inputOutput_s3_assetAuxiliary_files_uri = event['inputOutputS3AssetAuxiliaryFilesPath']
            output_file_type = event['outputFileType']

            logger.info(f"External task token present: {bool(external_sfn_task_token)}")
            logger.info(f"Input file: {input_s3_asset_files_uri}")
            logger.info(f"Output type: {output_file_type}")

        except KeyError as e:
            error_msg = f"Missing required parameter: {str(e)}"
            logger.error(error_msg)
            abort_external_workflow(error_msg, {"function": "openPipeline", "stage": "parameter_extraction"})
            return {
                'statusCode': 400,
                'body': {
                    "message": "Bad Request - Missing required parameter",
                    "error": error_msg,
                    "requestId": execution_id
                }
            }

        # Validate S3 URIs
        s3_uris_to_validate = [
            (input_s3_asset_files_uri, 'inputS3AssetFilePath'),
            (output_s3_asset_files_uri, 'outputS3AssetFilesPath'),
            (output_s3_asset_preview_uri, 'outputS3AssetPreviewPath'),
            (output_s3_asset_metadata_uri, 'outputS3AssetMetadataPath'),
            (inputOutput_s3_assetAuxiliary_files_uri, 'inputOutputS3AssetAuxiliaryFilesPath')
        ]

        for uri, param_name in s3_uris_to_validate:
            is_valid, error_message = validate_s3_uri(uri, param_name)
            if not is_valid:
                logger.error(error_message)
                abort_external_workflow(error_message, {"function": "openPipeline", "stage": "s3_validation"})
                return {
                    'statusCode': 400,
                    'body': {
                        "message": "Bad Request - Invalid S3 URI",
                        "error": error_message,
                        "requestId": execution_id
                    }
                }

        # Extract and validate file extension
        try:
            file_root, extension = os.path.splitext(input_s3_asset_files_uri)
            logger.info(f"Input file extension: {extension}")

            # Validate file extension
            if not extension or extension == '':
                error_msg = f"Input file has no extension: {input_s3_asset_files_uri}"
                logger.error(error_msg)
                abort_external_workflow(error_msg, {"function": "openPipeline", "stage": "extension_validation"})
                return {
                    'statusCode': 400,
                    'body': {
                        "message": "Bad Request - File has no extension",
                        "error": error_msg,
                        "requestId": execution_id
                    }
                }

            # Check allowed file extensions
            allowed_extensions = [ext.strip().lower() for ext in ALLOWED_INPUT_FILEEXTENSIONS.split(',')]
            if extension.lower() not in allowed_extensions:
                error_msg = f"Unsupported file extension '{extension}'. Allowed extensions: {', '.join(allowed_extensions)}"
                logger.error(error_msg)
                abort_external_workflow(error_msg, {"function": "openPipeline", "stage": "extension_validation"})
                return {
                    'statusCode': 400,
                    'body': {
                        "message": "Bad Request - Unsupported file type",
                        "error": error_msg,
                        "supportedExtensions": allowed_extensions,
                        "requestId": execution_id
                    }
                }

        except Exception as e:
            error_msg = f"Error processing file extension: {str(e)}"
            logger.exception(error_msg)
            abort_external_workflow(error_msg, {"function": "openPipeline", "stage": "extension_processing"})
            return {
                'statusCode': 500,
                'body': {
                    "message": "Internal Server Error - File processing failed",
                    "error": error_msg,
                    "requestId": execution_id
                }
            }

        # Generate job name with enhanced uniqueness
        try:
            timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]  # Include milliseconds
            job_name = f"PipelineJobEKS_{timestamp}"
            logger.info(f"Generated job name: {job_name}")

        except Exception as e:
            error_msg = f"Error generating job name: {str(e)}"
            logger.exception(error_msg)
            abort_external_workflow(error_msg, {"function": "openPipeline", "stage": "job_name_generation"})
            return {
                'statusCode': 500,
                'body': {
                    "message": "Internal Server Error - Job name generation failed",
                    "error": error_msg,
                    "requestId": execution_id
                }
            }

        # Validate environment variables
        try:
            if not STATE_MACHINE_ARN:
                error_msg = "STATE_MACHINE_ARN not configured"
                logger.error(error_msg)
                abort_external_workflow(error_msg, {"function": "openPipeline", "stage": "environment_validation"})
                return {
                    'statusCode': 500,
                    'body': {
                        "message": "Internal Server Error - Configuration missing",
                        "error": error_msg,
                        "requestId": execution_id
                    }
                }

            logger.info(f"Using State Machine ARN: {STATE_MACHINE_ARN}")

        except Exception as e:
            error_msg = f"Error validating environment: {str(e)}"
            logger.exception(error_msg)
            abort_external_workflow(error_msg, {"function": "openPipeline", "stage": "environment_validation"})
            return {
                'statusCode': 500,
                'body': {
                    "message": "Internal Server Error - Environment validation failed",
                    "error": error_msg,
                    "requestId": execution_id
                }
            }

        # Create StateMachine Execution Input with validation
        try:
            sfn_input = {
                "jobName": job_name,
                "inputS3AssetFilePath": input_s3_asset_files_uri,
                "outputS3AssetFilesPath": output_s3_asset_files_uri,
                "outputS3AssetPreviewPath": output_s3_asset_preview_uri,
                "outputS3AssetMetadataPath": output_s3_asset_metadata_uri,
                "inputOutputS3AssetAuxiliaryFilesPath": inputOutput_s3_assetAuxiliary_files_uri,
                "inputMetadata": input_Metadata,
                "inputParameters": input_Parameters,
                "externalSfnTaskToken": external_sfn_task_token,
                "outputFileType": output_file_type
            }

            # Validate SFN input size
            sfn_input_str = json.dumps(sfn_input)
            sfn_input_size = len(sfn_input_str.encode('utf-8'))
            logger.info(f"SFN input size: {sfn_input_size} bytes")

            # Step Functions has a 256KB limit for input
            if sfn_input_size > 256 * 1024:
                error_msg = f"SFN input size ({sfn_input_size} bytes) exceeds 256KB limit"
                logger.error(error_msg)
                abort_external_workflow(error_msg, {"function": "openPipeline", "stage": "sfn_input_validation"})
                return {
                    'statusCode': 400,
                    'body': {
                        "message": "Bad Request - Input too large",
                        "error": error_msg,
                        "requestId": execution_id
                    }
                }

        except Exception as e:
            error_msg = f"Error creating SFN input: {str(e)}"
            logger.exception(error_msg)
            abort_external_workflow(error_msg, {"function": "openPipeline", "stage": "sfn_input_creation"})
            return {
                'statusCode': 500,
                'body': {
                    "message": "Internal Server Error - SFN input creation failed",
                    "error": error_msg,
                    "requestId": execution_id
                }
            }

        # Start the Step Functions state machine with retry logic
        try:
            logger.info(f"Starting EKS SFN State Machine: {STATE_MACHINE_ARN}")
            logger.info(f"Job name: {job_name}")

            # Add retry logic for Step Functions start_execution
            max_retries = 3
            last_error = None

            for attempt in range(max_retries):
                try:
                    logger.info(f"SFN start attempt {attempt + 1}/{max_retries}")

                    sfn_response = sfn.start_execution(
                        stateMachineArn=STATE_MACHINE_ARN,
                        name=job_name,
                        input=sfn_input_str
                    )

                    logger.info(f"SFN execution started successfully: {sfn_response['executionArn']}")
                    break

                except Exception as e:
                    last_error = e
                    logger.warning(f"SFN start attempt {attempt + 1} failed: {str(e)}")

                    if attempt == max_retries - 1:
                        raise last_error

                    # Exponential backoff
                    import time
                    wait_time = 2 ** attempt
                    logger.info(f"Waiting {wait_time}s before retry")
                    time.sleep(wait_time)

            # Format response datetime for JSON serialization
            if 'startDate' in sfn_response:
                sfn_response["startDate"] = sfn_response["startDate"].strftime('%m-%d-%Y %H:%M:%S')

            # Calculate execution time
            execution_time = (datetime.datetime.now() - start_time).total_seconds()
            logger.info(f"OpenPipeline execution completed successfully in {execution_time:.2f} seconds")

            # Success response
            return {
                'statusCode': 200,
                'body': {
                    "message": "Starting Asset Processing State Machine with EKS",
                    "execution": sfn_response,
                    "jobName": job_name,
                    "requestId": execution_id,
                    "executionTime": execution_time
                }
            }

        except Exception as e:
            error_msg = f"Failed to start Step Functions execution: {str(e)}"
            logger.exception(error_msg)

            # Enhanced error context
            error_context = {
                "function": "openPipeline",
                "stage": "sfn_execution",
                "state_machine_arn": STATE_MACHINE_ARN,
                "job_name": job_name,
                "input_file": input_s3_asset_files_uri
            }

            abort_external_workflow(error_msg, error_context)

            return {
                'statusCode': 500,
                'body': {
                    "message": "Internal Server Error - Failed to start processing",
                    "error": error_msg,
                    "requestId": execution_id,
                    "context": error_context
                }
            }

    except Exception as e:
        # Top-level exception handler
        execution_time = (datetime.datetime.now() - start_time).total_seconds()

        logger.exception(f"Unexpected error in OpenPipelineEKS: {str(e)}")
        logger.error(f"Error type: {type(e).__name__}")
        logger.error(f"Execution time before error: {execution_time:.2f} seconds")

        # Enhanced error context
        error_context = {
            "function": "openPipeline",
            "stage": "top_level_handler",
            "execution_time": execution_time,
            "error_type": type(e).__name__
        }

        abort_external_workflow(f"Unexpected error: {str(e)}", error_context)

        return {
            'statusCode': 500,
            'body': {
                "message": "Internal Server Error",
                "error": str(e),
                "errorType": type(e).__name__,
                "requestId": execution_id,
                "executionTime": execution_time,
                "context": error_context
            }
        }
