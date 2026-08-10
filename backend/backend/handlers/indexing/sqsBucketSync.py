"""
This file is responsible for handling bucket syncing events.
When syncing is enabled and files are created or deleted in a specified Amazon S3 bucket,
the business logic below manages mapping the Amazon S3 information to VAMS assets.
"""
import json
import os
import boto3
import time
import hashlib
import urllib.parse
from datetime import datetime
from customLogging.logger import safeLogger
from common.resourceNames import get_table_name, ResourceKeys
from handlers.assets.createAsset import create_asset
from handlers.assets.assetCount import update_asset_count
from models.assetsV3 import CreateAssetRequestModel
from handlers.databases.createDatabase import create_database
from models.databases import CreateDatabaseRequestModel
from typing import Dict, List, Optional, Tuple
from botocore.exceptions import ClientError
from botocore.config import Config as BotoConfig
from boto3.dynamodb.conditions import Key
from common.validators import validate
from common.s3MetadataKeys import (
    ASSET_ID_METADATA_KEY,
    DATABASE_ID_METADATA_KEY,
    VAMS_CHANGE_SOURCE_METADATA_KEY,
    VAMS_CHANGE_USER_ID_METADATA_KEY,
    VAMS_CHANGE_WORKFLOW_ID_METADATA_KEY,
    VAMS_CHANGE_WORKFLOW_EXECUTION_ID_METADATA_KEY,
    VAMS_CHANGE_ASSET_ID_FROM_METADATA_KEY,
    VAMS_CHANGE_DATABASE_ID_FROM_METADATA_KEY,
    VAMS_CHANGE_ASSET_FILE_PATH_FROM_METADATA_KEY,
    VAMS_CHANGE_ASSET_FILE_VERSION_FROM_METADATA_KEY,
    VAMS_CHANGE_SOURCE_DIRECT,
    normalize_history_file_path,
)
from common.s3PathPatterns import RESERVED_S3_PREFIX_FOLDERS
from common.s3 import is_object_version_archived
from common.assetHistory import (
    CHANGE_SOURCE_UNARCHIVE_DIRECT,
    build_asset_snapshot,
    write_asset_history_record,
)

# Initialize AWS clients
dynamodb = boto3.resource('dynamodb')
sns_client = boto3.client('sns')
s3_client = boto3.client('s3')
s3_resource = boto3.resource('s3')
lambda_client = boto3.client('lambda')
# Bounded timeouts on the EventBridge client so an unreachable endpoint (e.g. an isolated-subnet
# deployment missing the events VPC endpoint) fails fast rather than blocking the ingestion hot
# path for the full default connect timeout on every batch. The publish is best-effort.
events_client = boto3.client(
    'events',
    config=BotoConfig(connect_timeout=3, read_timeout=5, retries={'max_attempts': 2}))
dynamodb_client = boto3.client('dynamodb')
logger = safeLogger(service_name="sqsBucketSync")

