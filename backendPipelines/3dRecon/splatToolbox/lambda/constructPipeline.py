#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import json
import os
import boto3
import hashlib
import time
from customLogging.logger import safeLogger

logger = safeLogger(service="ConstructSplatToolboxPipeline")

# Initialize S3 client
s3_client = boto3.client('s3')

def is_duplicate_job(job_name, input_file_path, aux_bucket, aux_key, expiration_seconds=300):
    """
    Check if this is a duplicate job request by using S3 aux assets location as a simple lock mechanism.
    
    Args:
        job_name: The job name
        input_file_path: The input file path
        aux_bucket: The auxiliary assets bucket for temp files
        aux_key: The auxiliary assets key prefix for temp files
        expiration_seconds: How long to consider a request as duplicate (default: 5 minutes)
        
    Returns:
        True if this is a duplicate job request, False otherwise
    """
    try:
        # Create a unique identifier for this job
        job_hash = hashlib.md5(f"{job_name}:{input_file_path}".encode('utf-8')).hexdigest()
        lock_key = f"{aux_key}/locks/{job_hash}"
        
        try:
            # Check if a lock object exists
            response = s3_client.head_object(
                Bucket=aux_bucket,
                Key=lock_key
            )
            
            # If we get here, the lock exists
            last_modified = response.get('LastModified')
            if last_modified:
                # Check if the lock has expired
                now = time.time()
                lock_time = last_modified.timestamp()
                if now - lock_time < expiration_seconds:
                    logger.info(f"Duplicate job detected for {job_name} (lock is {now - lock_time} seconds old)")
                    return True
                else:
                    logger.info(f"Lock exists but has expired ({now - lock_time} seconds old)")
            
        except s3_client.exceptions.ClientError as e:
            if e.response['Error']['Code'] == '404':
                # Lock doesn't exist, which is what we want
                pass
            else:
                # Some other error occurred
                logger.warning(f"Error checking lock: {str(e)}")
                # Continue processing to be safe
        
        # Create a lock object in aux assets location
        s3_client.put_object(
            Bucket=aux_bucket,
            Key=lock_key,
            Body=f"Lock created at {time.time()}"
        )
        
        logger.info(f"Created lock for {job_name} at s3://{aux_bucket}/{lock_key}")
        return False
    except Exception as e:
        # If there's any error in the deduplication logic, log it but continue processing
        logger.warning(f"Error in deduplication check: {str(e)}")
    
    return False

def lambda_handler(event, context):
    """
    ConstructPipeline
    Builds pipeline input definition to run the Batch application
    """

    logger.info(f"Event: {event}")
    logger.info(f"Context: {context}")
    
    job_name = event.get("jobName")
    input_s3_asset_file_path = event.get("inputS3AssetFilePath")
    inputOutput_s3_assetAuxiliary_files_uri = event.get('inputOutputS3AssetAuxiliaryFilesPath')
    
    # Extract aux bucket and key for lock file
    aux_bucket = ""
    aux_key = ""
    if inputOutput_s3_assetAuxiliary_files_uri:
        aux_bucket, aux_key = inputOutput_s3_assetAuxiliary_files_uri.replace("s3://", "").split("/", 1)
    
    # Check for duplicate job requests using aux assets location
    if aux_bucket and aux_key and is_duplicate_job(job_name, input_s3_asset_file_path, aux_bucket, aux_key):
        logger.warning(f"Detected duplicate job request for {job_name} with input {input_s3_asset_file_path}")
        return {
            "jobName": job_name,
            "status": "DUPLICATE_DETECTED",
            "error": {
                "Error": "DuplicateJobError",
                "Cause": f"A job with the same parameters was recently started. Skipping duplicate execution."
            }
        }

    definition = construct_splattoolbox_definition(event)
    logger.info(f"Definition: {definition}")

    return {
        "jobName": job_name,
        "currentStageType": definition["stages"][0]["type"],
        "definition": ["python", "__main__.py", json.dumps(definition)],
        "inputMetadata": event.get("inputMetadata", ""),
        "inputParameters": event.get("inputParameters", ""),
        "externalSfnTaskToken": event.get("externalSfnTaskToken", ""),
        "status": "STARTING"
    }

def determine_resource_requirements(definition):
    """
    Determine appropriate vCPUs and memory based on job requirements
    """
    # Default resources for basic jobs
    vcpus = 4
    memory = 16384  # 16 GB
    
    # Check if this is a complex job requiring more resources
    stage = definition["stages"][0]
    input_file = stage.get("inputFile", {})
    filename = input_file.get("objectKey", "")
    
    # Estimate complexity based on file size indicators or job parameters
    if "large" in filename.lower() or "4k" in filename.lower():
        vcpus = 16
        memory = 65536  # 64 GB
    elif "medium" in filename.lower() or "hd" in filename.lower():
        vcpus = 8
        memory = 32768  # 32 GB
    
    return vcpus, memory


def construct_splattoolbox_definition(event) -> dict:
    input_s3_asset_file_uri = event['inputS3AssetFilePath']
    output_s3_asset_files_uri = event.get('outputS3AssetFilesPath', '')
    inputOutput_s3_assetAuxiliary_files_uri = event['inputOutputS3AssetAuxiliaryFilesPath']

    input_s3_asset_file_bucket, input_s3_asset_file_key = input_s3_asset_file_uri.replace("s3://", "").split("/", 1)
    inputOutput_s3_assetAuxiliary_files_bucket, inputOutput_s3_assetAuxiliary_files_key = inputOutput_s3_assetAuxiliary_files_uri.replace("s3://", "").split("/", 1)

    input_s3_asset_file_root, input_s3_asset_extension = os.path.splitext(input_s3_asset_file_key)

    # MUST use outputS3AssetFilesPath from workflow for proper file registration
    if output_s3_asset_files_uri:
        output_s3_asset_files_bucket, output_s3_asset_files_key = output_s3_asset_files_uri.replace("s3://", "").split("/", 1)
        output_bucket = output_s3_asset_files_bucket
        output_dir = output_s3_asset_files_key if output_s3_asset_files_key.endswith('/') else output_s3_asset_files_key + '/'
    else:
        # Fallback for non-workflow execution (direct pipeline invocation)
        output_bucket = input_s3_asset_file_bucket
        output_dir = f"{input_s3_asset_file_root}/3dRecon/splatToolbox/"

    splat_stage = {
        "type": "SPLAT",
        "inputFile": {
            "bucketName": input_s3_asset_file_bucket,
            "objectKey": input_s3_asset_file_key,
            "fileExtension": input_s3_asset_extension
        },
        "outputFiles": {
            "bucketName": output_bucket,
            "objectDir": output_dir,
        },
        "outputMetadata": {
            "bucketName": "",
            "objectDir": "",
        },
        "temporaryFiles": {
            "bucketName": inputOutput_s3_assetAuxiliary_files_bucket,
            "objectDir": f"{inputOutput_s3_assetAuxiliary_files_key}/",
        }
    }

    definition = {
        "jobName": event.get("jobName"),
        "stages": [splat_stage],
        "inputMetadata": event.get("inputMetadata", ""),
        "inputParameters": event.get("inputParameters", ""),
        "externalSfnTaskToken": event.get("externalSfnTaskToken", ""),
    }

    return definition
