# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
import uuid
from datetime import datetime
from botocore.config import Config
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
from common.assetHistory import (
    CHANGE_SOURCE_CREATE,
    CHANGE_SOURCE_CREATE_DIRECT,
    build_asset_snapshot,
    write_asset_history_record,
)
from handlers.assets.assetCount import update_asset_count
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from common.tagScope import GLOBAL_SCOPE
from models.common import APIGatewayProxyResponseV2, internal_error, success, validation_error, general_error, authorization_error, VAMSGeneralErrorResponse, validation_error_message
from models.assetsV3 import CreateAssetRequestModel, CreateAssetResponseModel

# Configure AWS clients
retry_config = Config(
    retries={
        'max_attempts': 5,
        'mode': 'adaptive'
    }
)

region = os.environ['AWS_REGION']
dynamodb = boto3.resource('dynamodb', config=retry_config)
sns_client = boto3.client('sns', config=retry_config)
s3_client = boto3.client('s3', config=retry_config)
logger = safeLogger(service_name="CreateAsset")

# Load environment variables
try:
    from common.resourceNames import ResourceKeys, get_table_name
    s3_asset_buckets_table = get_table_name(ResourceKeys.S3_ASSET_BUCKETS_STORAGE_TABLE)
    asset_storage_table_name = get_table_name(ResourceKeys.ASSET_STORAGE_TABLE)
    db_database = get_table_name(ResourceKeys.DATABASE_STORAGE_TABLE)
    tag_type_table_name = get_table_name(ResourceKeys.TAG_TYPE_STORAGE_TABLE)
    tag_table_name = get_table_name(ResourceKeys.TAG_STORAGE_TABLE)
    asset_versions_table_name = get_table_name(ResourceKeys.ASSET_VERSIONS_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed resolving resource names")
    raise e

# Initialize DynamoDB tables
asset_table = dynamodb.Table(asset_storage_table_name)
database_table = dynamodb.Table(db_database)
buckets_table = dynamodb.Table(s3_asset_buckets_table)
tag_table = dynamodb.Table(tag_table_name)
tag_type_table = dynamodb.Table(tag_type_table_name)

# Archiving rewrites an asset record under a "{databaseId}#deleted" partition key.
ARCHIVED_DATABASE_SUFFIX = '#deleted'

#######################
# Utility Functions
#######################

def normalize_s3_path(asset_base_key, file_path):
    """
    Intelligently resolve the full S3 key, avoiding duplication if file_path already contains the asset base key.
    
    Args:
        asset_base_key: The base key from assetLocation (e.g., "assetId/" or "custom/path/")
        file_path: The file path from the request (may or may not include the base key)
        
    Returns:
        The properly resolved S3 key without duplication
    """
    # Normalize the asset base key to ensure it ends with '/'
    if asset_base_key and not asset_base_key.endswith('/'):
        asset_base_key = asset_base_key + '/'
    
    # Remove leading slash from file path if present
    if file_path.startswith('/'):
        file_path = file_path[1:]
    
    # Check if file_path already starts with the asset_base_key
    if file_path.startswith(asset_base_key):
        # File path already contains the base key, use as-is
        logger.info(f"File path '{file_path}' already contains base key '{asset_base_key}', using as-is")
        return file_path
    else:
        # File path doesn't contain base key, combine them
        resolved_path = asset_base_key + file_path
        logger.info(f"Combined base key '{asset_base_key}' with file path '{file_path}' to get '{resolved_path}'")
        return resolved_path

def normalize_base_assets_prefix(base_assets_prefix):
    """Normalize a bucket record's baseAssetsPrefix to its canonical S3 form.

    Bucket records can spell the same physical prefix root several ways
    ("assets", "assets/", "/assets/"), so every comparison of one bucket record
    against another goes through this single normalization: trailing slash
    present, no leading slash.
    """
    if not base_assets_prefix:
        return ''
    if not base_assets_prefix.endswith('/'):
        base_assets_prefix += '/'
    if base_assets_prefix.startswith('/'):
        base_assets_prefix = base_assets_prefix[1:]
    return base_assets_prefix


def get_default_bucket_details(databaseId):
    """Get default S3 bucket details from database default bucket DynamoDB"""
    try:
        db_response = database_table.get_item(
            Key={
                'databaseId': databaseId
            }
        )
        database = db_response.get("Item", {})

        bucket_response = buckets_table.query(
            KeyConditionExpression=Key('bucketId').eq(database.get('defaultBucketId')),
            Limit=1
        )
        # Use the first item from the query results
        bucket = bucket_response.get("Items", [{}])[0] if bucket_response.get("Items") else {}
        bucket_id = bucket.get('bucketId')
        bucket_name = bucket.get('bucketName')
        base_assets_prefix = bucket.get('baseAssetsPrefix')

        #Check to make sure we have what we need
        if not bucket_name or not base_assets_prefix:
            raise VAMSGeneralErrorResponse(f"Error getting database default bucket details.")

        base_assets_prefix = normalize_base_assets_prefix(base_assets_prefix)

        return {
            'bucketId': bucket_id,
            'bucketName': bucket_name,
            'baseAssetsPrefix': base_assets_prefix
        }
    except Exception as e:
        logger.exception(f"Error getting database default bucket details: {e}")
        raise VAMSGeneralErrorResponse(f"Error getting database default bucket details.")
    

def save_asset_details(asset_data):
    """Save a NEW asset record to DynamoDB.

    Conditional on the (databaseId, assetId) not already existing so a concurrent
    or duplicate create (e.g. a redelivered bucket-sync event) cannot silently
    overwrite an existing asset. Callers treat the conditional failure as
    "asset already exists".
    """
    try:
        asset_table.put_item(
            Item=asset_data,
            ConditionExpression='attribute_not_exists(databaseId) AND attribute_not_exists(assetId)'
        )
    except ClientError as e:
        if e.response.get('Error', {}).get('Code') == 'ConditionalCheckFailedException':
            logger.info(f"Asset already exists on conditional create: {asset_data.get('assetId')}")
            raise VAMSGeneralErrorResponse("Asset with specified ID already exists")
        logger.exception(f"Error saving asset details: {e}")
        raise VAMSGeneralErrorResponse("Error saving asset.")
    except Exception as e:
        logger.exception(f"Error saving asset details: {e}")
        raise VAMSGeneralErrorResponse("Error saving asset.")

def create_sns_topic_for_asset(database_id, asset_id):
    """Create an SNS topic for an asset"""
    try:
        topic_response = sns_client.create_topic(Name=f'AssetTopic{database_id}-{asset_id}')
        return topic_response['TopicArn']
    except Exception as e:
        logger.exception(f"Error creating SNS topic: {e}")
        raise VAMSGeneralErrorResponse(f"Error creating SNS topic.")


def _scopes_for_database(database_id):
    """Return the tag/tag-type resolution scopes for an asset's database.

    An asset's tags resolve within its own database partition plus the shared
    GLOBAL partition; another database's partition is never in scope.
    """
    scopes = [GLOBAL_SCOPE]
    if database_id and database_id != GLOBAL_SCOPE:
        scopes.append(database_id)
    return scopes

def _query_scoped_items(table, database_id):
    """Collect all items from the GLOBAL and database_id partitions of a composite table."""
    items = []
    for scope in _scopes_for_database(database_id):
        query_kwargs = {'KeyConditionExpression': Key('databaseId').eq(scope)}
        while True:
            response = table.query(**query_kwargs)
            items.extend(response.get('Items', []))
            if 'LastEvaluatedKey' in response:
                query_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
            else:
                break
    return items

def get_set_tag_types(tags, database_id):
    """Get unique tag types for a list of tags within the asset's DB + GLOBAL scope"""
    uniqueSetTagTypes = []

    # If no tags provided, return no tag types
    if tags is None or len(tags) == 0:
        return uniqueSetTagTypes

    # Loop through every in-scope tag (asset's database partition + GLOBAL)
    for tag in _query_scoped_items(tag_table, database_id):
        # If the tags provided matches the tag looked up, add to uniqueSetTagTypes if it's not already part of the array
        if tag["tagName"] in tags:
            if tag["tagTypeName"] not in uniqueSetTagTypes:
                uniqueSetTagTypes.append(tag["tagTypeName"])

    return uniqueSetTagTypes

def get_required_tag_types(database_id):
    """Get tag types that are required for assets within the asset's DB + GLOBAL scope"""
    # In-scope tag types (asset's database partition + GLOBAL)
    rawTagTypeItems = _query_scoped_items(tag_type_table, database_id)

    # Get tags associated and then exclude tag types from required if no tags associated
    tags = _query_scoped_items(tag_table, database_id)

    formatted_tag_results = {}
    for tagResult in tags:
        tagName = tagResult["tagName"]
        tagTypeName = tagResult["tagTypeName"]

        if tagTypeName not in formatted_tag_results:
            formatted_tag_results[tagTypeName] = [tagName]
        else:
            formatted_tag_results[tagTypeName].append(tagName)

    # Final tag required loops
    tagTypesRequired = []
    for tagType in rawTagTypeItems:
        # if tagtype has "required" set to true and there are tags in formatted_tag_results for the type, add to list
        if tagType.get("required", "False") == "True":
            if tagType["tagTypeName"] in formatted_tag_results:
                tagTypesRequired.append(tagType["tagTypeName"])

    return tagTypesRequired

def verify_all_required_tags_satisfied(assetTags, database_id):
    """Verify that all required tag types are satisfied by the asset tags"""
    assetTagTypes = get_set_tag_types(assetTags, database_id)
    requiredTagTypes = get_required_tag_types(database_id)
    missingTagTypesForError = []

    if requiredTagTypes is None or len(requiredTagTypes) == 0:
        return True
    else:
        for requiredTagType in requiredTagTypes:
            if requiredTagType not in assetTagTypes:
                missingTagTypesForError.append(requiredTagType)

    if len(missingTagTypesForError) == 0:
        return True

    # Raise error with list of required tag types missing from assets
    if len(missingTagTypesForError) > 0:
        raise ValueError(f"Asset Details are missing tags of required tag types: {missingTagTypesForError}")

def check_s3_prefix_exists(bucket_name, prefix):
    """
    Check whether an S3 prefix (folder) is occupied, archived data included.

    Read through list_object_versions rather than list_objects_v2, because the two
    disagree on precisely the state this check exists to detect. Archiving an asset
    issues delete_object with no VersionId over every object under its prefix, so
    the objects are retained as non-current versions behind delete markers.
    list_objects_v2 returns only current objects and reports such a prefix as
    empty; list_object_versions returns the retained versions and the delete
    markers, so an archived asset's prefix still reads as occupied.

    Occupancy is a property of the prefix itself, so this holds whatever assetId
    or bucket record the occupying asset belongs to — including an asset whose key
    was supplied through bucketExistingKey and shares no segment with its assetId.

    The probe drops the trailing slash and re-filters, because a key equal to the
    prefix without it is a conflict that the slash-bearing prefix does not match.
    bucketExistingKey accepts `legacy`, which normalize_s3_path resolves to
    `<base>legacy` with no trailing slash, and `<base>legacy` sorts immediately
    outside `<base>legacy/` — so probing only the slash-bearing form reports the
    location free while resolve_asset_file_path (handlers/assets/assetFiles.py)
    appends the slash and reads the very same objects. Widening the probe alone
    would match a sibling such as `<base>legacy-other/`, so each returned key is
    accepted only when it equals the bare form or sits under the slash-bearing
    one — the same containment rule keys_conflict() applies.

    Args:
        bucket_name: S3 bucket name
        prefix: S3 prefix to check (should end with '/')

    Returns:
        bool: True if any object version or delete marker occupies the prefix
    """
    bare = prefix.rstrip('/')
    folder = bare + '/'
    try:
        response = s3_client.list_object_versions(
            Bucket=bucket_name,
            Prefix=bare,
            MaxKeys=100  # Enough to see past sibling keys sharing the bare prefix
        )
        for entry in (response.get('Versions') or []) + (response.get('DeleteMarkers') or []):
            key = entry.get('Key', '')
            if key == bare or key.startswith(folder):
                return True
        # A truncated page whose entries were all siblings leaves the question open, so
        # treat it as occupied rather than free: this check is the only occupancy guard on
        # the derived branch, and reading it as free is the failure that admits a takeover.
        return bool(response.get('IsTruncated'))
    except Exception as e:
        logger.exception(f"Error checking S3 prefix existence: {e}")
        raise VAMSGeneralErrorResponse("Error validating S3 location")

def check_s3_key_exists(bucket_name, key):
    """
    Check if a specific S3 key exists
    
    Args:
        bucket_name: S3 bucket name
        key: S3 key to check
        
    Returns:
        bool: True if key exists, False otherwise
    """
    try:
        s3_client.head_object(Bucket=bucket_name, Key=key)
        return True
    except s3_client.exceptions.ClientError as e:
        if e.response['Error']['Code'] == '404':
            return False
        else:
            logger.exception(f"Error checking S3 key existence: {e}")
            raise VAMSGeneralErrorResponse("Error validating S3 location")
    except Exception as e:
        logger.exception(f"Error checking S3 key existence: {e}")
        raise VAMSGeneralErrorResponse("Error validating S3 location")

def normalize_location_key(key):
    """Normalize an S3 location key for comparison (strip leading slash).

    Values resolved by normalize_s3_path() have their leading slash stripped, so we
    normalize stored assetLocation.Key values the same way before comparing.
    """
    if not key:
        return ''
    return key.lstrip('/')


def keys_conflict(existing_key, target_key):
    """Whether two stored S3 asset locations occupy the same place in the bucket.

    Compared on prefix-folder semantics, so a conflict is an exact match or a
    parent/child containment in either direction.
    """
    existing_key = normalize_location_key(existing_key)
    target_key = normalize_location_key(target_key)
    if not existing_key or not target_key:
        return False

    existing_prefix = existing_key if existing_key.endswith('/') else existing_key + '/'
    target_prefix = target_key if target_key.endswith('/') else target_key + '/'

    return (existing_key == target_key
            or target_prefix.startswith(existing_prefix)
            or existing_prefix.startswith(target_prefix))


def prefix_roots_overlap(prefix_a, prefix_b):
    """Whether two bucket records' base prefixes cover overlapping S3 keys.

    Equal prefixes overlap, and so does a nested pair: records at 'assets/' and
    'assets/team1/' on one bucket both cover every key under 'assets/team1/', so
    an asset registered under either can occupy a key the other's assets reach.
    An empty prefix is the bucket root, which contains every prefix in it.
    """
    prefix_a = normalize_base_assets_prefix(prefix_a)
    prefix_b = normalize_base_assets_prefix(prefix_b)
    return (prefix_a == prefix_b
            or prefix_a.startswith(prefix_b)
            or prefix_b.startswith(prefix_a))


def resolve_colocated_bucket_ids(bucket_id, bucket_name, base_assets_prefix):
    """Return every registered bucketId whose prefix root overlaps this location.

    Ownership records are indexed by bucketId, but several bucket records can point
    into the same physical region of one bucket — either at the same
    baseAssetsPrefix, or at prefixes that nest one inside the other — in which case
    assets held under different bucketIds share S3 keys. Keying an ownership check
    on a single bucketId would let a record under a sibling bucketId slip past it,
    so the check runs against every overlapping bucketId.

    Overlap is deliberately wider than equality: a record included here only
    widens the set of asset records the ownership checks read, and the conflict
    verdict itself still rests on keys_conflict() comparing full keys, so an
    unrelated asset under an overlapping record cannot cause a false rejection.

    Read through the bucketNameGSI, which is partitioned on bucketName, so this
    is a keyed query over one physical bucket's records rather than a table scan.
    The asset's own bucketId is always included, so the caller still gets the
    primary check when no sibling record exists.

    Args:
        bucket_id: The bucketId the new asset will use
        bucket_name: The physical S3 bucket name that bucketId resolves to
        base_assets_prefix: The normalized base prefix that bucketId resolves to

    Returns:
        list: bucketIds whose prefix root overlaps this one, own bucketId first
    """
    bucket_ids = [bucket_id]
    if not bucket_name:
        return bucket_ids

    try:
        query_kwargs = {
            'IndexName': 'bucketNameGSI',
            'KeyConditionExpression': Key('bucketName').eq(bucket_name),
        }
        while True:
            response = buckets_table.query(**query_kwargs)
            for item in response.get('Items', []):
                candidate_id = item.get('bucketId')
                if not candidate_id or candidate_id in bucket_ids:
                    continue
                if prefix_roots_overlap(item.get('baseAssetsPrefix'), base_assets_prefix):
                    bucket_ids.append(candidate_id)

            if 'LastEvaluatedKey' in response:
                query_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
            else:
                break
    except Exception as e:
        logger.exception(f"Error resolving colocated bucket records: {e}")
        raise VAMSGeneralErrorResponse("Error validating S3 location")

    return bucket_ids


def log_unusable_location_record(item, context):
    """Log an asset record the key comparison cannot evaluate.

    A record whose assetLocation.Key is absent or empty compares as "no conflict"
    against every target, so it is skipped rather than protected. No reachable
    create path produces one, which is why this logs instead of rejecting — a
    rejection would turn one malformed row into a create-path outage for the whole
    bucket — but the row is recorded so a fail-open skip is visible.

    A record with no bucketId is not on BucketIdGSI at all and so cannot be
    logged here; on the derived-key branch such a record's data is still caught
    by the S3 prefix check, which reads the bucket rather than the index.
    """
    logger.warning(
        f"Skipping asset record with no usable assetLocation.Key during {context}: "
        f"{item.get('databaseId')}:{item.get('assetId')}"
    )


def assert_existing_key_not_owned(bucket_ids, resolved_s3_key):
    """Ensure no existing asset already points at the resolved S3 key.

    Because multiple databases can share one bucket and prefix root, an asset's
    S3 location is only unambiguously owned when a single asset record maps to it.
    We query all assets in the colocated buckets (via the BucketIdGSI) and reject
    if any existing asset's assetLocation.Key equals, is a parent of, or is a
    child of the resolved key, so a new asset cannot be bound onto a location
    another asset owns.

    A caller-supplied bucketExistingKey is an arbitrary key, so every record in
    the partition has to be compared: this walks the full BucketIdGSI partition.
    Use assert_derived_asset_key_not_owned for a key derived from the assetId.

    This is the only ownership authority on the bucketExistingKey branch. That
    branch requires the key to already exist in S3, so an S3 occupancy test
    cannot distinguish the caller's legitimate onboarding target from another
    asset's data, and the asset records are what separate them.

    Args:
        bucket_ids: The bucketIds mapping to the new asset's physical location
        resolved_s3_key: The full S3 key resolved from bucketExistingKey

    Raises:
        VAMSGeneralErrorResponse: if an existing asset already occupies the key
    """
    target = normalize_location_key(resolved_s3_key)
    if not target:
        return

    try:
        for bucket_id in bucket_ids:
            query_kwargs = {
                'IndexName': 'BucketIdGSI',
                'KeyConditionExpression': Key('bucketId').eq(bucket_id),
            }
            while True:
                response = asset_table.query(**query_kwargs)
                for item in response.get('Items', []):
                    existing_key = normalize_location_key(
                        item.get('assetLocation', {}).get('Key', '')
                    )
                    if not existing_key:
                        log_unusable_location_record(item, "bucketExistingKey ownership check")
                        continue
                    if not keys_conflict(existing_key, target):
                        continue
                    logger.error(
                        f"bucketExistingKey {resolved_s3_key} conflicts with existing asset "
                        f"{item.get('databaseId')}:{item.get('assetId')} at {existing_key}"
                    )
                    raise VAMSGeneralErrorResponse(
                        "The specified bucketExistingKey is already in use by another asset"
                    )

                if 'LastEvaluatedKey' in response:
                    query_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
                else:
                    break
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error validating bucketExistingKey ownership: {e}")
        raise VAMSGeneralErrorResponse("Error validating S3 location")


def assert_derived_asset_key_not_owned(bucket_ids, asset_id, resolved_s3_key, database_id=None):
    """Ensure no asset RECORD already owns the location derived from an assetId.

    This is the record-side half of the derived branch's two-layer check, and it
    covers exactly one thing the S3 side cannot: an asset record that still owns
    the key while its S3 data has been permanently expunged — every version hard
    deleted, or a lifecycle rule that removed the non-current versions and the
    delete markers — leaving an occupied record over an empty prefix. The S3
    check covers the converse, an occupied prefix whose owner is unreachable
    through this lookup (a key that does not derive from its asset's assetId, or
    an asset under a bucket record that is not colocated). Both are needed;
    neither subsumes the other.

    The record survives archiving unchanged apart from its databaseId (which gains
    a "#deleted" suffix), so it is found here whatever partition it now sits in.

    BucketIdGSI is partitioned on bucketId with assetId as its sort key, and the
    key on this path is derived from the assetId, so a full key-condition on both
    is an exact lookup rather than a walk of the bucket's partition. That
    narrowness is what limits this layer to same-assetId collisions.

    Args:
        bucket_ids: The bucketIds mapping to the new asset's physical location
        asset_id: The assetId the derived S3 key was built from
        resolved_s3_key: The derived S3 key (baseAssetsPrefix + assetId + '/')
        database_id: The requesting database, used only to decide how specific the
            rejection message may be

    Raises:
        VAMSGeneralErrorResponse: if an existing asset already occupies the key
    """
    target = normalize_location_key(resolved_s3_key)
    if not target or not asset_id:
        return

    try:
        for bucket_id in bucket_ids:
            response = asset_table.query(
                IndexName='BucketIdGSI',
                KeyConditionExpression=Key('bucketId').eq(bucket_id) & Key('assetId').eq(asset_id),
            )
            for item in response.get('Items', []):
                existing_key = normalize_location_key(
                    item.get('assetLocation', {}).get('Key', '')
                )
                if not existing_key:
                    log_unusable_location_record(item, "derived asset key ownership check")
                    continue
                if not keys_conflict(existing_key, target):
                    continue
                logger.error(
                    f"Derived asset key {resolved_s3_key} conflicts with existing asset "
                    f"{item.get('databaseId')}:{item.get('assetId')} at {existing_key}"
                )
                raise VAMSGeneralErrorResponse(
                    derived_key_conflict_message(item.get('databaseId', ''), database_id)
                )
    except VAMSGeneralErrorResponse:
        raise
    except Exception as e:
        logger.exception(f"Error validating asset identifier ownership: {e}")
        raise VAMSGeneralErrorResponse("Error validating S3 location")


def derived_key_conflict_message(owner_database_id, requesting_database_id):
    """The rejection text for a derived-key conflict, scaled to what the caller may see.

    When the conflicting record belongs to the caller's OWN database the caller can
    already list it (listAssets with includeArchived), so naming the situation and
    its remedy discloses nothing and turns an opaque refusal into an actionable one.
    When it belongs to another database the message stays generic and identical to
    the ordinary live-collision refusal, so it confirms nothing about another
    database's contents.
    """
    generic = "Asset identifier is not unique for the given S3 bucket location"
    if not requesting_database_id or not owner_database_id:
        return generic
    if owner_database_id.replace(ARCHIVED_DATABASE_SUFFIX, '') != requesting_database_id:
        return generic
    if owner_database_id.endswith(ARCHIVED_DATABASE_SUFFIX):
        return ("An archived asset in this database already occupies this asset identifier's "
                "S3 location. Unarchive that asset to restore it, or permanently delete it, "
                "before reusing the identifier.")
    return "An asset in this database already occupies this asset identifier's S3 location"


def create_prefix_folder(bucket, prefix):
    """Create a prefix folder in S3 bucket.

    Raises rather than reporting failure through a return value: the folder
    marker is the only live object under a new asset's prefix, so an asset
    persisted without it holds a prefix that lists as empty.
    """
    try:
        # Create an empty object with the prefix to simulate a folder
        s3_client.put_object(
            Bucket=bucket,
            Key=prefix,
            Body='',
            # Grant the bucket owner full control so a folder marker written into a
            # cross-account asset bucket is owned/readable by that account.
            ACL='bucket-owner-full-control'
        )
        logger.info(f"Created prefix folder {prefix} in bucket {bucket}")
        return True
    except Exception as e:
        logger.exception(f"Error creating prefix folder: {e}")
        raise VAMSGeneralErrorResponse("Error creating the asset S3 location")

def create_initial_version_record(database_id, asset_id, version_id, description, created_by='SYSTEM_USER'):
    """Create initial version record in the asset versions table

    Args:
        database_id: The database ID (needed for the V2 composite PK)
        asset_id: The asset ID
        version_id: The version ID
        description: Version description
        created_by: Username of the creator
    """
    try:
        versions_table = dynamodb.Table(asset_versions_table_name)
        version_id = f"{version_id}"
        now = datetime.utcnow().isoformat()

        version_record = {
            'databaseId:assetId': f"{database_id}:{asset_id}",
            'assetVersionId': version_id,
            'databaseId': database_id,
            'assetId': asset_id,
            'dateCreated': now,
            'comment': f'Initial asset creation - Version {version_id} (No Files, No Metadata)',
            'description': description,
            'createdBy': created_by,
            'isCurrentVersion': True
        }

        versions_table.put_item(Item=version_record)
        logger.info(f"Created initial version record {version_id} for asset {asset_id}")
        return version_id

    except Exception as e:
        logger.exception(f"Error creating initial version record: {e}")
        raise VAMSGeneralErrorResponse(f"Error creating initial version record.")

#######################
# API Implementation
#######################

def validate_tags_exist(tags, database_id):
    """Validate that all provided tags exist within the asset's DB + GLOBAL scope.

    Tag names are resolved from only the GLOBAL partition and the asset's own
    database partition, so an asset cannot reference another database's tags.
    """
    if not tags:
        return True

    # Gather valid tag names from the GLOBAL and asset-database partitions only
    existing_tags = set()
    for tag in _query_scoped_items(tag_table, database_id):
        existing_tags.add(tag["tagName"])

    # Check for invalid tags (present in neither GLOBAL nor the asset's database)
    invalid_tags = [tag for tag in tags if tag not in existing_tags]
    if invalid_tags:
        logger.error(f"Asset tags not found in database {database_id} or GLOBAL scope")
        raise ValueError("Invalid tags provided. Tags must exist in the system.")

    return True

def create_asset(request_model: CreateAssetRequestModel, claims_and_roles, s3ExternalGenerated = False):
    """Create a new asset (metadata only)"""
    # Generate asset ID if not provided
    assetId = request_model.assetId if request_model.assetId else f"x{str(uuid.uuid4())}"
    databaseId = request_model.databaseId
    
    # Check if asset already exists (if assetId was provided)
    if request_model.assetId:
        existing_asset = asset_table.get_item(
            Key={
                'databaseId': databaseId,
                'assetId': assetId
            }
        ).get('Item')
        
        if existing_asset:
            raise VAMSGeneralErrorResponse("Asset with specified ID already exists")
    
    # Verify database exists
    db_response = database_table.get_item(
        Key={
            'databaseId': databaseId
        }
    )
    if 'Item' not in db_response:
        raise VAMSGeneralErrorResponse("VAMS General Error: Database does not exist")
    
    # Validate tags exist in the system (only if we aren't generating from S3 external where we don't know tags)
    if not s3ExternalGenerated:
        validate_tags_exist(request_model.tags, databaseId)
        verify_all_required_tags_satisfied(request_model.tags, databaseId)
    
    # Create asset record
    now = datetime.utcnow().strftime('%B %d %Y - %H:%M:%S')

    #Get bucket and prefix details
    bucketDetails = get_default_bucket_details(databaseId)
    
    # Determine S3 bucket and key
    s3_bucket_id = bucketDetails['bucketId']
    s3_bucket = bucketDetails['bucketName']
    s3_bucket_prefix = bucketDetails['baseAssetsPrefix']

    # Ownership checks run against every bucket record whose prefix root overlaps
    # this physical location, not just the database's own bucketId.
    colocated_bucket_ids = resolve_colocated_bucket_ids(s3_bucket_id, s3_bucket, s3_bucket_prefix)

    # The location checks below are a read followed by a write, so two concurrent
    # creates in different databases can both pass and both persist onto one
    # prefix. save_asset_details' ConditionExpression guards only the caller's own
    # (databaseId, assetId) and cannot see the other database's record. Closing
    # this needs a per-location claim record written with a conditional put, which
    # is a data-model change and is tracked separately.

    if request_model.bucketExistingKey:
        # Use the provided existing key (must still be at the base path for the database id -> bucket id provided)
        s3_key = normalize_s3_path(s3_bucket_prefix, request_model.bucketExistingKey)
        logger.info(f"Validating existing S3 key: {s3_key} in bucket: {s3_bucket}")

        # Ensure the resolved key actually falls under THIS database's base prefix.
        # normalize_s3_path returns the file path as-is when it already starts with the
        # base key; guard against a supplied key that resolves outside the base prefix.
        normalized_base_prefix = normalize_location_key(s3_bucket_prefix)
        if not normalize_location_key(s3_key).startswith(normalized_base_prefix):
            error_msg = "The specified bucketExistingKey is not within the asset's database default S3 bucket location"
            logger.error(f"{error_msg}: resolved {s3_key} not under base prefix {normalized_base_prefix}")
            raise VAMSGeneralErrorResponse(error_msg)

        # Check if the key exists in S3 (full path: bucketPrefix/bucketExistingKey)
        if not check_s3_key_exists(s3_bucket, s3_key):
            error_msg = "The specified bucketExistingKey does not exist in the asset's database default S3 bucket"
            logger.error(error_msg)
            raise VAMSGeneralErrorResponse(error_msg)

        # Reject if another asset (in any database sharing this bucket) already owns
        # this S3 location, so an asset cannot be bound onto another asset's data.
        assert_existing_key_not_owned(colocated_bucket_ids, s3_key)

        logger.info(f"Using existing S3 key: {s3_key} in bucket: {s3_bucket}")
    else:
        # Create a new prefix folder
        s3_key = s3_bucket_prefix + assetId + '/'
        logger.info(f"Validating new prefix uniqueness: {s3_key} in bucket: {s3_bucket}")

        # Two layers guard this location, each covering the other's blind spot. The
        # get_item existence check above sees neither, being scoped to the caller's
        # own live database partition.
        #
        # Layer 1, the asset records: catches a record that owns the key while its
        # S3 data has been permanently expunged, so nothing remains under the
        # prefix to find. Limited to this assetId, being an exact index lookup.
        assert_derived_asset_key_not_owned(colocated_bucket_ids, assetId, s3_key, databaseId)

        # Layer 2, S3 itself: catches occupancy of the prefix whoever owns it —
        # including an asset whose key does not derive from its assetId (a
        # bucketExistingKey onboarding) and an asset under a bucket record this
        # deployment's registry does not colocate. Version-aware, so an archived
        # asset's retained versions and delete markers still register.
        if check_s3_prefix_exists(s3_bucket, s3_key):
            # For S3-external generation (bucket sync ingestion), the prefix
            # existing is the trigger for creation: files were placed directly
            # in S3 and the asset record is being bound onto them. Only reject
            # if another asset record already owns the location.
            if not s3ExternalGenerated:
                error_msg = "Asset identifier is not unique for the given S3 bucket location"
                logger.error(error_msg)
                raise VAMSGeneralErrorResponse(error_msg)
            assert_existing_key_not_owned(colocated_bucket_ids, s3_key)
            logger.info(f"Binding S3-external asset to existing prefix: {s3_key} in bucket: {s3_bucket}")
        else:
            logger.info(f"Creating new prefix folder: {s3_key} in bucket: {s3_bucket}")
            create_prefix_folder(s3_bucket, s3_key)
    
    # Get username for version creation
    username = claims_and_roles.get("tokens", ["SYSTEM_USER"])[0]
    
    # Create initial version record in versions table
    initial_version_id = create_initial_version_record(
        databaseId,
        assetId,
        '0',
        request_model.description,
        username
    )
    
    # Create asset record with new structure
    asset = {
        'databaseId': databaseId,
        'assetId': assetId,
        'assetName': request_model.assetName,
        'description': request_model.description,
        'isDistributable': request_model.isDistributable,
        'tags': request_model.tags if request_model.tags else [],
        'assetType': 'none',  # No files yet
        'snsTopic': create_sns_topic_for_asset(databaseId, assetId),
        'currentVersionId': initial_version_id,
        'bucketId': s3_bucket_id,
        'assetLocation' : {
            'Key': s3_key,
        }
    }
    
    # Save asset to DynamoDB
    save_asset_details(asset)

    # Update asset count
    update_asset_count(db_database, asset_storage_table_name, {}, databaseId)

    # Record creation in asset history (best-effort)
    write_asset_history_record(
        databaseId,
        assetId,
        CHANGE_SOURCE_CREATE_DIRECT if s3ExternalGenerated else CHANGE_SOURCE_CREATE,
        username,
        build_asset_snapshot(asset)
    )

    # Return response
    return CreateAssetResponseModel(
        assetId=assetId,
        message="Asset created successfully"
    )

#######################
# Lambda Handler
#######################

def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for asset creation API"""
    global claims_and_roles
    claims_and_roles = request_to_claims(event)
    
    try:
        # Parse request body with enhanced error handling
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"}, event=event)
        
        # Parse JSON body safely
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"}, event=event)
        elif isinstance(body, dict):
            body = body
        else:
            logger.error("Request body is not a string")
            return validation_error(body={'message': "Request body cannot be parsed"}, event=event)
        
        # Validate required fields in the request body
        required_fields = ['databaseId', 'assetName', 'description', 'isDistributable']
        for field in required_fields:
            if field not in body:
                return validation_error(body={'message': f"Missing required field: {field}"}, event=event)
        
        # Parse request model
        request_model = parse(body, model=CreateAssetRequestModel)
        
        # Check authorization
        asset = {
            "object__type": "asset",
            "databaseId": request_model.databaseId,
            "assetName": request_model.assetName,
            "tags": request_model.tags
        }
        
        # Fail closed: with no authenticated identity no authorization can be
        # evaluated, so deny rather than fall through to the mutation.
        if len(claims_and_roles["tokens"]) == 0:
            return authorization_error()

        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if not (casbin_enforcer.enforce(asset, "PUT") and casbin_enforcer.enforceAPI(event)):
            return authorization_error()

        # Process request
        response = create_asset(request_model, claims_and_roles)
        return success(body=response.dict())
            
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': validation_error_message(v)}, event=event)
    except ValueError as v:
        logger.exception(f"Value error: {v}")
        return validation_error(body={'message': str(v)}, event=event)
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error(event=event)
