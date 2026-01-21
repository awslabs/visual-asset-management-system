# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import boto3
import os
import threading
import os
import trimesh
from common.logger import safeLogger
from botocore.exceptions import ClientError
from boto3.s3.transfer import TransferConfig

logger = safeLogger(service="conversionTrimeshPipeline")

s3_client = boto3.client('s3')
s3 = boto3.resource('s3')


def download(bucket_name, object_key, file_path):
    logger.info(
        "Downloading Object from S3 Bucket. Bucket: {}, Object: {}, File Path: {}".format(
            bucket_name, object_key, file_path
        )
    )
    try:
        with open(file_path, "wb") as data:
            s3_client.download_fileobj(bucket_name, object_key, data)
    except ClientError as e:
        logger.exception(e)
        raise Exception("Could not download input file from S3 bucket")
    return file_path


def uploadV2(bucket_name, object_key, file_path):
    logger.info(
        f"Uploading Object to S3 Bucket w/ auto chunking for multi-part.\nBucket:{bucket_name}.\n:Object: {object_key}"
    )

   # Multipart upload
    try:
        GB = 1024 ** 3
        MB = 1024 ** 2
        config = TransferConfig(multipart_threshold=1*GB, max_concurrency=10,
                                multipart_chunksize=100*MB, use_threads=True
                                )
        s3.meta.client.upload_file(file_path, bucket_name, object_key,
                                   ExtraArgs={},
                                   Config=config,
                                   Callback=ProgressPercentage(file_path)
                                   )
    except ClientError as e:
        logger.exception(e)
        raise Exception("Could not upload output file to S3 bucket")
    return object_key
    
def convert_input_output(input_path, output_path, output_filetype):
    input_bucket, input_key = input_path.replace("s3://", "").split("/", 1)
    output_bucket, output_key = output_path.replace("s3://", "").split("/", 1)
    logger.info(input_key)
    logger.info(output_key)

    supported_formats = ['.stl', '.obj', '.ply', '.gltf', '.glb', '.3mf', '.xaml', '.3dxml', '.dae', '.xyz']

    #Folder check
    if (input_key.endswith("/")):
        raise ValueError("Input S3 URI cannot be a folder")

    # Check input and output formats
    input_s3_asset_file_root, input_s3_asset_extension = os.path.splitext(input_key)
    if (not input_s3_asset_extension or input_s3_asset_extension == '' or input_s3_asset_extension not in supported_formats):
        raise ValueError(f"Input format {input_s3_asset_extension} not supported by Trimesh pipeline")
    if output_filetype not in supported_formats:
        raise ValueError(f"Output format {output_filetype} not supported by Trimesh pipeline")

    # Download input file from S3
    temp_file = '/tmp/input{}'.format(input_s3_asset_extension)
    download(input_bucket, input_key, temp_file)

    # Load mesh using trimesh
    mesh = trimesh.load(temp_file)

    # Export mesh to output format
    output_file = os.path.join('/tmp', f'output{output_filetype}')
    mesh.export(output_file, file_type=output_filetype)

    # Upload output file to S3
    outputFileName, _ = os.path.splitext(os.path.basename(input_key)) #get the original file name without extension
    outputFileName = f"{outputFileName}{output_filetype}" #add final output extension
    output_key = os.path.join(output_key, outputFileName) #get final storage key location for output file
    uploadV2(output_bucket, output_key, output_file) #upload to storage

    logger.info("Conversion complete")


def lambda_handler(event, context):

    logger.info(event)
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
        logger.error(message)
        raise ValueError(message)

    if isinstance(event['body'], str):
        data = json.loads(event['body'])
    else:
        data = event['body']

    # Check external task token if passed (Synchronous Pipeline so no task token should be passed)
    if 'TaskToken' in data:
        raise Exception("VAMS Workflow TaskToken found in pipeline input. Make sure to register this pipeline in VAMS as NOT needing a task token callback.")
        
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

    #Get outputType if defined
    if 'outputType' in data:
        output_filetype = data['outputType']
    else:
        output_filetype = ''

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

    convert_input_output(data['inputS3AssetFilePath'], data['outputS3AssetFilesPath'], output_filetype)

    return {
        'statusCode': 200, 
        'body': 'Success'
    }



# Class for multipart upload
class ProgressPercentage(object):
    def __init__(self, filename):
        self._filename = filename
        self._size = float(os.path.getsize(filename))
        self._seen_so_far = 0
        self._lock = threading.Lock()

    def __call__(self, bytes_amount):
        # To simplify we'll assume this is hooked up
        # to a single filename.
        with self._lock:
            self._seen_so_far += bytes_amount
            percentage = (self._seen_so_far / self._size) * 100