# Environment variables
try:
    asset_bucket_name = os.environ.get('ASSET_BUCKET_NAME')
    asset_bucket_prefix = os.environ.get('ASSET_BUCKET_PREFIX')
    s3_asset_buckets_table = get_table_name(ResourceKeys.S3_ASSET_BUCKETS_STORAGE_TABLE)
    asset_table_name = get_table_name(ResourceKeys.ASSET_STORAGE_TABLE)
    db_table_name = get_table_name(ResourceKeys.DATABASE_STORAGE_TABLE)
    database_id = os.environ.get('DEFAULT_DATABASE_ID')
    file_indexer_sns_topic_arn = os.environ.get("FILE_INDEXER_SNS_TOPIC_ARN", "")
    # Orchestration EventBridge bus + deployment event-source prefix for publishing file-upload
    # trigger events (Phase 2 fileUpload delivery). Optional: empty disables the EventBridge publish.
    orchestration_bus_name = os.environ.get("ORCHESTRATION_BUS_NAME", "")
    orchestration_event_source_prefix = os.environ.get("ORCHESTRATION_EVENT_SOURCE_PREFIX", "")
    asset_file_metadata_table_name = get_table_name(ResourceKeys.ASSET_FILE_METADATA_STORAGE_TABLE)
    file_attribute_table_name = get_table_name(ResourceKeys.FILE_ATTRIBUTE_STORAGE_TABLE)
    asset_file_version_history_table_name = get_table_name(ResourceKeys.ASSET_FILE_VERSION_HISTORY_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed loading environment variables or resolving resource names")
    raise e

if not database_id:
    raise Exception('databaseId not configured')

# Initialize metadata tables
asset_file_metadata_table = dynamodb.Table(asset_file_metadata_table_name) if asset_file_metadata_table_name else None
file_attribute_table = dynamodb.Table(file_attribute_table_name) if file_attribute_table_name else None
asset_file_version_history_table = dynamodb.Table(asset_file_version_history_table_name) if asset_file_version_history_table_name else None

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

    def delete(self, key):
        """Remove a single cache entry if present"""
        self.cache.pop(key, None)

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

def is_file_archived(bucket: str, key: str, version_id: str = None) -> bool:
    """Determine if file is archived based on S3 delete markers.

    Delegates to the shared head_object-based helper, which is O(1) per check
    regardless of how many versions the key has.
    """
    return is_object_version_archived(bucket, key, version_id, client=s3_client)

def object_still_exists(bucket_name: str, object_key: str) -> bool:
    """Return True if the S3 object still exists (has a live current version).

    Used to guard asset auto-creation: an ObjectCreated event can be delivered
    (or redelivered under SQS at-least-once) after the object — and its asset —
    were deleted, which would otherwise recreate an empty ghost asset. A missing
    object (404) or a current delete marker (405 MethodNotAllowed) means the file
    is gone. On any other/unexpected error, fail open (return True) so a transient
    S3 error never suppresses legitimate ingestion of a genuinely new file.

    On a 404, retry with the alternative encoding (decoded <-> raw) — the same
    tolerance update_s3_metadata applies — so a genuinely new upload whose
    filename contains a '+' (e.g. BACC66K41F158AM+---.CATPart) is not mistaken
    for a deleted object when the event pipeline delivered the other shape.
    """
    try:
        s3_client.head_object(Bucket=bucket_name, Key=object_key)
        return True
    except ClientError as e:
        code = e.response.get('Error', {}).get('Code', '')
        if code == '405':
            # Current version is a delete marker — archived/gone regardless of
            # encoding, so do not run the +/space fallback.
            return False
        if code in ('404', 'NoSuchKey', 'NotFound'):
            alt_key = urllib.parse.unquote_plus(object_key)
            if alt_key == object_key:
                alt_key = urllib.parse.quote(object_key, safe="/+")
            if alt_key == object_key:
                return False  # nothing else to try
            try:
                s3_client.head_object(Bucket=bucket_name, Key=alt_key)
                return True
            except ClientError as e2:
                code2 = e2.response.get('Error', {}).get('Code', '')
                if code2 in ('404', 'NoSuchKey', 'NotFound'):
                    return False
                logger.warning(f"Unexpected error checking existence of {alt_key}; failing open: {e2}")
                return True
            except Exception as e2:
                logger.warning(f"Unexpected error checking existence of {alt_key}; failing open: {e2}")
                return True
        logger.warning(f"Unexpected error checking existence of {object_key}; failing open: {e}")
        return True
    except Exception as e:
        logger.warning(f"Unexpected error checking existence of {object_key}; failing open: {e}")
        return True

def is_object_permanently_deleted(bucket_name: str, object_key: str) -> bool:
    """Return True only when no versions or delete markers remain for the key.

    Distinguishes a permanent delete (all versions removed — metadata cleanup is
    safe) from an archive (delete marker over live versions — reversible, so
    metadata must be preserved). Fails closed: on any error, treat the object as
    NOT permanently deleted so metadata is never destroyed on uncertainty.
    """
    try:
        response = s3_client.list_object_versions(
            Bucket=bucket_name,
            Prefix=object_key,
            MaxKeys=25
        )
        for version in response.get('Versions', []):
            if version.get('Key') == object_key:
                return False
        for marker in response.get('DeleteMarkers', []):
            if marker.get('Key') == object_key:
                return False
        return True
    except Exception as e:
        logger.warning(f"Error checking version state for {object_key}; preserving metadata: {e}")
        return False


def determine_asset_type(assetId, bucket, prefix):
    """Determine the asset type based on S3 contents"""
    try:
        
        logger.info(f"Determining asset type from bucket: {bucket}, prefix: {prefix}")
        
        # List all objects with the specified prefix
        response = s3_client.list_objects_v2(
            Bucket=bucket,
            Prefix=prefix,
        )
        
        # Get the contents and filter out folder markers (objects ending with '/')
        contents = response.get('Contents', [])
        
        # Filter out archived files
        non_archived_files = []
        file_count = 0
        for item in contents:
            if item['Key'].endswith('/'):
                # Skip folder markers
                continue
                
            try:
                # Check if file is archived using the new method
                if not is_file_archived(bucket, item['Key']):
                    non_archived_files.append(item)
                    file_count += 1
                    
                    # Short circuit if we've found more than one file
                    if file_count > 1:
                        logger.info(f"Found multiple files, short-circuiting and returning 'folder'")
                        return 'folder'
            except Exception as e:
                logger.warning(f"Error checking if file {item['Key']} is archived: {e}")
                # If we can't check archive status, include the file by default
                non_archived_files.append(item)
                file_count += 1
                
                # Short circuit if we've found more than one file
                if file_count > 1:
                    logger.info(f"Found multiple files, short-circuiting and returning 'folder'")
                    return 'folder'
        
        # At this point, we have 0 or 1 files
        logger.info(f"Found {file_count} non-archived files in {bucket}/{prefix} (total objects: {len(contents)})")
        
        # Determine asset type
        if file_count == 0:
            logger.info("No non-archived files found, returning None")
            return None  # No files found
        else:  # file_count == 1
            # Extract file extension from the single file
            file_key = non_archived_files[0]['Key']
            file_name = os.path.basename(file_key)
            
            # Skip if the file is just a folder marker
            if file_name == '':
                logger.info("Single object is a folder marker, returning 'folder'")
                return 'folder'
                
            if '.' in file_name:
                extension = '.' + file_name.split('.')[-1].lower()  # Convert to lowercase for consistency
                logger.info(f"Determined asset type as file with extension: {extension}")
                return extension
            else:
                logger.info("Determined asset type as unknown (no file extension)")
                return 'unknown'
    except Exception as e:
        logger.exception(f"Error determining asset type: {e}")
        return None
    
def update_asset_type(bucket_id: str, asset_id: str, bucket_name: str, asset_base_key: str) -> bool:
    """
    Update asset type based on bucket contents
    
    Args:
        bucket_id: The bucket ID
        asset_id: The asset ID
        bucket_name: The S3 bucket name
        asset_base_key: The base key for the asset in S3
        
    Returns:
        bool: True if updated successfully, False otherwise
    """
    try:
        # Look up asset in DynamoDB
        asset_data = lookup_asset(bucket_id, asset_id)
        if not asset_data:
            logger.warning(f"Asset {asset_id} not found in bucket {bucket_id}, cannot update asset type")
            return False
        
        # Determine asset type
        asset_type = determine_asset_type(asset_id, bucket_name, asset_base_key)
        logger.info(f"Asset type determined for asset {asset_id}: {asset_type}")
        
        # Update asset type if it has changed
        current_asset_type = asset_data.get('assetType')
        if asset_type and asset_type != current_asset_type:
            logger.info(f"Updating asset type for {asset_id} from {current_asset_type} to {asset_type}")

            if not _update_asset_type_attribute(bucket_id, asset_id, asset_data['databaseId'], asset_type):
                return False

            # Update cache
            cache_key = f"{bucket_id}:{asset_id}"
            asset_data['assetType'] = asset_type
            asset_cache.set(cache_key, asset_data)

            return True
        elif not asset_type and not current_asset_type:
            # If both are None/empty, set to 'none'
            logger.info(f"Setting default asset type 'none' for {asset_id}")

            if not _update_asset_type_attribute(bucket_id, asset_id, asset_data['databaseId'], 'none'):
                return False

            # Update cache
            cache_key = f"{bucket_id}:{asset_id}"
            asset_data['assetType'] = 'none'
            asset_cache.set(cache_key, asset_data)

            return True

        logger.info(f"Asset type for {asset_id} remains unchanged: {current_asset_type}")
        return True
    except Exception as e:
        logger.exception(f"Error updating asset type: {e}")
        return False


def _update_asset_type_attribute(bucket_id: str, asset_id: str, database_id: str, asset_type: str) -> bool:
    """Set assetType on an existing asset record.

    The update is conditional on the record still existing: the asset may have
    been archived or deleted (moved out of this databaseId partition) between
    the cached lookup and this update, and an unconditional update_item would
    re-create a phantom record containing only the key and assetType.

    Args:
        bucket_id: The bucket ID (for cache invalidation)
        asset_id: The asset ID
        database_id: The databaseId partition the asset was looked up in
        asset_type: The asset type value to set

    Returns:
        bool: True if updated, False if the record no longer exists
    """
    table = dynamodb.Table(asset_table_name)
    try:
        table.update_item(
            Key={
                'databaseId': database_id,
                'assetId': asset_id
            },
            UpdateExpression="SET assetType = :assetType",
            ConditionExpression="attribute_exists(assetId)",
            ExpressionAttributeValues={
                ':assetType': asset_type
            }
        )
        return True
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            # Asset moved (archived/deleted) since the cached lookup - drop the
            # stale cache entries so later events re-query the current location
            logger.warning(
                f"Asset {asset_id} no longer exists in {database_id}; "
                "skipping asset type update and invalidating cache"
            )
            asset_cache.delete(f"{bucket_id}:{asset_id}")
            asset_cache.delete(f"{bucket_id}:{asset_id}:archived")
            return False
        raise

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
        # Query the asset table using the GSI. The GSI matches records in BOTH
        # the live and archived ({databaseId}#deleted) partitions — only a live
        # record counts here, otherwise an archived asset would be treated as
        # active (and its archived databaseId would leak into metadata keys).
        table = dynamodb.Table(asset_table_name)
        response = table.query(
            IndexName="BucketIdGSI",
            KeyConditionExpression=Key('bucketId').eq(bucket_id) & Key('assetId').eq(asset_id)
        )

        for item in response.get('Items', []):
            if not item.get('databaseId', '').endswith('#deleted'):
                # Cache the result
                asset_cache.set(cache_key, item)
                return item

        return None
    except Exception as e:
        logger.exception(f"Error looking up asset: {e}")
        return None

def lookup_archived_asset(bucket_id: str, asset_id: str) -> Optional[Dict]:
    """
    Look up archived asset in DynamoDB (with #deleted suffix in databaseId)
    
    Args:
        bucket_id: The bucket ID
        asset_id: The asset ID
        
    Returns:
        dict: Archived asset data if found, None otherwise
    """
    # Check cache first
    cache_key = f"{bucket_id}:{asset_id}:archived"
    cached_result = asset_cache.get(cache_key)
    if cached_result is not None:
        logger.info(f"Cache hit for archived asset {asset_id} in bucket {bucket_id}")
        return cached_result
    
    try:
        # Query the asset table using the GSI
        table = dynamodb.Table(asset_table_name)
        response = table.query(
            IndexName="BucketIdGSI",
            KeyConditionExpression=Key('bucketId').eq(bucket_id) & Key('assetId').eq(asset_id)
        )
        
        # Filter for archived assets (databaseId ends with #deleted)
        for item in response.get('Items', []):
            if item.get('databaseId', '').endswith('#deleted'):
                logger.info(f"Found archived asset {asset_id} in bucket {bucket_id}")
                # Cache the result
                asset_cache.set(cache_key, item)
                return item
        
        return None
    except Exception as e:
        logger.exception(f"Error looking up archived asset: {e}")
        return None

def lookup_archived_database(database_id: str) -> Optional[Dict]:
    """
    Look up archived database in DynamoDB (with #deleted suffix)
    
    Args:
        database_id: The database ID (without #deleted suffix)
        
    Returns:
        dict: Archived database data if found, None otherwise
    """
    # Check cache first
    cache_key = f"database:{database_id}:archived"
    cached_result = database_cache.get(cache_key)
    if cached_result is not None:
        logger.info(f"Cache hit for archived database {database_id}")
        return cached_result
    
    try:
        table = dynamodb.Table(db_table_name)
        archived_db_id = f"{database_id}#deleted"
        response = table.get_item(Key={'databaseId': archived_db_id})
        
        if 'Item' in response:
            logger.info(f"Found archived database {database_id}")
            # Cache the result
            database_cache.set(cache_key, response['Item'])
            return response['Item']
        
        return None
    except Exception as e:
        logger.exception(f"Error looking up archived database: {e}")
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

def get_bucket_info_from_bucket_id(bucket_id: str) -> Optional[Dict]:
    """
    Get bucket information from S3 asset buckets table using bucket ID.
    Uses caching to prevent excessive DynamoDB calls.
    
    Args:
        bucket_id: The bucket ID
        
    Returns:
        dict: Bucket information if found, None otherwise
    """
    # Check cache first
    cache_key = f"bucket_info:{bucket_id}"
    cached_result = s3_buckets_cache.get(cache_key)
    if cached_result is not None:
        logger.info(f"Cache hit for bucket info {bucket_id}")
        return cached_result
    
    try:
        buckets_table = dynamodb.Table(s3_asset_buckets_table)
        bucket_response = buckets_table.query(
            KeyConditionExpression=Key('bucketId').eq(bucket_id)
        )
        if bucket_response.get('Items'):
            bucket_info = bucket_response['Items'][0]
            # Cache the result
            s3_buckets_cache.set(cache_key, bucket_info)
            return bucket_info
        
        return None
    except Exception as e:
        logger.exception(f"Error getting bucket info: {e}")
        return None

def get_or_create_database_for_bucket(bucket_id: str, bucket_name: str, prefix: str) -> Optional[str]:
    """
    Get or create a database for a specific bucket/prefix combination.
    Ensures unique database names per bucket/prefix and checks for archived databases.
    Uses caching to prevent excessive DynamoDB calls.
    
    Args:
        bucket_id: The bucket ID
        bucket_name: The S3 bucket name
        prefix: The base prefix in the bucket
        
    Returns:
        str: Database ID if found or created successfully, None if archived or error
    """
    # Check cache for this specific bucket/prefix combination
    prefix_for_cache = prefix.rstrip('/') if prefix else 'root'
    cache_key = f"db_for_bucket_prefix:{bucket_name}:{prefix_for_cache}"
    cached_db_id = database_cache.get(cache_key)
    if cached_db_id is not None:
        logger.info(f"Cache hit for database for bucket {bucket_name} prefix {prefix}")
        return cached_db_id
    
    # Look up databases that match this bucket
    databases = lookup_databases(bucket_id)
    
    # Filter databases to match this specific bucket/prefix combination
    matching_databases = []
    for db in databases:
        # Get bucket info for this database (uses caching)
        bucket_info = get_bucket_info_from_bucket_id(db.get('defaultBucketId'))
        if bucket_info:
            # Normalize prefix for comparison
            db_prefix = bucket_info.get('baseAssetsPrefix', '').rstrip('/')
            check_prefix = prefix.rstrip('/') if prefix else ''
            
            if bucket_info.get('bucketName') == bucket_name and db_prefix == check_prefix:
                matching_databases.append(db)
    
    # If we found matching databases, use the first one (or default if it exists)
    if matching_databases:
        # Check if default database exists in matching databases
        default_db = next((db for db in matching_databases if db['databaseId'] == database_id), None)
        if default_db:
            logger.info(f"Using default database {database_id} for bucket {bucket_name} prefix {prefix}")
            # Cache the result
            database_cache.set(cache_key, default_db['databaseId'])
            return default_db['databaseId']
        else:
            logger.info(f"Using existing database {matching_databases[0]['databaseId']} for bucket {bucket_name} prefix {prefix}")
            # Cache the result
            database_cache.set(cache_key, matching_databases[0]['databaseId'])
            return matching_databases[0]['databaseId']
    
    # No matching database found - need to create one
    # Generate unique database ID based on bucket and prefix
    prefix_for_hash = prefix.rstrip('/') if prefix else 'root'
    prefix_hash = hashlib.md5(f"{bucket_name}:{prefix_for_hash}".encode()).hexdigest()[:8] # nosec B324
    unique_db_id = f"{database_id}-{prefix_hash}"
    
    # Check cache for this specific database ID
    db_cache_key = f"database:{unique_db_id}"
    cached_db = database_cache.get(db_cache_key)
    if cached_db is not None:
        logger.info(f"Cache hit for database {unique_db_id}")
        # Cache the bucket/prefix mapping as well
        database_cache.set(cache_key, unique_db_id)
        return unique_db_id
    
    # Check if this database ID already exists (active)
    existing_db = None
    try:
        db_table = dynamodb.Table(db_table_name)
        response = db_table.get_item(Key={'databaseId': unique_db_id})
        existing_db = response.get('Item')
        if existing_db:
            # Cache the result
            database_cache.set(db_cache_key, existing_db)
    except Exception as e:
        logger.warning(f"Error checking for existing database: {e}")
    
    # If database exists and is active, use it
    if existing_db:
        logger.info(f"Using existing database {unique_db_id} for bucket {bucket_name} prefix {prefix}")
        # Cache the bucket/prefix mapping
        database_cache.set(cache_key, unique_db_id)
        return existing_db['databaseId']
    
    # Check for archived version - DO NOT recreate if archived
    archived_db = lookup_archived_database(unique_db_id)
    if archived_db:
        logger.info(f"Database {unique_db_id} is archived, skipping creation")
        # Cache the fact that this database is archived (cache as None)
        database_cache.set(cache_key, None)
        return None
    
    # Create new database
    logger.info(f"Creating new database {unique_db_id} for bucket {bucket_name} prefix {prefix}")
    created_db_id = create_new_database(bucket_id, unique_db_id)
    
    # Cache the result if creation was successful
    if created_db_id:
        database_cache.set(cache_key, created_db_id)
    
    return created_db_id

def restore_archived_asset(bucket_id: str, asset_id: str, archived_asset: Dict) -> Optional[str]:
    """Restore an archived asset record after a direct S3 upload to its prefix.

    DynamoDB-only unarchive: the asset record moves back from the
    {databaseId}#deleted partition to the live partition. Previously archived
    files keep their S3 delete markers — the files now present under the prefix
    define the asset's contents; users unarchive individual old files via the
    file unarchive API as needed.

    Concurrency: a batch of ObjectCreated events for the same archived asset can
    race here. The live-partition write is conditional on no live record
    existing, and the archived-record delete is keyed exactly, so the restore
    happens once; losers treat the asset as already restored.

    Returns:
        The live database ID on success (or when already restored), None on error.
    """
    archived_db_id = archived_asset.get('databaseId', '')
    if not archived_db_id.endswith('#deleted'):
        logger.warning(f"Archived asset {asset_id} has unexpected databaseId {archived_db_id}")
        return None
    live_db_id = archived_db_id[:-len('#deleted')]

    try:
        table = dynamodb.Table(asset_table_name)

        restored = {k: v for k, v in archived_asset.items()
                    if k not in ('status', 'archivedAt', 'archivedBy', 'archivedReason')}
        restored['databaseId'] = live_db_id
        restored['unarchivedAt'] = datetime.utcnow().isoformat()
        restored['unarchivedBy'] = 'SYSTEM_USER'
        restored['unarchivedReason'] = 'Auto-restored by bucket sync: new file uploaded directly to S3 under archived asset prefix'

        try:
            table.put_item(
                Item=restored,
                ConditionExpression='attribute_not_exists(databaseId) AND attribute_not_exists(assetId)'
            )
        except ClientError as e:
            if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                logger.info(f"Asset {asset_id} already restored in {live_db_id} by a concurrent event")
                asset_cache.delete(f"{bucket_id}:{asset_id}")
                asset_cache.delete(f"{bucket_id}:{asset_id}:archived")
                return live_db_id
            raise

        table.delete_item(Key={'databaseId': archived_db_id, 'assetId': asset_id})

        # Invalidate caches so subsequent events in this container see the live record
        asset_cache.delete(f"{bucket_id}:{asset_id}")
        asset_cache.delete(f"{bucket_id}:{asset_id}:archived")

        # Keep the database's asset count in sync (best-effort, matching the
        # API unarchive flow)
        try:
            update_asset_count(db_table_name, asset_table_name, {}, live_db_id)
        except Exception as e:
            logger.warning(f"Asset count update failed after restoring {asset_id}: {e}")

        # Record the auto-restore in asset history (best-effort)
        write_asset_history_record(
            live_db_id, asset_id, CHANGE_SOURCE_UNARCHIVE_DIRECT, 'SYSTEM_USER',
            build_asset_snapshot(restored, unarchived_reason=restored.get('unarchivedReason'))
        )

        return live_db_id
    except Exception as e:
        logger.exception(f"Error restoring archived asset {asset_id}: {e}")
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
            tags=[]
        )
        
        # Create the asset
        # Note: We're passing an empty dict for claims_and_roles since this is a system operation
        response = create_asset(request_model, {"tokens": ["SYSTEM_USER"]}, True)
        
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

