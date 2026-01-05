"""
Asset indexer for VAMS dual-index OpenSearch system.
Handles indexing of assets with full data lookups from multiple sources.

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
from models.indexing import AssetDocumentModel, AssetIndexRequest, IndexOperationResponse

# Configure AWS clients with retry configuration
retry_config = Config(
    retries={
        'max_attempts': 5,
        'mode': 'adaptive'
    }
)

dynamodb = boto3.resource('dynamodb', config=retry_config)
opensearch_client = boto3.client('opensearchserverless', config=retry_config) if os.environ.get('OPENSEARCH_TYPE') == 'serverless' else boto3.client('opensearch', config=retry_config)
logger = safeLogger(service_name="AssetIndexer")

# Global variables for claims and roles
claims_and_roles = {}

# Load environment variables with error handling
try:
    asset_storage_table_name = os.environ["ASSET_STORAGE_TABLE_NAME"]
    asset_file_metadata_table_name = os.environ["ASSET_FILE_METADATA_STORAGE_TABLE_NAME"]
    s3_asset_buckets_table_name = os.environ["S3_ASSET_BUCKETS_STORAGE_TABLE_NAME"]
    asset_links_table_name = os.environ["ASSET_LINKS_STORAGE_TABLE_V2_NAME"]
    asset_versions_table_name = os.environ["ASSET_VERSIONS_STORAGE_TABLE_NAME"]
    opensearch_asset_index_ssm_param = os.environ["OPENSEARCH_ASSET_INDEX_SSM_PARAM"]
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
opensearch_asset_index = get_ssm_parameter_value(opensearch_asset_index_ssm_param)
opensearch_endpoint = get_ssm_parameter_value(opensearch_endpoint_ssm_param)

# Initialize DynamoDB tables
asset_storage_table = dynamodb.Table(asset_storage_table_name)
asset_file_metadata_table = dynamodb.Table(asset_file_metadata_table_name)
s3_asset_buckets_table = dynamodb.Table(s3_asset_buckets_table_name)
asset_links_table = dynamodb.Table(asset_links_table_name)
asset_versions_table = dynamodb.Table(asset_versions_table_name)

#######################
# OpenSearch Client Management
#######################

class AssetIndexOpenSearchManager:
    """Singleton OpenSearch client manager for asset indexing operations"""
    
    _instance = None
    _client = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(AssetIndexOpenSearchManager, cls).__new__(cls)
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
            
            logger.info(f"Initialized asset index OpenSearch client for index: {opensearch_asset_index}")
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
opensearch_manager = AssetIndexOpenSearchManager()

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
        value_type: Optional type hint from metadataValueType field
        
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

def get_asset_metadata(database_id: str, asset_id: str) -> Dict[str, Any]:
    """
    Get asset-level metadata from NEW schema table as a flat single-level JSON object.
    Returns metadata without any type prefixes - just field names and values.
    """
    try:
        # Build composite key for asset-level metadata (file_path = "/")
        composite_key = f"{database_id}:{asset_id}:/"
        
        all_metadata = {}
        
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
                all_metadata[metadata_key] = normalized_value
        
        return all_metadata
    except Exception as e:
        logger.exception(f"Error getting asset metadata for {database_id}/{asset_id}: {e}")
        return {}

def get_asset_version_info(asset_id: str) -> Dict[str, Any]:
    """Get current asset version information"""
    try:
        # Get current version info - remove Limit to ensure we scan all records
        # The FilterExpression will still only return matching items
        response = asset_versions_table.query(
            KeyConditionExpression=Key('assetId').eq(asset_id),
            FilterExpression=boto3.dynamodb.conditions.Attr('isCurrentVersion').eq(True)
        )
        
        items = response.get('Items', [])
        if items:
            # Should only be one current version, but take the first if multiple exist
            version_info = items[0]
            return {
                'versionId': version_info.get('assetVersionId'),
                'createdAt': version_info.get('dateCreated'),
                'comment': version_info.get('comment', '')
            }
        
        return {}
    except Exception as e:
        logger.exception(f"Error getting asset version info for {asset_id}: {e}")
        return {}

def get_asset_relationship_flags(database_id: str, asset_id: str) -> Dict[str, bool]:
    """Get asset relationship flags (children, parents, related)"""
    try:
        asset_key = f"{database_id}:{asset_id}"
        
        # Check for children (where this asset is the 'from' in parentChild relationships)
        children_response = asset_links_table.query(
            IndexName='fromAssetGSI',
            KeyConditionExpression=Key('fromAssetDatabaseId:fromAssetId').eq(asset_key),
            FilterExpression=boto3.dynamodb.conditions.Attr('relationshipType').eq('parentChild'),
        )
        has_children = len(children_response.get('Items', [])) > 0
        
        # Check for parents (where this asset is the 'to' in parentChild relationships)
        parents_response = asset_links_table.query(
            IndexName='toAssetGSI',
            KeyConditionExpression=Key('toAssetDatabaseId:toAssetId').eq(asset_key),
            FilterExpression=boto3.dynamodb.conditions.Attr('relationshipType').eq('parentChild'),
        )
        has_parents = len(parents_response.get('Items', [])) > 0
        
        # Check for related assets (where this asset is in 'related' relationships)
        related_from_response = asset_links_table.query(
            IndexName='fromAssetGSI',
            KeyConditionExpression=Key('fromAssetDatabaseId:fromAssetId').eq(asset_key),
            FilterExpression=boto3.dynamodb.conditions.Attr('relationshipType').eq('related'),
        )
        
        related_to_response = asset_links_table.query(
            IndexName='toAssetGSI',
            KeyConditionExpression=Key('toAssetDatabaseId:toAssetId').eq(asset_key),
            FilterExpression=boto3.dynamodb.conditions.Attr('relationshipType').eq('related'),
        )
        
        has_related = (len(related_from_response.get('Items', [])) > 0 or 
                      len(related_to_response.get('Items', [])) > 0)
        
        return {
            'has_children': has_children,
            'has_parents': has_parents,
            'has_related': has_related
        }
        
    except Exception as e:
        logger.exception(f"Error getting asset relationship flags for {database_id}/{asset_id}: {e}")
        return {
            'has_children': False,
            'has_parents': False,
            'has_related': False
        }

def is_asset_archived(asset_details: Dict[str, Any]) -> bool:
    """Check if asset is archived based on #deleted markers"""
    try:
        asset_id = asset_details.get('assetId', '')
        database_id = asset_details.get('databaseId', '')
        
        # Check for #deleted marker in asset ID or database ID
        return '#deleted' in asset_id or '#deleted' in database_id
        
    except Exception as e:
        logger.exception(f"Error checking asset archive status: {e}")
        return False

