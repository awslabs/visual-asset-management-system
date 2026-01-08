"""
Garnet Framework Database Indexer for VAMS.

This Lambda function processes database change events and converts database data
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
logger = safeLogger(service_name="GarnetDatabaseIndexer")

# Load environment variables with error handling
try:
    database_storage_table_name = os.environ["DATABASE_STORAGE_TABLE_NAME"]
    database_metadata_storage_table_name = os.environ["DATABASE_METADATA_STORAGE_TABLE_NAME"]
    s3_asset_buckets_storage_table_name = os.environ["S3_ASSET_BUCKETS_STORAGE_TABLE_NAME"]
    garnet_ingestion_queue_url = os.environ["GARNET_INGESTION_QUEUE_URL"]
    garnet_api_endpoint = os.environ["GARNET_API_ENDPOINT"]
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

# Initialize DynamoDB tables
database_storage_table = dynamodb.Table(database_storage_table_name)
database_metadata_table = dynamodb.Table(database_metadata_storage_table_name)
s3_asset_buckets_table = dynamodb.Table(s3_asset_buckets_storage_table_name)

#######################
# Data Retrieval Functions
#######################

def get_database_metadata(database_id: str) -> Dict[str, Any]:
    """
    Get database-level metadata from databaseMetadataStorageTable.
    Returns metadata as a dictionary with value and type information.
    """
    try:
        # Query using DatabaseIdIndex GSI
        response = database_metadata_table.query(
            IndexName='DatabaseIdIndex',
            KeyConditionExpression=Key('databaseId').eq(database_id)
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
        logger.exception(f"Error getting database metadata for {database_id}: {e}")
        return {}

#######################
# NGSI-LD Conversion Functions
#######################

def convert_database_to_ngsi_ld(database_data: Dict[str, Any], bucket_details: Optional[Dict[str, Any]] = None, database_metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Convert VAMS database data to NGSI-LD format for Garnet Framework.
    
    NGSI-LD Reference: https://garnet-framework.dev/docs/getting-started/ngsi-ld
    
    Args:
        database_data: Database record from DynamoDB
        bucket_details: Optional S3 bucket details
        database_metadata: Optional database metadata
        
    Returns:
        NGSI-LD formatted entity
    """
    try:
        database_id = database_data.get('databaseId', '')
        
        # Create base NGSI-LD entity
        ngsi_ld_entity = {
            "id": f"urn:vams:database:{database_id}",
            "type": "VAMSDatabase",
            "scope": [f"/Database/{database_id}"],
            # "@context": [
            #     "https://uri.etsi.org/ngsi-ld/v1/ngsi-ld-core-context.jsonld",
            #     {
            #         "vams": "https://vams.aws.com/ontology/",
            #         "VAMSDatabase": "vams:Database",
            #         "databaseId": "vams:databaseId",
            #         "description": "vams:description",
            #         "defaultBucketId": "vams:defaultBucketId",
            #         "bucketName": "vams:bucketName",
            #         "baseAssetsPrefix": "vams:baseAssetsPrefix",
            #         "restrictMetadataOutsideSchemas": "vams:restrictMetadataOutsideSchemas",
            #         "restrictFileUploadsToExtensions": "vams:restrictFileUploadsToExtensions",
            #         "assetCount": "vams:assetCount",
            #         "createdBy": "vams:createdBy",
            #         "dateCreated": "vams:dateCreated",
            #         "dateModified": "vams:dateModified",
            #         "isArchived": "vams:isArchived"
            #     }
            # ]
        }
        
        # Add databaseId as a property for easy reference
        ngsi_ld_entity["databaseId"] = {
            "type": "Property",
            "value": database_id
        }
        
        # Add database properties as NGSI-LD properties
        if database_data.get('description'):
            ngsi_ld_entity["description"] = {
                "type": "Property", 
                "value": database_data['description']
            }
        
        # Use defaultBucketId (the actual field name in DynamoDB)
        if database_data.get('defaultBucketId'):
            ngsi_ld_entity["defaultBucketId"] = {
                "type": "Property",
                "value": database_data['defaultBucketId']
            }
        
        # Add restrictMetadataOutsideSchemas
        if 'restrictMetadataOutsideSchemas' in database_data:
            ngsi_ld_entity["restrictMetadataOutsideSchemas"] = {
                "type": "Property",
                "value": database_data['restrictMetadataOutsideSchemas']
            }
        
        # Add restrictFileUploadsToExtensions
        if database_data.get('restrictFileUploadsToExtensions'):
            ngsi_ld_entity["restrictFileUploadsToExtensions"] = {
                "type": "Property",
                "value": database_data['restrictFileUploadsToExtensions']
            }
        
        # Add assetCount
        if 'assetCount' in database_data:
            ngsi_ld_entity["assetCount"] = {
                "type": "Property",
                "value": database_data['assetCount']
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
        
        # Add metadata properties
        if database_data.get('createdBy'):
            ngsi_ld_entity["createdBy"] = {
                "type": "Property",
                "value": database_data['createdBy']
            }
        
        if database_data.get('dateCreated'):
            ngsi_ld_entity["dateCreated"] = {
                "type": "Property",
                "value": {
                    "@type": "DateTime",
                    "@value": database_data['dateCreated']
                }
            }
        
        if database_data.get('dateModified'):
            ngsi_ld_entity["dateModified"] = {
                "type": "Property", 
                "value": {
                    "@type": "DateTime",
                    "@value": database_data['dateModified']
                }
            }
        
        # Check if database is archived (contains #deleted)
        is_archived = '#deleted' in database_id
        ngsi_ld_entity["isArchived"] = {
            "type": "Property",
            "value": is_archived
        }
        
        # Add location information if available
        if database_data.get('location'):
            ngsi_ld_entity["location"] = {
                "type": "GeoProperty",
                "value": database_data['location']
            }
        
        # Add custom metadata as properties using metadataValueType
        if database_metadata:
            for key, metadata_info in database_metadata.items():
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
        
        # Add relationships to bucket if bucket details available
        if bucket_details and bucket_details.get('bucketId'):
            ngsi_ld_entity["usesBucket"] = {
                "type": "Relationship",
                "object": f"urn:vams:bucket:{bucket_details['bucketId']}"
            }
        
        return ngsi_ld_entity
        
    except Exception as e:
        logger.exception(f"Error converting database to NGSI-LD: {e}")
        raise VAMSGeneralErrorResponse("Error converting database data to NGSI-LD format")

#######################
# Data Retrieval Functions
#######################

def get_database_details(database_id: str) -> Optional[Dict[str, Any]]:
    """Get database details from DynamoDB"""
    try:
        response = database_storage_table.get_item(
            Key={'databaseId': database_id}
        )
        
        if 'Item' not in response:
            logger.warning(f"Database not found: {database_id}")
            return None
            
        return response['Item']
    except Exception as e:
        logger.exception(f"Error getting database details for {database_id}: {e}")
        return None

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
        logger.info(f"Retrieved bucket record: {json.dumps(bucket, default=decimal_to_number)}")
        
        bucket_name = bucket.get('bucketName')
        base_assets_prefix = bucket.get('baseAssetsPrefix', '/')
        
        logger.info(f"Bucket details - bucketName: {bucket_name}, baseAssetsPrefix (raw): {base_assets_prefix}")
        
        if not bucket_name:
            logger.error(f"Bucket name missing for bucketId: {bucket_id}")
            return None
        
        # Ensure prefix ends with slash
        if not base_assets_prefix.endswith('/'):
            base_assets_prefix += '/'
        
        # Remove leading slash
        if base_assets_prefix.startswith('/'):
            base_assets_prefix = base_assets_prefix[1:]
        
        logger.info(f"Bucket details (processed) - baseAssetsPrefix: {base_assets_prefix}")
        
        result = {
            'bucketId': bucket_id,
            'bucketName': bucket_name,
            'baseAssetsPrefix': base_assets_prefix
        }
        
        logger.info(f"Returning bucket details: {json.dumps(result, default=decimal_to_number)}")
        return result
    except Exception as e:
        logger.exception(f"Error getting bucket details for {bucket_id}: {e}")
        return None

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
                    'StringValue': 'VAMSDatabase',
                    'DataType': 'String'
                },
                'source': {
                    'StringValue': 'vams-database-indexer',
                    'DataType': 'String'
                }
            }
        )
        
        logger.info(f"Successfully sent database entity to Garnet ingestion queue: {ngsi_ld_entity['id']}, MessageId: {response.get('MessageId')}")
        return True
        
    except Exception as e:
        logger.exception(f"Error sending entity to Garnet ingestion queue: {e}")
        return False

