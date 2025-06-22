"""
This file is responsible for handling bucket syncing events.
When syncing is enabled and files are created or deleted in a specified Amazon S3 bucket,
the business logic below manages mapping the Amazon S3 information to VAMS assets.
"""
import json
import os
import re
import boto3
import time
from botocore.config import Config
from datetime import datetime
from handlers.metadata import to_update_expr
from customLogging.logger import safeLogger
from handlers.assets.createAsset import create_asset
from models.assetsV3 import CreateAssetRequestModel, AssetLinksModel
from handlers.databases.createDatabase import create_database
from models.databases import CreateDatabaseRequestModel
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from typing import Dict, List, Optional, Any, Union, Tuple
from boto3.dynamodb.conditions import Key
from common.validators import validate

# Initialize AWS clients
dynamodb = boto3.resource('dynamodb')
sns_client = boto3.client('sns')
s3_client = boto3.client('s3')
lambda_client = boto3.client('lambda')
dynamodb_client = boto3.client('dynamodb')
logger = safeLogger(service_name="sqsBucketSync")

# Environment variables
asset_bucket_name = os.environ.get('ASSET_BUCKET_NAME')
asset_bucket_prefix = os.environ.get('ASSET_BUCKET_PREFIX')

s3_asset_buckets_table = os.environ.get('S3_ASSET_BUCKETS_STORAGE_TABLE_NAME')
asset_table_name = os.environ.get('ASSET_STORAGE_TABLE_NAME')
db_table_name = os.environ["DATABASE_STORAGE_TABLE_NAME"]

database_id = os.environ.get('DEFAULT_DATABASE_ID')  

openSearchIndexing_lambda_name = os.environ["INDEXING_FUNCTION_NAME"]

if not database_id:
    raise Exception('databaseId not configured')

# Cache implementation
class SimpleCache:
    """Simple in-memory cache with TTL"""
    def __init__(self):
        self.cache = {}
        
    def get(self, key):
        """Get value from cache if it exists and is not expired"""
        if key in self.cache:
            value, expiry = self.cache[key]
            if expiry > time.time():
                return value
            else:
                # Remove expired item
                del self.cache[key]
        return None
        
    def set(self, key, value, ttl=60):  # Default TTL: 60 seconds
        """Set value in cache with expiry time"""
        self.cache[key] = (value, time.time() + ttl)
        
    def clear(self):
        """Clear all cache entries"""
        self.cache = {}

# Initialize caches
s3_buckets_cache = SimpleCache()  # Cache for S3 asset buckets table
database_cache = SimpleCache()    # Cache for database lookups
asset_cache = SimpleCache()       # Cache for asset lookups

def validate_asset_id(asset_id: str) -> bool:
    """
    Validate asset ID format using the common validator
    
    Args:
        asset_id: The asset ID to validate
        
    Returns:
        bool: True if valid, False otherwise
    """
    # Use the common validator for ASSET_ID
    (valid, _) = validate({
        'assetId': {
            'value': asset_id,
            'validator': 'ASSET_ID'
        }
    })
    
    return valid

def get_bucket_id(bucket_name: str, prefix: str) -> Optional[str]:
    """
    Get bucket ID from S3 asset buckets table
    
    Args:
        bucket_name: The S3 bucket name
        prefix: The prefix in the bucket
        
    Returns:
        str: Bucket ID if found, None otherwise
    """
    # Check cache first
    cache_key = f"{bucket_name}:{prefix}"
    cached_result = s3_buckets_cache.get(cache_key)
    if cached_result is not None:
        logger.info(f"Cache hit for bucket {bucket_name} with prefix {prefix}")
        return cached_result
    
    try:
        # Normalize prefix to ensure it ends with a slash
        if prefix and not prefix.endswith('/'):
            prefix = prefix + '/'
            
        # Query the S3 asset buckets table
        table = dynamodb.Table(s3_asset_buckets_table)
        response = table.query(
            IndexName="bucketNameGSI",
            KeyConditionExpression=Key('bucketName').eq(bucket_name) & Key('baseAssetsPrefix').eq(prefix)
        )
        
        if response.get('Items'):
            bucket_id = response['Items'][0].get('bucketId')
            is_versioning_enabled = response['Items'][0].get('isVersioningEnabled', False)
            
            # Cache the result
            s3_buckets_cache.set(cache_key, bucket_id)
            s3_buckets_cache.set(f"{bucket_id}:versioning", is_versioning_enabled)
            
            return bucket_id
        
        return None
    except Exception as e:
        logger.exception(f"Error getting bucket ID: {e}")
        return None