def build_asset_document(request: AssetIndexRequest, asset_details: Dict[str, Any], 
                        bucket_details: Dict[str, Any], asset_metadata: Dict[str, Any],
                        version_info: Dict[str, Any], relationship_flags: Dict[str, bool],
                        is_archived: bool) -> AssetDocumentModel:
    """Build an asset document for indexing"""
    
    # Normalize databaseId for storage (remove #deleted suffix)
    normalized_database_id = request.databaseId.replace('#deleted', '')
    
    # Create base document
    doc = AssetDocumentModel(
        str_databaseid=normalized_database_id,  # Store normalized version
        str_assetid=request.assetId,
        str_bucketid=bucket_details.get('bucketId'),
        str_assetname=asset_details.get('assetName'),
        str_bucketname=bucket_details.get('bucketName'),
        str_bucketprefix=bucket_details.get('baseAssetsPrefix'),
        str_assettype=asset_details.get('assetType'),
        str_description=asset_details.get('description'),
        bool_isdistributable=asset_details.get('isDistributable'),
        list_tags=asset_details.get('tags', []),
        bool_archived=is_archived
    )
    
    # Add version information
    if version_info:
        doc.str_asset_version_id = version_info.get('versionId')
        doc.date_asset_version_createdate = version_info.get('createdAt')
        doc.str_asset_version_comment = version_info.get('comment')
    
    # Add relationship flags
    doc.bool_has_asset_children = relationship_flags.get('has_children', False)
    doc.bool_has_asset_parents = relationship_flags.get('has_parents', False)
    doc.bool_has_assets_related = relationship_flags.get('has_related', False)
    
    # Add metadata fields with MD_ prefix
    if asset_metadata:
        doc.add_metadata_fields(asset_metadata)
    
    return doc

