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
from common.timestamps import utc_now
from customLogging.logger import safeLogger
from customLogging.auditLogging import audited_handler, current_audit_context, log_action
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
from common.eventLogSummary import event_log_summary
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
from common.s3PathPatterns import RESERVED_S3_PREFIX_FOLDERS, key_has_reserved_segment
from common.s3 import is_object_version_archived, region_routing_s3_client, region_routing_s3_resource
from common.assetHistory import (
    CHANGE_SOURCE_UNARCHIVE_DIRECT,
    build_asset_snapshot,
    write_asset_history_record,
)
from common.batchItemFailures import all_batch_item_failures, with_batch_item_failures
from common.assetLocations import (
    RESOLVED_AMBIGUOUS,
    RESOLVED_FOUND,
    LocationTooDeepError,
    contains,
    derive_location,
    find_conflicts,
    index_ready,
    is_folder_location,
    normalize_base_prefix,
    normalize_location,
    relative_path_in,
    resolve_owner,
)

# Initialize AWS clients
retry_config = BotoConfig(retries={'max_attempts': 5, 'mode': 'adaptive'})
dynamodb = boto3.resource('dynamodb', config=retry_config)
sns_client = boto3.client('sns', config=retry_config)
s3_client = region_routing_s3_client()
s3_resource = region_routing_s3_resource()
lambda_client = boto3.client('lambda', config=retry_config)
# Bounded timeouts on the EventBridge client so an unreachable endpoint (e.g. an isolated-subnet
# deployment missing the events VPC endpoint) fails fast rather than blocking the ingestion hot
# path for the full default connect timeout on every batch. The publish is best-effort.
events_client = boto3.client(
    'events',
    config=BotoConfig(connect_timeout=3, read_timeout=5, retries={'max_attempts': 2}))
dynamodb_client = boto3.client('dynamodb', config=retry_config)
sqs_client = boto3.client('sqs', config=retry_config)
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
    # The deployment's queue for records bucket sync cannot attribute by rule (two live assets on
    # one location). Optional: without it a rejected record is only logged.
    rejected_queue_url = os.environ.get("BUCKET_SYNC_REJECTED_QUEUE_URL", "")
except Exception as e:
    logger.exception("Failed loading environment variables or resolving resource names")
    raise e

if not database_id:
    raise Exception('databaseId not configured')

# This function's own name, used to recognize the ObjectCreated event that THIS handler's
# metadata stamp produces (see is_vams_metadata_stamp_echo). The Lambda runtime always sets it;
# an empty value simply disables the recognition, which degrades to the pre-existing behaviour of
# treating the echo as a content change rather than to anything unsafe.
self_function_name = os.environ.get('AWS_LAMBDA_FUNCTION_NAME', '')

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

# Cached in place of a database id when a bucket's auto-generated database is archived, so a repeated
# miss is served from the cache (None is what the cache returns for "not cached").
ARCHIVED_DATABASE_CACHE_VALUE = "#archived"

# How bucket sync found the asset a file belongs to: the first folder under the bucket's prefix
# (normal assets, and assets it auto-creates), or the location index (any other stored location).
RESOLVED_BY_FAST_PATH = "fastPath"
RESOLVED_BY_LOCATION_INDEX = "locationIndex"

# The additive field on each forwarded S3 record that carries bucket sync's answer to its consumers.
VAMS_BINDING_FIELD = "vamsBinding"

# Why a record went to the rejected-record queue.
REJECTED_REASON_AMBIGUOUS = "ambiguousLocation"

# A first-level auto-create whose folder already holds other objects is deferred once by this many
# seconds, so an asset created through the API a moment earlier reaches the location index first.
DEFERRED_AUTO_CREATE_VISIBILITY_SECONDS = 10

# Amazon SNS message and Amazon EventBridge entry size limits, less a margin for the envelope.
SNS_MESSAGE_MAX_BYTES = 256 * 1024
EVENTBRIDGE_ENTRY_MAX_BYTES = 256 * 1024
PUBLISH_SIZE_MARGIN_BYTES = 8 * 1024


class RecordRedrive(Exception):
    """A record whose SQS message must be received again: it is reported as a batch item failure,
    and its visibility timeout is set to `visibility_timeout` seconds when one is given."""

    def __init__(self, message: str, visibility_timeout: Optional[int] = None):
        super().__init__(message)
        self.visibility_timeout = visibility_timeout


def record_identity(record: Dict) -> str:
    """A stable key for one S3 record, equal for equal records (the event is parsed from JSON)."""
    return json.dumps(record, sort_keys=True, default=str)


def asset_location_of(asset_row: Optional[Dict], base_prefix: str, asset_id: str) -> str:
    """The asset's stored location (no leading `/`), or the folder derived from its id for a row
    that carries none."""
    stored = normalize_location(((asset_row or {}).get('assetLocation') or {}).get('Key'))
    return stored or derive_location({'baseAssetsPrefix': base_prefix}, '', asset_id)


def build_binding(database_id: str, asset_id: str, bucket_id: str, relative_path: str,
                  resolved_by: str) -> Dict[str, str]:
    """The `vamsBinding` bucket sync adds to a forwarded record: which asset holds the file and how
    that was decided. The database id is the live one, also for an archived asset."""
    live_database_id = (database_id or '')
    if live_database_id.endswith('#deleted'):
        live_database_id = live_database_id[:-len('#deleted')]
    return {
        'databaseId': live_database_id,
        'assetId': asset_id,
        'bucketId': bucket_id,
        'relativePath': relative_path,
        'resolvedBy': resolved_by,
    }


def with_binding(record: Dict, bindings: Optional[Dict[str, Dict]]) -> Dict:
    """The record with its `vamsBinding` added, or the record unchanged when it has none."""
    binding = (bindings or {}).get(record_identity(record))
    return {**record, VAMS_BINDING_FIELD: binding} if binding else record


def reject_record(record: Dict, bucket_name: str, object_key: str, reason: str,
                  candidates) -> Tuple[bool, bool, str]:
    """Log a record that cannot be attributed by rule and send it to the rejected-record queue.

    Nothing is attributed and nothing is forwarded. A send that fails raises RecordRedrive, so the
    record is received again rather than lost.
    """
    candidate_assets = [f"{c.database_id}:{c.asset_id}" for c in (candidates or ())]
    logger.error(f"Bucket sync rejected key {object_key} in bucket {bucket_name} ({reason}); "
                 f"candidate assets: {candidate_assets}")
    if not rejected_queue_url:
        logger.error("No rejected-record queue is configured; the rejected record is only logged")
        return True, False, f"Rejected {object_key}: {reason}"
    body = {
        'reason': reason,
        'bucket': bucket_name,
        'key': object_key,
        'candidateAssets': candidate_assets,
        'record': record,
    }
    try:
        sqs_client.send_message(QueueUrl=rejected_queue_url, MessageBody=json.dumps(body, default=str))
    except Exception as e:
        logger.exception(f"Could not send key {object_key} to the rejected-record queue: {e}")
        raise RecordRedrive(f"Could not send {object_key} to the rejected-record queue")
    return True, False, f"Rejected {object_key}: {reason}"


