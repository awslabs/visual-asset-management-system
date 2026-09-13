#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""
Lambda Function to Call from within VAMS Pipeline and Workflows for Manual Execution
Note: Lambda function name must start with "vams" to allow invoke permissioning from vams.
"""
import os
import boto3
import json
from customLogging.logger import safeLogger
import manifestHelper
from botocore.config import Config

# Adaptive retry with client-side rate limiting, per backendPipelines/CLAUDE.md. A pipeline lambda
# runs against throttling-prone services (Step Functions, Amazon S3, EventBridge) for the length of
# a job, so a bare client leaves it on botocore's default mode with no rate limiting and a sustained
# burst surfaces as a throttling error on the caller instead of being smoothed.
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

OPEN_PIPELINE_FUNCTION_NAME = os.environ["OPEN_PIPELINE_FUNCTION_NAME"]

logger = safeLogger(service="VamsExecuteSystemGenAiMetadataPipeline")
lambda_client = boto3.client('lambda', config=retry_config)
s3_client = boto3.client('s3', config=retry_config)
sfn_client = boto3.client('stepfunctions', region_name=os.environ.get('AWS_REGION', 'us-east-1'), config=retry_config)

TASK_FAILURE_ERROR = "SystemGenAiMetadataPipelineError"


def execute_pipeline(resolved, external_task_token, executing_userName, executing_requestContext,
                     workflow_execution_id):
    first_file = (resolved.get("inputFiles") or [{}])[0] or {}

    # Create the object message to be sent: every location the state machine reads plus the
    # identity of the one file this run describes (asset, database, asset-relative path, S3 version).
    messagePayload = {
        "inputS3AssetFilePath": resolved['inputS3AssetFilePath'],
        "outputS3AssetFilesPath": resolved['outputS3AssetFilesPath'],
        "outputS3AssetPreviewPath": resolved['outputS3AssetPreviewPath'],
        "outputS3AssetMetadataPath": resolved['outputS3AssetMetadataPath'],
        "outputS3AssetResultsPath": resolved['outputS3AssetResultsPath'],
        "inputOutputS3AssetAuxiliaryFilesPath": resolved['inputOutputS3AssetAuxiliaryFilesPath'],
        "inputMetadataS3Location": resolved['inputMetadataS3Location'],
        "inputConfigurationS3Location": resolved['inputConfigurationS3Location'],
        "sfnExternalTaskToken": external_task_token,
        "executingUserName": executing_userName,
        "executingRequestContext": executing_requestContext,
        "orchestrationEventPrefix": resolved['orchestrationEventPrefix'],
        "assetId": resolved['assetId'],
        "databaseId": resolved['databaseId'],
        "bucketId": first_file.get("bucketId", "") or "",
        "relativePath": first_file.get("relativePath", "") or "",
        "versionId": first_file.get("versionId", "") or "",
        "workflowExecutionId": workflow_execution_id or "",
    }

    # Invoke the pipeline construct pipeline lambda
    logger.info("Invoking Asset Lambda .........")
    lambda_response = lambda_client.invoke(FunctionName=OPEN_PIPELINE_FUNCTION_NAME,
                                        InvocationType='RequestResponse',
                                        Payload=json.dumps(messagePayload).encode('utf-8'))
    logger.info("lambda response")
    logger.info(lambda_response)
    logger.info("Invoke Open Pipeline Lambda Successfully.")

    if 'StatusCode' not in lambda_response or lambda_response['StatusCode'] != 200:
        message = lambda_response.get("body", {}).get("message", "")
        raise Exception("Invoke Open Pipeline Lambda Failed. " + message)

    # A handled invocation still returns StatusCode 200 when the invoked function raised: the
    # failure is reported via FunctionError. Without this check an unhandled error in
    # openPipeline reads as success here, so no task-token failure is ever sent and the
    # workflow's callback task blocks until taskTimeout.
    if lambda_response.get('FunctionError'):
        raise Exception(
            "Invoke Open Pipeline Lambda Failed: " + str(lambda_response.get('FunctionError')))


def abort_external_workflow(error, task_token):
    """Fail the VAMS workflow's waitForCallback task token so the pipeline task does not wait
    for the full taskTimeout when this lambda cannot start the pipeline."""
    if not task_token:
        return
    try:
        sfn_client.send_task_failure(
            taskToken=task_token,
            error=TASK_FAILURE_ERROR,
            cause=str(error)[:256]
        )
        logger.info("Sent task failure callback to Step Functions")
    except Exception as e:
        logger.error(f"Failed to send task failure callback: {e}")


def lambda_handler(event, context):
    logger.info(event)

    external_task_token = None

    try:
        response = {
            'statusCode': 200,
            'body': '',
            'headers': {
                'Content-Type': 'application/json'
            }
        }

        # Parse request body
        if not event.get('body'):
            message = 'Request body is required'
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            logger.error(response)
            return response

        if isinstance(event['body'], str):
            data = json.loads(event['body'])
        else:
            data = event['body']

        # Get external task token if passed
        if 'TaskToken' in data:
            external_task_token = data['TaskToken']
        else:
            raise Exception("VAMS Workflow TaskToken not found in pipeline input. Make sure to register this pipeline in VAMS as needing a task token callback.")

        executing_userName = data.get('executingUserName', '') or ''
        executing_requestContext = data.get('executingRequestContext', '') or ''
        workflow_execution_id = data.get('workflowExecutionId', '') or ''

        # Resolve input/output locations from the workflow manifest (fallback to payload fields)
        resolved = manifestHelper.resolve_pipeline_inputs(data, s3_client)
        # Single input file per execution today (SFN/manifest layer is multi-file-ready).
        manifestHelper.enforce_single_input_file(resolved)
        logger.info(f"Resolved pipeline inputs (manifestUsed={resolved['manifestUsed']}): {resolved}")

        if not resolved.get('inputFiles'):
            raise Exception("This pipeline describes one input file, but the workflow manifest supplied none.")

        # The results prefix is where a Bedrock failure is recorded (execution.status.json); a run
        # without one could never report FAILED, so it is rejected before any compute starts.
        if not resolved.get('outputS3AssetResultsPath'):
            raise Exception("The workflow manifest carries no results prefix; this pipeline cannot record its analysis summary or a failure status without one.")

        # The vector indexer resolves the file's bucket from the event's bucketId and drops an event
        # without one. A manifest built from an earlier step's outputs carries no bucketId, so the
        # run still analyses the file and writes its attributes and metadata; the embedding step
        # records SKIPPED instead of publishing an event nothing could index.
        if not (resolved['inputFiles'][0] or {}).get('bucketId'):
            logger.warning("The workflow manifest's input file carries no bucketId; the embedding step will be skipped for this run.")

        # Validate input is a specific file path, not a folder/whole asset
        input_path = resolved['inputS3AssetFilePath']
        if not input_path or input_path.endswith('/'):
            message = 'Input must be a specific file path, not a folder or asset'
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            logger.error(response)
            abort_external_workflow(message, external_task_token)
            return response

        # Starts execution of pipeline
        execute_pipeline(resolved, external_task_token, executing_userName, executing_requestContext,
                         workflow_execution_id)

        return {
            'statusCode': 200,
            'body': 'Success'
        }
    except Exception as e:
        logger.exception(e)
        abort_external_workflow(e, external_task_token)
        return {
            'statusCode': 500,
            'body': json.dumps({"message": "Internal Server Error"})
        }