#######################
# OpenSearch Operations
#######################

def index_asset_document(document: AssetDocumentModel) -> bool:
    """Index an asset document in OpenSearch with retry logic for 429 errors"""
    try:
        if not opensearch_manager.is_available():
            raise VAMSGeneralErrorResponse("OpenSearch client not available")
        
        client = opensearch_manager.get_client()

        # Normalize databaseId for storage (addition of #deleted suffix if archived)
        normalized_database_id = document.str_databaseid
        if(document.bool_archived and "#deleted" not in normalized_database_id):
            normalized_database_id = f"{normalized_database_id}#deleted"
        
        # Create document ID from key components
        doc_id = f"{normalized_database_id}#{document.str_assetid}"
        
        # Convert document to dict for indexing
        doc_dict = document.dict(exclude_unset=True)
        
        # Index the document with retry logic
        response = opensearch_operation_with_retry(
            lambda: client.index(
                index=opensearch_asset_index,
                id=doc_id,
                body=doc_dict
            ),
            operation_name=f"index asset {doc_id}"
        )
        
        logger.info(f"Indexed asset document: {doc_id}")
        return response.get('result') in ['created', 'updated']
        
    except Exception as e:
        logger.exception(f"Error indexing asset document: {e}")
        return False

def delete_asset_document(database_id: str, asset_id: str) -> bool:
    """Delete an asset document from OpenSearch with retry logic for 429 errors"""
    try:
        if not opensearch_manager.is_available():
            raise VAMSGeneralErrorResponse("OpenSearch client not available")
        
        client = opensearch_manager.get_client()
        
        # Create document ID
        doc_id = f"{database_id}#{asset_id}"
        
        # Delete the document with retry logic
        response = opensearch_operation_with_retry(
            lambda: client.delete(
                index=opensearch_asset_index,
                id=doc_id,
                ignore=[404]  # Ignore if document doesn't exist
            ),
            operation_name=f"delete asset {doc_id}"
        )
        
        logger.info(f"Deleted asset document: {doc_id}")
        return True
        
    except Exception as e:
        logger.exception(f"Error deleting asset document: {e}")
        return False

#######################
# Business Logic Functions
#######################