#######################
# Business Logic Functions
#######################

def handle_database_stream(event_record: Dict[str, Any]) -> bool:
    """
    Handle DynamoDB database table stream for Garnet indexing.
    Uses same pattern as OpenSearch assetIndexer.py handle_asset_stream().
    
    Args:
        event_record: DynamoDB stream record
        
    Returns:
        True if successful, False otherwise
    """
    try:
        event_name = event_record.get('eventName', '')
        dynamodb_data = event_record.get('dynamodb', {})
        
        # For REMOVE events, extract ID from Keys
        if event_name == 'REMOVE':
            keys = dynamodb_data.get('Keys', {})
            database_id = keys.get('databaseId', {}).get('S')
            
            if not database_id:
                logger.warning("Missing database ID in REMOVE event keys")
                return True  # Skip, not an error
            
            logger.info(f"Processing REMOVE event for database: {database_id}")
            
            # For delete operations, create a minimal NGSI-LD entity for deletion
            ngsi_ld_entity = {
                "id": f"urn:vams:database:{database_id}",
                "type": "VAMSDatabase"
            }
            
            success = send_to_garnet_ingestion_queue(ngsi_ld_entity)
            if success:
                logger.info(f"Successfully sent database deletion to Garnet: {database_id}")
            else:
                logger.error(f"Failed to send database deletion to Garnet: {database_id}")
            return success
        
        # For INSERT/MODIFY events, use NewImage
        record_data = dynamodb_data.get('NewImage', {})
        
        if not record_data:
            logger.warning("No record data found in database stream event")
            return True  # Skip, not an error
        
        # Extract database ID from DynamoDB record
        database_id = record_data.get('databaseId', {}).get('S')
        
        if not database_id:
            logger.warning("Missing database ID in database stream")
            return True  # Skip, not an error
        
        # For INSERT/MODIFY, always index
        logger.info(f"Processing {event_name} event for database: {database_id}")
        
        # Get full database details
        database_details = get_database_details(database_id)
        if not database_details:
            logger.warning(f"Database not found for indexing: {database_id}")
            return True  # Not an error, database might have been deleted
        
        # Get bucket details if database has a bucket (use defaultBucketId)
        bucket_details = None
        bucket_id = database_details.get('defaultBucketId')
        if bucket_id:
            bucket_details = get_bucket_details(bucket_id)
        
        # Get database metadata
        database_metadata = get_database_metadata(database_id)
        
        # Convert to NGSI-LD format
        ngsi_ld_entity = convert_database_to_ngsi_ld(database_details, bucket_details, database_metadata)
        
        # Send to Garnet ingestion queue
        success = send_to_garnet_ingestion_queue(ngsi_ld_entity)
        if success:
            logger.info(f"Successfully sent database to Garnet: {database_id}")
        else:
            logger.error(f"Failed to send database to Garnet: {database_id}")
        return success
        
    except Exception as e:
        logger.exception(f"Error handling database stream: {e}")
        return False

