# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Centralized metadata service handler for VAMS - Handles metadata across all entity types."""

import os
import boto3
import json
import base64
import time
from datetime import datetime
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeDeserializer
from botocore.exceptions import ClientError
from botocore.config import Config
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from common.constants import STANDARD_JSON_RESPONSE
from common.apiRoutes import (
    API_ASSET_LINK_METADATA, API_ASSET_METADATA,
    API_FILE_METADATA, API_DATABASE_METADATA,
)
from common.validators import validate
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from common.metadataSchemaValidation import (
    get_aggregated_schemas,
    validate_metadata_against_schema,
    validate_metadata_keys_against_schema,
    enrich_metadata_with_schema
)
from models.common import APIGatewayProxyResponseV2, internal_error, success, validation_error, general_error, authorization_error, VAMSGeneralErrorResponse, validation_error_message
from handlers.assets.assetVersions import validate_asset_version_exists, get_asset_metadata_version
from models.metadata import (
    # Asset Link Metadata Models
    AssetLinkMetadataPathRequestModel,
    GetAssetLinkMetadataRequestModel,
    CreateAssetLinkMetadataRequestModel,
    UpdateAssetLinkMetadataRequestModel,
    DeleteAssetLinkMetadataRequestModel,
    AssetLinkMetadataResponseModel,
    GetAssetLinkMetadataResponseModel,
    # Asset Metadata Models
    AssetMetadataPathRequestModel,
    GetAssetMetadataRequestModel,
    CreateAssetMetadataRequestModel,
    UpdateAssetMetadataRequestModel,
    DeleteAssetMetadataRequestModel,
    AssetMetadataResponseModel,
    GetAssetMetadataResponseModel,
    # File Metadata Models
    FileMetadataPathRequestModel,
    GetFileMetadataRequestModel,
    CreateFileMetadataRequestModel,
    UpdateFileMetadataRequestModel,
    DeleteFileMetadataRequestModel,
    FileMetadataResponseModel,
    GetFileMetadataResponseModel,
    # Database Metadata Models
    DatabaseMetadataPathRequestModel,
    GetDatabaseMetadataRequestModel,
    CreateDatabaseMetadataRequestModel,
    UpdateDatabaseMetadataRequestModel,
    DeleteDatabaseMetadataRequestModel,
    DatabaseMetadataResponseModel,
    GetDatabaseMetadataResponseModel,
    # Common Models
    BulkOperationResponseModel,
    MetadataValueType,
    UpdateType,
    # Shared limits and pagination defaults
    MAX_METADATA_PAGE_SIZE,
    DEFAULT_METADATA_MAX_ITEMS,
    DEFAULT_METADATA_PAGE_SIZE,
)

# Configure AWS clients with retry configuration
retry_config = Config(
    retries={
        'max_attempts': 5,
        'mode': 'adaptive'
    }
)

region = os.environ.get('AWS_REGION', 'us-east-1')
dynamodb = boto3.resource('dynamodb', config=retry_config)
dynamodb_client = boto3.client('dynamodb', config=retry_config)
s3 = boto3.client('s3', config=retry_config)
logger = safeLogger(service_name="MetadataService")

# Global variables for claims and roles
claims_and_roles = {}

# Constants
MAX_METADATA_RECORDS_PER_ENTITY = 500

# The pagination defaults are imported from models.metadata, where the request models declare them
# as their field defaults: maxItems is the per-response ceiling that keeps the payload under the
# Lambda (6 MB) / API Gateway limits, pageSize is the slice served, and the page is the smaller of
# the two — so a caller supplying only maxItems bounds the response with it and one supplying only
# pageSize keeps the default ceiling. Defining them here as well would let a request that reaches a
# handler through a model disagree with one that does not.

# Returned for a pagination parameter above MAX_METADATA_PAGE_SIZE, the ceiling shared with the
# request models, which carry it as their le= bound. A parameter arriving through a model is
# refused there; resolve_metadata_page_parameter covers a query_params dict built without one.
METADATA_PAGE_SIZE_OUT_OF_RANGE_MESSAGE = (
    f"pageSize and maxItems must each be between 1 and {MAX_METADATA_PAGE_SIZE}"
)

# Bound on re-driving a partially accepted batch. DynamoDB answers HTTP 200 with a
# populated UnprocessedItems map when part of a batch was throttled or exceeded a
# limit -- botocore's retry layer does not see that as an error, so the requests have
# to be re-sent here or they are silently lost.
#
# The bound is what keeps a throttled table from trading data loss for a Lambda
# timeout. Five attempts with 50/100/200/400 ms of backoff cap one chunk at 0.75 s of
# sleep; at the MAX_METADATA_RECORDS_PER_ENTITY ceiling (500 records, 20 chunks) the
# worst case is 15 s against this handler's 15-minute timeout. Exhausting the bound
# raises, so the caller's own failure handling reports the affected keys rather than
# the operation claiming success.
BATCH_WRITE_MAX_ATTEMPTS = 5
BATCH_WRITE_INITIAL_BACKOFF_SECONDS = 0.05

# Returned when the schema-validation block cannot complete. That block carries schema
# conformance, the controlled-list check, the type-change guard and the
# restrictMetadataOutsideSchemas key prohibition, so an infrastructure error inside it
# denies the write instead of letting the metadata through unvalidated. The message states
# the outcome rather than inviting a retry: the causes range from a transient throttle to a
# stored schema this code cannot read, and the guard cannot tell them apart.
SCHEMA_VALIDATION_UNAVAILABLE_MESSAGE = (
    "Metadata could not be validated against the applicable schemas; nothing was written"
)

# Logged when the additive schema-default injection fails. That step only ADDS fields whose
# schema declares a default, and it runs after schema conformance, the controlled-list check
# and the off-schema key prohibition have all passed -- so a failure there loses defaults and
# cannot admit anything the checks refused. It is therefore the one step in the block that stays
# fail-open, which the surrounding fail-closed arm would otherwise convert into a denied write.
SCHEMA_DEFAULT_INJECTION_FAILED_LOG = (
    "Schema default injection failed; the validated metadata is written without "
    "schema-supplied defaults"
)

# Returned when the deletion-validation block cannot complete. That block is the only guard on
# removing metadata a schema governs: a required field may not be deleted, and neither may a
# field that another schema field depends on. Swallowing an error there deleted the keys with no
# validation and answered 200, so a transient DynamoDB error -- or a SchemaLookupError from a
# schema read that did not complete -- silently turned the control off, exactly as it did on the
# write path. Like SCHEMA_VALIDATION_UNAVAILABLE_MESSAGE the text states the outcome rather than
# inviting a retry, and names no request input (Rule 11).
SCHEMA_DELETION_VALIDATION_UNAVAILABLE_MESSAGE = (
    "Metadata deletion could not be validated against the applicable schemas; "
    "nothing was deleted"
)

# Returned when deletion validation REFUSES the deletion. validate_metadata_deletion builds one
# reason per refused key, and every reason names the metadata key the caller asked to delete -- and
# the schema field that depends on it -- so the reasons are logged and the caller gets this generic
# text instead (Rule 11). The prefix is kept so a refusal stays distinguishable from the
# could-not-validate outcome above.
SCHEMA_DELETION_NOT_ALLOWED_MESSAGE = (
    "Deletion validation failed: one or more of the requested metadata keys is required by a "
    "metadata schema, or another schema field depends on it; nothing was deleted"
)

# Load environment variables
try:
    from common.resourceNames import ResourceKeys, get_table_name
    asset_links_table_v2_name = get_table_name(ResourceKeys.ASSET_LINKS_STORAGE_TABLE_V2)
    asset_links_metadata_table_name = get_table_name(ResourceKeys.ASSET_LINKS_METADATA_STORAGE_TABLE)
    asset_storage_table_name = get_table_name(ResourceKeys.ASSET_STORAGE_TABLE)
    database_storage_table_name = get_table_name(ResourceKeys.DATABASE_STORAGE_TABLE)
    database_metadata_table_name = get_table_name(ResourceKeys.DATABASE_METADATA_STORAGE_TABLE)
    asset_file_metadata_table_name = get_table_name(ResourceKeys.ASSET_FILE_METADATA_STORAGE_TABLE)
    file_attribute_table_name = get_table_name(ResourceKeys.FILE_ATTRIBUTE_STORAGE_TABLE)
    s3_asset_buckets_table_name = get_table_name(ResourceKeys.S3_ASSET_BUCKETS_STORAGE_TABLE)
    metadata_schema_table_v2_name = get_table_name(ResourceKeys.METADATA_SCHEMA_STORAGE_TABLE_V2)
except Exception as e:
    logger.exception("Failed resolving resource names")
    raise e

# Initialize DynamoDB tables
asset_links_table = dynamodb.Table(asset_links_table_v2_name)
asset_links_metadata_table = dynamodb.Table(asset_links_metadata_table_name)
asset_storage_table = dynamodb.Table(asset_storage_table_name)
database_storage_table = dynamodb.Table(database_storage_table_name)
database_metadata_table = dynamodb.Table(database_metadata_table_name)
asset_file_metadata_table = dynamodb.Table(asset_file_metadata_table_name)
file_attribute_table = dynamodb.Table(file_attribute_table_name)
s3_asset_buckets_table = dynamodb.Table(s3_asset_buckets_table_name)

#######################
# Common Utility Functions
#######################

class BatchWriteIncompleteError(Exception):
    """DynamoDB still reported UnprocessedItems after the retry bound was exhausted.

    Raised so the caller's existing failure handling runs: the affected keys move from
    successfulItems into failedItems, a REPLACE_ALL rolls back, and the response stops
    claiming a clean write.
    """


def batch_write_with_retry(table_name: str, batch: list) -> None:
    """Write one batch_write_item chunk, re-driving whatever DynamoDB did not accept.

    Every batch write in this handler goes through here. `batch_write_item` answers
    HTTP 200 with the requests it declined in `UnprocessedItems`, so a caller that
    ignores the response reports a partial write as a complete one.

    Args:
        table_name: Target table for every request in the chunk.
        batch: PutRequest/DeleteRequest entries, at most the 25 DynamoDB accepts.

    Raises:
        BatchWriteIncompleteError: Requests remained unprocessed after
            BATCH_WRITE_MAX_ATTEMPTS attempts.
    """
    pending = list(batch)
    if not pending:
        return

    backoff = BATCH_WRITE_INITIAL_BACKOFF_SECONDS
    for attempt in range(1, BATCH_WRITE_MAX_ATTEMPTS + 1):
        response = dynamodb_client.batch_write_item(RequestItems={table_name: pending})
        pending = (response or {}).get('UnprocessedItems', {}).get(table_name) or []
        if not pending:
            return
        logger.warning(
            f"batch_write_item left {len(pending)} of {len(batch)} requests unprocessed on "
            f"{table_name} (attempt {attempt} of {BATCH_WRITE_MAX_ATTEMPTS})"
        )
        if attempt < BATCH_WRITE_MAX_ATTEMPTS:
            # Deliberate exponential backoff between batch_write_item retries. DynamoDB answers a
            # partially accepted batch with UnprocessedItems rather than an error, and the documented
            # handling is to resend only those after a growing delay -- retrying immediately re-hits
            # the same throttle. This is not a sleep left in from debugging.
            #
            # The suppression must be the LAST comment line before the call: semgrep applies
            # `nosemgrep` to the line immediately following it, so an explanation placed in between
            # silently detaches it and the finding returns.
            # nosemgrep: arbitrary-sleep
            time.sleep(backoff)
            backoff *= 2

    logger.error(
        f"batch_write_item could not place {len(pending)} of {len(batch)} requests on "
        f"{table_name} after {BATCH_WRITE_MAX_ATTEMPTS} attempts"
    )
    raise BatchWriteIncompleteError(
        f"{len(pending)} of {len(batch)} items were not written after "
        f"{BATCH_WRITE_MAX_ATTEMPTS} attempts"
    )


# Stored-row attributes that a metadata read tolerates as absent, and the placeholder each is
# validated with before being reported as null. A row written by an earlier release can carry
# neither (handlers/indexing/fileIndexer.py tolerates exactly that shape), which is why the
# DELETE paths and the REPLACE_ALL rollbacks already read them with .get. The GET paths read
# them by subscript, so ONE such row raised KeyError inside the schema-enrichment arm, the
# unenriched fallback raised the same KeyError again, and the entity's whole metadata list
# answered 400 -- withholding the one thing needed to repair it, which is the key. Absent is
# reported as null: the row is neither dropped, which would leave nobody aware it exists, nor
# given a value or a type it does not carry.
TOLERATED_ABSENT_STORED_FIELDS = ('metadataValue', 'metadataValueType')
_ABSENT_FIELD_VALIDATION_PLACEHOLDERS = {
    'metadataValue': '',
    'metadataValueType': MetadataValueType.STRING,
}

# Attributes enrich_metadata_with_schema adds to a row. Read with .get, so the unenriched
# fallback needs no second, differently written conversion -- it leaves them at the response
# model's own defaults. That second copy is where this class kept reappearing.
SCHEMA_ENRICHMENT_RESPONSE_FIELDS = (
    'metadataSchemaName', 'metadataSchemaField', 'metadataSchemaRequired',
    'metadataSchemaSequence', 'metadataSchemaDefaultValue', 'metadataSchemaDependsOn',
    'metadataSchemaMultiFieldConflict', 'metadataSchemaControlledListKeys',
)

# How many metadata keys one aggregated line names. Rule 9: keys are identifiers, so naming
# them is safe where rendering their values would not be -- but an entity can hold
# MAX_METADATA_RECORDS_PER_ENTITY of them, and a line that grows with the data is the same
# volume problem in another shape. The count is always reported in full.
ABSENT_FIELD_LOG_KEY_SAMPLE = 25


def log_absent_stored_fields(absent_keys_by_field: dict, disposition: str) -> None:
    """Report a whole entity's malformed stored rows in one line per absent attribute.

    Reporting per row emitted a line for every legacy row on every metadata request, so an
    upgraded entity's log volume scaled with its row count. The count, plus enough keys to begin
    repairing them, is what an operator needs -- and neither requires a line per row.

    Args:
        absent_keys_by_field: Attribute name -> metadata keys of the rows that lack it.
        disposition: What the caller did about it, restated in the line.
    """
    for field_name in sorted(absent_keys_by_field):
        keys = absent_keys_by_field[field_name]
        if not keys:
            continue
        sample = [str(key) for key in keys[:ABSENT_FIELD_LOG_KEY_SAMPLE]]
        remainder = len(keys) - len(sample)
        more = f" (+{remainder} more)" if remainder > 0 else ""
        logger.warning(
            f"{len(keys)} stored metadata row(s) carry no {field_name}; {disposition}. "
            f"Keys: {', '.join(sample)}{more}")


def _stored_metadata_entry(deserialized: dict, key_fields, value_fields,
                           value_type_fields) -> tuple:
    """One stored metadata row, normalized to (key, {metadataValue, metadataValueType}, absent).

    Each field argument is the ordered list of attribute names that can carry that part of a
    row: file ATTRIBUTE rows store attributeKey/attributeValue/attributeValueType, metadata rows
    store the metadata* names, and a row may carry either.

    An absent attribute is reported back to the caller and evaluated as absent rather than
    raising. Reading one by subscript inside the fail-closed schema-validation arm raised
    KeyError -- which that arm answers with SCHEMA_VALIDATION_UNAVAILABLE_MESSAGE, so one such
    row made every metadata create AND update for the entity a permanent 400: the write that
    would repair the row is the write being refused. A malformed stored row is a different
    condition from a schema lookup that did not complete, and only the second may deny a write.

    Evaluating the attributes as absent keeps the checks conservative rather than lenient: an
    absent value still reads as empty, so a schema-required field still blocks the write, and an
    absent value type only skips the type comparison it has no operand for.

    Reporting is the caller's job so that a whole entity is reported in one line; use
    stored_metadata_entries, which does both.

    Returns:
        Tuple of (key, {'metadataValue': ..., 'metadataValueType': ...}, absent_field_names).
    """
    def first_present(names):
        for name in names:
            if name in deserialized:
                return deserialized[name], True
        return None, False

    key, _ = first_present(key_fields)
    value, has_value = first_present(value_fields)
    value_type, has_value_type = first_present(value_type_fields)

    absent = ([value_fields[0]] if not has_value else []) + (
        [value_type_fields[0]] if not has_value_type else [])

    return key, {'metadataValue': value, 'metadataValueType': value_type}, absent


def stored_metadata_entries(items, key_fields=('metadataKey',),
                            value_fields=('metadataValue',),
                            value_type_fields=('metadataValueType',)) -> dict:
    """One entity's stored metadata rows, keyed by metadata key, reported in one log line.

    Args:
        items: Rows as DynamoDB returns them, in the typed attribute-value shape.
        key_fields: Attribute names that can hold the metadata key, most specific first.
        value_fields: Attribute names that can hold the value.
        value_type_fields: Attribute names that can hold the value type.

    Returns:
        Dict of metadata key -> {'metadataValue': ..., 'metadataValueType': ...}.
    """
    deserializer = TypeDeserializer()
    entries = {}
    absent_keys_by_field = {}
    for item in items:
        deserialized = {k: deserializer.deserialize(v) for k, v in item.items()}
        key, entry, absent = _stored_metadata_entry(
            deserialized, key_fields, value_fields, value_type_fields)
        entries[key] = entry
        for field_name in absent:
            absent_keys_by_field.setdefault(field_name, []).append(key)

    log_absent_stored_fields(
        absent_keys_by_field,
        "evaluating those attributes as absent instead of refusing the operation")
    return entries


def metadata_response_models(model_cls, items, **identity_fields) -> list:
    """Response models for one entity's metadata rows, tolerating absent stored attributes.

    Every metadata GET converts its rows here, whether the schema-enrichment arm completed or
    the unenriched fallback ran, so the stored attribute names have one reader rather than two.

    A row missing metadataValue or metadataValueType is reported with that field null. The rest
    of the row is still validated by the response model: the absent fields are validated with a
    placeholder that is replaced by None before the item is returned, so no placeholder reaches
    a caller and no type is invented for a row that does not carry one.

    Args:
        model_cls: The response model for this entity type.
        items: Metadata row dicts, enriched or raw.
        identity_fields: Entity identity fields shared by every row (databaseId, assetId,
            filePath, assetLinkId), taken from the request path rather than the stored row.

    Returns:
        List of response models, one per row, in the order given.
    """
    response_models = []
    absent_keys_by_field = {}
    for item in items:
        fields = dict(identity_fields)
        fields['metadataKey'] = item.get('metadataKey')
        for field_name in TOLERATED_ABSENT_STORED_FIELDS:
            fields[field_name] = item.get(field_name)
        for field_name in SCHEMA_ENRICHMENT_RESPONSE_FIELDS:
            fields[field_name] = item.get(field_name)

        absent = [name for name in TOLERATED_ABSENT_STORED_FIELDS if fields[name] is None]
        if absent:
            for field_name in absent:
                absent_keys_by_field.setdefault(field_name, []).append(fields['metadataKey'])
            placeholders = {
                name: _ABSENT_FIELD_VALIDATION_PLACEHOLDERS[name] for name in absent}
            validated = model_cls(**dict(fields, **placeholders))
            response_models.append(validated.copy(update={name: None for name in absent}))
        else:
            response_models.append(model_cls(**fields))

    log_absent_stored_fields(
        absent_keys_by_field,
        "returning those attributes as null so the row stays visible for repair")
    return response_models


def resolve_metadata_page_parameter(raw, default: int, name: str) -> int:
    """One metadata pagination parameter, defaulted when absent and bounded when oversized.

    Args:
        raw: The submitted value, as the request model or the query string left it.
        default: Size to use when nothing usable was submitted.
        name: Parameter name, for the log line.

    Returns:
        A size between 1 and MAX_METADATA_PAGE_SIZE.

    Raises:
        VAMSGeneralErrorResponse: The submitted value exceeds MAX_METADATA_PAGE_SIZE.
    """
    if raw is None or raw == '':
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning(f"Metadata {name} is not a number; serving {default}")
        return default
    if value < 1:
        logger.warning(f"Metadata {name} is below 1; serving {default}")
        return default
    if value > MAX_METADATA_PAGE_SIZE:
        logger.warning(
            f"Metadata {name} of {value} exceeds the {MAX_METADATA_PAGE_SIZE} ceiling")
        raise VAMSGeneralErrorResponse(METADATA_PAGE_SIZE_OUT_OF_RANGE_MESSAGE)
    return value


def paginate_metadata_records(records: list, query_params: dict):
    """Offset-paginate an already-enriched, fully-ordered metadata record list.

    Metadata GETs enrich the full record set with schema fields (injecting
    schema-defined fields that have no stored value) and order it by schema
    sequence, so paging happens after enrichment on the in-memory list rather
    than at the DynamoDB cursor level. The page size and ceiling default to
    named constants when not supplied, keeping the response payload bounded
    while preserving correct ordering and schema injection.

    Args:
        records: The fully enriched, ordered list of metadata response items.
        query_params: May contain 'startingToken' (base64 offset), 'pageSize',
            and 'maxItems'.

    Returns:
        Tuple of (page_records, next_token). next_token is None on the last page.

    Raises:
        VAMSGeneralErrorResponse: pageSize or maxItems exceeds MAX_METADATA_PAGE_SIZE.
    """
    # The page is the smaller of the slice asked for and the ceiling allowed, so maxItems
    # bounds the response even though every request model supplies a pageSize default.
    page_size = min(
        resolve_metadata_page_parameter(
            query_params.get('pageSize'), DEFAULT_METADATA_PAGE_SIZE, 'pageSize'),
        resolve_metadata_page_parameter(
            query_params.get('maxItems'), DEFAULT_METADATA_MAX_ITEMS, 'maxItems'),
    )

    # Decode the starting offset from the token (defaults to first page).
    start = 0
    starting_token = query_params.get('startingToken')
    if starting_token:
        try:
            start = int(base64.b64decode(starting_token).decode('utf-8'))
        except (ValueError, base64.binascii.Error, UnicodeDecodeError):
            logger.warning("Invalid metadata startingToken; serving from the first page")
            start = 0
        if start < 0:
            start = 0

    page = records[start:start + page_size]

    next_token = None
    if start + page_size < len(records):
        next_offset = start + page_size
        next_token = base64.b64encode(str(next_offset).encode('utf-8')).decode('utf-8')

    return page, next_token