def decode_s3_event_key(raw_key: str) -> str:
    """Decode an S3 key from an S3 event notification.

    AWS documents that S3 event notifications URL-encode object keys using
    form encoding (spaces → '+', other specials → '%XX'). The canonical
    AWS SDK convention for reversing this is ``urllib.parse.unquote_plus``.

    However, literal '+' characters in real S3 keys are also common (VAMS
    file uploads include filenames like ``BACC66K41F158AM+---.CATPart``),
    and some event sources deliver the key without any encoding applied.
    Blindly calling ``unquote_plus`` on a literal ``+`` turns it into a
    space, which then 404s on ``head_object``.

    Strategy: return the ``unquote_plus`` form if it actually differs
    (indicating the event WAS encoded); otherwise return the raw key.
    Callers that make an S3 API call with this key should additionally
    fall back to the raw key if the decoded-first attempt 404s — see
    ``update_s3_metadata`` for the template.
    """
    if not raw_key:
        return raw_key
    decoded = urllib.parse.unquote_plus(raw_key)
    return decoded if decoded != raw_key else raw_key


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
    # S3 event keys may arrive form-encoded ('+' for space, %XX for other
    # specials) or as literal text depending on the source. Try the key
    # as-given first; on 404, try the alternative (decoded <-> raw) so we
    # tolerate both shapes. This prevents uploads with special characters
    # like '+' in the filename (e.g., BACC66K41F158AM+---.CATPart) from
    # failing when the event pipeline delivered one shape but the object
    # was stored with the other.
    def _head_with_fallback():
        try:
            return s3_client.head_object(Bucket=bucket_name, Key=object_key), object_key
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") not in ("404", "NoSuchKey"):
                raise
            # 404 path — try the alternative encoding
            alt_key = urllib.parse.unquote_plus(object_key)
            if alt_key == object_key:
                # Try re-encoding instead (raw key that should have been encoded)
                alt_key = urllib.parse.quote(object_key, safe="/+")
            if alt_key == object_key:
                raise  # nothing else to try
            logger.info(
                f"head_object 404 for key {object_key!r}; retrying with "
                f"alternative encoding {alt_key!r}"
            )
            return s3_client.head_object(Bucket=bucket_name, Key=alt_key), alt_key

    try:
        response, effective_key = _head_with_fallback()

        # Check if metadata already matches
        current_metadata = response.get('Metadata', {})

        # Keep a change source already stamped by a VAMS action; objects arriving
        # without one were changed outside VAMS, so stamp "direct".
        desired_change_source = current_metadata.get(VAMS_CHANGE_SOURCE_METADATA_KEY) or VAMS_CHANGE_SOURCE_DIRECT

        if (current_metadata.get(DATABASE_ID_METADATA_KEY) == database_id
                and current_metadata.get(ASSET_ID_METADATA_KEY) == asset_id
                and current_metadata.get(VAMS_CHANGE_SOURCE_METADATA_KEY) == desired_change_source):
            logger.info(f"Metadata already matches for {effective_key}")
            return True

        # Copy the object to itself with updated metadata
        metadata = {
            **current_metadata,
            DATABASE_ID_METADATA_KEY: database_id,
            ASSET_ID_METADATA_KEY: asset_id,
            VAMS_CHANGE_SOURCE_METADATA_KEY: desired_change_source,
        }

        # Use boto3 resource copy() which automatically handles multipart for large files
        copy_source = {
            'Bucket': bucket_name,
            'Key': effective_key
        }
        s3_resource.Object(bucket_name, effective_key).copy(
            copy_source,
            ExtraArgs={
                'Metadata': metadata,
                'MetadataDirective': 'REPLACE'
            }
        )

        logger.info(f"Updated metadata for {effective_key}")
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

    #ignore prefix if we don't have one or process by removing path start
    if not prefix or prefix == '' or prefix == '/':
        # The asset ID is the first part of the path
        parts = object_key.split('/')
        if parts:
            return parts[0]
    else:
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


