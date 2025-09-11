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

OPEN_PIPELINE_FUNCTION_NAME = os.environ["OPEN_PIPELINE_FUNCTION_NAME"]

logger = safeLogger(service="VamsExecuteRapidPipeline")
lambda_client = boto3.client('lambda')

def execute_pipeline(input_s3_asset_file_path, output_s3_asset_files_path, output_s3_asset_preview_path, output_s3_asset_metadata_path
                                        , inputOutput_s3_assetAuxiliary_files_path, input_metadata, input_parameters, external_task_token
                                        , executing_userName, executing_requestContext, output_file_type):

    # Create the object message to be sent
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


def lambda_handler(event, context):
    logger.info(event)

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

        #Get input parameters if defined
        if 'inputParameters' in data:
            input_parameters = data['inputParameters']
        else:
            input_parameters = ''

        #Get input metadata if defined
        if 'inputMetadata' in data:
            input_metadata = data['inputMetadata']
        else:
            input_metadata = ''

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

        #Get Pipeline OutputType
        if 'outputType' in data: 
            output_file_type = data['outputType']
        else: 
            output_file_type = ''

        # Starts excution of pipeline 
        execute_pipeline(data['inputS3AssetFilePath'], data['outputS3AssetFilesPath'], data['outputS3AssetPreviewPath']
                                            , data['outputS3AssetMetadataPath'], data['inputOutputS3AssetAuxiliaryFilesPath']
                                            , input_metadata, input_parameters, external_task_token, executing_userName,
                                            executing_requestContext, output_file_type)

        return {
            'statusCode': 200,
            'body': 'Success'
        }
    except Exception as e:
        logger.exception(e)
        return {
            'statusCode': 500,
            'body': json.dumps({"message": "Internal Server Error"})
        }
