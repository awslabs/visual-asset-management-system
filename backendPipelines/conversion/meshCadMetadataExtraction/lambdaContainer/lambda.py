# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import boto3
import os
import threading
from typing import Dict, Any
from common.logger import safeLogger
from botocore.exceptions import ClientError
from boto3.s3.transfer import TransferConfig
from metadata_extractors import extract_cad_metadata, extract_mesh_metadata, get_handler_for_format

logger = safeLogger(service="cadMetadataExtractionPipeline")

s3_client = boto3.client('s3')
s3 = boto3.resource('s3')


def download(bucket_name, object_key, file_path):
    """
    Download a file from S3.
    
    Args:
        bucket_name: S3 bucket name
        object_key: S3 object key
        file_path: Local file path to save the downloaded file
        
    Returns:
        Path to the downloaded file or None if download failed
    """
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


def upload(bucket_name, object_key, file_path):
    """
    Upload a file to S3 with multipart upload support.
    
    Args:
        bucket_name: S3 bucket name
        object_key: S3 object key
        file_path: Local file path to upload
        
    Returns:
        S3 object key or None if upload failed
    """
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


def transform_to_attribute_format(metadata_dict):
    """
    Transform extracted metadata to new attribute format.
    
    Args:
        metadata_dict: Dictionary of extracted metadata
        
    Returns:
        Dictionary with type, updateType, and metadata array
    """
    metadata_array = []
    
    for key, value in metadata_dict.items():
        # Convert value to string if not already
        if isinstance(value, dict) or isinstance(value, list):
            value_str = json.dumps(value)
        else:
            value_str = str(value)
        
        metadata_array.append({
            "metadataKey": key,
            "metadataValue": value_str,
            "metadataValueType": "string"
        })
    
    return {
        "type": "attribute",
        "updateType": "update",
        "metadata": metadata_array
    }


def _fetch_json_from_s3(s3_location):
    """Fetch + parse a JSON object from an s3:// location. Best-effort: returns {} for a
    missing/empty location or any S3/parse failure."""
    if not s3_location or not s3_location.startswith("s3://"):
        return {}
    bucket, _, key = s3_location[len("s3://"):].partition("/")
    if not bucket or not key:
        return {}
    try:
        resp = s3_client.get_object(Bucket=bucket, Key=key)
        body = resp["Body"].read().decode("utf-8")
        return json.loads(body) if body else {}
    except Exception as e:
        logger.warning(f"Could not read {s3_location}: {e}")
        return {}


def asset_relative_path(relative_path, object_key, asset_root_s3_key=""):
    """The input file's path within its asset, without a leading slash ('/parts/housing/model.obj'
    -> 'parts/housing/model.obj').

    The manifest's relativePath is authoritative: a shadowed input (a file a prior pipeline step
    rewrote) is located in the run's OUTPUT bucket under the pipeline output prefix while keeping
    the original file's asset identity, so its object key shares no prefix with the asset root and
    only relativePath states where the file lives on the asset. The object key trimmed by the asset
    root is the fallback for a direct/local invocation carrying no manifest."""
    trimmed = (relative_path or "").strip("/")
    if trimmed:
        return trimmed
    key = object_key or ""
    if asset_root_s3_key and key.startswith(asset_root_s3_key):
        key = key[len(asset_root_s3_key):]
    return key.lstrip("/")