def auto_create_refusal(asset_table, bucket_name: str, location: str) -> Optional[str]:
    """Why an auto-created asset may not take `location`, or None when the location index shows no
    asset sharing a key with it. Read only once the index is ready."""
    try:
        conflicts = find_conflicts(asset_table, bucket_name, location)
    except LocationTooDeepError:
        return "the location is nested too deeply to check"
    if conflicts:
        owners = [f"{c.database_id}:{c.asset_id} at {c.location}" for c in conflicts[:5]]
        return f"its location overlaps existing assets {owners}"
    return None


def folder_holds_other_objects(bucket_name: str, folder: str, object_key: str) -> bool:
    """Whether anything besides `object_key` is stored under `folder` (one short listing)."""
    response = s3_client.list_objects_v2(Bucket=bucket_name, Prefix=folder, MaxKeys=2)
    return any(item.get('Key') != object_key for item in (response.get('Contents') or []))


def _sync_file_path(object_key: str, prefix: str, asset_id: str) -> str:
    """The asset-relative file path in the form the API records name a file ('/dir/file.ext')."""
    return "/" + extract_relative_file_path(object_key, prefix, asset_id).lstrip("/")


def _written_by_vams(object_metadata) -> bool:
    """Whether an object was written through VAMS (an upload, copy, move, revert, archive or pipeline
    output), which stamps its change source on the object; those writes are recorded by the action
    that made them. A direct write to the bucket carries no change source, or the direct one."""
    source = (object_metadata or {}).get(VAMS_CHANGE_SOURCE_METADATA_KEY)
    return bool(source) and source != VAMS_CHANGE_SOURCE_DIRECT


def _log_sync(action: str, operation: str, **kwargs) -> None:
    """Record a bucket sync action of the invocation being served (the actor is the system)."""
    ctx = current_audit_context()
    if ctx is not None:
        log_action(ctx.event, action, details={"source": "bucketSync", "operation": operation}, **kwargs)


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
        
        # Get the contents and filter out folder markers (objects ending with '/'). A single-file
        # location used as a prefix also lists keys that only share its characters, which are not
        # part of the asset.
        contents = response.get('Contents', [])
        if prefix and not is_folder_location(prefix):
            contents = [item for item in contents if contains(prefix, item.get('Key'))]
        
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
    
def update_asset_type(bucket_id: str, asset_id: str, bucket_name: str, asset_base_key: str,
                      asset_data: Optional[Dict] = None) -> bool:
    """
    Update asset type based on bucket contents
    
    Args:
        bucket_id: The bucket ID
        asset_id: The asset ID
        bucket_name: The S3 bucket name
        asset_base_key: The asset's stored location in S3 (a folder, or a single-file key)
        asset_data: The live asset row when the caller already read it; read here otherwise
        
    Returns:
        bool: True if updated successfully, False otherwise
    """
    try:
        # Look up asset in DynamoDB unless the caller already holds the live row
        if asset_data is None or str(asset_data.get('databaseId', '')).endswith('#deleted'):
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
    Look up LIVE databases by bucket ID

    A soft-deleted database keeps its row with `#deleted` appended to its databaseId, and the scan
    matches it just as readily as a live one. The caller uses `matching_databases[0]` when the
    configured default is not among the results, so an ingested object could be written into a deleted
    database — where it is invisible to every list and unreachable through the API, with the sync logged
    as successful. Filtering here rather than at the call site keeps every caller safe, and matches how
    the rest of this module already treats the suffix (see the archived-asset lookups below).

    Args:
        bucket_id: The bucket ID

    Returns:
        list: List of live database data
    """
    try:
        # Scan the database table for matching bucket ID, paging to exhaustion.
        #
        # A FilterExpression is applied AFTER each 1 MB page is read, so a single call is not a lookup:
        # empty (or partial) Items alongside a LastEvaluatedKey is the normal shape for "the match is on
        # a later page". Stopping at the first page is a false negative that grows with the table, and
        # the caller does not treat a miss as an error — it mints a NEW databaseId with a hash suffix
        # (see create_new_database below) and ingests the object there. So the object lands in a
        # database nobody expects, and the sync is logged as successful.
        table = dynamodb.Table(db_table_name)
        databases = []
        scan_kwargs = {'FilterExpression': Key('defaultBucketId').eq(bucket_id)}
        while True:
            response = table.scan(**scan_kwargs)
            databases.extend(
                db for db in response.get('Items', [])
                if not str(db.get('databaseId', '')).endswith('#deleted')
            )
            # Presence, not truthiness: DynamoDB omits the key when the scan is exhausted and never
            # returns a falsy one, so `in` is the accurate test — and the one that terminates against an
            # under-stubbed reader, where `.get(...)` returns a truthy MagicMock on every iteration.
            if 'LastEvaluatedKey' not in response:
                break
            scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']

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
        return None if cached_db_id == ARCHIVED_DATABASE_CACHE_VALUE else cached_db_id
    
    # Look up databases that match this bucket
    databases = lookup_databases(bucket_id)
    
    # Filter databases to match this specific bucket/prefix combination
    matching_databases = []
    for db in databases:
        # Get bucket info for this database (uses caching)
        bucket_info = get_bucket_info_from_bucket_id(db.get('defaultBucketId'))
        if bucket_info:
            if (bucket_info.get('bucketName') == bucket_name
                    and normalize_base_prefix(bucket_info.get('baseAssetsPrefix'))
                    == normalize_base_prefix(prefix)):
                matching_databases.append(db)

    # Exactly one database assigned to this registration: that one. None, or several: the
    # registration's default database, which is the configured default sync database when it is
    # assigned here, and otherwise the auto-generated database below. Never an arbitrary one.
    if len(matching_databases) == 1:
        only_db_id = matching_databases[0]['databaseId']
        logger.info(f"Using database {only_db_id}, the one database assigned to bucket {bucket_name} prefix {prefix}")
        database_cache.set(cache_key, only_db_id)
        return only_db_id
    default_db = next((db for db in matching_databases if db['databaseId'] == database_id), None)
    if default_db:
        logger.info(f"Using default database {database_id} for bucket {bucket_name} prefix {prefix}")
        database_cache.set(cache_key, default_db['databaseId'])
        return default_db['databaseId']
    if matching_databases:
        logger.info(f"{len(matching_databases)} databases are assigned to bucket {bucket_name} prefix {prefix} "
                    f"and the default sync database {database_id} is not one of them; using the bucket's "
                    "auto-generated database")
    
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
        # Cache the fact that this database is archived, so the next miss is served from the cache
        database_cache.set(cache_key, ARCHIVED_DATABASE_CACHE_VALUE)
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
        The live database ID on success (or when already restored), None on error
        or when the asset's database has been deleted.
    """
    archived_db_id = archived_asset.get('databaseId', '')
    if not archived_db_id.endswith('#deleted'):
        logger.warning(f"Archived asset {asset_id} has unexpected databaseId {archived_db_id}")
        return None
    live_db_id = archived_db_id[:-len('#deleted')]

    try:
        table = dynamodb.Table(asset_table_name)

        # Only into a database that still exists; a deleted database's archived assets stay archived
        if not verify_database_exists(live_db_id):
            logger.warning(f"Not restoring archived asset {asset_id}: database {live_db_id} has been deleted")
            return None

        # Copy the archived row as stored now: the caller's image comes from an eventually consistent
        # index read and a container cache, and can predate a write to the row.
        current = table.get_item(Key={'databaseId': archived_db_id, 'assetId': asset_id},
                                 ConsistentRead=True).get('Item')
        restored = {k: v for k, v in (current or archived_asset).items()
                    if k not in ('status', 'archivedAt', 'archivedBy', 'archivedReason')}
        restored['databaseId'] = live_db_id
        restored['unarchivedAt'] = utc_now().isoformat()
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