def is_versioning_enabled(bucket_id: str) -> bool:
    """
    Check if versioning is enabled for a bucket
    
    Args:
        bucket_id: The bucket ID
        
    Returns:
        bool: True if versioning is enabled, False otherwise
    """
    # Check cache first
    cache_key = f"{bucket_id}:versioning"
    cached_result = s3_buckets_cache.get(cache_key)
    if cached_result is not None:
        return cached_result
    
    try:
        # Query the S3 asset buckets table
        table = dynamodb.Table(s3_asset_buckets_table)
        response = table.get_item(Key={'bucketId': bucket_id})
        
        if 'Item' in response:
            is_versioning_enabled = response['Item'].get('isVersioningEnabled', False)
            
            # Cache the result
            s3_buckets_cache.set(cache_key, is_versioning_enabled)
            
            return is_versioning_enabled
        
        return False
    except Exception as e:
        logger.exception(f"Error checking if versioning is enabled: {e}")
        return False

def lookup_asset(bucket_id: str, asset_id: str) -> Optional[Dict]:
    """
    Look up asset in DynamoDB
    
    Args:
        bucket_id: The bucket ID
        asset_id: The asset ID
        
    Returns:
        dict: Asset data if found, None otherwise
    """
    # Check cache first
    cache_key = f"{bucket_id}:{asset_id}"
    cached_result = asset_cache.get(cache_key)
    if cached_result is not None:
        logger.info(f"Cache hit for asset {asset_id} in bucket {bucket_id}")
        return cached_result
    
    try:
        # Query the asset table using the GSI
        table = dynamodb.Table(asset_table_name)
        response = table.query(
            IndexName="BucketIdGSI",
            KeyConditionExpression=Key('bucketId').eq(bucket_id) & Key('assetId').eq(asset_id)
        )
        
        if response.get('Items'):
            asset_data = response['Items'][0]
            
            # Cache the result
            asset_cache.set(cache_key, asset_data)
            
            return asset_data
        
        return None
    except Exception as e:
        logger.exception(f"Error looking up asset: {e}")
        return None

def lookup_databases(bucket_id: str) -> List[Dict]:
    """
    Look up databases by bucket ID
    
    Args:
        bucket_id: The bucket ID
        
    Returns:
        list: List of database data
    """
    try:
        # Scan the database table for matching bucket ID
        table = dynamodb.Table(db_table_name)
        response = table.scan(
            FilterExpression=Key('defaultBucketId').eq(bucket_id)
        )
        
        databases = response.get('Items', [])
        
        # Cache each database individually by databaseId
        for db in databases:
            if 'databaseId' in db:
                cache_key = f"database:{db['databaseId']}"
                database_cache.set(cache_key, db)
        
        return databases
    except Exception as e:
        logger.exception(f"Error looking up databases: {e}")
        return []

def create_new_database(bucket_id: str, database_id: str) -> Optional[str]:
    """
    Create a new database
    
    Args:
        bucket_id: The bucket ID
        database_id: The database ID
        
    Returns:
        str: Database ID if created successfully, None otherwise
    """
    try:
        # Create database request model
        request_model = CreateDatabaseRequestModel(
            databaseId=database_id,
            description=f"Auto-created database for bucket {bucket_id}",
            defaultBucketId=bucket_id
        )
        
        # Create the database
        response = create_database(request_model)
        
        # Add the new database to the cache by databaseId
        new_db = {
            'databaseId': database_id,
            'description': f"Auto-created database for bucket {bucket_id}",
            'defaultBucketId': bucket_id
        }
        cache_key = f"database:{database_id}"
        database_cache.set(cache_key, new_db)
        
        return response.databaseId
    except Exception as e:
        logger.exception(f"Error creating database: {e}")
        return None