def extract_relative_file_path(object_key: str, prefix: str, asset_id: str) -> str:
    """
    Extract the relative file path from S3 object key
    
    This removes the prefix and asset_id to get the file path relative to the asset base.
    
    Args:
        object_key: The S3 object key (e.g., "prefix/assetId/folder/file.txt")
        prefix: The base prefix (e.g., "prefix/")
        asset_id: The asset ID (e.g., "assetId")
        
    Returns:
        str: Relative file path (e.g., "folder/file.txt")
    """
    # Normalize prefix
    if prefix and prefix != '/' and prefix != '':
        if not prefix.endswith('/'):
            prefix = prefix + '/'
    else:
        prefix = ''
    
    # Remove prefix from object key
    if prefix and object_key.startswith(prefix):
        path_after_prefix = object_key[len(prefix):]
    else:
        path_after_prefix = object_key
    
    # Remove asset_id and the following slash
    asset_prefix = f"{asset_id}/"
    if path_after_prefix.startswith(asset_prefix):
        relative_path = path_after_prefix[len(asset_prefix):]
        return relative_path
    
    # If we can't extract properly, return the path after prefix
    return path_after_prefix


def delete_file_metadata_on_s3_delete(database_id: str, asset_id: str, relative_file_path: str):
    """Delete metadata and attributes when file is deleted directly in S3

    Args:
        database_id: The database ID
        asset_id: The asset ID
        relative_file_path: The relative file path (without asset base prefix)
    """
    try:
        # Skip if relative path is empty
        if not relative_file_path:
            logger.info("Skipping metadata deletion for empty relative path")
            return

        # Skip if this is a folder (ends with /)
        if relative_file_path.endswith('/'):
            logger.info(f"Skipping metadata deletion for folder: {relative_file_path}")
            return

        # Construct the composite key for new metadata tables. Metadata and
        # attribute rows key file paths asset-relative with exactly one leading
        # slash (see backend Rule 13): {databaseId}:{assetId}:/{relative_file_path}
        relative_file_path = "/" + relative_file_path.lstrip("/")
        composite_key = f"{database_id}:{asset_id}:{relative_file_path}"
        
        deleted_metadata_count = 0
        deleted_attribute_count = 0
        
        # Delete from asset_file_metadata_table (file metadata)
        if asset_file_metadata_table:
            try:
                # Query all metadata for this file using GSI
                response = asset_file_metadata_table.query(
                    IndexName='DatabaseIdAssetIdFilePathIndex',
                    KeyConditionExpression=Key('databaseId:assetId:filePath').eq(composite_key)
                )
                
                # Delete all metadata items
                for item in response.get('Items', []):
                    asset_file_metadata_table.delete_item(
                        Key={
                            'metadataKey': item['metadataKey'],
                            'databaseId:assetId:filePath': composite_key
                        }
                    )
                    deleted_metadata_count += 1
                
                if deleted_metadata_count > 0:
                    logger.info(f"Deleted {deleted_metadata_count} metadata items for file {composite_key}")
                    
            except Exception as e:
                logger.warning(f"Error deleting file metadata: {e}")
        
        # Delete from file_attribute_table (file attributes)
        if file_attribute_table:
            try:
                # Query all attributes for this file using GSI
                response = file_attribute_table.query(
                    IndexName='DatabaseIdAssetIdFilePathIndex',
                    KeyConditionExpression=Key('databaseId:assetId:filePath').eq(composite_key)
                )
                
                # Delete all attribute items
                for item in response.get('Items', []):
                    file_attribute_table.delete_item(
                        Key={
                            'attributeKey': item['attributeKey'],
                            'databaseId:assetId:filePath': composite_key
                        }
                    )
                    deleted_attribute_count += 1
                
                if deleted_attribute_count > 0:
                    logger.info(f"Deleted {deleted_attribute_count} attribute items for file {composite_key}")
                    
            except Exception as e:
                logger.warning(f"Error deleting file attributes: {e}")
        
        if deleted_metadata_count == 0 and deleted_attribute_count == 0:
            logger.info(f"No metadata or attributes found for file {composite_key}")
        
    except Exception as e:
        logger.exception(f"Error deleting metadata for file on S3 delete: {e}")
        # Don't fail the whole operation if metadata deletion fails
        pass

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
        raise Exception(f"Error verifying database.")

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
        raise Exception(f"Error verifying asset.")

