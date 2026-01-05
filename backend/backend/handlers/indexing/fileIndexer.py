"""
File indexer for VAMS dual-index OpenSearch system.
Handles indexing of S3 files with full data lookups from multiple sources.

Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
SPDX-License-Identifier: Apache-2.0
"""

import os
import boto3
import json
import time
import random
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple, Callable
from boto3.dynamodb.conditions import Key
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
from models.indexing import FileDocumentModel, FileIndexRequest, IndexOperationResponse

# Configure AWS clients with retry configuration
retry_config = Config(
    retries={
        'max_attempts': 5,
        'mode': 'adaptive'
    }
)

#Excluded patterns or prefixes from file paths to exclude
excluded_prefixes = ['pipeline', 'pipelines', 'preview', 'previews', 'temp-upload', 'temp-uploads']
excluded_patterns = ['.previewFile.']

dynamodb = boto3.resource('dynamodb', config=retry_config)
s3_client = boto3.client('s3', config=retry_config)
opensearch_client = boto3.client('opensearchserverless', config=retry_config) if os.environ.get('OPENSEARCH_TYPE') == 'serverless' else boto3.client('opensearch', config=retry_config)
logger = safeLogger(service_name="FileIndexer")

# Global variables for claims and roles
claims_and_roles = {}

# Load environment variables with error handling
try:
    asset_storage_table_name = os.environ["ASSET_STORAGE_TABLE_NAME"]
    asset_file_metadata_table_name = os.environ["ASSET_FILE_METADATA_STORAGE_TABLE_NAME"]
    file_attribute_table_name = os.environ["FILE_ATTRIBUTE_STORAGE_TABLE_NAME"]
    s3_asset_buckets_table_name = os.environ["S3_ASSET_BUCKETS_STORAGE_TABLE_NAME"]
    opensearch_file_index_ssm_param = os.environ["OPENSEARCH_FILE_INDEX_SSM_PARAM"]
    opensearch_endpoint_ssm_param = os.environ["OPENSEARCH_ENDPOINT_SSM_PARAM"]
    opensearch_type = os.environ.get("OPENSEARCH_TYPE", "serverless")
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

# Get SSM parameter values
def get_ssm_parameter_value(parameter_name: str) -> str:
    """Get SSM parameter value"""
    try:
        ssm_client = boto3.client('ssm', config=retry_config)
        response = ssm_client.get_parameter(Name=parameter_name)
        return response['Parameter']['Value']
    except Exception as e:
        logger.exception(f"Error getting SSM parameter {parameter_name}: {e}")
        raise VAMSGeneralErrorResponse(f"Error getting configuration parameter: {parameter_name}")

# Load OpenSearch configuration from SSM
opensearch_file_index = get_ssm_parameter_value(opensearch_file_index_ssm_param)
opensearch_endpoint = get_ssm_parameter_value(opensearch_endpoint_ssm_param)

# Initialize DynamoDB tables
asset_storage_table = dynamodb.Table(asset_storage_table_name)
asset_file_metadata_table = dynamodb.Table(asset_file_metadata_table_name)
file_attribute_table = dynamodb.Table(file_attribute_table_name)
s3_asset_buckets_table = dynamodb.Table(s3_asset_buckets_table_name)

#######################
# OpenSearch Client Management
#######################

class FileIndexOpenSearchManager:
    """Singleton OpenSearch client manager for file indexing operations"""
    
    _instance = None
    _client = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(FileIndexOpenSearchManager, cls).__new__(cls)
        return cls._instance
    
    def __init__(self):
        if self._client is None:
            self._initialize_client()
    
    def _initialize_client(self):
        """Initialize OpenSearch client with connection pooling"""
        try:
            from opensearchpy import OpenSearch, RequestsHttpConnection, AWSV4SignerAuth
            
            # Create OpenSearch client
            host = opensearch_endpoint.replace('https://', '').replace('http://', '')
            region = os.environ.get('AWS_REGION', 'us-east-1')
            service = 'aoss' if opensearch_type == 'serverless' else 'es'
            
            # Use AWSV4SignerAuth which uses boto3 credentials automatically
            credentials = boto3.Session().get_credentials()
            awsauth = AWSV4SignerAuth(credentials, region, service)
            
            self._client = OpenSearch(
                hosts=[{'host': host, 'port': 443}],
                http_auth=awsauth,
                use_ssl=True,
                verify_certs=True,
                connection_class=RequestsHttpConnection,
                pool_maxsize=20,
                timeout=30,
                max_retries=3,
                retry_on_timeout=True
            )
            
            logger.info(f"Initialized file index OpenSearch client for index: {opensearch_file_index}")
        except Exception as e:
            logger.exception(f"Failed to initialize OpenSearch client: {e}")
            raise VAMSGeneralErrorResponse("Failed to initialize search service")
    
    def get_client(self):
        """Get the OpenSearch client instance"""
        if self._client is None:
            self._initialize_client()
        return self._client
    
    def is_available(self) -> bool:
        """Check if OpenSearch client is available"""
        return self._client is not None

# Global client manager instance
opensearch_manager = FileIndexOpenSearchManager()

#######################
# OpenSearch Retry Logic
#######################

def opensearch_operation_with_retry(operation_func: Callable, max_retries: int = 5, operation_name: str = "operation") -> Any:
    """
    Execute OpenSearch operation with exponential backoff retry for 429 errors
    
    Args:
        operation_func: Lambda/function that performs the OpenSearch operation
        max_retries: Maximum number of retry attempts (default: 5)
        operation_name: Name of operation for logging
    
    Returns:
        Result from the operation function
        
    Raises:
        TransportError: If operation fails after all retries or for non-429 errors
    """
    from opensearchpy.exceptions import TransportError
    
    for attempt in range(max_retries):
        try:
            return operation_func()
        except TransportError as e:
            if e.status_code == 429 and attempt < max_retries - 1:
                # Calculate exponential backoff with jitter
                wait_time = (2 ** attempt) + random.uniform(0, 1)
                logger.warning(
                    f"Rate limited (429) during {operation_name}, "
                    f"retrying in {wait_time:.2f}s (attempt {attempt + 1}/{max_retries})"
                )
                time.sleep(wait_time)
            else:
                # Re-raise if not 429 or max retries reached
                raise

#######################
# Metadata Normalization
#######################

