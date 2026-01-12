# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
import gzip
import base64
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeDeserializer
from botocore.exceptions import ClientError
from botocore.config import Config
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from models.common import APIGatewayProxyResponseV2, internal_error, success, validation_error, general_error, authorization_error, VAMSGeneralErrorResponse
from models.assetExport import (
    AssetExportRequestModel,
    AssetExportResponseModel,
    AssetExportAssetModel,
    AssetExportUnauthorizedAssetModel,
    AssetExportRelationshipModel,
    AssetExportFileModel,
    AssetExportMetadataItemModel
)

# Configure AWS clients with retry configuration
region = os.environ.get('AWS_REGION', 'us-east-1')

# Set environment variable for S3 client configuration
os.environ["AWS_S3_US_EAST_1_REGIONAL_ENDPOINT"] = "regional"

# Standardized retry configuration for all AWS clients
retry_config = Config(
    retries={
        'max_attempts': 5,
        'mode': 'adaptive'
    }
)

s3_config = Config(signature_version='s3v4', s3={'addressing_style': 'path'}, retries={'max_attempts': 5, 'mode': 'adaptive'})
s3_client = boto3.client('s3', region_name=region, config=s3_config)
dynamodb = boto3.resource('dynamodb', config=retry_config)
dynamodb_client = boto3.client('dynamodb', config=retry_config)
lambda_client = boto3.client('lambda', config=retry_config)
logger = safeLogger(service_name="AssetExportService")

# Global variables for claims and roles
claims_and_roles = {}

# Bucket cache for performance optimization
bucket_cache = {}

# Load environment variables
try:
    asset_storage_table_name = os.environ["ASSET_STORAGE_TABLE_NAME"]
    asset_versions_table_name = os.environ["ASSET_VERSIONS_STORAGE_TABLE_NAME"]
    asset_file_versions_table_name = os.environ["ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME"]
    asset_file_metadata_table_name = os.environ["ASSET_FILE_METADATA_STORAGE_TABLE_NAME"]
    file_attribute_table_name = os.environ["FILE_ATTRIBUTE_STORAGE_TABLE_NAME"]
    asset_links_table_name = os.environ["ASSET_LINKS_STORAGE_TABLE_V2_NAME"]
    asset_links_metadata_table_name = os.environ["ASSET_LINKS_METADATA_STORAGE_TABLE_NAME"]
    s3_asset_buckets_table_name = os.environ["S3_ASSET_BUCKETS_STORAGE_TABLE_NAME"]
    asset_links_function_name = os.environ["ASSET_LINKS_FUNCTION_NAME"]
    presigned_url_timeout = os.environ["PRESIGNED_URL_TIMEOUT_SECONDS"]
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

# Initialize DynamoDB tables
asset_table = dynamodb.Table(asset_storage_table_name)
asset_versions_table = dynamodb.Table(asset_versions_table_name)
asset_file_versions_table = dynamodb.Table(asset_file_versions_table_name)
asset_links_table = dynamodb.Table(asset_links_table_name)
asset_links_metadata_table = dynamodb.Table(asset_links_metadata_table_name)
buckets_table = dynamodb.Table(s3_asset_buckets_table_name)

# Constants
COMPRESSION_THRESHOLD = 102400  # 100KB
ALLOWED_PREVIEW_EXTENSIONS = ['.png', '.jpg', '.jpeg', '.svg', '.gif']

#######################
# Utility Functions
#######################

def get_default_bucket_details(bucketId: str) -> Dict:
    """Get default S3 bucket details from database default bucket DynamoDB with caching"""
    global bucket_cache
    
    # Check cache first
    if bucketId in bucket_cache:
        logger.debug(f"Using cached bucket details for {bucketId}")
        return bucket_cache[bucketId]
    
    try:
        bucket_response = buckets_table.query(
            KeyConditionExpression=Key('bucketId').eq(bucketId),
            Limit=1
        )
        bucket = bucket_response.get("Items", [{}])[0] if bucket_response.get("Items") else {}
        bucket_id = bucket.get('bucketId')
        bucket_name = bucket.get('bucketName')
        base_assets_prefix = bucket.get('baseAssetsPrefix')

        if not bucket_name or not base_assets_prefix:
            raise VAMSGeneralErrorResponse("Database configuration invalid")
        
        if not base_assets_prefix.endswith('/'):
            base_assets_prefix += '/'

        if base_assets_prefix.startswith('/'):
            base_assets_prefix = base_assets_prefix[1:]

        bucket_details = {
            'bucketId': bucket_id,
            'bucketName': bucket_name,
            'baseAssetsPrefix': base_assets_prefix
        }
        
        # Cache the result
        bucket_cache[bucketId] = bucket_details
        logger.debug(f"Cached bucket details for {bucketId}")
        
        return bucket_details
    except VAMSGeneralErrorResponse as e:
        raise e
    except Exception as e:
        logger.exception(f"Error getting bucket details: {e}")
        raise VAMSGeneralErrorResponse("Error retrieving bucket configuration")

