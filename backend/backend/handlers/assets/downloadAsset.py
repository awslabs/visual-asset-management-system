# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
from concurrent.futures import ThreadPoolExecutor
from boto3.dynamodb.conditions import Key
from botocore.config import Config
from botocore.exceptions import ClientError
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from common.constants import STANDARD_JSON_RESPONSE
from common.s3MetadataKeys import (
    VAMS_STATUS_METADATA_KEY,
    VAMS_STATUS_ARCHIVED,
    VAMS_STATUS_DELETED,
)
from common.validators import validate
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from customLogging.auditLogging import log_file_download, log_file_download_bulk
from common.s3 import validateS3AssetExtensionsAndContentType, validateUnallowedFileExtensionAndContentType
from models.common import APIGatewayProxyResponseV2, internal_error, success, validation_error, general_error, authorization_error, VAMSGeneralErrorResponse
from models.assetsV3 import (
    DownloadAssetRequestModel, DownloadAssetResponseModel, DownloadAssetFileUrlModel
)
from handlers.assets.assetVersions import (
    resolve_file_version_from_asset_version,
    resolve_asset_version_id_from_alias
)

#Set environment variable for S3 client configuration
#'regional' set to add region decriptor to presigned urls for us-east-1 (ignored for non us-east-1 regions)
os.environ["AWS_S3_US_EAST_1_REGIONAL_ENDPOINT"] = "regional" 

# Configure AWS clients. The connection pool must cover the bulk worker pool
# or threads serialize on connections. Adaptive retries add a client-side rate
# limiter so a burst of HeadObject calls (e.g. many concurrent whole-asset
# downloads of the same S3 prefix) degrades to slower-but-successful instead of
# surfacing 503 SlowDown as per-file failures.
region = os.environ['AWS_REGION']
s3_config = Config(signature_version='s3v4', s3={'addressing_style': 'path'},
                   max_pool_connections=50,
                   retries={'max_attempts': 5, 'mode': 'adaptive'})
s3 = boto3.client('s3', region_name=region, config=s3_config)
dynamodb = boto3.resource('dynamodb')
logger = safeLogger(service_name="DownloadAsset")

# Worker pool size for per-file S3 checks and URL generation in bulk requests.
# Each key costs one HeadObject; 1500 keys must finish well inside the API
# Gateway 29s window, so this pool is wider than the usual 10-worker pools.
# Tunable via env var so it can be dialed down without a code change if S3
# SlowDown is ever observed under concurrent same-asset load.
try:
    MAX_PARALLEL_S3_WORKERS = int(os.environ.get("DOWNLOAD_MAX_PARALLEL_S3_WORKERS", "25"))
except (ValueError, TypeError):
    MAX_PARALLEL_S3_WORKERS = 25

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
            raise VAMSGeneralErrorResponse(f"Error getting database default bucket details.")
        
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
        raise VAMSGeneralErrorResponse(f"Error getting bucket details.")

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
        raise VAMSGeneralErrorResponse(f"Error retrieving asset.")