def resolve_inputs_from_manifest(data):
    """Resolve the input file path, its asset-relative path, and the output-metadata path from the
    workflow manifest (inputManifestS3Location), falling back to the legacy top-level body fields
    when no manifest is present (direct/local invocations). Locations are carried as bucket +
    relative keys, so s3:// URIs are reconstructed here. Returns (input_s3_asset_file_path,
    input_asset_relative_path, output_s3_asset_metadata_path).

    Mirrors ``manifestHelper.resolve_inputs`` + ``enforce_single_input_file``, which the pipelines
    with a ``lambda/`` code asset vendor; this pipeline is a container image, so it reads the same
    envelope fields directly. Any change to the envelope applies to both."""
    manifest = _fetch_json_from_s3(data.get("inputManifestS3Location", ""))
    input_files = (manifest or {}).get("inputFiles") or []
    # The pipeline is registered with inputFileArity 'one' and extracts attributes for a single
    # file per execution; more than one resolved input would be silently dropped.
    if len(input_files) > 1:
        raise ValueError(
            f"This pipeline processes a single input file per execution, but the workflow "
            f"manifest supplied {len(input_files)} input files. Multi-file input is not yet "
            f"supported for this pipeline."
        )
    input_path = ""
    relative_path = ""
    if input_files:
        first = input_files[0]
        if first.get("bucket") and first.get("key"):
            input_path = f"s3://{first['bucket']}/{first['key']}"
        relative_path = asset_relative_path(
            first.get("relativePath"), first.get("key", ""), first.get("assetRootS3Key", ""))
    input_path = input_path or data.get("inputS3AssetFilePath", "")
    if not relative_path:
        _, _, legacy_key = input_path.replace("s3://", "").partition("/")
        relative_path = asset_relative_path("", legacy_key, data.get("inputAssetLocationKey", ""))
    # Output-metadata path reconstructed from the outputs bucket + bucket-relative metadata prefix.
    outputs = (manifest or {}).get("outputs", {})
    output_path = ""
    if outputs.get("bucket") and outputs.get("metadata"):
        output_path = f"s3://{outputs['bucket']}/{outputs['metadata']}"
    output_path = output_path or data.get("outputS3AssetMetadataPath", "")
    return input_path, relative_path, output_path


def extract_metadata(input_asset_relative_path, input_path, output_path):
    """
    Extract metadata from a CAD or mesh file.

    Args:
        input_asset_relative_path: the input file's path within its asset
        input_path: S3 URI of the input file
        output_path: S3 URI of the output directory

    Returns:
        Dictionary with status code and message
    """
    input_bucket, input_key = input_path.replace("s3://", "").split("/", 1)
    output_bucket, output_key = output_path.replace("s3://", "").split("/", 1)
    
    logger.info(f"Input: {input_key}")
    logger.info(f"Output: {output_key}")

    # Folder check
    if input_key.endswith("/"):
        raise ValueError("Input S3 URI cannot be a folder")

    # Get file extension
    _, file_extension = os.path.splitext(input_key)
    file_extension = file_extension.lower()
    
    # Check if format is supported
    handler_type = get_handler_for_format(input_key)
    if not handler_type:
        raise ValueError(f"Unsupported file format: {file_extension}")
    
    # Download input file from S3
    temp_file = f'/tmp/input{file_extension}'
    download(input_bucket, input_key, temp_file)
    
    # Extract metadata based on file type
    try:
        if handler_type == 'cad':
            metadata = extract_cad_metadata(temp_file)
        elif handler_type == 'mesh':
            metadata = extract_mesh_metadata(temp_file)
        else:
            raise ValueError(f"Unknown handler type: {handler_type}")
        
        # Transform to new attribute format
        attribute_data = transform_to_attribute_format(metadata)
        
        # Save attribute data to JSON file
        metadata_file = '/tmp/metadata.json'
        with open(metadata_file, 'w') as f:
            json.dump(attribute_data, f, indent=2)
        
        # Upload attributes to S3 as file-level attributes. The attribute file is named after the
        # input file's path WITHIN THE ASSET so the write-back step applies the attributes to that
        # asset file; the input object key is not usable here because a shadowed input sits in the
        # run's output bucket rather than under the asset root.
        if not output_key.endswith("/"):
            output_key += "/"
        output_relative_key = f"{output_key}{input_asset_relative_path}.attribute.json"
        upload(output_bucket, output_relative_key, metadata_file)
        
        logger.info("Attribute extraction complete")
        
        return {
            'statusCode': 200,
            'body': {
                'message': 'Attribute extraction successful',
                'metadata_location': f"s3://{output_bucket}/{output_key}"
            }
        }
    
    except Exception as e:
        logger.exception(f"Error extracting attributes: {str(e)}")
        raise Exception(f"Attribute extraction failed: {str(e)}")


def lambda_handler(event, context):
    """
    Lambda handler function.
    
    Args:
        event: Lambda event
        context: Lambda context
        
    Returns:
        Dictionary with status code and message
    """
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

    # Resolve the input file path, its asset-relative path, and the output-metadata path from the
    # workflow manifest (each input file is self-locating; legacy body fields are the fallback).
    input_path, asset_relative_input_path, output_path = resolve_inputs_from_manifest(data)

    # Extract metadata
    result = extract_metadata(asset_relative_input_path, input_path, output_path)

    return result


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
            logger.info(
                f"\r{self._filename} Progress: {self._seen_so_far} / {self._size} ({percentage:.2f}%)"
            )
