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
from boto3.dynamodb.types import TypeDeserializer
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
dynamodb_client = boto3.client('dynamodb', config=retry_config)
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
deserializer = TypeDeserializer()
paginator = dynamodb_client.get_paginator('scan')

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
        
        #Make sure we end in a slash for the path
        if not base_assets_prefix.endswith('/'):
            base_assets_prefix += '/'

        # Remove leading slash from file path if present
        if base_assets_prefix.startswith('/'):
            base_assets_prefix = base_assets_prefix[1:]

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


def get_set_tag_types(tags):
    """Get unique tag types for a list of tags"""
    uniqueSetTagTypes = []

    # If no tags provided, return no tag types
    if tags is None or len(tags) == 0:
        return uniqueSetTagTypes

    # Loop to get all tag results (to know their tag types)
    rawTagItems = []
    page_iteratorTags = paginator.paginate(
        TableName=tag_table_name,
        PaginationConfig={
            'MaxItems': 1000,
            'PageSize': 1000,
        }
    ).build_full_result()
    if(len(page_iteratorTags["Items"]) > 0):
        rawTagItems.extend(page_iteratorTags["Items"])
        while("NextToken" in page_iteratorTags):
            page_iteratorTags = paginator.paginate(
                TableName=tag_table_name,
                PaginationConfig={
                    'MaxItems': 1000,
                    'PageSize': 1000,
                    'StartingToken': page_iteratorTags["NextToken"]
                }
            ).build_full_result()
            if(len(page_iteratorTags["Items"]) > 0):
                rawTagItems.extend(page_iteratorTags["Items"])

    # Loop through every tag in the database
    for tag in rawTagItems:
        deserialized_document = {k: deserializer.deserialize(v) for k, v in tag.items()}

        # If the tags provided matches the tag looked up, add to uniqueSetTagTypes if it's not already part of the array
        if deserialized_document["tagName"] in tags:
            if deserialized_document["tagTypeName"] not in uniqueSetTagTypes:
                uniqueSetTagTypes.append(deserialized_document["tagTypeName"])

    return uniqueSetTagTypes

def get_required_tag_types():
    """Get tag types that are required for assets"""
    # Loop to get all tag results for tag type
    rawTagTypeItems = []
    page_iteratorTags = paginator.paginate(
        TableName=tag_type_table_name,
        PaginationConfig={
            'MaxItems': 1000,
            'PageSize': 1000,
        }
    ).build_full_result()
    if(len(page_iteratorTags["Items"]) > 0):
        rawTagTypeItems.extend(page_iteratorTags["Items"])
        while("NextToken" in page_iteratorTags):
            page_iteratorTags = paginator.paginate(
                TableName=tag_type_table_name,
                PaginationConfig={
                    'MaxItems': 1000,
                    'PageSize': 1000,
                    'StartingToken': page_iteratorTags["NextToken"]
                }
            ).build_full_result()
            if(len(page_iteratorTags["Items"]) > 0):
                rawTagTypeItems.extend(page_iteratorTags["Items"])

    # Get tags associated and then exclude tag types from required if no tags associated
    # Loop to get all tag results for tag type
    rawTagItems = []
    page_iteratorTags = paginator.paginate(
        TableName=tag_table_name,
        PaginationConfig={
            'MaxItems': 1000,
            'PageSize': 1000,
        }
    ).build_full_result()
    if(len(page_iteratorTags["Items"]) > 0):
        rawTagItems.extend(page_iteratorTags["Items"])
        while("NextToken" in page_iteratorTags):
            page_iteratorTags = paginator.paginate(
                TableName=tag_table_name,
                PaginationConfig={
                    'MaxItems': 1000,
                    'PageSize': 1000,
                    'StartingToken': page_iteratorTags["NextToken"]
                }
            ).build_full_result()
            if(len(page_iteratorTags["Items"]) > 0):
                rawTagItems.extend(page_iteratorTags["Items"])

    tags = []
    for tag in rawTagItems:
        deserialized_document = {k: deserializer.deserialize(v) for k, v in tag.items()}
        tags.append(deserialized_document)

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
        deserialized_document = {k: deserializer.deserialize(v) for k, v in tagType.items()}

        # if tagtype has "required" set to true and there are tags in formatted_tag_results for the type, add to list
        if deserialized_document.get("required", "False") == "True":
            if deserialized_document["tagTypeName"] in formatted_tag_results:
                tagTypesRequired.append(deserialized_document["tagTypeName"])

    return tagTypesRequired