def build_filtered_event(original_event, successful_s3_records):
    """
    Build a filtered event that preserves the original structure
    but only includes successfully processed S3 records.
    
    Args:
        original_event: The original event (SQS, SNS, or direct S3)
        successful_s3_records: List of S3 records that were successfully processed
        
    Returns:
        dict: Filtered event with same structure as original, or None if no records
    """
    if not successful_s3_records:
        return None
    
    try:
        # Check if this is a direct S3 event
        if 'Records' in original_event and original_event['Records'] and 'eventSource' in original_event['Records'][0] and original_event['Records'][0]['eventSource'] == 'aws:s3':
            logger.info("Building filtered direct S3 event")
            return {
                'Records': successful_s3_records
            }
        
        # Check if this is an SQS event
        if 'Records' in original_event and original_event['Records'] and 'eventSource' in original_event['Records'][0] and original_event['Records'][0]['eventSource'] == 'aws:sqs':
            logger.info("Building filtered SQS event")
            filtered_sqs_records = []
            
            for sqs_record in original_event['Records']:
                if not sqs_record.get('body'):
                    continue
                
                try:
                    parsed_body = json.loads(sqs_record['body'])
                    
                    # Check if this is an SNS message
                    if 'Message' in parsed_body:
                        try:
                            message = json.loads(parsed_body['Message'])
                            if 'Records' in message:
                                # Filter S3 records in the SNS message
                                filtered_s3_records = [r for r in message['Records'] if r in successful_s3_records]
                                if filtered_s3_records:
                                    message['Records'] = filtered_s3_records
                                    parsed_body['Message'] = json.dumps(message)
                                    sqs_record_copy = sqs_record.copy()
                                    sqs_record_copy['body'] = json.dumps(parsed_body)
                                    filtered_sqs_records.append(sqs_record_copy)
                        except json.JSONDecodeError:
                            # If Message is not valid JSON, keep original if any successful records
                            if successful_s3_records:
                                filtered_sqs_records.append(sqs_record)
                    elif 'Records' in parsed_body:
                        # Direct S3 event in SQS body
                        filtered_s3_records = [r for r in parsed_body['Records'] if r in successful_s3_records]
                        if filtered_s3_records:
                            parsed_body['Records'] = filtered_s3_records
                            sqs_record_copy = sqs_record.copy()
                            sqs_record_copy['body'] = json.dumps(parsed_body)
                            filtered_sqs_records.append(sqs_record_copy)
                except json.JSONDecodeError:
                    logger.warning("Could not parse SQS body, skipping record")
                    continue
            
            if filtered_sqs_records:
                return {
                    'Records': filtered_sqs_records
                }
            return None
        
        # Check if this is an SNS event
        if 'Records' in original_event and original_event['Records'] and 'EventSource' in original_event['Records'][0] and original_event['Records'][0]['EventSource'] == 'aws:sns':
            logger.info("Building filtered SNS event")
            filtered_sns_records = []
            
            for sns_record in original_event['Records']:
                if not sns_record.get('Sns') or not sns_record['Sns'].get('Message'):
                    continue
                
                try:
                    message = json.loads(sns_record['Sns']['Message'])
                    if 'Records' in message:
                        # Filter S3 records in the SNS message
                        filtered_s3_records = [r for r in message['Records'] if r in successful_s3_records]
                        if filtered_s3_records:
                            message['Records'] = filtered_s3_records
                            sns_record_copy = sns_record.copy()
                            sns_record_copy['Sns'] = sns_record['Sns'].copy()
                            sns_record_copy['Sns']['Message'] = json.dumps(message)
                            filtered_sns_records.append(sns_record_copy)
                except json.JSONDecodeError:
                    logger.warning("Could not parse SNS message, skipping record")
                    continue
            
            if filtered_sns_records:
                return {
                    'Records': filtered_sns_records
                }
            return None
        
        # If we can't determine the event type, return None
        logger.warning("Could not determine event type for filtering, returning None")
        return None
    except Exception as e:
        logger.exception(f"Error building filtered event: {e}")
        return None

def publish_to_file_indexer_sns(event):
    """
    Publish S3 event to file indexer SNS topic for downstream processing.
    
    Args:
        event: The S3 event to publish
    """
    try:
        if not file_indexer_sns_topic_arn:
            logger.warning("FILE_INDEXER_SNS_TOPIC_ARN not configured, skipping SNS publish")
            return
        
        # Prepare payload for indexing
        event.update({
            "ASSET_BUCKET_NAME": asset_bucket_name,
            "ASSET_BUCKET_PREFIX": asset_bucket_prefix
        })
        
        # Publish to SNS topic
        response = sns_client.publish(
            TopicArn=file_indexer_sns_topic_arn,
            Message=json.dumps(event, default=str),
            Subject='S3 Bucket Sync Event'
        )
        
        logger.info(f"Successfully published to file indexer SNS topic: {response['MessageId']}")
    except Exception as e:
        logger.exception(f"Error publishing to file indexer SNS topic: {e}")
        # We don't re-raise the exception here to avoid stopping the process

def publish_to_orchestration_bus(successful_records):
    """Publish an asset.file.uploaded event to the VAMS orchestration EventBridge bus (Phase 2
    fileUpload trigger delivery). A standing rule on the bus routes these to the SQS buffer the
    workflowTriggerDispatch lambda consumes.

    Publishes a CLEAN, flat detail — {"Records": [{"s3": {...}}], ASSET_BUCKET_*} — built from the
    already-unwrapped S3 records, so the dispatcher does not re-implement the SQS->SNS->S3 unwrapping.
    Every S3 record is published, including workflow-written ones: the re-trigger decision belongs to
    the dispatcher, which knows the candidate workflow (see the loop-guard note in the body).

    Best-effort: a publish failure is logged, not raised (auto-trigger is non-critical to the primary
    ingestion path)."""
    try:
        if not orchestration_bus_name or not orchestration_event_source_prefix:
            return
        # Every S3 record is published, including workflow-written ones. The re-trigger decision is
        # made per candidate WORKFLOW by the dispatcher (which already reads the object's provenance
        # metadata), because it depends on that workflow's allowWorkflowTriggerChaining and on whether
        # the file came from that same workflow. Filtering here would drop the record before any
        # workflow is known, making a per-workflow opt-in unreachable.
        publishable_records = [r for r in (successful_records or []) if r.get("s3")]
        if not publishable_records:
            return
        detail = {
            "Records": publishable_records,
            "ASSET_BUCKET_NAME": asset_bucket_name,
            "ASSET_BUCKET_PREFIX": asset_bucket_prefix,
        }
        try:
            events_client.put_events(Entries=[{
                "EventBusName": orchestration_bus_name,
                "Source": f"{orchestration_event_source_prefix}.trigger.fileUpload",
                "DetailType": "asset.file.uploaded",
                "Detail": json.dumps(detail, default=str),
            }])
        except Exception as e:
            logger.exception(f"EventBridge put_events failed for asset.file.uploaded on bus {orchestration_bus_name}: {e}")
            return
        logger.info(f"Published asset.file.uploaded event ({len(publishable_records)} record(s)) to the orchestration bus")
    except Exception as e:
        logger.exception(f"Error publishing to orchestration bus: {e}")

def build_history_record(database_id, asset_id, relative_file_path, version_id,
                         s3_metadata, s3_last_modified):
    """Build an asset-file change-history item from an ingested object's S3 metadata.

    No vams-changesource present means the file was changed outside VAMS (e.g. a
    direct S3 upload): record changeSource="direct" with no provenance columns.
    Otherwise map the vams-change* metadata to flat columns (skipping blanks).
    """
    change_source = s3_metadata.get(VAMS_CHANGE_SOURCE_METADATA_KEY) or VAMS_CHANGE_SOURCE_DIRECT
    relative_file_path = normalize_history_file_path(relative_file_path)
    record = {
        "databaseId:assetId:filePath": f"{database_id}:{asset_id}:{relative_file_path}",
        "versionId": version_id or "null",
        "databaseId:assetId": f"{database_id}:{asset_id}",
        "databaseId": database_id,
        "assetId": asset_id,
        "filePath": relative_file_path,
        "changeSource": change_source,
        "recordCreated": datetime.utcnow().isoformat() + "Z",
        "s3LastModified": s3_last_modified or "",
    }
    if change_source != VAMS_CHANGE_SOURCE_DIRECT:
        col_map = {
            VAMS_CHANGE_USER_ID_METADATA_KEY: "changeUserId",
            VAMS_CHANGE_WORKFLOW_ID_METADATA_KEY: "changeWorkflowId",
            VAMS_CHANGE_WORKFLOW_EXECUTION_ID_METADATA_KEY: "changeWorkflowExecutionId",
            VAMS_CHANGE_ASSET_ID_FROM_METADATA_KEY: "changeAssetIdFrom",
            VAMS_CHANGE_DATABASE_ID_FROM_METADATA_KEY: "changeDatabaseIdFrom",
            VAMS_CHANGE_ASSET_FILE_PATH_FROM_METADATA_KEY: "changeAssetFilePathFrom",
            VAMS_CHANGE_ASSET_FILE_VERSION_FROM_METADATA_KEY: "changeAssetFileVersionFrom",
        }
        for meta_key, col in col_map.items():
            val = s3_metadata.get(meta_key)
            if val:
                record[col] = val
    return record


