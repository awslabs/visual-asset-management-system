"""
Garnet Framework File Indexer for VAMS.

This Lambda function processes file change events and converts file data
to NGSI-LD format for ingestion into the Garnet Framework knowledge graph.

Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
SPDX-License-Identifier: Apache-2.0
"""

import os
import json
import boto3
import uuid
import urllib.parse
from decimal import Decimal
from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from botocore.config import Config
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from customLogging.logger import safeLogger
from common.validators import validate
from models.common import VAMSGeneralErrorResponse

# Helper function to convert Decimal to int/float for JSON serialization
def decimal_to_number(obj):
    """Convert Decimal objects to int or float for JSON serialization"""
    if isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

# Configure AWS clients with retry configuration
retry_config = Config(
    retries={
        'max_attempts': 5,
        'mode': 'adaptive'
    }
)

dynamodb = boto3.resource('dynamodb', config=retry_config)
s3_client = boto3.client('s3', config=retry_config)
sqs = boto3.client('sqs', config=retry_config)
logger = safeLogger(service_name="GarnetFileIndexer")

# Excluded patterns or prefixes from file paths to exclude
excluded_prefixes = ['pipeline', 'pipelines', 'preview', 'previews', 'temp-upload', 'temp-uploads']
excluded_patterns = ['.previewFile.']

# Load environment variables with error handling
try:
    asset_storage_table_name = os.environ["ASSET_STORAGE_TABLE_NAME"]
    asset_file_metadata_storage_table_name = os.environ["ASSET_FILE_METADATA_STORAGE_TABLE_NAME"]
    file_attribute_storage_table_name = os.environ["FILE_ATTRIBUTE_STORAGE_TABLE_NAME"]
    s3_asset_buckets_storage_table_name = os.environ["S3_ASSET_BUCKETS_STORAGE_TABLE_NAME"]
    garnet_ingestion_queue_url = os.environ["GARNET_INGESTION_QUEUE_URL"]
    garnet_api_endpoint = os.environ["GARNET_API_ENDPOINT"]
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

# Initialize DynamoDB tables
asset_storage_table = dynamodb.Table(asset_storage_table_name)
asset_file_metadata_table = dynamodb.Table(asset_file_metadata_storage_table_name)
file_attribute_table = dynamodb.Table(file_attribute_storage_table_name)
s3_asset_buckets_table = dynamodb.Table(s3_asset_buckets_storage_table_name)

#######################
# Utility Functions
#######################

def extract_file_extension(file_path: str) -> Optional[str]:
    """Extract file extension from file path"""
    if '.' in file_path and not file_path.endswith('/'):
        return file_path.split('.')[-1].lower()
    return None

def is_folder_path(file_path: str) -> bool:
    """Check if path represents a folder"""
    return file_path.endswith('/') or '.' not in os.path.basename(file_path)

def should_skip_file(s3_key: str) -> bool:
    """Check if file should be skipped based on excluded patterns/prefixes"""
    # Skip folder markers
    if s3_key.endswith('/'):
        return True
    
    # Check if s3_key contains any excluded patterns
    if any(pattern in s3_key for pattern in excluded_patterns):
        return True
    
    # Check if s3_key starts with any excluded prefixes
    path_parts = s3_key.split('/')
    for part in path_parts:
        if any(part.startswith(prefix) for prefix in excluded_prefixes):
            return True
    
    return False

#######################
# Data Retrieval Functions
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

