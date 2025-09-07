# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
import uuid
from datetime import datetime
from botocore.config import Config
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeDeserializer
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
from handlers.assets.assetCount import update_asset_count
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from models.common import APIGatewayProxyResponseV2, internal_error, success, validation_error, general_error, authorization_error, VAMSGeneralErrorResponse
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
    s3_asset_buckets_table = os.environ["S3_ASSET_BUCKETS_STORAGE_TABLE_NAME"]
    asset_storage_table_name = os.environ["ASSET_STORAGE_TABLE_NAME"]
    db_database = os.environ["DATABASE_STORAGE_TABLE_NAME"]
    tag_type_table_name = os.environ["TAG_TYPES_STORAGE_TABLE_NAME"]
    tag_table_name = os.environ["TAG_STORAGE_TABLE_NAME"]
    asset_versions_table_name = os.environ.get("ASSET_VERSIONS_STORAGE_TABLE_NAME")
except Exception as e:
    logger.exception("Failed loading environment variables")
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
    """Save asset details to DynamoDB"""
    try:
        asset_table.put_item(Item=asset_data)
    except Exception as e:
        logger.exception(f"Error saving asset details: {e}")
        raise VAMSGeneralErrorResponse(f"Error saving asset.")

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

def create_prefix_folder(bucket, prefix):
    """Create a prefix folder in S3 bucket"""
    try:
        # Create an empty object with the prefix to simulate a folder
        s3_client.put_object(
            Bucket=bucket,
            Key=prefix,
            Body=''
        )
        logger.info(f"Created prefix folder {prefix} in bucket {bucket}")
        return True
    except Exception as e:
        logger.exception(f"Error creating prefix folder: {e}")
        return False

def create_initial_version_record(asset_id, version_id, description, created_by='system'):
    """Create initial version record in the asset versions table"""
    try:
        versions_table = dynamodb.Table(asset_versions_table_name)
        version_id = f"{version_id}"
        now = datetime.utcnow().isoformat()
        
        version_record = {
            'assetId': asset_id,
            'assetVersionId': version_id,
            'dateCreated': now,
            'comment': f'Initial asset creation - Version {version_id} (No Files)',
            'description': description,
            'specifiedPipelines': [],
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
    db_table = dynamodb.Table(db_database)
    db_response = db_table.get_item(
        Key={
            'databaseId': databaseId
        }
    )
    if 'Item' not in db_response:
        raise VAMSGeneralErrorResponse("VAMS General Error: Database does not exist")
    
    # Verify required tags (only if we aren't generating from S3 external where we don't know tags)
    if not s3ExternalGenerated:
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
        logger.info(f"Using existing S3 key: {s3_key} in bucket: {s3_bucket}")
    else:
        # Create a new prefix folder
        s3_key = s3_bucket_prefix + assetId + '/'
        logger.info(f"Creating new prefix folder: {s3_key} in bucket: {s3_bucket}")
        create_prefix_folder(s3_bucket, s3_key)
    
    # Get username for version creation
    username = claims_and_roles.get("tokens", ["system"])[0]
    
    # Create initial version record in versions table
    initial_version_id = create_initial_version_record(
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
            return validation_error(body={'message': "Request body is required"})
        
        # Parse JSON body safely
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"})
        elif isinstance(body, dict):
            body = body
        else:
            logger.error("Request body is not a string")
            return validation_error(body={'message': "Request body cannot be parsed"})
        
        # Validate required fields in the request body
        required_fields = ['databaseId', 'assetName', 'description', 'isDistributable']
        for field in required_fields:
            if field not in body:
                return validation_error(body={'message': f"Missing required field: {field}"})
        
        # Parse request model
        request_model = parse(body, model=CreateAssetRequestModel)
        
        # Check authorization
        asset = {
            "object__type": "asset",
            "databaseId": request_model.databaseId,
            "assetName": request_model.assetName,
            "tags": request_model.tags
        }
        
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if not (casbin_enforcer.enforce(asset, "PUT") and casbin_enforcer.enforceAPI(event)):
                return authorization_error()
        
        # Process request
        response = create_asset(request_model, claims_and_roles)
        return success(body=response.dict())
            
    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)})
    except ValueError as v:
        logger.exception(f"Value error: {v}")
        return validation_error(body={'message': str(v)})
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)})
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error()