def get_bucket_details(bucket_id: str) -> dict:
    """Get S3 bucket details from buckets table
    
    Args:
        bucket_id: The bucket ID
        
    Returns:
        Dictionary with bucketName and baseAssetsPrefix
    """
    try:
        response = s3_asset_buckets_table.query(
            KeyConditionExpression=Key('bucketId').eq(bucket_id),
            Limit=1
        )
        bucket = response.get("Items", [{}])[0] if response.get("Items") else {}
        bucket_name = bucket.get('bucketName')
        base_assets_prefix = bucket.get('baseAssetsPrefix', '/')
        
        if not bucket_name:
            raise VAMSGeneralErrorResponse("Bucket configuration not found")
        
        # Ensure prefix ends with slash
        if not base_assets_prefix.endswith('/'):
            base_assets_prefix += '/'
        
        # Remove leading slash
        if base_assets_prefix.startswith('/'):
            base_assets_prefix = base_assets_prefix[1:]
        
        return {
            'bucketName': bucket_name,
            'baseAssetsPrefix': base_assets_prefix
        }
    except Exception as e:
        logger.exception(f"Error getting bucket details: {e}")
        raise VAMSGeneralErrorResponse("Error retrieving bucket configuration")


def check_entity_authorization(entity: dict, action: str, claims_and_roles: dict) -> bool:
    """Check if user has permission to perform action on entity
    
    Args:
        entity: The entity dictionary with object__type
        action: The action to check (GET, POST, PUT, DELETE)
        claims_and_roles: User claims and roles
        
    Returns:
        True if authorized, False otherwise
    """
    try:
        if len(claims_and_roles.get("tokens", [])) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            return casbin_enforcer.enforce(entity, action)
        return False
    except Exception as e:
        logger.exception(f"Error checking authorization: {e}")
        return False


def check_multi_action_authorization(entity: dict, actions: list, claims_and_roles: dict) -> bool:
    """Check if user has ALL specified permissions on entity
    
    Args:
        entity: The entity dictionary with object__type
        actions: List of actions to check (e.g., ["PUT", "POST", "DELETE"])
        claims_and_roles: User claims and roles
        
    Returns:
        True if user has all permissions, False otherwise
    """
    try:
        if len(claims_and_roles.get("tokens", [])) == 0:
            return False
        
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        for action in actions:
            if not casbin_enforcer.enforce(entity, action):
                logger.warning(f"User lacks {action} permission for entity")
                return False
        
        return True
    except Exception as e:
        logger.exception(f"Error checking multi-action authorization: {e}")
        return False


#######################
# Entity Validation Functions
#######################

def validate_asset_link_exists(asset_link_id: str) -> dict:
    """Validate that an asset link exists and return it
    
    Args:
        asset_link_id: The asset link ID
        
    Returns:
        The asset link dictionary
        
    Raises:
        VAMSGeneralErrorResponse: If asset link not found
    """
    try:
        response = asset_links_table.get_item(
            Key={'assetLinkId': asset_link_id}
        )
        
        if 'Item' not in response:
            raise VAMSGeneralErrorResponse("Asset link not found")
        
        return response['Item']
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error validating asset link: {e}")
        raise VAMSGeneralErrorResponse("Error validating asset link")


def validate_asset_exists(database_id: str, asset_id: str) -> dict:
    """Validate that an asset exists and return it
    
    Args:
        database_id: The database ID
        asset_id: The asset ID
        
    Returns:
        The asset dictionary
        
    Raises:
        VAMSGeneralErrorResponse: If asset not found
    """
    try:
        response = asset_storage_table.get_item(
            Key={
                'databaseId': database_id,
                'assetId': asset_id
            }
        )
        
        if 'Item' not in response:
            raise VAMSGeneralErrorResponse("Asset not found")
        
        return response['Item']
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error validating asset: {e}")
        raise VAMSGeneralErrorResponse("Error validating asset")


def validate_file_exists(database_id: str, asset_id: str, file_path: str) -> bool:
    """Validate that a file exists in S3
    
    Args:
        database_id: The database ID
        asset_id: The asset ID
        file_path: The relative file path (with leading slash)
        
    Returns:
        True if file exists
        
    Raises:
        VAMSGeneralErrorResponse: If file not found or validation fails
    """
    try:
        # First get the asset to get bucket and location information
        asset = validate_asset_exists(database_id, asset_id)
        
        # Get bucket details
        bucket_details = get_bucket_details(asset['bucketId'])
        bucket_name = bucket_details['bucketName']
        
        # Get the asset location from the asset details
        if 'assetLocation' not in asset or 'Key' not in asset['assetLocation']:
            raise VAMSGeneralErrorResponse("Asset location not found")
        
        # Use the asset's actual location as the base path
        asset_base_path = asset['assetLocation']['Key']
        
        # Ensure asset base path ends with slash
        if not asset_base_path.endswith('/'):
            asset_base_path += '/'
        
        # Remove leading slash from file_path before combining (to avoid double slash)
        normalized_file_path = file_path.lstrip('/')
        
        # Construct full S3 key using asset's actual location
        full_key = f"{asset_base_path}{normalized_file_path}"
        
        # Check if file exists in S3
        try:
            s3.head_object(Bucket=bucket_name, Key=full_key)
            return True
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchKey' or e.response['Error']['Code'] == '404':
                raise VAMSGeneralErrorResponse("File not found in S3")
            raise
            
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error validating file: {e}")
        raise VAMSGeneralErrorResponse("Error validating file")


def validate_database_exists(database_id: str) -> dict:
    """Validate that a database exists and return it
    
    Args:
        database_id: The database ID
        
    Returns:
        The database dictionary
        
    Raises:
        VAMSGeneralErrorResponse: If database not found
    """
    try:
        response = database_storage_table.get_item(
            Key={'databaseId': database_id}
        )
        
        if 'Item' not in response:
            raise VAMSGeneralErrorResponse("Database not found")
        
        return response['Item']
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error validating database: {e}")
        raise VAMSGeneralErrorResponse("Error validating database")


def get_database_config(database_id: str) -> dict:
    """Get database configuration including restrictMetadataOutsideSchemas
    
    This is an alias for validate_database_exists that emphasizes
    retrieving configuration settings.
    
    Args:
        database_id: The database ID
        
    Returns:
        Database configuration dictionary
        
    Raises:
        VAMSGeneralErrorResponse: If database not found
    """
    return validate_database_exists(database_id)


#######################
# Asset Link Metadata CRUD Operations
#######################

def get_asset_link_metadata(asset_link_id: str, query_params: dict, claims_and_roles: dict) -> GetAssetLinkMetadataResponseModel:
    """Get metadata for an asset link - Returns one page of records
    
    Args:
        asset_link_id: The asset link ID
        query_params: 'pageSize', 'maxItems' and 'startingToken' for the returned page
        claims_and_roles: User claims and roles
        
    Returns:
        GetAssetLinkMetadataResponseModel with one page of metadata records and its NextToken
    """
    try:
        # Validate asset link exists and check authorization
        asset_link = validate_asset_link_exists(asset_link_id)
        
        # Check permissions on both assets
        from_asset = validate_asset_exists(asset_link['fromAssetDatabaseId'], asset_link['fromAssetId'])
        to_asset = validate_asset_exists(asset_link['toAssetDatabaseId'], asset_link['toAssetId'])
        
        from_asset.update({"object__type": "asset"})
        to_asset.update({"object__type": "asset"})
        
        if not (check_entity_authorization(from_asset, "GET", claims_and_roles) and 
                check_entity_authorization(to_asset, "GET", claims_and_roles)):
            raise PermissionError("Not authorized to view metadata for this asset link")
        
        # Fetch every record: the page is sliced after schema enrichment and ordering
        paginator = dynamodb_client.get_paginator('query')
        page_iterator = paginator.paginate(
            TableName=asset_links_metadata_table_name,
            KeyConditionExpression='assetLinkId = :linkId',
            ExpressionAttributeValues={':linkId': {'S': asset_link_id}},
            ScanIndexForward=False
        ).build_full_result()
        
        # Process ALL items
        metadata_list = []
        deserializer = TypeDeserializer()
        for item in page_iterator.get('Items', []):
            deserialized_item = {k: deserializer.deserialize(v) for k, v in item.items()}
            metadata_list.append(deserialized_item)
        
        # Fetch database configs and schema enrichment
        restrict_metadata_outside_schemas = False
        try:
            # Get schemas from both databases + GLOBAL
            database_ids = [asset_link['fromAssetDatabaseId'], asset_link['toAssetDatabaseId'], 'GLOBAL']
            # Remove duplicates while preserving order
            database_ids = list(dict.fromkeys(database_ids))
            
            aggregated_schema = get_aggregated_schemas(
                database_ids=database_ids,
                entity_type='assetLinkMetadata',
                file_path=None,
                dynamodb_client=dynamodb_client,
                schema_table_name=metadata_schema_table_v2_name
            )
            
            # Calculate restrictMetadataOutsideSchemas
            # For asset links: true if EITHER database has restriction AND schemas exist
            schemas_exist = len(aggregated_schema) > 0
            if schemas_exist:
                try:
                    from_db_config = get_database_config(asset_link['fromAssetDatabaseId'])
                    to_db_config = get_database_config(asset_link['toAssetDatabaseId'])
                    
                    from_db_restricts = from_db_config.get('restrictMetadataOutsideSchemas', False) == True
                    to_db_restricts = to_db_config.get('restrictMetadataOutsideSchemas', False) == True
                    
                    restrict_metadata_outside_schemas = from_db_restricts or to_db_restricts
                except Exception as e:
                    logger.warning(f"Error fetching database configs for restriction check: {e}")
                    restrict_metadata_outside_schemas = False
            
            # Enrich metadata with schema information
            enriched_metadata = enrich_metadata_with_schema(metadata_list, aggregated_schema)

            # Convert to response models
            metadata_list = metadata_response_models(
                AssetLinkMetadataResponseModel, enriched_metadata,
                assetLinkId=asset_link_id)
        except Exception as e:
            logger.warning(f"Error enriching metadata with schema: {e}")
            # If schema enrichment fails, return metadata without enrichment
            metadata_list = metadata_response_models(
                AssetLinkMetadataResponseModel, metadata_list,
                assetLinkId=asset_link_id)
            restrict_metadata_outside_schemas = False

        # Offset-paginate the enriched, ordered list to bound the response payload.
        page, next_token = paginate_metadata_records(metadata_list, query_params)

        # Build response
        result = GetAssetLinkMetadataResponseModel(
            metadata=page,
            restrictMetadataOutsideSchemas=restrict_metadata_outside_schemas,
            NextToken=next_token
        )

        return result
        
    except PermissionError as p:
        raise p
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error getting asset link metadata: {e}")
        raise VAMSGeneralErrorResponse("Error retrieving metadata")


def create_asset_link_metadata(asset_link_id: str, request_model: CreateAssetLinkMetadataRequestModel, claims_and_roles: dict) -> BulkOperationResponseModel:
    """Create metadata for an asset link (bulk operation) - Now supports upsert (create or update)
    
    Args:
        asset_link_id: The asset link ID
        request_model: The create request model with metadata items
        claims_and_roles: User claims and roles
        
    Returns:
        BulkOperationResponseModel with operation results
    """
    try:
        # Validate asset link exists and check authorization
        asset_link = validate_asset_link_exists(asset_link_id)
        
        # Check permissions on both assets
        from_asset = validate_asset_exists(asset_link['fromAssetDatabaseId'], asset_link['fromAssetId'])
        to_asset = validate_asset_exists(asset_link['toAssetDatabaseId'], asset_link['toAssetId'])
        
        from_asset.update({"object__type": "asset"})
        to_asset.update({"object__type": "asset"})
        
        if not (check_entity_authorization(from_asset, "POST", claims_and_roles) and 
                check_entity_authorization(to_asset, "POST", claims_and_roles)):
            raise PermissionError("Not authorized to create metadata for this asset link")
        
        # Validate 500 record limit: Fetch existing + count with new
        try:
            paginator = dynamodb_client.get_paginator('query')
            page_iterator = paginator.paginate(
                TableName=asset_links_metadata_table_name,
                KeyConditionExpression='assetLinkId = :linkId',
                ExpressionAttributeValues={':linkId': {'S': asset_link_id}}
            ).build_full_result()
            
            existing_count = len(page_iterator.get('Items', []))
            new_unique_keys = {item.metadataKey for item in request_model.metadata}
            
            # Get existing keys to determine how many are truly new
            existing_keys = set()
            deserializer = TypeDeserializer()
            for item in page_iterator.get('Items', []):
                deserialized = {k: deserializer.deserialize(v) for k, v in item.items()}
                existing_keys.add(deserialized['metadataKey'])
            
            # Calculate final count after upsert
            final_count = len(existing_keys.union(new_unique_keys))
            
            if final_count > MAX_METADATA_RECORDS_PER_ENTITY:
                raise VAMSGeneralErrorResponse(
                    f"Maximum {MAX_METADATA_RECORDS_PER_ENTITY} metadata records allowed per entity "
                    f"(current: {existing_count}, attempting to add: {len(new_unique_keys)}, final would be: {final_count})"
                )
        except VAMSGeneralErrorResponse:
            raise
        except Exception as e:
            logger.warning(f"Error checking record limit: {e}")
            # Continue without limit check if it fails
        
        # Check if user is SYSTEM_USER - bypass schema validation
        username = claims_and_roles.get("tokens", ["SYSTEM_USER"])[0]
        skip_schema_validation = (username == "SYSTEM_USER")
        
        # Schema validation for non-SYSTEM_USER users
        if not skip_schema_validation:
            try:
                # Get schemas from both databases + GLOBAL
                database_ids = [asset_link['fromAssetDatabaseId'], asset_link['toAssetDatabaseId'], 'GLOBAL']
                database_ids = list(dict.fromkeys(database_ids))  # Remove duplicates
                
                aggregated_schema = get_aggregated_schemas(
                    database_ids=database_ids,
                    entity_type='assetLinkMetadata',
                    file_path=None,
                    dynamodb_client=dynamodb_client,
                    schema_table_name=metadata_schema_table_v2_name
                )
                
                # COMPREHENSIVE VALIDATION: Fetch existing metadata and merge with incoming
                paginator = dynamodb_client.get_paginator('query')
                page_iterator = paginator.paginate(
                    TableName=asset_links_metadata_table_name,
                    KeyConditionExpression='assetLinkId = :linkId',
                    ExpressionAttributeValues={':linkId': {'S': asset_link_id}}
                ).build_full_result()
                
                # Build existing metadata dict
                existing_metadata = stored_metadata_entries(page_iterator.get('Items', []))
                
                # Merge incoming metadata with existing (simulating upsert)
                merged_metadata = existing_metadata.copy()
                for item in request_model.metadata:
                    merged_metadata[item.metadataKey] = {
                        'metadataValue': item.metadataValue,
                        'metadataValueType': item.metadataValueType.value
                    }
                
                # Validate the complete merged state
                is_valid, errors, metadata_with_defaults = validate_metadata_against_schema(
                    merged_metadata, aggregated_schema, "POST", existing_metadata
                )
                
                if not is_valid:
                    error_message = "Schema validation failed: " + "; ".join(errors)
                    raise VAMSGeneralErrorResponse(error_message)
                
                # Check restrictMetadataOutsideSchemas setting (only if schemas exist)
                if aggregated_schema:
                    from_db_config = get_database_config(asset_link['fromAssetDatabaseId'])
                    to_db_config = get_database_config(asset_link['toAssetDatabaseId'])
                    restrict = (from_db_config.get('restrictMetadataOutsideSchemas', False) or 
                               to_db_config.get('restrictMetadataOutsideSchemas', False))
                    
                    if restrict:
                        keys_valid, key_errors = validate_metadata_keys_against_schema(
                            merged_metadata, aggregated_schema, True
                        )
                        if not keys_valid:
                            error_message = "Metadata key validation failed: " + "; ".join(key_errors)
                            raise VAMSGeneralErrorResponse(error_message)
                
                # Update request model with defaults applied (only for new fields).
                # This step is purely ADDITIVE and runs after every check above has passed,
                # so it is deliberately fail-open: a failure here loses schema-supplied
                # defaults and cannot admit anything validation refused. Guarded on its own so
                # the surrounding fail-closed arm does not turn it into a denied write.
                try:
                    updated_metadata = []
                    for item in request_model.metadata:
                        updated_metadata.append(item)

                    # Add any new fields with defaults that weren't in the request
                    for key, value_dict in metadata_with_defaults.items():
                        if key not in existing_metadata and not any(item.metadataKey == key for item in request_model.metadata):
                            from models.metadata import MetadataItemModel
                            updated_metadata.append(MetadataItemModel(
                                metadataKey=key,
                                metadataValue=value_dict['metadataValue'],
                                metadataValueType=value_dict['metadataValueType']
                            ))
                    request_model.metadata = updated_metadata
                except Exception as default_error:
                    logger.warning(
                        f"{SCHEMA_DEFAULT_INJECTION_FAILED_LOG}: {default_error}"
                    )
                
            except VAMSGeneralErrorResponse:
                raise
            except Exception as e:
                logger.exception(f"Error during schema validation: {e}")
                raise VAMSGeneralErrorResponse(SCHEMA_VALIDATION_UNAVAILABLE_MESSAGE)
        
        # Process metadata items in bulk - UNIFIED UPSERT (create or update)
        successful_items = []
        failed_items = []
        items_to_write = []
        
        for metadata_item in request_model.metadata:
            try:
                # Prepare item for upsert (will create or update) - FIX: Use DynamoDB typed format
                item = {
                    'assetLinkId': {'S': asset_link_id},
                    'metadataKey': {'S': metadata_item.metadataKey},
                    'metadataValue': {'S': metadata_item.metadataValue},
                    'metadataValueType': {'S': metadata_item.metadataValueType.value}
                }
                
                items_to_write.append({'PutRequest': {'Item': item}})
                successful_items.append(metadata_item.metadataKey)
                
            except Exception as e:
                logger.warning(f"Error preparing metadata item {metadata_item.metadataKey}: {e}")
                failed_items.append({
                    'key': metadata_item.metadataKey,
                    'error': str(e)
                })
        
        # Write items in batches of 25
        if items_to_write:
            for i in range(0, len(items_to_write), 25):
                batch = items_to_write[i:i+25]
                try:
                    batch_write_with_retry(asset_links_metadata_table_name, batch)
                except Exception as e:
                    logger.exception(f"Error in batch write: {e}")
                    # Mark all items in this batch as failed
                    for item in batch:
                        key = item['PutRequest']['Item']['metadataKey']['S']
                        if key in successful_items:
                            successful_items.remove(key)
                        failed_items.append({
                            'key': key,
                            'error': 'Batch write failed'
                        })
        
        # Build response
        timestamp = datetime.utcnow().isoformat()
        total_items = len(request_model.metadata)
        success_count = len(successful_items)
        failure_count = len(failed_items)
        
        return BulkOperationResponseModel(
            success=success_count > 0,
            totalItems=total_items,
            successCount=success_count,
            failureCount=failure_count,
            successfulItems=successful_items,
            failedItems=failed_items,
            message=f"Upserted {success_count} of {total_items} metadata items",
            timestamp=timestamp
        )
        
    except PermissionError as p:
        raise p
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error creating asset link metadata: {e}")
        raise VAMSGeneralErrorResponse("Error creating metadata")


def update_asset_link_metadata(asset_link_id: str, request_model: UpdateAssetLinkMetadataRequestModel, claims_and_roles: dict) -> BulkOperationResponseModel:
    """Update metadata for an asset link (bulk operation) - Supports UPDATE and REPLACE_ALL modes
    
    Args:
        asset_link_id: The asset link ID
        request_model: The update request model with metadata items and updateType
        claims_and_roles: User claims and roles
        
    Returns:
        BulkOperationResponseModel with operation results
    """
    try:
        # Validate asset link exists and check authorization
        asset_link = validate_asset_link_exists(asset_link_id)
        
        # Check permissions on both assets
        from_asset = validate_asset_exists(asset_link['fromAssetDatabaseId'], asset_link['fromAssetId'])
        to_asset = validate_asset_exists(asset_link['toAssetDatabaseId'], asset_link['toAssetId'])
        
        from_asset.update({"object__type": "asset"})
        to_asset.update({"object__type": "asset"})
        
        # Check authorization based on updateType
        if request_model.updateType == UpdateType.REPLACE_ALL:
            # REPLACE_ALL requires PUT, POST, and DELETE permissions
            if not (check_multi_action_authorization(from_asset, ["PUT", "POST", "DELETE"], claims_and_roles) and 
                    check_multi_action_authorization(to_asset, ["PUT", "POST", "DELETE"], claims_and_roles)):
                raise PermissionError("REPLACE_ALL requires PUT, POST, and DELETE permissions on both assets")
        else:
            # UPDATE mode requires only PUT permission
            if not (check_entity_authorization(from_asset, "PUT", claims_and_roles) and 
                    check_entity_authorization(to_asset, "PUT", claims_and_roles)):
                raise PermissionError("Not authorized to update metadata for this asset link")
        
        # Check if user is SYSTEM_USER - bypass schema validation
        username = claims_and_roles.get("tokens", ["SYSTEM_USER"])[0]
        skip_schema_validation = (username == "SYSTEM_USER")
        
        # Schema validation for non-SYSTEM_USER users
        if not skip_schema_validation:
            try:
                # Fetch ALL existing metadata for this asset link
                paginator = dynamodb_client.get_paginator('query')
                page_iterator = paginator.paginate(
                    TableName=asset_links_metadata_table_name,
                    KeyConditionExpression='assetLinkId = :linkId',
                    ExpressionAttributeValues={':linkId': {'S': asset_link_id}}
                ).build_full_result()
                
                # Build existing metadata dict
                existing_metadata = stored_metadata_entries(page_iterator.get('Items', []))
                
                # Validate 500 record limit based on updateType
                if request_model.updateType == UpdateType.UPDATE:
                    # For UPDATE: Check final count after merge
                    new_unique_keys = {item.metadataKey for item in request_model.metadata}
                    existing_keys = set(existing_metadata.keys())
                    final_count = len(existing_keys.union(new_unique_keys))
                    
                    if final_count > MAX_METADATA_RECORDS_PER_ENTITY:
                        raise VAMSGeneralErrorResponse(
                            f"Maximum {MAX_METADATA_RECORDS_PER_ENTITY} metadata records allowed per entity "
                            f"(current: {len(existing_keys)}, attempting to add: {len(new_unique_keys)}, final would be: {final_count})"
                        )
                    
                    # Merge with updates
                    for item in request_model.metadata:
                        existing_metadata[item.metadataKey] = {
                            'metadataValue': item.metadataValue,
                            'metadataValueType': item.metadataValueType.value
                        }
                    metadata_to_validate = existing_metadata
                else:  # REPLACE_ALL
                    # For REPLACE_ALL: Just check incoming count
                    if len(request_model.metadata) > MAX_METADATA_RECORDS_PER_ENTITY:
                        raise VAMSGeneralErrorResponse(
                            f"Maximum {MAX_METADATA_RECORDS_PER_ENTITY} metadata records allowed per entity "
                            f"(attempting to set: {len(request_model.metadata)})"
                        )
                    
                    # Validate only provided metadata (all-or-nothing)
                    metadata_to_validate = {
                        item.metadataKey: {
                            'metadataValue': item.metadataValue,
                            'metadataValueType': item.metadataValueType.value
                        }
                        for item in request_model.metadata
                    }
                
                # Get schemas and validate
                database_ids = [asset_link['fromAssetDatabaseId'], asset_link['toAssetDatabaseId'], 'GLOBAL']
                database_ids = list(dict.fromkeys(database_ids))
                
                aggregated_schema = get_aggregated_schemas(
                    database_ids=database_ids,
                    entity_type='assetLinkMetadata',
                    file_path=None,
                    dynamodb_client=dynamodb_client,
                    schema_table_name=metadata_schema_table_v2_name
                )
                
                is_valid, errors, metadata_with_defaults = validate_metadata_against_schema(
                    metadata_to_validate, aggregated_schema, "PUT"
                )
                
                if not is_valid:
                    error_message = "Schema validation failed: " + "; ".join(errors)
                    raise VAMSGeneralErrorResponse(error_message)
                
            except VAMSGeneralErrorResponse:
                raise
            except Exception as e:
                logger.exception(f"Error during schema validation: {e}")
                raise VAMSGeneralErrorResponse(SCHEMA_VALIDATION_UNAVAILABLE_MESSAGE)
        
        # Route to appropriate operation based on updateType
        if request_model.updateType == UpdateType.REPLACE_ALL:
            # REPLACE_ALL: Delete unlisted keys, then upsert all provided
            return _replace_all_asset_link_metadata(asset_link_id, request_model.metadata, claims_and_roles)
        else:
            # UPDATE: Upsert provided metadata (create or update)
            return _upsert_asset_link_metadata(asset_link_id, request_model.metadata, claims_and_roles)
        
    except PermissionError as p:
        raise p
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error updating asset link metadata: {e}")
        raise VAMSGeneralErrorResponse("Error updating metadata")


