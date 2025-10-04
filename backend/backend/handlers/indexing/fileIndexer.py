"""
File indexer for VAMS dual-index OpenSearch system.
Handles indexing of S3 files with full data lookups from multiple sources.

Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
SPDX-License-Identifier: Apache-2.0
"""

import os
import boto3
import json
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
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
    metadata_storage_table_name = os.environ["METADATA_STORAGE_TABLE_NAME"]
    s3_asset_buckets_table_name = os.environ["S3_ASSET_BUCKETS_STORAGE_TABLE_NAME"]
    opensearch_file_index_ssm_param = os.environ["OPENSEARCH_FILE_INDEX_SSM_PARAM"]
    opensearch_endpoint_ssm_param = os.environ["OPENSEARCH_ENDPOINT_SSM_PARAM"]
    opensearch_type = os.environ.get("OPENSEARCH_TYPE", "serverless")
    auth_table_name = os.environ["AUTH_TABLE_NAME"]
    user_roles_table_name = os.environ["USER_ROLES_TABLE_NAME"]
    roles_table_name = os.environ["ROLES_TABLE_NAME"]
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
metadata_storage_table = dynamodb.Table(metadata_storage_table_name)
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
       

def get_file_metadata(database_id: str, asset_id: str, file_path: str) -> Dict[str, Any]:
    """Get file-specific metadata from metadata table"""
    try:

        # Query for metadata with specific path
        # Note: For now the way metadata for a file is stored is as format /assetid/path/to/file
        metadata_keys = [
            f"{asset_id}{file_path}", # Path variant 1
            f"/{asset_id}{file_path}" # Path variant 2
        ]
        
        all_metadata = {}

        for key in metadata_keys:
            try:
                response = metadata_storage_table.get_item(
                    Key={
                        'databaseId': database_id,
                        'assetId': key
                    }
                )
                
                if 'Item' in response:
                    metadata = response['Item']
                    # Remove system fields and merge
                    filtered_metadata = {k: v for k, v in metadata.items() 
                                       if not k.startswith('_') and k not in ['databaseId', 'assetId']}
                    all_metadata.update(filtered_metadata)
            except Exception as e:
                logger.warning(f"Error getting metadata for key {key} : {e}")
                continue
        
        return all_metadata
    except Exception as e:
        logger.exception(f"Error getting file metadata for {database_id}/{asset_id}/{file_path} : {e}")
        return {}       

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
                       s3_file_info: Optional[Dict[str, Any]], is_archived: bool) -> FileDocumentModel:
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
    """Index a file document in OpenSearch"""
    try:
        if not opensearch_manager.is_available():
            raise VAMSGeneralErrorResponse("OpenSearch client not available")
        
        client = opensearch_manager.get_client()
        
        # Create document ID from key components
        doc_id = f"{document.str_databaseid}#{document.str_assetid}#{document.str_key}"
        
        # Convert document to dict for indexing
        doc_dict = document.dict(exclude_unset=True)
        
        # Index the document
        response = client.index(
            index=opensearch_file_index,
            id=doc_id,
            body=doc_dict
        )
        
        logger.info(f"Indexed file document: {doc_id}")
        return response.get('result') in ['created', 'updated']
        
    except Exception as e:
        logger.exception(f"Error indexing file document: {e}")
        return False

def delete_file_document(database_id: str, asset_id: str, file_path: str) -> bool:
    """Delete a file document from OpenSearch"""
    try:
        if not opensearch_manager.is_available():
            raise VAMSGeneralErrorResponse("OpenSearch client not available")
        
        client = opensearch_manager.get_client()
        
        # Create document ID
        doc_id = f"{database_id}#{asset_id}#{file_path}"
        
        # Delete the document
        response = client.delete(
            index=opensearch_file_index,
            id=doc_id,
            ignore=[404]  # Ignore if document doesn't exist
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
            
            # Get file metadata
            file_metadata = get_file_metadata(request.databaseId, request.assetId, request.filePath)
            
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
                file_metadata, s3_file_info, is_archived
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
        
        # Extract asset ID and database ID from S3 object metadata
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
                
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey':
                # File was deleted, we need to handle this differently
                # For now, we'll skip since we can't determine the asset/database ID
                logger.warning(f"File deleted but cannot determine asset/database ID: {s3_key}")
                return IndexOperationResponse(
                    success=True,
                    message="File deleted but metadata unavailable",
                    indexName=opensearch_file_index,
                    operation="skip"
                )
            else:
                raise e
        
        # Determine operation based on event type
        operation = "delete" if "Delete" in event_name else "index"
        is_archived = "Delete" in event_name
        
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
    """Handle DynamoDB metadata table stream for file indexing"""
    try:
        event_name = event_record.get('eventName', '')
        
        # Get the record data
        if event_name == 'REMOVE':
            # For deletes, use the old image
            record_data = event_record.get('dynamodb', {}).get('OldImage', {})
        else:
            # For inserts/updates, use the new image
            record_data = event_record.get('dynamodb', {}).get('NewImage', {})
        
        if not record_data:
            logger.warning("No record data found in metadata stream event")
            return IndexOperationResponse(
                success=True,
                message="No record data, skipping",
                indexName=opensearch_file_index,
                operation="skip"
            )
        
        # Extract database ID and asset ID from DynamoDB record
        database_id = record_data.get('databaseId', {}).get('S')
        asset_id_with_path = record_data.get('assetId', {}).get('S')
        
        if not database_id or not asset_id_with_path:
            logger.warning("Missing database ID or asset ID in metadata stream")
            return IndexOperationResponse(
                success=True,
                message="Missing IDs, skipping",
                indexName=opensearch_file_index,
                operation="skip"
            )
        
        # Check if this is a file metadata record (has path)
        if '/' not in asset_id_with_path:
            logger.info("Asset-level metadata, skipping for file index")
            return IndexOperationResponse(
                success=True,
                message="Asset-level metadata, skipping",
                indexName=opensearch_file_index,
                operation="skip"
            )
        
        # Extract asset ID and file path
        # Example entry: "/ASSETID/PATH/TO/FILE"
        # Remove leading slash before splitting
        asset_id_with_path_clean = asset_id_with_path.lstrip('/')
        asset_id, file_path = asset_id_with_path_clean.split('/', 1)
        
        # Skip if path is empty or root
        if not file_path or file_path == '/' or file_path == '':
            logger.info("Root path metadata, skipping for file index")
            return IndexOperationResponse(
                success=True,
                message="Root path metadata, skipping",
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
        s3_key = asset_base_key + file_path
        
        # Determine operation
        operation = "delete" if event_name == 'REMOVE' else "index"

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
            operation=operation
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
        
        # Handle different event sources
        if 'Records' in event:
            for record in event['Records']:
                event_source = record.get('eventSource', '')
                
                if event_source == 'aws:s3':
                    # Direct S3 bucket notification
                    result = handle_s3_notification(record)
                    results.append(result)
                    
                elif event_source == 'aws:sqs':
                    # SQS message (may contain SNS message with S3 event)
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
                            
                            # Check if SNS message contains S3 records
                            if 'Records' in sns_message:
                                for s3_record in sns_message['Records']:
                                    if s3_record.get('eventSource') == 'aws:s3':
                                        result = handle_s3_notification(s3_record)
                                        results.append(result)
                            else:
                                logger.warning("SNS message does not contain S3 records")
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
                    # DynamoDB stream from metadata table
                    result = handle_metadata_stream(record)
                    results.append(result)
                    
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