def create_new_asset(bucket_id: str, database_id: str, asset_id: str) -> Optional[str]:
    """
    Create a new asset
    
    Args:
        bucket_id: The bucket ID
        database_id: The database ID
        asset_id: The asset ID
        
    Returns:
        str: Asset ID if created successfully, None otherwise
    """
    try:
        # Create asset request model
        request_model = CreateAssetRequestModel(
            databaseId=database_id,
            assetId=asset_id,
            assetName=asset_id,
            description=f"Auto-created asset for {asset_id}",
            isDistributable=True,
            tags=[],
            assetLinks=None
        )
        
        # Create the asset
        # Note: We're passing an empty dict for claims_and_roles since this is a system operation
        response = create_asset(request_model, {"tokens": ["system"]}, True)
        
        # Add the new asset to the cache instead of clearing it
        cache_key = f"{bucket_id}:{asset_id}"
        new_asset = {
            'databaseId': database_id,
            'assetId': asset_id,
            'assetName': asset_id,
            'description': f"Auto-created asset for {asset_id}",
            'isDistributable': True,
            'tags': [],
            'bucketId': bucket_id
        }
        asset_cache.set(cache_key, new_asset)
        
        return response.assetId
    except Exception as e:
        logger.exception(f"Error creating asset: {e}")
        return None

def update_s3_metadata(bucket_name: str, object_key: str, database_id: str, asset_id: str) -> bool:
    """
    Update S3 object metadata
    
    Args:
        bucket_name: The S3 bucket name
        object_key: The S3 object key
        database_id: The database ID
        asset_id: The asset ID
        
    Returns:
        bool: True if updated successfully, False otherwise
    """
    try:
        # Get the current object metadata
        response = s3_client.head_object(
            Bucket=bucket_name,
            Key=object_key
        )
        
        # Check if metadata already matches
        current_metadata = response.get('Metadata', {})
        if current_metadata.get('databaseid') == database_id and current_metadata.get('assetid') == asset_id:
            logger.info(f"Metadata already matches for {object_key}")
            return True
        
        # Copy the object to itself with updated metadata
        metadata = {**current_metadata, 'databaseid': database_id, 'assetid': asset_id}
        
        s3_client.copy_object(
            Bucket=bucket_name,
            CopySource={'Bucket': bucket_name, 'Key': object_key},
            Key=object_key,
            Metadata=metadata,
            MetadataDirective='REPLACE'
        )
        
        logger.info(f"Updated metadata for {object_key}")
        return True
    except Exception as e:
        logger.exception(f"Error updating S3 metadata: {e}")
        return False

def delete_s3_object(bucket_name: str, object_key: str, versioning_enabled: bool) -> bool:
    """
    Delete S3 object and all its versions if versioning is enabled
    
    Args:
        bucket_name: The S3 bucket name
        object_key: The S3 object key
        versioning_enabled: Whether versioning is enabled
        
    Returns:
        bool: True if deleted successfully, False otherwise
    """
    try:
        if versioning_enabled:
            # List all versions of the object
            versions = s3_client.list_object_versions(
                Bucket=bucket_name,
                Prefix=object_key
            )
            
            # Delete all versions
            for version in versions.get('Versions', []):
                s3_client.delete_object(
                    Bucket=bucket_name,
                    Key=object_key,
                    VersionId=version['VersionId']
                )
                
            # Delete any delete markers
            for marker in versions.get('DeleteMarkers', []):
                s3_client.delete_object(
                    Bucket=bucket_name,
                    Key=object_key,
                    VersionId=marker['VersionId']
                )
        else:
            # Delete the object
            s3_client.delete_object(
                Bucket=bucket_name,
                Key=object_key
            )
        
        logger.info(f"Deleted object {object_key}")
        return True
    except Exception as e:
        logger.exception(f"Error deleting S3 object: {e}")
        return False