def process_asset_index_request(request: AssetIndexRequest) -> IndexOperationResponse:
    """Process an asset index request with full data lookup"""
    
    try:
        # Validate input parameters using VAMS validators
        # Skip databaseId validation for archived assets (databaseId ends with #deleted)
        if not request.databaseId.endswith('#deleted'):
            (valid, message) = validate({
                'databaseId': {
                    'value': request.databaseId,
                    'validator': 'ID'
                },
                'assetId': {
                    'value': request.assetId,
                    'validator': 'ASSET_ID'
                }
            })
            if not valid:
                logger.error(f"Validation error in asset index request: {message}")
                return IndexOperationResponse(
                    success=False,
                    message="Invalid input parameters",
                    indexName=opensearch_asset_index,
                    operation="validation_error"
                )
        else:
            # For archived assets, only validate the assetId
            (valid, message) = validate({
                'assetId': {
                    'value': request.assetId,
                    'validator': 'ASSET_ID'
                }
            })
            if not valid:
                logger.error(f"Validation error in asset index request: {message}")
                return IndexOperationResponse(
                    success=False,
                    message="Invalid input parameters",
                    indexName=opensearch_asset_index,
                    operation="validation_error"
                )
        
        if request.operation == "delete":
            # Check if asset still exists
            asset_details = get_asset_details(request.databaseId, request.assetId)
            
            if not asset_details:
                # Asset completely removed, delete from index
                success = delete_asset_document(request.databaseId, request.assetId)
                
                return IndexOperationResponse(
                    success=success,
                    message="Asset document deleted" if success else "Failed to delete asset document",
                    documentId=f"{request.databaseId}#{request.assetId}",
                    indexName=opensearch_asset_index,
                    operation="delete"
                )
            else:
                # Asset still exists, treat as update (might be archived)
                request.operation = "index"
        
        if request.operation == "index":
            # Get asset details
            asset_details = get_asset_details(request.databaseId, request.assetId)
            if not asset_details:
                logger.warning(f"Asset not found for indexing: {request.databaseId}/{request.assetId}")
                return IndexOperationResponse(
                    success=True,
                    message="Asset not found, skipping indexing",
                    indexName=opensearch_asset_index,
                    operation="skip"
                )
            
            # Get bucket details
            bucket_id = asset_details.get('bucketId')
            if not bucket_id:
                logger.warning(f"No bucket ID found for asset: {request.assetId}")
                return IndexOperationResponse(
                    success=True,
                    message="No bucket ID, skipping indexing",
                    indexName=opensearch_asset_index,
                    operation="skip"
                )
            
            bucket_details = get_bucket_details(bucket_id)
            if not bucket_details:
                logger.warning(f"Bucket details not found for bucket: {bucket_id}")
                return IndexOperationResponse(
                    success=True,
                    message="Bucket details not found, skipping indexing",
                    indexName=opensearch_asset_index,
                    operation="skip"
                )
            
            # Get asset metadata
            asset_metadata = get_asset_metadata(request.databaseId, request.assetId)
            
            # Get version information
            version_info = get_asset_version_info(request.assetId)
            
            # Get relationship flags
            relationship_flags = get_asset_relationship_flags(request.databaseId, request.assetId)
            
            # Check if asset is archived
            is_archived = is_asset_archived(asset_details) or request.isArchived
            
            # Build document
            document = build_asset_document(
                request, asset_details, bucket_details, 
                asset_metadata, version_info, relationship_flags, is_archived
            )
            
            # Index the document
            success = index_asset_document(document)
            
            doc_id = f"{request.databaseId}#{request.assetId}"
            
            return IndexOperationResponse(
                success=success,
                message="Asset document indexed" if success else "Failed to index asset document",
                documentId=doc_id,
                indexName=opensearch_asset_index,
                operation="index"
            )
        
        else:
            raise VAMSGeneralErrorResponse(f"Unknown operation: {request.operation}")
            
    except Exception as e:
        logger.exception(f"Error processing asset index request: {e}")
        return IndexOperationResponse(
            success=False,
            message=f"Error processing request: {str(e)}",
            indexName=opensearch_asset_index,
            operation=request.operation
        )

#######################
# Event Handlers
#######################