def _upsert_asset_link_metadata(asset_link_id: str, metadata_items: list, claims_and_roles: dict) -> BulkOperationResponseModel:
    """Internal helper: Upsert asset link metadata (create or update)"""
    try:
        successful_items = []
        failed_items = []
        items_to_write = []
        
        for metadata_item in metadata_items:
            try:
                # Prepare item for upsert (will create or update) - Use DynamoDB typed format
                item = {
                    'assetLinkId': {'S': asset_link_id},
                    'metadataKey': {'S': metadata_item.metadataKey},
                    'metadataValue': {'S': metadata_item.metadataValue},
                    'metadataValueType': {'S': metadata_item.metadataValueType.value}
                }
                
                items_to_write.append({'PutRequest': {'Item': item}})
                successful_items.append(metadata_item.metadataKey)
                
            except Exception as e:
                logger.warning(f"Error preparing metadata item {metadata_item.metadataKey}: {e}")
                failed_items.append({
                    'key': metadata_item.metadataKey,
                    'error': str(e)
                })
        
        # Write items in batches of 25
        if items_to_write:
            for i in range(0, len(items_to_write), 25):
                batch = items_to_write[i:i+25]
                try:
                    batch_write_with_retry(asset_links_metadata_table_name, batch)
                except Exception as e:
                    logger.exception(f"Error in batch write: {e}")
                    for item in batch:
                        key = item['PutRequest']['Item']['metadataKey']['S']
                        if key in successful_items:
                            successful_items.remove(key)
                        failed_items.append({
                            'key': key,
                            'error': 'Batch write failed'
                        })
        
        # Build response
        timestamp = datetime.utcnow().isoformat()
        total_items = len(metadata_items)
        success_count = len(successful_items)
        failure_count = len(failed_items)
        
        return BulkOperationResponseModel(
            success=success_count > 0,
            totalItems=total_items,
            successCount=success_count,
            failureCount=failure_count,
            successfulItems=successful_items,
            failedItems=failed_items,
            message=f"Upserted {success_count} of {total_items} metadata items",
            timestamp=timestamp
        )
        
    except Exception as e:
        logger.exception(f"Error in upsert operation: {e}")
        raise VAMSGeneralErrorResponse("Error upserting metadata")


def _replace_all_asset_link_metadata(asset_link_id: str, metadata_items: list, claims_and_roles: dict) -> BulkOperationResponseModel:
    """Internal helper: Replace all asset link metadata with rollback on failure"""
    try:
        # Step 1: Fetch all existing metadata
        paginator = dynamodb_client.get_paginator('query')
        page_iterator = paginator.paginate(
            TableName=asset_links_metadata_table_name,
            KeyConditionExpression='assetLinkId = :linkId',
            ExpressionAttributeValues={':linkId': {'S': asset_link_id}}
        ).build_full_result()
        
        existing_metadata = []
        deserializer = TypeDeserializer()
        for item in page_iterator.get('Items', []):
            deserialized = {k: deserializer.deserialize(v) for k, v in item.items()}
            existing_metadata.append(deserialized)
        
        # Step 2: Determine which keys to delete
        provided_keys = {item.metadataKey for item in metadata_items}
        existing_keys = {item['metadataKey'] for item in existing_metadata}
        keys_to_delete = existing_keys - provided_keys
        
        # Store items to delete for potential rollback
        deleted_items_backup = [
            item for item in existing_metadata 
            if item['metadataKey'] in keys_to_delete
        ]
        
        logger.info(f"REPLACE_ALL: Deleting {len(keys_to_delete)} keys, upserting {len(provided_keys)} keys")
        
        # Step 3: Delete keys not in provided list
        if keys_to_delete:
            items_to_delete = []
            for key in keys_to_delete:
                items_to_delete.append({
                    'DeleteRequest': {
                        'Key': {
                            'assetLinkId': {'S': asset_link_id},
                            'metadataKey': {'S': key}
                        }
                    }
                })
            
            # Delete in batches of 25
            for i in range(0, len(items_to_delete), 25):
                batch = items_to_delete[i:i+25]
                try:
                    batch_write_with_retry(asset_links_metadata_table_name, batch)
                except Exception as e:
                    logger.exception(f"Error deleting metadata in REPLACE_ALL: {e}")
                    raise VAMSGeneralErrorResponse("Failed to delete existing metadata")
        
        # Step 4: Upsert all provided metadata
        try:
            items_to_write = []
            for metadata_item in metadata_items:
                # Use DynamoDB typed format
                item = {
                    'assetLinkId': {'S': asset_link_id},
                    'metadataKey': {'S': metadata_item.metadataKey},
                    'metadataValue': {'S': metadata_item.metadataValue},
                    'metadataValueType': {'S': metadata_item.metadataValueType.value}
                }
                items_to_write.append({'PutRequest': {'Item': item}})
            
            # Write in batches of 25
            for i in range(0, len(items_to_write), 25):
                batch = items_to_write[i:i+25]
                batch_write_with_retry(asset_links_metadata_table_name, batch)
            
            # Success - build response
            timestamp = datetime.utcnow().isoformat()
            return BulkOperationResponseModel(
                success=True,
                totalItems=len(metadata_items),
                successCount=len(metadata_items),
                failureCount=0,
                successfulItems=[item.metadataKey for item in metadata_items],
                failedItems=[],
                message=f"Replaced all metadata: deleted {len(keys_to_delete)} keys, upserted {len(metadata_items)} keys",
                timestamp=timestamp
            )
            
        except Exception as upsert_error:
            # Step 5: Rollback - attempt to restore deleted items
            logger.error(f"Upsert failed in REPLACE_ALL, attempting rollback: {upsert_error}")
            
            if deleted_items_backup:
                try:
                    # Restore deleted items - Use DynamoDB typed format
                    items_to_restore = []
                    for item in deleted_items_backup:
                        restore_item = {
                            'assetLinkId': {'S': item['assetLinkId']},
                            'metadataKey': {'S': item['metadataKey']},
                            # Carried only when the backup holds them: a row written by an
                            # earlier release can lack either attribute, and subscripting it
                            # here raised before any write was issued - so a single legacy row
                            # in the deleted set meant NO row was restored and the metadata was
                            # permanently lost. A rollback reinstates what was there, so an
                            # absent attribute stays absent rather than gaining a default.
                            **({'metadataValue': {'S': item['metadataValue']}}
                               if item.get('metadataValue') is not None else {}),
                            **({'metadataValueType': {'S': item['metadataValueType']}}
                               if item.get('metadataValueType') is not None else {}),
                        }
                        items_to_restore.append({'PutRequest': {'Item': restore_item}})
                    
                    # Restore in batches of 25
                    for i in range(0, len(items_to_restore), 25):
                        batch = items_to_restore[i:i+25]
                        batch_write_with_retry(asset_links_metadata_table_name, batch)
                    
                    logger.info(f"Rollback successful: restored {len(deleted_items_backup)} deleted items")
                    rollback_succeeded = True

                except Exception as rollback_error:
                    logger.error(f"Rollback failed: {rollback_error}")
                    rollback_succeeded = False

                # Reported outside the rollback try so the except arm above cannot catch
                # this signal and describe a completed rollback as an inconsistent one.
                if rollback_succeeded:
                    raise VAMSGeneralErrorResponse(
                        "REPLACE_ALL operation failed, all changes rolled back successfully"
                    )
                raise VAMSGeneralErrorResponse(
                    "REPLACE_ALL operation failed and rollback unsuccessful - data may be inconsistent. "
                    "Please contact administrator."
                )
            else:
                # No items were deleted, so just report the upsert failure
                raise VAMSGeneralErrorResponse(f"REPLACE_ALL operation failed during upsert: {str(upsert_error)}")
        
    except PermissionError as p:
        raise p
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error in REPLACE_ALL operation: {e}")
        raise VAMSGeneralErrorResponse("Error in REPLACE_ALL operation")


def delete_asset_link_metadata(asset_link_id: str, request_model: DeleteAssetLinkMetadataRequestModel, claims_and_roles: dict) -> BulkOperationResponseModel:
    """Delete metadata for an asset link (bulk operation)
    
    Args:
        asset_link_id: The asset link ID
        request_model: The delete request model with metadata keys
        claims_and_roles: User claims and roles
        
    Returns:
        BulkOperationResponseModel with operation results
    """
    try:
        # Validate asset link exists and check authorization
        asset_link = validate_asset_link_exists(asset_link_id)
        
        # Check permissions on both assets
        from_asset = validate_asset_exists(asset_link['fromAssetDatabaseId'], asset_link['fromAssetId'])
        to_asset = validate_asset_exists(asset_link['toAssetDatabaseId'], asset_link['toAssetId'])
        
        from_asset.update({"object__type": "asset"})
        to_asset.update({"object__type": "asset"})
        
        if not (check_entity_authorization(from_asset, "DELETE", claims_and_roles) and 
                check_entity_authorization(to_asset, "DELETE", claims_and_roles)):
            raise PermissionError("Not authorized to delete metadata for this asset link")
        
        # NEW: Schema validation for deletion
        try:
            # Fetch all existing metadata
            paginator = dynamodb_client.get_paginator('query')
            page_iterator = paginator.paginate(
                TableName=asset_links_metadata_table_name,
                KeyConditionExpression='assetLinkId = :linkId',
                ExpressionAttributeValues={':linkId': {'S': asset_link_id}}
            ).build_full_result()
            
            existing_metadata = {}
            deserializer = TypeDeserializer()
            for item in page_iterator.get('Items', []):
                deserialized = {k: deserializer.deserialize(v) for k, v in item.items()}
                # .get: only the remaining KEYS matter here, and a row written by an earlier
                # version may carry neither attribute. The block below denies on error.
                existing_metadata[deserialized.get('metadataKey')] = {
                    'metadataValue': deserialized.get('metadataValue'),
                    'metadataValueType': deserialized.get('metadataValueType')
                }
            
            # Calculate remaining metadata after deletion
            remaining_metadata = {
                k: v for k, v in existing_metadata.items() 
                if k not in request_model.metadataKeys
            }
            
            # Get schemas and validate deletion
            database_ids = [asset_link['fromAssetDatabaseId'], asset_link['toAssetDatabaseId'], 'GLOBAL']
            database_ids = list(dict.fromkeys(database_ids))  # Remove duplicates
            
            aggregated_schema = get_aggregated_schemas(
                database_ids=database_ids,
                entity_type='assetLinkMetadata',
                file_path=None,
                dynamodb_client=dynamodb_client,
                schema_table_name=metadata_schema_table_v2_name
            )
            
            # Validate deletion
            from common.metadataSchemaValidation import validate_metadata_deletion
            is_valid, validation_errors = validate_metadata_deletion(
                request_model.metadataKeys,
                remaining_metadata,
                aggregated_schema
            )
            
            if not is_valid:
                logger.warning(
                    f"Deletion validation failed: {'; '.join(validation_errors)}")
                raise VAMSGeneralErrorResponse(SCHEMA_DELETION_NOT_ALLOWED_MESSAGE)
                
        except VAMSGeneralErrorResponse:
            raise
        except Exception as e:
            # Fail closed like the write path: this block is the only guard on removing a
            # schema-required field, and swallowing the error deleted the keys unvalidated.
            logger.exception(f"Error during deletion validation: {e}")
            raise VAMSGeneralErrorResponse(SCHEMA_DELETION_VALIDATION_UNAVAILABLE_MESSAGE)
        
        # Process metadata keys
        successful_items = []
        failed_items = []
        
        # Use batch write for efficiency
        items_to_delete = []
        
        for metadata_key in request_model.metadataKeys:
            try:
                # Check if metadata exists
                existing_response = asset_links_metadata_table.get_item(
                    Key={
                        'assetLinkId': asset_link_id,
                        'metadataKey': metadata_key
                    }
                )
                
                if 'Item' not in existing_response:
                    failed_items.append({
                        'key': metadata_key,
                        'error': 'Metadata key not found'
                    })
                    continue
                
                # Prepare item for batch delete
                items_to_delete.append({
                    'DeleteRequest': {
                        'Key': {
                            'assetLinkId': {'S': asset_link_id},
                            'metadataKey': {'S': metadata_key}
                        }
                    }
                })
                successful_items.append(metadata_key)
                
            except Exception as e:
                logger.warning(f"Error preparing delete for metadata key {metadata_key}: {e}")
                failed_items.append({
                    'key': metadata_key,
                    'error': str(e)
                })
        
        # Delete items in batches of 25
        if items_to_delete:
            for i in range(0, len(items_to_delete), 25):
                batch = items_to_delete[i:i+25]
                try:
                    batch_write_with_retry(asset_links_metadata_table_name, batch)
                except Exception as e:
                    logger.exception(f"Error in batch delete: {e}")
                    # Mark all items in this batch as failed
                    for item in batch:
                        key = item['DeleteRequest']['Key']['metadataKey']['S']
                        if key in successful_items:
                            successful_items.remove(key)
                        failed_items.append({
                            'key': key,
                            'error': 'Batch delete failed'
                        })
        
        # Build response
        timestamp = datetime.utcnow().isoformat()
        total_items = len(request_model.metadataKeys)
        success_count = len(successful_items)
        failure_count = len(failed_items)
        
        return BulkOperationResponseModel(
            success=success_count > 0,
            totalItems=total_items,
            successCount=success_count,
            failureCount=failure_count,
            successfulItems=successful_items,
            failedItems=failed_items,
            message=f"Deleted {success_count} of {total_items} metadata items",
            timestamp=timestamp
        )
        
    except PermissionError as p:
        raise p
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error deleting asset link metadata: {e}")
        raise VAMSGeneralErrorResponse("Error deleting metadata")


#######################
# Request Handlers - Asset Link Metadata
#######################

def handle_asset_link_metadata_get(event):
    """Handle GET requests for asset link metadata"""
    path_parameters = event.get('pathParameters', {})
    query_parameters = event.get('queryStringParameters', {}) or {}
    
    try:
        # Parse and validate path parameters (validation in model)
        try:
            path_request_model = parse(path_parameters, model=AssetLinkMetadataPathRequestModel)
        except ValidationError as v:
            logger.exception(f"Validation error in path parameters: {v}")
            return validation_error(body={'message': validation_error_message(v)}, event=event)
        
        # Parse query parameters
        try:
            query_request_model = parse(query_parameters, model=GetAssetLinkMetadataRequestModel)
            query_params = {
                'maxItems': query_request_model.maxItems,
                'pageSize': query_request_model.pageSize,
                'startingToken': query_request_model.startingToken
            }
        except ValidationError as v:
            logger.exception(f"Validation error in query parameters: {v}")
            return validation_error(body={'message': validation_error_message(v)}, event=event)
        
        # Get metadata
        response = get_asset_link_metadata(path_request_model.assetLinkId, query_params, claims_and_roles)
        return success(body=response.dict())
        
    except PermissionError as p:
        logger.warning(f"Permission error: {p}")
        return authorization_error(body={'message': str(p)})
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling GET request: {e}")
        return internal_error(event=event)


def handle_asset_link_metadata_post(event):
    """Handle POST requests to create asset link metadata"""
    path_parameters = event.get('pathParameters', {})
    
    try:
        # Parse and validate path parameters (validation in model)
        try:
            path_request_model = parse(path_parameters, model=AssetLinkMetadataPathRequestModel)
        except ValidationError as v:
            logger.exception(f"Validation error in path parameters: {v}")
            return validation_error(body={'message': validation_error_message(v)}, event=event)
        
        # Parse request body
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"}, event=event)
        
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"}, event=event)
        elif isinstance(body, dict):
            body = body
        else:
            logger.error("Request body is not a string or dict")
            return validation_error(body={'message': "Request body cannot be parsed"}, event=event)
        
        # Parse and validate the request model
        request_model = parse(body, model=CreateAssetLinkMetadataRequestModel)
        
        # Create metadata
        response = create_asset_link_metadata(path_request_model.assetLinkId, request_model, claims_and_roles)
        return success(body=response.dict())
        
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': validation_error_message(v)}, event=event)
    except PermissionError as p:
        logger.warning(f"Permission error: {p}")
        return authorization_error(body={'message': str(p)})
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling POST request: {e}")
        return internal_error(event=event)


def handle_asset_link_metadata_put(event):
    """Handle PUT requests to update asset link metadata"""
    path_parameters = event.get('pathParameters', {})
    
    try:
        # Parse and validate path parameters (validation in model)
        try:
            path_request_model = parse(path_parameters, model=AssetLinkMetadataPathRequestModel)
        except ValidationError as v:
            logger.exception(f"Validation error in path parameters: {v}")
            return validation_error(body={'message': validation_error_message(v)}, event=event)
        
        # Parse request body
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"}, event=event)
        
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"}, event=event)
        elif isinstance(body, dict):
            body = body
        else:
            logger.error("Request body is not a string or dict")
            return validation_error(body={'message': "Request body cannot be parsed"}, event=event)
        
        # Parse and validate the request model
        request_model = parse(body, model=UpdateAssetLinkMetadataRequestModel)
        
        # Update metadata
        response = update_asset_link_metadata(path_request_model.assetLinkId, request_model, claims_and_roles)
        return success(body=response.dict())
        
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': validation_error_message(v)}, event=event)
    except PermissionError as p:
        logger.warning(f"Permission error: {p}")
        return authorization_error(body={'message': str(p)})
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling PUT request: {e}")
        return internal_error(event=event)


def handle_asset_link_metadata_delete(event):
    """Handle DELETE requests to delete asset link metadata"""
    path_parameters = event.get('pathParameters', {})
    
    try:
        # Parse and validate path parameters (validation in model)
        try:
            path_request_model = parse(path_parameters, model=AssetLinkMetadataPathRequestModel)
        except ValidationError as v:
            logger.exception(f"Validation error in path parameters: {v}")
            return validation_error(body={'message': validation_error_message(v)}, event=event)
        
        # Parse request body
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"}, event=event)
        
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"}, event=event)
        elif isinstance(body, dict):
            body = body
        else:
            logger.error("Request body is not a string or dict")
            return validation_error(body={'message': "Request body cannot be parsed"}, event=event)
        
        # Parse and validate the request model
        request_model = parse(body, model=DeleteAssetLinkMetadataRequestModel)
        
        # Delete metadata
        response = delete_asset_link_metadata(path_request_model.assetLinkId, request_model, claims_and_roles)
        return success(body=response.dict())
        
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': validation_error_message(v)}, event=event)
    except PermissionError as p:
        logger.warning(f"Permission error: {p}")
        return authorization_error(body={'message': str(p)})
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling DELETE request: {e}")
        return internal_error(event=event)


#######################
# Asset Metadata CRUD Operations
#######################

def get_asset_metadata(database_id: str, asset_id: str, query_params: dict, claims_and_roles: dict) -> GetAssetMetadataResponseModel:
    """Get metadata for an asset - Returns one page of records

    Args:
        database_id: The database ID
        asset_id: The asset ID
        query_params: 'pageSize', 'maxItems' and 'startingToken' for the returned page
        claims_and_roles: User claims and roles

    Returns:
        GetAssetMetadataResponseModel with one page of metadata records and its NextToken
    """
    # Check if querying a specific version
    asset_version_id = query_params.get('assetVersionId')
    if asset_version_id:
        return get_asset_metadata_from_version(
            database_id, asset_id, asset_version_id, query_params, claims_and_roles
        )

    try:
        # Validate asset exists and check authorization
        asset = validate_asset_exists(database_id, asset_id)
        asset.update({"object__type": "asset"})
        
        if not check_entity_authorization(asset, "GET", claims_and_roles):
            raise PermissionError("Not authorized to view metadata for this asset")
        
        # Build composite key for query
        composite_key = f"{database_id}:{asset_id}:/"
        
        # Fetch every record: the page is sliced after schema enrichment and ordering
        paginator = dynamodb_client.get_paginator('query')
        page_iterator = paginator.paginate(
            TableName=asset_file_metadata_table_name,
            IndexName='DatabaseIdAssetIdFilePathIndex',
            KeyConditionExpression='#pk = :pkValue',
            ExpressionAttributeNames={'#pk': 'databaseId:assetId:filePath'},
            ExpressionAttributeValues={':pkValue': {'S': composite_key}},
            ScanIndexForward=False
        ).build_full_result()
        
        # Process ALL items
        metadata_list = []
        deserializer = TypeDeserializer()
        for item in page_iterator.get('Items', []):
            deserialized_item = {k: deserializer.deserialize(v) for k, v in item.items()}
            metadata_list.append(deserialized_item)
        
        # Fetch database config and schema enrichment
        restrict_metadata_outside_schemas = False
        try:
            database_ids = [database_id, 'GLOBAL']
            
            aggregated_schema = get_aggregated_schemas(
                database_ids=database_ids,
                entity_type='assetMetadata',
                file_path=None,
                dynamodb_client=dynamodb_client,
                schema_table_name=metadata_schema_table_v2_name
            )
            
            # Calculate restrictMetadataOutsideSchemas
            schemas_exist = len(aggregated_schema) > 0
            if schemas_exist:
                try:
                    db_config = get_database_config(database_id)
                    db_restricts = db_config.get('restrictMetadataOutsideSchemas', False) == True
                    restrict_metadata_outside_schemas = db_restricts
                except Exception as e:
                    logger.warning(f"Error fetching database config for restriction check: {e}")
                    restrict_metadata_outside_schemas = False
            
            # Enrich metadata with schema information
            enriched_metadata = enrich_metadata_with_schema(metadata_list, aggregated_schema)

            # Convert to response models
            metadata_list = metadata_response_models(
                AssetMetadataResponseModel, enriched_metadata,
                databaseId=database_id, assetId=asset_id)
        except Exception as e:
            logger.warning(f"Error enriching metadata with schema: {e}")
            # If schema enrichment fails, return metadata without enrichment
            metadata_list = metadata_response_models(
                AssetMetadataResponseModel, metadata_list,
                databaseId=database_id, assetId=asset_id)
            restrict_metadata_outside_schemas = False

        # Offset-paginate the enriched, ordered list to bound the response payload.
        page, next_token = paginate_metadata_records(metadata_list, query_params)

        # Build response
        result = GetAssetMetadataResponseModel(
            metadata=page,
            restrictMetadataOutsideSchemas=restrict_metadata_outside_schemas,
            NextToken=next_token
        )

        return result
        
    except PermissionError as p:
        raise p
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error getting asset metadata: {e}")
        raise VAMSGeneralErrorResponse("Error retrieving metadata")


