# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Lambda to execute the Coordinate Transform pipeline from VAMS workflows.
Note: Lambda function name must start with "vams" to allow invoke permissioning.
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

logger = safeLogger(service="VamsExecuteCoordinateTransformPipeline")
lambda_client = boto3.client('lambda', config=retry_config)
s3_client = boto3.client('s3', config=retry_config)
sfn_client = boto3.client('stepfunctions', region_name=os.environ.get('AWS_REGION', 'us-east-1'), config=retry_config)
OPEN_PIPELINE_FUNCTION_NAME = os.environ["OPEN_PIPELINE_FUNCTION_NAME"]


def execute_pipeline(input_s3_asset_file_path, output_s3_asset_files_path,
                     output_s3_asset_preview_path, output_s3_asset_metadata_path,
                     inputOutput_s3_assetAuxiliary_files_path,
                     asset_id, database_id,
                     input_metadata_s3_location, input_configuration_s3_location,
                     external_task_token, executing_userName, executing_requestContext,
                     orchestration_event_prefix=""):

    messagePayload = {
        "inputS3AssetFilePath": input_s3_asset_file_path,
        "outputS3AssetFilesPath": output_s3_asset_files_path,
        "outputS3AssetPreviewPath": output_s3_asset_preview_path,
        "outputS3AssetMetadataPath": output_s3_asset_metadata_path,
        "inputOutputS3AssetAuxiliaryFilesPath": inputOutput_s3_assetAuxiliary_files_path,
        "assetId": asset_id,
        "databaseId": database_id,
        "inputMetadataS3Location": input_metadata_s3_location,
        "inputConfigurationS3Location": input_configuration_s3_location,
        "sfnExternalTaskToken": external_task_token,
        "executingUserName": executing_userName,
        "executingRequestContext": executing_requestContext,
        "orchestrationEventPrefix": orchestration_event_prefix,
    }

    logger.info("Invoking Open Pipeline Lambda")
    lambda_response = lambda_client.invoke(
        FunctionName=OPEN_PIPELINE_FUNCTION_NAME,
        InvocationType='RequestResponse',
        Payload=json.dumps(messagePayload).encode('utf-8')
    )
    logger.info(f"Lambda response: {lambda_response}")

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
            error="CoordinateTransformPipelineError",
            cause=str(error)[:256]
        )
        logger.info("Sent task failure callback to Step Functions")
    except Exception as e:
        logger.error(f"Failed to send task failure callback: {e}")


def lambda_handler(event, context):
    logger.info(event)

    external_task_token = None

    try:
        if not event.get('body'):
            raise ValueError('Request body is required')

        if isinstance(event['body'], str):
            data = json.loads(event['body'])
        else:
            data = event['body']

        if 'TaskToken' in data:
            external_task_token = data['TaskToken']
        else:
            raise Exception(
                "VAMS Workflow TaskToken not found in pipeline input. "
                "Register this pipeline as needing a task token callback."
            )

        executing_userName = data.get('executingUserName', '')
        executing_requestContext = data.get('executingRequestContext', '')

        # Resolve input/output locations from the workflow manifest, falling back to payload path fields
        resolved = manifestHelper.resolve_pipeline_inputs(data, s3_client)
        # Single input file per execution today (SFN/manifest layer is multi-file-ready).
        manifestHelper.enforce_single_input_file(resolved)
        logger.info(f"Resolved pipeline inputs (manifestUsed={resolved['manifestUsed']}): {resolved}")

        execute_pipeline(
            resolved['inputS3AssetFilePath'],
            resolved['outputS3AssetFilesPath'],
            resolved['outputS3AssetPreviewPath'],
            resolved['outputS3AssetMetadataPath'],
            resolved['inputOutputS3AssetAuxiliaryFilesPath'],
            resolved['assetId'],
            resolved['databaseId'],
            resolved['inputMetadataS3Location'],
            resolved['inputConfigurationS3Location'],
            external_task_token,
            executing_userName,
            executing_requestContext,
            resolved['orchestrationEventPrefix'],
        )

        return {'statusCode': 200, 'body': 'Success'}

    except Exception as e:
        logger.exception(e)
        abort_external_workflow(e, external_task_token)
        return {
            'statusCode': 500,
            'body': json.dumps({"message": "Internal Server Error"})
        }