# The notification names of this handler's metadata stamp: boto3's managed copy() issues a single
# CopyObject below its 8 MiB multipart threshold and a multipart copy at or above it.
_METADATA_STAMP_EVENT_NAMES = ('ObjectCreated:Copy', 'ObjectCreated:CompleteMultipartUpload')


def is_vams_metadata_stamp_echo(record: Dict) -> bool:
    """Return True when this record is the echo of THIS handler's own metadata stamp.

    ``update_s3_metadata`` stamps ``databaseid`` / ``assetid`` / ``vams-changesource`` onto an object
    by copying it onto itself. That copy is an object write, so Amazon S3 raises a *second*
    notification — ``ObjectCreated:Copy``, or ``ObjectCreated:CompleteMultipartUpload`` for an object
    at or above the 8 MiB managed-copy multipart threshold — for a file that only ever changed once,
    and every consumer downstream of this handler treated it as a new file. Measured consequences:

    *   the file indexers re-indexed the file a second time;
    *   ``asset.file.uploaded`` was published a second time, so a ``fileUpload`` workflow trigger
        fired TWICE for one written file;
    *   ``update_asset_type`` re-listed the asset's prefix and ``write_file_version_history``
        re-wrote its row, on identical content.

    The history row itself does not duplicate — the second pass reads the same ``VersionId`` (no
    further copy is made), so the ``put_item`` overwrites the same PK+SK. The cost there is a
    redundant ``head_object`` plus write, not a phantom version. Measured on the deployment; do not
    upgrade this to "a duplicate version row" without re-measuring.

    Note this only arises for an object that arrives WITHOUT the stamp — a direct S3 write, a folder
    marker written outside VAMS or before VAMS wrote markers stamped, or a pipeline output landing on
    an asset path. A VAMS upload is already stamped by ``uploadFile``, and a VAMS folder marker by
    ``folder_marker_metadata``, so ``update_s3_metadata`` takes its "metadata already matches"
    branch, makes no copy, and raises no echo. Verified both ways live: a CLI upload produced one notification, a
    direct ``s3 cp`` of the same content produced two.

    The doubling on that path is what pushes a burst into AWS Lambda's
    recursive-loop detector: at 16 hops the invocation is DROPPED before the handler runs, so its
    SQS messages are never deleted and cycle until the queue's retention expires, with ``Errors``
    and ``Throttles`` both zero and no log line written.

    Two conditions, and both are required:

    *   ``eventName`` is ``ObjectCreated:Copy`` or ``ObjectCreated:CompleteMultipartUpload``. The
        stamp copy is the only way this handler writes, and it completes as a multipart upload at or
        above 8 MiB.
    *   the copy was performed by **this function**. ``userIdentity.principalId`` on an S3 event is
        ``AWS:<roleId>:<roleSessionName>``, and for a Lambda the session name is the function name.

    Requiring both is what keeps this from swallowing real work. A file COPY or MOVE performed by
    the VAMS file-operations handlers, or by any other principal, is a genuine content change at the
    destination key and still forwards — it fails the second condition, as does a large direct write,
    which completes as ``CompleteMultipartUpload`` under the writer's principal. A plain
    ``ObjectCreated:Put`` always forwards, whatever its metadata already says, which matters because
    ``uploadFile`` stamps the same three keys at upload time: recognizing the echo by "the metadata
    already matches" would have discarded every VAMS upload.
    """
    if not self_function_name:
        return False
    event_name = str(record.get('eventName') or '')
    # S3 delivers this without the "s3:" prefix on the notification record, but an event routed
    # through EventBridge or hand-built by a test may carry it; accept either spelling.
    if not any(name in event_name for name in _METADATA_STAMP_EVENT_NAMES):
        return False
    principal_id = str((record.get('userIdentity') or {}).get('principalId') or '')
    # Exact component match on the session name, not a substring of the whole id: a substring test
    # would also match a different function whose name merely contains this one's.
    return self_function_name in principal_id.split(':')


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