def normalize_metadata_value(value: str, value_type: Optional[str] = None) -> Any:
    """
    Normalize metadata values for OpenSearch indexing with type detection.
    Handles dates, booleans, numbers, and strings with fallback to string.
    
    Args:
        value: The metadata value to normalize (from DynamoDB)
        value_type: Optional type hint from metadataValueType/attributeValueType field
        
    Returns:
        Normalized value in the appropriate type, or string if parsing fails
    """
    import re
    
    if not isinstance(value, str):
        return value
    
    # If we have a type hint, try to use it
    if value_type:
        try:
            if value_type == 'boolean' or value_type == 'bool':
                # Handle boolean values
                if value.lower() in ('true', '1', 'yes', 'on'):
                    return True
                elif value.lower() in ('false', '0', 'no', 'off'):
                    return False
                else:
                    return value  # Return as string if not parseable
                    
            elif value_type in ('number', 'integer', 'int'):
                # Try integer first
                try:
                    return int(value)
                except ValueError:
                    # Try float
                    try:
                        return float(value)
                    except ValueError:
                        return value  # Return as string if not parseable
                        
            elif value_type in ('float', 'double'):
                try:
                    return float(value)
                except ValueError:
                    return value  # Return as string if not parseable
                    
            elif value_type in ('date', 'datetime'):
                # Handle datetime - strip microseconds
                datetime_pattern = r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\.(\d+)(Z|[+-]\d{2}:\d{2})'
                match = re.match(datetime_pattern, value)
                if match:
                    base_datetime = match.group(1)
                    timezone = match.group(3)
                    return f"{base_datetime}{timezone}"
                else:
                    return value  # Return as-is if already in correct format
                    
        except Exception as e:
            logger.warning(f"Error parsing metadata value with type hint '{value_type}': {e}")
            # Fall through to auto-detection
    
    # Auto-detection if no type hint or type hint parsing failed
    
    # 1. Try to detect and normalize datetime with microseconds
    datetime_pattern = r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\.(\d+)(Z|[+-]\d{2}:\d{2})'
    match = re.match(datetime_pattern, value)
    if match:
        base_datetime = match.group(1)
        timezone = match.group(3)
        normalized = f"{base_datetime}{timezone}"
        logger.debug(f"Normalized datetime from '{value}' to '{normalized}'")
        return normalized
    
    # 2. Try boolean detection
    if value.lower() in ('true', 'false'):
        return value.lower() == 'true'
    
    # 3. Try number detection
    try:
        # Try integer first (no decimal point or scientific notation)
        if '.' not in value and 'e' not in value.lower():
            return int(value)
    except ValueError:
        pass
    
    try:
        # Try float
        return float(value)
    except ValueError:
        pass
    
    # 4. Default: return as string
    return value

#######################
# Utility Functions
#######################

def get_bucket_details(bucket_id: str) -> Optional[Dict[str, Any]]:
    """Get S3 bucket details from database"""
    try:
        bucket_response = s3_asset_buckets_table.query(
            KeyConditionExpression=Key('bucketId').eq(bucket_id),
            Limit=1
        )
        
        items = bucket_response.get("Items", [])
        if not items:
            logger.warning(f"No bucket found for bucketId: {bucket_id}")
            return None
            
        bucket = items[0]
        bucket_name = bucket.get('bucketName')
        base_assets_prefix = bucket.get('baseAssetsPrefix', '/')
        
        if not bucket_name:
            logger.error(f"Bucket name missing for bucketId: {bucket_id}")
            return None
        
        # Ensure prefix ends with slash
        if not base_assets_prefix.endswith('/'):
            base_assets_prefix += '/'
        
        # Remove leading slash
        if base_assets_prefix.startswith('/'):
            base_assets_prefix = base_assets_prefix[1:]
        
        return {
            'bucketId': bucket_id,
            'bucketName': bucket_name,
            'baseAssetsPrefix': base_assets_prefix
        }
    except Exception as e:
        logger.exception(f"Error getting bucket details for {bucket_id}: {e}")
        return None

def get_asset_details(database_id: str, asset_id: str) -> Optional[Dict[str, Any]]:
    """Get asset details from DynamoDB"""
    try:
        response = asset_storage_table.get_item(
            Key={
                'databaseId': database_id,
                'assetId': asset_id
            }
        )
        
        if 'Item' not in response:
            logger.warning(f"Asset not found: {database_id}/{asset_id}")
            return None
            
        return response['Item']
    except Exception as e:
        logger.exception(f"Error getting asset details for {database_id}/{asset_id}: {e}")
        return None

def lookup_database_id_for_permanent_delete(
    asset_id: str, 
    bucket_name: str, 
    bucket_prefix: str
) -> Tuple[Optional[str], bool]:
    """
    Lookup database_id for permanently deleted file using 3-step process:
    1. Query assetIdGSI with just asset_id
    2. If multiple results, filter by bucket match
    3. If still ambiguous, return error
    
    Args:
        asset_id: The asset ID to lookup
        bucket_name: The S3 bucket name from the event
        bucket_prefix: The S3 bucket prefix from the event
    
    Returns:
        Tuple of (database_id, success) where success indicates if lookup succeeded
    """
    try:
        # Step 1: Query assetIdGSI with just asset_id
        logger.info(f"Looking up database_id for permanently deleted file with asset_id: {asset_id}")
        
        response = asset_storage_table.query(
            IndexName='assetIdGSI',
            KeyConditionExpression=Key('assetId').eq(asset_id)
        )
        
        items = response.get('Items', [])
        
        if len(items) == 0:
            logger.warning(f"No assets found with asset_id: {asset_id}")
            return None, False
        
        if len(items) == 1:
            # Single match - use this database_id
            database_id = items[0].get('databaseId')
            logger.info(f"Found single asset match for {asset_id}, database_id: {database_id}")
            return database_id, True
        
        # Step 2: Multiple matches - filter by bucket
        logger.info(f"Found {len(items)} assets with asset_id {asset_id}, filtering by bucket")
        
        matching_assets = []
        for item in items:
            bucket_id = item.get('bucketId')
            if not bucket_id:
                continue
            
            # Get bucket details
            bucket_details = get_bucket_details(bucket_id)
            if not bucket_details:
                continue
            
            # Normalize bucket prefix for comparison
            item_bucket_name = bucket_details.get('bucketName')
            item_bucket_prefix = bucket_details.get('baseAssetsPrefix', '/')
            
            # Ensure both prefixes are normalized the same way
            if not item_bucket_prefix.endswith('/'):
                item_bucket_prefix += '/'
            if not item_bucket_prefix.startswith('/') and item_bucket_prefix != '/':
                item_bucket_prefix = '/' + item_bucket_prefix
            
            event_bucket_prefix = bucket_prefix
            if not event_bucket_prefix.endswith('/'):
                event_bucket_prefix += '/'
            if not event_bucket_prefix.startswith('/') and event_bucket_prefix != '/':
                event_bucket_prefix = '/' + event_bucket_prefix
            
            # Compare bucket name and prefix
            if item_bucket_name == bucket_name and item_bucket_prefix == event_bucket_prefix:
                matching_assets.append(item)
                logger.info(f"Bucket match found: database_id={item.get('databaseId')}, bucket={item_bucket_name}, prefix={item_bucket_prefix}")
        
        if len(matching_assets) == 1:
            # Single match after bucket filtering
            database_id = matching_assets[0].get('databaseId')
            logger.info(f"Found single bucket match for {asset_id}, database_id: {database_id}")
            return database_id, True
        
        # Step 3: Still ambiguous or no matches
        if len(matching_assets) == 0:
            logger.error(f"No bucket matches found for asset_id {asset_id} with bucket {bucket_name} and prefix {bucket_prefix}")
            return None, False
        else:
            logger.error(f"Multiple assets ({len(matching_assets)}) match asset_id {asset_id} with bucket {bucket_name} and prefix {bucket_prefix}, cannot determine unique database_id")
            return None, False
            
    except Exception as e:
        logger.exception(f"Error looking up database_id for asset_id {asset_id}: {e}")
        return None, False
       