def verify_all_required_tags_satisfied(assetTags):
    """Verify that all required tag types are satisfied by the asset tags"""
    assetTagTypes = get_set_tag_types(assetTags)
    requiredTagTypes = get_required_tag_types()
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
    Check if an S3 prefix (folder) exists by listing objects with that prefix
    
    Args:
        bucket_name: S3 bucket name
        prefix: S3 prefix to check (should end with '/')
        
    Returns:
        bool: True if prefix exists (has objects), False otherwise
    """
    try:
        response = s3_client.list_objects_v2(
            Bucket=bucket_name,
            Prefix=prefix,
            MaxKeys=1  # Only need to know if at least one object exists
        )
        return 'Contents' in response and len(response['Contents']) > 0
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


def assert_existing_key_not_owned(bucket_id, resolved_s3_key):
    """Ensure no existing asset already points at the resolved S3 key.

    Because multiple databases can share one bucket and prefix root, an asset's
    S3 location is only unambiguously owned when a single asset record maps to it.
    We query all assets in the same bucket (via the BucketIdGSI) and reject if any
    existing asset's assetLocation.Key equals, is a parent of, or is a child of the
    resolved key, so a new asset cannot be bound onto a location another asset owns.

    Args:
        bucket_id: The bucketId the new asset will use
        resolved_s3_key: The full S3 key resolved from bucketExistingKey

    Raises:
        VAMSGeneralErrorResponse: if an existing asset already occupies the key
    """
    target = normalize_location_key(resolved_s3_key)
    if not target:
        return

    # Compare on prefix-folder semantics: treat the target as its containing prefix
    target_prefix = target if target.endswith('/') else target + '/'

    try:
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
                    continue
                existing_prefix = existing_key if existing_key.endswith('/') else existing_key + '/'

                # Reject exact match, or where one prefix contains the other
                # (parent/child relationship within the shared bucket).
                if (existing_key == target
                        or target_prefix.startswith(existing_prefix)
                        or existing_prefix.startswith(target_prefix)):
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


def create_prefix_folder(bucket, prefix):
    """Create a prefix folder in S3 bucket"""
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
        return False

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

def validate_tags_exist(tags):
    """Validate that all provided tags exist in the database"""
    if not tags:
        return True
    
    # Get all existing tags from database
    rawTagItems = []
    page_iterator = paginator.paginate(
        TableName=tag_table_name,
        PaginationConfig={'MaxItems': 1000, 'PageSize': 1000}
    ).build_full_result()
    
    if len(page_iterator["Items"]) > 0:
        rawTagItems.extend(page_iterator["Items"])
        while "NextToken" in page_iterator:
            page_iterator = paginator.paginate(
                TableName=tag_table_name,
                PaginationConfig={
                    'MaxItems': 1000, 'PageSize': 1000,
                    'StartingToken': page_iterator["NextToken"]
                }
            ).build_full_result()
            if len(page_iterator["Items"]) > 0:
                rawTagItems.extend(page_iterator["Items"])
    
    existing_tags = []
    for tag in rawTagItems:
        deserialized_document = {k: deserializer.deserialize(v) for k, v in tag.items()}
        existing_tags.append(deserialized_document["tagName"])
    
    # Check for invalid tags
    invalid_tags = [tag for tag in tags if tag not in existing_tags]
    if invalid_tags:
        raise ValueError(f"Invalid tags provided. Tags must exist in the system.")
    
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
        validate_tags_exist(request_model.tags)
        verify_all_required_tags_satisfied(request_model.tags)
    
    # Create asset record
    now = datetime.utcnow().strftime('%B %d %Y - %H:%M:%S')

    #Get bucket and prefix details
    bucketDetails = get_default_bucket_details(databaseId)
    
    # Determine S3 bucket and key
    s3_bucket_id = bucketDetails['bucketId']
    s3_bucket = bucketDetails['bucketName']
    s3_bucket_prefix = bucketDetails['baseAssetsPrefix']
    
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
        assert_existing_key_not_owned(s3_bucket_id, s3_key)

        logger.info(f"Using existing S3 key: {s3_key} in bucket: {s3_bucket}")
    else:
        # Create a new prefix folder
        s3_key = s3_bucket_prefix + assetId + '/'
        logger.info(f"Validating new prefix uniqueness: {s3_key} in bucket: {s3_bucket}")

        # Check if the prefix already exists (full path: bucketPrefix/assetId/)
        if check_s3_prefix_exists(s3_bucket, s3_key):
            # For S3-external generation (bucket sync ingestion), the prefix
            # existing is the trigger for creation: files were placed directly
            # in S3 and the asset record is being bound onto them. Only reject
            # if another asset record already owns the location.
            if not s3ExternalGenerated:
                error_msg = "Asset identifier is not unique for the given S3 bucket location"
                logger.error(error_msg)
                raise VAMSGeneralErrorResponse(error_msg)
            assert_existing_key_not_owned(s3_bucket_id, s3_key)
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