def is_init_marker(object_key: str) -> bool:
    """
    Whether the key is an import marker: an object named exactly `init`, or a folder
    marker named `init/`, at any depth. Matching is case-sensitive.
    """
    return (object_key in ('init', 'init/')
            or object_key.endswith('/init')
            or object_key.endswith('/init/'))

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
            # The listing for Prefix=object_key also returns longer keys (`init.txt`, `init/...`).
            # Keys come back in ascending order and the exact key sorts first, so the object's own
            # versions and delete markers end at the first entry for another key. All of them are
            # collected before any is deleted, so the continuation markers stay valid.
            # Not common.s3.list_all_object_versions: it reads the whole prefix, folder contents
            # included, and its cap counts entries, not keys.
            own_version_ids = []
            list_kwargs = {'Bucket': bucket_name, 'Prefix': object_key}
            while True:
                page = s3_client.list_object_versions(**list_kwargs)
                entries = (page.get('Versions') or []) + (page.get('DeleteMarkers') or [])
                own_version_ids.extend(e['VersionId'] for e in entries if e.get('Key') == object_key)
                if any(e.get('Key') != object_key for e in entries) or page.get('IsTruncated') is not True:
                    break
                list_kwargs['KeyMarker'] = page.get('NextKeyMarker')
                list_kwargs['VersionIdMarker'] = page.get('NextVersionIdMarker')

            for version_id in own_version_ids:
                s3_client.delete_object(
                    Bucket=bucket_name,
                    Key=object_key,
                    VersionId=version_id
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
        response = table.get_item(Key={'databaseId': database_id}, ConsistentRead=True)
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

def build_filtered_event(original_event, successful_s3_records, bindings=None):
    """
    Build a filtered event that preserves the original structure
    but only includes successfully processed S3 records.
    
    Args:
        original_event: The original event (SQS, SNS, or direct S3)
        successful_s3_records: List of S3 records that were successfully processed
        bindings: Optional `vamsBinding` per record (by `record_identity`), added to the records kept
        
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
                'Records': [with_binding(r, bindings) for r in successful_s3_records]
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
                                filtered_s3_records = [with_binding(r, bindings) for r in message['Records']
                                                       if r in successful_s3_records]
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
                        filtered_s3_records = [with_binding(r, bindings) for r in parsed_body['Records']
                                               if r in successful_s3_records]
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
                        filtered_s3_records = [with_binding(r, bindings) for r in message['Records']
                                               if r in successful_s3_records]
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

def split_event_to_size(event: Dict, limit: int) -> List[Dict]:
    """The event as one message, or split by its top-level `Records` into messages that each fit in
    `limit` bytes. A single record that does not fit is returned as it is, and its publish fails."""
    records = event.get('Records') or []
    if len(records) <= 1 or len(json.dumps(event, default=str).encode('utf-8')) <= limit:
        return [event]
    middle = len(records) // 2
    return (split_event_to_size({**event, 'Records': records[:middle]}, limit)
            + split_event_to_size({**event, 'Records': records[middle:]}, limit))


def publish_to_file_indexer_sns(event):
    """
    Publish S3 event to file indexer SNS topic for downstream processing.
    
    Args:
        event: The S3 event to publish

    Returns:
        bool: True when the event reached the topic or no topic is configured; False when the
        publish failed, which leaves its records undelivered to every file indexer.
    """
    try:
        if not file_indexer_sns_topic_arn:
            logger.warning("FILE_INDEXER_SNS_TOPIC_ARN not configured, skipping SNS publish")
            return True
        
        # Prepare payload for indexing
        event.update({
            "ASSET_BUCKET_NAME": asset_bucket_name,
            "ASSET_BUCKET_PREFIX": asset_bucket_prefix
        })
        
        # Publish to SNS topic, split by message when the records would exceed one message
        for part in split_event_to_size(event, SNS_MESSAGE_MAX_BYTES - PUBLISH_SIZE_MARGIN_BYTES):
            response = sns_client.publish(
                TopicArn=file_indexer_sns_topic_arn,
                Message=json.dumps(part, default=str),
                Subject='S3 Bucket Sync Event'
            )
            logger.info(f"Successfully published to file indexer SNS topic: {response['MessageId']}")
        return True
    except Exception as e:
        logger.exception(f"Error publishing to file indexer SNS topic: {e}")
        return False

# PutEvents result codes for a transient refusal of an entry; an entry refused with one of these is
# re-sent once. EventBridge reports a refused entry in a normal response (FailedEntryCount plus the
# entry's ErrorCode and ErrorMessage) rather than as an error, so botocore's retry layer never
# re-sends it.
RETRYABLE_PUT_EVENTS_ERROR_CODES = frozenset({"ThrottlingException", "InternalFailure"})

def _refused_put_events_entry(response):
    """Return the first result entry a PutEvents response reports as refused, or None when every
    entry was accepted. Result entries match the request entries one for one; a refused entry
    carries ErrorCode and ErrorMessage in place of an EventId."""
    for entry in (response or {}).get("Entries") or []:
        if entry.get("ErrorCode"):
            return entry
    return None

def publish_to_orchestration_bus(successful_records):
    """Publish an asset.file.uploaded event to the VAMS orchestration EventBridge bus (Phase 2
    fileUpload trigger delivery). A standing rule on the bus routes these to the SQS buffer the
    workflowTriggerDispatch lambda consumes.

    Publishes a CLEAN, flat detail — {"Records": [{"s3": {...}}], ASSET_BUCKET_*} — built from the
    already-unwrapped S3 records, so the dispatcher does not re-implement the SQS->SNS->S3 unwrapping.
    Every S3 record is published, including workflow-written ones: the re-trigger decision belongs to
    the dispatcher, which knows the candidate workflow (see the loop-guard note in the body).

    Best-effort: a publish failure is logged, not raised (auto-trigger is non-critical to the primary
    ingestion path). An entry PutEvents refuses with a retryable code is re-sent once; a refusal that
    remains is logged as an error naming the objects whose fileUpload triggers do not fire."""
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
        parts = split_event_to_size(
            {
                "Records": publishable_records,
                "ASSET_BUCKET_NAME": asset_bucket_name,
                "ASSET_BUCKET_PREFIX": asset_bucket_prefix,
            },
            EVENTBRIDGE_ENTRY_MAX_BYTES - PUBLISH_SIZE_MARGIN_BYTES,
        )
        for detail in parts:
            _put_file_uploaded_entry(detail)
    except Exception as e:
        logger.exception(f"Error publishing to orchestration bus: {e}")


def _put_file_uploaded_entry(detail):
    """Put one asset.file.uploaded entry; a retryable refusal is re-sent once, and a refusal that
    remains is logged as an error naming the objects whose fileUpload triggers do not fire."""
    publishable_records = detail["Records"]
    entry = {
        "EventBusName": orchestration_bus_name,
        "Source": f"{orchestration_event_source_prefix}.trigger.fileUpload",
        "DetailType": "asset.file.uploaded",
        "Detail": json.dumps(detail, default=str),
    }
    try:
        refused = _refused_put_events_entry(events_client.put_events(Entries=[entry]))
        if refused and refused.get("ErrorCode") in RETRYABLE_PUT_EVENTS_ERROR_CODES:
            logger.warning(
                f"EventBridge refused asset.file.uploaded on bus {orchestration_bus_name} "
                f"({refused.get('ErrorCode')}: {refused.get('ErrorMessage')}); re-sending once")
            refused = _refused_put_events_entry(events_client.put_events(Entries=[entry]))
    except Exception as e:
        logger.exception(f"EventBridge put_events failed for asset.file.uploaded on bus {orchestration_bus_name}: {e}")
        return
    if refused:
        object_keys = [r["s3"].get("object", {}).get("key") for r in publishable_records]
        logger.error(
            f"EventBridge put_events refused asset.file.uploaded on bus {orchestration_bus_name} "
            f"({refused.get('ErrorCode')}: {refused.get('ErrorMessage')}); the fileUpload triggers for "
            f"{len(publishable_records)} record(s) will not fire: {object_keys}")
        return
    logger.info(f"Published asset.file.uploaded event ({len(publishable_records)} record(s)) to the orchestration bus")

def build_history_record(database_id, asset_id, relative_file_path, version_id,
                         s3_metadata, s3_last_modified, etag=None, size=None, resolved_by=None):
    """Build an asset-file change-history item from an ingested object's S3 metadata.

    No vams-changesource present means the file was changed outside VAMS (e.g. a
    direct S3 upload): record changeSource="direct" with no provenance columns.
    Otherwise map the vams-change* metadata to flat columns (skipping blanks).

    The row is also the sync ledger entry of that version: `etag` and `size` of the version, and
    `resolvedBy`, how bucket sync found its asset. Each is written only when known.
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
        "recordCreated": utc_now().isoformat() + "Z",
        "s3LastModified": s3_last_modified or "",
    }
    if etag:
        record["etag"] = str(etag).strip('"')
    if size is not None:
        record["size"] = int(size)
    if resolved_by:
        record["resolvedBy"] = resolved_by
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
                               s3_metadata, s3_last_modified, etag=None, size=None, resolved_by=None):
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
            s3_metadata, s3_last_modified, etag=etag, size=size, resolved_by=resolved_by,
        )
        asset_file_version_history_table.put_item(Item=record)
    except Exception as e:
        logger.exception(f"Failed writing file version history for {asset_id}/{relative_file_path}: {e}")