def handle_database_metadata_stream(event_record: Dict[str, Any]) -> bool:
    """
    Handle DynamoDB database metadata table stream for Garnet indexing.
    
    Args:
        event_record: DynamoDB stream record
        
    Returns:
        True if successful, False otherwise
    """
    try:
        event_name = event_record.get('eventName', '')
        dynamodb_data = event_record.get('dynamodb', {})
        
        # Extract database ID from the record
        database_id = None
        
        if event_name == 'REMOVE':
            # For REMOVE events, use Keys
            keys = dynamodb_data.get('Keys', {})
            database_id = keys.get('databaseId', {}).get('S')
        else:
            # For INSERT/MODIFY events, use NewImage
            record_data = dynamodb_data.get('NewImage', {})
            database_id = record_data.get('databaseId', {}).get('S')
        
        if not database_id:
            logger.warning("Missing database ID in database metadata stream")
            return True  # Skip, not an error
        
        logger.info(f"Processing {event_name} event for database metadata: {database_id}")
        
        # For any metadata change, re-index the entire database
        database_details = get_database_details(database_id)
        if not database_details:
            logger.warning(f"Database not found for metadata indexing: {database_id}")
            return True  # Not an error, database might have been deleted
        
        # Get bucket details if database has a bucket (use defaultBucketId)
        bucket_details = None
        bucket_id = database_details.get('defaultBucketId')
        if bucket_id:
            bucket_details = get_bucket_details(bucket_id)
        
        # Get database metadata
        database_metadata = get_database_metadata(database_id)
        
        # Convert to NGSI-LD format
        ngsi_ld_entity = convert_database_to_ngsi_ld(database_details, bucket_details, database_metadata)
        
        # Send to Garnet ingestion queue
        success = send_to_garnet_ingestion_queue(ngsi_ld_entity)
        if success:
            logger.info(f"Successfully sent database to Garnet after metadata change: {database_id}")
        else:
            logger.error(f"Failed to send database to Garnet after metadata change: {database_id}")
        return success
        
    except Exception as e:
        logger.exception(f"Error handling database metadata stream: {e}")
        return False

#######################
# Lambda Handler
#######################

def lambda_handler(event, context: LambdaContext) -> Dict[str, Any]:
    """
    Lambda handler for Garnet Framework database indexing.
    Uses same pattern as OpenSearch assetIndexer.py lambda_handler().
    
    Processes SQS events containing SNS messages with DynamoDB stream records.
    """
    try:
        logger.info(f"Processing database indexing event: {json.dumps(event, default=str)}")
        
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
                            
                            # Check if SNS message is a DynamoDB stream record
                            source_arn = sns_message.get('eventSourceARN', '')
                            
                            if database_storage_table_name in source_arn:
                                # Database table stream
                                success = handle_database_stream(sns_message)
                                if success:
                                    successful_records += 1
                                else:
                                    failed_records += 1
                            elif database_metadata_storage_table_name in source_arn:
                                # Database metadata table stream
                                success = handle_database_metadata_stream(sns_message)
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
        
        logger.info(f"Garnet database indexing completed: {successful_records} successful, {failed_records} failed")
        
        return {
            'statusCode': 200,
            'body': {
                'message': 'Garnet database indexing completed',
                'successful_records': successful_records,
                'failed_records': failed_records
            }
        }
        
    except Exception as e:
        logger.exception(f"Error in Garnet database indexer lambda handler: {e}")
        return {
            'statusCode': 500,
            'body': {
                'message': 'Error processing Garnet database indexing',
                'error': str(e)
            }
        }