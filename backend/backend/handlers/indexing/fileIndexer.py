"""
File indexer for VAMS dual-index OpenSearch system.
Handles indexing of S3 files with full data lookups from multiple sources.

Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
SPDX-License-Identifier: Apache-2.0
"""

import os
import boto3
import json
import hashlib
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
from common.resourceNames import get_table_name, ResourceKeys
from common.s3MetadataKeys import (
    ASSET_ID_METADATA_KEY,
    DATABASE_ID_METADATA_KEY,
    SEARCHABLE_VAMS_METADATA_KEYS,
    is_system_metadata_key,
)
from common.validators import validate
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from models.common import APIGatewayProxyResponseV2, internal_error, success, validation_error, general_error, authorization_error, VAMSGeneralErrorResponse, validation_error_message
from models.indexing import FileDocumentModel, FileIndexRequest, IndexOperationResponse, MAX_S3_KEY_LENGTH
from common.indexing.geoLocation import build_geo_location
from common.s3PathPatterns import RESERVED_S3_PREFIX_FOLDERS, PREVIEW_FILE_PATTERN
from common.dynamoDbMetadataKeys import is_excluded_metadata_record

# Configure AWS clients with retry configuration
retry_config = Config(
    retries={
        'max_attempts': 5,
        'mode': 'adaptive'
    }
)

#Excluded patterns or prefixes from file paths to exclude
excluded_prefixes = RESERVED_S3_PREFIX_FOLDERS
excluded_patterns = [] # PREVIEW_FILE_PATTERN not included here as the fileIndexer processes these in a special way

dynamodb = boto3.resource('dynamodb', config=retry_config)
s3_client = boto3.client('s3', config=retry_config)
opensearch_client = boto3.client('opensearchserverless', config=retry_config) if os.environ.get('OPENSEARCH_TYPE') == 'serverless' else boto3.client('opensearch', config=retry_config)
logger = safeLogger(service_name="FileIndexer")

# Global variables for claims and roles
claims_and_roles = {}

# Load environment variables with error handling
try:
    asset_storage_table_name = get_table_name(ResourceKeys.ASSET_STORAGE_TABLE)
    asset_file_metadata_table_name = get_table_name(ResourceKeys.ASSET_FILE_METADATA_STORAGE_TABLE)
    file_attribute_table_name = get_table_name(ResourceKeys.FILE_ATTRIBUTE_STORAGE_TABLE)
    s3_asset_buckets_table_name = get_table_name(ResourceKeys.S3_ASSET_BUCKETS_STORAGE_TABLE)
    opensearch_file_index_ssm_param = os.environ["OPENSEARCH_FILE_INDEX_SSM_PARAM"]
    opensearch_endpoint_ssm_param = os.environ["OPENSEARCH_ENDPOINT_SSM_PARAM"]
    opensearch_type = os.environ.get("OPENSEARCH_TYPE", "serverless")
except Exception as e:
    logger.exception("Failed loading environment variables or resolving resource names")
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

def query_all_pages(table, **query_kwargs) -> List[Dict[str, Any]]:
    """Query a table, following LastEvaluatedKey, and return every matching item.

    A single DynamoDB query returns at most 1 MB of items, so a caller that reads
    only the first page silently truncates the result set.
    """
    items: List[Dict[str, Any]] = []
    while True:
        response = table.query(**query_kwargs)
        items.extend(response.get('Items', []))

        # DynamoDB omits LastEvaluatedKey on the last page, so the end of the walk is the key's
        # ABSENCE. Reading its value instead never terminates against an under-stubbed reader,
        # whose every ``get`` answers truthily.
        if 'LastEvaluatedKey' not in response:
            return items
        query_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']


def normalize_bucket_prefix(prefix: Optional[str]) -> Optional[str]:
    """Normalize a bucket prefix to the form stored on a file document.

    ``get_bucket_details`` stores ``baseAssetsPrefix`` with a trailing slash and no
    leading slash, so a bucket rooted at ``/`` is indexed as an empty string.
    Returns None when the prefix is unknown.
    """
    if prefix is None:
        return None
    if not prefix.endswith('/'):
        prefix += '/'
    if prefix.startswith('/'):
        prefix = prefix[1:]
    return prefix


# Registered prefixes per bucket name, cached for the container's lifetime with a TTL.
# The records are written by a CDK custom resource at deploy time, so they are stable
# within a Lambda container.
_registered_prefix_cache: Dict[str, Tuple[float, List[str]]] = {}
_REGISTERED_PREFIX_CACHE_TTL_SECONDS = 300


def get_registered_bucket_prefixes(bucket_name: str) -> List[str]:
    """Normalized ``baseAssetsPrefix`` values registered for a bucket name.

    Read from the bucketNameGSI rather than from an asset record, so it still resolves
    once the asset it belonged to has been permanently deleted.
    """
    cached = _registered_prefix_cache.get(bucket_name)
    if cached and cached[0] > time.time():
        return cached[1]

    records = query_all_pages(
        s3_asset_buckets_table,
        IndexName='bucketNameGSI',
        KeyConditionExpression=Key('bucketName').eq(bucket_name)
    )
    prefixes = []
    for record in records:
        normalized = normalize_bucket_prefix(record.get('baseAssetsPrefix'))
        if normalized is not None:
            prefixes.append(normalized)

    _registered_prefix_cache[bucket_name] = (
        time.time() + _REGISTERED_PREFIX_CACHE_TTL_SECONDS, prefixes)
    return prefixes