def process_s3_record(record: Dict, binding_out: Optional[Dict] = None,
                      receive_count: Optional[int] = None) -> Tuple[bool, bool, str]:
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
        binding_out: Filled with the record's `vamsBinding` once its asset is known
        receive_count: How often the record's SQS message was received (None outside SQS)
        
    Raises:
        RecordRedrive: the record's message must be received again (a deferred auto-create, or a
            rejected record the rejected-record queue did not accept)

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

        # 1.c Check if the key is VAMS system data and must be skipped.
        #
        # Tested over EVERY path segment of the whole key, matching fileIndexer and
        # workflowTriggerDispatch. The previous form tested only the extracted asset id -- the first
        # segment after the configured prefix -- which missed a reserved folder nested below an asset
        # id, and could not run at all for a key that does not start with the configured prefix
        # (extraction returns None above). Treating pipeline output as an asset file is not merely
        # wasted work: update_s3_metadata below re-stamps it by copying the object onto itself, and
        # that copy re-enters the same s3:ObjectCreated notification that triggered this handler.
        if key_has_reserved_segment(object_key, prefix, RESERVED_S3_PREFIX_FOLDERS):
            logger.info(f"Key {object_key} contains a reserved VAMS folder segment, skipping")
            return False, False, f"Key {object_key} contains a reserved VAMS folder segment"

        # 1.d The first folder is an asset id only for the fast path and for auto-create. A file in
        # any other stored location is found through the location index whatever its first folder is.
        first_folder_valid = validate_asset_id(asset_id)

        # 1.e Determine WHAT HAPPENED before doing anything: an ObjectCreated event this handler
        # caused itself, by stamping metadata in step 4 below, describes no change a consumer needs
        # to see. Everything after this point — asset lookup/creation, the metadata stamp, the asset
        # type recomputation, the version-history row — has already run for the write that preceded
        # the stamp, and every one of them would run a second time on identical content.
        #
        # Returns processed-successfully so the SQS message is deleted, and should_index False so
        # neither the indexers nor the fileUpload trigger see it. This is the ONE case where a
        # withheld record is not "out of scope": it is the same file the previous record already
        # carried, which is why withholding it loses nothing.
        if is_vams_metadata_stamp_echo(record):
            logger.info(
                f"Key {object_key} event is this handler's own metadata stamp "
                f"({record.get('eventName')}); no change to process or forward"
            )
            return True, False, f"Skipped own metadata stamp echo for {object_key}"

        base_prefix = normalize_base_prefix(prefix)
        asset_table = dynamodb.Table(asset_table_name)
        # A redelivered record resolves again without the per-container index cache.
        use_index_cache = not (receive_count and receive_count > 1)

        # 2.a Fast path: the first folder under the prefix names the asset (BucketIdGSI). A hit counts
        # only when that asset's stored location holds the key.
        resolved_by = RESOLVED_BY_FAST_PATH
        owner_asset_id = asset_id
        owner_bucket_id = bucket_id
        asset_data = lookup_asset(bucket_id, asset_id) if first_folder_valid else None
        if asset_data and not contains(asset_location_of(asset_data, base_prefix, asset_id), object_key):
            logger.info(f"Asset {asset_id} does not hold {object_key} at its stored location; resolving by location")
            asset_data = None

        # 2.b On a miss: the live asset whose stored location contains the key (location index).
        # Two live assets on one location attribute nothing; the record goes to the rejected queue.
        if not asset_data:
            resolution = resolve_owner(asset_table, bucket_name, object_key, use_cache=use_index_cache)
            if resolution.status == RESOLVED_AMBIGUOUS:
                return reject_record(record, bucket_name, object_key, REJECTED_REASON_AMBIGUOUS,
                                     resolution.candidates)
            if resolution.status == RESOLVED_FOUND:
                asset_data = resolution.row
                resolved_by = RESOLVED_BY_LOCATION_INDEX
                owner_asset_id = resolution.entry.asset_id
                owner_bucket_id = resolution.entry.bucket_id or bucket_id

        database_id_to_use = None
        archived_asset = None
        if asset_data:
            logger.info(f"Asset {owner_asset_id} found in bucket {owner_bucket_id} ({resolved_by})")
            database_id_to_use = asset_data.get('databaseId')
        else:
            # 2.c No live owner. Until the location index is ready (its backfill marker exists) a miss
            # there proves nothing, so the first folder decides exactly as before. Once it is ready,
            # an archived owner is found by location, and an auto-created asset may not overlap any
            # asset's location.
            index_is_ready = index_ready(asset_table_name)
            if index_is_ready:
                archived = resolve_owner(asset_table, bucket_name, object_key, include_archived=True,
                                         use_cache=use_index_cache)
                if archived.status == RESOLVED_AMBIGUOUS:
                    return reject_record(record, bucket_name, object_key, REJECTED_REASON_AMBIGUOUS,
                                         archived.candidates)
                if archived.status == RESOLVED_FOUND:
                    resolved_by = RESOLVED_BY_LOCATION_INDEX
                    owner_asset_id = archived.entry.asset_id
                    owner_bucket_id = archived.entry.bucket_id or bucket_id
                    if archived.entry.archived:
                        archived_asset = archived.row
                    else:
                        asset_data = archived.row
                        database_id_to_use = asset_data.get('databaseId')
            elif first_folder_valid:
                archived_asset = lookup_archived_asset(bucket_id, asset_id)

            if asset_data:
                logger.info(f"Asset {owner_asset_id} found in bucket {owner_bucket_id} ({resolved_by})")
            elif archived_asset:
                # A new file placed directly in S3 under an archived asset's location restores the
                # asset record (DynamoDB-only unarchive): the asset becomes active again but previously
                # archived files keep their delete markers — the files present under the location are
                # the asset's files, and users unarchive individual old files via the API.
                if not object_still_exists(bucket_name, object_key):
                    logger.info(
                        f"Object {object_key} no longer exists; not restoring archived asset {owner_asset_id}"
                    )
                    return True, True, f"Skipped stale create event for archived asset {owner_asset_id}"
                restored_database_id = restore_archived_asset(owner_bucket_id, owner_asset_id, archived_asset)
                if not restored_database_id:
                    logger.error(f"Failed to restore archived asset {owner_asset_id}")
                    return False, True, f"Failed to restore archived asset {owner_asset_id}"
                _log_sync("assetSync", "restore", database_id=restored_database_id, asset_id=owner_asset_id)
                logger.info(
                    f"Restored archived asset {owner_asset_id} to database {restored_database_id} "
                    f"after direct S3 upload of {object_key}"
                )
                database_id_to_use = restored_database_id
            else:
                # Auto-create an asset for a new first-level folder.
                if not first_folder_valid:
                    logger.info(f"Asset ID {asset_id} is not valid, skipping processing but forwarding to indexers")
                    return False, True, f"Asset ID {asset_id} is not valid"
                derived_location = derive_location({'baseAssetsPrefix': base_prefix}, '', asset_id)
                if index_is_ready:
                    refusal = auto_create_refusal(asset_table, bucket_name, derived_location)
                    if refusal:
                        logger.warning(f"Not auto-creating asset {asset_id} for {object_key}: {refusal}")
                        return False, True, f"Auto-create of asset {asset_id} refused"
                    # The folder already holds other objects: an asset created through the API a
                    # moment ago may own one of them and not be in the index yet. Look again once.
                    if receive_count == 1 and folder_holds_other_objects(bucket_name, derived_location, object_key):
                        logger.info(f"Deferring the auto-create of asset {asset_id} for {object_key} once")
                        raise RecordRedrive(f"Deferred auto-create of asset {asset_id}",
                                            DEFERRED_AUTO_CREATE_VISIBILITY_SECONDS)

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
                _log_sync("assetSync", "create", database_id=database_id_to_use, asset_id=asset_id)

        # The asset's stored location decides the file's path and the asset type.
        asset_location = asset_location_of(asset_data or archived_asset, base_prefix, owner_asset_id)
        relative_path = relative_path_in(asset_location, object_key)
        if relative_path is None:
            relative_path = "/" + extract_relative_file_path(object_key, prefix, owner_asset_id).lstrip("/")
        if binding_out is not None:
            binding_out.update(build_binding(database_id_to_use, owner_asset_id, owner_bucket_id,
                                             relative_path, resolved_by))

        # 3. An "init" import marker is deleted and not indexed when its asset is the first-level folder
        # named by its own id (however the asset was found). In any other stored location an object
        # named `init` is an ordinary file.
        first_level_asset = (owner_asset_id == asset_id and asset_location
                             == derive_location({'baseAssetsPrefix': base_prefix}, '', asset_id))
        if first_level_asset and is_init_marker(object_key):
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
        update_result = update_s3_metadata(bucket_name, object_key, database_id_to_use, owner_asset_id)
        if not update_result:
            logger.error(f"Failed to update metadata for {object_key}")
            return False, True, f"Failed to update metadata for {object_key}"
        
        # 5. Update asset type from the files at the asset's stored location
        update_asset_type(owner_bucket_id, owner_asset_id, bucket_name, asset_location, asset_data=asset_data)

        # Record the change history row of the version this event names (best-effort; never fails
        # the sync), which is also that version's sync ledger entry. Skip folder markers (no
        # version/content). head_object returns 'null' for VersionId on buckets without versioning.
        object_metadata = None
        if not object_key.endswith('/'):
            try:
                event_version_id = (record['s3'].get('object') or {}).get('versionId')
                head_kwargs = {'Bucket': bucket_name, 'Key': object_key}
                if event_version_id:
                    head_kwargs['VersionId'] = event_version_id
                head = s3_client.head_object(**head_kwargs)
                object_metadata = head.get('Metadata', {}) or {}
                last_modified = head.get('LastModified')
                write_file_version_history(
                    database_id_to_use,
                    owner_asset_id,
                    relative_path,
                    head.get('VersionId') or event_version_id or 'null',
                    object_metadata,
                    last_modified.isoformat() if last_modified else "",
                    etag=head.get('ETag'),
                    size=head.get('ContentLength'),
                    resolved_by=resolved_by,
                )
            except Exception as e:
                logger.exception(f"History write skipped for {object_key}: {e}")

        # 6. Check if object key ends with '/' (folder marker) - process but skip indexing
        if object_key.endswith('/'):
            logger.info(f"Folder marker detected: {object_key}, processing but skipping indexing")
            return True, False, f"Processed folder marker {object_key}"

        if not _written_by_vams(object_metadata):
            _log_sync("fileSync", "created", database_id=database_id_to_use, asset_id=owner_asset_id,
                      file_path=relative_path,
                      bytes=(record.get('s3', {}).get('object') or {}).get('size'))
        return True, True, f"Successfully processed {object_key}"
    except RecordRedrive:
        raise
    except Exception as e:
        logger.exception(f"Error processing S3 record: {e}")
        # Unexpected failure mid-processing: still forward to the indexers so
        # OpenSearch and other registered indexers can reconcile independently.
        return False, True, f"Error processing S3 record."