def extract_asset_id_from_key(object_key: str, prefix: str) -> Optional[str]:
    """
    Extract asset ID from object key
    
    Args:
        object_key: The S3 object key
        prefix: The base prefix
        
    Returns:
        str: Asset ID if found, None otherwise
    """
    if not prefix.endswith('/'):
        prefix = prefix + '/'
        
    # Remove the prefix from the object key
    if object_key.startswith(prefix):
        relative_path = object_key[len(prefix):]
        
        # The asset ID is the first part of the path
        parts = relative_path.split('/')
        if parts:
            return parts[0]
    
    return None

def verify_database_exists(database_id):
    """Check if a database exists"""
    table = dynamodb.Table(db_table_name)
    try:
        response = table.get_item(Key={'databaseId': database_id})
        if 'Item' not in response:
            return False
        return True
    except Exception as e:
        logger.exception(f"Error verifying database: {e}")
        raise Exception(f"Error verifying database: {str(e)}")

def verify_asset_exists(database_id, asset_id):
    """Check if an asset exists in the database"""
    table = dynamodb.Table(asset_table_name)
    try:
        response = table.get_item(Key={
            'databaseId': database_id,
            'assetId': asset_id
        })
        return 'Item' in response
    except Exception as e:
        logger.exception(f"Error verifying asset: {e}")
        raise Exception(f"Error verifying asset: {str(e)}")

def invoke_lambda(function_name, payload, invocation_type="RequestResponse"):
    """Invoke a lambda function with the given payload"""
    try:
        logger.info(f"Invoking {function_name} lambda...")
        lambda_response = lambda_client.invoke(
            FunctionName=function_name,
            InvocationType=invocation_type,
            Payload=json.dumps(payload).encode('utf-8')
        )
        
        if invocation_type == "RequestResponse":
            stream = lambda_response['Payload']
            response_payload = json.loads(stream.read().decode("utf-8"))
            logger.info(f"Lambda response: {response_payload}")
            return response_payload
        return None
    except Exception as e:
        logger.exception(f"Error invoking lambda function {function_name}: {e}")
        raise Exception(f"Error invoking lambda function {function_name}: {str(e)}")

def runOpenSearchIndexingLambda(event):
    """
    Run the OpenSearch indexing lambda
    
    Args:
        event: The event to pass to the lambda
    """
    try:
        # Prepare payload for indexing
        event.update({
            "ASSET_BUCKET_NAME": asset_bucket_name,
            "ASSET_BUCKET_PREFIX": asset_bucket_prefix
        })
        
        # Invoke openSearchIndexing lambda
        invoke_lambda(openSearchIndexing_lambda_name, event, 'Event')
        logger.info("Successfully invoked OpenSearch indexing lambda")
    except Exception as e:
        logger.exception(f"Error running OpenSearch indexing lambda: {e}")
        # We don't re-raise the exception here to avoid stopping the process