def is_file_archived(metadata):
    """Determine if file is archived based on S3 metadata
    
    Args:
        metadata: The S3 object metadata
        
    Returns:
        True if file is archived, False otherwise
    """
    vams_status = metadata.get('Metadata', {}).get(VAMS_STATUS_METADATA_KEY, '')
    storage_class = metadata.get('StorageClass', 'STANDARD')

    # File is archived if:
    # 1. Has vams-status=archived or deleted metadata, OR
    # 2. Storage class is GLACIER/DEEP_ARCHIVE
    return (vams_status in [VAMS_STATUS_ARCHIVED, VAMS_STATUS_DELETED] or
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

def get_distributable_asset_context(databaseId, assetId):
    """Get the asset's bucket and base key, verifying the asset is downloadable"""
    asset = get_asset_details(databaseId, assetId)
    if not asset:
        raise VAMSGeneralErrorResponse("Asset not found in database")

    # Check if asset is distributable
    if not asset.get('isDistributable', False):
        raise VAMSGeneralErrorResponse("Asset not distributable")

    # Get asset location
    asset_location = asset.get('assetLocation')
    if not asset_location:
        raise VAMSGeneralErrorResponse("Asset location not found")

    # Get bucket details from bucketId
    bucketDetails = get_default_bucket_details(asset.get('bucketId'))
    return bucketDetails['bucketName'], asset_location.get('Key')

def resolve_and_sign_file_key(databaseId, assetId, asset_bucket, asset_base_key, raw_key,
                              version_id=None, asset_version_id=None, asset_version_alias=None):
    """Resolve one file key against the asset and generate its presigned URL

    Returns:
        (downloadUrl, versionId) tuple

    Raises:
        VAMSGeneralErrorResponse for per-file failures (missing, archived, disallowed type)
    """
    # Determine final S3 key
    if raw_key:
        # Check if the key already starts with the asset base key to avoid duplication
        if raw_key.startswith(asset_base_key):
            # Key already includes the base path, use it as-is
            final_key = raw_key
        else:
            # Key is relative, combine with base path
            final_key = normalize_s3_path(asset_base_key, raw_key)
    else:
        # If no key provided, use base key directly
        final_key = asset_base_key

    # Resolve version ID first -- an asset-version pin (whole-set) resolves the
    # per-file S3 version from the snapshot; otherwise version_id is either the
    # per-file version passed in or None (latest).
    if asset_version_alias or asset_version_id:
        # Compute relative key by stripping asset_base_key from final_key
        # This matches how fileKey is stored in version records (relative to asset prefix)
        relative_file_key = final_key
        normalized_base = asset_base_key if asset_base_key.endswith('/') else asset_base_key + '/'
        if relative_file_key.startswith(normalized_base):
            relative_file_key = relative_file_key[len(normalized_base):]
        relative_file_key = relative_file_key.lstrip('/')

        if asset_version_alias:
            resolved_asset_version_id = resolve_asset_version_id_from_alias(databaseId, assetId, asset_version_alias)
            version_id = resolve_file_version_from_asset_version(databaseId, assetId, resolved_asset_version_id, relative_file_key)
        else:
            version_id = resolve_file_version_from_asset_version(databaseId, assetId, asset_version_id, relative_file_key)

    if final_key.endswith('/'):
        # Prefix download (asset base location): validate every object under it
        if not validateS3AssetExtensionsAndContentType(asset_bucket, final_key):
            raise VAMSGeneralErrorResponse("Unallowed file extension or content type in asset file")
        if not check_s3_object_exists(asset_bucket, final_key):
            raise VAMSGeneralErrorResponse("File not found in S3")
    else:
        # Single file: one HeadObject covers both the existence check and the
        # extension/content-type validation - critical for bulk requests where
        # per-key S3 round trips bound the whole request's latency. When a
        # specific version is requested, head THAT version so an older version
        # is still downloadable even if the latest version is a delete marker.
        head_params = {'Bucket': asset_bucket, 'Key': final_key}
        if version_id:
            head_params['VersionId'] = version_id
        try:
            head = s3.head_object(**head_params)
        except ClientError as e:
            if e.response['Error']['Code'] in ('404', 'NoSuchKey'):
                raise VAMSGeneralErrorResponse("File not found in S3")
            if e.response['Error']['Code'] in ('405', 'MethodNotAllowed'):
                # This version is a delete marker
                raise VAMSGeneralErrorResponse("File version has been archived and cannot be downloaded", status_code=410)
            logger.exception(f"Error checking file: {e}")
            raise VAMSGeneralErrorResponse("Error checking file in S3")
        if not validateUnallowedFileExtensionAndContentType(final_key, head.get('ContentType', '')):
            raise VAMSGeneralErrorResponse("Unallowed file extension or content type in asset file")

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
        return url, version_id
    except Exception as e:
        logger.exception(f"Error generating presigned URL: {e}")
        raise VAMSGeneralErrorResponse(f"Error generating download URL.")

def download_asset_file(databaseId, assetId, request_model):
    """Generate download URL for asset file

    Args:
        databaseId: Database ID
        assetId: Asset ID
        request_model: DownloadAssetRequestModel instance

    Returns:
        DownloadAssetResponseModel instance
    """
    asset_bucket, asset_base_key = get_distributable_asset_context(databaseId, assetId)

    url, version_id = resolve_and_sign_file_key(
        databaseId, assetId, asset_bucket, asset_base_key, request_model.key,
        version_id=request_model.versionId,
        asset_version_id=request_model.assetVersionId,
        asset_version_alias=request_model.assetVersionIdAlias
    )

    # Return response model
    return DownloadAssetResponseModel(
        downloadUrl=url,
        expiresIn=int(token_timeout),
        downloadType="assetFile",
        versionId=version_id
    )

def download_asset_files_bulk(databaseId, assetId, request_model):
    """Generate download URLs for multiple files of the same asset

    Asset-level checks (existence, distributable, bucket lookup) run once;
    per-key resolution and URL signing run in a bounded worker pool. Per-file
    failures are soft: the file's entry carries success=False and an error
    message rather than failing the whole request.

    Args:
        databaseId: Database ID
        assetId: Asset ID
        request_model: DownloadAssetRequestModel instance with keys set

    Returns:
        DownloadAssetResponseModel instance with per-file entries
    """
    asset_bucket, asset_base_key = get_distributable_asset_context(databaseId, assetId)

    def _sign_one(entry):
        # entry is {'key': str, 'versionId': Optional[str]} (normalized in the model)
        raw_key = entry['key']
        per_file_version = entry.get('versionId')
        try:
            url, version_id = resolve_and_sign_file_key(
                databaseId, assetId, asset_bucket, asset_base_key, raw_key,
                version_id=per_file_version,
                asset_version_id=request_model.assetVersionId,
                asset_version_alias=request_model.assetVersionIdAlias
            )
            return DownloadAssetFileUrlModel(
                key=raw_key, downloadUrl=url, versionId=version_id, success=True
            )
        except VAMSGeneralErrorResponse as e:
            return DownloadAssetFileUrlModel(key=raw_key, success=False, error=str(e))
        except Exception as e:
            logger.exception(f"Error generating URL for {raw_key}: {e}")
            return DownloadAssetFileUrlModel(
                key=raw_key, success=False, error="Error generating download URL."
            )

    max_workers = min(MAX_PARALLEL_S3_WORKERS, len(request_model.keys))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        file_entries = list(executor.map(_sign_one, request_model.keys))

    successful = [f for f in file_entries if f.success]
    skipped = [f for f in file_entries if not f.success]
    if not successful:
        raise VAMSGeneralErrorResponse("No download URLs could be generated for the requested files")

    if skipped:
        message = (f"Generated {len(successful)} of {len(file_entries)} download URLs. "
                   f"Warning: {len(skipped)} file path(s) do not exist or are not "
                   "downloadable and were skipped.")
    else:
        message = f"Generated {len(successful)} download URLs"

    # Top-level downloadUrl carries the first successful URL for compatibility
    # with single-URL response consumers
    return DownloadAssetResponseModel(
        downloadUrl=successful[0].downloadUrl,
        expiresIn=int(token_timeout),
        downloadType="assetFile",
        files=file_entries,
        message=message
    )

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
        raise VAMSGeneralErrorResponse("Asset not found in database")
        
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
        raise VAMSGeneralErrorResponse(f"Error generating download URL.")

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
            return validation_error(body={'message': "Request body is required"}, event=event)
        
        # Parse JSON body safely
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"}, event=event)
        elif isinstance(body, dict):
            body = body
        else:
            logger.error("Request body is not a string")
            return validation_error(body={'message': "Request body cannot be parsed"}, event=event)
        
        # Get path parameters
        path_parameters = event.get('pathParameters', {})
        if not path_parameters or 'databaseId' not in path_parameters or 'assetId' not in path_parameters:
            return validation_error(body={'message': "Missing databaseId or assetId in path parameters"}, event=event)
            
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
            return validation_error(body={'message': message}, event=event)
        
        # Parse request model
        try:
            request_model = parse(body, model=DownloadAssetRequestModel)
        except ValidationError as v:
            logger.error(f"Validation error: {v}")
            return validation_error(body={'message': str(v)}, event=event)
        
        # Check authorization
        asset = get_asset_details(database_id, asset_id)
        if not asset:
            return validation_error(body={'message': "Asset not found"}, event=event)
        
        asset["object__type"] = "asset"
        
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not (casbin_enforcer.enforce(asset, "GET") and casbin_enforcer.enforceAPI(event)):
                return authorization_error()
        
        # Process download request based on type
        try:
            if request_model.downloadType == "assetFile":
                if request_model.keys is not None:
                    response = download_asset_files_bulk(database_id, asset_id, request_model)
                else:
                    response = download_asset_file(database_id, asset_id, request_model)
            else:  # assetPreview
                response = download_asset_preview(database_id, asset_id, request_model)

            # AUDIT LOG: File download - log before returning presigned URL(s).
            # Bulk requests log one entry per successfully signed file in a
            # single batched CloudWatch write.
            if request_model.keys is not None:
                log_file_download_bulk(
                    event,
                    database_id,
                    asset_id,
                    [{"filePath": f.key, "versionId": f.versionId}
                     for f in (response.files or []) if f.success],
                    {"downloadType": request_model.downloadType}
                )
            else:
                log_file_download(
                    event,
                    database_id,
                    asset_id,
                    request_model.key if request_model.key else "asset_root",
                    {
                        "downloadType": request_model.downloadType,
                        "versionId": request_model.versionId
                    }
                )

            return success(body=response.dict())
        except VAMSGeneralErrorResponse as e:
            # Extract status code if provided
            status_code = getattr(e, 'status_code', 400)
            return general_error(status_code=status_code, body={'message': str(e)}, event=event)
            
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)}, event=event)
    except ValueError as v:
        logger.exception(f"Value error: {v}")
        return validation_error(body={'message': str(v)}, event=event)
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        # Extract status code if provided
        status_code = getattr(v, 'status_code', 400)
        return general_error(status_code=status_code, body={'message': str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error(event=event)