def on_storage_event_created(event, bindings=None, receive_counts=None, redrives=None):
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
        bindings: Filled with each forwarded record's `vamsBinding`, by `record_identity`
        receive_counts: The SQS receive count of each record, by `record_identity`
        redrives: Filled with (record, visibility timeout) for each record to receive again
        
    Returns:
        tuple: (success_status, successful_records) where success_status is a boolean
               indicating if processing completed without hard errors, and successful_records
               is a list of records that were successfully processed
    """
    logger.info(f"Processing storage event: {event_log_summary(event)}")
    
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
            binding = {}
            receive_count = (receive_counts or {}).get(record_identity(record))
            try:
                success, should_index, message = process_s3_record(
                    record, binding_out=binding, receive_count=receive_count)
            except RecordRedrive as redrive:
                if redrives is None:
                    error_count += 1
                    logger.error(f"Record could not be completed and cannot be returned to its queue: {redrive}")
                else:
                    redrives.append((record, redrive.visibility_timeout))
                    logger.info(f"Record returned to its queue: {redrive}")
                continue
            if should_index and binding and bindings is not None:
                bindings[record_identity(record)] = binding

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

def _sqs_messages_with_s3_records(event) -> List[Tuple[Dict, List[Dict]]]:
    """Each SQS message of the event with the S3 records its body carries (an SNS notification or an
    S3 event), parsed as parse_event parses them. Empty for any other event shape."""
    pairs = []
    records = (event or {}).get('Records') or [] if isinstance(event, dict) else []
    if not records or not isinstance(records[0], dict) or records[0].get('eventSource') != 'aws:sqs':
        return pairs
    for sqs_record in records:
        try:
            body = json.loads(sqs_record.get('body') or '')
            if isinstance(body, dict) and 'Message' in body:
                body = json.loads(body['Message'])
        except (TypeError, ValueError):
            continue
        s3_records = body.get('Records') if isinstance(body, dict) else None
        if isinstance(s3_records, list):
            pairs.append((sqs_record, s3_records))
    return pairs


def receive_counts_by_record(event) -> Dict[str, int]:
    """The SQS `ApproximateReceiveCount` of each S3 record's message, by `record_identity`."""
    counts = {}
    for sqs_record, s3_records in _sqs_messages_with_s3_records(event):
        try:
            count = int((sqs_record.get('attributes') or {}).get('ApproximateReceiveCount') or 0)
        except (TypeError, ValueError):
            count = 0
        if count <= 0:
            continue
        for s3_record in s3_records:
            counts[record_identity(s3_record)] = count
    return counts