def handle_asset_stream(event_record: Dict[str, Any]) -> IndexOperationResponse:
    """Handle DynamoDB asset table stream for asset indexing"""
    try:
        event_name = event_record.get('eventName', '')
        dynamodb_data = event_record.get('dynamodb', {})
        
        # For REMOVE events with NEW_IMAGE stream type, extract IDs from Keys
        if event_name == 'REMOVE':
            # DynamoDB streams with NEW_IMAGE view type don't include OldImage
            # But Keys are always available for REMOVE events
            keys = dynamodb_data.get('Keys', {})
            database_id = keys.get('databaseId', {}).get('S')
            asset_id = keys.get('assetId', {}).get('S')
            
            if not database_id or not asset_id:
                logger.warning("Missing database ID or asset ID in REMOVE event keys")
                return IndexOperationResponse(
                    success=True,
                    message="Missing IDs in keys, skipping",
                    indexName=opensearch_asset_index,
                    operation="skip"
                )
            
            logger.info(f"Processing REMOVE event for asset: {database_id}/{asset_id}")
            
            # Create delete request
            request = AssetIndexRequest(
                databaseId=database_id,
                assetId=asset_id,
                operation="delete"
            )
            
            # Process the delete request
            return process_asset_index_request(request)
        
        # For INSERT/MODIFY events, use NewImage
        record_data = dynamodb_data.get('NewImage', {})
        
        if not record_data:
            logger.warning("No record data found in asset stream event")
            return IndexOperationResponse(
                success=True,
                message="No record data, skipping",
                indexName=opensearch_asset_index,
                operation="skip"
            )
        
        # Extract database ID and asset ID from DynamoDB record
        database_id = record_data.get('databaseId', {}).get('S')
        asset_id = record_data.get('assetId', {}).get('S')
        
        if not database_id or not asset_id:
            logger.warning("Missing database ID or asset ID in asset stream")
            return IndexOperationResponse(
                success=True,
                message="Missing IDs, skipping",
                indexName=opensearch_asset_index,
                operation="skip"
            )
        
        # For INSERT/MODIFY, always index
        logger.info(f"Processing {event_name} event for asset: {database_id}/{asset_id}")
        
        # Create asset index request
        request = AssetIndexRequest(
            databaseId=database_id,
            assetId=asset_id,
            operation="index"
        )
        
        # Process the request
        return process_asset_index_request(request)
        
    except Exception as e:
        logger.exception(f"Error handling asset stream: {e}")
        return IndexOperationResponse(
            success=False,
            message=f"Error handling asset stream: {str(e)}",
            indexName=opensearch_asset_index,
            operation="error"
        )

def handle_metadata_stream(event_record: Dict[str, Any]) -> IndexOperationResponse:
    """Handle DynamoDB metadata table stream for asset indexing"""
    try:
        event_name = event_record.get('eventName', '')
        dynamodb_data = event_record.get('dynamodb', {})
        
        # For REMOVE events, Keys are always present regardless of StreamViewType
        # We should use Keys directly since OldImage won't exist for NEW_IMAGE stream type
        if event_name == 'REMOVE':
            # Get composite key from Keys (always present for REMOVE)
            keys = dynamodb_data.get('Keys', {})
            if not keys or 'databaseId:assetId:filePath' not in keys:
                logger.warning("Missing Keys in REMOVE event")
                return IndexOperationResponse(
                    success=True,
                    message="Missing Keys in REMOVE event, skipping",
                    indexName=opensearch_asset_index,
                    operation="skip"
                )
            
            composite_key = keys.get('databaseId:assetId:filePath', {}).get('S')
            if not composite_key:
                logger.warning("Missing composite key value in REMOVE event")
                return IndexOperationResponse(
                    success=True,
                    message="Missing composite key value, skipping",
                    indexName=opensearch_asset_index,
                    operation="skip"
                )
        else:
            # For INSERT/MODIFY, get the record data from NewImage
            record_data = dynamodb_data.get('NewImage', {})
            
            if not record_data:
                logger.warning("No NewImage found in INSERT/MODIFY event")
                return IndexOperationResponse(
                    success=True,
                    message="No NewImage data, skipping",
                    indexName=opensearch_asset_index,
                    operation="skip"
                )
            
            # Parse composite key format (databaseId:assetId:filePath)
            composite_key = record_data.get('databaseId:assetId:filePath', {}).get('S')
            
            if not composite_key:
                logger.warning("Missing composite key in metadata stream")
                return IndexOperationResponse(
                    success=True,
                    message="Missing composite key, skipping",
                    indexName=opensearch_asset_index,
                    operation="skip"
                )
        
        # Parse composite key
        parts = composite_key.split(':', 2)
        if len(parts) != 3:
            logger.warning(f"Invalid composite key format: {composite_key}")
            return IndexOperationResponse(
                success=True,
                message="Invalid composite key format, skipping",
                indexName=opensearch_asset_index,
                operation="skip"
            )
        
        database_id, asset_id, file_path = parts
        
        # Only process if it's asset-level (file_path is "/")
        if file_path != '/':
            logger.info("File-level metadata, skipping for asset index")
            return IndexOperationResponse(
                success=True,
                message="File-level metadata, skipping",
                indexName=opensearch_asset_index,
                operation="skip"
            )
        
        # Create asset index request
        request = AssetIndexRequest(
            databaseId=database_id,
            assetId=asset_id,
            operation="index"  # Always index for metadata changes
        )
        
        # Process the request
        return process_asset_index_request(request)
        
    except Exception as e:
        logger.exception(f"Error handling metadata stream: {e}")
        return IndexOperationResponse(
            success=False,
            message=f"Error handling metadata stream: {str(e)}",
            indexName=opensearch_asset_index,
            operation="error"
        )