def process_s3_record(record: Dict) -> Tuple[bool, str]:
    """
    Process a single S3 record
    
    Args:
        record: The S3 record to process
        
    Returns:
        tuple: (success, message)
    """
    try:
        if not record.get('s3'):
            return False, "Record does not contain S3 information"

        bucket_name = record['s3']['bucket']['name']
        object_key = record['s3']['object']['key']
        
        logger.info(f"Processing S3 record for bucket {bucket_name}, key {object_key}")
        
        # 1.a. Check if record bucket and base prefix matches the environment variables
        if asset_bucket_name and bucket_name != asset_bucket_name:
            logger.info(f"Bucket {bucket_name} does not match configured bucket {asset_bucket_name}, skipping")
            return False, f"Bucket {bucket_name} does not match configured bucket"
        
        if asset_bucket_prefix and not object_key.startswith(asset_bucket_prefix):
            logger.info(f"Object key {object_key} does not start with configured prefix {asset_bucket_prefix}, skipping")
            return False, f"Object key does not start with configured prefix"
        
        # Use the configured prefix or empty string
        prefix = asset_bucket_prefix or ""
        
        # 1.b Check if bucket name and prefix have a record in the S3 asset buckets table
        bucket_id = get_bucket_id(bucket_name, prefix)
        if not bucket_id:
            logger.info(f"No bucket ID found for {bucket_name} with prefix {prefix}, skipping")
            return False, f"No bucket ID found for {bucket_name} with prefix {prefix}"
        
        # Extract asset ID from the object key
        asset_id = extract_asset_id_from_key(object_key, prefix)
        if not asset_id:
            logger.info(f"Could not extract asset ID from {object_key}, skipping")
            return False, f"Could not extract asset ID from {object_key}"
        
        # 1.c Check if asset ID is a special folder to skip
        if asset_id in ['temp-uploads', 'preview', 'pipeline']:
            logger.info(f"Asset ID {asset_id} is a special folder, skipping")
            return False, f"Asset ID {asset_id} is a special folder"
        
        # 1.d Validate asset ID
        if not validate_asset_id(asset_id):
            logger.info(f"Asset ID {asset_id} is not valid, skipping")
            return False, f"Asset ID {asset_id} is not valid"
        
        # 2.a. Lookup asset in assets dynamoDB table
        asset_data = lookup_asset(bucket_id, asset_id)
        database_id_to_use = None
        
        if asset_data:
            logger.info(f"Asset {asset_id} found in bucket {bucket_id}")
            database_id_to_use = asset_data.get('databaseId')
        else:
            # 2.b. Lookup databases that match the bucketId
            databases = lookup_databases(bucket_id)
            
            if not databases:
                # Create a new database with defaultDatabaseId
                logger.info(f"No databases found for bucket {bucket_id}, creating new database")
                created_db_id = create_new_database(bucket_id, database_id)
                if not created_db_id:
                    logger.error(f"Failed to create database for bucket {bucket_id}")
                    return False, f"Failed to create database for bucket {bucket_id}"
                database_id_to_use = created_db_id
            elif len(databases) == 1:
                # Use the single database
                database_id_to_use = databases[0]['databaseId']
            else:
                # Multiple databases, check if any match defaultDatabaseId
                # First check cache
                cache_key = f"database:{database_id}"
                default_db = database_cache.get(cache_key)
                
                # If not in cache, check the list of databases
                if default_db is None:
                    default_db = next((db for db in databases if db['databaseId'] == database_id), None)
                if default_db:
                    database_id_to_use = default_db['databaseId']
                else:
                    # Create a new database with defaultDatabaseId
                    logger.info(f"No default database found for bucket {bucket_id}, creating new database")
                    created_db_id = create_new_database(bucket_id, database_id)
                    if not created_db_id:
                        logger.error(f"Failed to create database for bucket {bucket_id}")
                        return False, f"Failed to create database for bucket {bucket_id}"
                    database_id_to_use = created_db_id
            
            # 3. If asset doesn't exist, create it
            if not asset_data:
                logger.info(f"Creating new asset {asset_id} in database {database_id_to_use}")
                created_asset_id = create_new_asset(bucket_id, database_id_to_use, asset_id)
                if not created_asset_id:
                    logger.error(f"Failed to create asset {asset_id} in database {database_id_to_use}")
                    return False, f"Failed to create asset {asset_id} in database {database_id_to_use}"
        
        # 4. Check if the object key ends with "init" - If so return either way after attempting delete
        if object_key.endswith('init') or object_key.endswith('init/'):
            # Check if versioning is enabled
            versioning_enabled = is_versioning_enabled(bucket_id)
            
            # Delete the init object
            logger.info(f"Deleting init object {object_key}")
            delete_result = delete_s3_object(bucket_name, object_key, versioning_enabled)
            if not delete_result:
                logger.error(f"Failed to delete init object {object_key}")
                return False, f"Failed to delete init object {object_key}"
            
            return True, f"Deleted init object {object_key}"
        
        # 5. Check if file has S3 metadata attributes that match databaseid and assetid
        update_result = update_s3_metadata(bucket_name, object_key, database_id_to_use, asset_id)
        if not update_result:
            logger.error(f"Failed to update metadata for {object_key}")
            return False, f"Failed to update metadata for {object_key}"
        
        return True, f"Successfully processed {object_key}"
    except Exception as e:
        logger.exception(f"Error processing S3 record: {e}")
        return False, f"Error processing S3 record: {str(e)}"