def get_asset_with_permissions(databaseId: str, assetId: str, operation: str, claims_and_roles: Dict) -> Dict:
    """Get asset and verify permissions for the specified operation"""
    try:
        response = asset_table.get_item(Key={'databaseId': databaseId, 'assetId': assetId})
        asset = response.get('Item', {})
        
        if not asset:
            raise VAMSGeneralErrorResponse("Asset not found")
        
        asset["object__type"] = "asset"
        
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not casbin_enforcer.enforce(asset, operation):
                raise VAMSGeneralErrorResponse("Not Authorized to access asset")
        
        return asset
    except VAMSGeneralErrorResponse as e:
        raise e
    except Exception as e:
        logger.exception(f"Error getting asset with permissions: {e}")
        raise VAMSGeneralErrorResponse("Error retrieving asset")

def create_pagination_token(last_index: int, asset_tree: List[Dict]) -> str:
    """Create pagination token"""
    token_data = {
        'lastAssetIndex': last_index,
        'assetTree': asset_tree,
        'timestamp': datetime.utcnow().isoformat()
    }
    json_str = json.dumps(token_data)
    return base64.b64encode(json_str.encode('utf-8')).decode('utf-8')

def parse_pagination_token(token: str) -> Dict:
    """Parse pagination token"""
    try:
        json_str = base64.b64decode(token.encode('utf-8')).decode('utf-8')
        return json.loads(json_str)
    except Exception as e:
        logger.exception(f"Error parsing pagination token: {e}")
        raise VAMSGeneralErrorResponse("Invalid pagination token format")

def compress_response(response_dict: Dict) -> Dict:
    """Compress response if over threshold"""
    json_str = json.dumps(response_dict)
    
    if len(json_str.encode('utf-8')) > COMPRESSION_THRESHOLD:
        logger.info(f"Compressing response (size: {len(json_str.encode('utf-8'))} bytes)")
        compressed = gzip.compress(json_str.encode('utf-8'))
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/json',
                'Content-Encoding': 'gzip'
            },
            'body': base64.b64encode(compressed).decode('utf-8'),
            'isBase64Encoded': True
        }
    else:
        return {
            'statusCode': 200,
            'headers': {'Content-Type': 'application/json'},
            'body': json_str
        }

#######################
# Asset Tree Functions
#######################

def get_asset_tree_via_lambda(databaseId: str, assetId: str, event: Dict, fetch_entire_subtrees: bool = False) -> Dict:
    """Cross-invoke asset links lambda to get child tree with permissions
    
    Args:
        databaseId: Database ID
        assetId: Asset ID
        event: Lambda event
        fetch_entire_subtrees: If True, fetch full tree. If False, only root + 1 level
    """
    try:
        # Create a proper API Gateway event structure for the asset links lambda
        # The asset links service expects a GET request with path and query parameters
        request_context = event['requestContext'].copy()
        
        # Override the HTTP method to GET (asset links service only handles GET requests)
        request_context['http'] = request_context.get('http', {}).copy()
        request_context['http']['method'] = 'GET'
        request_context['http']['path'] = f"/database/{databaseId}/assets/{assetId}/links"
        
        payload = {
            "pathParameters": {
                "databaseId": databaseId,
                "assetId": assetId
            },
            "queryStringParameters": {
                "childTreeView": "true" if fetch_entire_subtrees else "false"
            },
            "requestContext": request_context
        }
        
        logger.info(f"Invoking asset links service for tree retrieval (full_tree={fetch_entire_subtrees})")
        logger.info(f"Lambda function name: {asset_links_function_name}")
        logger.info(f"Payload: {json.dumps(payload)}")
        
        response = lambda_client.invoke(
            FunctionName=asset_links_function_name,
            InvocationType='RequestResponse',
            Payload=json.dumps(payload).encode('utf-8')
        )
        
        # Log the raw response for debugging
        logger.info(f"Lambda invocation response status: {response.get('StatusCode')}")
        
        # Check for function errors
        if response.get('FunctionError'):
            logger.error(f"Lambda function error: {response.get('FunctionError')}")
            logger.error(f"Full response: {response}")
        
        stream = response.get('Payload', "")
        if not stream:
            logger.error("No payload in lambda response")
            raise VAMSGeneralErrorResponse("Error retrieving asset relationships: No response payload")
        
        # Read and parse the response
        response_str = stream.read().decode("utf-8")
        logger.info(f"Lambda response payload: {response_str}")
        
        json_response = json.loads(response_str)
        status_code = json_response.get('statusCode')
        
        if status_code == 200:
            body = json.loads(json_response['body'])
            logger.info(f"Successfully retrieved asset tree with {len(body.get('children', []))} children")
            return body
        else:
            # Log detailed error information
            error_body = json_response.get('body', '')
            logger.error(f"Asset links service returned status {status_code}")
            logger.error(f"Error response body: {error_body}")
            
            # Try to parse error message from body
            try:
                error_details = json.loads(error_body) if isinstance(error_body, str) else error_body
                error_message = error_details.get('message', 'Unknown error')
                logger.error(f"Error message: {error_message}")
                raise VAMSGeneralErrorResponse(f"Error retrieving asset relationships")
            except:
                raise VAMSGeneralErrorResponse(f"Error retrieving asset relationships")
        
    except VAMSGeneralErrorResponse as e:
        raise e
    except json.JSONDecodeError as e:
        logger.exception(f"Failed to parse lambda response JSON: {e}")
        raise VAMSGeneralErrorResponse("Error retrieving asset relationships")
    except Exception as e:
        logger.exception(f"Error invoking asset links service: {e}")
        raise VAMSGeneralErrorResponse(f"Error retrieving asset relationships")