def resolve_registered_bucket_prefix(
    bucket_name: str, s3_key: str, event_prefix: Optional[str]
) -> Optional[str]:
    """Resolve which registered prefix an object key sits under, normalized to the form
    stored on a file document (``prefix-a/``, or ``''`` for a bucket rooted at ``/``).

    Object keys are ``{baseAssetsPrefix}{assetId}/{filePath}`` — the same
    ``asset_base_key`` shape the indexing path builds — so the registered prefix has to
    be removed before the first remaining component is the asset ID.

    A non-root ``event_prefix`` that fits the key is authoritative and is taken as-is:
    each bucket registration gets its own sync Lambda carrying that one prefix in its
    environment. The root prefix is NOT taken on faith, because it is also what an
    absent value defaults to everywhere this field is read — and treating a non-root
    bucket as root reads the prefix's first path segment as the asset ID. In that case,
    and whenever the key does not fit, fall back to the bucket's own registration
    records. Registered prefixes for one bucket cannot overlap (``getConfig`` rejects
    that, and the root overlaps every prefix), so at most one record can match a key.
    """
    normalized_event_prefix = normalize_bucket_prefix(event_prefix)
    if normalized_event_prefix and s3_key.startswith(normalized_event_prefix):
        return normalized_event_prefix

    try:
        for candidate in get_registered_bucket_prefixes(bucket_name):
            if s3_key.startswith(candidate):
                return candidate
    except Exception as e:
        logger.exception(
            f"Error resolving registered prefixes for bucket {bucket_name}: {e}")

    return normalized_event_prefix