def get_file_metadata(database_id: str, asset_id: str, file_path: str) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Get file-specific metadata AND attributes from new schema tables as flat single-level JSON objects.
    Returns separate dictionaries for metadata and attributes without any type prefixes.
    
    Returns:
        Tuple of (metadata_dict, attributes_dict) where:
        - Keys are just the field names (no MD_/AB_ or type prefixes)
        - Values are normalized (strings, numbers, booleans, dates)
    """
    try:
        # Build composite key for new schema
        composite_key = f"{database_id}:{asset_id}:{file_path}"
        
        metadata = {}
        attributes = {}
        
        # Query assetFileMetadataStorageTable for metadata fields
        response = asset_file_metadata_table.query(
            IndexName='DatabaseIdAssetIdFilePathIndex',
            KeyConditionExpression=Key('databaseId:assetId:filePath').eq(composite_key)
        )
        
        for item in response.get('Items', []):
            metadata_key = item.get('metadataKey')
            metadata_value = item.get('metadataValue')
            metadata_value_type = item.get('metadataValueType')
            
            # Skip system metadata records that conflict with OpenSearch field mappings
            if metadata_key == 'REINDEX_METADATA_RECORD':
                logger.debug(f"Skipping system metadata: {metadata_key}")
                continue  # Skip this metadata, but continue processing others
            
            if metadata_key and metadata_value:
                # Normalize the value with type hint
                normalized_value = normalize_metadata_value(metadata_value, metadata_value_type)
                
                # Store as flat single-level JSON: just field name -> value
                # No type prefixes (str_, num_, etc.) - flat object handles all types as strings
                metadata[metadata_key] = normalized_value
        
        # Query fileAttributeStorageTable for attribute fields
        response = file_attribute_table.query(
            IndexName='DatabaseIdAssetIdFilePathIndex',
            KeyConditionExpression=Key('databaseId:assetId:filePath').eq(composite_key)
        )
        
        for item in response.get('Items', []):
            attribute_key = item.get('attributeKey')
            attribute_value = item.get('attributeValue')
            attribute_value_type = item.get('attributeValueType')
            
            if attribute_key and attribute_value:
                # Normalize the value with type hint
                normalized_value = normalize_metadata_value(attribute_value, attribute_value_type)
                
                # Store as flat single-level JSON: just field name -> value
                # No type prefixes (str_, num_, etc.) - flat object handles all types as strings
                attributes[attribute_key] = normalized_value
        
        return metadata, attributes
    except Exception as e:
        logger.exception(f"Error getting file metadata for {database_id}/{asset_id}/{file_path}: {e}")
        return {}, {}

def get_s3_file_info(bucket_name: str, s3_key: str) -> Tuple[Optional[Dict[str, Any]], bool]:
    """Get S3 file information and archive status"""
    try:
        # Try to get current object
        try:
            response = s3_client.head_object(Bucket=bucket_name, Key=s3_key)
            
            file_info = {
                'size': response.get('ContentLength'),
                'lastModified': response.get('LastModified').isoformat() if response.get('LastModified') else None,
                'etag': response.get('ETag', '').strip('"'),
                'versionId': response.get('VersionId', 'null'),
                'contentType': response.get('ContentType')
            }
            
            # Extract additional metadata from S3 object metadata
            s3_metadata = response.get('Metadata', {})
            for key, value in s3_metadata.items():
                if not key.startswith('vams-') and key not in ['assetid', 'databaseid', 'uploadid']:
                    file_info[f"s3_{key}"] = value
                if key in ['vams-primarytype']: #We do want to add this vams metadata key to search. 
                    file_info[f"s3_{key}"] = value
            
            return file_info, False  # Not archived
            
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                # File might be archived, check for delete markers
                try:
                    versions_response = s3_client.list_object_versions(
                        Bucket=bucket_name,
                        Prefix=s3_key,
                        MaxKeys=10
                    )
                    
                    # Check if there are any delete markers
                    delete_markers = versions_response.get('DeleteMarkers', [])
                    versions = versions_response.get('Versions', [])
                    
                    # Find if this specific key has a delete marker
                    has_delete_marker = any(marker['Key'] == s3_key for marker in delete_markers)
                    
                    if has_delete_marker:
                        # File is archived, try to get info from latest version
                        latest_version = None
                        for version in versions:
                            if version['Key'] == s3_key:
                                if latest_version is None or version['LastModified'] > latest_version['LastModified']:
                                    latest_version = version
                        
                        if latest_version:
                            file_info = {
                                'size': latest_version.get('Size'),
                                'lastModified': latest_version.get('LastModified').isoformat() if latest_version.get('LastModified') else None,
                                'etag': latest_version.get('ETag', '').strip('"'),
                                'versionId': latest_version.get('VersionId', 'null'),
                                'contentType': None
                            }
                            return file_info, True  # Archived
                    
                    return None, False  # File doesn't exist
                    
                except Exception as inner_e:
                    logger.warning(f"Error checking versions for {s3_key}: {inner_e}")
                    return None, False
            else:
                raise e
                
    except Exception as e:
        logger.exception(f"Error getting S3 file info for {bucket_name}/{s3_key}: {e}")
        return None, False

def extract_file_extension(file_path: str) -> Optional[str]:
    """Extract file extension from file path"""
    if '.' in file_path and not file_path.endswith('/'):
        return file_path.split('.')[-1].lower()
    return None

def is_folder_path(file_path: str) -> bool:
    """Check if path represents a folder"""
    return file_path.endswith('/') or '.' not in os.path.basename(file_path)

def build_file_document(request: FileIndexRequest, asset_details: Dict[str, Any], 
                       bucket_details: Dict[str, Any], file_metadata: Dict[str, Any],
                       file_attributes: Dict[str, Any], s3_file_info: Optional[Dict[str, Any]], 
                       is_archived: bool) -> FileDocumentModel:
    """Build a file document for indexing"""
    
    # Extract file extension
    file_ext = extract_file_extension(request.filePath)
    
    # Create base document
    doc = FileDocumentModel(
        str_key=request.filePath,
        str_databaseid=request.databaseId,
        str_assetid=request.assetId,
        str_bucketid=bucket_details.get('bucketId'),
        str_assetname=asset_details.get('assetName'),
        str_bucketname=bucket_details.get('bucketName'),
        str_bucketprefix=bucket_details.get('baseAssetsPrefix'),
        str_fileext=file_ext,
        bool_archived=is_archived,
        list_tags=asset_details.get('tags', [])
    )
    
    # Add S3 file information if available
    if s3_file_info:
        doc.num_filesize = s3_file_info.get('size')
        doc.date_lastmodified = s3_file_info.get('lastModified')
        doc.str_etag = s3_file_info.get('etag')
        doc.str_s3_version_id = s3_file_info.get('versionId')
    
    # Add metadata fields with MD_ prefix
    if file_metadata:
        doc.add_metadata_fields(file_metadata)
    
    # Add attribute fields with AB_ prefix
    if file_attributes:
        doc.add_attribute_fields(file_attributes)
    
    # Add S3 metadata if present
    if s3_file_info:
        s3_metadata = {k: v for k, v in s3_file_info.items() 
                      if k.startswith('s3_') and k != 's3_'}
        if s3_metadata:
            doc.add_metadata_fields(s3_metadata)
    
    return doc

#######################
# OpenSearch Operations
#######################

def index_file_document(document: FileDocumentModel) -> bool:
    """Index a file document in OpenSearch with retry logic for 429 errors"""
    try:
        if not opensearch_manager.is_available():
            raise VAMSGeneralErrorResponse("OpenSearch client not available")
        
        client = opensearch_manager.get_client()
        
        # Create document ID from key components
        doc_id = f"{document.str_databaseid}#{document.str_assetid}#{document.str_key}"
        
        # Convert document to dict for indexing
        doc_dict = document.dict(exclude_unset=True)
        
        # Index the document with retry logic
        response = opensearch_operation_with_retry(
            lambda: client.index(
                index=opensearch_file_index,
                id=doc_id,
                body=doc_dict
            ),
            operation_name=f"index file {doc_id}"
        )
        
        logger.info(f"Indexed file document: {doc_id}")
        return response.get('result') in ['created', 'updated']
        
    except Exception as e:
        logger.exception(f"Error indexing file document: {e}")
        return False

def delete_file_document(database_id: str, asset_id: str, file_path: str) -> bool:
    """Delete a file document from OpenSearch with retry logic for 429 errors"""
    try:
        if not opensearch_manager.is_available():
            raise VAMSGeneralErrorResponse("OpenSearch client not available")
        
        client = opensearch_manager.get_client()
        
        # Create document ID
        doc_id = f"{database_id}#{asset_id}#{file_path}"
        
        # Delete the document with retry logic
        response = opensearch_operation_with_retry(
            lambda: client.delete(
                index=opensearch_file_index,
                id=doc_id,
                ignore=[404]  # Ignore if document doesn't exist
            ),
            operation_name=f"delete file {doc_id}"
        )
        
        logger.info(f"Deleted file document: {doc_id}")
        return True
        
    except Exception as e:
        logger.exception(f"Error deleting file document: {e}")
        return False

#######################
# Business Logic Functions
#######################

def process_file_index_request(request: FileIndexRequest) -> IndexOperationResponse:
    """Process a file index request with full data lookup"""
    
    try:
        # Validate input parameters using VAMS validators
        (valid, message) = validate({
            'databaseId': {
                'value': request.databaseId,
                'validator': 'ID'
            },
            'assetId': {
                'value': request.assetId,
                'validator': 'ASSET_ID'
            },
            'filePath': {
                'value': request.filePath,
                'validator': 'RELATIVE_FILE_PATH'
            },
            'bucketName': {
                'value': request.bucketName,
                'validator': 'STRING_256'
            },
            's3Key': {
                'value': request.s3Key,
                'validator': 'STRING_256'
            }
        })
        if not valid:
            logger.error(f"Validation error in file index request: {message}")
            return IndexOperationResponse(
                success=False,
                message="Invalid input parameters",
                indexName=opensearch_file_index,
                operation="validation_error"
            )
        
        # Skip folder paths
        if is_folder_path(request.filePath):
            logger.info(f"Skipping folder path: {request.filePath}")
            return IndexOperationResponse(
                success=True,
                message="Skipped folder path",
                indexName=opensearch_file_index,
                operation="skip"
            )
        
        if request.operation == "delete":
            # Delete the document
            success = delete_file_document(request.databaseId, request.assetId, request.filePath)
            
            return IndexOperationResponse(
                success=success,
                message="File document deleted" if success else "Failed to delete file document",
                documentId=f"{request.databaseId}#{request.assetId}#{request.filePath}",
                indexName=opensearch_file_index,
                operation="delete"
            )
        
        elif request.operation == "index":
            # Get asset details
            asset_details = get_asset_details(request.databaseId, request.assetId)
            if not asset_details:
                raise VAMSGeneralErrorResponse(f"Asset not found: {request.databaseId}/{request.assetId}")
            
            # Get bucket details
            bucket_id = asset_details.get('bucketId')
            if not bucket_id:
                raise VAMSGeneralErrorResponse(f"No bucket ID found for asset: {request.assetId}")
            
            bucket_details = get_bucket_details(bucket_id)
            if not bucket_details:
                raise VAMSGeneralErrorResponse(f"Bucket details not found for bucket: {bucket_id}")
            
            # Get file metadata and attributes (returned as separate dicts)
            file_metadata, file_attributes = get_file_metadata(request.databaseId, request.assetId, request.filePath)
            
            # Get S3 file information
            s3_file_info, is_archived = get_s3_file_info(request.bucketName, request.s3Key)
            
            # Handle delete marker case
            if request.isArchived or is_archived:
                is_archived = True
            
            # If file doesn't exist and no delete marker, skip indexing
            if not s3_file_info and not is_archived:
                logger.warning(f"File not found and not archived: {request.s3Key}")
                return IndexOperationResponse(
                    success=True,
                    message="File not found, skipping indexing",
                    indexName=opensearch_file_index,
                    operation="skip"
                )
            
            # Build document
            document = build_file_document(
                request, asset_details, bucket_details, 
                file_metadata, file_attributes, s3_file_info, is_archived
            )
            
            # Index the document
            success = index_file_document(document)
            
            doc_id = f"{request.databaseId}#{request.assetId}#{request.filePath}"
            
            return IndexOperationResponse(
                success=success,
                message="File document indexed" if success else "Failed to index file document",
                documentId=doc_id,
                indexName=opensearch_file_index,
                operation="index"
            )
        
        else:
            raise VAMSGeneralErrorResponse(f"Unknown operation: {request.operation}")
            
    except Exception as e:
        logger.exception(f"Error processing file index request: {e}")
        return IndexOperationResponse(
            success=False,
            message=f"Error processing request: {str(e)}",
            indexName=opensearch_file_index,
            operation=request.operation
        )

#######################
# Event Handlers
#######################

def handle_s3_notification(event_record: Dict[str, Any]) -> IndexOperationResponse:
    """Handle S3 bucket notification for file indexing"""
    try:
        # Extract S3 information from event
        s3_info = event_record.get('s3', {})
        bucket_name = s3_info.get('bucket', {}).get('name')
        s3_key = s3_info.get('object', {}).get('key')
        event_name = event_record.get('eventName', '')
        
        if not bucket_name or not s3_key:
            raise VAMSGeneralErrorResponse("Missing S3 bucket or key information")
        
        # URL decode the S3 key
        import urllib.parse
        s3_key = urllib.parse.unquote_plus(s3_key)
        
        # Skip folder markers
        if s3_key.endswith('/'):
            logger.info(f"Skipping folder marker: {s3_key}")
            return IndexOperationResponse(
                success=True,
                message="Skipped folder marker",
                indexName=opensearch_file_index,
                operation="skip"
            )
        
        # Skip excluded prefixes and patterns
        # These are system/temporary files that should not be indexed
        
        # Check if s3_key contains any excluded patterns
        if any(pattern in s3_key for pattern in excluded_patterns):
            logger.info(f"Ignoring file with excluded pattern from indexing: {s3_key}")
            return IndexOperationResponse(
                success=True,
                message="Skipped excluded pattern file",
                indexName=opensearch_file_index,
                operation="skip"
            )
        
        # Check if s3_key starts with any excluded prefixes (after any bucket prefix)
        # We need to check the path components, not just the raw key
        path_parts = s3_key.split('/')
        for part in path_parts:
            if any(part.startswith(prefix) for prefix in excluded_prefixes):
                logger.info(f"Ignoring excluded patterns or prefixes (pipeline, preview, temp-upload file, etc.) from indexing: {s3_key}")
                return IndexOperationResponse(
                    success=True,
                    message="Skipped excluded patterns or prefix files",
                    indexName=opensearch_file_index,
                    operation="skip"
                )
        
        # Handle ObjectRemoved:Delete events specially
        if "Delete" in event_name:
            logger.info(f"Processing delete event for file: {s3_key}")
            
            # For delete events, we need to parse asset ID from S3 key path
            # Typical structure: {basePrefix}{assetId}/{filePath}
            # We'll try to extract the asset ID from the path
            
            # Check versioning to determine if archived or permanently deleted
            try:
                versions_response = s3_client.list_object_versions(
                    Bucket=bucket_name,
                    Prefix=s3_key,
                    MaxKeys=10
                )
                
                delete_markers = versions_response.get('DeleteMarkers', [])
                versions = versions_response.get('Versions', [])
                
                # Check if this specific key has a delete marker and versions
                has_delete_marker = any(marker['Key'] == s3_key for marker in delete_markers)
                has_versions = any(v['Key'] == s3_key for v in versions)
                
                if has_delete_marker and has_versions:
                    # File is archived (delete marker exists but versions remain)
                    logger.info(f"File is archived (has delete marker and versions): {s3_key}")
                    
                    # Get metadata from latest version
                    latest_version = None
                    for version in versions:
                        if version['Key'] == s3_key:
                            if latest_version is None or version['LastModified'] > latest_version['LastModified']:
                                latest_version = version
                    
                    if latest_version:
                        # Get metadata from the version
                        try:
                            version_response = s3_client.head_object(
                                Bucket=bucket_name,
                                Key=s3_key,
                                VersionId=latest_version['VersionId']
                            )
                            s3_metadata = version_response.get('Metadata', {})
                            asset_id = s3_metadata.get('assetid')
                            database_id = s3_metadata.get('databaseid')
                            
                            if asset_id and database_id:
                                # File is archived - need to index with archived flag
                                # Get asset details to calculate relative path
                                asset_details = get_asset_details(database_id, asset_id)
                                if not asset_details:
                                    logger.warning(f"Asset not found for archived file: {database_id}/{asset_id}")
                                    return IndexOperationResponse(
                                        success=True,
                                        message="Asset not found for archived file, skipping",
                                        indexName=opensearch_file_index,
                                        operation="skip"
                                    )
                                
                                # Get bucket details
                                bucket_details = get_bucket_details(asset_details.get('bucketId'))
                                if not bucket_details:
                                    logger.warning(f"Bucket details not found for archived file asset: {asset_id}")
                                    return IndexOperationResponse(
                                        success=True,
                                        message="Bucket details not found for archived file, skipping",
                                        indexName=opensearch_file_index,
                                        operation="skip"
                                    )
                                
                                # Calculate relative path
                                asset_location = asset_details.get('assetLocation', {})
                                asset_base_key = asset_location.get('Key', f"{bucket_details['baseAssetsPrefix']}{asset_id}/")
                                
                                if s3_key.startswith(asset_base_key):
                                    relative_path = s3_key[len(asset_base_key):]
                                else:
                                    relative_path = s3_key
                                
                                # Ensure relative path starts with a slash
                                if not relative_path.startswith('/'):
                                    relative_path = '/' + relative_path
                                
                                # Get file metadata and attributes (returned as separate dicts)
                                file_metadata, file_attributes = get_file_metadata(database_id, asset_id, relative_path)
                                
                                # Get S3 file info from the version we already have
                                s3_file_info = {
                                    'size': latest_version.get('Size'),
                                    'lastModified': latest_version.get('LastModified').isoformat() if latest_version.get('LastModified') else None,
                                    'etag': latest_version.get('ETag', '').strip('"'),
                                    'versionId': latest_version.get('VersionId', 'null'),
                                    'contentType': None
                                }
                                
                                # Build document with archived flag
                                document = build_file_document(
                                    FileIndexRequest(
                                        databaseId=database_id,
                                        assetId=asset_id,
                                        filePath=relative_path,
                                        bucketName=bucket_name,
                                        s3Key=s3_key,
                                        isArchived=True,
                                        operation="index"
                                    ),
                                    asset_details,
                                    bucket_details,
                                    file_metadata,
                                    file_attributes,
                                    s3_file_info,
                                    True  # is_archived
                                )
                                
                                # Index the archived file
                                success = index_file_document(document)
                                
                                return IndexOperationResponse(
                                    success=success,
                                    message="Archived file indexed" if success else "Failed to index archived file",
                                    documentId=f"{database_id}#{asset_id}#{relative_path}",
                                    indexName=opensearch_file_index,
                                    operation="index"
                                )
                            else:
                                logger.warning(f"Missing metadata in archived version for {s3_key}")
                                return IndexOperationResponse(
                                    success=True,
                                    message="Missing metadata in archived version, skipping",
                                    indexName=opensearch_file_index,
                                    operation="skip"
                                )
                        except Exception as e:
                            logger.warning(f"Error getting metadata from version: {e}")
                            return IndexOperationResponse(
                                success=True,
                                message="Error accessing archived version metadata",
                                indexName=opensearch_file_index,
                                operation="skip"
                            )
                    else:
                        logger.warning(f"No versions found for archived file: {s3_key}")
                        return IndexOperationResponse(
                            success=True,
                            message="No versions found for archived file",
                            indexName=opensearch_file_index,
                            operation="skip"
                        )
                else:
                    # File is permanently deleted (no versions or no delete marker)
                    logger.info(f"File is permanently deleted: {s3_key}")
                    
                    # Try to parse asset ID from S3 key path
                    # Typical structure: {basePrefix}{assetId}/{filePath}
                    # Asset ID is typically a UUID or identifier before the first slash after prefix
                    
                    # Split the key and try to find the asset ID
                    # This is a heuristic approach - asset ID is usually the first path component
                    key_parts = s3_key.split('/')
                    if len(key_parts) >= 2:
                        # Assume asset ID is the first component (or second if first is empty/prefix)
                        potential_asset_id = key_parts[0] if key_parts[0] else (key_parts[1] if len(key_parts) > 1 else None)
                        
                        if potential_asset_id:
                            # Get bucket info from event record (passed from lambda_handler)
                            # These are stored at the top level of the event
                            event_bucket_name = event_record.get('ASSET_BUCKET_NAME', bucket_name)
                            event_bucket_prefix = event_record.get('ASSET_BUCKET_PREFIX', '/')
                            
                            # Lookup database_id using the new helper function
                            database_id, lookup_success = lookup_database_id_for_permanent_delete(
                                potential_asset_id,
                                event_bucket_name,
                                event_bucket_prefix
                            )
                            
                            if lookup_success and database_id:
                                logger.info(f"Successfully looked up database_id {database_id} for permanently deleted file")
                                asset_id = potential_asset_id
                                
                                # Calculate relative path from S3 key
                                # S3 key format: {assetId}/{filePath}
                                if len(key_parts) > 1:
                                    relative_path = '/' + '/'.join(key_parts[1:])
                                else:
                                    relative_path = '/' + s3_key
                                
                                # For permanent deletes, directly delete from OpenSearch
                                success = delete_file_document(database_id, asset_id, relative_path)
                                
                                return IndexOperationResponse(
                                    success=success,
                                    message="Permanently deleted file removed from index" if success else "Failed to delete file document",
                                    documentId=f"{database_id}#{asset_id}#{relative_path}",
                                    indexName=opensearch_file_index,
                                    operation="delete"
                                )
                            else:
                                logger.warning(f"Cannot determine database_id for permanently deleted file: {s3_key}")
                                return IndexOperationResponse(
                                    success=True,
                                    message="Cannot identify permanently deleted file, skipping",
                                    indexName=opensearch_file_index,
                                    operation="skip"
                                )
                        else:
                            logger.warning(f"Cannot parse asset ID from S3 key: {s3_key}")
                            return IndexOperationResponse(
                                success=True,
                                message="Cannot parse asset ID from S3 key",
                                indexName=opensearch_file_index,
                                operation="skip"
                            )
                    else:
                        logger.warning(f"Cannot parse asset ID from S3 key: {s3_key}")
                        return IndexOperationResponse(
                            success=True,
                            message="Cannot parse asset ID from S3 key",
                            indexName=opensearch_file_index,
                            operation="skip"
                        )
                        
            except Exception as e:
                logger.exception(f"Error checking file versioning status: {e}")
                return IndexOperationResponse(
                    success=False,
                    message=f"Error checking file versioning: {str(e)}",
                    indexName=opensearch_file_index,
                    operation="error"
                )
        else:
            # For non-delete events, extract metadata from current object
            try:
                s3_response = s3_client.head_object(Bucket=bucket_name, Key=s3_key)
                s3_metadata = s3_response.get('Metadata', {})
                
                asset_id = s3_metadata.get('assetid')
                database_id = s3_metadata.get('databaseid')
                
                if not asset_id or not database_id:
                    logger.warning(f"Missing asset/database ID in S3 metadata for {s3_key}")
                    return IndexOperationResponse(
                        success=True,
                        message="Missing metadata, skipping",
                        indexName=opensearch_file_index,
                        operation="skip"
                    )
                
                operation = "index"
                is_archived = False
                    
            except ClientError as e:
                logger.exception(f"Error getting S3 object metadata: {e}")
                return IndexOperationResponse(
                    success=False,
                    message=f"Error getting S3 metadata: {str(e)}",
                    indexName=opensearch_file_index,
                    operation="error"
                )
        
        # Calculate relative file path
        # This requires getting the asset details to determine the base prefix
        asset_details = get_asset_details(database_id, asset_id)
        if not asset_details:
            logger.warning(f"Asset not found for S3 file: {database_id}/{asset_id}")
            return IndexOperationResponse(
                success=True,
                message="Asset not found, skipping",
                indexName=opensearch_file_index,
                operation="skip"
            )
        
        # Get bucket details to determine prefix
        bucket_details = get_bucket_details(asset_details.get('bucketId'))
        if not bucket_details:
            logger.warning(f"Bucket details not found for asset: {asset_id}")
            return IndexOperationResponse(
                success=True,
                message="Bucket details not found, skipping",
                indexName=opensearch_file_index,
                operation="skip"
            )
        
        # Calculate relative path
        asset_location = asset_details.get('assetLocation', {})
        asset_base_key = asset_location.get('Key', f"{bucket_details['baseAssetsPrefix']}{asset_id}/")
        
        if s3_key.startswith(asset_base_key):
            relative_path = s3_key[len(asset_base_key):]
        else:
            relative_path = s3_key

        # Ensure relative path starts with a slash
        if not relative_path.startswith('/'):
            relative_path = '/' + relative_path
        
        # Create file index request
        request = FileIndexRequest(
            databaseId=database_id,
            assetId=asset_id,
            filePath=relative_path,
            bucketName=bucket_name,
            s3Key=s3_key,
            isArchived=is_archived,
            operation=operation
        )
        
        # Process the request
        return process_file_index_request(request)
        
    except Exception as e:
        logger.exception(f"Error handling S3 notification: {e}")
        return IndexOperationResponse(
            success=False,
            message=f"Error handling S3 notification: {str(e)}",
            indexName=opensearch_file_index,
            operation="error"
        )

def handle_metadata_stream(event_record: Dict[str, Any]) -> IndexOperationResponse:
    """Handle DynamoDB metadata/attribute table streams for file indexing"""
    try:
        event_name = event_record.get('eventName', '')
        dynamodb_data = event_record.get('dynamodb', {})
        
        # For REMOVE events, Keys are always present regardless of StreamViewType
        # We should use Keys directly since NewImage won't exist for REMOVE
        if event_name == 'REMOVE':
            # Get composite key from Keys (always present for REMOVE)
            keys = dynamodb_data.get('Keys', {})
            if not keys or 'databaseId:assetId:filePath' not in keys:
                logger.warning("Missing Keys in REMOVE event")
                return IndexOperationResponse(
                    success=True,
                    message="Missing Keys in REMOVE event, skipping",
                    indexName=opensearch_file_index,
                    operation="skip"
                )
            
            composite_key = keys.get('databaseId:assetId:filePath', {}).get('S')
            if not composite_key:
                logger.warning("Missing composite key value in REMOVE event")
                return IndexOperationResponse(
                    success=True,
                    message="Missing composite key value, skipping",
                    indexName=opensearch_file_index,
                    operation="skip"
                )
            
            # Parse composite key
            parts = composite_key.split(':', 2)
            if len(parts) != 3:
                logger.warning(f"Invalid composite key format in REMOVE: {composite_key}")
                return IndexOperationResponse(
                    success=True,
                    message="Invalid composite key format, skipping",
                    indexName=opensearch_file_index,
                    operation="skip"
                )
            
            database_id, asset_id, file_path = parts
            
            # Skip if it's asset-level (file_path is just "/")
            if file_path == '/':
                logger.info("Asset-level metadata REMOVE, skipping for file index")
                return IndexOperationResponse(
                    success=True,
                    message="Asset-level metadata, skipping",
                    indexName=opensearch_file_index,
                    operation="skip"
                )
            
            # Skip folder paths
            if is_folder_path(file_path):
                logger.info(f"Folder path metadata REMOVE, skipping: {file_path}")
                return IndexOperationResponse(
                    success=True,
                    message="Folder path, skipping",
                    indexName=opensearch_file_index,
                    operation="skip"
                )
            
            # For REMOVE, we don't need to re-index, just return success
            # The metadata/attribute was deleted, so we should re-index the file
            # to update its metadata fields in OpenSearch
            logger.info(f"Metadata REMOVE for file {database_id}/{asset_id}{file_path}, will re-index")
            
            # Get asset details to determine bucket and S3 key
            asset_details = get_asset_details(database_id, asset_id)
            if not asset_details:
                logger.warning(f"Asset not found for metadata REMOVE: {database_id}/{asset_id}")
                return IndexOperationResponse(
                    success=True,
                    message="Asset not found, skipping",
                    indexName=opensearch_file_index,
                    operation="skip"
                )
            
            # Get bucket details
            bucket_details = get_bucket_details(asset_details.get('bucketId'))
            if not bucket_details:
                logger.warning(f"Bucket details not found for asset: {asset_id}")
                return IndexOperationResponse(
                    success=True,
                    message="Bucket details not found, skipping",
                    indexName=opensearch_file_index,
                    operation="skip"
                )
            
            # Calculate S3 key
            asset_location = asset_details.get('assetLocation', {})
            asset_base_key = asset_location.get('Key', f"{bucket_details['baseAssetsPrefix']}{asset_id}/")
            s3_key = asset_base_key + file_path.lstrip('/')
            
            # Ensure relative path starts with a slash
            if not file_path.startswith('/'):
                file_path = '/' + file_path
            
            # Create file index request to re-index (which will fetch remaining metadata)
            request = FileIndexRequest(
                databaseId=database_id,
                assetId=asset_id,
                filePath=file_path,
                bucketName=bucket_details['bucketName'],
                s3Key=s3_key,
                operation="index"  # Re-index to update metadata
            )
            
            # Process the request
            return process_file_index_request(request)
        
        # For INSERT/MODIFY, get the record data from NewImage
        record_data = dynamodb_data.get('NewImage', {})
        
        if not record_data:
            logger.warning("No NewImage found in INSERT/MODIFY event")
            return IndexOperationResponse(
                success=True,
                message="No NewImage data, skipping",
                indexName=opensearch_file_index,
                operation="skip"
            )
        
        # Parse composite key format (databaseId:assetId:filePath)
        composite_key = record_data.get('databaseId:assetId:filePath', {}).get('S')
        
        if not composite_key:
            logger.warning("Missing composite key in metadata stream")
            return IndexOperationResponse(
                success=True,
                message="Missing composite key, skipping",
                indexName=opensearch_file_index,
                operation="skip"
            )
        
        # Parse composite key
        parts = composite_key.split(':', 2)
        if len(parts) != 3:
            logger.warning(f"Invalid composite key format: {composite_key}")
            return IndexOperationResponse(
                success=True,
                message="Invalid composite key format, skipping",
                indexName=opensearch_file_index,
                operation="skip"
            )
        
        database_id, asset_id, file_path = parts
        
        # Skip if it's asset-level (file_path is just "/")
        if file_path == '/':
            logger.info("Asset-level metadata, skipping for file index")
            return IndexOperationResponse(
                success=True,
                message="Asset-level metadata, skipping",
                indexName=opensearch_file_index,
                operation="skip"
            )
        
        # Skip folder paths
        if is_folder_path(file_path):
            logger.info(f"Folder path metadata, skipping: {file_path}")
            return IndexOperationResponse(
                success=True,
                message="Folder path, skipping",
                indexName=opensearch_file_index,
                operation="skip"
            )
        
        # Get asset details to determine bucket and S3 key
        asset_details = get_asset_details(database_id, asset_id)
        if not asset_details:
            logger.warning(f"Asset not found for metadata: {database_id}/{asset_id}")
            return IndexOperationResponse(
                success=True,
                message="Asset not found, skipping",
                indexName=opensearch_file_index,
                operation="skip"
            )
        
        # Get bucket details
        bucket_details = get_bucket_details(asset_details.get('bucketId'))
        if not bucket_details:
            logger.warning(f"Bucket details not found for asset: {asset_id}")
            return IndexOperationResponse(
                success=True,
                message="Bucket details not found, skipping",
                indexName=opensearch_file_index,
                operation="skip"
            )
        
        # Calculate S3 key
        asset_location = asset_details.get('assetLocation', {})
        asset_base_key = asset_location.get('Key', f"{bucket_details['baseAssetsPrefix']}{asset_id}/")
        s3_key = asset_base_key + file_path.lstrip('/')
        
        # Ensure relative path starts with a slash
        if not file_path.startswith('/'):
            file_path = '/' + file_path
        
        # Create file index request
        request = FileIndexRequest(
            databaseId=database_id,
            assetId=asset_id,
            filePath=file_path,
            bucketName=bucket_details['bucketName'],
            s3Key=s3_key,
            operation="index"
        )
        
        # Process the request
        return process_file_index_request(request)
        
    except Exception as e:
        logger.exception(f"Error handling metadata stream: {e}")
        return IndexOperationResponse(
            success=False,
            message=f"Error handling metadata stream: {str(e)}",
            indexName=opensearch_file_index,
            operation="error"
        )

#######################
# Lambda Handler
#######################

def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for file indexing operations"""
    global claims_and_roles
    
    try:
        logger.info(f"Processing file indexing event: {json.dumps(event, default=str)}")
        
        results = []
        
        # Extract bucket info from top-level event (if present)
        asset_bucket_name = event.get('ASSET_BUCKET_NAME')
        asset_bucket_prefix = event.get('ASSET_BUCKET_PREFIX', '/')
        
        # Handle different event sources
        if 'Records' in event:
            for record in event['Records']:
                event_source = record.get('eventSource', '')
                
                if event_source == 'aws:s3':
                    # Direct S3 bucket notification
                    # Pass bucket info to the record for permanent delete lookups
                    if asset_bucket_name:
                        record['ASSET_BUCKET_NAME'] = asset_bucket_name
                        record['ASSET_BUCKET_PREFIX'] = asset_bucket_prefix
                    result = handle_s3_notification(record)
                    results.append(result)
                    
                elif event_source == 'aws:sqs':
                    # SQS message (may contain SNS message with S3 event or DynamoDB stream)
                    try:
                        # Parse SQS message body
                        body = record.get('body', '')
                        if isinstance(body, str):
                            body = json.loads(body)
                        
                        # Check if this is an SNS message
                        if body.get('Type') == 'Notification' and body.get('Message'):
                            # Parse SNS message
                            sns_message = body.get('Message')
                            if isinstance(sns_message, str):
                                sns_message = json.loads(sns_message)
                            
                            # First check if SNS message is a direct DynamoDB stream record (from SNS queuing Lambda)
                            # This is the direct SNS→SQS path
                            if sns_message.get('eventSource') == 'aws:dynamodb' or \
                               sns_message.get('eventName') in ['INSERT', 'MODIFY', 'REMOVE']:
                                # Direct DynamoDB stream record from SNS queuing Lambda
                                result = handle_metadata_stream(sns_message)
                                results.append(result)
                            
                            # Check if SNS message contains Records array (nested structure from sqsBucketSync)
                            elif 'Records' in sns_message:
                                for inner_record in sns_message['Records']:
                                    inner_event_source = inner_record.get('eventSource', '')
                                    
                                    if inner_event_source == 'aws:s3':
                                        # Direct S3 record in SNS message
                                        if asset_bucket_name:
                                            inner_record['ASSET_BUCKET_NAME'] = asset_bucket_name
                                            inner_record['ASSET_BUCKET_PREFIX'] = asset_bucket_prefix
                                        result = handle_s3_notification(inner_record)
                                        results.append(result)
                                    
                                    elif inner_event_source == 'aws:sqs':
                                        # Nested SQS record (from sqsBucketSync) - parse further
                                        try:
                                            inner_body = inner_record.get('body', '')
                                            if isinstance(inner_body, str):
                                                inner_body = json.loads(inner_body)
                                            
                                            # Check if this inner SQS message contains SNS notification
                                            if inner_body.get('Type') == 'Notification' and inner_body.get('Message'):
                                                inner_sns_message = inner_body.get('Message')
                                                if isinstance(inner_sns_message, str):
                                                    inner_sns_message = json.loads(inner_sns_message)
                                                
                                                # Now check for S3 records in the inner SNS message
                                                if 'Records' in inner_sns_message:
                                                    for s3_record in inner_sns_message['Records']:
                                                        if s3_record.get('eventSource') == 'aws:s3':
                                                            # Extract bucket info from the nested structure
                                                            nested_bucket_name = inner_sns_message.get('ASSET_BUCKET_NAME', asset_bucket_name)
                                                            nested_bucket_prefix = inner_sns_message.get('ASSET_BUCKET_PREFIX', asset_bucket_prefix)
                                                            
                                                            if nested_bucket_name:
                                                                s3_record['ASSET_BUCKET_NAME'] = nested_bucket_name
                                                                s3_record['ASSET_BUCKET_PREFIX'] = nested_bucket_prefix
                                                            
                                                            result = handle_s3_notification(s3_record)
                                                            results.append(result)
                                        except json.JSONDecodeError as inner_e:
                                            logger.exception(f"Error parsing nested SQS/SNS message: {inner_e}")
                                    
                                    else:
                                        logger.warning(f"Unknown record event source in SNS message: {inner_event_source}")
                            
                            else:
                                logger.warning(f"SNS message does not contain recognized event format: {sns_message.keys()}")
                        else:
                            logger.warning("SQS message is not an SNS notification")
                    except json.JSONDecodeError as e:
                        logger.exception(f"Error parsing SQS/SNS message: {e}")
                        results.append(IndexOperationResponse(
                            success=False,
                            message=f"Error parsing SQS/SNS message: {str(e)}",
                            indexName=opensearch_file_index,
                            operation="error"
                        ))
                    
                elif event_source == 'aws:dynamodb':
                    # DynamoDB stream from metadata/attribute tables
                    source_arn = record.get('eventSourceARN', '')
                    
                    if asset_file_metadata_table_name in source_arn or \
                       file_attribute_table_name in source_arn:
                        # Metadata or attribute table stream
                        result = handle_metadata_stream(record)
                        results.append(result)
                    else:
                        logger.warning(f"Unknown DynamoDB table in source ARN: {source_arn}")
                    
                else:
                    logger.warning(f"Unknown event source: {event_source}")
        
        else:
            # Direct invocation with FileIndexRequest
            try:
                request = parse(event, model=FileIndexRequest)
                result = process_file_index_request(request)
                results.append(result)
            except ValidationError as v:
                logger.exception(f"Validation error: {v}")
                return validation_error(body={'message': str(v)})
        
        # Summarize results
        successful = sum(1 for r in results if r.success)
        total = len(results)
        
        response_body = {
            'message': f"Processed {successful}/{total} file indexing operations successfully",
            'results': [r.dict() for r in results]
        }
        
        return success(body=response_body)
        
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)})
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)})
    except Exception as e:
        logger.exception(f"Internal error in file indexer: {e}")
        return internal_error()