def flatten_tree_to_list(tree_data: Dict, root_asset_id: str, root_database_id: str) -> List[Dict]:
    """Flatten tree structure to list of asset identifiers"""
    asset_list = []
    
    # Add root asset first
    asset_list.append({
        'assetId': root_asset_id,
        'databaseId': root_database_id,
        'isRoot': True
    })
    
    # Recursively add children
    def add_children(children_list: List[Dict]):
        for child in children_list:
            asset_list.append({
                'assetId': child['assetId'],
                'databaseId': child['databaseId'],
                'isRoot': False
            })
            if child.get('children'):
                add_children(child['children'])
    
    # Process children from tree
    if tree_data.get('children'):
        add_children(tree_data['children'])
    
    return asset_list

#######################
# Data Retrieval Functions
#######################

def batch_get_assets(asset_identifiers: List[Dict]) -> Dict[str, Dict]:
    """Batch get asset details for multiple assets"""
    asset_details = {}
    
    # Process in batches of 100 (DynamoDB batch_get_item limit)
    batch_size = 100
    for i in range(0, len(asset_identifiers), batch_size):
        batch = asset_identifiers[i:i + batch_size]
        
        try:
            request_items = {
                asset_storage_table_name: {
                    'Keys': [
                        {
                            'databaseId': item['databaseId'],
                            'assetId': item['assetId']
                        }
                        for item in batch
                    ]
                }
            }
            
            response = dynamodb.batch_get_item(RequestItems=request_items)
            
            for item in response.get('Responses', {}).get(asset_storage_table_name, []):
                key = f"{item['databaseId']}:{item['assetId']}"
                asset_details[key] = item
                
        except Exception as e:
            logger.exception(f"Error in batch get assets: {e}")
            # Fall back to individual gets for this batch
            for item in batch:
                try:
                    response = asset_table.get_item(
                        Key={'databaseId': item['databaseId'], 'assetId': item['assetId']}
                    )
                    if 'Item' in response:
                        key = f"{item['databaseId']}:{item['assetId']}"
                        asset_details[key] = response['Item']
                except Exception as inner_e:
                    logger.warning(f"Error getting asset {item['assetId']}: {inner_e}")
    
    return asset_details

def get_asset_metadata(databaseId: str, assetId: str) -> Dict:
    """Get asset-level metadata using new table structure"""
    try:
        # Composite key for asset metadata: databaseId:assetId:/
        composite_key = f"{databaseId}:{assetId}:/"
        
        # Query using GSI
        response = dynamodb_client.query(
            TableName=asset_file_metadata_table_name,
            IndexName='DatabaseIdAssetIdFilePathIndex',
            KeyConditionExpression='#pk = :pkValue',
            ExpressionAttributeNames={'#pk': 'databaseId:assetId:filePath'},
            ExpressionAttributeValues={':pkValue': {'S': composite_key}}
        )
        
        # Deserialize and convert to dict
        metadata = {}
        deserializer = TypeDeserializer()
        for item in response.get('Items', []):
            deserialized = {k: deserializer.deserialize(v) for k, v in item.items()}
            # Store as key-value pairs
            metadata[deserialized['metadataKey']] = deserialized['metadataValue']
        
        return metadata
    except Exception as e:
        logger.warning(f"Error getting asset metadata for {assetId}: {e}")
        return {}

