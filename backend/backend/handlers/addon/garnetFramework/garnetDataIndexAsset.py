"""
Garnet Framework Asset Indexer for VAMS.

This Lambda function processes asset change events and converts asset data
to NGSI-LD format for ingestion into the Garnet Framework knowledge graph.

Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
SPDX-License-Identifier: Apache-2.0
"""

import os
import json
import boto3
import uuid
from decimal import Decimal
from datetime import datetime
from typing import Dict, Any, Optional, List
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
sqs = boto3.client('sqs', config=retry_config)
logger = safeLogger(service_name="GarnetAssetIndexer")

# Load environment variables with error handling
try:
    asset_storage_table_name = os.environ["ASSET_STORAGE_TABLE_NAME"]
    asset_file_metadata_storage_table_name = os.environ["ASSET_FILE_METADATA_STORAGE_TABLE_NAME"]
    s3_asset_buckets_storage_table_name = os.environ["S3_ASSET_BUCKETS_STORAGE_TABLE_NAME"]
    asset_links_storage_table_v2_name = os.environ["ASSET_LINKS_STORAGE_TABLE_V2_NAME"]
    asset_links_metadata_storage_table_name = os.environ["ASSET_LINKS_METADATA_STORAGE_TABLE_NAME"]
    asset_versions_storage_table_name = os.environ["ASSET_VERSIONS_STORAGE_TABLE_NAME"]
    garnet_ingestion_queue_url = os.environ["GARNET_INGESTION_QUEUE_URL"]
    garnet_api_endpoint = os.environ["GARNET_API_ENDPOINT"]
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

# Initialize DynamoDB tables
asset_storage_table = dynamodb.Table(asset_storage_table_name)
asset_file_metadata_table = dynamodb.Table(asset_file_metadata_storage_table_name)
s3_asset_buckets_table = dynamodb.Table(s3_asset_buckets_storage_table_name)
asset_links_table = dynamodb.Table(asset_links_storage_table_v2_name)
asset_links_metadata_table = dynamodb.Table(asset_links_metadata_storage_table_name)
asset_versions_table = dynamodb.Table(asset_versions_storage_table_name)

#######################
# Data Retrieval Functions - Asset Links
#######################

def get_asset_link_details(asset_link_id: str) -> Optional[Dict[str, Any]]:
    """Get asset link details from DynamoDB"""
    try:
        response = asset_links_table.get_item(
            Key={'assetLinkId': asset_link_id}
        )
        
        if 'Item' not in response:
            logger.warning(f"Asset link not found: {asset_link_id}")
            return None
            
        return response['Item']
    except Exception as e:
        logger.exception(f"Error getting asset link details for {asset_link_id}: {e}")
        return None

def get_asset_link_metadata(asset_link_id: str) -> Dict[str, Any]:
    """
    Get asset link metadata from assetLinksMetadataStorageTable.
    Returns metadata as a dictionary with value and type information.
    """
    try:
        response = asset_links_metadata_table.query(
            KeyConditionExpression=Key('assetLinkId').eq(asset_link_id)
        )
        
        all_metadata = {}
        for item in response.get('Items', []):
            metadata_key = item.get('metadataKey')
            metadata_value = item.get('metadataValue')
            metadata_value_type = item.get('metadataValueType', 'string')
            
            if metadata_key and metadata_value:
                all_metadata[metadata_key] = {
                    'value': metadata_value,
                    'type': metadata_value_type
                }
        
        return all_metadata
    except Exception as e:
        logger.exception(f"Error getting asset link metadata for {asset_link_id}: {e}")
        return {}