def write_file_version_history(database_id, asset_id, relative_file_path, version_id,
                               s3_metadata, s3_last_modified):
    """Write a single change-history record for an ingested object version.

    Best-effort: no-op when the table is not configured, and never raises -- a
    history-write failure must not break the bucket sync / indexing flow. The
    put_item overwrites the same PK+SK (last-write-wins, idempotent on re-sync).
    """
    if not asset_file_version_history_table:
        return
    try:
        record = build_history_record(
            database_id, asset_id, relative_file_path, version_id,
            s3_metadata, s3_last_modified,
        )
        asset_file_version_history_table.put_item(Item=record)
    except Exception as e:
        logger.exception(f"Failed writing file version history for {asset_id}/{relative_file_path}: {e}")


def process_s3_record(record: Dict) -> Tuple[bool, bool, str]:
    """
    Process a single S3 record
    
    This function implements the core business logic for processing S3 events:
    *. Validates bucket and prefix against environment variables
    *. Checks if bucket and prefix have a record in S3 asset buckets table
    *. Skips special folders (temp-uploads, preview, pipeline, etc.)
    *. Validates asset ID format
    *. Looks up or creates assets/databases as needed
    *. Handles "init" files by deleting them
    *. Updates S3 metadata with database and asset IDs
    *. Detects folder markers (keys ending with '/') and skips indexing
    
    Args:
        record: The S3 record to process
        
    Returns:
        tuple: (processing_success, should_index, message) where:
            - processing_success: boolean indicating if processing was successful
            - should_index: boolean indicating if record should be sent to indexer.
              Independent of processing_success: benign skips and most processing
              failures still forward to the indexers so OpenSearch and other
              registered indexers can reconcile their own records. Only records
              that are truly out of scope (wrong bucket/prefix, reserved folders,
              init files, folder markers, malformed records) are withheld.
            - message: string with details about the processing result
    """
    try:
        # Validate record has S3 information
        if not record.get('s3'):
            return False, False, "Record does not contain S3 information"

        # Extract bucket name and object key. S3 event notifications deliver
        # the key URL-encoded per AWS spec (spaces → '+', specials → '%XX');
        # decode it here so downstream comparisons, prefix checks, and S3
        # API calls operate on the actual object key. The update_s3_metadata
        # helper has an additional fallback to the raw form in case this
        # particular event source delivered the key literally.
        bucket_name = record['s3']['bucket']['name']
        raw_object_key = record['s3']['object']['key']
        object_key = decode_s3_event_key(raw_object_key)

        if object_key != raw_object_key:
            logger.info(
                f"Processing S3 record for bucket {bucket_name}, "
                f"key {object_key} (decoded from {raw_object_key})"
            )
        else:
            logger.info(
                f"Processing S3 record for bucket {bucket_name}, key {object_key}"
            )

        #Copy prefix
        prefix = asset_bucket_prefix
        
        #Make sure prefix doesn't start with a '/'. 
        if prefix and prefix != '/':
            prefix = prefix.lstrip('/')
        
        # 1.a. Check if record bucket and base prefix matches the environment variables
        if asset_bucket_name and bucket_name != asset_bucket_name:
            logger.info(f"Bucket {bucket_name} does not match configured bucket {asset_bucket_name}, skipping")
            return False, False, f"Bucket {bucket_name} does not match configured bucket"
        
        #Note: if '/' given, treat this as no prefix
        if prefix and prefix != '/' and not object_key.startswith(prefix):
            logger.info(f"Object key {object_key} does not start with configured prefix {prefix}, skipping")
            return False, False, f"Object key does not start with configured prefix"
        
        # Use the configured prefix or empty string
        prefix = prefix or ""
        
        # 1.b Check if bucket name and prefix have a record in the S3 asset buckets table
        # Use cache to prevent excessive lookups (TTL: 60 seconds)
        # A missing bucket record (registration race / transient lookup failure) skips
        # VAMS-side processing but still forwards to the indexers — the file indexer
        # resolves bucket details independently via the object's S3 metadata.
        bucket_id = get_bucket_id(bucket_name, prefix)
        if not bucket_id:
            logger.info(f"No bucket ID found for {bucket_name} with prefix {prefix}, skipping processing but forwarding to indexers")
            return False, True, f"No bucket ID found for {bucket_name} with prefix {prefix}"
        
        # Extract asset ID from the object key. Failure to extract or validate an
        # asset ID skips VAMS-side processing but still forwards to the indexers,
        # which resolve asset/database IDs independently via S3 object metadata.
        asset_id = extract_asset_id_from_key(object_key, prefix)
        if not asset_id:
            logger.info(f"Could not extract asset ID from {object_key}, skipping processing but forwarding to indexers")
            return False, True, f"Could not extract asset ID from {object_key}"

        # 1.c Check if asset ID is a special folder to skip
        if asset_id in RESERVED_S3_PREFIX_FOLDERS:
            logger.info(f"Asset ID {asset_id} is a special folder, skipping")
            return False, False, f"Asset ID {asset_id} is a special folder"

        # 1.d Validate asset ID
        if not validate_asset_id(asset_id):
            logger.info(f"Asset ID {asset_id} is not valid, skipping processing but forwarding to indexers")
            return False, True, f"Asset ID {asset_id} is not valid"
        
        # 2.a. Lookup asset in assets dynamoDB table
        # Use cache to prevent excessive lookups (TTL: 60 seconds)
        asset_data = lookup_asset(bucket_id, asset_id)
        database_id_to_use = None
        
        if asset_data:
            logger.info(f"Asset {asset_id} found in bucket {bucket_id}")
            database_id_to_use = asset_data.get('databaseId')
        else:
            # Check if asset is archived before creating new one. A new file
            # placed directly in S3 under an archived asset's prefix restores
            # the asset record (DynamoDB-only unarchive): the asset becomes
            # active again but previously archived files keep their delete
            # markers — the files present under the prefix are the asset's
            # files, and users unarchive individual old files via the API.
            archived_asset = lookup_archived_asset(bucket_id, asset_id)
            if archived_asset:
                if not object_still_exists(bucket_name, object_key):
                    logger.info(
                        f"Object {object_key} no longer exists; not restoring archived asset {asset_id}"
                    )
                    return True, True, f"Skipped stale create event for archived asset {asset_id}"
                restored_database_id = restore_archived_asset(bucket_id, asset_id, archived_asset)
                if not restored_database_id:
                    logger.error(f"Failed to restore archived asset {asset_id}")
                    return False, True, f"Failed to restore archived asset {asset_id}"
                logger.info(
                    f"Restored archived asset {asset_id} to database {restored_database_id} "
                    f"after direct S3 upload of {object_key}"
                )
                database_id_to_use = restored_database_id
            else:
                # Get or create database for this bucket/prefix
                database_id_to_use = get_or_create_database_for_bucket(bucket_id, bucket_name, prefix)

                if not database_id_to_use:
                    logger.error(f"Could not get or create database for bucket {bucket_id} (may be archived)")
                    return False, True, f"Could not get or create database for bucket {bucket_id}"

                # Guard against recreating a ghost asset from a stale / redelivered
                # ObjectCreated event: if the object no longer exists (deleted since the
                # event was enqueued), do not recreate the asset. A genuinely new file
                # still exists here and proceeds to creation as intended. The record is
                # still forwarded to the indexers (should_index=True) so OpenSearch and
                # other registered indexers can reconcile their records for the file.
                if not object_still_exists(bucket_name, object_key):
                    logger.info(
                        f"Object {object_key} no longer exists; skipping asset (re)creation for {asset_id} "
                        "but forwarding event to indexers"
                    )
                    return True, True, f"Skipped stale create event for {object_key}"

                # Create the asset
                logger.info(f"Creating new asset {asset_id} in database {database_id_to_use}")
                created_asset_id = create_new_asset(bucket_id, database_id_to_use, asset_id)
                if not created_asset_id:
                    logger.error(f"Failed to create asset {asset_id} in database {database_id_to_use}")
                    return False, True, f"Failed to create asset {asset_id} in database {database_id_to_use}"
        
        # 3. Check if the object key ends with "init" - If so delete and skip indexing
        if object_key.endswith('init') or object_key.endswith('init/'):
            # Check if versioning is enabled
            versioning_enabled = is_versioning_enabled(bucket_id)
            
            # Delete the init object
            logger.info(f"Deleting init object {object_key}")
            delete_result = delete_s3_object(bucket_name, object_key, versioning_enabled)
            if not delete_result:
                logger.error(f"Failed to delete init object {object_key}")
                return False, False, f"Failed to delete init object {object_key}"
            
            # Processed successfully but skip indexing
            return True, False, f"Deleted init object {object_key}"
        
        # 4. Check if file has S3 metadata attributes that match databaseid and assetid
        update_result = update_s3_metadata(bucket_name, object_key, database_id_to_use, asset_id)
        if not update_result:
            logger.error(f"Failed to update metadata for {object_key}")
            return False, True, f"Failed to update metadata for {object_key}"
        
        # 5. Update asset type based on all files in the bucket
        # Construct the asset base key (prefix + assetId + /)
        asset_base_key = f"{prefix}{asset_id}/" if prefix and prefix != '/' else f"{asset_id}/"
        update_asset_type(bucket_id, asset_id, bucket_name, asset_base_key)

        # Record file version change history (best-effort; never fails the sync).
        # Skip folder markers (no version/content). head_object returns 'null' for
        # VersionId on buckets without versioning enabled.
        if not object_key.endswith('/'):
            try:
                head = s3_client.head_object(Bucket=bucket_name, Key=object_key)
                last_modified = head.get('LastModified')
                write_file_version_history(
                    database_id_to_use,
                    asset_id,
                    extract_relative_file_path(object_key, prefix, asset_id),
                    head.get('VersionId', 'null'),
                    head.get('Metadata', {}) or {},
                    last_modified.isoformat() if last_modified else "",
                )
            except Exception as e:
                logger.exception(f"History write skipped for {object_key}: {e}")

        # 6. Check if object key ends with '/' (folder marker) - process but skip indexing
        if object_key.endswith('/'):
            logger.info(f"Folder marker detected: {object_key}, processing but skipping indexing")
            return True, False, f"Processed folder marker {object_key}"
        
        return True, True, f"Successfully processed {object_key}"
    except Exception as e:
        logger.exception(f"Error processing S3 record: {e}")
        # Unexpected failure mid-processing: still forward to the indexers so
        # OpenSearch and other registered indexers can reconcile independently.
        return False, True, f"Error processing S3 record."