def get_file_metadata(database_id: str, asset_id: str, file_path: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Get file-specific metadata AND attributes from schema tables.
    Returns separate dictionaries for metadata and attributes with value and type information.
    
    Returns:
        Tuple of (metadata_dict, attributes_dict)
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
            metadata_value_type = item.get('metadataValueType', 'string')
            
            # Skip system metadata records
            if metadata_key == 'REINDEX_METADATA_RECORD':
                logger.debug(f"Skipping system metadata: {metadata_key}")
                continue
            
            if metadata_key and metadata_value:
                metadata[metadata_key] = {
                    'value': metadata_value,
                    'type': metadata_value_type
                }
        
        # Query fileAttributeStorageTable for attribute fields
        response = file_attribute_table.query(
            IndexName='DatabaseIdAssetIdFilePathIndex',
            KeyConditionExpression=Key('databaseId:assetId:filePath').eq(composite_key)
        )
        
        for item in response.get('Items', []):
            attribute_key = item.get('attributeKey')
            attribute_value = item.get('attributeValue')
            attribute_value_type = item.get('attributeValueType', 'string')
            
            if attribute_key and attribute_value:
                attributes[attribute_key] = {
                    'value': attribute_value,
                    'type': attribute_value_type
                }
        
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
                if key in ['vams-primarytype']:
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
                    
                    delete_markers = versions_response.get('DeleteMarkers', [])
                    versions = versions_response.get('Versions', [])
                    
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

#######################
# NGSI-LD Conversion Functions
#######################

def convert_file_to_ngsi_ld(
    database_id: str,
    asset_id: str,
    file_path: str,
    asset_data: Dict[str, Any],
    bucket_details: Dict[str, Any],
    file_metadata: Dict[str, Any],
    file_attributes: Dict[str, Any],
    s3_file_info: Optional[Dict[str, Any]],
    is_archived: bool
) -> Dict[str, Any]:
    """
    Convert VAMS file data to NGSI-LD format for Garnet Framework.
    
    NGSI-LD Reference: https://garnet-framework.dev/docs/getting-started/ngsi-ld
    
    Args:
        database_id: The database ID
        asset_id: The asset ID
        file_path: The relative file path
        asset_data: Parent asset information
        bucket_details: S3 bucket details
        file_metadata: File metadata dictionary
        file_attributes: File attributes dictionary
        s3_file_info: S3 file information
        is_archived: Whether file is archived
        
    Returns:
        NGSI-LD formatted entity
    """
    try:
        # URL encode the file path for the entity ID
        encoded_file_path = urllib.parse.quote(file_path, safe='')
        
        # Create base NGSI-LD entity
        ngsi_ld_entity = {
            "id": f"urn:vams:file:{database_id}:{asset_id}:{encoded_file_path}",
            "type": "VAMSFile",
            "scope": [f"/Database/{database_id}/Asset/{asset_id}/File/{encoded_file_path}"],
            # "@context": [
            #     "https://uri.etsi.org/ngsi-ld/v1/ngsi-ld-core-context.jsonld",
            #     {
            #         "vams": "https://vams.aws.com/ontology/",
            #         "VAMSFile": "vams:File",
            #         "filePath": "vams:filePath",
            #         "fileExtension": "vams:fileExtension",
            #         "fileSize": "vams:fileSize",
            #         "lastModified": "vams:lastModified",
            #         "contentType": "vams:contentType",
            #         "etag": "vams:etag",
            #         "s3VersionId": "vams:s3VersionId",
            #         "s3Key": "vams:s3Key",
            #         "bucketName": "vams:bucketName",
            #         "assetName": "vams:assetName",
            #         "isArchived": "vams:isArchived",
            #         "belongsToAsset": "vams:belongsToAsset"
            #     }
            # ]
        }
        
        # Add file path property
        ngsi_ld_entity["filePath"] = {
            "type": "Property",
            "value": file_path
        }
        
        # Add file extension if available
        file_ext = extract_file_extension(file_path)
        if file_ext:
            ngsi_ld_entity["fileExtension"] = {
                "type": "Property",
                "value": file_ext
            }
        
        # Add S3 file information if available
        if s3_file_info:
            if s3_file_info.get('size') is not None:
                ngsi_ld_entity["fileSize"] = {
                    "type": "Property",
                    "value": s3_file_info['size']
                }
            
            if s3_file_info.get('lastModified'):
                ngsi_ld_entity["lastModified"] = {
                    "type": "Property",
                    "value": {
                        "@type": "DateTime",
                        "@value": s3_file_info['lastModified']
                    }
                }
            
            if s3_file_info.get('contentType'):
                ngsi_ld_entity["contentType"] = {
                    "type": "Property",
                    "value": s3_file_info['contentType']
                }
            
            if s3_file_info.get('etag'):
                ngsi_ld_entity["etag"] = {
                    "type": "Property",
                    "value": s3_file_info['etag']
                }
            
            if s3_file_info.get('versionId'):
                ngsi_ld_entity["s3VersionId"] = {
                    "type": "Property",
                    "value": s3_file_info['versionId']
                }
        
        # Add bucket information
        if bucket_details:
            if bucket_details.get('bucketName'):
                ngsi_ld_entity["bucketName"] = {
                    "type": "Property",
                    "value": bucket_details['bucketName']
                }
            
            # Calculate full S3 key
            asset_location = asset_data.get('assetLocation', {})
            asset_base_key = asset_location.get('Key', f"{bucket_details['baseAssetsPrefix']}{asset_id}/")
            s3_key = asset_base_key + file_path.lstrip('/')
            
            ngsi_ld_entity["s3Key"] = {
                "type": "Property",
                "value": s3_key
            }
        
        # Add asset name for context
        if asset_data.get('assetName'):
            ngsi_ld_entity["assetName"] = {
                "type": "Property",
                "value": asset_data['assetName']
            }
        
        # Add archived status
        ngsi_ld_entity["isArchived"] = {
            "type": "Property",
            "value": is_archived
        }
        
        # Add custom metadata as properties using metadataValueType
        if file_metadata:
            for key, metadata_info in file_metadata.items():
                # Prefix custom metadata to avoid conflicts
                metadata_key = f"metadata_{key}"
                
                # Extract value and type from metadata info
                metadata_value = metadata_info.get('value')
                metadata_type = metadata_info.get('type', 'string').lower()
                
                # Use metadataValueType to determine NGSI-LD property type
                if metadata_type in ['geopoint', 'geojson']:
                    # Parse the value as JSON for GeoJSON types
                    try:
                        geo_value = json.loads(metadata_value) if isinstance(metadata_value, str) else metadata_value
                        ngsi_ld_entity[metadata_key] = {
                            "type": "GeoProperty",
                            "value": geo_value
                        }
                    except (json.JSONDecodeError, TypeError) as e:
                        logger.warning(f"Failed to parse GeoJSON metadata {key}: {e}, using as Property")
                        ngsi_ld_entity[metadata_key] = {
                            "type": "Property",
                            "value": metadata_value
                        }
                
                elif metadata_type == 'json':
                    # Parse the value as JSON for JSON type
                    try:
                        json_value = json.loads(metadata_value) if isinstance(metadata_value, str) else metadata_value
                        ngsi_ld_entity[metadata_key] = {
                            "type": "JsonProperty",
                            "json": json_value
                        }
                    except (json.JSONDecodeError, TypeError) as e:
                        logger.warning(f"Failed to parse JSON metadata {key}: {e}, using as Property")
                        ngsi_ld_entity[metadata_key] = {
                            "type": "Property",
                            "value": metadata_value
                        }
                
                elif metadata_type in ['xyz', 'wxyz', 'matrix4x4', 'lla']:
                    # These are JSON structures but should be stored as JsonProperty
                    try:
                        json_value = json.loads(metadata_value) if isinstance(metadata_value, str) else metadata_value
                        ngsi_ld_entity[metadata_key] = {
                            "type": "JsonProperty",
                            "json": json_value
                        }
                    except (json.JSONDecodeError, TypeError) as e:
                        logger.warning(f"Failed to parse {metadata_type} metadata {key}: {e}, using as Property")
                        ngsi_ld_entity[metadata_key] = {
                            "type": "Property",
                            "value": metadata_value
                        }
                
                else:
                    # All other types (string, number, boolean, date, etc.) use Property
                    ngsi_ld_entity[metadata_key] = {
                        "type": "Property",
                        "value": metadata_value
                    }
        
        # Add custom attributes as properties using attributeValueType
        # Note: File attributes only support STRING type per VAMS validation
        if file_attributes:
            for key, attribute_info in file_attributes.items():
                # Prefix custom attributes to avoid conflicts
                attribute_key = f"attribute_{key}"
                
                # Extract value and type from attribute info
                attribute_value = attribute_info.get('value')
                attribute_type = attribute_info.get('type', 'string').lower()
                
                # File attributes are always strings per VAMS validation, use Property
                ngsi_ld_entity[attribute_key] = {
                    "type": "Property",
                    "value": attribute_value
                }
        
        # Add relationship to parent asset
        ngsi_ld_entity["belongsToAsset"] = {
            "type": "Relationship",
            "object": f"urn:vams:asset:{database_id}:{asset_id}"
        }
        
        return ngsi_ld_entity
        
    except Exception as e:
        logger.exception(f"Error converting file to NGSI-LD: {e}")
        raise VAMSGeneralErrorResponse("Error converting file data to NGSI-LD format")

#######################
# Garnet Integration Functions
#######################

def send_to_garnet_ingestion_queue(ngsi_ld_entity: Dict[str, Any]) -> bool:
    """
    Send NGSI-LD entity to Garnet Framework ingestion queue.
    
    Args:
        ngsi_ld_entity: The NGSI-LD formatted entity
        
    Returns:
        True if successful, False otherwise
    """
    try:
        # Log the entity being sent (use decimal_to_number for logging)
        logger.info(f"Sending NGSI-LD entity to Garnet ingestion queue: {json.dumps(ngsi_ld_entity, indent=2, default=decimal_to_number)}")
        
        # Send NGSI-LD entity directly to SQS queue (use decimal_to_number for serialization)
        response = sqs.send_message(
            QueueUrl=garnet_ingestion_queue_url,
            MessageBody=json.dumps(ngsi_ld_entity, default=decimal_to_number),
            MessageAttributes={
                'entityType': {
                    'StringValue': 'VAMSFile',
                    'DataType': 'String'
                },
                'source': {
                    'StringValue': 'vams-file-indexer',
                    'DataType': 'String'
                }
            }
        )
        
        logger.info(f"Successfully sent file entity to Garnet ingestion queue: {ngsi_ld_entity['id']}, MessageId: {response.get('MessageId')}")
        return True
        
    except Exception as e:
        logger.exception(f"Error sending entity to Garnet ingestion queue: {e}")
        return False

#######################
# Business Logic Functions
#######################

def handle_s3_notification(event_record: Dict[str, Any]) -> bool:
    """
    Handle S3 bucket notification for file indexing.
    Uses same pattern as OpenSearch fileIndexer.py handle_s3_notification().
    
    Note: S3 events come through sqsBucketSync lambda which adds ASSET_BUCKET_NAME/PREFIX
    
    Args:
        event_record: S3 event record
        
    Returns:
        True if successful, False otherwise
    """
    try:
        # Extract S3 information from event
        s3_info = event_record.get('s3', {})
        bucket_name = s3_info.get('bucket', {}).get('name')
        s3_key = s3_info.get('object', {}).get('key')
        event_name = event_record.get('eventName', '')
        
        if not bucket_name or not s3_key:
            logger.error("Missing S3 bucket or key information")
            return False
        
        # URL decode the S3 key
        s3_key = urllib.parse.unquote_plus(s3_key)
        
        # Skip folder markers
        if s3_key.endswith('/'):
            logger.info(f"Skipping folder marker: {s3_key}")
            return True
        
        # Skip excluded prefixes and patterns
        if any(pattern in s3_key for pattern in excluded_patterns):
            logger.info(f"Ignoring file with excluded pattern: {s3_key}")
            return True
        
        path_parts = s3_key.split('/')
        for part in path_parts:
            if any(part.startswith(prefix) for prefix in excluded_prefixes):
                logger.info(f"Ignoring excluded prefix file: {s3_key}")
                return True
        
        logger.info(f"Processing S3 event: {event_name} for {s3_key}")
        
        # Get S3 object metadata to extract asset/database IDs
        try:
            s3_response = s3_client.head_object(Bucket=bucket_name, Key=s3_key)
            s3_metadata = s3_response.get('Metadata', {})
            
            asset_id = s3_metadata.get('assetid')
            database_id = s3_metadata.get('databaseid')
            
            if not asset_id or not database_id:
                logger.warning(f"Missing asset/database ID in S3 metadata for {s3_key}")
                return True  # Skip, not an error
            
        except ClientError as e:
            logger.warning(f"Error getting S3 object metadata: {e}")
            return True  # Skip, not an error
        
        # Get asset details
        asset_details = get_asset_details(database_id, asset_id)
        if not asset_details:
            logger.warning(f"Asset not found for S3 file: {database_id}/{asset_id}")
            return True  # Skip, not an error
        
        # Get bucket details
        bucket_details = get_bucket_details(asset_details.get('bucketId'))
        if not bucket_details:
            logger.warning(f"Bucket details not found for asset: {asset_id}")
            return True  # Skip, not an error
        
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
        
        # Get file metadata and attributes
        file_metadata, file_attributes = get_file_metadata(database_id, asset_id, relative_path)
        
        # Get S3 file information
        s3_file_info, is_archived = get_s3_file_info(bucket_name, s3_key)
        
        # Convert to NGSI-LD format
        ngsi_ld_entity = convert_file_to_ngsi_ld(
            database_id,
            asset_id,
            relative_path,
            asset_details,
            bucket_details,
            file_metadata,
            file_attributes,
            s3_file_info,
            is_archived
        )
        
        # Send to Garnet ingestion queue
        success = send_to_garnet_ingestion_queue(ngsi_ld_entity)
        if success:
            logger.info(f"Successfully sent file to Garnet from S3 event: {database_id}/{asset_id}{relative_path}")
        else:
            logger.error(f"Failed to send file to Garnet from S3 event: {database_id}/{asset_id}{relative_path}")
        return success
        
    except Exception as e:
        logger.exception(f"Error handling S3 notification: {e}")
        return False

def handle_file_metadata_stream(event_record: Dict[str, Any]) -> bool:
    """
    Handle DynamoDB file metadata/attribute table stream for Garnet indexing.
    Uses same pattern as OpenSearch fileIndexer.py handle_metadata_stream().
    
    Args:
        event_record: DynamoDB stream record
        
    Returns:
        True if successful, False otherwise
    """
    try:
        event_name = event_record.get('eventName', '')
        dynamodb_data = event_record.get('dynamodb', {})
        
        # Extract composite key
        composite_key = None
        
        if event_name == 'REMOVE':
            # For REMOVE events, use Keys
            keys = dynamodb_data.get('Keys', {})
            composite_key = keys.get('databaseId:assetId:filePath', {}).get('S')
        else:
            # For INSERT/MODIFY events, use NewImage
            record_data = dynamodb_data.get('NewImage', {})
            composite_key = record_data.get('databaseId:assetId:filePath', {}).get('S')
        
        if not composite_key:
            logger.warning("Missing composite key in file metadata stream")
            return True  # Skip, not an error
        
        # Parse composite key
        parts = composite_key.split(':', 2)
        if len(parts) != 3:
            logger.warning(f"Invalid composite key format: {composite_key}")
            return True  # Skip, not an error
        
        database_id, asset_id, file_path = parts
        
        # Skip asset-level metadata (file_path = "/")
        if file_path == '/':
            logger.info("Asset-level metadata, skipping for file indexer")
            return True  # Skip, not an error
        
        # Skip folder paths
        if is_folder_path(file_path):
            logger.info(f"Folder path metadata, skipping: {file_path}")
            return True  # Skip, not an error
        
        logger.info(f"Processing {event_name} event for file metadata: {database_id}/{asset_id}{file_path}")
        
        # For any metadata/attribute change, re-index the entire file
        asset_details = get_asset_details(database_id, asset_id)
        if not asset_details:
            logger.warning(f"Asset not found for file metadata indexing: {database_id}/{asset_id}")
            return True  # Not an error, asset might have been deleted
        
        # Get bucket details
        bucket_id = asset_details.get('bucketId')
        if not bucket_id:
            logger.warning(f"No bucket ID found for asset: {asset_id}")
            return True  # Skip, not an error
        
        bucket_details = get_bucket_details(bucket_id)
        if not bucket_details:
            logger.warning(f"Bucket details not found for bucket: {bucket_id}")
            return True  # Skip, not an error
        
        # Get file metadata and attributes
        file_metadata, file_attributes = get_file_metadata(database_id, asset_id, file_path)
        
        # Get S3 file information
        asset_location = asset_details.get('assetLocation', {})
        asset_base_key = asset_location.get('Key', f"{bucket_details['baseAssetsPrefix']}{asset_id}/")
        s3_key = asset_base_key + file_path.lstrip('/')
        
        s3_file_info = None
        is_archived = False
        if bucket_details.get('bucketName'):
            s3_file_info, s3_is_archived = get_s3_file_info(bucket_details['bucketName'], s3_key)
            if s3_is_archived:
                is_archived = True
        
        # Convert to NGSI-LD format
        ngsi_ld_entity = convert_file_to_ngsi_ld(
            database_id,
            asset_id,
            file_path,
            asset_details,
            bucket_details,
            file_metadata,
            file_attributes,
            s3_file_info,
            is_archived
        )
        
        # Send to Garnet ingestion queue
        success = send_to_garnet_ingestion_queue(ngsi_ld_entity)
        if success:
            logger.info(f"Successfully sent file to Garnet after metadata change: {database_id}/{asset_id}{file_path}")
        else:
            logger.error(f"Failed to send file to Garnet after metadata change: {database_id}/{asset_id}{file_path}")
        return success
        
    except Exception as e:
        logger.exception(f"Error handling file metadata stream: {e}")
        return False

#######################
# Lambda Handler
#######################

def lambda_handler(event, context: LambdaContext) -> Dict[str, Any]:
    """
    Lambda handler for Garnet Framework file indexing.
    Uses same pattern as OpenSearch fileIndexer.py lambda_handler().
    
    Handles both DynamoDB metadata streams AND S3 bucket sync events.
    """
    try:
        logger.info(f"Processing file indexing event: {json.dumps(event, default=str)}")
        
        successful_records = 0
        failed_records = 0
        
        # Extract bucket info from top-level event (if present from sqsBucketSync)
        asset_bucket_name = event.get('ASSET_BUCKET_NAME')
        asset_bucket_prefix = event.get('ASSET_BUCKET_PREFIX', '/')
        
        # Handle different event sources (same pattern as OpenSearch indexers)
        if 'Records' in event:
            for record in event['Records']:
                event_source = record.get('eventSource', '')
                
                if event_source == 'aws:sqs':
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
                            
                            # Check if SNS message is a direct DynamoDB stream record
                            if sns_message.get('eventSource') == 'aws:dynamodb' or \
                               sns_message.get('eventName') in ['INSERT', 'MODIFY', 'REMOVE']:
                                # Direct DynamoDB stream record from SNS queuing Lambda
                                success = handle_file_metadata_stream(sns_message)
                                if success:
                                    successful_records += 1
                                else:
                                    failed_records += 1
                            
                            # Check if SNS message contains Records array (nested from sqsBucketSync)
                            elif 'Records' in sns_message:
                                for inner_record in sns_message['Records']:
                                    inner_event_source = inner_record.get('eventSource', '')
                                    
                                    if inner_event_source == 'aws:s3':
                                        # S3 record in SNS message
                                        if asset_bucket_name:
                                            inner_record['ASSET_BUCKET_NAME'] = asset_bucket_name
                                            inner_record['ASSET_BUCKET_PREFIX'] = asset_bucket_prefix
                                        success = handle_s3_notification(inner_record)
                                        if success:
                                            successful_records += 1
                                        else:
                                            failed_records += 1
                                    
                                    elif inner_event_source == 'aws:sqs':
                                        # Nested SQS record (from sqsBucketSync)
                                        try:
                                            inner_body = inner_record.get('body', '')
                                            if isinstance(inner_body, str):
                                                inner_body = json.loads(inner_body)
                                            
                                            if inner_body.get('Type') == 'Notification' and inner_body.get('Message'):
                                                inner_sns_message = inner_body.get('Message')
                                                if isinstance(inner_sns_message, str):
                                                    inner_sns_message = json.loads(inner_sns_message)
                                                
                                                if 'Records' in inner_sns_message:
                                                    for s3_record in inner_sns_message['Records']:
                                                        if s3_record.get('eventSource') == 'aws:s3':
                                                            nested_bucket_name = inner_sns_message.get('ASSET_BUCKET_NAME', asset_bucket_name)
                                                            nested_bucket_prefix = inner_sns_message.get('ASSET_BUCKET_PREFIX', asset_bucket_prefix)
                                                            
                                                            if nested_bucket_name:
                                                                s3_record['ASSET_BUCKET_NAME'] = nested_bucket_name
                                                                s3_record['ASSET_BUCKET_PREFIX'] = nested_bucket_prefix
                                                            
                                                            success = handle_s3_notification(s3_record)
                                                            if success:
                                                                successful_records += 1
                                                            else:
                                                                failed_records += 1
                                        except json.JSONDecodeError as inner_e:
                                            logger.exception(f"Error parsing nested SQS/SNS message: {inner_e}")
                                            failed_records += 1
                                    
                                    else:
                                        logger.warning(f"Unknown inner event source: {inner_event_source}")
                                        failed_records += 1
                            
                            else:
                                logger.warning(f"SNS message format not recognized: {list(sns_message.keys())}")
                                failed_records += 1
                        else:
                            logger.warning("SQS message is not an SNS notification")
                            failed_records += 1
                    except json.JSONDecodeError as e:
                        logger.exception(f"Error parsing SQS/SNS message: {e}")
                        failed_records += 1
                        
                else:
                    logger.warning(f"Unknown event source: {event_source}")
                    failed_records += 1
        
        logger.info(f"Garnet file indexing completed: {successful_records} successful, {failed_records} failed")
        
        return {
            'statusCode': 200,
            'body': {
                'message': 'Garnet file indexing completed',
                'successful_records': successful_records,
                'failed_records': failed_records
            }
        }
        
    except Exception as e:
        logger.exception(f"Error in Garnet file indexer lambda handler: {e}")
        return {
            'statusCode': 500,
            'body': {
                'message': 'Error processing Garnet file indexing',
                'error': str(e)
            }
        }