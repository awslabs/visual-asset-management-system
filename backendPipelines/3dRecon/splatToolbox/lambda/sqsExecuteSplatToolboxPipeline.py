#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""
Lambda Function to Call from an SNS trigger from S3
"""
import os
import boto3
import json
from customLogging.logger import safeLogger
from urllib.parse import unquote_plus

logger = safeLogger(service="SnsExecuteSplatToolboxPipeline")
lambda_client = boto3.client('lambda')
OPEN_PIPELINE_FUNCTION_NAME = os.environ["OPEN_PIPELINE_FUNCTION_NAME"]
S3_ASSETAUXILIARY_BUCKET_NAME = os.environ["S3_ASSETAUXILIARY_BUCKET_NAME"]

def execute_pipeline(input_s3_asset_file_path, output_s3_asset_files_path, output_s3_asset_preview_path, 
                    output_s3_asset_metadata_path, inputOutput_s3_assetAuxiliary_files_path, input_metadata, 
                    input_parameters, external_task_token, executing_userName, executing_requestContext):
    
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
        "executingRequestContext": executing_requestContext
    }

    logger.info("Invoking Open Pipeline Lambda")
    lambda_response = lambda_client.invoke(
        FunctionName=OPEN_PIPELINE_FUNCTION_NAME,
        InvocationType='RequestResponse',
        Payload=json.dumps(messagePayload).encode('utf-8')
    )
    logger.info("Invoke Open Pipeline Lambda Successfully")
    
    if 'StatusCode' not in lambda_response or lambda_response['StatusCode'] != 200:
        message = lambda_response.get("body", {}).get("message", "")
        raise Exception("Invoke Open Pipeline Lambda Failed. " + message)



def lambda_handler(event, context):
    logger.info(event)

    try:
        if not event['Records']:
            logger.error("No records to process")
            return {'statusCode': 500, 'body': {'error': "No records to process"}}

        for sns_record in event['Records']:
            logger.info(f"SNS Record: {sns_record}")
            
            try:
                sns_message = json.loads(sns_record["body"])
                s3_records = json.loads(sns_message["Message"])['Records']
                logger.info(f"S3 Records: {s3_records}")
            except Exception as e:
                logger.exception(f"Error parsing SNS message: {str(e)}")
                s3_records = []
                continue

            # SNS-triggered pipelines have no metadata or parameters from workflow
            input_Metadata = ''
            input_Parameters = ''
            external_sfn_task_token = ''

            for record in s3_records:
                logger.info(f"S3 Record: {record}")
                
                if isinstance(record['s3'], list):
                    s3Record = record['s3'][0]
                else:
                    s3Record = record['s3']

                s3_source_bucket = s3Record['bucket']['name']
                s3_source_key = unquote_plus(s3Record['object']['key'])

                # Filter out unwanted files
                ignore_patterns = ["pipeline", "pipelines", "preview", "previews", "temp-upload", "temp-uploads", "3dRecon"]
                if any(pattern in s3_source_key for pattern in ignore_patterns):
                    logger.info(f"Ignoring file: {s3_source_key}")
                    continue

                # Skip folder creation events (size = 0)
                if s3Record['object'].get('size', 0) == 0:
                    logger.info(f"Ignoring folder creation: {s3_source_key}")
                    continue

                inputS3AssetFilePath = f"s3://{s3_source_bucket}/{s3_source_key}"
                inputOutputS3AssetAuxiliaryFilesPath = f"s3://{S3_ASSETAUXILIARY_BUCKET_NAME}/{s3_source_key}/3dRecon/splatToolbox"

                logger.info(f"Executing pipeline for: {s3_source_key}")
                logger.info(f"Input: {inputS3AssetFilePath}")
                logger.info(f"Aux assets location: {inputOutputS3AssetAuxiliaryFilesPath}")
                
                execute_pipeline(inputS3AssetFilePath, '', '', '', inputOutputS3AssetAuxiliaryFilesPath,
                               input_Metadata, input_Parameters, external_sfn_task_token, "", "")

        return {'statusCode': 200, 'body': 'Success'}
        
    except Exception as e:
        logger.exception(e)
        return {'statusCode': 500, 'body': json.dumps({"message": "Internal Server Error"})}