def get_asset_metadata_from_version(database_id: str, asset_id: str, asset_version_id: str,
                                    query_params: dict, claims_and_roles: dict) -> GetAssetMetadataResponseModel:
    """Get asset metadata from a specific version snapshot"""
    try:
        # Validate asset exists and check authorization
        asset = validate_asset_exists(database_id, asset_id)
        asset.update({"object__type": "asset"})

        if not check_entity_authorization(asset, "GET", claims_and_roles):
            raise PermissionError("Not authorized to view metadata for this asset")

        # Validate asset version exists before querying metadata
        validate_asset_version_exists(database_id, asset_id, asset_version_id)

        # Get metadata snapshot for this version
        metadata_items_raw = get_asset_metadata_version(database_id, asset_id, asset_version_id)

        if not metadata_items_raw:
            logger.info(f"No metadata found for version {asset_version_id}")
            return GetAssetMetadataResponseModel(
                metadata=[],
                restrictMetadataOutsideSchemas=False
            )

        # Filter for asset-level metadata only (filePath="/") and type="metadata"
        asset_metadata = [item for item in metadata_items_raw if item.filePath == "/" and item.type == "metadata"]

        # Convert to dict format for schema enrichment
        metadata_list = []
        for item in asset_metadata:
            metadata_list.append({
                'metadataKey': item.metadataKey,
                'metadataValue': item.metadataValue,
                'metadataValueType': item.metadataValueType
            })

        # Apply schema enrichment (same as current flow)
        restrict_metadata_outside_schemas = False
        try:
            database_ids = [database_id, 'GLOBAL']

            aggregated_schema = get_aggregated_schemas(
                database_ids=database_ids,
                entity_type='assetMetadata',
                file_path=None,
                dynamodb_client=dynamodb_client,
                schema_table_name=metadata_schema_table_v2_name
            )

            schemas_exist = len(aggregated_schema) > 0
            if schemas_exist:
                try:
                    db_config = get_database_config(database_id)
                    db_restricts = db_config.get('restrictMetadataOutsideSchemas', False) == True
                    restrict_metadata_outside_schemas = db_restricts
                except Exception as e:
                    logger.warning(f"Error fetching database config for restriction check: {e}")
                    restrict_metadata_outside_schemas = False

            enriched_metadata = enrich_metadata_with_schema(metadata_list, aggregated_schema)

            metadata_list = metadata_response_models(
                AssetMetadataResponseModel, enriched_metadata,
                databaseId=database_id, assetId=asset_id)
        except Exception as e:
            logger.warning(f"Error enriching version metadata with schema: {e}")
            metadata_list = metadata_response_models(
                AssetMetadataResponseModel, metadata_list,
                databaseId=database_id, assetId=asset_id)
            restrict_metadata_outside_schemas = False

        # Offset-paginate the enriched, ordered list to bound the response payload.
        page, next_token = paginate_metadata_records(metadata_list, query_params)

        return GetAssetMetadataResponseModel(
            metadata=page,
            restrictMetadataOutsideSchemas=restrict_metadata_outside_schemas,
            NextToken=next_token
        )

    except PermissionError as p:
        raise p
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error getting asset metadata from version: {e}")
        raise VAMSGeneralErrorResponse("Error retrieving metadata from version")


def create_asset_metadata(database_id: str, asset_id: str, request_model: CreateAssetMetadataRequestModel, claims_and_roles: dict) -> BulkOperationResponseModel:
    """Create metadata for an asset (bulk operation)
    
    Args:
        database_id: The database ID
        asset_id: The asset ID
        request_model: The create request model with metadata items
        claims_and_roles: User claims and roles
        
    Returns:
        BulkOperationResponseModel with operation results
    """
    try:
        # Validate asset exists and check authorization
        asset = validate_asset_exists(database_id, asset_id)
        asset.update({"object__type": "asset"})
        
        if not check_entity_authorization(asset, "POST", claims_and_roles):
            raise PermissionError("Not authorized to create metadata for this asset")
        
        # Validate 500 record limit: Fetch existing + count with new
        composite_key = f"{database_id}:{asset_id}:/"
        try:
            paginator = dynamodb_client.get_paginator('query')
            page_iterator = paginator.paginate(
                TableName=asset_file_metadata_table_name,
                IndexName='DatabaseIdAssetIdFilePathIndex',
                KeyConditionExpression='#pk = :pkValue',
                ExpressionAttributeNames={'#pk': 'databaseId:assetId:filePath'},
                ExpressionAttributeValues={':pkValue': {'S': composite_key}}
            ).build_full_result()
            
            existing_count = len(page_iterator.get('Items', []))
            new_unique_keys = {item.metadataKey for item in request_model.metadata}
            
            # Get existing keys to determine how many are truly new
            existing_keys = set()
            deserializer = TypeDeserializer()
            for item in page_iterator.get('Items', []):
                deserialized = {k: deserializer.deserialize(v) for k, v in item.items()}
                existing_keys.add(deserialized['metadataKey'])
            
            # Calculate final count after upsert
            final_count = len(existing_keys.union(new_unique_keys))
            
            if final_count > MAX_METADATA_RECORDS_PER_ENTITY:
                raise VAMSGeneralErrorResponse(
                    f"Maximum {MAX_METADATA_RECORDS_PER_ENTITY} metadata records allowed per entity "
                    f"(current: {existing_count}, attempting to add: {len(new_unique_keys)}, final would be: {final_count})"
                )
        except VAMSGeneralErrorResponse:
            raise
        except Exception as e:
            logger.warning(f"Error checking record limit: {e}")
            # Continue without limit check if it fails
        
        # Check if user is SYSTEM_USER - bypass schema validation
        username = claims_and_roles.get("tokens", ["SYSTEM_USER"])[0]
        skip_schema_validation = (username == "SYSTEM_USER")
        
        # Schema validation for non-SYSTEM_USER users
        if not skip_schema_validation:
            try:
                database_ids = [database_id, 'GLOBAL']
                
                aggregated_schema = get_aggregated_schemas(
                    database_ids=database_ids,
                    entity_type='assetMetadata',
                    file_path=None,
                    dynamodb_client=dynamodb_client,
                    schema_table_name=metadata_schema_table_v2_name
                )
                
                # COMPREHENSIVE VALIDATION: Fetch existing metadata and merge with incoming
                paginator = dynamodb_client.get_paginator('query')
                page_iterator = paginator.paginate(
                    TableName=asset_file_metadata_table_name,
                    IndexName='DatabaseIdAssetIdFilePathIndex',
                    KeyConditionExpression='#pk = :pkValue',
                    ExpressionAttributeNames={'#pk': 'databaseId:assetId:filePath'},
                    ExpressionAttributeValues={':pkValue': {'S': composite_key}}
                ).build_full_result()
                
                # Build existing metadata dict
                existing_metadata = stored_metadata_entries(page_iterator.get('Items', []))
                
                # Merge incoming metadata with existing (simulating upsert)
                merged_metadata = existing_metadata.copy()
                for item in request_model.metadata:
                    merged_metadata[item.metadataKey] = {
                        'metadataValue': item.metadataValue,
                        'metadataValueType': item.metadataValueType.value
                    }
                
                # Validate the complete merged state
                is_valid, errors, metadata_with_defaults = validate_metadata_against_schema(
                    merged_metadata, aggregated_schema, "POST", existing_metadata
                )
                
                if not is_valid:
                    error_message = "Schema validation failed: " + "; ".join(errors)
                    raise VAMSGeneralErrorResponse(error_message)
                
                # Check restrictMetadataOutsideSchemas setting (only if schemas exist)
                if aggregated_schema:
                    db_config = get_database_config(database_id)
                    restrict = db_config.get('restrictMetadataOutsideSchemas', False)
                    
                    if restrict:
                        keys_valid, key_errors = validate_metadata_keys_against_schema(
                            merged_metadata, aggregated_schema, True
                        )
                        if not keys_valid:
                            error_message = "Metadata key validation failed: " + "; ".join(key_errors)
                            raise VAMSGeneralErrorResponse(error_message)
                
                # Update request model with defaults applied (only for new fields).
                # This step is purely ADDITIVE and runs after every check above has passed,
                # so it is deliberately fail-open: a failure here loses schema-supplied
                # defaults and cannot admit anything validation refused. Guarded on its own so
                # the surrounding fail-closed arm does not turn it into a denied write.
                try:
                    updated_metadata = []
                    for item in request_model.metadata:
                        updated_metadata.append(item)

                    # Add any new fields with defaults that weren't in the request
                    for key, value_dict in metadata_with_defaults.items():
                        if key not in existing_metadata and not any(item.metadataKey == key for item in request_model.metadata):
                            from models.metadata import MetadataItemModel
                            updated_metadata.append(MetadataItemModel(
                                metadataKey=key,
                                metadataValue=value_dict['metadataValue'],
                                metadataValueType=value_dict['metadataValueType']
                            ))
                    request_model.metadata = updated_metadata
                except Exception as default_error:
                    logger.warning(
                        f"{SCHEMA_DEFAULT_INJECTION_FAILED_LOG}: {default_error}"
                    )
                
            except VAMSGeneralErrorResponse:
                raise
            except Exception as e:
                logger.exception(f"Error during schema validation: {e}")
                raise VAMSGeneralErrorResponse(SCHEMA_VALIDATION_UNAVAILABLE_MESSAGE)
        
        # Process metadata items in bulk
        successful_items = []
        failed_items = []
        asset_composite_key = f"{database_id}:{asset_id}"
        
        # Prepare items for batch write
        items_to_write = []
        
        for metadata_item in request_model.metadata:
            try:
                # Prepare item for upsert (will create or update)
                item = {
                    'metadataKey': {'S': metadata_item.metadataKey},
                    'databaseId:assetId:filePath': {'S': composite_key},
                    'databaseId:assetId': {'S': asset_composite_key},
                    'metadataValue': {'S': metadata_item.metadataValue},
                    'metadataValueType': {'S': metadata_item.metadataValueType.value}
                }
                
                items_to_write.append({'PutRequest': {'Item': item}})
                successful_items.append(metadata_item.metadataKey)
                
            except Exception as e:
                logger.warning(f"Error preparing metadata item {metadata_item.metadataKey}: {e}")
                failed_items.append({
                    'key': metadata_item.metadataKey,
                    'error': str(e)
                })
        
        # Write items in batches of 25
        if items_to_write:
            for i in range(0, len(items_to_write), 25):
                batch = items_to_write[i:i+25]
                try:
                    batch_write_with_retry(asset_file_metadata_table_name, batch)
                except Exception as e:
                    logger.exception(f"Error in batch write: {e}")
                    for item in batch:
                        key = item['PutRequest']['Item']['metadataKey']['S']
                        if key in successful_items:
                            successful_items.remove(key)
                        failed_items.append({
                            'key': key,
                            'error': 'Batch write failed'
                        })
        
        # Build response
        timestamp = datetime.utcnow().isoformat()
        total_items = len(request_model.metadata)
        success_count = len(successful_items)
        failure_count = len(failed_items)
        
        return BulkOperationResponseModel(
            success=success_count > 0,
            totalItems=total_items,
            successCount=success_count,
            failureCount=failure_count,
            successfulItems=successful_items,
            failedItems=failed_items,
            message=f"Created {success_count} of {total_items} metadata items",
            timestamp=timestamp
        )
        
    except PermissionError as p:
        raise p
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error creating asset metadata: {e}")
        raise VAMSGeneralErrorResponse("Error creating metadata")


def update_asset_metadata(database_id: str, asset_id: str, request_model: UpdateAssetMetadataRequestModel, claims_and_roles: dict) -> BulkOperationResponseModel:
    """Update metadata for an asset (bulk operation) - Supports UPDATE and REPLACE_ALL modes
    
    Args:
        database_id: The database ID
        asset_id: The asset ID
        request_model: The update request model with metadata items and updateType
        claims_and_roles: User claims and roles
        
    Returns:
        BulkOperationResponseModel with operation results
    """
    try:
        # Validate asset exists and check authorization
        asset = validate_asset_exists(database_id, asset_id)
        asset.update({"object__type": "asset"})
        
        # Check authorization based on updateType
        if request_model.updateType == UpdateType.REPLACE_ALL:
            # REPLACE_ALL requires PUT, POST, and DELETE permissions
            if not check_multi_action_authorization(asset, ["PUT", "POST", "DELETE"], claims_and_roles):
                raise PermissionError("REPLACE_ALL requires PUT, POST, and DELETE permissions")
        else:
            # UPDATE mode requires only PUT permission
            if not check_entity_authorization(asset, "PUT", claims_and_roles):
                raise PermissionError("Not authorized to update metadata for this asset")
        
        # Check if user is SYSTEM_USER - bypass schema validation
        username = claims_and_roles.get("tokens", ["SYSTEM_USER"])[0]
        skip_schema_validation = (username == "SYSTEM_USER")
        
        # Schema validation for non-SYSTEM_USER users
        composite_key = f"{database_id}:{asset_id}:/"
        if not skip_schema_validation:
            try:
                # Fetch ALL existing metadata for this asset
                paginator = dynamodb_client.get_paginator('query')
                page_iterator = paginator.paginate(
                    TableName=asset_file_metadata_table_name,
                    IndexName='DatabaseIdAssetIdFilePathIndex',
                    KeyConditionExpression='#pk = :pkValue',
                    ExpressionAttributeNames={'#pk': 'databaseId:assetId:filePath'},
                    ExpressionAttributeValues={':pkValue': {'S': composite_key}}
                ).build_full_result()
                
                # Build existing metadata dict
                existing_metadata = stored_metadata_entries(page_iterator.get('Items', []))
                
                # Validate 500 record limit based on updateType
                if request_model.updateType == UpdateType.UPDATE:
                    # For UPDATE: Check final count after merge
                    new_unique_keys = {item.metadataKey for item in request_model.metadata}
                    existing_keys = set(existing_metadata.keys())
                    final_count = len(existing_keys.union(new_unique_keys))
                    
                    if final_count > MAX_METADATA_RECORDS_PER_ENTITY:
                        raise VAMSGeneralErrorResponse(
                            f"Maximum {MAX_METADATA_RECORDS_PER_ENTITY} metadata records allowed per entity "
                            f"(current: {len(existing_keys)}, attempting to add: {len(new_unique_keys)}, final would be: {final_count})"
                        )
                    
                    # Merge with updates
                    for item in request_model.metadata:
                        existing_metadata[item.metadataKey] = {
                            'metadataValue': item.metadataValue,
                            'metadataValueType': item.metadataValueType.value
                        }
                    metadata_to_validate = existing_metadata
                else:  # REPLACE_ALL
                    # For REPLACE_ALL: Just check incoming count
                    if len(request_model.metadata) > MAX_METADATA_RECORDS_PER_ENTITY:
                        raise VAMSGeneralErrorResponse(
                            f"Maximum {MAX_METADATA_RECORDS_PER_ENTITY} metadata records allowed per entity "
                            f"(attempting to set: {len(request_model.metadata)})"
                        )
                    
                    # Validate only provided metadata (all-or-nothing)
                    metadata_to_validate = {
                        item.metadataKey: {
                            'metadataValue': item.metadataValue,
                            'metadataValueType': item.metadataValueType.value
                        }
                        for item in request_model.metadata
                    }
                
                # Get schemas and validate
                database_ids = [database_id, 'GLOBAL']
                
                aggregated_schema = get_aggregated_schemas(
                    database_ids=database_ids,
                    entity_type='assetMetadata',
                    file_path=None,
                    dynamodb_client=dynamodb_client,
                    schema_table_name=metadata_schema_table_v2_name
                )
                
                is_valid, errors, metadata_with_defaults = validate_metadata_against_schema(
                    metadata_to_validate, aggregated_schema, "PUT", existing_metadata
                )
                
                if not is_valid:
                    error_message = "Schema validation failed: " + "; ".join(errors)
                    raise VAMSGeneralErrorResponse(error_message)
                
                # Check restrictMetadataOutsideSchemas setting (only if schemas exist)
                if aggregated_schema:
                    db_config = get_database_config(database_id)
                    restrict = db_config.get('restrictMetadataOutsideSchemas', False)
                    
                    if restrict:
                        keys_valid, key_errors = validate_metadata_keys_against_schema(
                            metadata_to_validate, aggregated_schema, True
                        )
                        if not keys_valid:
                            error_message = "Metadata key validation failed: " + "; ".join(key_errors)
                            raise VAMSGeneralErrorResponse(error_message)
                
            except VAMSGeneralErrorResponse:
                raise
            except Exception as e:
                logger.exception(f"Error during schema validation: {e}")
                raise VAMSGeneralErrorResponse(SCHEMA_VALIDATION_UNAVAILABLE_MESSAGE)
        
        # Route to appropriate operation based on updateType
        if request_model.updateType == UpdateType.REPLACE_ALL:
            # REPLACE_ALL: Delete unlisted keys, then upsert all provided
            return _replace_all_asset_metadata(database_id, asset_id, request_model.metadata, claims_and_roles)
        else:
            # UPDATE: Upsert provided metadata (create or update)
            return _upsert_asset_metadata(database_id, asset_id, request_model.metadata, claims_and_roles)
        
    except PermissionError as p:
        raise p
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error updating asset metadata: {e}")
        raise VAMSGeneralErrorResponse("Error updating metadata")


def _upsert_asset_metadata(database_id: str, asset_id: str, metadata_items: list, claims_and_roles: dict) -> BulkOperationResponseModel:
    """Internal helper: Upsert asset metadata (create or update)"""
    try:
        successful_items = []
        failed_items = []
        items_to_write = []
        composite_key = f"{database_id}:{asset_id}:/"
        asset_composite_key = f"{database_id}:{asset_id}"
        
        for metadata_item in metadata_items:
            try:
                # Prepare item for upsert (will create or update)
                item = {
                    'metadataKey': {'S': metadata_item.metadataKey},
                    'databaseId:assetId:filePath': {'S': composite_key},
                    'databaseId:assetId': {'S': asset_composite_key},
                    'metadataValue': {'S': metadata_item.metadataValue},
                    'metadataValueType': {'S': metadata_item.metadataValueType.value}
                }
                
                items_to_write.append({'PutRequest': {'Item': item}})
                successful_items.append(metadata_item.metadataKey)
                
            except Exception as e:
                logger.warning(f"Error preparing metadata item {metadata_item.metadataKey}: {e}")
                failed_items.append({
                    'key': metadata_item.metadataKey,
                    'error': str(e)
                })
        
        # Write items in batches of 25
        if items_to_write:
            for i in range(0, len(items_to_write), 25):
                batch = items_to_write[i:i+25]
                try:
                    batch_write_with_retry(asset_file_metadata_table_name, batch)
                except Exception as e:
                    logger.exception(f"Error in batch write: {e}")
                    for item in batch:
                        key = item['PutRequest']['Item']['metadataKey']['S']
                        if key in successful_items:
                            successful_items.remove(key)
                        failed_items.append({
                            'key': key,
                            'error': 'Batch write failed'
                        })
        
        # Build response
        timestamp = datetime.utcnow().isoformat()
        total_items = len(metadata_items)
        success_count = len(successful_items)
        failure_count = len(failed_items)
        
        return BulkOperationResponseModel(
            success=success_count > 0,
            totalItems=total_items,
            successCount=success_count,
            failureCount=failure_count,
            successfulItems=successful_items,
            failedItems=failed_items,
            message=f"Upserted {success_count} of {total_items} metadata items",
            timestamp=timestamp
        )
        
    except Exception as e:
        logger.exception(f"Error in upsert operation: {e}")
        raise VAMSGeneralErrorResponse("Error upserting metadata")


