# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
from concurrent.futures import ThreadPoolExecutor
from boto3.dynamodb.conditions import Key
from botocore.config import Config
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
# Shared download-eligibility checks (asset, key, version pin, delete marker, blocklist).
# is_file_archived and normalize_s3_path keep their historical names in this module.
from common.downloadEligibility import (  # noqa: F401 (is_file_archived, normalize_s3_path)
    check_file_eligibility,
    get_distributable_asset_context as _get_distributable_asset_context,
    is_file_archived,
    normalize_s3_path,
    s3_object_exists,
)
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from customLogging.auditLogging import log_file_download, log_file_download_bulk
from common.s3 import (
    bucket_region_fields,
    region_routing_s3_client,
    validateS3AssetExtensionsAndContentType,
    validateUnallowedFileExtensionAndContentType,
    list_all_objects,
    presigned_get_object_params,
)
from models.common import APIGatewayProxyResponseV2, internal_error, success, validation_error, general_error, authorization_error, VAMSGeneralErrorResponse, validation_error_message
from models.assetsV3 import (
    DownloadAssetRequestModel, DownloadAssetResponseModel, DownloadAssetFileUrlModel
)
from handlers.assets.assetVersions import (
    resolve_file_version_from_asset_version,
    resolve_asset_version_id_from_alias
)

# Configure AWS clients. The S3 client is the Region-routing client from common.s3 (its per-Region
# clients carry S3_ASSET_CLIENT_CONFIG: SigV4, adaptive retries, a connection pool covering the bulk
# worker pool). Adaptive retries add a client-side rate limiter so a burst of calls degrades to
# slower-but-successful instead of surfacing throttling as per-file failures.
region = os.environ['AWS_REGION']
s3 = region_routing_s3_client()
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
dynamodb = boto3.resource('dynamodb', config=retry_config)
logger = safeLogger(service_name="DownloadAsset")

# Worker pool size for per-file S3 checks and URL generation in bulk requests and
# for the per-object checks of a whole-asset prefix download.
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
    from common.resourceNames import ResourceKeys, get_table_name
    s3_asset_buckets_table = get_table_name(ResourceKeys.S3_ASSET_BUCKETS_STORAGE_TABLE)
    asset_storage_table_name = get_table_name(ResourceKeys.ASSET_STORAGE_TABLE)
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
            'baseAssetsPrefix': base_assets_prefix,
            **bucket_region_fields(bucket)
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

def check_s3_object_exists(bucket, key, version_id=None):
    """Check if S3 object exists
    
    Args:
        bucket: S3 bucket name
        key: S3 object key
        version_id: Optional version ID to check
        
    Returns:
        True if object exists, False otherwise
    """
    return s3_object_exists(s3, bucket, key, version_id)

#######################
# Core Download Logic
#######################

def get_distributable_asset_context(databaseId, assetId):
    """Get the asset's bucket and base key, verifying the asset is downloadable"""
    return _get_distributable_asset_context(
        databaseId, assetId,
        get_asset=get_asset_details,
        get_bucket_details=get_default_bucket_details
    )

def resolve_and_sign_file_key(databaseId, assetId, asset_bucket, asset_base_key, raw_key,
                              version_id=None, asset_version_id=None, asset_version_alias=None):
    """Resolve one file key against the asset and generate its presigned URL

    The eligibility checks (key containment and length, asset-version pin,
    existence, delete marker, blocklist) run in common.downloadEligibility with
    this module's clients and helpers; signing stays here.

    Returns:
        (downloadUrl, versionId) tuple

    Raises:
        VAMSGeneralErrorResponse for per-file failures (missing, archived, disallowed
        type, key outside the asset's own prefix, resolved key over the S3 key limit)
    """
    final_key, version_id = check_file_eligibility(
        databaseId, assetId, s3, asset_bucket, asset_base_key, raw_key,
        version_id=version_id,
        asset_version_id=asset_version_id,
        asset_version_alias=asset_version_alias,
        resolve_file_version=resolve_file_version_from_asset_version,
        resolve_alias=resolve_asset_version_id_from_alias,
        max_workers=MAX_PARALLEL_S3_WORKERS,
        list_objects=list_all_objects,
        is_allowed=validateUnallowedFileExtensionAndContentType,
        object_exists=check_s3_object_exists
    )

    # Generate presigned URL
    try:
        params = {
            'Bucket': asset_bucket,
            'Key': final_key
        }

        if version_id:
            params['VersionId'] = version_id

        # SVG keys get the image/svg+xml type and an attachment disposition (common.s3)
        url = s3.generate_presigned_url(
            'get_object',
            Params=presigned_get_object_params(params),
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
        # SVG keys get the image/svg+xml type and an attachment disposition (common.s3)
        url = s3.generate_presigned_url(
            'get_object',
            Params=presigned_get_object_params({
                'Bucket': preview_bucket,
                'Key': preview_key
            }),
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
            return validation_error(body={'message': validation_error_message(v)}, event=event)
        
        # Check authorization
        asset = get_asset_details(database_id, asset_id)
        if not asset:
            return validation_error(body={'message': "Asset not found"}, event=event)
        
        asset["object__type"] = "asset"

        # Fail closed: with no authenticated identity no authorization can be
        # evaluated, so deny rather than fall through to presigned-URL generation.
        if len(claims_and_roles["tokens"]) == 0:
            return authorization_error()

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
        return validation_error(body={'message': validation_error_message(v)}, event=event)
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