def on_storage_event_created(event):
    """
    Process S3 storage events for created files
    
    This function handles S3 events for file creation, implementing the following process:
    1. Validates bucket and prefix against environment variables
    2. Checks if bucket and prefix have a record in S3 asset buckets table
    3. Skips special folders (temp-uploads, preview, pipeline)
    4. Validates asset ID format
    5. Looks up or creates assets/databases as needed
    6. Handles "init" files by deleting them
    7. Updates S3 metadata with database and asset IDs
    
    Args:
        event: The S3 event containing records to process
        
    Returns:
        tuple: (success_status, successful_records) where success_status is a boolean
               indicating if processing completed without hard errors, and successful_records
               is a list of records that were successfully processed
    """
    logger.info(f"Processing storage event: {json.dumps(event)}")
    
    success_count = 0
    error_count = 0
    skip_count = 0
    successful_records = []
    
    # Process each record in the event
    for record in event.get('Records', []):
        # Skip records without S3 information
        if not record.get('s3'):
            logger.warning("Record does not contain S3 information, skipping")
            skip_count += 1
            continue
            
        # Handle records with S3 information
        try:
            # Process the S3 record - now returns (success, should_index, message)
            success, should_index, message = process_s3_record(record)

            # Forward to the indexers whenever should_index is True — even when
            # VAMS-side processing failed or was skipped — so OpenSearch and other
            # registered indexers can reconcile their records independently.
            if should_index:
                successful_records.append(record)
                logger.info(f"Record queued for indexing: {message}")

            # Track success/failure counts
            if success:
                success_count += 1
                if not should_index:
                    logger.info(f"Successfully processed record but skipping indexing: {message}")
            else:
                # Classify a non-success result as a skip (benign, expected) vs
                # an error (something that actually failed mid-action). Skips
                # are events we intentionally decline to process — wrong
                # bucket/prefix, special folders like temp-uploads, invalid
                # asset IDs, etc. Errors are downstream action failures like
                # "Failed to ..." or "Could not get or create ...".
                #
                # Note: the message comes from process_s3_record above. None
                # of its benign-skip messages naturally contain the word
                # "skipping", so the older substring-based check was
                # miscategorizing every skip as an error. We now match against
                # the error-prefix set explicitly and default to skip.
                _error_prefixes = (
                    "Record does not contain S3 information",
                    "Failed to",
                    "Could not get or create",
                    "Error processing",
                )
                if any(message.startswith(p) for p in _error_prefixes):
                    error_count += 1
                    logger.error(f"Error processing record: {message}")
                else:
                    skip_count += 1
                    logger.info(f"Skipped record: {message}")
        except Exception as e:
            # Catch any unexpected exceptions during record processing
            error_count += 1
            logger.exception(f"Unexpected error processing record: {e}")
    
    # Log summary of processing results
    logger.info(f"Processed {len(event.get('Records', []))} records: {success_count} successful, {error_count} errors, {skip_count} skipped")
    
    # Return success status and list of successful records
    return (error_count == 0 or success_count > 0), successful_records

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
                    logger.warning("SQS record missing body field, skipping")
                    continue
                
                try:
                    parsed_body = json.loads(record['body'])
                    
                    # Check if this is an SNS message
                    if 'Message' in parsed_body:
                        try:
                            # Try to parse the Message field as JSON
                            message = json.loads(parsed_body['Message'])
                            if 'Records' in message:
                                s3_events.append(message)
                        except json.JSONDecodeError as e:
                            # Handle case where Message is not valid JSON
                            logger.warning(f"Message field is not valid JSON: {e}. Message content: {parsed_body['Message']}")
                            # If Message contains S3 event data in a non-standard format, try to extract it
                            if 's3' in parsed_body['Message'] or 'bucket' in parsed_body['Message']:
                                logger.info("Attempting to process Message as raw S3 event data")
                                # Create a placeholder event with the raw message for further processing
                                s3_events.append({
                                    'Records': [{
                                        'eventSource': 'aws:s3',
                                        'rawMessage': parsed_body['Message']
                                    }]
                                })
                    elif 'Records' in parsed_body:
                        s3_events.append(parsed_body)
                except json.JSONDecodeError as e:
                    logger.exception(f"Error parsing SQS record body as JSON: {e}")
                except Exception as e:
                    logger.exception(f"Unexpected error parsing SQS record: {e}")
            
            if s3_events:
                # Combine all S3 events into a single event
                combined_event = {'Records': []}
                for event in s3_events:
                    if 'Records' in event:
                        combined_event['Records'].extend(event['Records'])
                
                return combined_event
        
        # Check if this is an SNS event
        if 'Records' in event and event['Records'] and 'EventSource' in event['Records'][0] and event['Records'][0]['EventSource'] == 'aws:sns':
            logger.info("Detected SNS event")
            s3_events = []
            
            for record in event['Records']:
                if not record.get('Sns') or not record['Sns'].get('Message'):
                    logger.warning("SNS record missing Sns.Message field, skipping")
                    continue
                
                try:
                    message = json.loads(record['Sns']['Message'])
                    if 'Records' in message:
                        s3_events.append(message)
                except json.JSONDecodeError as e:
                    logger.warning(f"SNS Message field is not valid JSON: {e}. Message content: {record['Sns']['Message']}")
                    # If Message contains S3 event data in a non-standard format, try to extract it
                    if 's3' in record['Sns']['Message'] or 'bucket' in record['Sns']['Message']:
                        logger.info("Attempting to process SNS Message as raw S3 event data")
                        # Create a placeholder event with the raw message for further processing
                        s3_events.append({
                            'Records': [{
                                'eventSource': 'aws:s3',
                                'rawMessage': record['Sns']['Message']
                            }]
                        })
                except Exception as e:
                    logger.exception(f"Unexpected error parsing SNS message: {e}")
            
            if s3_events:
                # Combine all S3 events into a single event
                combined_event = {'Records': []}
                for event in s3_events:
                    if 'Records' in event:
                        combined_event['Records'].extend(event['Records'])
                
                return combined_event
    except Exception as e:
        logger.exception(f"Error parsing event: {e}")
    
    # Return the original event if we couldn't parse it
    logger.warning("Could not parse event into a standard format, returning original event")
    return event