def get_all_asset_links_for_asset(database_id: str, asset_id: str) -> List[str]:
    """
    Get all asset link IDs where this asset is either 'from' or 'to'.
    Returns list of asset link IDs to re-index.
    
    Args:
        database_id: The database ID
        asset_id: The asset ID
        
    Returns:
        List of asset link IDs
    """
    try:
        asset_key = f"{database_id}:{asset_id}"
        asset_link_ids = []
        
        # Get links where this asset is the 'from' asset
        from_response = asset_links_table.query(
            IndexName='fromAssetGSI',
            KeyConditionExpression=Key('fromAssetDatabaseId:fromAssetId').eq(asset_key)
        )
        
        for item in from_response.get('Items', []):
            link_id = item.get('assetLinkId')
            if link_id:
                asset_link_ids.append(link_id)
        
        # Get links where this asset is the 'to' asset
        to_response = asset_links_table.query(
            IndexName='toAssetGSI',
            KeyConditionExpression=Key('toAssetDatabaseId:toAssetId').eq(asset_key)
        )
        
        for item in to_response.get('Items', []):
            link_id = item.get('assetLinkId')
            if link_id and link_id not in asset_link_ids:  # Avoid duplicates
                asset_link_ids.append(link_id)
        
        logger.info(f"Found {len(asset_link_ids)} asset links for asset {database_id}/{asset_id}")
        return asset_link_ids
        
    except Exception as e:
        logger.exception(f"Error getting asset links for {database_id}/{asset_id}: {e}")
        return []

#######################
# NGSI-LD Conversion Functions
#######################