_queue_urls: Dict[str, str] = {}


def queue_url_for_arn(queue_arn: str) -> Optional[str]:
    """The URL of the SQS queue an event-source record came from (cached per container). Read with
    GetQueueUrl so no endpoint suffix is assumed for the partition."""
    if queue_arn in _queue_urls:
        return _queue_urls[queue_arn]
    parts = (queue_arn or '').split(':')
    if len(parts) < 6 or not parts[5]:
        return None
    url = sqs_client.get_queue_url(QueueName=parts[5], QueueOwnerAWSAccountId=parts[4])['QueueUrl']
    _queue_urls[queue_arn] = url
    return url


def redrive_failures(event, redrives) -> List[Dict[str, str]]:
    """The batch item failures of the messages whose records are to be received again. A message is
    first given the visibility timeout its record asks for; when that fails it keeps the queue's."""
    failures: List[Dict[str, str]] = []
    if not redrives:
        return failures
    seen = set()
    pairs = _sqs_messages_with_s3_records(event)
    for record, visibility_timeout in redrives:
        for sqs_record, s3_records in pairs:
            message_id = sqs_record.get('messageId')
            if record not in s3_records or not message_id or message_id in seen:
                continue
            seen.add(message_id)
            failures.append({'itemIdentifier': message_id})
            if visibility_timeout is None:
                continue
            try:
                queue_url = queue_url_for_arn(sqs_record.get('eventSourceARN') or '')
                if queue_url:
                    sqs_client.change_message_visibility(QueueUrl=queue_url,
                                                         ReceiptHandle=sqs_record.get('receiptHandle'),
                                                         VisibilityTimeout=visibility_timeout)
            except Exception as e:
                logger.warning(f"Could not set the visibility timeout of message {message_id}: {e}")
    return failures


def merge_failures(*failure_lists) -> List[Dict[str, str]]:
    """Batch item failures from several sources, each message named once."""
    merged, seen = [], set()
    for failures in failure_lists:
        for failure in failures or []:
            identifier = failure.get('itemIdentifier')
            if identifier and identifier not in seen:
                seen.add(identifier)
                merged.append(failure)
    return merged


def relative_path_for_key(asset_location: str, object_key: str, prefix: str, asset_id: str,
                          resolved_by: str) -> Optional[str]:
    """The asset-relative path (leading `/`) of a key in its asset, or None when the asset's stored
    location does not hold it. A legacy first-folder match falls back to the path after the folder."""
    relative = relative_path_in(asset_location, object_key)
    if relative is None and resolved_by == RESOLVED_BY_FAST_PATH:
        relative = "/" + extract_relative_file_path(object_key, prefix, asset_id).lstrip("/")
    return relative


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
                    logger.warning(
                        f"SNS Message field is not valid JSON ({len(record['Sns']['Message'])} characters): {e}"
                    )
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