def lambda_handler_created(event, context):
    """
    Handler for file creation events from SQS
    
    This function is the main entry point for processing file creation events.
    It parses the event from different sources (SQS, SNS, direct S3),
    processes the storage event, and runs the OpenSearch indexing lambda for
    every record flagged for indexing — including records whose VAMS-side
    processing failed or was skipped, so OpenSearch and other registered
    indexers can reconcile their records independently. Only truly
    out-of-scope records (wrong bucket/prefix, reserved folders, init files,
    folder markers, malformed records) are withheld from the indexers.

    Args:
        event: The event from the event source (SQS, SNS, or direct S3)
        context: The Lambda context

    Returns:
        None
    """
    logger.info(f"File creation event received: {json.dumps(event)}")

    try:
        # Parse the event to handle different sources
        parsed_event = parse_event(event)

        # Process the storage event if it contains records
        if parsed_event.get('Records'):
            # Process the storage event and get success status and indexable records
            success, successful_records = on_storage_event_created(parsed_event)

            if not success:
                logger.warning("Hard errors encountered during storage event processing")

            # Publish every record flagged for indexing, regardless of hard errors
            # elsewhere in the batch — withholding them would leave OpenSearch and
            # other registered indexers out of sync.
            if successful_records:
                # Build filtered event preserving original structure
                filtered_event = build_filtered_event(event, successful_records)

                if filtered_event:
                    logger.info(f"Publishing {len(successful_records)} records to file indexer SNS")
                    publish_to_file_indexer_sns(filtered_event)

                    # Publish to the orchestration EventBridge bus for the fileUpload trigger
                    # dispatcher. Pass the flat S3 records so a clean detail is published
                    # (workflow-sourced outputs are excluded).
                    publish_to_orchestration_bus(successful_records)
                else:
                    logger.info("All records filtered out, skipping file indexer SNS publish")
            else:
                logger.info("No records to publish, skipping file indexer SNS publish")
        else:
            logger.warning("No records found in parsed event, nothing to process")
    except Exception as e:
        logger.exception(f"Unhandled error in lambda_handler_created: {e}")
        # We don't run the indexing lambda on unhandled exceptions to avoid potential data corruption

def lambda_handler_deleted(event, context):
    """
    Handler for file deleted events from SQS
    
    This function is the entry point for processing file deletion events.
    For deletions, we update the asset type if the file is not a folder marker,
    then run the OpenSearch indexing lambda to update the search index.

    Records are forwarded to the file indexer even when VAMS-side processing is
    skipped or fails (e.g. the asset record or file is already gone) — the
    indexers must still see the delete so OpenSearch and other registered
    indexers can remove their records. Only truly out-of-scope records (no S3
    info, init files, folder markers, reserved folders) are withheld.

    Args:
        event: The event from the event source (SQS, SNS, or direct S3)
        context: The Lambda context

    Returns:
        None
    """
    logger.info(f"File deletion event received: {json.dumps(event)}")

    try:
        # Parse the event to handle different sources
        parsed_event = parse_event(event)

        # Process records if present
        if parsed_event.get('Records'):
            indexable_records = []

            # Check each record for files that are not folder markers
            for record in parsed_event.get('Records', []):
                # Skip records without S3 information
                if not record.get('s3'):
                    logger.warning("Record does not contain S3 information, skipping")
                    continue

                # Extract bucket name and object key
                bucket_name = record['s3']['bucket']['name']
                object_key = record['s3']['object']['key']

                # Skip init files entirely (both processing and indexing)
                if object_key.endswith('init') or object_key.endswith('init/'):
                    logger.info(f"Skipping init file: {object_key}")
                    continue

                # Skip folder markers (objects ending with '/')
                if object_key.endswith('/'):
                    logger.info(f"Skipping folder marker: {object_key}")
                    continue

                # VAMS-side cleanup (metadata deletion, asset type update) is
                # best-effort per record: any skip or failure below still forwards
                # the record to the indexers.
                try:
                    # Copy prefix
                    prefix = asset_bucket_prefix

                    # Make sure prefix doesn't start with a '/'
                    if prefix and prefix != '/':
                        prefix = prefix.lstrip('/')

                    # Use the configured prefix or empty string
                    prefix = prefix or ""

                    # Get bucket ID
                    bucket_id = get_bucket_id(bucket_name, prefix)
                    if not bucket_id:
                        logger.info(f"No bucket ID found for {bucket_name} with prefix {prefix}, skipping cleanup but forwarding to indexers")
                        indexable_records.append(record)
                        continue

                    # Extract asset ID from the object key
                    asset_id = extract_asset_id_from_key(object_key, prefix)
                    if not asset_id:
                        logger.info(f"Could not extract asset ID from {object_key}, skipping cleanup but forwarding to indexers")
                        indexable_records.append(record)
                        continue

                    # Skip special folders (never indexed)
                    if asset_id in RESERVED_S3_PREFIX_FOLDERS:
                        logger.info(f"Asset ID {asset_id} is a special folder, skipping")
                        continue

                    # Validate asset ID
                    if not validate_asset_id(asset_id):
                        logger.info(f"Asset ID {asset_id} is not valid, skipping cleanup but forwarding to indexers")
                        indexable_records.append(record)
                        continue

                    # Construct the asset base key (prefix + assetId + /)
                    asset_base_key = f"{prefix}{asset_id}/" if prefix and prefix != '/' else f"{asset_id}/"

                    # Get asset data to retrieve database_id for metadata deletion.
                    # The asset record may already be gone (asset-level delete) —
                    # the record is still forwarded so the indexers can clean up.
                    asset_data = lookup_asset(bucket_id, asset_id)
                    if asset_data:
                        database_id_for_asset = asset_data.get('databaseId')

                        # Extract relative file path for metadata deletion
                        relative_file_path = extract_relative_file_path(object_key, prefix, asset_id)

                        # Only delete metadata/attributes when the file is
                        # permanently gone (no versions or delete markers remain).
                        # A delete-marker event from an archive flow is reversible —
                        # unarchive restores the file and must find its metadata intact.
                        if is_object_permanently_deleted(bucket_name, object_key):
                            logger.info(f"Deleting metadata/attributes for permanently deleted file: {relative_file_path}")
                            delete_file_metadata_on_s3_delete(database_id_for_asset, asset_id, relative_file_path)
                        else:
                            logger.info(f"File {object_key} still has versions (archived); preserving metadata/attributes")
                    else:
                        logger.info(f"Asset {asset_id} not found in bucket {bucket_id}; skipping metadata cleanup")

                    # Update asset type based on remaining files
                    logger.info(f"Updating asset type for {asset_id} after file deletion")
                    update_asset_type(bucket_id, asset_id, bucket_name, asset_base_key)

                    logger.info(f"Successfully processed deletion for {object_key}")
                except Exception as e:
                    logger.exception(f"Error processing deletion record for {object_key}; forwarding to indexers anyway: {e}")

                # Forward the record to the indexers regardless of cleanup outcome
                indexable_records.append(record)

            # Only publish to SNS if we have records to index
            if indexable_records:
                # Build filtered event preserving original structure
                filtered_event = build_filtered_event(event, indexable_records)

                if filtered_event:
                    logger.info(f"Publishing {len(indexable_records)} deletion records to file indexer SNS")
                    publish_to_file_indexer_sns(filtered_event)
                else:
                    logger.info("All deletion records filtered out, skipping file indexer SNS publish")
            else:
                logger.info("No deletion records to publish, skipping file indexer SNS publish")
        else:
            logger.warning("No records found in parsed deletion event, nothing to process")
    except Exception as e:
        logger.exception(f"Error in lambda_handler_deleted: {e}")