def get_file_metadata(databaseId: str, assetId: str, relative_path: str) -> Dict:
    """Get file-specific metadata using new table structure"""
    try:
        # Ensure relative_path starts with /
        if not relative_path.startswith('/'):
            relative_path = '/' + relative_path
        
        # Composite key for file metadata: databaseId:assetId:/path/to/file
        composite_key = f"{databaseId}:{assetId}:{relative_path}"
        
        # Query using GSI
        response = dynamodb_client.query(
            TableName=asset_file_metadata_table_name,
            IndexName='DatabaseIdAssetIdFilePathIndex',
            KeyConditionExpression='#pk = :pkValue',
            ExpressionAttributeNames={'#pk': 'databaseId:assetId:filePath'},
            ExpressionAttributeValues={':pkValue': {'S': composite_key}}
        )
        
        # Deserialize and convert to dict
        metadata = {}
        deserializer = TypeDeserializer()
        for item in response.get('Items', []):
            deserialized = {k: deserializer.deserialize(v) for k, v in item.items()}
            # Store as key-value pairs
            metadata[deserialized['metadataKey']] = deserialized['metadataValue']
        
        return metadata
    except Exception as e:
        logger.warning(f"Error getting file metadata for {relative_path}: {e}")
        return {}


def get_file_attributes(databaseId: str, assetId: str, relative_path: str) -> Dict:
    """Get file attributes using new file attributes table"""
    try:
        # Ensure relative_path starts with /
        if not relative_path.startswith('/'):
            relative_path = '/' + relative_path
        
        # Composite key for file attributes: databaseId:assetId:/path/to/file
        composite_key = f"{databaseId}:{assetId}:{relative_path}"
        
        # Query using GSI
        response = dynamodb_client.query(
            TableName=file_attribute_table_name,
            IndexName='DatabaseIdAssetIdFilePathIndex',
            KeyConditionExpression='#pk = :pkValue',
            ExpressionAttributeNames={'#pk': 'databaseId:assetId:filePath'},
            ExpressionAttributeValues={':pkValue': {'S': composite_key}}
        )
        
        # Deserialize and convert to dict
        attributes = {}
        deserializer = TypeDeserializer()
        for item in response.get('Items', []):
            deserialized = {k: deserializer.deserialize(v) for k, v in item.items()}
            # Handle both attributeKey and metadataKey field names for compatibility
            key = deserialized.get('attributeKey', deserialized.get('metadataKey'))
            value = deserialized.get('attributeValue', deserialized.get('metadataValue'))
            if key:
                attributes[key] = value
        
        return attributes
    except Exception as e:
        logger.warning(f"Error getting file attributes for {relative_path}: {e}")
        return {}

def get_asset_version_info(assetId: str, versionId: str) -> Optional[Dict]:
    """Get asset version information"""
    try:
        response = asset_versions_table.get_item(
            Key={
                'assetId': assetId,
                'assetVersionId': versionId
            }
        )
        
        return response.get('Item')
    except Exception as e:
        logger.warning(f"Error getting version info for {assetId} version {versionId}: {e}")
        return None