def convert_asset_link_to_ngsi_ld(
    asset_link_data: Dict[str, Any],
    link_metadata: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Convert VAMS asset link to NGSI-LD relationship entity for Garnet Framework.
    
    Args:
        asset_link_data: Asset link record from DynamoDB
        link_metadata: Optional asset link metadata
        
    Returns:
        NGSI-LD formatted relationship entity
    """
    try:
        asset_link_id = asset_link_data.get('assetLinkId', '')
        relationship_type = asset_link_data.get('relationshipType', 'related')
        
        # Parse from and to asset information
        from_asset_key = asset_link_data.get('fromAssetDatabaseId:fromAssetId', '')
        to_asset_key = asset_link_data.get('toAssetDatabaseId:toAssetId', '')
        
        from_database_id, from_asset_id = from_asset_key.split(':', 1) if ':' in from_asset_key else ('', '')
        to_database_id, to_asset_id = to_asset_key.split(':', 1) if ':' in to_asset_key else ('', '')
        
        # Create base NGSI-LD entity with enhanced context
        ngsi_ld_entity = {
            "id": f"urn:vams:assetlink:{asset_link_id}",
            "type": "VAMSAssetLink",
            "scope": [f"/AssetLink/{asset_link_id}"],
            # "@context": [
            #     "https://uri.etsi.org/ngsi-ld/v1/ngsi-ld-core-context.jsonld",
            #     {
            #         "vams": "https://vams.aws.com/ontology/",
            #         "VAMSAssetLink": "vams:AssetLink",
            #         "assetLinkId": "vams:assetLinkId",
            #         "relationshipType": "vams:relationshipType",
            #         "assetLinkAliasId": "vams:assetLinkAliasId",
            #         "tags": "vams:tags",
            #         "fromDatabaseId": "vams:fromDatabaseId",
            #         "fromAssetId": "vams:fromAssetId",
            #         "toDatabaseId": "vams:toDatabaseId",
            #         "toAssetId": "vams:toAssetId",
            #         "fromAsset": "vams:fromAsset",
            #         "toAsset": "vams:toAsset",
            #         "dateCreated": "vams:dateCreated"
            #     }
            # ]
        }
        
        # Add asset link ID as property for easy reference
        ngsi_ld_entity["assetLinkId"] = {
            "type": "Property",
            "value": asset_link_id
        }
        
        # Add relationship type
        ngsi_ld_entity["relationshipType"] = {
            "type": "Property",
            "value": relationship_type
        }
        
        # Add alias ID if present (for parent-child relationships)
        if asset_link_data.get('assetLinkAliasId'):
            ngsi_ld_entity["assetLinkAliasId"] = {
                "type": "Property",
                "value": asset_link_data['assetLinkAliasId']
            }
        
        # Add tags if present
        if asset_link_data.get('tags'):
            ngsi_ld_entity["tags"] = {
                "type": "Property",
                "value": asset_link_data['tags']
            }
        
        # Add individual database/asset IDs as properties for easier querying
        ngsi_ld_entity["fromDatabaseId"] = {
            "type": "Property",
            "value": from_database_id
        }
        
        ngsi_ld_entity["fromAssetId"] = {
            "type": "Property",
            "value": from_asset_id
        }
        
        ngsi_ld_entity["toDatabaseId"] = {
            "type": "Property",
            "value": to_database_id
        }
        
        ngsi_ld_entity["toAssetId"] = {
            "type": "Property",
            "value": to_asset_id
        }
        
        # Add relationships to assets
        ngsi_ld_entity["fromAsset"] = {
            "type": "Relationship",
            "object": f"urn:vams:asset:{from_database_id}:{from_asset_id}"
        }
        
        ngsi_ld_entity["toAsset"] = {
            "type": "Relationship",
            "object": f"urn:vams:asset:{to_database_id}:{to_asset_id}"
        }
        
        # Add creation date if available
        if asset_link_data.get('dateCreated'):
            ngsi_ld_entity["dateCreated"] = {
                "type": "Property",
                "value": {
                    "@type": "DateTime",
                    "@value": asset_link_data['dateCreated']
                }
            }
        
        # Add custom metadata as properties using metadataValueType
        if link_metadata:
            for key, metadata_info in link_metadata.items():
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
        
        return ngsi_ld_entity
        
    except Exception as e:
        logger.exception(f"Error converting asset link to NGSI-LD: {e}")
        raise VAMSGeneralErrorResponse("Error converting asset link data to NGSI-LD format")


def convert_asset_to_ngsi_ld(
    asset_data: Dict[str, Any], 
    bucket_details: Optional[Dict[str, Any]] = None,
    asset_metadata: Optional[Dict[str, Any]] = None,
    version_info: Optional[Dict[str, Any]] = None,
    relationship_flags: Optional[Dict[str, bool]] = None
) -> Dict[str, Any]:
    """
    Convert VAMS asset data to NGSI-LD format for Garnet Framework.
    
    NGSI-LD Reference: https://garnet-framework.dev/docs/getting-started/ngsi-ld
    
    Args:
        asset_data: Asset record from DynamoDB
        bucket_details: Optional S3 bucket details
        asset_metadata: Optional asset metadata
        version_info: Optional asset version information
        relationship_flags: Optional relationship flags
        
    Returns:
        NGSI-LD formatted entity
    """
    try:
        database_id = asset_data.get('databaseId', '')
        asset_id = asset_data.get('assetId', '')
        
        # Create base NGSI-LD entity
        ngsi_ld_entity = {
            "id": f"urn:vams:asset:{database_id}:{asset_id}",
            "type": "VAMSAsset",
            "scope": [f"/Database/{database_id}/Asset/{asset_id}"],
            # "@context": [
            #     "https://uri.etsi.org/ngsi-ld/v1/ngsi-ld-core-context.jsonld",
            #     {
            #         "vams": "https://vams.aws.com/ontology/",
            #         "VAMSAsset": "vams:Asset",
            #         "assetName": "vams:assetName",
            #         "assetType": "vams:assetType",
            #         "description": "vams:description",
            #         "databaseId": "vams:databaseId",
            #         "bucketId": "vams:bucketId",
            #         "bucketName": "vams:bucketName",
            #         "baseAssetsPrefix": "vams:baseAssetsPrefix",
            #         "isDistributable": "vams:isDistributable",
            #         "tags": "vams:tags",
            #         "createdBy": "vams:createdBy",
            #         "dateCreated": "vams:dateCreated",
            #         "dateModified": "vams:dateModified",
            #         "isArchived": "vams:isArchived",
            #         "hasChildren": "vams:hasChildren",
            #         "hasParents": "vams:hasParents",
            #         "hasRelated": "vams:hasRelated",
            #         "belongsToDatabase": "vams:belongsToDatabase",
            #         "usesBucket": "vams:usesBucket"
            #     }
            # ]
        }
        
        # Add asset properties as NGSI-LD properties
        if asset_data.get('assetName'):
            ngsi_ld_entity["assetName"] = {
                "type": "Property",
                "value": asset_data['assetName']
            }
        
        if asset_data.get('assetType'):
            ngsi_ld_entity["assetType"] = {
                "type": "Property",
                "value": asset_data['assetType']
            }
        
        if asset_data.get('description'):
            ngsi_ld_entity["description"] = {
                "type": "Property", 
                "value": asset_data['description']
            }
        
        ngsi_ld_entity["databaseId"] = {
            "type": "Property",
            "value": database_id
        }
        
        if asset_data.get('bucketId'):
            ngsi_ld_entity["bucketId"] = {
                "type": "Property",
                "value": asset_data['bucketId']
            }
        
        # Add bucket details if available
        if bucket_details:
            if bucket_details.get('bucketName'):
                ngsi_ld_entity["bucketName"] = {
                    "type": "Property",
                    "value": bucket_details['bucketName']
                }
            
            if bucket_details.get('baseAssetsPrefix'):
                ngsi_ld_entity["baseAssetsPrefix"] = {
                    "type": "Property",
                    "value": bucket_details['baseAssetsPrefix']
                }
        
        # Add boolean properties
        if 'isDistributable' in asset_data:
            ngsi_ld_entity["isDistributable"] = {
                "type": "Property",
                "value": asset_data['isDistributable']
            }
        
        # Add tags as a list property
        if asset_data.get('tags'):
            ngsi_ld_entity["tags"] = {
                "type": "Property",
                "value": asset_data['tags']
            }
        
        # Add metadata properties
        if asset_data.get('createdBy'):
            ngsi_ld_entity["createdBy"] = {
                "type": "Property",
                "value": asset_data['createdBy']
            }
        
        if asset_data.get('dateCreated'):
            ngsi_ld_entity["dateCreated"] = {
                "type": "Property",
                "value": {
                    "@type": "DateTime",
                    "@value": asset_data['dateCreated']
                }
            }
        
        if asset_data.get('dateModified'):
            ngsi_ld_entity["dateModified"] = {
                "type": "Property", 
                "value": {
                    "@type": "DateTime",
                    "@value": asset_data['dateModified']
                }
            }
        
        # Check if asset is archived (contains #deleted)
        is_archived = '#deleted' in database_id or '#deleted' in asset_id
        ngsi_ld_entity["isArchived"] = {
            "type": "Property",
            "value": is_archived
        }
        
        # Add relationship flags if available
        if relationship_flags:
            ngsi_ld_entity["hasChildren"] = {
                "type": "Property",
                "value": relationship_flags.get('has_children', False)
            }
            
            ngsi_ld_entity["hasParents"] = {
                "type": "Property",
                "value": relationship_flags.get('has_parents', False)
            }
            
            ngsi_ld_entity["hasRelated"] = {
                "type": "Property",
                "value": relationship_flags.get('has_related', False)
            }
        
        # Add version information if available
        if version_info:
            if version_info.get('versionId'):
                ngsi_ld_entity["currentVersionId"] = {
                    "type": "Property",
                    "value": version_info['versionId']
                }
            
            if version_info.get('createdAt'):
                ngsi_ld_entity["versionCreatedAt"] = {
                    "type": "Property",
                    "value": {
                        "@type": "DateTime",
                        "@value": version_info['createdAt']
                    }
                }
            
            if version_info.get('comment'):
                ngsi_ld_entity["versionComment"] = {
                    "type": "Property",
                    "value": version_info['comment']
                }
        
        # Add custom metadata as properties using metadataValueType
        if asset_metadata:
            for key, metadata_info in asset_metadata.items():
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
        
        # Add relationships
        # Relationship to database
        normalized_database_id = database_id.replace('#deleted', '')
        ngsi_ld_entity["belongsToDatabase"] = {
            "type": "Relationship",
            "object": f"urn:vams:database:{normalized_database_id}"
        }
        
        # Relationship to bucket if available
        if bucket_details and bucket_details.get('bucketId'):
            ngsi_ld_entity["usesBucket"] = {
                "type": "Relationship",
                "object": f"urn:vams:bucket:{bucket_details['bucketId']}"
            }
        
        return ngsi_ld_entity
        
    except Exception as e:
        logger.exception(f"Error converting asset to NGSI-LD: {e}")
        raise VAMSGeneralErrorResponse("Error converting asset data to NGSI-LD format")

#######################
# Data Retrieval Functions (reusing patterns from assetIndexer.py)
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
    Get asset-level metadata from NEW schema table.
    Returns metadata as a dictionary with value and type information.
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
            metadata_value_type = item.get('metadataValueType', 'string')
            
            # Skip system metadata records that conflict with OpenSearch field mappings
            if metadata_key == 'REINDEX_METADATA_RECORD':
                logger.debug(f"Skipping system metadata: {metadata_key}")
                continue  # Skip this metadata, but continue processing others
            
            if metadata_key and metadata_value:
                all_metadata[metadata_key] = {
                    'value': metadata_value,
                    'type': metadata_value_type
                }
        
        return all_metadata
    except Exception as e:
        logger.exception(f"Error getting asset metadata for {database_id}/{asset_id}: {e}")
        return {}

def get_asset_version_info(asset_id: str) -> Dict[str, Any]:
    """Get current asset version information"""
    try:
        # Get current version info
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
                    'StringValue': ngsi_ld_entity.get('type', 'VAMSAsset'),
                    'DataType': 'String'
                },
                'source': {
                    'StringValue': 'vams-asset-indexer',
                    'DataType': 'String'
                }
            }
        )
        
        logger.info(f"Successfully sent entity to Garnet ingestion queue: {ngsi_ld_entity['id']}, MessageId: {response.get('MessageId')}")
        return True
        
    except Exception as e:
        logger.exception(f"Error sending entity to Garnet ingestion queue: {e}")
        return False

#######################
# Business Logic Functions
#######################

def handle_asset_stream(event_record: Dict[str, Any]) -> bool:
    """
    Handle DynamoDB asset table stream for Garnet indexing.
    Uses same pattern as OpenSearch assetIndexer.py handle_asset_stream().
    
    Args:
        event_record: DynamoDB stream record
        
    Returns:
        True if successful, False otherwise
    """
    try:
        event_name = event_record.get('eventName', '')
        dynamodb_data = event_record.get('dynamodb', {})
        
        # For REMOVE events, extract IDs from Keys
        if event_name == 'REMOVE':
            keys = dynamodb_data.get('Keys', {})
            database_id = keys.get('databaseId', {}).get('S')
            asset_id = keys.get('assetId', {}).get('S')
            
            if not database_id or not asset_id:
                logger.warning("Missing database ID or asset ID in REMOVE event keys")
                return True  # Skip, not an error
            
            logger.info(f"Processing REMOVE event for asset: {database_id}/{asset_id}")
            
            # For delete operations, create a minimal NGSI-LD entity for deletion
            ngsi_ld_entity = {
                "id": f"urn:vams:asset:{database_id}:{asset_id}",
                "type": "VAMSAsset"
            }
            
            success = send_to_garnet_ingestion_queue(ngsi_ld_entity)
            if success:
                logger.info(f"Successfully sent asset deletion to Garnet: {database_id}/{asset_id}")
            else:
                logger.error(f"Failed to send asset deletion to Garnet: {database_id}/{asset_id}")
            return success
        
        # For INSERT/MODIFY events, use NewImage
        record_data = dynamodb_data.get('NewImage', {})
        
        if not record_data:
            logger.warning("No record data found in asset stream event")
            return True  # Skip, not an error
        
        # Extract database ID and asset ID from DynamoDB record
        database_id = record_data.get('databaseId', {}).get('S')
        asset_id = record_data.get('assetId', {}).get('S')
        
        if not database_id or not asset_id:
            logger.warning("Missing database ID or asset ID in asset stream")
            return True  # Skip, not an error
        
        # For INSERT/MODIFY, always index
        logger.info(f"Processing {event_name} event for asset: {database_id}/{asset_id}")
        
        # Get full asset details
        asset_details = get_asset_details(database_id, asset_id)
        if not asset_details:
            logger.warning(f"Asset not found for indexing: {database_id}/{asset_id}")
            return True  # Not an error, asset might have been deleted
        
        # Get bucket details if asset has a bucket
        bucket_details = None
        bucket_id = asset_details.get('bucketId')
        if bucket_id:
            bucket_details = get_bucket_details(bucket_id)
        
        # Get asset metadata
        asset_metadata = get_asset_metadata(database_id, asset_id)
        
        # Get version information
        version_info = get_asset_version_info(asset_id)
        
        # Get relationship flags
        relationship_flags = get_asset_relationship_flags(database_id, asset_id)
        
        # Convert to NGSI-LD format
        ngsi_ld_entity = convert_asset_to_ngsi_ld(
            asset_details, 
            bucket_details, 
            asset_metadata, 
            version_info, 
            relationship_flags
        )
        
        # Send asset to Garnet ingestion queue
        success = send_to_garnet_ingestion_queue(ngsi_ld_entity)
        if success:
            logger.info(f"Successfully sent asset to Garnet: {database_id}/{asset_id}")
        else:
            logger.error(f"Failed to send asset to Garnet: {database_id}/{asset_id}")
        
        # Also re-index all asset links related to this asset
        # This ensures asset link entities stay in sync when asset properties change
        asset_link_ids = get_all_asset_links_for_asset(database_id, asset_id)
        
        if asset_link_ids:
            logger.info(f"Re-indexing {len(asset_link_ids)} asset links for asset {database_id}/{asset_id}")
            
            for link_id in asset_link_ids:
                try:
                    link_details = get_asset_link_details(link_id)
                    if link_details:
                        link_metadata = get_asset_link_metadata(link_id)
                        link_entity = convert_asset_link_to_ngsi_ld(link_details, link_metadata)
                        link_success = send_to_garnet_ingestion_queue(link_entity)
                        
                        if link_success:
                            logger.info(f"Successfully re-indexed asset link {link_id} for asset change")
                        else:
                            logger.error(f"Failed to re-index asset link {link_id} for asset change")
                except Exception as link_error:
                    logger.exception(f"Error re-indexing asset link {link_id}: {link_error}")
        
        return success
        
    except Exception as e:
        logger.exception(f"Error handling asset stream: {e}")
        return False

def handle_asset_metadata_stream(event_record: Dict[str, Any]) -> bool:
    """
    Handle DynamoDB asset metadata table stream for Garnet indexing.
    Uses same pattern as OpenSearch assetIndexer.py handle_metadata_stream().
    
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
            logger.warning("Missing composite key in asset metadata stream")
            return True  # Skip, not an error
        
        # Parse composite key
        parts = composite_key.split(':', 2)
        if len(parts) != 3:
            logger.warning(f"Invalid composite key format: {composite_key}")
            return True  # Skip, not an error
        
        database_id, asset_id, file_path = parts
        
        # Only process if it's asset-level (file_path is "/")
        if file_path != '/':
            logger.info("File-level metadata, skipping for asset indexer")
            return True  # Skip, not an error
        
        logger.info(f"Processing {event_name} event for asset metadata: {database_id}/{asset_id}")
        
        # For any metadata change, re-index the entire asset
        asset_details = get_asset_details(database_id, asset_id)
        if not asset_details:
            logger.warning(f"Asset not found for metadata indexing: {database_id}/{asset_id}")
            return True  # Not an error, asset might have been deleted
        
        # Get bucket details if asset has a bucket
        bucket_details = None
        bucket_id = asset_details.get('bucketId')
        if bucket_id:
            bucket_details = get_bucket_details(bucket_id)
        
        # Get asset metadata
        asset_metadata = get_asset_metadata(database_id, asset_id)
        
        # Get version information
        version_info = get_asset_version_info(asset_id)
        
        # Get relationship flags
        relationship_flags = get_asset_relationship_flags(database_id, asset_id)
        
        # Convert to NGSI-LD format
        ngsi_ld_entity = convert_asset_to_ngsi_ld(
            asset_details, 
            bucket_details, 
            asset_metadata, 
            version_info, 
            relationship_flags
        )
        
        # Send to Garnet ingestion queue
        success = send_to_garnet_ingestion_queue(ngsi_ld_entity)
        if success:
            logger.info(f"Successfully sent asset to Garnet after metadata change: {database_id}/{asset_id}")
        else:
            logger.error(f"Failed to send asset to Garnet after metadata change: {database_id}/{asset_id}")
        return success
        
    except Exception as e:
        logger.exception(f"Error handling asset metadata stream: {e}")
        return False

def handle_asset_links_stream(event_record: Dict[str, Any]) -> List[bool]:
    """
    Handle DynamoDB asset links table stream for Garnet indexing.
    Uses same pattern as OpenSearch assetIndexer.py handle_asset_links_stream().
    
    Creates asset link entity AND updates both connected assets.
    
    Args:
        event_record: DynamoDB stream record
        
    Returns:
        List of success booleans (one for link, two for assets)
    """
    try:
        event_name = event_record.get('eventName', '')
        dynamodb_data = event_record.get('dynamodb', {})
        
        # Extract asset link information
        asset_link_id = None
        from_asset_key = None
        to_asset_key = None
        
        if event_name == 'REMOVE':
            # For REMOVE events, use Keys
            keys = dynamodb_data.get('Keys', {})
            asset_link_id = keys.get('assetLinkId', {}).get('S')
            from_asset_key = keys.get('fromAssetDatabaseId:fromAssetId', {}).get('S')
            to_asset_key = keys.get('toAssetDatabaseId:toAssetId', {}).get('S')
        else:
            # For INSERT/MODIFY events, use NewImage
            record_data = dynamodb_data.get('NewImage', {})
            asset_link_id = record_data.get('assetLinkId', {}).get('S')
            from_asset_key = record_data.get('fromAssetDatabaseId:fromAssetId', {}).get('S')
            to_asset_key = record_data.get('toAssetDatabaseId:toAssetId', {}).get('S')
        
        if not asset_link_id or not from_asset_key or not to_asset_key:
            logger.warning("Missing asset link information in stream")
            return [True]  # Skip, not an error
        
        # Parse asset keys
        from_database_id, from_asset_id = from_asset_key.split(':', 1)
        to_database_id, to_asset_id = to_asset_key.split(':', 1)
        
        logger.info(f"Processing {event_name} event for asset link: {asset_link_id}")
        
        results = []
        
        # Handle the asset link entity itself
        if event_name == 'REMOVE':
            # Delete the asset link entity
            ngsi_ld_entity = {
                "id": f"urn:vams:assetlink:{asset_link_id}",
                "type": "VAMSAssetLink"
            }
            success = send_to_garnet_ingestion_queue(ngsi_ld_entity)
            results.append(success)
        else:
            # Create/update the asset link entity
            asset_link_details = get_asset_link_details(asset_link_id)
            if asset_link_details:
                link_metadata = get_asset_link_metadata(asset_link_id)
                ngsi_ld_entity = convert_asset_link_to_ngsi_ld(asset_link_details, link_metadata)
                success = send_to_garnet_ingestion_queue(ngsi_ld_entity)
                results.append(success)
        
        # Update both connected assets (their relationship flags changed)
        for database_id, asset_id in [(from_database_id, from_asset_id), (to_database_id, to_asset_id)]:
            asset_details = get_asset_details(database_id, asset_id)
            if asset_details:
                bucket_details = None
                bucket_id = asset_details.get('bucketId')
                if bucket_id:
                    bucket_details = get_bucket_details(bucket_id)
                
                asset_metadata = get_asset_metadata(database_id, asset_id)
                version_info = get_asset_version_info(asset_id)
                relationship_flags = get_asset_relationship_flags(database_id, asset_id)
                
                ngsi_ld_entity = convert_asset_to_ngsi_ld(
                    asset_details, bucket_details, asset_metadata, version_info, relationship_flags
                )
                success = send_to_garnet_ingestion_queue(ngsi_ld_entity)
                results.append(success)
        
        return results
        
    except Exception as e:
        logger.exception(f"Error handling asset links stream: {e}")
        return [False]

def handle_asset_link_metadata_stream(event_record: Dict[str, Any]) -> bool:
    """
    Handle DynamoDB asset link metadata table stream for Garnet indexing.
    
    Args:
        event_record: DynamoDB stream record
        
    Returns:
        True if successful, False otherwise
    """
    try:
        event_name = event_record.get('eventName', '')
        dynamodb_data = event_record.get('dynamodb', {})
        
        # Extract asset link ID
        asset_link_id = None
        
        if event_name == 'REMOVE':
            # For REMOVE events, use Keys
            keys = dynamodb_data.get('Keys', {})
            asset_link_id = keys.get('assetLinkId', {}).get('S')
        else:
            # For INSERT/MODIFY events, use NewImage
            record_data = dynamodb_data.get('NewImage', {})
            asset_link_id = record_data.get('assetLinkId', {}).get('S')
        
        if not asset_link_id:
            logger.warning("Missing asset link ID in asset link metadata stream")
            return True  # Skip, not an error
        
        logger.info(f"Processing {event_name} event for asset link metadata: {asset_link_id}")
        
        # For any metadata change, re-index the entire asset link
        asset_link_details = get_asset_link_details(asset_link_id)
        if not asset_link_details:
            logger.warning(f"Asset link not found for metadata indexing: {asset_link_id}")
            return True  # Not an error, link might have been deleted
        
        # Get asset link metadata
        link_metadata = get_asset_link_metadata(asset_link_id)
        
        # Convert to NGSI-LD format
        ngsi_ld_entity = convert_asset_link_to_ngsi_ld(asset_link_details, link_metadata)
        
        # Send to Garnet ingestion queue
        success = send_to_garnet_ingestion_queue(ngsi_ld_entity)
        if success:
            logger.info(f"Successfully sent asset link to Garnet after metadata change: {asset_link_id}")
        else:
            logger.error(f"Failed to send asset link to Garnet after metadata change: {asset_link_id}")
        return success
        
    except Exception as e:
        logger.exception(f"Error handling asset link metadata stream: {e}")
        return False

#######################
# Lambda Handler
#######################

def lambda_handler(event, context: LambdaContext) -> Dict[str, Any]:
    """
    Lambda handler for Garnet Framework asset indexing.
    Uses same pattern as OpenSearch assetIndexer.py lambda_handler().
    
    Processes SQS events containing SNS messages with DynamoDB stream records.
    """
    try:
        logger.info(f"Processing asset indexing event: {json.dumps(event, default=str)}")
        
        successful_records = 0
        failed_records = 0
        
        # Handle different event sources (same pattern as OpenSearch indexers)
        if 'Records' in event:
            for record in event['Records']:
                event_source = record.get('eventSource', '')
                
                if event_source == 'aws:sqs':
                    # SQS message (contains SNS message with DynamoDB stream record)
                    try:
                        # Parse SQS message body
                        body = record.get('body', '')
                        if isinstance(body, str):
                            body = json.loads(body)
                        
                        # Check if this is an SNS message
                        if body.get('Type') == 'Notification' and body.get('Message'):
                            # Parse SNS message (contains DynamoDB stream record)
                            sns_message = body.get('Message')
                            if isinstance(sns_message, str):
                                sns_message = json.loads(sns_message)
                            
                            # Check source ARN to determine table type
                            source_arn = sns_message.get('eventSourceARN', '')
                            
                            if asset_storage_table_name in source_arn:
                                # Asset table stream
                                success = handle_asset_stream(sns_message)
                                if success:
                                    successful_records += 1
                                else:
                                    failed_records += 1
                                    
                            elif asset_file_metadata_storage_table_name in source_arn:
                                # Asset metadata table stream
                                success = handle_asset_metadata_stream(sns_message)
                                if success:
                                    successful_records += 1
                                else:
                                    failed_records += 1
                                    
                            elif asset_links_storage_table_v2_name in source_arn:
                                # Asset links table stream
                                link_results = handle_asset_links_stream(sns_message)
                                successful_records += sum(1 for r in link_results if r)
                                failed_records += sum(1 for r in link_results if not r)
                                
                            elif asset_links_metadata_storage_table_name in source_arn:
                                # Asset link metadata table stream
                                success = handle_asset_link_metadata_stream(sns_message)
                                if success:
                                    successful_records += 1
                                else:
                                    failed_records += 1
                                    
                            else:
                                logger.warning(f"Unknown table in source ARN: {source_arn}")
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
        
        logger.info(f"Garnet asset indexing completed: {successful_records} successful, {failed_records} failed")
        
        return {
            'statusCode': 200,
            'body': {
                'message': 'Garnet asset indexing completed',
                'successful_records': successful_records,
                'failed_records': failed_records
            }
        }
        
    except Exception as e:
        logger.exception(f"Error in Garnet asset indexer lambda handler: {e}")
        return {
            'statusCode': 500,
            'body': {
                'message': 'Error processing Garnet asset indexing',
                'error': str(e)
            }
        }