def _replace_all_asset_metadata(database_id: str, asset_id: str, metadata_items: list, claims_and_roles: dict) -> BulkOperationResponseModel:
    """Internal helper: Replace all asset metadata with rollback on failure"""
    try:
        composite_key = f"{database_id}:{asset_id}:/"
        
        # Step 1: Fetch all existing metadata
        paginator = dynamodb_client.get_paginator('query')
        page_iterator = paginator.paginate(
            TableName=asset_file_metadata_table_name,
            IndexName='DatabaseIdAssetIdFilePathIndex',
            KeyConditionExpression='#pk = :pkValue',
            ExpressionAttributeNames={'#pk': 'databaseId:assetId:filePath'},
            ExpressionAttributeValues={':pkValue': {'S': composite_key}}
        ).build_full_result()
        
        existing_metadata = []
        deserializer = TypeDeserializer()
        for item in page_iterator.get('Items', []):
            deserialized = {k: deserializer.deserialize(v) for k, v in item.items()}
            existing_metadata.append(deserialized)
        
        # Step 2: Determine which keys to delete
        provided_keys = {item.metadataKey for item in metadata_items}
        existing_keys = {item['metadataKey'] for item in existing_metadata}
        keys_to_delete = existing_keys - provided_keys
        
        # Store items to delete for potential rollback
        deleted_items_backup = [
            item for item in existing_metadata 
            if item['metadataKey'] in keys_to_delete
        ]
        
        logger.info(f"REPLACE_ALL: Deleting {len(keys_to_delete)} keys, upserting {len(provided_keys)} keys")
        
        # Step 3: Delete keys not in provided list
        if keys_to_delete:
            items_to_delete = []
            for key in keys_to_delete:
                items_to_delete.append({
                    'DeleteRequest': {
                        'Key': {
                            'metadataKey': {'S': key},
                            'databaseId:assetId:filePath': {'S': composite_key}
                        }
                    }
                })
            
            # Delete in batches of 25
            for i in range(0, len(items_to_delete), 25):
                batch = items_to_delete[i:i+25]
                try:
                    batch_write_with_retry(asset_file_metadata_table_name, batch)
                except Exception as e:
                    logger.exception(f"Error deleting metadata in REPLACE_ALL: {e}")
                    raise VAMSGeneralErrorResponse("Failed to delete existing metadata")
        
        # Step 4: Upsert all provided metadata
        try:
            items_to_write = []
            asset_composite_key = f"{database_id}:{asset_id}"
            for metadata_item in metadata_items:
                item = {
                    'metadataKey': {'S': metadata_item.metadataKey},
                    'databaseId:assetId:filePath': {'S': composite_key},
                    'databaseId:assetId': {'S': asset_composite_key},
                    'metadataValue': {'S': metadata_item.metadataValue},
                    'metadataValueType': {'S': metadata_item.metadataValueType.value}
                }
                items_to_write.append({'PutRequest': {'Item': item}})
            
            # Write in batches of 25
            for i in range(0, len(items_to_write), 25):
                batch = items_to_write[i:i+25]
                batch_write_with_retry(asset_file_metadata_table_name, batch)
            
            # Success - build response
            timestamp = datetime.utcnow().isoformat()
            return BulkOperationResponseModel(
                success=True,
                totalItems=len(metadata_items),
                successCount=len(metadata_items),
                failureCount=0,
                successfulItems=[item.metadataKey for item in metadata_items],
                failedItems=[],
                message=f"Replaced all metadata: deleted {len(keys_to_delete)} keys, upserted {len(metadata_items)} keys",
                timestamp=timestamp
            )
            
        except Exception as upsert_error:
            # Step 5: Rollback - attempt to restore deleted items
            logger.error(f"Upsert failed in REPLACE_ALL, attempting rollback: {upsert_error}")
            
            if deleted_items_backup:
                try:
                    # Restore deleted items
                    items_to_restore = []
                    for item in deleted_items_backup:
                        restore_item = {
                            'metadataKey': {'S': item['metadataKey']},
                            'databaseId:assetId:filePath': {'S': composite_key},
                            'databaseId:assetId': {'S': asset_composite_key},
                            # Carried only when the backup holds them: a row written by an
                            # earlier release can lack either attribute, and subscripting it
                            # here raised before any write was issued - so a single legacy row
                            # in the deleted set meant NO row was restored and the metadata was
                            # permanently lost. A rollback reinstates what was there, so an
                            # absent attribute stays absent rather than gaining a default.
                            **({'metadataValue': {'S': item['metadataValue']}}
                               if item.get('metadataValue') is not None else {}),
                            **({'metadataValueType': {'S': item['metadataValueType']}}
                               if item.get('metadataValueType') is not None else {}),
                        }
                        items_to_restore.append({'PutRequest': {'Item': restore_item}})
                    
                    # Restore in batches of 25
                    for i in range(0, len(items_to_restore), 25):
                        batch = items_to_restore[i:i+25]
                        batch_write_with_retry(asset_file_metadata_table_name, batch)
                    
                    logger.info(f"Rollback successful: restored {len(deleted_items_backup)} deleted items")
                    rollback_succeeded = True

                except Exception as rollback_error:
                    logger.error(f"Rollback failed: {rollback_error}")
                    rollback_succeeded = False

                # Reported outside the rollback try so the except arm above cannot catch
                # this signal and describe a completed rollback as an inconsistent one.
                if rollback_succeeded:
                    raise VAMSGeneralErrorResponse(
                        "REPLACE_ALL operation failed, all changes rolled back successfully"
                    )
                raise VAMSGeneralErrorResponse(
                    "REPLACE_ALL operation failed and rollback unsuccessful - data may be inconsistent. "
                    "Please contact administrator."
                )
            else:
                # No items were deleted, so just report the upsert failure
                raise VAMSGeneralErrorResponse(f"REPLACE_ALL operation failed during upsert: {str(upsert_error)}")
        
    except PermissionError as p:
        raise p
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error in REPLACE_ALL operation: {e}")
        raise VAMSGeneralErrorResponse("Error in REPLACE_ALL operation")


def delete_asset_metadata(database_id: str, asset_id: str, request_model: DeleteAssetMetadataRequestModel, claims_and_roles: dict) -> BulkOperationResponseModel:
    """Delete metadata for an asset (bulk operation)
    
    Args:
        database_id: The database ID
        asset_id: The asset ID
        request_model: The delete request model with metadata keys
        claims_and_roles: User claims and roles
        
    Returns:
        BulkOperationResponseModel with operation results
    """
    try:
        # Validate asset exists and check authorization
        asset = validate_asset_exists(database_id, asset_id)
        asset.update({"object__type": "asset"})
        
        if not check_entity_authorization(asset, "DELETE", claims_and_roles):
            raise PermissionError("Not authorized to delete metadata for this asset")
        
        # NEW: Schema validation for deletion
        composite_key = f"{database_id}:{asset_id}:/"
        try:
            # Fetch all existing metadata
            paginator = dynamodb_client.get_paginator('query')
            page_iterator = paginator.paginate(
                TableName=asset_file_metadata_table_name,
                IndexName='DatabaseIdAssetIdFilePathIndex',
                KeyConditionExpression='#pk = :pkValue',
                ExpressionAttributeNames={'#pk': 'databaseId:assetId:filePath'},
                ExpressionAttributeValues={':pkValue': {'S': composite_key}}
            ).build_full_result()
            
            existing_metadata = {}
            deserializer = TypeDeserializer()
            for item in page_iterator.get('Items', []):
                deserialized = {k: deserializer.deserialize(v) for k, v in item.items()}
                # .get: only the remaining KEYS matter here, and a row written by an earlier
                # version may carry neither attribute. The block below denies on error.
                existing_metadata[deserialized.get('metadataKey')] = {
                    'metadataValue': deserialized.get('metadataValue'),
                    'metadataValueType': deserialized.get('metadataValueType')
                }
            
            # Calculate remaining metadata after deletion
            remaining_metadata = {
                k: v for k, v in existing_metadata.items() 
                if k not in request_model.metadataKeys
            }
            
            # Get schemas and validate deletion
            database_ids = [database_id, 'GLOBAL']
            
            aggregated_schema = get_aggregated_schemas(
                database_ids=database_ids,
                entity_type='assetMetadata',
                file_path=None,
                dynamodb_client=dynamodb_client,
                schema_table_name=metadata_schema_table_v2_name
            )
            
            # Validate deletion
            from common.metadataSchemaValidation import validate_metadata_deletion
            is_valid, validation_errors = validate_metadata_deletion(
                request_model.metadataKeys,
                remaining_metadata,
                aggregated_schema
            )
            
            if not is_valid:
                logger.warning(
                    f"Deletion validation failed: {'; '.join(validation_errors)}")
                raise VAMSGeneralErrorResponse(SCHEMA_DELETION_NOT_ALLOWED_MESSAGE)
                
        except VAMSGeneralErrorResponse:
            raise
        except Exception as e:
            # Fail closed like the write path: this block is the only guard on removing a
            # schema-required field, and swallowing the error deleted the keys unvalidated.
            logger.exception(f"Error during deletion validation: {e}")
            raise VAMSGeneralErrorResponse(SCHEMA_DELETION_VALIDATION_UNAVAILABLE_MESSAGE)
        
        # Process metadata keys
        successful_items = []
        failed_items = []
        items_to_delete = []
        
        for metadata_key in request_model.metadataKeys:
            try:
                # Check if metadata exists
                existing_response = asset_file_metadata_table.get_item(
                    Key={
                        'metadataKey': metadata_key,
                        'databaseId:assetId:filePath': composite_key
                    }
                )
                
                if 'Item' not in existing_response:
                    failed_items.append({
                        'key': metadata_key,
                        'error': 'Metadata key not found'
                    })
                    continue
                
                # Prepare item for batch delete
                items_to_delete.append({
                    'DeleteRequest': {
                        'Key': {
                            'metadataKey': {'S': metadata_key},
                            'databaseId:assetId:filePath': {'S': composite_key}
                        }
                    }
                })
                successful_items.append(metadata_key)
                
            except Exception as e:
                logger.warning(f"Error preparing delete for metadata key {metadata_key}: {e}")
                failed_items.append({
                    'key': metadata_key,
                    'error': str(e)
                })
        
        # Delete items in batches of 25
        if items_to_delete:
            for i in range(0, len(items_to_delete), 25):
                batch = items_to_delete[i:i+25]
                try:
                    batch_write_with_retry(asset_file_metadata_table_name, batch)
                except Exception as e:
                    logger.exception(f"Error in batch delete: {e}")
                    for item in batch:
                        key = item['DeleteRequest']['Key']['metadataKey']['S']
                        if key in successful_items:
                            successful_items.remove(key)
                        failed_items.append({
                            'key': key,
                            'error': 'Batch delete failed'
                        })
        
        # Build response
        timestamp = datetime.utcnow().isoformat()
        total_items = len(request_model.metadataKeys)
        success_count = len(successful_items)
        failure_count = len(failed_items)
        
        return BulkOperationResponseModel(
            success=success_count > 0,
            totalItems=total_items,
            successCount=success_count,
            failureCount=failure_count,
            successfulItems=successful_items,
            failedItems=failed_items,
            message=f"Deleted {success_count} of {total_items} metadata items",
            timestamp=timestamp
        )
        
    except PermissionError as p:
        raise p
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error deleting asset metadata: {e}")
        raise VAMSGeneralErrorResponse("Error deleting metadata")


#######################
# Request Handlers - Asset Metadata
#######################

def handle_asset_metadata_get(event):
    """Handle GET requests for asset metadata"""
    path_parameters = event.get('pathParameters', {})
    query_parameters = event.get('queryStringParameters', {}) or {}
    
    try:
        # Parse and validate path parameters (validation in model)
        try:
            path_request_model = parse(path_parameters, model=AssetMetadataPathRequestModel)
        except ValidationError as v:
            logger.exception(f"Validation error in path parameters: {v}")
            return validation_error(body={'message': validation_error_message(v)}, event=event)
        
        # Parse query parameters
        try:
            query_request_model = parse(query_parameters, model=GetAssetMetadataRequestModel)
            query_params = {
                'maxItems': query_request_model.maxItems,
                'pageSize': query_request_model.pageSize,
                'startingToken': query_request_model.startingToken,
                'assetVersionId': query_request_model.assetVersionId
            }
        except ValidationError as v:
            logger.exception(f"Validation error in query parameters: {v}")
            return validation_error(body={'message': validation_error_message(v)}, event=event)
        
        # Get metadata
        response = get_asset_metadata(path_request_model.databaseId, path_request_model.assetId, query_params, claims_and_roles)
        return success(body=response.dict())
        
    except PermissionError as p:
        logger.warning(f"Permission error: {p}")
        return authorization_error(body={'message': str(p)})
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling GET request: {e}")
        return internal_error(event=event)


def handle_asset_metadata_post(event):
    """Handle POST requests to create asset metadata"""
    path_parameters = event.get('pathParameters', {})
    
    try:
        # Parse and validate path parameters (validation in model)
        try:
            path_request_model = parse(path_parameters, model=AssetMetadataPathRequestModel)
        except ValidationError as v:
            logger.exception(f"Validation error in path parameters: {v}")
            return validation_error(body={'message': validation_error_message(v)}, event=event)
        
        # Parse request body
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"}, event=event)
        
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"}, event=event)
        elif isinstance(body, dict):
            body = body
        else:
            logger.error("Request body is not a string or dict")
            return validation_error(body={'message': "Request body cannot be parsed"}, event=event)
        
        # Parse and validate the request model
        request_model = parse(body, model=CreateAssetMetadataRequestModel)
        
        # Create metadata
        response = create_asset_metadata(path_request_model.databaseId, path_request_model.assetId, request_model, claims_and_roles)
        return success(body=response.dict())
        
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': validation_error_message(v)}, event=event)
    except PermissionError as p:
        logger.warning(f"Permission error: {p}")
        return authorization_error(body={'message': str(p)})
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling POST request: {e}")
        return internal_error(event=event)


def handle_asset_metadata_put(event):
    """Handle PUT requests to update asset metadata"""
    path_parameters = event.get('pathParameters', {})
    
    try:
        # Parse and validate path parameters (validation in model)
        try:
            path_request_model = parse(path_parameters, model=AssetMetadataPathRequestModel)
        except ValidationError as v:
            logger.exception(f"Validation error in path parameters: {v}")
            return validation_error(body={'message': validation_error_message(v)}, event=event)
        
        # Parse request body
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"}, event=event)
        
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"}, event=event)
        elif isinstance(body, dict):
            body = body
        else:
            logger.error("Request body is not a string or dict")
            return validation_error(body={'message': "Request body cannot be parsed"}, event=event)
        
        # Parse and validate the request model
        request_model = parse(body, model=UpdateAssetMetadataRequestModel)
        
        # Update metadata
        response = update_asset_metadata(path_request_model.databaseId, path_request_model.assetId, request_model, claims_and_roles)
        return success(body=response.dict())
        
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': validation_error_message(v)}, event=event)
    except PermissionError as p:
        logger.warning(f"Permission error: {p}")
        return authorization_error(body={'message': str(p)})
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling PUT request: {e}")
        return internal_error(event=event)


def handle_asset_metadata_delete(event):
    """Handle DELETE requests to delete asset metadata"""
    path_parameters = event.get('pathParameters', {})
    
    try:
        # Parse and validate path parameters (validation in model)
        try:
            path_request_model = parse(path_parameters, model=AssetMetadataPathRequestModel)
        except ValidationError as v:
            logger.exception(f"Validation error in path parameters: {v}")
            return validation_error(body={'message': validation_error_message(v)}, event=event)
        
        # Parse request body
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"}, event=event)
        
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"}, event=event)
        elif isinstance(body, dict):
            body = body
        else:
            logger.error("Request body is not a string or dict")
            return validation_error(body={'message': "Request body cannot be parsed"}, event=event)
        
        # Parse and validate the request model
        request_model = parse(body, model=DeleteAssetMetadataRequestModel)
        
        # Delete metadata
        response = delete_asset_metadata(path_request_model.databaseId, path_request_model.assetId, request_model, claims_and_roles)
        return success(body=response.dict())
        
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': validation_error_message(v)}, event=event)
    except PermissionError as p:
        logger.warning(f"Permission error: {p}")
        return authorization_error(body={'message': str(p)})
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling DELETE request: {e}")
        return internal_error(event=event)


#######################
# File Metadata/Attribute CRUD Operations
#######################

def get_file_metadata(database_id: str, asset_id: str, file_path: str, metadata_type: str, query_params: dict, claims_and_roles: dict):
    """Get metadata or attributes for a file - Returns one page of records"""
    # Check if querying a specific version
    asset_version_id = query_params.get('assetVersionId')
    if asset_version_id:
        return get_file_metadata_from_version(
            database_id, asset_id, file_path, metadata_type,
            asset_version_id, query_params, claims_and_roles
        )

    try:
        # No S3 validation for GET - metadata can exist even if file doesn't
        asset = validate_asset_exists(database_id, asset_id)
        asset.update({"object__type": "asset"})
        
        if not check_entity_authorization(asset, "GET", claims_and_roles):
            raise PermissionError("Not authorized to view metadata for this file")
        
        composite_key = f"{database_id}:{asset_id}:{file_path}"
        table_name = asset_file_metadata_table_name if metadata_type == 'metadata' else file_attribute_table_name
        
        # Fetch every record: the page is sliced after schema enrichment and ordering
        paginator = dynamodb_client.get_paginator('query')
        page_iterator = paginator.paginate(
            TableName=table_name,
            IndexName='DatabaseIdAssetIdFilePathIndex',
            KeyConditionExpression='#pk = :pkValue',
            ExpressionAttributeNames={'#pk': 'databaseId:assetId:filePath'},
            ExpressionAttributeValues={':pkValue': {'S': composite_key}},
            ScanIndexForward=False
        ).build_full_result()
        
        # Process ALL items
        metadata_list = []
        deserializer = TypeDeserializer()
        for item in page_iterator.get('Items', []):
            deserialized_item = {k: deserializer.deserialize(v) for k, v in item.items()}
            # Normalize field names to metadataKey/metadataValue/metadataValueType
            if metadata_type == 'attribute':
                key_field = deserialized_item.get('attributeKey', deserialized_item.get('metadataKey'))
                value_field = deserialized_item.get('attributeValue', deserialized_item.get('metadataValue'))
                type_field = deserialized_item.get('attributeValueType', deserialized_item.get('metadataValueType'))
            else:
                key_field = deserialized_item.get('metadataKey')
                value_field = deserialized_item.get('metadataValue')
                type_field = deserialized_item.get('metadataValueType')
            
            # Store as dict for enrichment
            metadata_list.append({
                'metadataKey': key_field,
                'metadataValue': value_field,
                'metadataValueType': type_field
            })
        
        # Fetch database config and schema enrichment
        restrict_metadata_outside_schemas = False
        try:
            database_ids = [database_id, 'GLOBAL']
            entity_type = 'fileMetadata' if metadata_type == 'metadata' else 'fileAttribute'
            
            aggregated_schema = get_aggregated_schemas(
                database_ids=database_ids,
                entity_type=entity_type,
                file_path=file_path,
                dynamodb_client=dynamodb_client,
                schema_table_name=metadata_schema_table_v2_name
            )
            
            # Calculate restrictMetadataOutsideSchemas
            schemas_exist = len(aggregated_schema) > 0
            if schemas_exist:
                try:
                    db_config = get_database_config(database_id)
                    db_restricts = db_config.get('restrictMetadataOutsideSchemas', False) == True
                    restrict_metadata_outside_schemas = db_restricts
                except Exception as e:
                    logger.warning(f"Error fetching database config for restriction check: {e}")
                    restrict_metadata_outside_schemas = False
            
            # Enrich metadata with schema information
            enriched_metadata = enrich_metadata_with_schema(metadata_list, aggregated_schema)

            # Convert to response models
            metadata_list = metadata_response_models(
                FileMetadataResponseModel, enriched_metadata,
                databaseId=database_id, assetId=asset_id, filePath=file_path)
        except Exception as e:
            logger.warning(f"Error enriching metadata with schema: {e}")
            # If schema enrichment fails, return metadata without enrichment
            metadata_list = metadata_response_models(
                FileMetadataResponseModel, metadata_list,
                databaseId=database_id, assetId=asset_id, filePath=file_path)
            restrict_metadata_outside_schemas = False

        # Offset-paginate the enriched, ordered list to bound the response payload.
        page, next_token = paginate_metadata_records(metadata_list, query_params)

        # Build response
        result = GetFileMetadataResponseModel(
            metadata=page,
            restrictMetadataOutsideSchemas=restrict_metadata_outside_schemas,
            NextToken=next_token
        )

        return result
    except PermissionError as p:
        raise p
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error getting file metadata: {e}")
        raise VAMSGeneralErrorResponse("Error retrieving metadata")


def get_file_metadata_from_version(database_id: str, asset_id: str, file_path: str,
                                   metadata_type: str, asset_version_id: str,
                                   query_params: dict, claims_and_roles: dict):
    """Get file metadata/attributes from a specific version snapshot"""
    try:
        asset = validate_asset_exists(database_id, asset_id)
        asset.update({"object__type": "asset"})

        if not check_entity_authorization(asset, "GET", claims_and_roles):
            raise PermissionError("Not authorized to view metadata for this file")

        # Validate asset version exists before querying metadata
        validate_asset_version_exists(database_id, asset_id, asset_version_id)

        metadata_items_raw = get_asset_metadata_version(database_id, asset_id, asset_version_id)

        if not metadata_items_raw:
            logger.info(f"No metadata found for version {asset_version_id}")
            return GetFileMetadataResponseModel(
                metadata=[],
                restrictMetadataOutsideSchemas=False
            )

        # Filter for this specific file and type
        file_metadata = [
            item for item in metadata_items_raw
            if item.filePath == file_path and item.type == metadata_type
        ]

        metadata_list = []
        for item in file_metadata:
            metadata_list.append({
                'metadataKey': item.metadataKey,
                'metadataValue': item.metadataValue,
                'metadataValueType': item.metadataValueType
            })

        # Apply schema enrichment
        restrict_metadata_outside_schemas = False
        try:
            database_ids = [database_id, 'GLOBAL']
            entity_type = 'fileMetadata' if metadata_type == 'metadata' else 'fileAttribute'

            aggregated_schema = get_aggregated_schemas(
                database_ids=database_ids,
                entity_type=entity_type,
                file_path=file_path,
                dynamodb_client=dynamodb_client,
                schema_table_name=metadata_schema_table_v2_name
            )

            schemas_exist = len(aggregated_schema) > 0
            if schemas_exist:
                try:
                    db_config = get_database_config(database_id)
                    db_restricts = db_config.get('restrictMetadataOutsideSchemas', False) == True
                    restrict_metadata_outside_schemas = db_restricts
                except Exception as e:
                    logger.warning(f"Error fetching database config for restriction check: {e}")
                    restrict_metadata_outside_schemas = False

            enriched_metadata = enrich_metadata_with_schema(metadata_list, aggregated_schema)

            metadata_list = metadata_response_models(
                FileMetadataResponseModel, enriched_metadata,
                databaseId=database_id, assetId=asset_id, filePath=file_path)
        except Exception as e:
            logger.warning(f"Error enriching version file metadata with schema: {e}")
            metadata_list = metadata_response_models(
                FileMetadataResponseModel, metadata_list,
                databaseId=database_id, assetId=asset_id, filePath=file_path)
            restrict_metadata_outside_schemas = False

        # Offset-paginate the enriched, ordered list to bound the response payload.
        page, next_token = paginate_metadata_records(metadata_list, query_params)

        return GetFileMetadataResponseModel(
            metadata=page,
            restrictMetadataOutsideSchemas=restrict_metadata_outside_schemas,
            NextToken=next_token
        )

    except PermissionError as p:
        raise p
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error getting file metadata from version: {e}")
        raise VAMSGeneralErrorResponse("Error retrieving file metadata from version")


