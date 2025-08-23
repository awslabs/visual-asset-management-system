# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
from boto3.dynamodb.conditions import Key
from botocore.config import Config
from botocore.exceptions import ClientError
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from common.s3 import validateS3AssetExtensionsAndContentType
from models.common import APIGatewayProxyResponseV2, internal_error, success, validation_error, general_error, authorization_error, VAMSGeneralErrorResponse
from models.assetsV3 import (
    DownloadAssetRequestModel, DownloadAssetResponseModel
)

# Configure AWS clients
region = os.environ['AWS_REGION']
s3_config = Config(signature_version='s3v4', s3={'addressing_style': 'path'})
s3 = boto3.client('s3', region_name=region, config=s3_config)
dynamodb = boto3.resource('dynamodb')
logger = safeLogger(service_name="DownloadAsset")

# Constants
PREVIEW_PREFIX = 'previews/'

# Load environment variables
try:
    s3_asset_buckets_table = os.environ["S3_ASSET_BUCKETS_STORAGE_TABLE_NAME"]
    asset_storage_table_name = os.environ["ASSET_STORAGE_TABLE_NAME"]
    token_timeout = os.environ["PRESIGNED_URL_TIMEOUT_SECONDS"]
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

# Initialize DynamoDB tables
buckets_table = dynamodb.Table(s3_asset_buckets_table)
asset_table = dynamodb.Table(asset_storage_table_name)

#######################
# Utility Functions
#######################

def get_default_bucket_details(bucketId):
    """Get default S3 bucket details from database default bucket DynamoDB"""
    try:

        bucket_response = buckets_table.query(
            KeyConditionExpression=Key('bucketId').eq(bucketId),
            Limit=1
        )
        # Use the first item from the query results
        bucket = bucket_response.get("Items", [{}])[0] if bucket_response.get("Items") else {}
        bucket_id = bucket.get('bucketId')
        bucket_name = bucket.get('bucketName')
        base_assets_prefix = bucket.get('baseAssetsPrefix')

        #Check to make sure we have what we need
        if not bucket_name or not base_assets_prefix:
            raise VAMSGeneralErrorResponse(f"Error getting database default bucket details: {str(e)}")
        
        #Make sure we end in a slash for the path
        if not base_assets_prefix.endswith('/'):
            base_assets_prefix += '/'

        # Remove leading slash from file path if present
        if base_assets_prefix.startswith('/'):
            base_assets_prefix = base_assets_prefix[1:]

        return {
            'bucketId': bucket_id,
            'bucketName': bucket_name,
            'baseAssetsPrefix': base_assets_prefix
        }
    except Exception as e:
        logger.exception(f"Error getting bucket details: {e}")
        raise VAMSGeneralErrorResponse(f"Error getting bucket details: {str(e)}")

def get_asset_details(databaseId, assetId):
    """Get asset details from DynamoDB"""
    try:
        response = asset_table.query(
            KeyConditionExpression=Key('databaseId').eq(databaseId) & Key('assetId').eq(assetId),
            ScanIndexForward=False
        )
        
        if not response.get('Items'):
            return None
            
        # Return the first (most recent) item
        return response['Items'][0]
    except Exception as e:
        logger.exception(f"Error getting asset details: {e}")
        raise VAMSGeneralErrorResponse(f"Error retrieving asset: {str(e)}")

def is_file_archived(metadata):
    """Determine if file is archived based on S3 metadata
    
    Args:
        metadata: The S3 object metadata
        
    Returns:
        True if file is archived, False otherwise
    """
    vams_status = metadata.get('Metadata', {}).get('vams-status', '')
    storage_class = metadata.get('StorageClass', 'STANDARD')
    
    # File is archived if:
    # 1. Has vams-status=archived or deleted metadata, OR
    # 2. Storage class is GLACIER/DEEP_ARCHIVE
    return (vams_status in ['archived', 'deleted'] or 
            storage_class in ['GLACIER', 'DEEP_ARCHIVE'])