def split_asset_key(s3_key: str, base_prefix: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Split an object key into (assetId, asset-relative path) for a bucket registered
    at ``base_prefix``.

    Returns ``(None, None)`` when the key carries no path below the asset folder.
    Splitting the raw key instead of the prefix-relative remainder yields the prefix's
    first segment as the asset ID and leaves the asset ID inside the relative path —
    neither of which matches a document, which is indexed with the asset-relative
    ``str_key`` and the bare asset ID.
    """
    normalized = normalize_bucket_prefix(base_prefix) or ''
    remainder = s3_key[len(normalized):] if normalized and s3_key.startswith(normalized) else s3_key
    parts = remainder.lstrip('/').split('/')
    if len(parts) < 2 or not parts[0]:
        return None, None
    return parts[0], '/' + '/'.join(parts[1:])


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


def get_asset_details_any_state(database_id: str, asset_id: str) -> Tuple[Optional[Dict[str, Any]], bool]:
    """Get asset details from the live partition, falling back to the archived one.

    Archiving an asset moves its record to the `{databaseId}#deleted` partition,
    but the S3 object metadata (and therefore the file index document IDs) keep
    the live database_id. File events arriving for an archived asset must still
    resolve the asset record so their documents can be (re)indexed with the
    archived flag rather than silently skipped.

    Returns:
        Tuple of (asset_details, asset_is_archived). (None, False) if the record
        exists in neither partition.
    """
    asset_details = get_asset_details(database_id, asset_id)
    if asset_details:
        return asset_details, False
    if database_id.endswith('#deleted'):
        return None, False
    archived_details = get_asset_details(f"{database_id}#deleted", asset_id)
    if archived_details:
        logger.info(f"Asset {asset_id} found in archived partition {database_id}#deleted")
        return archived_details, True
    return None, False

def asset_record_belongs_to_event_bucket(
    item: Dict[str, Any],
    asset_id: str,
    bucket_name: str,
    bucket_prefix: Optional[str]
) -> bool:
    """Whether an ``assetIdGSI`` record points at the bucket and prefix an event came from.

    ``assetId`` is unique within a database, not across databases, so a record identifies
    the database a deleted object belonged to only once its registered bucket name and
    ``baseAssetsPrefix`` agree with the event's. Both sides are canonicalized through
    ``normalize_bucket_prefix``, so the stored spelling (``prefix-a/``, ``''`` at the root)
    and the event spelling (``/prefix-a/``, ``/``) compare equal.

    A record carrying no ``bucketId``, a ``bucketId`` that resolves to no registration, or
    an event whose prefix could not be resolved is unverifiable rather than matching: an
    unknown prefix on one side would otherwise compare equal to an unknown one on the other.
    """
    normalized_event_prefix = normalize_bucket_prefix(bucket_prefix)
    if normalized_event_prefix is None:
        logger.warning(
            f"Cannot verify asset {asset_id} against bucket {bucket_name}: the event "
            "carries no bucket prefix"
        )
        return False

    bucket_id = item.get('bucketId')
    if not bucket_id:
        logger.warning(
            f"Asset record for {asset_id} in database {item.get('databaseId')} carries no "
            f"bucketId; cannot verify it against bucket {bucket_name} prefix "
            f"{normalized_event_prefix!r}"
        )
        return False

    bucket_details = get_bucket_details(bucket_id)
    if not bucket_details:
        logger.warning(
            f"Asset record for {asset_id} in database {item.get('databaseId')} points at "
            f"unresolvable bucketId {bucket_id}; cannot verify it against bucket "
            f"{bucket_name} prefix {normalized_event_prefix!r}"
        )
        return False

    item_bucket_name = bucket_details.get('bucketName')
    item_bucket_prefix = normalize_bucket_prefix(bucket_details.get('baseAssetsPrefix') or '/')
    if item_bucket_name == bucket_name and item_bucket_prefix == normalized_event_prefix:
        logger.info(
            f"Bucket match found: database_id={item.get('databaseId')}, "
            f"bucket={item_bucket_name}, prefix={item_bucket_prefix!r}"
        )
        return True

    logger.warning(
        f"Asset record for {asset_id} in database {item.get('databaseId')} belongs to "
        f"bucket {item_bucket_name} prefix {item_bucket_prefix!r}, not to the event's "
        f"bucket {bucket_name} prefix {normalized_event_prefix!r}"
    )
    return False


def lookup_database_id_for_permanent_delete(
    asset_id: str,
    bucket_name: str,
    bucket_prefix: Optional[str]
) -> Tuple[Optional[str], bool]:
    """
    Lookup database_id for permanently deleted file using 3-step process:
    1. Query assetIdGSI with just asset_id
    2. Keep only the results whose registered bucket and prefix match the event's
    3. If zero or more than one remains, return error

    A single result is verified against the event's bucket the same way multiple results
    are: ``assetId`` is not unique across databases, so an unverified single match
    resolves the document of a live asset in another database.

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
        
        items = query_all_pages(
            asset_storage_table,
            IndexName='assetIdGSI',
            KeyConditionExpression=Key('assetId').eq(asset_id)
        )

        if len(items) == 0:
            logger.warning(f"No assets found with asset_id: {asset_id}")
            return None, False

        if len(items) == 1:
            # Single match - usable only once it is confirmed to belong to the bucket and
            # prefix the event came from, otherwise the exact-_id delete the caller runs
            # next removes another database's live document. The record may live in the
            # archived partition ({databaseId}#deleted); document IDs always use the live
            # database_id, so strip the suffix.
            if not asset_record_belongs_to_event_bucket(items[0], asset_id, bucket_name, bucket_prefix):
                logger.error(
                    f"Single asset match for {asset_id} does not belong to bucket "
                    f"{bucket_name} with prefix {bucket_prefix}, cannot determine database_id"
                )
                return None, False
            database_id = items[0].get('databaseId')
            if database_id and database_id.endswith('#deleted'):
                database_id = database_id[:-len('#deleted')]
            logger.info(f"Found single asset match for {asset_id}, database_id: {database_id}")
            return database_id, True

        # Step 2: Multiple matches - filter by bucket
        logger.info(f"Found {len(items)} assets with asset_id {asset_id}, filtering by bucket")

        matching_assets = [
            item for item in items
            if asset_record_belongs_to_event_bucket(item, asset_id, bucket_name, bucket_prefix)
        ]

        if len(matching_assets) == 1:
            # Single match after bucket filtering (strip archived-partition suffix)
            database_id = matching_assets[0].get('databaseId')
            if database_id and database_id.endswith('#deleted'):
                database_id = database_id[:-len('#deleted')]
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

    The metadata table records use ``metadataKey``/``metadataValue``/``metadataValueType``.
    The attribute table records may use ``attributeKey``/``attributeValue``/``attributeValueType``
    OR fall back to the legacy ``metadataKey``/``metadataValue``/``metadataValueType`` field
    names (mirroring the read path in handlers.metadata.metadataService).

    The metadata and attribute reads are isolated so a failure in one branch does
    not wipe the other

    Returns:
        Tuple of (metadata_dict, attributes_dict) where:
        - Keys are just the field names (no MD_/AB_ or type prefixes)
        - Values are normalized (strings, numbers, booleans, dates)
    """
    composite_key = f"{database_id}:{asset_id}:{file_path}"
    metadata: Dict[str, Any] = {}
    attributes: Dict[str, Any] = {}

    # --- Metadata table ---
    try:
        items = query_all_pages(
            asset_file_metadata_table,
            IndexName='DatabaseIdAssetIdFilePathIndex',
            KeyConditionExpression=Key('databaseId:assetId:filePath').eq(composite_key)
        )
        for item in items:
            metadata_key = item.get('metadataKey')
            metadata_value = item.get('metadataValue')
            metadata_value_type = item.get('metadataValueType')

            # Skip system metadata records that conflict with OpenSearch field mappings
            if is_excluded_metadata_record(metadata_key):
                logger.debug(f"Skipping system metadata: {metadata_key}")
                continue

            # Accept any non-None value (including "", 0, False after normalization).
            # Drop only records that have no key at all.
            if metadata_key and metadata_value is not None:
                metadata[metadata_key] = normalize_metadata_value(
                    metadata_value, metadata_value_type
                )

        logger.info(
            f"Loaded {len(metadata)} metadata fields (from {len(items)} records) "
            f"for {composite_key}"
        )
    except Exception as e:
        logger.exception(
            f"Error reading file metadata for {composite_key} from "
            f"{asset_file_metadata_table_name}: {e}"
        )

    # --- Attribute table ---
    try:
        items = query_all_pages(
            file_attribute_table,
            IndexName='DatabaseIdAssetIdFilePathIndex',
            KeyConditionExpression=Key('databaseId:assetId:filePath').eq(composite_key)
        )
        for item in items:
            # Field-name fallback matches handlers.metadata.metadataService — records
            # in the attribute table may have been written with either the
            # attribute* or metadata* attribute names depending on writer version.
            attribute_key = item.get('attributeKey') or item.get('metadataKey')
            attribute_value = (
                item.get('attributeValue')
                if item.get('attributeValue') is not None
                else item.get('metadataValue')
            )
            attribute_value_type = (
                item.get('attributeValueType') or item.get('metadataValueType')
            )

            if attribute_key and attribute_value is not None:
                attributes[attribute_key] = normalize_metadata_value(
                    attribute_value, attribute_value_type
                )

        logger.info(
            f"Loaded {len(attributes)} attribute fields (from {len(items)} records) "
            f"for {composite_key}"
        )
    except Exception as e:
        logger.exception(
            f"Error reading file attributes for {composite_key} from "
            f"{file_attribute_table_name}: {e}"
        )

    return metadata, attributes

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
                if not is_system_metadata_key(key):
                    file_info[f"s3_{key}"] = value
                if key in SEARCHABLE_VAMS_METADATA_KEYS:  # We do want to add this vams metadata key to search.
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

def find_preview_file_key(bucket_name: str, s3_key: str) -> str:
    """Check if a .previewFile.* exists for this file in S3.

    Preview files are stored as ``{s3_key}.previewFile.{ext}`` (e.g.
    ``myasset/photo.e57.previewFile.gif``).

    Args:
        bucket_name: The S3 bucket name.
        s3_key: The full S3 key of the source file.

    Returns:
        The S3 key of the preview file if found, or empty string if not.
    """
    try:
        prefix = s3_key + PREVIEW_FILE_PATTERN
        response = s3_client.list_objects_v2(
            Bucket=bucket_name,
            Prefix=prefix,
            MaxKeys=5
        )
        for obj in response.get('Contents', []):
            key = obj.get('Key', '')
            if key.startswith(prefix):
                return key
        return ''
    except Exception as e:
        logger.warning(f"Error checking for preview file for {s3_key}: {e}")
        return ''


def extract_file_extension(file_path: str) -> Optional[str]:
    """Extract file extension from file path.

    Read from the basename, so a dot in a parent folder name (`/folder.v2/LICENSE`)
    is not mistaken for an extension. A file with no extension has none.
    """
    if file_path.endswith('/'):
        return None
    basename = os.path.basename(file_path)
    if '.' not in basename:
        return None
    return basename.split('.')[-1].lower()

def is_folder_path(file_path: str) -> bool:
    """Check if path represents a folder.

    Folder-ness is decided from the key shape alone. A missing filename extension
    does not mean a folder: `LICENSE`, `Dockerfile`, `Makefile` and extension-less
    data exports are ordinary files, and upload validation accepts them.
    """
    return file_path.endswith('/')

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

    # Check for an associated preview file in S3
    bucket_name = bucket_details.get('bucketName', '')
    if bucket_name and request.s3Key:
        doc.str_previewfilekey = find_preview_file_key(bucket_name, request.s3Key)
    else:
        doc.str_previewfilekey = ''
    
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

    # Derive geo_MD_location from metadata (location key takes priority over lat/lon/alt).
    # File metadata wins over file attributes when both contain location data.
    geo_shape = build_geo_location(file_metadata) or build_geo_location(file_attributes)
    if geo_shape is not None:
        doc.geo_MD_location = geo_shape

    return doc

#######################
# OpenSearch Operations
#######################

# OpenSearch refuses a document _id longer than 512 bytes.
MAX_OPENSEARCH_DOCUMENT_ID_BYTES = 512


def build_file_document_id(database_id: str, asset_id: str, file_path: str) -> str:
    """Build the OpenSearch _id of a file document.

    The id is ``{databaseId}#{assetId}#{filePath}``. S3 allows an object key of
    up to 1024 bytes while OpenSearch refuses an _id over 512 bytes, so an id
    that does not fit is shortened to a byte-truncated prefix plus a digest of
    the full id. The digest is derived from the three components alone, so the
    index and delete paths address the same document for any path length.
    """
    doc_id = f"{database_id}#{asset_id}#{file_path}"
    encoded = doc_id.encode('utf-8')
    if len(encoded) <= MAX_OPENSEARCH_DOCUMENT_ID_BYTES:
        return doc_id

    digest = hashlib.sha256(encoded).hexdigest()
    prefix_budget = MAX_OPENSEARCH_DOCUMENT_ID_BYTES - len(digest) - 1
    prefix = encoded[:prefix_budget].decode('utf-8', errors='ignore')
    return f"{prefix}#{digest}"


def _is_invalid_geo_shape_error(error: Exception) -> bool:
    """Detect OpenSearch's mapper_parsing_exception for an invalid geo_shape.

    A degenerate polygon (self-intersecting, zero-area, coincident edges) drawn
    in the metadata map editor surfaces as a 400 mapper_parsing_exception. We
    don't want one bad geometry to block the rest of the document from being
    indexed, so callers retry without the geo field.
    """
    msg = str(error)
    return (
        "mapper_parsing_exception" in msg
        and ("invalid_shape_exception" in msg or "geo_shape" in msg)
    )


def index_file_document(document: FileDocumentModel) -> bool:
    """Index a file document in OpenSearch with retry logic for 429 errors.

    If OpenSearch rejects the document because of a malformed geo_MD_location
    shape (e.g. a self-intersecting polygon authored in the metadata map
    editor), we retry once without the geo field so the rest of the document --
    including MD_ / AB_ metadata -- still lands in the index. The bad geometry
    is logged so it can be cleaned up.
    """
    try:
        if not opensearch_manager.is_available():
            raise VAMSGeneralErrorResponse("OpenSearch client not available")

        client = opensearch_manager.get_client()

        # Create document ID from key components
        doc_id = build_file_document_id(document.str_databaseid, document.str_assetid, document.str_key)

        # Convert document to dict for indexing
        doc_dict = document.dict(exclude_unset=True)

        # Diagnostic: log the top-level keys actually being sent to OpenSearch so we can
        # confirm MD_ / AB_ / geo_MD_location are present on the indexed body.
        md_count = len(doc_dict.get('MD_', {})) if isinstance(doc_dict.get('MD_'), dict) else 0
        ab_count = len(doc_dict.get('AB_', {})) if isinstance(doc_dict.get('AB_'), dict) else 0
        logger.info(
            f"Indexing file doc {doc_id}: keys={sorted(doc_dict.keys())}, "
            f"MD_ fields={md_count}, AB_ fields={ab_count}, "
            f"geo_MD_location={'present' if doc_dict.get('geo_MD_location') else 'absent'}"
        )

        try:
            response = opensearch_operation_with_retry(
                lambda: client.index(
                    index=opensearch_file_index,
                    id=doc_id,
                    body=doc_dict,
                ),
                operation_name=f"index file {doc_id}",
            )
        except Exception as e:
            # Drop the geo field and retry so a single malformed shape doesn't
            # also wipe out the rest of the document's metadata fields.
            if _is_invalid_geo_shape_error(e) and "geo_MD_location" in doc_dict:
                bad_geo = doc_dict.pop("geo_MD_location", None)
                logger.warning(
                    f"OpenSearch rejected geo_MD_location for {doc_id}: {e}. "
                    f"Retrying without the geo field. Bad shape: {bad_geo}"
                )
                response = opensearch_operation_with_retry(
                    lambda: client.index(
                        index=opensearch_file_index,
                        id=doc_id,
                        body=doc_dict,
                    ),
                    operation_name=f"index file {doc_id} (geo dropped)",
                )
            else:
                raise

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
        doc_id = build_file_document_id(database_id, asset_id, file_path)
        
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


def delete_file_documents_by_asset_and_path(asset_id: str, file_path: str,
                                            bucket_name: str,
                                            bucket_prefix: Optional[str]) -> int:
    """Delete file documents matched by asset ID + file path when the database
    ID cannot be resolved.

    Once an asset record is permanently deleted from DynamoDB, the trailing S3
    version-delete events can no longer resolve the database_id, so documents
    cannot be addressed by exact _id. Search the file index for documents with
    the matching asset ID, path, bucket and bucket prefix, and delete them by
    their _id.

    ``str_key`` is the asset-relative path, so asset ID + path + bucket name alone
    also matches a live document belonging to a different database backed by the
    same bucket under a different ``baseAssetsPrefix``. The prefix is therefore
    part of the filter, and the cleanup is skipped when it is unknown rather than
    run unscoped.

    Returns:
        Number of documents deleted (0 if none matched or the prefix is unknown).
    """
    try:
        normalized_prefix = normalize_bucket_prefix(bucket_prefix)
        if normalized_prefix is None:
            logger.warning(
                f"Skipping orphan file-document cleanup for {asset_id}{file_path}: "
                "the event carries no bucket prefix, so the match cannot be scoped "
                "to one database"
            )
            return 0

        if not opensearch_manager.is_available():
            raise VAMSGeneralErrorResponse("OpenSearch client not available")

        client = opensearch_manager.get_client()

        query = {
            "size": 100,
            "query": {
                "bool": {
                    "filter": [
                        {"term": {"str_assetid.keyword": asset_id}},
                        {"term": {"str_key.keyword": file_path}},
                        {"term": {"str_bucketname.keyword": bucket_name}},
                        {"term": {"str_bucketprefix.keyword": normalized_prefix}},
                    ]
                }
            }
        }

        # Drain all matches page by page. The exact asset+path+bucket filter
        # normally matches only a handful of documents (one per database that
        # ever mapped this asset), but don't assume a single page. Deleted
        # documents may still appear in search results until the next index
        # refresh, so stop as soon as a round yields no NEW document IDs.
        deleted = 0
        seen_ids = set()
        max_rounds = 50
        for _ in range(max_rounds):
            response = opensearch_operation_with_retry(
                lambda: client.search(index=opensearch_file_index, body=query),
                operation_name=f"search orphaned file docs for {asset_id}{file_path}"
            )
            new_ids = [h['_id'] for h in response.get('hits', {}).get('hits', [])
                       if h.get('_id') and h['_id'] not in seen_ids]
            if not new_ids:
                break
            for doc_id in new_ids:
                seen_ids.add(doc_id)
                opensearch_operation_with_retry(
                    lambda doc_id=doc_id: client.delete(
                        index=opensearch_file_index,
                        id=doc_id,
                        ignore=[404]
                    ),
                    operation_name=f"delete orphaned file doc {doc_id}"
                )
                deleted += 1
                logger.info(f"Deleted orphaned file document: {doc_id}")
        else:
            logger.warning(
                f"Orphan cleanup for {asset_id}{file_path} hit the round cap ({max_rounds}); "
                "remaining documents will be cleaned on subsequent events"
            )

        return deleted
    except Exception as e:
        logger.exception(f"Error deleting orphaned file documents for {asset_id}{file_path}: {e}")
        return 0

#######################
# Business Logic Functions
#######################

def validate_s3_key(name: str, value: str) -> Tuple[bool, str]:
    """Bound an S3 object key at the limit S3 itself enforces.

    S3's 1024 limit applies to the UTF-8 encoding of the key, so the bound is
    measured in bytes: a path built from multi-byte characters can satisfy a
    character count and still be refused by S3.
    """
    if not isinstance(value, str) or not value:
        return (False, name + " is a required field.")
    if len(value.encode('utf-8')) > MAX_S3_KEY_LENGTH:
        return (False, name + " must be at most " + str(MAX_S3_KEY_LENGTH) + " bytes")
    return (True, '')


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
            }
        })
        # s3Key carries the full object key, bounded by S3's own key limit.
        if valid:
            (valid, message) = validate_s3_key('s3Key', request.s3Key)
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
                documentId=build_file_document_id(request.databaseId, request.assetId, request.filePath),
                indexName=opensearch_file_index,
                operation="delete"
            )
        
        elif request.operation == "index":
            # Get asset details. Resolution falls back to the archived partition
            # (record may be mid-move during archive/unarchive); the document's
            # archived state is governed by the request/S3 state, not by which
            # partition the record was found in.
            asset_details, _ = get_asset_details_any_state(request.databaseId, request.assetId)
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
            
            doc_id = build_file_document_id(request.databaseId, request.assetId, request.filePath)

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
        if excluded_patterns and any(pattern in s3_key for pattern in excluded_patterns):
            logger.info(f"Ignoring file with excluded pattern from indexing: {s3_key}")
            return IndexOperationResponse(
                success=True,
                message="Skipped excluded pattern file",
                indexName=opensearch_file_index,
                operation="skip"
            )

        # Check if any path component is a reserved excluded folder (after any bucket prefix).
        path_parts = s3_key.split('/')
        for part in path_parts:
            if part in excluded_prefixes:
                logger.info(f"Ignoring excluded patterns or prefixes (pipeline, preview, temp-upload file, etc.) from indexing: {s3_key}")
                return IndexOperationResponse(
                    success=True,
                    message="Skipped excluded patterns or prefix files",
                    indexName=opensearch_file_index,
                    operation="skip"
                )

        # Special case: .previewFile. changes should trigger re-indexing of the base file
        # so the base file's str_previewfilekey field stays in sync.
        # This covers both creation and deletion of preview files.
        # Placed after excluded_prefixes check so preview files under excluded
        # prefixes (e.g. pipelines/) are still ignored.
        is_preview_rewrite = False
        if PREVIEW_FILE_PATTERN in s3_key:
            base_file_key = s3_key.split(PREVIEW_FILE_PATTERN)[0]
            logger.info(f"Preview file event detected: {s3_key}, checking base file: {base_file_key}")

            # Check if the base file exists in S3
            try:
                s3_client.head_object(Bucket=bucket_name, Key=base_file_key)
                # Base file exists — rewrite s3_key so we re-index the base file instead.
                # find_preview_file_key() in build_file_document will then correctly
                # detect whether the preview file is present or absent.
                logger.info(f"Base file exists, re-indexing base file: {base_file_key}")
                s3_key = base_file_key
                is_preview_rewrite = True
            except ClientError as e:
                if e.response['Error']['Code'] in ('404', 'NoSuchKey'):
                    logger.info(f"No base file found for preview file {s3_key}, skipping")
                    return IndexOperationResponse(
                        success=True,
                        message="Preview file with no base file, skipping",
                        indexName=opensearch_file_index,
                        operation="skip"
                    )
                else:
                    logger.warning(f"Error checking base file for preview: {e}")
                    return IndexOperationResponse(
                        success=False,
                        message=f"Error checking base file: {str(e)}",
                        indexName=opensearch_file_index,
                        operation="error"
                    )
        
        # When True, the live S3 object state overrides the asset record's
        # archived state for this document (set on delete-marker-removal events).
        force_live = False

        # Handle ObjectRemoved:Delete events specially.
        # Skip this branch when we rewrote a preview file to its base file —
        # the base file still exists and we just need to re-index it.
        if "Delete" in event_name and not is_preview_rewrite:
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

                # Only the CURRENT (IsLatest) entry decides archived state. A
                # non-latest delete marker buried under a live version (e.g. a
                # file re-uploaded or unarchived after an earlier delete) does
                # not make the file archived — and events can arrive out of
                # order, so the live S3 state, not the event, is authoritative.
                has_delete_marker = any(
                    marker['Key'] == s3_key and marker.get('IsLatest')
                    for marker in delete_markers
                )
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
                            asset_id = s3_metadata.get(ASSET_ID_METADATA_KEY)
                            database_id = s3_metadata.get(DATABASE_ID_METADATA_KEY)
                            
                            if asset_id and database_id:
                                # File is archived - need to index with archived flag
                                # Get asset details to calculate relative path. The
                                # asset itself may be archived (asset-archive flow
                                # creates these delete markers), so fall back to the
                                # archived partition — the file doc must still be
                                # flipped to archived rather than skipped.
                                asset_details, _ = get_asset_details_any_state(database_id, asset_id)
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
                                    documentId=build_file_document_id(database_id, asset_id, relative_path),
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
                elif not has_delete_marker and has_versions:
                    # Delete marker was removed but live versions remain — this is
                    # a file/asset unarchive (marker removal emits ObjectRemoved
                    # events). The file is live again: re-index it rather than
                    # deleting its document.
                    try:
                        live_head = s3_client.head_object(Bucket=bucket_name, Key=s3_key)
                    except ClientError as head_err:
                        if head_err.response['Error']['Code'] in ('404', 'NoSuchKey', '405'):
                            live_head = None
                        else:
                            raise
                    if live_head is None:
                        # Versions exist but none is current (shouldn't normally
                        # happen without a delete marker) — nothing indexable.
                        logger.warning(f"No current version for {s3_key} despite versions; skipping")
                        return IndexOperationResponse(
                            success=True,
                            message="No current version for file, skipping",
                            indexName=opensearch_file_index,
                            operation="skip"
                        )
                    s3_metadata = live_head.get('Metadata', {})
                    asset_id = s3_metadata.get(ASSET_ID_METADATA_KEY)
                    database_id = s3_metadata.get(DATABASE_ID_METADATA_KEY)
                    if not asset_id or not database_id:
                        logger.warning(f"Missing asset/database ID in S3 metadata for restored file {s3_key}")
                        return IndexOperationResponse(
                            success=True,
                            message="Missing metadata on restored file, skipping",
                            indexName=opensearch_file_index,
                            operation="skip"
                        )
                    # A permanent-delete burst emits one ObjectRemoved per version;
                    # an early event can observe the key still live mid-burst. If
                    # the asset record is gone from both partitions this is a
                    # delete in progress, not an unarchive — do not re-index.
                    restored_asset, _ = get_asset_details_any_state(database_id, asset_id)
                    if not restored_asset:
                        logger.info(
                            f"Object {s3_key} live but asset {asset_id} record gone; "
                            "skipping re-index (delete in progress)"
                        )
                        return IndexOperationResponse(
                            success=True,
                            message="Asset record gone for live object, skipping",
                            indexName=opensearch_file_index,
                            operation="skip"
                        )
                    logger.info(f"Delete marker removed and object live again, re-indexing: {s3_key}")
                    operation = "index"
                    is_archived = False
                    # The live S3 object is authoritative here: during an asset
                    # unarchive the markers are removed before the DynamoDB record
                    # moves back to the live partition, so a stale archived-partition
                    # record must not flip this document back to archived.
                    force_live = True
                else:
                    # File is permanently deleted (no versions remain)
                    logger.info(f"File is permanently deleted: {s3_key}")

                    # Object keys are {baseAssetsPrefix}{assetId}/{filePath}, the same
                    # asset_base_key shape the indexing path builds, so the registered
                    # prefix comes off before the first remaining component is the asset
                    # ID. Splitting the raw key reads a non-root bucket's prefix segment
                    # as the asset ID and leaves the asset ID inside the relative path,
                    # matching no document.
                    event_bucket_name = event_record.get('ASSET_BUCKET_NAME') or bucket_name
                    event_bucket_prefix = resolve_registered_bucket_prefix(
                        event_bucket_name, s3_key, event_record.get('ASSET_BUCKET_PREFIX')
                    )
                    potential_asset_id, relative_path = split_asset_key(s3_key, event_bucket_prefix)

                    if potential_asset_id:
                        database_id, lookup_success = lookup_database_id_for_permanent_delete(
                            potential_asset_id,
                            event_bucket_name,
                            event_bucket_prefix if event_bucket_prefix is not None else '/'
                        )

                        if lookup_success and database_id:
                            logger.info(f"Successfully looked up database_id {database_id} for permanently deleted file")
                            asset_id = potential_asset_id

                            # For permanent deletes, directly delete from OpenSearch
                            success = delete_file_document(database_id, asset_id, relative_path)

                            return IndexOperationResponse(
                                success=success,
                                message="Permanently deleted file removed from index" if success else "Failed to delete file document",
                                documentId=build_file_document_id(database_id, asset_id, relative_path),
                                indexName=opensearch_file_index,
                                operation="delete"
                            )
                        else:
                            # Asset record already gone (e.g. asset permanent delete
                            # removed DynamoDB before the trailing S3 version-delete
                            # events processed). Fall back to matching documents by
                            # asset ID + path so no orphaned documents remain in the
                            # index. The prefix scopes that match to one database; a
                            # None here means no registration for this bucket could be
                            # resolved at all, and delete_file_documents_by_asset_and_path
                            # declines rather than searching unscoped.
                            deleted_count = delete_file_documents_by_asset_and_path(
                                potential_asset_id, relative_path, event_bucket_name,
                                event_bucket_prefix
                            )
                            logger.warning(
                                f"Cannot determine database_id for permanently deleted file: {s3_key}; "
                                f"removed {deleted_count} orphaned document(s) by asset/path match"
                            )
                            return IndexOperationResponse(
                                success=True,
                                message=f"Removed {deleted_count} orphaned document(s) for unresolvable file" if deleted_count
                                        else "Cannot identify permanently deleted file, skipping",
                                indexName=opensearch_file_index,
                                operation="delete" if deleted_count else "skip"
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
                
                asset_id = s3_metadata.get(ASSET_ID_METADATA_KEY)
                database_id = s3_metadata.get(DATABASE_ID_METADATA_KEY)

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
        # This requires getting the asset details to determine the base prefix.
        # The asset may be archived (record moved to {databaseId}#deleted) while
        # its files still receive events — resolve from either partition and
        # carry the asset's archived state onto the file document.
        asset_details, asset_is_archived = get_asset_details_any_state(database_id, asset_id)
        if not asset_details:
            logger.warning(f"Asset not found for S3 file: {database_id}/{asset_id}")
            return IndexOperationResponse(
                success=True,
                message="Asset not found, skipping",
                indexName=opensearch_file_index,
                operation="skip"
            )
        if asset_is_archived and not force_live:
            is_archived = True
        
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

            # Skip preview files (.previewFile.*). Unlike the S3-event path, the
            # metadata stream does not rewrite a preview file to its base file, so
            # indexing one here would create a standalone document. The base file's
            # str_previewfilekey is kept in sync by the S3-event path instead.
            if PREVIEW_FILE_PATTERN in file_path:
                logger.info(f"Preview file metadata REMOVE, skipping for file index: {file_path}")
                return IndexOperationResponse(
                    success=True,
                    message="Preview file, skipping",
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

        # Skip preview files (.previewFile.*). Unlike the S3-event path, the
        # metadata stream does not rewrite a preview file to its base file, so
        # indexing one here would create a standalone document. The base file's
        # str_previewfilekey is kept in sync by the S3-event path instead.
        if PREVIEW_FILE_PATTERN in file_path:
            logger.info(f"Preview file metadata, skipping for file index: {file_path}")
            return IndexOperationResponse(
                success=True,
                message="Preview file, skipping",
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

def batch_item_identifier(record: Dict[str, Any]) -> Optional[str]:
    """Return the partial-batch identifier for an event-source record.

    An SQS record is identified by its messageId, a DynamoDB stream record by the
    shard record's SequenceNumber. Returns None for a record that carries neither
    (a hand-built or direct invocation), where partial-batch reporting does not
    apply.
    """
    message_id = record.get('messageId')
    if message_id:
        return message_id

    sequence_number = (record.get('dynamodb') or {}).get('SequenceNumber')
    if sequence_number:
        return sequence_number

    return None


def all_batch_item_failures(event) -> List[Dict[str, str]]:
    """Identify every record in the event, for reporting a whole batch as failed.

    Used when the failure is not attributable to one record. Re-processing an
    already-indexed record is harmless (indexing is an upsert keyed by the
    document id), so redriving the whole batch is the safe direction.
    """
    failures = []
    if not isinstance(event, dict):
        return failures
    for record in (event.get('Records') or []):
        identifier = batch_item_identifier(record)
        if identifier:
            failures.append({'itemIdentifier': identifier})
    return failures


def with_batch_item_failures(response, event, failures: List[Dict[str, str]]):
    """Attach the partial-batch failure report to an event-source response.

    A response that carries no `batchItemFailures` is a whole-batch SUCCESS to the
    event-source mapping, so every exit path of an event-source invocation must
    report, including the error ones.
    """
    if isinstance(event, dict) and 'Records' in event:
        if failures:
            logger.warning(f"Reporting {len(failures)} failed record(s) for redrive")
        response['batchItemFailures'] = failures
    return response


def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for file indexing operations"""
    global claims_and_roles

    try:
        logger.info(f"Processing file indexing event: {json.dumps(event, default=str)}")

        results = []
        batch_item_failures: List[Dict[str, str]] = []

        # Extract bucket info from top-level event (if present)
        asset_bucket_name = event.get('ASSET_BUCKET_NAME')
        asset_bucket_prefix = event.get('ASSET_BUCKET_PREFIX', '/')

        # Handle different event sources
        if 'Records' in event:
            for record in event['Records']:
                record_results_start = len(results)
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
                                # sqsBucketSync stamps the bucket identity onto the SNS
                                # payload, not onto the SQS envelope, so read it from the
                                # message and fall back to the top-level event.
                                sns_bucket_name = sns_message.get('ASSET_BUCKET_NAME') or asset_bucket_name
                                sns_bucket_prefix = sns_message.get('ASSET_BUCKET_PREFIX')
                                if sns_bucket_prefix is None:
                                    sns_bucket_prefix = asset_bucket_prefix
                                for inner_record in sns_message['Records']:
                                    inner_event_source = inner_record.get('eventSource', '')

                                    if inner_event_source == 'aws:s3':
                                        # Direct S3 record in SNS message
                                        if sns_bucket_name:
                                            inner_record['ASSET_BUCKET_NAME'] = sns_bucket_name
                                            inner_record['ASSET_BUCKET_PREFIX'] = sns_bucket_prefix
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

                if any(not r.success for r in results[record_results_start:]):
                    identifier = batch_item_identifier(record)
                    if identifier:
                        batch_item_failures.append({'itemIdentifier': identifier})
                    else:
                        logger.warning(
                            "File indexing failed for a record that carries no messageId or "
                            "SequenceNumber; the failure cannot be reported for redrive"
                        )

        else:
            # Direct invocation with FileIndexRequest
            try:
                request = parse(event, model=FileIndexRequest)
                result = process_file_index_request(request)
                results.append(result)
            except ValidationError as v:
                logger.exception(f"Validation error: {v}")
                return validation_error(body={'message': validation_error_message(v)}, event=event)

        # Summarize results
        successful = sum(1 for r in results if r.success)
        total = len(results)

        response_body = {
            'message': f"Processed {successful}/{total} file indexing operations successfully",
            'results': [r.dict() for r in results]
        }

        # Partial-batch failure report. The event-source mapping redrives only the
        # records whose indexing failed; without it a failed index write is deleted
        # from the queue as if it had been processed.
        return with_batch_item_failures(
            success(body=response_body), event, batch_item_failures)

    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return with_batch_item_failures(
            validation_error(body={'message': validation_error_message(v)}, event=event),
            event, all_batch_item_failures(event))
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return with_batch_item_failures(
            general_error(body={'message': str(v)}, event=event),
            event, all_batch_item_failures(event))
    except Exception as e:
        logger.exception(f"Internal error in file indexer: {e}")
        # The failure is not attributable to one record, so the whole batch is
        # reported: an error response without a failure report deletes every
        # message in it.
        return with_batch_item_failures(
            internal_error(event=event), event, all_batch_item_failures(event))