def create_file_metadata(database_id: str, asset_id: str, request_model: CreateFileMetadataRequestModel, claims_and_roles: dict):
    """Create metadata or attributes for a file (bulk operation)"""
    try:
        validate_file_exists(database_id, asset_id, request_model.filePath)
        asset = validate_asset_exists(database_id, asset_id)
        asset.update({"object__type": "asset"})
        
        if not check_entity_authorization(asset, "POST", claims_and_roles):
            raise PermissionError("Not authorized to create metadata for this file")
        
        # Validate 500 record limit: Fetch existing + count with new (separate limits for metadata vs attributes)
        composite_key = f"{database_id}:{asset_id}:{request_model.filePath}"
        table_name_for_limit_check = asset_file_metadata_table_name if request_model.type == 'metadata' else file_attribute_table_name
        try:
            paginator = dynamodb_client.get_paginator('query')
            page_iterator = paginator.paginate(
                TableName=table_name_for_limit_check,
                IndexName='DatabaseIdAssetIdFilePathIndex',
                KeyConditionExpression='#pk = :pkValue',
                ExpressionAttributeNames={'#pk': 'databaseId:assetId:filePath'},
                ExpressionAttributeValues={':pkValue': {'S': composite_key}}
            ).build_full_result()
            
            existing_count = len(page_iterator.get('Items', []))
            new_unique_keys = {item.metadataKey for item in request_model.metadata}
            
            # Get existing keys to determine how many are truly new
            existing_keys = set()
            deserializer = TypeDeserializer()
            for item in page_iterator.get('Items', []):
                deserialized = {k: deserializer.deserialize(v) for k, v in item.items()}
                if request_model.type == 'attribute':
                    key = deserialized.get('attributeKey', deserialized.get('metadataKey'))
                else:
                    key = deserialized['metadataKey']
                existing_keys.add(key)
            
            # Calculate final count after upsert
            final_count = len(existing_keys.union(new_unique_keys))
            
            if final_count > MAX_METADATA_RECORDS_PER_ENTITY:
                raise VAMSGeneralErrorResponse(
                    f"Maximum {MAX_METADATA_RECORDS_PER_ENTITY} {request_model.type} records allowed per file "
                    f"(current: {existing_count}, attempting to add: {len(new_unique_keys)}, final would be: {final_count})"
                )
        except VAMSGeneralErrorResponse:
            raise
        except Exception as e:
            logger.warning(f"Error checking record limit: {e}")
            # Continue without limit check if it fails
        
        # Check if user is SYSTEM_USER - bypass schema validation
        username = claims_and_roles.get("tokens", ["SYSTEM_USER"])[0]
        skip_schema_validation = (username == "SYSTEM_USER")
        
        # Schema validation for non-SYSTEM_USER users
        if not skip_schema_validation:
            try:
                database_ids = [database_id, 'GLOBAL']
                entity_type = 'fileMetadata' if request_model.type == 'metadata' else 'fileAttribute'
                
                aggregated_schema = get_aggregated_schemas(
                    database_ids=database_ids,
                    entity_type=entity_type,
                    file_path=request_model.filePath,
                    dynamodb_client=dynamodb_client,
                    schema_table_name=metadata_schema_table_v2_name
                )
                
                # COMPREHENSIVE VALIDATION: Fetch existing metadata and merge with incoming
                paginator = dynamodb_client.get_paginator('query')
                page_iterator = paginator.paginate(
                    TableName=table_name_for_limit_check,
                    IndexName='DatabaseIdAssetIdFilePathIndex',
                    KeyConditionExpression='#pk = :pkValue',
                    ExpressionAttributeNames={'#pk': 'databaseId:assetId:filePath'},
                    ExpressionAttributeValues={':pkValue': {'S': composite_key}}
                ).build_full_result()
                
                # Build existing metadata dict (normalize field names)
                if request_model.type == 'attribute':
                    existing_metadata = stored_metadata_entries(
                        page_iterator.get('Items', []),
                        key_fields=('attributeKey', 'metadataKey'),
                        value_fields=('attributeValue', 'metadataValue'),
                        value_type_fields=('attributeValueType', 'metadataValueType'))
                else:
                    existing_metadata = stored_metadata_entries(page_iterator.get('Items', []))
                
                # Merge incoming metadata with existing (simulating upsert)
                merged_metadata = existing_metadata.copy()
                for item in request_model.metadata:
                    merged_metadata[item.metadataKey] = {
                        'metadataValue': item.metadataValue,
                        'metadataValueType': item.metadataValueType.value
                    }
                
                # Validate the complete merged state
                is_valid, errors, metadata_with_defaults = validate_metadata_against_schema(
                    merged_metadata, aggregated_schema, "POST", existing_metadata
                )
                
                if not is_valid:
                    error_message = "Schema validation failed: " + "; ".join(errors)
                    raise VAMSGeneralErrorResponse(error_message)
                
                # Check restrictMetadataOutsideSchemas setting (only if schemas exist)
                if aggregated_schema:
                    db_config = get_database_config(database_id)
                    restrict = db_config.get('restrictMetadataOutsideSchemas', False)
                    
                    if restrict:
                        keys_valid, key_errors = validate_metadata_keys_against_schema(
                            merged_metadata, aggregated_schema, True
                        )
                        if not keys_valid:
                            error_message = "Metadata key validation failed: " + "; ".join(key_errors)
                            raise VAMSGeneralErrorResponse(error_message)
                
                # Update request model with defaults applied (only for new fields).
                # This step is purely ADDITIVE and runs after every check above has passed,
                # so it is deliberately fail-open: a failure here loses schema-supplied
                # defaults and cannot admit anything validation refused. Guarded on its own so
                # the surrounding fail-closed arm does not turn it into a denied write.
                try:
                    updated_metadata = []
                    for item in request_model.metadata:
                        updated_metadata.append(item)

                    # Add any new fields with defaults that weren't in the request
                    for key, value_dict in metadata_with_defaults.items():
                        if key not in existing_metadata and not any(item.metadataKey == key for item in request_model.metadata):
                            from models.metadata import MetadataItemModel
                            updated_metadata.append(MetadataItemModel(
                                metadataKey=key,
                                metadataValue=value_dict['metadataValue'],
                                metadataValueType=value_dict['metadataValueType']
                            ))
                    request_model.metadata = updated_metadata
                except Exception as default_error:
                    logger.warning(
                        f"{SCHEMA_DEFAULT_INJECTION_FAILED_LOG}: {default_error}"
                    )
                
            except VAMSGeneralErrorResponse:
                raise
            except Exception as e:
                logger.exception(f"Error during schema validation: {e}")
                raise VAMSGeneralErrorResponse(SCHEMA_VALIDATION_UNAVAILABLE_MESSAGE)
        
        successful_items = []
        failed_items = []
        table_name = asset_file_metadata_table_name if request_model.type == 'metadata' else file_attribute_table_name
        table = asset_file_metadata_table if request_model.type == 'metadata' else file_attribute_table
        items_to_write = []
        
        # Composite key for asset-level lookups (without file path)
        asset_composite_key = f"{database_id}:{asset_id}"
        
        for metadata_item in request_model.metadata:
            try:
                # Prepare item for upsert (will create or update)
                if request_model.type == 'metadata':
                    item = {
                        'metadataKey': {'S': metadata_item.metadataKey},
                        'databaseId:assetId:filePath': {'S': composite_key},
                        'databaseId:assetId': {'S': asset_composite_key},
                        'metadataValue': {'S': metadata_item.metadataValue},
                        'metadataValueType': {'S': metadata_item.metadataValueType.value}
                    }
                else:  # attribute
                    item = {
                        'attributeKey': {'S': metadata_item.metadataKey},
                        'databaseId:assetId:filePath': {'S': composite_key},
                        'databaseId:assetId': {'S': asset_composite_key},
                        'attributeValue': {'S': metadata_item.metadataValue},
                        'attributeValueType': {'S': metadata_item.metadataValueType.value}
                    }
                
                items_to_write.append({'PutRequest': {'Item': item}})
                successful_items.append(metadata_item.metadataKey)
            except Exception as e:
                logger.warning(f"Error preparing {request_model.type} item {metadata_item.metadataKey}: {e}")
                failed_items.append({'key': metadata_item.metadataKey, 'error': str(e)})
        
        if items_to_write:
            for i in range(0, len(items_to_write), 25):
                batch = items_to_write[i:i+25]
                try:
                    batch_write_with_retry(table_name, batch)
                except Exception as e:
                    logger.exception(f"Error in batch write: {e}")
                    for item in batch:
                        key_field = 'metadataKey' if request_model.type == 'metadata' else 'attributeKey'
                        key = item['PutRequest']['Item'][key_field]['S']
                        if key in successful_items:
                            successful_items.remove(key)
                        failed_items.append({'key': key, 'error': 'Batch write failed'})
        
        timestamp = datetime.utcnow().isoformat()
        return BulkOperationResponseModel(
            success=len(successful_items) > 0,
            totalItems=len(request_model.metadata),
            successCount=len(successful_items),
            failureCount=len(failed_items),
            successfulItems=successful_items,
            failedItems=failed_items,
            message=f"Created {len(successful_items)} of {len(request_model.metadata)} {request_model.type} items",
            timestamp=timestamp
        )
    except PermissionError as p:
        raise p
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error creating file metadata: {e}")
        raise VAMSGeneralErrorResponse("Error creating metadata")


def update_file_metadata(database_id: str, asset_id: str, request_model: UpdateFileMetadataRequestModel, claims_and_roles: dict):
    """Update metadata or attributes for a file (bulk operation) - Supports UPDATE and REPLACE_ALL modes"""
    try:
        validate_file_exists(database_id, asset_id, request_model.filePath)
        asset = validate_asset_exists(database_id, asset_id)
        asset.update({"object__type": "asset"})
        
        # Check authorization based on updateType
        if request_model.updateType == UpdateType.REPLACE_ALL:
            # REPLACE_ALL requires PUT, POST, and DELETE permissions
            if not check_multi_action_authorization(asset, ["PUT", "POST", "DELETE"], claims_and_roles):
                raise PermissionError("REPLACE_ALL requires PUT, POST, and DELETE permissions")
        else:
            # UPDATE mode requires only PUT permission
            if not check_entity_authorization(asset, "PUT", claims_and_roles):
                raise PermissionError("Not authorized to update metadata for this file")
        
        # Check if user is SYSTEM_USER - bypass schema validation
        username = claims_and_roles.get("tokens", ["SYSTEM_USER"])[0]
        skip_schema_validation = (username == "SYSTEM_USER")
        
        # Schema validation for non-SYSTEM_USER users
        composite_key = f"{database_id}:{asset_id}:{request_model.filePath}"
        if not skip_schema_validation:
            try:
                # Fetch ALL existing metadata for this file
                table_name_for_query = asset_file_metadata_table_name if request_model.type == 'metadata' else file_attribute_table_name
                paginator = dynamodb_client.get_paginator('query')
                page_iterator = paginator.paginate(
                    TableName=table_name_for_query,
                    IndexName='DatabaseIdAssetIdFilePathIndex',
                    KeyConditionExpression='#pk = :pkValue',
                    ExpressionAttributeNames={'#pk': 'databaseId:assetId:filePath'},
                    ExpressionAttributeValues={':pkValue': {'S': composite_key}}
                ).build_full_result()
                
                # Build existing metadata dict (normalize field names)
                if request_model.type == 'attribute':
                    existing_metadata = stored_metadata_entries(
                        page_iterator.get('Items', []),
                        key_fields=('attributeKey', 'metadataKey'),
                        value_fields=('attributeValue', 'metadataValue'),
                        value_type_fields=('attributeValueType', 'metadataValueType'))
                else:
                    existing_metadata = stored_metadata_entries(page_iterator.get('Items', []))
                
                # Validate 500 record limit based on updateType (separate limits for metadata vs attributes)
                if request_model.updateType == UpdateType.UPDATE:
                    # For UPDATE: Check final count after merge
                    new_unique_keys = {item.metadataKey for item in request_model.metadata}
                    existing_keys = set(existing_metadata.keys())
                    final_count = len(existing_keys.union(new_unique_keys))
                    
                    if final_count > MAX_METADATA_RECORDS_PER_ENTITY:
                        raise VAMSGeneralErrorResponse(
                            f"Maximum {MAX_METADATA_RECORDS_PER_ENTITY} {request_model.type} records allowed per file "
                            f"(current: {len(existing_keys)}, attempting to add: {len(new_unique_keys)}, final would be: {final_count})"
                        )
                    
                    # Merge with updates
                    for item in request_model.metadata:
                        existing_metadata[item.metadataKey] = {
                            'metadataValue': item.metadataValue,
                            'metadataValueType': item.metadataValueType.value
                        }
                    metadata_to_validate = existing_metadata
                else:  # REPLACE_ALL
                    # For REPLACE_ALL: Just check incoming count
                    if len(request_model.metadata) > MAX_METADATA_RECORDS_PER_ENTITY:
                        raise VAMSGeneralErrorResponse(
                            f"Maximum {MAX_METADATA_RECORDS_PER_ENTITY} {request_model.type} records allowed per file "
                            f"(attempting to set: {len(request_model.metadata)})"
                        )
                    
                    # Validate only provided metadata (all-or-nothing)
                    metadata_to_validate = {
                        item.metadataKey: {
                            'metadataValue': item.metadataValue,
                            'metadataValueType': item.metadataValueType.value
                        }
                        for item in request_model.metadata
                    }
                
                # Get schemas and validate
                database_ids = [database_id, 'GLOBAL']
                entity_type = 'fileMetadata' if request_model.type == 'metadata' else 'fileAttribute'
                
                aggregated_schema = get_aggregated_schemas(
                    database_ids=database_ids,
                    entity_type=entity_type,
                    file_path=request_model.filePath,
                    dynamodb_client=dynamodb_client,
                    schema_table_name=metadata_schema_table_v2_name
                )
                
                is_valid, errors, metadata_with_defaults = validate_metadata_against_schema(
                    metadata_to_validate, aggregated_schema, "PUT", existing_metadata
                )
                
                if not is_valid:
                    error_message = "Schema validation failed: " + "; ".join(errors)
                    raise VAMSGeneralErrorResponse(error_message)
                
                # Check restrictMetadataOutsideSchemas setting (only if schemas exist)
                if aggregated_schema:
                    db_config = get_database_config(database_id)
                    restrict = db_config.get('restrictMetadataOutsideSchemas', False)
                    
                    if restrict:
                        keys_valid, key_errors = validate_metadata_keys_against_schema(
                            metadata_to_validate, aggregated_schema, True
                        )
                        if not keys_valid:
                            error_message = "Metadata key validation failed: " + "; ".join(key_errors)
                            raise VAMSGeneralErrorResponse(error_message)
                
            except VAMSGeneralErrorResponse:
                raise
            except Exception as e:
                logger.exception(f"Error during schema validation: {e}")
                raise VAMSGeneralErrorResponse(SCHEMA_VALIDATION_UNAVAILABLE_MESSAGE)
        
        # Route to appropriate operation based on updateType
        if request_model.updateType == UpdateType.REPLACE_ALL:
            # REPLACE_ALL: Delete unlisted keys, then upsert all provided
            return _replace_all_file_metadata(database_id, asset_id, request_model.filePath, request_model.type, request_model.metadata, claims_and_roles)
        else:
            # UPDATE: Upsert provided metadata (create or update)
            return _upsert_file_metadata(database_id, asset_id, request_model.filePath, request_model.type, request_model.metadata, claims_and_roles)
        
    except PermissionError as p:
        raise p
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error updating file metadata: {e}")
        raise VAMSGeneralErrorResponse("Error updating metadata")


def _upsert_file_metadata(database_id: str, asset_id: str, file_path: str, metadata_type: str, metadata_items: list, claims_and_roles: dict) -> BulkOperationResponseModel:
    """Internal helper: Upsert file metadata/attributes (create or update)"""
    try:
        successful_items = []
        failed_items = []
        items_to_write = []
        composite_key = f"{database_id}:{asset_id}:{file_path}"
        asset_composite_key = f"{database_id}:{asset_id}"
        table_name = asset_file_metadata_table_name if metadata_type == 'metadata' else file_attribute_table_name
        
        for metadata_item in metadata_items:
            try:
                # Prepare item for upsert (will create or update)
                if metadata_type == 'metadata':
                    item = {
                        'metadataKey': {'S': metadata_item.metadataKey},
                        'databaseId:assetId:filePath': {'S': composite_key},
                        'databaseId:assetId': {'S': asset_composite_key},
                        'metadataValue': {'S': metadata_item.metadataValue},
                        'metadataValueType': {'S': metadata_item.metadataValueType.value}
                    }
                else:  # attribute
                    item = {
                        'attributeKey': {'S': metadata_item.metadataKey},
                        'databaseId:assetId:filePath': {'S': composite_key},
                        'databaseId:assetId': {'S': asset_composite_key},
                        'attributeValue': {'S': metadata_item.metadataValue},
                        'attributeValueType': {'S': metadata_item.metadataValueType.value}
                    }
                
                items_to_write.append({'PutRequest': {'Item': item}})
                successful_items.append(metadata_item.metadataKey)
                
            except Exception as e:
                logger.warning(f"Error preparing {metadata_type} item {metadata_item.metadataKey}: {e}")
                failed_items.append({
                    'key': metadata_item.metadataKey,
                    'error': str(e)
                })
        
        # Write items in batches of 25
        if items_to_write:
            for i in range(0, len(items_to_write), 25):
                batch = items_to_write[i:i+25]
                try:
                    batch_write_with_retry(table_name, batch)
                except Exception as e:
                    logger.exception(f"Error in batch write: {e}")
                    for item in batch:
                        key_field = 'metadataKey' if metadata_type == 'metadata' else 'attributeKey'
                        key = item['PutRequest']['Item'][key_field]['S']
                        if key in successful_items:
                            successful_items.remove(key)
                        failed_items.append({
                            'key': key,
                            'error': 'Batch write failed'
                        })
        
        # Build response
        timestamp = datetime.utcnow().isoformat()
        total_items = len(metadata_items)
        success_count = len(successful_items)
        failure_count = len(failed_items)
        
        return BulkOperationResponseModel(
            success=success_count > 0,
            totalItems=total_items,
            successCount=success_count,
            failureCount=failure_count,
            successfulItems=successful_items,
            failedItems=failed_items,
            message=f"Upserted {success_count} of {total_items} {metadata_type} items",
            timestamp=timestamp
        )
        
    except Exception as e:
        logger.exception(f"Error in upsert operation: {e}")
        raise VAMSGeneralErrorResponse("Error upserting metadata")


def _replace_all_file_metadata(database_id: str, asset_id: str, file_path: str, metadata_type: str, metadata_items: list, claims_and_roles: dict) -> BulkOperationResponseModel:
    """Internal helper: Replace all file metadata/attributes with rollback on failure"""
    try:
        composite_key = f"{database_id}:{asset_id}:{file_path}"
        table_name = asset_file_metadata_table_name if metadata_type == 'metadata' else file_attribute_table_name
        
        # Step 1: Fetch all existing metadata
        paginator = dynamodb_client.get_paginator('query')
        page_iterator = paginator.paginate(
            TableName=table_name,
            IndexName='DatabaseIdAssetIdFilePathIndex',
            KeyConditionExpression='#pk = :pkValue',
            ExpressionAttributeNames={'#pk': 'databaseId:assetId:filePath'},
            ExpressionAttributeValues={':pkValue': {'S': composite_key}}
        ).build_full_result()
        
        existing_metadata = []
        deserializer = TypeDeserializer()
        for item in page_iterator.get('Items', []):
            deserialized = {k: deserializer.deserialize(v) for k, v in item.items()}
            existing_metadata.append(deserialized)
        
        # Step 2: Determine which keys to delete
        provided_keys = {item.metadataKey for item in metadata_items}
        if metadata_type == 'metadata':
            existing_keys = {item['metadataKey'] for item in existing_metadata}
        else:  # attribute
            existing_keys = {item.get('attributeKey', item.get('metadataKey')) for item in existing_metadata}
        keys_to_delete = existing_keys - provided_keys
        
        # Store items to delete for potential rollback
        deleted_items_backup = [
            item for item in existing_metadata 
            if (item.get('metadataKey') if metadata_type == 'metadata' else item.get('attributeKey', item.get('metadataKey'))) in keys_to_delete
        ]
        
        logger.info(f"REPLACE_ALL: Deleting {len(keys_to_delete)} keys, upserting {len(provided_keys)} keys")
        
        # Step 3: Delete keys not in provided list
        if keys_to_delete:
            items_to_delete = []
            for key in keys_to_delete:
                if metadata_type == 'metadata':
                    items_to_delete.append({
                        'DeleteRequest': {
                            'Key': {
                                'metadataKey': {'S': key},
                                'databaseId:assetId:filePath': {'S': composite_key}
                            }
                        }
                    })
                else:  # attribute
                    items_to_delete.append({
                        'DeleteRequest': {
                            'Key': {
                                'attributeKey': {'S': key},
                                'databaseId:assetId:filePath': {'S': composite_key}
                            }
                        }
                    })
            
            # Delete in batches of 25
            for i in range(0, len(items_to_delete), 25):
                batch = items_to_delete[i:i+25]
                try:
                    batch_write_with_retry(table_name, batch)
                except Exception as e:
                    logger.exception(f"Error deleting {metadata_type} in REPLACE_ALL: {e}")
                    raise VAMSGeneralErrorResponse(f"Failed to delete existing {metadata_type}")
        
        # Step 4: Upsert all provided metadata
        try:
            items_to_write = []
            asset_composite_key = f"{database_id}:{asset_id}"
            for metadata_item in metadata_items:
                if metadata_type == 'metadata':
                    item = {
                        'metadataKey': {'S': metadata_item.metadataKey},
                        'databaseId:assetId:filePath': {'S': composite_key},
                        'databaseId:assetId': {'S': asset_composite_key},
                        'metadataValue': {'S': metadata_item.metadataValue},
                        'metadataValueType': {'S': metadata_item.metadataValueType.value}
                    }
                else:  # attribute
                    item = {
                        'attributeKey': {'S': metadata_item.metadataKey},
                        'databaseId:assetId:filePath': {'S': composite_key},
                        'databaseId:assetId': {'S': asset_composite_key},
                        'attributeValue': {'S': metadata_item.metadataValue},
                        'attributeValueType': {'S': metadata_item.metadataValueType.value}
                    }
                items_to_write.append({'PutRequest': {'Item': item}})
            
            # Write in batches of 25
            for i in range(0, len(items_to_write), 25):
                batch = items_to_write[i:i+25]
                batch_write_with_retry(table_name, batch)
            
            # Success - build response
            timestamp = datetime.utcnow().isoformat()
            return BulkOperationResponseModel(
                success=True,
                totalItems=len(metadata_items),
                successCount=len(metadata_items),
                failureCount=0,
                successfulItems=[item.metadataKey for item in metadata_items],
                failedItems=[],
                message=f"Replaced all {metadata_type}: deleted {len(keys_to_delete)} keys, upserted {len(metadata_items)} keys",
                timestamp=timestamp
            )
            
        except Exception as upsert_error:
            # Step 5: Rollback - attempt to restore deleted items
            logger.error(f"Upsert failed in REPLACE_ALL, attempting rollback: {upsert_error}")
            
            if deleted_items_backup:
                try:
                    # Restore deleted items
                    items_to_restore = []
                    for item in deleted_items_backup:
                        if metadata_type == 'metadata':
                            restore_item = {
                                'metadataKey': {'S': item['metadataKey']},
                                'databaseId:assetId:filePath': {'S': composite_key},
                                'databaseId:assetId': {'S': asset_composite_key},
                                # See the sibling rollback loops: an attribute absent from a
                                # row written by an earlier release must stay absent, and must
                                # not raise before the restore writes anything.
                                **({'metadataValue': {'S': item['metadataValue']}}
                                   if item.get('metadataValue') is not None else {}),
                                **({'metadataValueType': {'S': item['metadataValueType']}}
                                   if item.get('metadataValueType') is not None else {}),
                            }
                        else:  # attribute
                            key = item.get('attributeKey', item.get('metadataKey'))
                            value = item.get('attributeValue', item.get('metadataValue'))
                            value_type = item.get('attributeValueType', item.get('metadataValueType'))
                            restore_item = {
                                'attributeKey': {'S': key},
                                'databaseId:assetId:filePath': {'S': composite_key},
                                'databaseId:assetId': {'S': asset_composite_key},
                                'attributeValue': {'S': value},
                                'attributeValueType': {'S': value_type}
                            }
                        items_to_restore.append({'PutRequest': {'Item': restore_item}})
                    
                    # Restore in batches of 25
                    for i in range(0, len(items_to_restore), 25):
                        batch = items_to_restore[i:i+25]
                        batch_write_with_retry(table_name, batch)
                    
                    logger.info(f"Rollback successful: restored {len(deleted_items_backup)} deleted items")
                    rollback_succeeded = True

                except Exception as rollback_error:
                    logger.error(f"Rollback failed: {rollback_error}")
                    rollback_succeeded = False

                # Reported outside the rollback try so the except arm above cannot catch
                # this signal and describe a completed rollback as an inconsistent one.
                if rollback_succeeded:
                    raise VAMSGeneralErrorResponse(
                        "REPLACE_ALL operation failed, all changes rolled back successfully"
                    )
                raise VAMSGeneralErrorResponse(
                    "REPLACE_ALL operation failed and rollback unsuccessful - data may be inconsistent. "
                    "Please contact administrator."
                )
            else:
                # No items were deleted, so just report the upsert failure
                raise VAMSGeneralErrorResponse(f"REPLACE_ALL operation failed during upsert: {str(upsert_error)}")
        
    except PermissionError as p:
        raise p
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error in REPLACE_ALL operation: {e}")
        raise VAMSGeneralErrorResponse("Error in REPLACE_ALL operation")