def handle_asset_links_stream(event_record: Dict[str, Any]) -> List[IndexOperationResponse]:
    """Handle DynamoDB asset links table stream for asset indexing"""
    try:
        event_name = event_record.get('eventName', '')
        dynamodb_data = event_record.get('dynamodb', {})
        
        # For REMOVE events, Keys are always present regardless of StreamViewType
        # We should use Keys directly since OldImage won't exist for NEW_IMAGE stream type
        if event_name == 'REMOVE':
            # Get keys from Keys (always present for REMOVE)
            keys = dynamodb_data.get('Keys', {})
            if not keys:
                logger.warning("Missing Keys in REMOVE event for asset links")
                return [IndexOperationResponse(
                    success=True,
                    message="Missing Keys in REMOVE event, skipping",
                    indexName=opensearch_asset_index,
                    operation="skip"
                )]
            
            # Extract from and to asset information from Keys
            from_asset_key = keys.get('fromAssetDatabaseId:fromAssetId', {}).get('S')
            to_asset_key = keys.get('toAssetDatabaseId:toAssetId', {}).get('S')
        else:
            # For INSERT/MODIFY, use NewImage
            record_data = dynamodb_data.get('NewImage', {})
            
            if not record_data:
                logger.warning("No NewImage found in INSERT/MODIFY event for asset links")
                return [IndexOperationResponse(
                    success=True,
                    message="No NewImage data, skipping",
                    indexName=opensearch_asset_index,
                    operation="skip"
                )]
            
            # Extract from and to asset information from NewImage
            from_asset_key = record_data.get('fromAssetDatabaseId:fromAssetId', {}).get('S')
            to_asset_key = record_data.get('toAssetDatabaseId:toAssetId', {}).get('S')
        
        if not from_asset_key or not to_asset_key:
            logger.warning("Missing asset keys in asset links stream")
            return [IndexOperationResponse(
                success=True,
                message="Missing asset keys, skipping",
                indexName=opensearch_asset_index,
                operation="skip"
            )]
        
        # Parse asset keys
        from_database_id, from_asset_id = from_asset_key.split(':', 1)
        to_database_id, to_asset_id = to_asset_key.split(':', 1)
        
        results = []
        
        # Update both assets since their relationship flags may have changed
        for database_id, asset_id in [(from_database_id, from_asset_id), (to_database_id, to_asset_id)]:
            request = AssetIndexRequest(
                databaseId=database_id,
                assetId=asset_id,
                operation="index"  # Always index for link changes
            )
            
            result = process_asset_index_request(request)
            results.append(result)
        
        return results
        
    except Exception as e:
        logger.exception(f"Error handling asset links stream: {e}")
        return [IndexOperationResponse(
            success=False,
            message=f"Error handling asset links stream: {str(e)}",
            indexName=opensearch_asset_index,
            operation="error"
        )]

#######################
# Lambda Handler
#######################