def get_asset_file_versions(assetId: str, assetVersionId: str) -> Optional[Dict]:
    """Get file versions for a specific asset version"""
    try:
        partition_key = f"{assetId}:{assetVersionId}"
        
        response = asset_file_versions_table.query(
            KeyConditionExpression=Key('assetId:assetVersionId').eq(partition_key)
        )
        
        items = response.get('Items', [])
        
        if not items:
            return None
        
        files = []
        for item in items:
            file_info = {
                'relativeKey': item.get('fileKey'),
                'versionId': item.get('versionId'),
                'size': item.get('size'),
                'lastModified': item.get('lastModified'),
                'etag': item.get('etag')
            }
            files.append(file_info)
        
        return {
            'assetId': assetId,
            'assetVersionId': assetVersionId,
            'files': files,
            'createdAt': items[0].get('createdAt') if items else datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logger.exception(f"Error getting asset file versions: {e}")
        return None

def list_s3_files(bucket: str, prefix: str) -> List[Dict]:
    """List all files in S3 bucket prefix"""
    files = []
    
    try:
        if not prefix.endswith('/'):
            prefix = prefix + '/'
            
        paginator = s3_client.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get('Contents', []):
                file_name = os.path.basename(obj['Key'])
                is_folder = obj['Key'].endswith('/')
                
                relative_path = obj['Key']
                if relative_path.startswith(prefix):
                    relative_path = relative_path[len(prefix):]
                    if not relative_path.startswith('/'):
                        relative_path = '/' + relative_path
                
                item = {
                    'fileName': file_name,
                    'key': obj['Key'],
                    'relativePath': relative_path,
                    'isFolder': is_folder,
                    'dateCreatedCurrentVersion': obj['LastModified'].isoformat(),
                    'storageClass': obj.get('StorageClass', 'STANDARD')
                }
                
                if not is_folder:
                    item['size'] = obj['Size']
                
                # Get version ID
                try:
                    version_info = s3_client.head_object(Bucket=bucket, Key=obj['Key'])
                    item['versionId'] = version_info.get('VersionId', 'null')
                    item['isArchived'] = False
                    
                    # Get primaryType from metadata
                    metadata = version_info.get('Metadata', {})
                    primary_type = metadata.get('vams-primarytype', '')
                    item['primaryType'] = primary_type if primary_type else None
                    
                except Exception as e:
                    logger.warning(f"Error getting version info for {obj['Key']}: {e}")
                    item['versionId'] = 'null'
                    item['isArchived'] = False
                    item['primaryType'] = None
                
                files.append(item)
                    
    except Exception as e:
        logger.exception(f"Error listing S3 files: {e}")
    
    return files

def generate_presigned_url(bucket: str, key: str, version_id: str) -> Optional[str]:
    """Generate presigned URL for file download"""
    try:
        params = {
            'Bucket': bucket,
            'Key': key
        }
        
        if version_id and version_id != 'null':
            params['VersionId'] = version_id
            
        url = s3_client.generate_presigned_url(
            'get_object',
            Params=params,
            ExpiresIn=int(presigned_url_timeout)
        )
        
        return url
    except Exception as e:
        logger.warning(f"Error generating presigned URL for {key}: {e}")
        return None

def get_asset_link_metadata(assetLinkId: str) -> Dict:
    """Get all metadata for an asset link"""
    try:
        response = asset_links_metadata_table.query(
            KeyConditionExpression=Key('assetLinkId').eq(assetLinkId)
        )
        
        metadata = {}
        for item in response.get('Items', []):
            metadata[item['metadataKey']] = {
                'valueType': item['metadataValueType'],
                'value': item['metadataValue']
            }
        
        return metadata
    except Exception as e:
        logger.warning(f"Error getting asset link metadata for {assetLinkId}: {e}")
        return {}

def is_preview_file(file_path: str) -> bool:
    """Determine if a file is a preview file based on its path"""
    return '.previewFile.' in file_path

def get_base_file_for_preview(preview_file_path: str) -> str:
    """Get the base file path for a preview file"""
    return preview_file_path.split('.previewFile.')[0]

def is_allowed_preview_extension(file_path: str) -> bool:
    """Check if a preview file has an allowed extension"""
    if '.previewFile.' in file_path:
        extension = '.' + file_path.split('.previewFile.')[1]
        return extension.lower() in ALLOWED_PREVIEW_EXTENSIONS
    return False

def get_top_preview_file(preview_files: List[str], filter_extensions: bool = True) -> Optional[str]:
    """Get the top preview file from a list of preview files
    
    Args:
        preview_files: List of preview file keys
        filter_extensions: Whether to filter by allowed extensions
        
    Returns:
        The top preview file key or None if no valid preview files
    """
    if not preview_files:
        return None
    
    if filter_extensions:
        # Filter by allowed extensions
        allowed_files = [f for f in preview_files if is_allowed_preview_extension(f)]
        if allowed_files:
            return allowed_files[0]  # Return the first allowed file
        return None
    else:
        # Return the first file without filtering
        return preview_files[0]

#######################
# Main Export Logic
#######################

def apply_file_filters(files: List[Dict], request_model: AssetExportRequestModel) -> List[Dict]:
    """Apply filtering to files based on request parameters"""
    filtered_files = []
    
    for file in files:
        # Filter 1: Exclude folders if not requested
        if file['isFolder'] and not request_model.includeFolderFiles:
            continue
        
        # Filter 2: Include only files with primaryType if requested
        if request_model.includeOnlyPrimaryTypeFiles and not file.get('primaryType'):
            continue
        
        # Filter 3: Filter by extensions if provided
        if request_model.fileExtensions and not file['isFolder']:
            file_ext = os.path.splitext(file['fileName'])[1].lower()
            if file_ext not in [ext.lower() if ext.startswith('.') else f".{ext.lower()}" for ext in request_model.fileExtensions]:
                continue
        
        # Filter 4: Exclude archived files unless explicitly requested
        if file.get('isArchived', False) and not request_model.includeArchivedFiles:
            continue
        
        filtered_files.append(file)
    
    return filtered_files

def process_asset_batch(
    asset_identifiers: List[Dict],
    request_model: AssetExportRequestModel,
    claims_and_roles: Dict
) -> List[Dict]:
    """Process a batch of assets and gather all related data"""
    
    # Step 1: Batch get all asset details
    logger.info(f"Processing batch of {len(asset_identifiers)} assets")
    asset_details = batch_get_assets(asset_identifiers)
    
    processed_assets = []
    
    for asset_info in asset_identifiers:
        asset_key = f"{asset_info['databaseId']}:{asset_info['assetId']}"
        asset = asset_details.get(asset_key)
        
        if not asset:
            logger.warning(f"Asset not found: {asset_info['assetId']}")
            continue
        
        try:
            # Check permissions for this asset
            asset["object__type"] = "asset"
            if len(claims_and_roles["tokens"]) > 0:
                casbin_enforcer = CasbinEnforcer(claims_and_roles)
                if not casbin_enforcer.enforce(asset, "GET"):
                    # Permission denied - add unauthorized placeholder
                    logger.warning(f"Permission denied for asset {asset_info['assetId']}")
                    unauthorized_asset = {
                        'assetId': asset_info['assetId'],
                        'databaseId': asset_info['databaseId'],
                        'unauthorizedAsset': True
                    }
                    processed_assets.append(unauthorized_asset)
                    continue
            
            # Get bucket details
            bucket_details = get_default_bucket_details(asset['bucketId'])
            bucket_name = bucket_details['bucketName']
            bucket_prefix = bucket_details['baseAssetsPrefix']
            
            # Get version info
            current_version_id = asset.get('currentVersionId', '0')
            version_info = get_asset_version_info(asset['assetId'], current_version_id)
            
            # Get asset metadata if requested
            asset_metadata = {}
            if request_model.includeAssetMetadata:
                raw_metadata = get_asset_metadata(asset_info['databaseId'], asset_info['assetId'])
                # Convert to export format (all as string type per requirement #8)
                for key, value in raw_metadata.items():
                    if not key.startswith('_'):  # Skip private keys
                        asset_metadata[key] = {
                            'valueType': 'string',
                            'value': str(value)
                        }
            
            # Get files
            asset_location_key = asset.get('assetLocation', {}).get('Key', '')
            files = list_s3_files(bucket_name, asset_location_key)
            
            # Apply filters
            filtered_files = apply_file_filters(files, request_model)
            
            # Get file versions for version mismatch check
            file_versions = get_asset_file_versions(asset['assetId'], current_version_id)
            file_version_lookup = {}
            if file_versions and file_versions.get('files'):
                for fv in file_versions['files']:
                    file_version_lookup[fv['relativeKey']] = fv
            
            # Separate preview files from base files
            preview_files_list = []
            base_files_list = []
            
            for file in filtered_files:
                if is_preview_file(file['key']):
                    preview_files_list.append(file)
                else:
                    base_files_list.append(file)
            
            # Create lookup for preview files by base file key
            preview_lookup = {}
            for preview_file in preview_files_list:
                base_key = get_base_file_for_preview(preview_file['key'])
                if base_key not in preview_lookup:
                    preview_lookup[base_key] = []
                preview_lookup[base_key].append(preview_file['key'])
            
            # Process each base file
            export_files = []
            for file in base_files_list:
                # Check version mismatch
                relative_key = file['relativePath'].lstrip('/')
                matching_version = file_version_lookup.get(relative_key)
                
                if file['isFolder']:
                    version_mismatch = False
                elif file.get('isArchived'):
                    version_mismatch = True
                elif matching_version and matching_version.get('versionId') == file['versionId']:
                    version_mismatch = False
                else:
                    version_mismatch = True
                
                file['currentAssetVersionFileVersionMismatch'] = version_mismatch
                
                # Get file metadata if requested - always return {} if no metadata
                file_metadata = {}
                file_attributes = {}
                if request_model.includeFileMetadata and not file['isFolder']:
                    # Get file metadata
                    raw_file_metadata = get_file_metadata(
                        asset_info['databaseId'],
                        asset_info['assetId'],
                        file['relativePath']
                    )
                    if raw_file_metadata:
                        for key, value in raw_file_metadata.items():
                            if not key.startswith('_'):
                                file_metadata[key] = {
                                    'valueType': 'string',
                                    'value': str(value)
                                }
                    
                    # Get file attributes separately
                    raw_file_attributes = get_file_attributes(
                        asset_info['databaseId'],
                        asset_info['assetId'],
                        file['relativePath']
                    )
                    if raw_file_attributes:
                        for key, value in raw_file_attributes.items():
                            if not key.startswith('_'):
                                file_attributes[key] = {
                                    'valueType': 'string',
                                    'value': str(value)
                                }
                
                # Generate presigned URL if requested (skip for archived files)
                presigned_url = None
                presigned_expires = None
                if request_model.generatePresignedUrls and not file['isFolder'] and not file.get('isArchived', False):
                    presigned_url = generate_presigned_url(
                        bucket_name,
                        file['key'],
                        file['versionId']
                    )
                    if presigned_url:
                        presigned_expires = int(presigned_url_timeout)
                
                # Find preview file for this base file
                preview_file_path = ''
                if not file['isFolder']:
                    # Get preview files for this base file
                    base_file_key = file['key']
                    preview_files_for_base = preview_lookup.get(base_file_key, [])
                    
                    # Get the top preview file with allowed extension
                    top_preview = get_top_preview_file(preview_files_for_base, filter_extensions=True)
                    
                    if top_preview:
                        # Convert to relative path
                        if top_preview.startswith(asset_location_key):
                            preview_relative = top_preview[len(asset_location_key):]
                            if not preview_relative.startswith('/'):
                                preview_relative = '/' + preview_relative
                            preview_file_path = preview_relative
                
                # Build export file model
                export_file = {
                    'fileName': file['fileName'],
                    'key': file['key'],
                    'relativePath': file['relativePath'],
                    'isFolder': file['isFolder'],
                    'size': file.get('size'),
                    'dateCreatedCurrentVersion': file['dateCreatedCurrentVersion'],
                    'versionId': file['versionId'],
                    'storageClass': file['storageClass'],
                    'isArchived': file.get('isArchived', False),
                    'currentAssetVersionFileVersionMismatch': version_mismatch,
                    'primaryType': file.get('primaryType'),
                    'previewFile': preview_file_path,
                    'metadata': file_metadata,
                    'attributes': file_attributes,
                    'presignedFileDownloadUrl': presigned_url,
                    'presignedFileDownloadExpiresIn': presigned_expires
                }
                
                export_files.append(export_file)
            
            # Build export asset model
            export_asset = {
                'is_root_lookup_asset': asset_info.get('isRoot', False),
                'id': asset['assetId'],
                'databaseid': asset['databaseId'],
                'assetid': asset['assetId'],
                'bucketid': asset['bucketId'],
                'assetname': asset.get('assetName', ''),
                'bucketname': bucket_name,
                'bucketprefix': bucket_prefix,
                'assettype': asset.get('assetType', 'none'),
                'description': asset.get('description', ''),
                'isdistributable': asset.get('isDistributable', False),
                'tags': asset.get('tags', []),
                'asset_version_id': current_version_id,
                'asset_version_createdate': version_info.get('dateCreated', '') if version_info else '',
                'asset_version_comment': version_info.get('comment', '') if version_info else '',
                'archived': asset.get('status') == 'archived',
                'metadata': asset_metadata,  # Always a dict, either populated or {}
                'files': export_files
            }
            
            processed_assets.append(export_asset)
            
        except Exception as e:
            logger.exception(f"Error processing asset {asset_info['assetId']}: {e}")
            # Continue with other assets
            continue
    
    return processed_assets

def export_assets(
    databaseId: str,
    assetId: str,
    request_model: AssetExportRequestModel,
    claims_and_roles: Dict,
    event: Dict
) -> Dict:
    """Main export function with pagination support"""
    
    is_first_page = request_model.startingToken is None
    
    # SINGLE ASSET MODE: Skip relationship fetching entirely
    if not request_model.fetchAssetRelationships:
        logger.info("Single asset mode - skipping relationship fetching")
        
        # Only process the root asset
        asset_tree = [{
            'assetId': assetId,
            'databaseId': databaseId,
            'isRoot': True
        }]
        
        # Process single asset
        assets = process_asset_batch(asset_tree, request_model, claims_and_roles)
        
        return {
            'assets': assets,
            'relationships': None,
            'NextToken': None,
            'totalAssetsInTree': 1,
            'assetsInThisPage': len(assets)
        }
    
    if is_first_page:
        # FIRST PAGE: Get tree and relationships
        logger.info("First page request - retrieving asset tree")
        
        # Get tree via lambda (handles permissions)
        # Pass fetchEntireChildrenSubtrees to control tree depth
        tree_data = get_asset_tree_via_lambda(
            databaseId, 
            assetId, 
            event, 
            fetch_entire_subtrees=request_model.fetchEntireChildrenSubtrees
        )
        
        # Flatten tree to list
        asset_tree = flatten_tree_to_list(tree_data, assetId, databaseId)
        
        logger.info(f"Asset tree contains {len(asset_tree)} assets")
        
        # Extract relationships
        relationships = []
        if request_model.includeAssetLinkMetadata:
            # Get relationships from tree children
            def extract_rels(parent_id, parent_db_id, children):
                for child in children:
                    rel = {
                        'parentAssetId': parent_id,
                        'parentAssetDatabaseId': parent_db_id,
                        'childAssetId': child['assetId'],
                        'childAssetDatabaseId': child['databaseId'],
                        'assetLinkType': 'parentChild',
                        'assetLinkId': child.get('assetLinkId', ''),
                        'assetLinkAliasId': child.get('assetLinkAliasId')
                    }
                    
                    # Get link metadata
                    if child.get('assetLinkId'):
                        link_metadata = get_asset_link_metadata(child['assetLinkId'])
                        if link_metadata:
                            rel['metadata'] = link_metadata
                    
                    relationships.append(rel)
                    
                    if child.get('children'):
                        extract_rels(child['assetId'], child['databaseId'], child['children'])
            
            if tree_data.get('children'):
                extract_rels(assetId, databaseId, tree_data['children'])
            
            # Filter out parent relationships if includeParentRelationships is False
            # Use getattr with default for backwards compatibility
            include_parent_rels = getattr(request_model, 'includeParentRelationships', False)
            if not include_parent_rels:
                # Remove relationships where the root asset is the child (i.e., parent relationships)
                relationships = [
                    rel for rel in relationships 
                    if not (rel['childAssetId'] == assetId and rel['childAssetDatabaseId'] == databaseId)
                ]
                logger.info(f"Filtered parent relationships. Remaining relationships: {len(relationships)}")
        
        # Process first batch
        start_idx = 0
        end_idx = min(request_model.maxAssets, len(asset_tree))
        
    else:
        # SUBSEQUENT PAGE: Use stored tree from token
        logger.info("Subsequent page request - using stored tree")
        token_data = parse_pagination_token(request_model.startingToken)
        asset_tree = token_data['assetTree']
        start_idx = token_data['lastAssetIndex'] + 1
        end_idx = min(start_idx + request_model.maxAssets, len(asset_tree))
        relationships = None
    
    # Process assets in current batch
    batch_asset_ids = asset_tree[start_idx:end_idx]
    assets = process_asset_batch(batch_asset_ids, request_model, claims_and_roles)
    
    # Create next token if more assets remain
    next_token = None
    if end_idx < len(asset_tree):
        next_token = create_pagination_token(end_idx - 1, asset_tree)
    
    # Build response
    response = {
        'assets': assets,
        'totalAssetsInTree': len(asset_tree),
        'assetsInThisPage': len(assets)
    }
    
    if is_first_page:
        response['relationships'] = relationships
    
    if next_token:
        response['NextToken'] = next_token
    
    return response

#######################
# Request Handlers
#######################

def handle_post_export(event, context) -> APIGatewayProxyResponseV2:
    """Handle POST /export requests"""
    try:
        # Get path parameters
        path_params = event.get('pathParameters', {})
        if 'databaseId' not in path_params:
            return validation_error(body={'message': "No database ID in API Call"})
        
        if 'assetId' not in path_params:
            return validation_error(body={'message': "No asset ID in API Call"})
        
        # Validate path parameters
        (valid, message) = validate({
            'databaseId': {
                'value': path_params['databaseId'],
                'validator': 'ID'
            },
            'assetId': {
                'value': path_params['assetId'],
                'validator': 'ASSET_ID'
            },
        })
        
        if not valid:
            return validation_error(body={'message': message})
        
        # Parse request body with enhanced error handling (Pattern 2: Optional Body)
        body = event.get('body', {})
        
        # If body exists, parse it safely
        if body:
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
                logger.error("Request body is not a string or dict")
                return validation_error(body={'message': "Request body cannot be parsed"})
        
        # Now body is always a dict (either parsed or empty)
        # Parse request model (works with both empty and populated body)
        request_model = parse(body, model=AssetExportRequestModel)
        
        # Verify root asset permissions
        get_asset_with_permissions(
            path_params['databaseId'],
            path_params['assetId'],
            "GET",
            claims_and_roles
        )
        
        # Process export
        response_data = export_assets(
            path_params['databaseId'],
            path_params['assetId'],
            request_model,
            claims_and_roles,
            event
        )
        
        # Apply compression if needed
        return compress_response(response_data)
    
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)})
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)})
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error()

#######################
# Lambda Handler
#######################

def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for asset export operations"""
    global claims_and_roles, bucket_cache
    claims_and_roles = request_to_claims(event)
    
    # Clear bucket cache for each new request
    bucket_cache = {}
    
    try:
        # Get API path and method
        path = event['requestContext']['http']['path']
        method = event['requestContext']['http']['method']
        
        # Check API authorization
        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True
        
        if not method_allowed_on_api:
            return authorization_error()
        
        # Route to appropriate handler based on path pattern
        if method == 'POST' and '/export' in path:
            return handle_post_export(event, context)
        else:
            return validation_error(body={'message': "Invalid API path or method"})
    
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)})
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)})
    except Exception as e:
        logger.exception(f"Unhandled error in lambda_handler: {e}")
        return internal_error()