def delete_file_metadata(database_id: str, asset_id: str, request_model: DeleteFileMetadataRequestModel, claims_and_roles: dict):
    """Delete metadata or attributes for a file (bulk operation)"""
    try:
        # No S3 validation for DELETE - allow deleting metadata even if file doesn't exist
        asset = validate_asset_exists(database_id, asset_id)
        asset.update({"object__type": "asset"})
        
        if not check_entity_authorization(asset, "DELETE", claims_and_roles):
            raise PermissionError("Not authorized to delete metadata for this file")
        
        # NEW: Schema validation for deletion
        composite_key = f"{database_id}:{asset_id}:{request_model.filePath}"
        table_name = asset_file_metadata_table_name if request_model.type == 'metadata' else file_attribute_table_name
        
        try:
            # Fetch all existing metadata
            paginator = dynamodb_client.get_paginator('query')
            page_iterator = paginator.paginate(
                TableName=table_name,
                IndexName='DatabaseIdAssetIdFilePathIndex',
                KeyConditionExpression='#pk = :pkValue',
                ExpressionAttributeNames={'#pk': 'databaseId:assetId:filePath'},
                ExpressionAttributeValues={':pkValue': {'S': composite_key}}
            ).build_full_result()
            
            existing_metadata = {}
            deserializer = TypeDeserializer()
            for item in page_iterator.get('Items', []):
                deserialized = {k: deserializer.deserialize(v) for k, v in item.items()}
                if request_model.type == 'attribute':
                    key = deserialized.get('attributeKey', deserialized.get('metadataKey'))
                    value = deserialized.get('attributeValue', deserialized.get('metadataValue'))
                    value_type = deserialized.get('attributeValueType', deserialized.get('metadataValueType'))
                else:
                    key = deserialized.get('metadataKey')
                    value = deserialized.get('metadataValue')
                    value_type = deserialized.get('metadataValueType')
                
                existing_metadata[key] = {
                    'metadataValue': value,
                    'metadataValueType': value_type
                }
            
            # Calculate remaining metadata after deletion
            remaining_metadata = {
                k: v for k, v in existing_metadata.items() 
                if k not in request_model.metadataKeys
            }
            
            # Get schemas and validate deletion
            database_ids = [database_id, 'GLOBAL']
            entity_type = 'fileMetadata' if request_model.type == 'metadata' else 'fileAttribute'
            
            aggregated_schema = get_aggregated_schemas(
                database_ids=database_ids,
                entity_type=entity_type,
                file_path=request_model.filePath,
                dynamodb_client=dynamodb_client,
                schema_table_name=metadata_schema_table_v2_name
            )
            
            # Validate deletion
            from common.metadataSchemaValidation import validate_metadata_deletion
            is_valid, validation_errors = validate_metadata_deletion(
                request_model.metadataKeys,
                remaining_metadata,
                aggregated_schema
            )
            
            if not is_valid:
                logger.warning(
                    f"Deletion validation failed: {'; '.join(validation_errors)}")
                raise VAMSGeneralErrorResponse(SCHEMA_DELETION_NOT_ALLOWED_MESSAGE)
                
        except VAMSGeneralErrorResponse:
            raise
        except Exception as e:
            # Fail closed like the write path: this block is the only guard on removing a
            # schema-required field, and swallowing the error deleted the keys unvalidated.
            logger.exception(f"Error during deletion validation: {e}")
            raise VAMSGeneralErrorResponse(SCHEMA_DELETION_VALIDATION_UNAVAILABLE_MESSAGE)
        
        successful_items = []
        failed_items = []
        items_to_delete = []
        
        for metadata_key in request_model.metadataKeys:
            try:
                # Check if item exists and prepare for delete with appropriate field names
                if request_model.type == 'metadata':
                    existing_response = asset_file_metadata_table.get_item(
                        Key={
                            'metadataKey': metadata_key,
                            'databaseId:assetId:filePath': composite_key
                        }
                    )
                    
                    if 'Item' not in existing_response:
                        failed_items.append({'key': metadata_key, 'error': 'Metadata key not found'})
                        continue
                    
                    items_to_delete.append({
                        'DeleteRequest': {
                            'Key': {
                                'metadataKey': {'S': metadata_key},
                                'databaseId:assetId:filePath': {'S': composite_key}
                            }
                        }
                    })
                else:  # attribute
                    existing_response = file_attribute_table.get_item(
                        Key={
                            'attributeKey': metadata_key,
                            'databaseId:assetId:filePath': composite_key
                        }
                    )
                    
                    if 'Item' not in existing_response:
                        failed_items.append({'key': metadata_key, 'error': 'Attribute key not found'})
                        continue
                    
                    items_to_delete.append({
                        'DeleteRequest': {
                            'Key': {
                                'attributeKey': {'S': metadata_key},
                                'databaseId:assetId:filePath': {'S': composite_key}
                            }
                        }
                    })
                
                successful_items.append(metadata_key)
            except Exception as e:
                logger.warning(f"Error preparing delete for {request_model.type} key {metadata_key}: {e}")
                failed_items.append({'key': metadata_key, 'error': str(e)})
        
        if items_to_delete:
            for i in range(0, len(items_to_delete), 25):
                batch = items_to_delete[i:i+25]
                try:
                    batch_write_with_retry(table_name, batch)
                except Exception as e:
                    logger.exception(f"Error in batch delete: {e}")
                    for item in batch:
                        key_field = 'metadataKey' if request_model.type == 'metadata' else 'attributeKey'
                        key = item['DeleteRequest']['Key'][key_field]['S']
                        if key in successful_items:
                            successful_items.remove(key)
                        failed_items.append({'key': key, 'error': 'Batch delete failed'})
        
        timestamp = datetime.utcnow().isoformat()
        return BulkOperationResponseModel(
            success=len(successful_items) > 0,
            totalItems=len(request_model.metadataKeys),
            successCount=len(successful_items),
            failureCount=len(failed_items),
            successfulItems=successful_items,
            failedItems=failed_items,
            message=f"Deleted {len(successful_items)} of {len(request_model.metadataKeys)} {request_model.type} items",
            timestamp=timestamp
        )
    except PermissionError as p:
        raise p
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error deleting file metadata: {e}")
        raise VAMSGeneralErrorResponse("Error deleting metadata")


#######################
# Request Handlers - File Metadata/Attributes
#######################

def handle_file_metadata_get(event):
    """Handle GET requests for file metadata or attributes"""
    path_parameters = event.get('pathParameters', {})
    query_parameters = event.get('queryStringParameters', {}) or {}
    
    try:
        # Parse and validate path parameters (validation in model)
        path_request_model = parse(path_parameters, model=FileMetadataPathRequestModel)
        
        # Parse query parameters - validation handled in model (adds leading slash)
        query_request_model = parse(query_parameters, model=GetFileMetadataRequestModel)
        
        # Strip assetId prefix if present (after model validation)
        file_path = query_request_model.filePath
        if file_path.startswith(f"/{path_request_model.assetId}/"):
            file_path = file_path[len(path_request_model.assetId)+1:]
            logger.info(f"Stripped assetId prefix from filePath: {query_request_model.filePath} -> {file_path}")
        
        query_params = {'maxItems': query_request_model.maxItems, 'pageSize': query_request_model.pageSize, 'startingToken': query_request_model.startingToken, 'assetVersionId': query_request_model.assetVersionId}
        response = get_file_metadata(path_request_model.databaseId, path_request_model.assetId, file_path, query_request_model.type, query_params, claims_and_roles)
        return success(body=response.dict())
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': validation_error_message(v)}, event=event)
    except PermissionError as p:
        return authorization_error(body={'message': str(p)})
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling GET request: {e}")
        return internal_error(event=event)


def handle_file_metadata_post(event):
    """Handle POST requests to create file metadata or attributes"""
    path_parameters = event.get('pathParameters', {})
    
    try:
        # Parse and validate path parameters (validation in model)
        path_request_model = parse(path_parameters, model=FileMetadataPathRequestModel)
        
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"}, event=event)
        
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"}, event=event)
        
        # Parse request model - validation handled in model (adds leading slash)
        request_model = parse(body, model=CreateFileMetadataRequestModel)
        
        # Strip assetId prefix if present (after model validation)
        if request_model.filePath.startswith(f"/{path_request_model.assetId}/"):
            request_model.filePath = request_model.filePath[len(path_request_model.assetId)+1:]
            logger.info(f"Stripped assetId prefix from filePath")
        
        response = create_file_metadata(path_request_model.databaseId, path_request_model.assetId, request_model, claims_and_roles)
        return success(body=response.dict())
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': validation_error_message(v)}, event=event)
    except PermissionError as p:
        return authorization_error(body={'message': str(p)})
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling POST request: {e}")
        return internal_error(event=event)


def handle_file_metadata_put(event):
    """Handle PUT requests to update file metadata or attributes"""
    path_parameters = event.get('pathParameters', {})
    
    try:
        # Parse and validate path parameters (validation in model)
        path_request_model = parse(path_parameters, model=FileMetadataPathRequestModel)
        
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"}, event=event)
        
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"}, event=event)
        
        # Parse request model - validation handled in model (adds leading slash)
        request_model = parse(body, model=UpdateFileMetadataRequestModel)
        
        # Strip assetId prefix if present (after model validation)
        if request_model.filePath.startswith(f"/{path_request_model.assetId}/"):
            request_model.filePath = request_model.filePath[len(path_request_model.assetId)+1:]
            logger.info(f"Stripped assetId prefix from filePath")
        
        response = update_file_metadata(path_request_model.databaseId, path_request_model.assetId, request_model, claims_and_roles)
        return success(body=response.dict())
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': validation_error_message(v)}, event=event)
    except PermissionError as p:
        return authorization_error(body={'message': str(p)})
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling PUT request: {e}")
        return internal_error(event=event)


def handle_file_metadata_delete(event):
    """Handle DELETE requests to delete file metadata or attributes"""
    path_parameters = event.get('pathParameters', {})
    
    try:
        # Parse and validate path parameters (validation in model)
        path_request_model = parse(path_parameters, model=FileMetadataPathRequestModel)
        
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"}, event=event)
        
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"}, event=event)
        
        # Parse request model - validation handled in model (adds leading slash)
        request_model = parse(body, model=DeleteFileMetadataRequestModel)
        
        # Strip assetId prefix if present (after model validation)
        if request_model.filePath.startswith(f"/{path_request_model.assetId}/"):
            request_model.filePath = request_model.filePath[len(path_request_model.assetId)+1:]
            logger.info(f"Stripped assetId prefix from filePath")
        
        response = delete_file_metadata(path_request_model.databaseId, path_request_model.assetId, request_model, claims_and_roles)
        return success(body=response.dict())
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': validation_error_message(v)}, event=event)
    except PermissionError as p:
        return authorization_error(body={'message': str(p)})
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling DELETE request: {e}")
        return internal_error(event=event)


#######################
# Database Metadata CRUD Operations
#######################

def get_database_metadata(database_id: str, query_params: dict, claims_and_roles: dict):
    """Get metadata for a database - Returns one page of records"""
    try:
        database = validate_database_exists(database_id)
        database.update({"object__type": "database"})
        
        if not check_entity_authorization(database, "GET", claims_and_roles):
            raise PermissionError("Not authorized to view metadata for this database")
        
        # Fetch every record: the page is sliced after schema enrichment and ordering
        paginator = dynamodb_client.get_paginator('query')
        page_iterator = paginator.paginate(
            TableName=database_metadata_table_name,
            IndexName='DatabaseIdIndex',
            KeyConditionExpression='databaseId = :dbId',
            ExpressionAttributeValues={':dbId': {'S': database_id}},
            ScanIndexForward=False
        ).build_full_result()
        
        # Process ALL items
        metadata_list = []
        deserializer = TypeDeserializer()
        for item in page_iterator.get('Items', []):
            deserialized_item = {k: deserializer.deserialize(v) for k, v in item.items()}
            metadata_list.append(deserialized_item)
        
        # Fetch database config and schema enrichment
        restrict_metadata_outside_schemas = False
        try:
            database_ids = [database_id, 'GLOBAL']
            
            aggregated_schema = get_aggregated_schemas(
                database_ids=database_ids,
                entity_type='databaseMetadata',
                file_path=None,
                dynamodb_client=dynamodb_client,
                schema_table_name=metadata_schema_table_v2_name
            )
            
            # Calculate restrictMetadataOutsideSchemas
            schemas_exist = len(aggregated_schema) > 0
            if schemas_exist:
                try:
                    db_config = get_database_config(database_id)
                    db_restricts = db_config.get('restrictMetadataOutsideSchemas', False) == True
                    restrict_metadata_outside_schemas = db_restricts
                except Exception as e:
                    logger.warning(f"Error fetching database config for restriction check: {e}")
                    restrict_metadata_outside_schemas = False
            
            # Enrich metadata with schema information
            enriched_metadata = enrich_metadata_with_schema(metadata_list, aggregated_schema)

            # Convert to response models
            metadata_list = metadata_response_models(
                DatabaseMetadataResponseModel, enriched_metadata,
                databaseId=database_id)
        except Exception as e:
            logger.warning(f"Error enriching metadata with schema: {e}")
            # If schema enrichment fails, return metadata without enrichment
            metadata_list = metadata_response_models(
                DatabaseMetadataResponseModel, metadata_list,
                databaseId=database_id)
            restrict_metadata_outside_schemas = False

        # Offset-paginate the enriched, ordered list to bound the response payload.
        page, next_token = paginate_metadata_records(metadata_list, query_params)

        # Build response
        result = GetDatabaseMetadataResponseModel(
            metadata=page,
            restrictMetadataOutsideSchemas=restrict_metadata_outside_schemas,
            NextToken=next_token
        )

        return result
    except PermissionError as p:
        raise p
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error getting database metadata: {e}")
        raise VAMSGeneralErrorResponse("Error retrieving metadata")


def create_database_metadata(database_id: str, request_model: CreateDatabaseMetadataRequestModel, claims_and_roles: dict):
    """Create metadata for a database (bulk operation)"""
    try:
        database = validate_database_exists(database_id)
        database.update({"object__type": "database"})
        
        if not check_entity_authorization(database, "POST", claims_and_roles):
            raise PermissionError("Not authorized to create metadata for this database")
        
        # Validate 500 record limit: Fetch existing + count with new
        try:
            paginator = dynamodb_client.get_paginator('query')
            page_iterator = paginator.paginate(
                TableName=database_metadata_table_name,
                IndexName='DatabaseIdIndex',
                KeyConditionExpression='databaseId = :dbId',
                ExpressionAttributeValues={':dbId': {'S': database_id}}
            ).build_full_result()
            
            existing_count = len(page_iterator.get('Items', []))
            new_unique_keys = {item.metadataKey for item in request_model.metadata}
            
            # Get existing keys to determine how many are truly new
            existing_keys = set()
            deserializer = TypeDeserializer()
            for item in page_iterator.get('Items', []):
                deserialized = {k: deserializer.deserialize(v) for k, v in item.items()}
                existing_keys.add(deserialized['metadataKey'])
            
            # Calculate final count after upsert
            final_count = len(existing_keys.union(new_unique_keys))
            
            if final_count > MAX_METADATA_RECORDS_PER_ENTITY:
                raise VAMSGeneralErrorResponse(
                    f"Maximum {MAX_METADATA_RECORDS_PER_ENTITY} metadata records allowed per entity "
                    f"(current: {existing_count}, attempting to add: {len(new_unique_keys)}, final would be: {final_count})"
                )
        except VAMSGeneralErrorResponse:
            raise
        except Exception as e:
            logger.warning(f"Error checking record limit: {e}")
            # Continue without limit check if it fails
        
        # Check if user is SYSTEM_USER - bypass schema validation
        username = claims_and_roles.get("tokens", ["SYSTEM_USER"])[0]
        skip_schema_validation = (username == "SYSTEM_USER")
        
        # Schema validation for non-SYSTEM_USER users
        if not skip_schema_validation:
            try:
                database_ids = [database_id, 'GLOBAL']
                
                aggregated_schema = get_aggregated_schemas(
                    database_ids=database_ids,
                    entity_type='databaseMetadata',
                    file_path=None,
                    dynamodb_client=dynamodb_client,
                    schema_table_name=metadata_schema_table_v2_name
                )
                
                # COMPREHENSIVE VALIDATION: Fetch existing metadata and merge with incoming
                paginator = dynamodb_client.get_paginator('query')
                page_iterator = paginator.paginate(
                    TableName=database_metadata_table_name,
                    IndexName='DatabaseIdIndex',
                    KeyConditionExpression='databaseId = :dbId',
                    ExpressionAttributeValues={':dbId': {'S': database_id}}
                ).build_full_result()
                
                # Build existing metadata dict
                existing_metadata = stored_metadata_entries(page_iterator.get('Items', []))
                
                # Merge incoming metadata with existing (simulating upsert)
                merged_metadata = existing_metadata.copy()
                for item in request_model.metadata:
                    merged_metadata[item.metadataKey] = {
                        'metadataValue': item.metadataValue,
                        'metadataValueType': item.metadataValueType.value
                    }
                
                # Validate the complete merged state
                is_valid, errors, metadata_with_defaults = validate_metadata_against_schema(
                    merged_metadata, aggregated_schema, "POST", existing_metadata
                )
                
                if not is_valid:
                    error_message = "Schema validation failed: " + "; ".join(errors)
                    raise VAMSGeneralErrorResponse(error_message)
                
                # Check restrictMetadataOutsideSchemas setting (only if schemas exist)
                if aggregated_schema:
                    db_config = get_database_config(database_id)
                    restrict = db_config.get('restrictMetadataOutsideSchemas', False)
                    
                    if restrict:
                        keys_valid, key_errors = validate_metadata_keys_against_schema(
                            merged_metadata, aggregated_schema, True
                        )
                        if not keys_valid:
                            error_message = "Metadata key validation failed: " + "; ".join(key_errors)
                            raise VAMSGeneralErrorResponse(error_message)
                
                # Update request model with defaults applied (only for new fields).
                # This step is purely ADDITIVE and runs after every check above has passed,
                # so it is deliberately fail-open: a failure here loses schema-supplied
                # defaults and cannot admit anything validation refused. Guarded on its own so
                # the surrounding fail-closed arm does not turn it into a denied write.
                try:
                    updated_metadata = []
                    for item in request_model.metadata:
                        updated_metadata.append(item)

                    # Add any new fields with defaults that weren't in the request
                    for key, value_dict in metadata_with_defaults.items():
                        if key not in existing_metadata and not any(item.metadataKey == key for item in request_model.metadata):
                            from models.metadata import MetadataItemModel
                            updated_metadata.append(MetadataItemModel(
                                metadataKey=key,
                                metadataValue=value_dict['metadataValue'],
                                metadataValueType=value_dict['metadataValueType']
                            ))
                    request_model.metadata = updated_metadata
                except Exception as default_error:
                    logger.warning(
                        f"{SCHEMA_DEFAULT_INJECTION_FAILED_LOG}: {default_error}"
                    )
                
            except VAMSGeneralErrorResponse:
                raise
            except Exception as e:
                logger.exception(f"Error during schema validation: {e}")
                raise VAMSGeneralErrorResponse(SCHEMA_VALIDATION_UNAVAILABLE_MESSAGE)
        
        successful_items = []
        failed_items = []
        items_to_write = []
        
        for metadata_item in request_model.metadata:
            try:
                # Prepare item for upsert (will create or update)
                item = {
                    'metadataKey': {'S': metadata_item.metadataKey},
                    'databaseId': {'S': database_id},
                    'metadataValue': {'S': metadata_item.metadataValue},
                    'metadataValueType': {'S': metadata_item.metadataValueType.value}
                }
                items_to_write.append({'PutRequest': {'Item': item}})
                successful_items.append(metadata_item.metadataKey)
            except Exception as e:
                failed_items.append({'key': metadata_item.metadataKey, 'error': str(e)})
        
        if items_to_write:
            for i in range(0, len(items_to_write), 25):
                batch = items_to_write[i:i+25]
                try:
                    batch_write_with_retry(database_metadata_table_name, batch)
                except Exception as e:
                    logger.exception(f"Error in batch write: {e}")
                    for item in batch:
                        key = item['PutRequest']['Item']['metadataKey']['S']
                        if key in successful_items:
                            successful_items.remove(key)
                        failed_items.append({'key': key, 'error': 'Batch write failed'})
        
        timestamp = datetime.utcnow().isoformat()
        return BulkOperationResponseModel(
            success=len(successful_items) > 0,
            totalItems=len(request_model.metadata),
            successCount=len(successful_items),
            failureCount=len(failed_items),
            successfulItems=successful_items,
            failedItems=failed_items,
            message=f"Created {len(successful_items)} of {len(request_model.metadata)} metadata items",
            timestamp=timestamp
        )
    except PermissionError as p:
        raise p
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error creating database metadata: {e}")
        raise VAMSGeneralErrorResponse("Error creating metadata")