@audited_handler
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
        dict: for an SQS batch, `batchItemFailures` naming the messages whose records did not
        reach the file indexer topic (every message when the invocation failed unexpectedly);
        an empty list when all of them did.
    """
    logger.info(f"File creation event received: {event_log_summary(event)}")

    failures = []
    redrives = []

    try:
        # Parse the event to handle different sources
        parsed_event = parse_event(event)

        # Process the storage event if it contains records
        if parsed_event.get('Records'):
            # Process the storage event and get success status and indexable records
            bindings = {}
            success, successful_records = on_storage_event_created(
                parsed_event, bindings=bindings, receive_counts=receive_counts_by_record(event),
                redrives=redrives)

            if not success:
                logger.warning("Hard errors encountered during storage event processing")

            # Publish every record flagged for indexing, regardless of hard errors
            # elsewhere in the batch — withholding them would leave OpenSearch and
            # other registered indexers out of sync.
            if successful_records:
                # Build filtered event preserving original structure
                filtered_event = build_filtered_event(event, successful_records, bindings)

                if filtered_event:
                    logger.info(f"Publishing {len(successful_records)} records to file indexer SNS")
                    if publish_to_file_indexer_sns(filtered_event):
                        # Publish to the orchestration EventBridge bus for the fileUpload trigger
                        # dispatcher. Pass the flat S3 records so a clean detail is published.
                        publish_to_orchestration_bus([with_binding(r, bindings) for r in successful_records])
                    else:
                        # The indexers never saw these records, so their messages are redriven.
                        # The trigger is published by the delivery that reaches the topic, so a
                        # file fires its fileUpload trigger once rather than once per delivery.
                        failures = all_batch_item_failures(filtered_event)
                else:
                    logger.error("Indexable records could not be matched to their messages; reporting the batch for redrive")
                    failures = all_batch_item_failures(event)
            else:
                logger.info("No records to publish, skipping file indexer SNS publish")
        else:
            logger.warning("No records found in parsed event, nothing to process")
    except Exception as e:
        logger.exception(f"Unhandled error in lambda_handler_created: {e}")
        # Which records reached the indexers is unknown, so every message is redriven.
        failures = all_batch_item_failures(event)

    return with_batch_item_failures({}, event, merge_failures(failures, redrive_failures(event, redrives)))


@audited_handler
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
        dict: for an SQS batch, `batchItemFailures` naming the messages whose records did not
        reach the file indexer topic (every message when the invocation failed unexpectedly);
        an empty list when all of them did.
    """
    logger.info(f"File deletion event received: {event_log_summary(event)}")

    failures = []

    try:
        # Parse the event to handle different sources
        parsed_event = parse_event(event)

        # Process records if present
        if parsed_event.get('Records'):
            indexable_records = []
            bindings = {}

            # Check each record for files that are not folder markers
            for record in parsed_event.get('Records', []):
                # Skip records without S3 information
                if not record.get('s3'):
                    logger.warning("Record does not contain S3 information, skipping")
                    continue

                # Extract bucket name and object key. S3 event notifications deliver
                # the key URL-encoded per AWS spec (spaces → '+', specials → '%XX');
                # decode it here so metadata paths and S3 version lookups operate on
                # the actual object key. The record forwarded to the indexers keeps
                # the key exactly as received — fileIndexer applies its own decoding.
                bucket_name = record['s3']['bucket']['name']
                raw_object_key = record['s3']['object']['key']
                object_key = decode_s3_event_key(raw_object_key)

                # Skip init import markers entirely (both processing and indexing)
                if is_init_marker(object_key):
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

                    # Skip VAMS system data (never indexed). Segment-wise over the whole key for the
                    # same reasons as the created path above.
                    if key_has_reserved_segment(object_key, prefix, RESERVED_S3_PREFIX_FOLDERS):
                        logger.info(f"Key {object_key} contains a reserved VAMS folder segment, skipping")
                        continue

                    # The asset that holds the key, read once: its first folder (BucketIdGSI, live
                    # rows), else the location index (live or archived rows). The asset type update
                    # below takes the row rather than reading it again.
                    base_prefix = normalize_base_prefix(prefix)
                    first_folder_valid = validate_asset_id(asset_id)
                    resolved_by = RESOLVED_BY_FAST_PATH
                    owner_asset_id, owner_bucket_id = asset_id, bucket_id
                    asset_data = lookup_asset(bucket_id, asset_id) if first_folder_valid else None
                    if asset_data and not contains(asset_location_of(asset_data, base_prefix, asset_id), object_key):
                        asset_data = None
                    if not asset_data:
                        resolution = resolve_owner(dynamodb.Table(asset_table_name), bucket_name, object_key,
                                                   include_archived=True)
                        if resolution.status == RESOLVED_FOUND:
                            asset_data = resolution.row
                            resolved_by = RESOLVED_BY_LOCATION_INDEX
                            owner_asset_id = resolution.entry.asset_id
                            owner_bucket_id = resolution.entry.bucket_id or bucket_id
                    if not asset_data:
                        # The asset record may already be gone (asset-level delete); the record is
                        # still forwarded so the indexers can clean up.
                        if first_folder_valid:
                            logger.info(f"Asset {asset_id} not found in bucket {bucket_id}; skipping metadata cleanup")
                        else:
                            logger.info(f"Asset ID {asset_id} is not valid, skipping cleanup but forwarding to indexers")
                        indexable_records.append(record)
                        continue

                    asset_location = asset_location_of(asset_data, base_prefix, owner_asset_id)
                    database_id_for_asset = str(asset_data.get('databaseId', ''))
                    asset_is_live = not database_id_for_asset.endswith('#deleted')
                    live_database_id = (database_id_for_asset if asset_is_live
                                        else database_id_for_asset[:-len('#deleted')])

                    # Key spellings to clean up: the decoded key is the real object
                    # key for an encoded event, while a key delivered without any
                    # encoding applied is real as delivered (a literal '+' in a
                    # filename decodes to a space). Both are handled when they differ.
                    key_spellings = [object_key]
                    if raw_object_key != object_key:
                        key_spellings.append(raw_object_key)

                    # Only delete metadata/attributes when the file is permanently
                    # gone (no versions or delete markers remain under any spelling).
                    # A delete-marker event from an archive flow is reversible —
                    # unarchive restores the file and must find its metadata intact.
                    # Remaining versions under either spelling, or an error reading
                    # version state, preserve the metadata. Metadata rows are keyed by
                    # the live database id and the path relative to the asset's location.
                    permanently_deleted = all(
                        is_object_permanently_deleted(bucket_name, key) for key in key_spellings
                    )
                    if permanently_deleted:
                        for key in key_spellings:
                            relative_file_path = relative_path_for_key(asset_location, key, prefix,
                                                                       owner_asset_id, resolved_by)
                            if relative_file_path is None:
                                continue
                            logger.info(f"Deleting metadata/attributes for permanently deleted file: {relative_file_path}")
                            delete_file_metadata_on_s3_delete(live_database_id, owner_asset_id, relative_file_path)
                    else:
                        logger.info(f"File {object_key} still has versions (archived); preserving metadata/attributes")

                    relative_path = relative_path_for_key(asset_location, object_key, prefix,
                                                          owner_asset_id, resolved_by)
                    if relative_path is not None:
                        bindings[record_identity(record)] = build_binding(
                            live_database_id, owner_asset_id, owner_bucket_id, relative_path, resolved_by)

                    # Update asset type based on remaining files (a live asset only)
                    if asset_is_live:
                        logger.info(f"Updating asset type for {owner_asset_id} after file deletion")
                        update_asset_type(owner_bucket_id, owner_asset_id, bucket_name, asset_location,
                                          asset_data=asset_data)

                    logger.info(f"Successfully processed deletion for {object_key}")
                except Exception as e:
                    logger.exception(f"Error processing deletion record for {object_key}; forwarding to indexers anyway: {e}")

                # Forward the record to the indexers regardless of cleanup outcome
                indexable_records.append(record)

            # Only publish to SNS if we have records to index
            if indexable_records:
                # Build filtered event preserving original structure
                filtered_event = build_filtered_event(event, indexable_records, bindings)

                if filtered_event:
                    logger.info(f"Publishing {len(indexable_records)} deletion records to file indexer SNS")
                    if not publish_to_file_indexer_sns(filtered_event):
                        # The indexers never saw these deletes, so their messages are redriven.
                        failures = all_batch_item_failures(filtered_event)
                else:
                    logger.error("Indexable deletion records could not be matched to their messages; reporting the batch for redrive")
                    failures = all_batch_item_failures(event)
            else:
                logger.info("No deletion records to publish, skipping file indexer SNS publish")
        else:
            logger.warning("No records found in parsed deletion event, nothing to process")
    except Exception as e:
        logger.exception(f"Error in lambda_handler_deleted: {e}")
        # Which records reached the indexers is unknown, so every message is redriven.
        failures = all_batch_item_failures(event)

    return with_batch_item_failures({}, event, failures)