def on_storage_event_created(event):
    """
    Process S3 storage events for created files
    
    Args:
        event: The S3 event
    """
    logger.info(f"Processing storage event: {json.dumps(event)}")
    
    success_count = 0
    error_count = 0
    skip_count = 0
    
    for record in event.get('Records', []):
        if not record.get('s3'):
            skip_count += 1
            continue

        success, message = process_s3_record(record)
        if success:
            success_count += 1
        else:
            if "skipping" in message.lower():
                skip_count += 1
            else:
                error_count += 1
    
    logger.info(f"Processed {len(event.get('Records', []))} records: {success_count} successful, {error_count} errors, {skip_count} skipped")
    
    # Return True if there were no errors, False otherwise
    return error_count == 0

def parse_event(event):
    """
    Parse the event to handle different sources (SQS, SNS, direct S3)
    
    Args:
        event: The event to parse
        
    Returns:
        dict: The parsed S3 event
    """
    try:
        # Check if this is a direct S3 event
        if 'Records' in event and event['Records'] and 'eventSource' in event['Records'][0] and event['Records'][0]['eventSource'] == 'aws:s3':
            logger.info("Detected direct S3 event")
            return event
        
        # Check if this is an SQS event
        if 'Records' in event and event['Records'] and 'eventSource' in event['Records'][0] and event['Records'][0]['eventSource'] == 'aws:sqs':
            logger.info("Detected SQS event")
            s3_events = []
            
            for record in event['Records']:
                if not record.get('body'):
                    continue
                
                try:
                    parsed_body = json.loads(record['body'])
                    
                    # Check if this is an SNS message
                    if 'Message' in parsed_body:
                        message = json.loads(parsed_body['Message'])
                        if 'Records' in message:
                            s3_events.append(message)
                    elif 'Records' in parsed_body:
                        s3_events.append(parsed_body)
                except Exception as e:
                    logger.exception(f"Error parsing SQS record body: {e}")
            
            if s3_events:
                # Combine all S3 events into a single event
                combined_event = {'Records': []}
                for event in s3_events:
                    combined_event['Records'].extend(event['Records'])
                
                return combined_event
        
        # Check if this is an SNS event
        if 'Records' in event and event['Records'] and 'EventSource' in event['Records'][0] and event['Records'][0]['EventSource'] == 'aws:sns':
            logger.info("Detected SNS event")
            s3_events = []
            
            for record in event['Records']:
                if not record.get('Sns') or not record['Sns'].get('Message'):
                    continue
                
                try:
                    message = json.loads(record['Sns']['Message'])
                    if 'Records' in message:
                        s3_events.append(message)
                except Exception as e:
                    logger.exception(f"Error parsing SNS message: {e}")
            
            if s3_events:
                # Combine all S3 events into a single event
                combined_event = {'Records': []}
                for event in s3_events:
                    combined_event['Records'].extend(event['Records'])
                
                return combined_event
    except Exception as e:
        logger.exception(f"Error parsing event: {e}")
    
    # Return the original event if we couldn't parse it
    return event

def lambda_handler_created(event):
    """
    Handler for file creation events from SQS
    
    Args:
        event: The SQS event
    """
    logger.info(f"File creation event received: {json.dumps(event)}")
    
    try:
        # Parse the event to handle different sources
        parsed_event = parse_event(event)
        
        # Process the storage event
        if parsed_event.get('Records'):
            success = on_storage_event_created(parsed_event)
            
            # Only run indexing if there were no hard errors
            if success:
                # Run OpenSearch indexing
                runOpenSearchIndexingLambda(event)
            else:
                logger.warning("Skipping OpenSearch indexing due to errors in processing")
        else:
            logger.warning("No records found in parsed event")
    except Exception as e:
        logger.exception(f"Error in lambda_handler_created: {e}")
        # We still want to run the indexing lambda even if there are errors
        runOpenSearchIndexingLambda(event)

def lambda_handler_deleted(event):
    """
    Handler for file deleted events from SQS
    
    Args:
        event: The SQS event
    """
    logger.info(f"File deletion event received: {json.dumps(event)}")
    
    try:
        # For deletions, we just run the OpenSearch indexing lambda
        runOpenSearchIndexingLambda(event)
    except Exception as e:
        logger.exception(f"Error in lambda_handler_deleted: {e}")
