#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
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


logger = safeLogger(service="VamsExecutePreviewPcPotreeViewerPipeline")
lambda_client = boto3.client('lambda')
s3_client = boto3.client('s3')
sfn_client = boto3.client('stepfunctions', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
OPEN_PIPELINE_FUNCTION_NAME = os.environ["OPEN_PIPELINE_FUNCTION_NAME"]


def execute_pipeline(input_s3_asset_file_path, output_s3_asset_files_path, output_s3_asset_preview_path, output_s3_asset_metadata_path
                                        , inputOutput_s3_assetAuxiliary_files_path, input_metadata_s3_location, input_configuration_s3_location, external_task_token
                                        , executing_userName, executing_requestContext, orchestration_event_prefix=""):

    # Create the object message to be sent
    messagePayload = {
        "inputS3AssetFilePath": input_s3_asset_file_path,
        "outputS3AssetFilesPath": output_s3_asset_files_path,
        "outputS3AssetPreviewPath": output_s3_asset_preview_path,
        "outputS3AssetMetadataPath": output_s3_asset_metadata_path,
        "inputOutputS3AssetAuxiliaryFilesPath": inputOutput_s3_assetAuxiliary_files_path,
        "inputMetadataS3Location": input_metadata_s3_location,
        "inputConfigurationS3Location": input_configuration_s3_location,
        "sfnExternalTaskToken": external_task_token,
        "executingUserName": executing_userName,
        "executingRequestContext": executing_requestContext,
        "orchestrationEventPrefix": orchestration_event_prefix
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


def abort_external_workflow(error, task_token):
    """Fail the VAMS workflow's waitForCallback task token so the pipeline task does not wait
    for the full taskTimeout when this lambda cannot start the pipeline."""
    if not task_token:
        return
    try:
        sfn_client.send_task_failure(
            taskToken=task_token,
            error="PcPotreeViewerPipelineError",
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

        #Get Executing username
        if 'executingUserName' in data:
            executing_userName = data['executingUserName']
        else:
            executing_userName = ''

        #Get Executing requestContext
        if 'executingRequestContext' in data:
            executing_requestContext = data['executingRequestContext']
        else:
            executing_requestContext = ''

        logger.info(data)

        # Resolve input file + metadata/config S3 locations from the workflow manifest
        resolved = manifestHelper.resolve_pipeline_inputs(data, s3_client)
        # Single input file per execution today (SFN/manifest layer is multi-file-ready).
        manifestHelper.enforce_single_input_file(resolved)
        logger.info(f"Resolved pipeline inputs (manifestUsed={resolved['manifestUsed']}): {resolved}")

        # Potree writes its octree viewer data to the per-input-file aux preview location. The
        # viewer subfolder comes from the manifest's auxPreviewPipelineSuffix; until that is
        # sourced from the pipeline configuration it is empty, so fall back to the hardcoded
        # "PotreeViewer" subfolder here to keep the viewer path intact.
        inputOutputS3AssetAuxiliaryFilesPath = resolved['auxPreviewS3Path']
        if not resolved.get('auxPreviewPipelineSuffix') and inputOutputS3AssetAuxiliaryFilesPath:
            inputOutputS3AssetAuxiliaryFilesPath = inputOutputS3AssetAuxiliaryFilesPath.rstrip('/') + "/PotreeViewer"

        # Starts excution of pipeline
        execute_pipeline(resolved['inputS3AssetFilePath'], '', '', '', inputOutputS3AssetAuxiliaryFilesPath
                                            , resolved['inputMetadataS3Location'], resolved['inputConfigurationS3Location'], external_task_token
                                            , executing_userName, executing_requestContext, resolved['orchestrationEventPrefix'])

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