def update_database_metadata(database_id: str, request_model: UpdateDatabaseMetadataRequestModel, claims_and_roles: dict):
    """Update metadata for a database (bulk operation) - Supports UPDATE and REPLACE_ALL modes"""
    try:
        database = validate_database_exists(database_id)
        database.update({"object__type": "database"})
        
        # Check authorization based on updateType
        if request_model.updateType == UpdateType.REPLACE_ALL:
            # REPLACE_ALL requires PUT, POST, and DELETE permissions
            if not check_multi_action_authorization(database, ["PUT", "POST", "DELETE"], claims_and_roles):
                raise PermissionError("REPLACE_ALL requires PUT, POST, and DELETE permissions")
        else:
            # UPDATE mode requires only PUT permission
            if not check_entity_authorization(database, "PUT", claims_and_roles):
                raise PermissionError("Not authorized to update metadata for this database")
        
        # Check if user is SYSTEM_USER - bypass schema validation
        username = claims_and_roles.get("tokens", ["SYSTEM_USER"])[0]
        skip_schema_validation = (username == "SYSTEM_USER")
        
        # Schema validation for non-SYSTEM_USER users
        if not skip_schema_validation:
            try:
                # Fetch ALL existing metadata for this database
                paginator = dynamodb_client.get_paginator('query')
                page_iterator = paginator.paginate(
                    TableName=database_metadata_table_name,
                    IndexName='DatabaseIdIndex',
                    KeyConditionExpression='databaseId = :dbId',
                    ExpressionAttributeValues={':dbId': {'S': database_id}}
                ).build_full_result()
                
                # Build existing metadata dict
                existing_metadata = stored_metadata_entries(page_iterator.get('Items', []))
                
                # Validate 500 record limit based on updateType
                if request_model.updateType == UpdateType.UPDATE:
                    # For UPDATE: Check final count after merge
                    new_unique_keys = {item.metadataKey for item in request_model.metadata}
                    existing_keys = set(existing_metadata.keys())
                    final_count = len(existing_keys.union(new_unique_keys))
                    
                    if final_count > MAX_METADATA_RECORDS_PER_ENTITY:
                        raise VAMSGeneralErrorResponse(
                            f"Maximum {MAX_METADATA_RECORDS_PER_ENTITY} metadata records allowed per entity "
                            f"(current: {len(existing_keys)}, attempting to add: {len(new_unique_keys)}, final would be: {final_count})"
                        )
                    
                    # Merge with updates
                    for item in request_model.metadata:
                        existing_metadata[item.metadataKey] = {
                            'metadataValue': item.metadataValue,
                            'metadataValueType': item.metadataValueType.value
                        }
                    metadata_to_validate = existing_metadata
                else:  # REPLACE_ALL
                    # For REPLACE_ALL: Just check incoming count
                    if len(request_model.metadata) > MAX_METADATA_RECORDS_PER_ENTITY:
                        raise VAMSGeneralErrorResponse(
                            f"Maximum {MAX_METADATA_RECORDS_PER_ENTITY} metadata records allowed per entity "
                            f"(attempting to set: {len(request_model.metadata)})"
                        )
                    
                    # Validate only provided metadata (all-or-nothing)
                    metadata_to_validate = {
                        item.metadataKey: {
                            'metadataValue': item.metadataValue,
                            'metadataValueType': item.metadataValueType.value
                        }
                        for item in request_model.metadata
                    }
                
                # Get schemas and validate
                database_ids = [database_id, 'GLOBAL']
                
                aggregated_schema = get_aggregated_schemas(
                    database_ids=database_ids,
                    entity_type='databaseMetadata',
                    file_path=None,
                    dynamodb_client=dynamodb_client,
                    schema_table_name=metadata_schema_table_v2_name
                )
                
                is_valid, errors, metadata_with_defaults = validate_metadata_against_schema(
                    metadata_to_validate, aggregated_schema, "PUT", existing_metadata
                )
                
                if not is_valid:
                    error_message = "Schema validation failed: " + "; ".join(errors)
                    raise VAMSGeneralErrorResponse(error_message)
                
                # Check restrictMetadataOutsideSchemas setting (only if schemas exist)
                if aggregated_schema:
                    db_config = get_database_config(database_id)
                    restrict = db_config.get('restrictMetadataOutsideSchemas', False)
                    
                    if restrict:
                        keys_valid, key_errors = validate_metadata_keys_against_schema(
                            metadata_to_validate, aggregated_schema, True
                        )
                        if not keys_valid:
                            error_message = "Metadata key validation failed: " + "; ".join(key_errors)
                            raise VAMSGeneralErrorResponse(error_message)
                
            except VAMSGeneralErrorResponse:
                raise
            except Exception as e:
                logger.exception(f"Error during schema validation: {e}")
                raise VAMSGeneralErrorResponse(SCHEMA_VALIDATION_UNAVAILABLE_MESSAGE)
        
        # Route to appropriate operation based on updateType
        if request_model.updateType == UpdateType.REPLACE_ALL:
            # REPLACE_ALL: Delete unlisted keys, then upsert all provided
            return _replace_all_database_metadata(database_id, request_model.metadata, claims_and_roles)
        else:
            # UPDATE: Upsert provided metadata (create or update)
            return _upsert_database_metadata(database_id, request_model.metadata, claims_and_roles)
        
    except PermissionError as p:
        raise p
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error updating database metadata: {e}")
        raise VAMSGeneralErrorResponse("Error updating metadata")


def _upsert_database_metadata(database_id: str, metadata_items: list, claims_and_roles: dict) -> BulkOperationResponseModel:
    """Internal helper: Upsert database metadata (create or update)"""
    try:
        successful_items = []
        failed_items = []
        items_to_write = []
        
        for metadata_item in metadata_items:
            try:
                # Prepare item for upsert (will create or update)
                item = {
                    'metadataKey': {'S': metadata_item.metadataKey},
                    'databaseId': {'S': database_id},
                    'metadataValue': {'S': metadata_item.metadataValue},
                    'metadataValueType': {'S': metadata_item.metadataValueType.value}
                }
                
                items_to_write.append({'PutRequest': {'Item': item}})
                successful_items.append(metadata_item.metadataKey)
                
            except Exception as e:
                logger.warning(f"Error preparing metadata item {metadata_item.metadataKey}: {e}")
                failed_items.append({
                    'key': metadata_item.metadataKey,
                    'error': str(e)
                })
        
        # Write items in batches of 25
        if items_to_write:
            for i in range(0, len(items_to_write), 25):
                batch = items_to_write[i:i+25]
                try:
                    batch_write_with_retry(database_metadata_table_name, batch)
                except Exception as e:
                    logger.exception(f"Error in batch write: {e}")
                    for item in batch:
                        key = item['PutRequest']['Item']['metadataKey']['S']
                        if key in successful_items:
                            successful_items.remove(key)
                        failed_items.append({
                            'key': key,
                            'error': 'Batch write failed'
                        })
        
        # Build response
        timestamp = datetime.utcnow().isoformat()
        total_items = len(metadata_items)
        success_count = len(successful_items)
        failure_count = len(failed_items)
        
        return BulkOperationResponseModel(
            success=success_count > 0,
            totalItems=total_items,
            successCount=success_count,
            failureCount=failure_count,
            successfulItems=successful_items,
            failedItems=failed_items,
            message=f"Upserted {success_count} of {total_items} metadata items",
            timestamp=timestamp
        )
        
    except Exception as e:
        logger.exception(f"Error in upsert operation: {e}")
        raise VAMSGeneralErrorResponse("Error upserting metadata")


def _replace_all_database_metadata(database_id: str, metadata_items: list, claims_and_roles: dict) -> BulkOperationResponseModel:
    """Internal helper: Replace all database metadata with rollback on failure"""
    try:
        # Step 1: Fetch all existing metadata
        paginator = dynamodb_client.get_paginator('query')
        page_iterator = paginator.paginate(
            TableName=database_metadata_table_name,
            IndexName='DatabaseIdIndex',
            KeyConditionExpression='databaseId = :dbId',
            ExpressionAttributeValues={':dbId': {'S': database_id}}
        ).build_full_result()
        
        existing_metadata = []
        deserializer = TypeDeserializer()
        for item in page_iterator.get('Items', []):
            deserialized = {k: deserializer.deserialize(v) for k, v in item.items()}
            existing_metadata.append(deserialized)
        
        # Step 2: Determine which keys to delete
        provided_keys = {item.metadataKey for item in metadata_items}
        existing_keys = {item['metadataKey'] for item in existing_metadata}
        keys_to_delete = existing_keys - provided_keys
        
        # Store items to delete for potential rollback
        deleted_items_backup = [
            item for item in existing_metadata 
            if item['metadataKey'] in keys_to_delete
        ]
        
        logger.info(f"REPLACE_ALL: Deleting {len(keys_to_delete)} keys, upserting {len(provided_keys)} keys")
        
        # Step 3: Delete keys not in provided list
        if keys_to_delete:
            items_to_delete = []
            for key in keys_to_delete:
                items_to_delete.append({
                    'DeleteRequest': {
                        'Key': {
                            'metadataKey': {'S': key},
                            'databaseId': {'S': database_id}
                        }
                    }
                })
            
            # Delete in batches of 25
            for i in range(0, len(items_to_delete), 25):
                batch = items_to_delete[i:i+25]
                try:
                    batch_write_with_retry(database_metadata_table_name, batch)
                except Exception as e:
                    logger.exception(f"Error deleting metadata in REPLACE_ALL: {e}")
                    raise VAMSGeneralErrorResponse("Failed to delete existing metadata")
        
        # Step 4: Upsert all provided metadata
        try:
            items_to_write = []
            for metadata_item in metadata_items:
                item = {
                    'metadataKey': {'S': metadata_item.metadataKey},
                    'databaseId': {'S': database_id},
                    'metadataValue': {'S': metadata_item.metadataValue},
                    'metadataValueType': {'S': metadata_item.metadataValueType.value}
                }
                items_to_write.append({'PutRequest': {'Item': item}})
            
            # Write in batches of 25
            for i in range(0, len(items_to_write), 25):
                batch = items_to_write[i:i+25]
                batch_write_with_retry(database_metadata_table_name, batch)
            
            # Success - build response
            timestamp = datetime.utcnow().isoformat()
            return BulkOperationResponseModel(
                success=True,
                totalItems=len(metadata_items),
                successCount=len(metadata_items),
                failureCount=0,
                successfulItems=[item.metadataKey for item in metadata_items],
                failedItems=[],
                message=f"Replaced all metadata: deleted {len(keys_to_delete)} keys, upserted {len(metadata_items)} keys",
                timestamp=timestamp
            )
            
        except Exception as upsert_error:
            # Step 5: Rollback - attempt to restore deleted items
            logger.error(f"Upsert failed in REPLACE_ALL, attempting rollback: {upsert_error}")
            
            if deleted_items_backup:
                try:
                    # Restore deleted items
                    items_to_restore = []
                    for item in deleted_items_backup:
                        restore_item = {
                            'metadataKey': {'S': item['metadataKey']},
                            'databaseId': {'S': database_id},
                            # Carried only when the backup holds them: a row written by an
                            # earlier release can lack either attribute, and subscripting it
                            # here raised before any write was issued - so a single legacy row
                            # in the deleted set meant NO row was restored and the metadata was
                            # permanently lost. A rollback reinstates what was there, so an
                            # absent attribute stays absent rather than gaining a default.
                            **({'metadataValue': {'S': item['metadataValue']}}
                               if item.get('metadataValue') is not None else {}),
                            **({'metadataValueType': {'S': item['metadataValueType']}}
                               if item.get('metadataValueType') is not None else {}),
                        }
                        items_to_restore.append({'PutRequest': {'Item': restore_item}})
                    
                    # Restore in batches of 25
                    for i in range(0, len(items_to_restore), 25):
                        batch = items_to_restore[i:i+25]
                        batch_write_with_retry(database_metadata_table_name, batch)
                    
                    logger.info(f"Rollback successful: restored {len(deleted_items_backup)} deleted items")
                    rollback_succeeded = True

                except Exception as rollback_error:
                    logger.error(f"Rollback failed: {rollback_error}")
                    rollback_succeeded = False

                # Reported outside the rollback try so the except arm above cannot catch
                # this signal and describe a completed rollback as an inconsistent one.
                if rollback_succeeded:
                    raise VAMSGeneralErrorResponse(
                        "REPLACE_ALL operation failed, all changes rolled back successfully"
                    )
                raise VAMSGeneralErrorResponse(
                    "REPLACE_ALL operation failed and rollback unsuccessful - data may be inconsistent. "
                    "Please contact administrator."
                )
            else:
                # No items were deleted, so just report the upsert failure
                raise VAMSGeneralErrorResponse(f"REPLACE_ALL operation failed during upsert: {str(upsert_error)}")
        
    except PermissionError as p:
        raise p
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error in REPLACE_ALL operation: {e}")
        raise VAMSGeneralErrorResponse("Error in REPLACE_ALL operation")


def delete_database_metadata(database_id: str, request_model: DeleteDatabaseMetadataRequestModel, claims_and_roles: dict):
    """Delete metadata for a database (bulk operation)"""
    try:
        database = validate_database_exists(database_id)
        database.update({"object__type": "database"})
        
        if not check_entity_authorization(database, "DELETE", claims_and_roles):
            raise PermissionError("Not authorized to delete metadata for this database")
        
        # NEW: Schema validation for deletion
        try:
            # Fetch all existing metadata
            paginator = dynamodb_client.get_paginator('query')
            page_iterator = paginator.paginate(
                TableName=database_metadata_table_name,
                IndexName='DatabaseIdIndex',
                KeyConditionExpression='databaseId = :dbId',
                ExpressionAttributeValues={':dbId': {'S': database_id}}
            ).build_full_result()
            
            existing_metadata = {}
            deserializer = TypeDeserializer()
            for item in page_iterator.get('Items', []):
                deserialized = {k: deserializer.deserialize(v) for k, v in item.items()}
                # .get: only the remaining KEYS matter here, and a row written by an earlier
                # version may carry neither attribute. The block below denies on error.
                existing_metadata[deserialized.get('metadataKey')] = {
                    'metadataValue': deserialized.get('metadataValue'),
                    'metadataValueType': deserialized.get('metadataValueType')
                }
            
            # Calculate remaining metadata after deletion
            remaining_metadata = {
                k: v for k, v in existing_metadata.items() 
                if k not in request_model.metadataKeys
            }
            
            # Get schemas and validate deletion
            database_ids = [database_id, 'GLOBAL']
            
            aggregated_schema = get_aggregated_schemas(
                database_ids=database_ids,
                entity_type='databaseMetadata',
                file_path=None,
                dynamodb_client=dynamodb_client,
                schema_table_name=metadata_schema_table_v2_name
            )
            
            # Validate deletion
            from common.metadataSchemaValidation import validate_metadata_deletion
            is_valid, validation_errors = validate_metadata_deletion(
                request_model.metadataKeys,
                remaining_metadata,
                aggregated_schema
            )
            
            if not is_valid:
                logger.warning(
                    f"Deletion validation failed: {'; '.join(validation_errors)}")
                raise VAMSGeneralErrorResponse(SCHEMA_DELETION_NOT_ALLOWED_MESSAGE)
                
        except VAMSGeneralErrorResponse:
            raise
        except Exception as e:
            # Fail closed like the write path: this block is the only guard on removing a
            # schema-required field, and swallowing the error deleted the keys unvalidated.
            logger.exception(f"Error during deletion validation: {e}")
            raise VAMSGeneralErrorResponse(SCHEMA_DELETION_VALIDATION_UNAVAILABLE_MESSAGE)
        
        successful_items = []
        failed_items = []
        items_to_delete = []
        
        for metadata_key in request_model.metadataKeys:
            try:
                # Check if metadata exists
                existing_response = database_metadata_table.get_item(
                    Key={
                        'metadataKey': metadata_key,
                        'databaseId': database_id
                    }
                )
                
                if 'Item' not in existing_response:
                    failed_items.append({'key': metadata_key, 'error': 'Metadata key not found'})
                    continue
                
                # Prepare item for batch delete
                items_to_delete.append({
                    'DeleteRequest': {
                        'Key': {
                            'metadataKey': {'S': metadata_key},
                            'databaseId': {'S': database_id}
                        }
                    }
                })
                successful_items.append(metadata_key)
            except Exception as e:
                failed_items.append({'key': metadata_key, 'error': str(e)})
        
        if items_to_delete:
            for i in range(0, len(items_to_delete), 25):
                batch = items_to_delete[i:i+25]
                try:
                    batch_write_with_retry(database_metadata_table_name, batch)
                except Exception as e:
                    logger.exception(f"Error in batch delete: {e}")
                    for item in batch:
                        key = item['DeleteRequest']['Key']['metadataKey']['S']
                        if key in successful_items:
                            successful_items.remove(key)
                        failed_items.append({'key': key, 'error': 'Batch delete failed'})
        
        timestamp = datetime.utcnow().isoformat()
        return BulkOperationResponseModel(
            success=len(successful_items) > 0,
            totalItems=len(request_model.metadataKeys),
            successCount=len(successful_items),
            failureCount=len(failed_items),
            successfulItems=successful_items,
            failedItems=failed_items,
            message=f"Deleted {len(successful_items)} of {len(request_model.metadataKeys)} metadata items",
            timestamp=timestamp
        )
    except PermissionError as p:
        raise p
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error deleting database metadata: {e}")
        raise VAMSGeneralErrorResponse("Error deleting metadata")


#######################
# Request Handlers - Database Metadata
#######################

def handle_database_metadata_get(event):
    """Handle GET requests for database metadata"""
    path_parameters = event.get('pathParameters', {})
    query_parameters = event.get('queryStringParameters', {}) or {}
    
    try:
        # Parse and validate path parameters (validation in model)
        path_request_model = parse(path_parameters, model=DatabaseMetadataPathRequestModel)
        
        query_request_model = parse(query_parameters, model=GetDatabaseMetadataRequestModel)
        query_params = {'maxItems': query_request_model.maxItems, 'pageSize': query_request_model.pageSize, 'startingToken': query_request_model.startingToken}

        response = get_database_metadata(path_request_model.databaseId, query_params, claims_and_roles)
        return success(body=response.dict())
    except PermissionError as p:
        return authorization_error(body={'message': str(p)})
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling GET request: {e}")
        return internal_error(event=event)


def handle_database_metadata_post(event):
    """Handle POST requests to create database metadata"""
    path_parameters = event.get('pathParameters', {})
    
    try:
        # Parse and validate path parameters (validation in model)
        path_request_model = parse(path_parameters, model=DatabaseMetadataPathRequestModel)
        
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"}, event=event)
        
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"}, event=event)
        
        request_model = parse(body, model=CreateDatabaseMetadataRequestModel)
        response = create_database_metadata(path_request_model.databaseId, request_model, claims_and_roles)
        return success(body=response.dict())
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': validation_error_message(v)}, event=event)
    except PermissionError as p:
        return authorization_error(body={'message': str(p)})
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling POST request: {e}")
        return internal_error(event=event)


def handle_database_metadata_put(event):
    """Handle PUT requests to update database metadata"""
    path_parameters = event.get('pathParameters', {})
    
    try:
        # Parse and validate path parameters (validation in model)
        path_request_model = parse(path_parameters, model=DatabaseMetadataPathRequestModel)
        
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"}, event=event)
        
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"}, event=event)
        
        request_model = parse(body, model=UpdateDatabaseMetadataRequestModel)
        response = update_database_metadata(path_request_model.databaseId, request_model, claims_and_roles)
        return success(body=response.dict())
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': validation_error_message(v)}, event=event)
    except PermissionError as p:
        return authorization_error(body={'message': str(p)})
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling PUT request: {e}")
        return internal_error(event=event)


def handle_database_metadata_delete(event):
    """Handle DELETE requests to delete database metadata"""
    path_parameters = event.get('pathParameters', {})
    
    try:
        # Parse and validate path parameters (validation in model)
        path_request_model = parse(path_parameters, model=DatabaseMetadataPathRequestModel)
        
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"}, event=event)
        
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"}, event=event)
        
        request_model = parse(body, model=DeleteDatabaseMetadataRequestModel)
        response = delete_database_metadata(path_request_model.databaseId, request_model, claims_and_roles)
        return success(body=response.dict())
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': validation_error_message(v)}, event=event)
    except PermissionError as p:
        return authorization_error(body={'message': str(p)})
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling DELETE request: {e}")
        return internal_error(event=event)


#######################
# Lambda Handler
#######################

def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for centralized metadata service"""
    global claims_and_roles
    claims_and_roles = request_to_claims(event)
    
    try:
        # Parse request
        path = event['requestContext']['http']['path']
        method = event['requestContext']['http']['method']
        
        # Check API authorization
        method_allowed_on_api = False
        if len(claims_and_roles.get("tokens", [])) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True
        
        if not method_allowed_on_api:
            return authorization_error()
        
        # Route to appropriate handler based on the master API route definitions
        # Asset Link Metadata Routes
        if API_ASSET_LINK_METADATA.matches(path):
            if method == 'GET':
                return handle_asset_link_metadata_get(event)
            elif method == 'POST':
                return handle_asset_link_metadata_post(event)
            elif method == 'PUT':
                return handle_asset_link_metadata_put(event)
            elif method == 'DELETE':
                return handle_asset_link_metadata_delete(event)

        # File Metadata/Attribute Routes
        elif API_FILE_METADATA.matches(path):
            if method == 'GET':
                return handle_file_metadata_get(event)
            elif method == 'POST':
                return handle_file_metadata_post(event)
            elif method == 'PUT':
                return handle_file_metadata_put(event)
            elif method == 'DELETE':
                return handle_file_metadata_delete(event)

        # Asset Metadata Routes (not file metadata)
        elif API_ASSET_METADATA.matches(path):
            if method == 'GET':
                return handle_asset_metadata_get(event)
            elif method == 'POST':
                return handle_asset_metadata_post(event)
            elif method == 'PUT':
                return handle_asset_metadata_put(event)
            elif method == 'DELETE':
                return handle_asset_metadata_delete(event)

        # Database Metadata Routes
        elif API_DATABASE_METADATA.matches(path):
            if method == 'GET':
                return handle_database_metadata_get(event)
            elif method == 'POST':
                return handle_database_metadata_post(event)
            elif method == 'PUT':
                return handle_database_metadata_put(event)
            elif method == 'DELETE':
                return handle_database_metadata_delete(event)
        
        # If no route matched
        return validation_error(body={'message': "Route not found"}, event=event)
        
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': validation_error_message(v)}, event=event)
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error(event=event)