def is_delete_marker(bucket, key, version_id=None):
    """Check if a specific version is a delete marker
    
    Args:
        bucket: S3 bucket name
        key: S3 object key
        version_id: Optional version ID to check
        
    Returns:
        True if version is a delete marker, False otherwise
    """
    try:
        # If no version ID provided, check if the latest version is a delete marker
        if not version_id:
            response = s3.list_object_versions(
                Bucket=bucket,
                Prefix=key,
                MaxKeys=1
            )
            
            delete_markers = response.get('DeleteMarkers', [])
            if delete_markers and delete_markers[0].get('IsLatest', False):
                return True
            return False
            
        # If version ID provided, check if it's a delete marker
        response = s3.list_object_versions(
            Bucket=bucket,
            Prefix=key,
            MaxKeys=100  # Increase this if needed to find the specific version
        )
        
        # Check delete markers
        for marker in response.get('DeleteMarkers', []):
            if marker.get('VersionId') == version_id:
                return True
                
        return False
    except Exception as e:
        logger.warning(f"Error checking delete marker: {e}")
        return False

def check_s3_object_exists(bucket, key, version_id=None):
    """Check if S3 object exists
    
    Args:
        bucket: S3 bucket name
        key: S3 object key
        version_id: Optional version ID to check
        
    Returns:
        True if object exists, False otherwise
    """
    try:
        params = {'Bucket': bucket, 'Key': key}
        if version_id:
            params['VersionId'] = version_id
            
        s3.head_object(**params)
        return True
    except ClientError as e:
        if e.response['Error']['Code'] == '404':
            return False
        logger.warning(f"Error checking if object exists: {e}")
        raise
        
def normalize_s3_path(base_path, relative_path):
    """
    Normalize S3 path to ensure there's only a single slash between components.
    
    Args:
        base_path: The base path (prefix)
        relative_path: The relative path to append
        
    Returns:
        Normalized path with a single slash between components
    """
    # Remove trailing slashes from base_path
    base_path = base_path.rstrip('/')
    # Remove leading slashes from relative_path
    relative_path = relative_path.lstrip('/')
    # Join with a single slash
    return f"{base_path}/{relative_path}"

#######################
# Core Download Logic
#######################

def download_asset_file(databaseId, assetId, request_model):
    """Generate download URL for asset file
    
    Args:
        databaseId: Database ID
        assetId: Asset ID
        request_model: DownloadAssetRequestModel instance
        
    Returns:
        DownloadAssetResponseModel instance
    """
    # Get asset details
    asset = get_asset_details(databaseId, assetId)
    if not asset:
        raise VAMSGeneralErrorResponse(f"Asset {assetId} not found in database {databaseId}")
        
    # Check if asset is distributable
    if not asset.get('isDistributable', False):
        raise VAMSGeneralErrorResponse("Asset not distributable")
        
    # Get asset location
    asset_location = asset.get('assetLocation')
    if not asset_location:
        raise VAMSGeneralErrorResponse("Asset location not found")
        
    # Get bucket details from bucketId
    bucketDetails = get_default_bucket_details(asset.get('bucketId'))
    asset_bucket = bucketDetails['bucketName']
    asset_base_key = asset_location.get('Key')
    
    # Determine final S3 key
    if request_model.key:
        # Check if the key already starts with the asset base key to avoid duplication
        if request_model.key.startswith(asset_base_key):
            # Key already includes the base path, use it as-is
            final_key = request_model.key
        else:
            # Key is relative, combine with base path
            final_key = normalize_s3_path(asset_base_key, request_model.key)
    else:
        # If no key provided, use base key directly
        final_key = asset_base_key
    
    # Validate file extension and content type
    if not validateS3AssetExtensionsAndContentType(asset_bucket, final_key):
        raise VAMSGeneralErrorResponse("Unallowed file extension or content type in asset file")
    
    # Check if file exists
    if not check_s3_object_exists(asset_bucket, final_key):
        raise VAMSGeneralErrorResponse("File not found in S3")
    
    # Handle version ID
    version_id = request_model.versionId
    
    # Check if version is a delete marker
    if version_id and is_delete_marker(asset_bucket, final_key, version_id):
        # Use 410 Gone for archived/deleted versions
        raise VAMSGeneralErrorResponse("File version has been archived and cannot be downloaded", status_code=410)
    
    # Generate presigned URL
    try:
        params = {
            'Bucket': asset_bucket,
            'Key': final_key
        }
        
        if version_id:
            params['VersionId'] = version_id
            
        url = s3.generate_presigned_url(
            'get_object',
            Params=params,
            ExpiresIn=int(token_timeout)
        )
        
        # Return response model
        return DownloadAssetResponseModel(
            downloadUrl=url,
            expiresIn=int(token_timeout),
            downloadType="assetFile",
            versionId=version_id
        )
    except Exception as e:
        logger.exception(f"Error generating presigned URL: {e}")
        raise VAMSGeneralErrorResponse(f"Error generating download URL: {str(e)}")