def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for asset indexing operations"""
    global claims_and_roles
    
    try:
        logger.info(f"Processing asset indexing event: {json.dumps(event, default=str)}")
        
        results = []
        
        # Handle different event sources
        if 'Records' in event:
            for record in event['Records']:
                event_source = record.get('eventSource', '')
                
                if event_source == 'aws:dynamodb':
                    # Determine which table based on event source ARN
                    source_arn = record.get('eventSourceARN', '')
                    
                    if asset_storage_table_name in source_arn:
                        # Asset table stream
                        result = handle_asset_stream(record)
                        results.append(result)
                        
                    elif asset_file_metadata_table_name in source_arn:
                        # Metadata table stream
                        result = handle_metadata_stream(record)
                        results.append(result)
                        
                    elif asset_links_table_name in source_arn:
                        # Asset links table stream
                        link_results = handle_asset_links_stream(record)
                        results.extend(link_results)
                        
                    else:
                        logger.warning(f"Unknown DynamoDB table in source ARN: {source_arn}")
                
                elif event_source == 'aws:sqs':
                    # SQS message (contains SNS message with DynamoDB stream record)
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
                            # This is the direct SNS→SQS path - check by eventSourceARN or eventName
                            source_arn = sns_message.get('eventSourceARN', '')
                            
                            if asset_storage_table_name in source_arn:
                                # Direct asset table stream from SNS queuing Lambda
                                result = handle_asset_stream(sns_message)
                                results.append(result)
                            elif asset_file_metadata_table_name in source_arn:
                                # Direct metadata table stream from SNS queuing Lambda
                                result = handle_metadata_stream(sns_message)
                                results.append(result)
                            elif asset_links_table_name in source_arn:
                                # Direct asset links table stream from SNS queuing Lambda
                                link_results = handle_asset_links_stream(sns_message)
                                results.extend(link_results)
                            # Fallback: check if it's a DynamoDB stream by eventName and try to determine type
                            elif sns_message.get('eventName') in ['INSERT', 'MODIFY', 'REMOVE']:
                                # This is a DynamoDB stream record, try to determine type by data structure
                                dynamodb_data = sns_message.get('dynamodb', {})
                                new_image = dynamodb_data.get('NewImage', {})
                                old_image = dynamodb_data.get('OldImage', {})
                                keys = dynamodb_data.get('Keys', {})
                                
                                # Check if it has assetLinkId (asset links table)
                                if 'assetLinkId' in new_image or 'assetLinkId' in old_image or 'assetLinkId' in keys:
                                    link_results = handle_asset_links_stream(sns_message)
                                    results.extend(link_results)
                                # Check if it has assetId and databaseId (could be asset or metadata)
                                elif ('assetId' in new_image or 'assetId' in old_image or 'assetId' in keys) and \
                                     ('databaseId' in new_image or 'databaseId' in old_image or 'databaseId' in keys):
                                    # Try to determine if it's asset or metadata by checking assetId format
                                    asset_id_value = new_image.get('assetId', {}).get('S') or \
                                                    old_image.get('assetId', {}).get('S') or \
                                                    keys.get('assetId', {}).get('S')
                                    
                                    if asset_id_value and '/' in asset_id_value:
                                        # Has path, likely metadata
                                        result = handle_metadata_stream(sns_message)
                                        results.append(result)
                                    else:
                                        # No path, likely asset
                                        result = handle_asset_stream(sns_message)
                                        results.append(result)
                                else:
                                    logger.warning(f"Cannot determine DynamoDB table type from SNS message structure")
                            
                            else:
                                logger.warning(f"SNS message does not contain recognized event format (no eventSource, eventName, or Records): {list(sns_message.keys())}")
                        else:
                            logger.warning("SQS message is not an SNS notification")
                    except json.JSONDecodeError as e:
                        logger.exception(f"Error parsing SQS/SNS message: {e}")
                        results.append(IndexOperationResponse(
                            success=False,
                            message=f"Error parsing SQS/SNS message: {str(e)}",
                            indexName=opensearch_asset_index,
                            operation="error"
                        ))
                        
                else:
                    logger.warning(f"Unknown event source: {event_source}")
        
        else:
            # Direct invocation with AssetIndexRequest
            try:
                request = parse(event, model=AssetIndexRequest)
                result = process_asset_index_request(request)
                results.append(result)
            except ValidationError as v:
                logger.exception(f"Validation error: {v}")
                return validation_error(body={'message': str(v)})
        
        # Summarize results
        successful = sum(1 for r in results if r.success)
        total = len(results)
        
        response_body = {
            'message': f"Processed {successful}/{total} asset indexing operations successfully",
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
        logger.exception(f"Internal error in asset indexer: {e}")
        return internal_error()