def download_asset_preview(databaseId, assetId, request_model):
    """Generate download URL for asset preview
    
    Args:
        databaseId: Database ID
        assetId: Asset ID
        request_model: DownloadAssetRequestModel instance
        
    Returns:
        DownloadAssetResponseModel instance
    """
    # Get asset details
    asset = get_asset_details(databaseId, assetId)
    if not asset:
        raise VAMSGeneralErrorResponse(f"Asset {assetId} not found in database {databaseId}")
        
    # Check if asset is distributable
    if not asset.get('isDistributable', False):
        raise VAMSGeneralErrorResponse("Asset not distributable")
        
    # Get preview location
    preview_location = asset.get('previewLocation')
    if not preview_location:
        raise VAMSGeneralErrorResponse("Asset preview location not found")
        
    # Get bucket details from bucketId
    bucketDetails = get_default_bucket_details(asset.get('bucketId'))
    preview_bucket = bucketDetails['bucketName']
    preview_key = preview_location.get('Key')
    
    # Validate file extension and content type
    if not validateS3AssetExtensionsAndContentType(preview_bucket, preview_key):
        raise VAMSGeneralErrorResponse("Unallowed file extension or content type in preview file")
    
    # Check if preview file exists in S3
    if not check_s3_object_exists(preview_bucket, preview_key):
        raise VAMSGeneralErrorResponse("Preview file not found in S3")
    
    # Generate presigned URL
    try:
        url = s3.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': preview_bucket,
                'Key': preview_key
            },
            ExpiresIn=int(token_timeout)
        )
        
        # Return response model
        return DownloadAssetResponseModel(
            downloadUrl=url,
            expiresIn=int(token_timeout),
            downloadType="assetPreview"
        )
    except Exception as e:
        logger.exception(f"Error generating presigned URL: {e}")
        raise VAMSGeneralErrorResponse(f"Error generating download URL: {str(e)}")

#######################
# Lambda Handler
#######################

def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for asset download API"""
    claims_and_roles = request_to_claims(event)
    
    try:
        # Parse request body with enhanced error handling
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"})
        
        # Parse JSON body safely
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"})
        elif isinstance(body, dict):
            body = body
        else:
            logger.error("Request body is not a string")
            return validation_error(body={'message': "Request body cannot be parsed"})
        
        # Get path parameters
        path_parameters = event.get('pathParameters', {})
        if not path_parameters or 'databaseId' not in path_parameters or 'assetId' not in path_parameters:
            return validation_error(body={'message': "Missing databaseId or assetId in path parameters"})
            
        database_id = path_parameters['databaseId']
        asset_id = path_parameters['assetId']
        
        # Validate path parameters
        (valid, message) = validate({
            'databaseId': {
                'value': database_id,
                'validator': 'ID'
            },
            'assetId': {
                'value': asset_id,
                'validator': 'ASSET_ID'
            }
        })
        
        if not valid:
            logger.error(message)
            return validation_error(body={'message': message})
        
        # Parse request model
        try:
            request_model = parse(body, model=DownloadAssetRequestModel)
        except ValidationError as v:
            logger.error(f"Validation error: {v}")
            return validation_error(body={'message': str(v)})
        
        # Check authorization
        asset = get_asset_details(database_id, asset_id)
        if not asset:
            return validation_error(body={'message': f"Asset {asset_id} not found"})
        
        asset["object__type"] = "asset"
        
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not (casbin_enforcer.enforce(asset, "GET") and casbin_enforcer.enforceAPI(event)):
                return authorization_error()
        
        # Process download request based on type
        try:
            if request_model.downloadType == "assetFile":
                response = download_asset_file(database_id, asset_id, request_model)
            else:  # assetPreview
                response = download_asset_preview(database_id, asset_id, request_model)
                
            return success(body=response.dict())
        except VAMSGeneralErrorResponse as e:
            # Extract status code if provided
            status_code = getattr(e, 'status_code', 400)
            return general_error(status_code=status_code, body={'message': str(e)})
            
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)})
    except ValueError as v:
        logger.exception(f"Value error: {v}")
        return validation_error(body={'message': str(v)})
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        # Extract status code if provided
        status_code = getattr(v, 'status_code', 400)
        return general_error(status_code=status_code, body={'message': str(v)})
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error()
